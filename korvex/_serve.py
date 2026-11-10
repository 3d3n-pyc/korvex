"""Running an App with Korvex's built-in server (Linux and macOS).

With one worker, the current process serves the app. With several, it
opens the listening socket, forks workers that all accept on it, and
supervises them: a crashed worker is restarted, but one that dies right
after starting (a failing startup hook, say) stops everything.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import os
import random
import signal
import socket
import stat
import sys
import threading
import time
import traceback
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any, NamedTuple
from urllib.parse import unquote

from korvex import _core
from korvex._app import _call, _finish, _raw_headers
from korvex._requests import Request
from korvex._response import EventStream, StreamingResponse
from korvex.middleware import current_request_id

# Python 3.12+: a handler that doesn't wait completes at once, skipping the
# loop's queue.
_eager_task_factory = getattr(asyncio, "eager_task_factory", None)


def _start_task(loop: asyncio.AbstractEventLoop, coroutine: Any) -> asyncio.Task:
    if _eager_task_factory is not None:
        return _eager_task_factory(loop, coroutine)
    return loop.create_task(coroutine)


if TYPE_CHECKING:
    from korvex._app import App

logger = logging.getLogger("korvex")
access_logger = logging.getLogger("korvex.access")

# A worker failing this soon after starting would only fail again: don't restart.
STARTUP_WINDOW = 5.0


class Config(NamedTuple):
    """How the server treats requests, set by App.run()."""

    max_body: int
    read_timeout: float
    body_limits: list[tuple[str, str, int]]  # (method, path, bytes)
    trusted_proxies: list[str]
    uds: str | None
    access_log: bool
    ws_ping_interval: float = 20.0
    forwarded_host: bool = False
    constraints: tuple[tuple[str, str], ...] = ()  # the app's own route constraints, for body_limits
    threads: int = 1  # event loops per worker, each in its own thread
    max_connections: int = 0  # open connections per worker and thread; 0 for no limit
    max_requests: int = 0  # HTTP requests a worker serves before it restarts, give or take 10%; 0 for never
    root_path: str = ""  # the prefix a proxy strips before forwarding; "" for none


def run(app: App, host: str, port: int, *, workers: int, graceful_timeout: float, config: Config) -> None:
    if not hasattr(_core, "Server"):
        raise NotImplementedError("the built-in server needs Linux or macOS; use uvicorn on Windows")
    if workers < 1:
        raise ValueError("workers must be at least 1")
    if config.threads < 1:
        raise ValueError("threads must be at least 1")
    if config.root_path and (not config.root_path.startswith("/") or config.root_path.endswith("/")):
        raise ValueError("root_path must be empty, or start with / and not end with /")
    if config.max_connections < 0:
        raise ValueError("max_connections must be 0 (no limit) or more")
    if config.max_requests < 0:
        raise ValueError("max_requests must be 0 (never restart) or more")
    if config.threads > 1 and getattr(sys, "_is_gil_enabled", lambda: True)():
        logger.warning(
            "threads=%d with the GIL on: the threads share one core. A free-threaded Python (3.14t) runs them in parallel",
            config.threads,
        )
    if config.access_log:
        _enable_access_log()
    listener = _listen_unix(config.uds) if config.uds else _listen_tcp(host, port)
    try:
        if workers == 1 and config.max_requests == 0:
            serve_process(app, listener.detach(), graceful_timeout, config)
        else:
            supervise(app, listener, workers, graceful_timeout, config)
    finally:
        if config.uds:
            os.unlink(config.uds)


def _listen_tcp(host: str, port: int) -> socket.socket:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    return socket.create_server((host, port), family=family, backlog=2048)


def _listen_unix(path: str) -> socket.socket:
    # A socket file left by a killed server blocks the bind. Remove only a
    # socket, never a regular file.
    if os.path.exists(path) and stat.S_ISSOCK(os.stat(path).st_mode):
        os.unlink(path)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(path)
    # Let a proxy running as another user connect, as uvicorn does; restrict
    # with the directory's permissions.
    os.chmod(path, 0o666)
    listener.listen(2048)
    return listener


def _enable_access_log() -> None:
    """Shows access logs, unless logging is already configured."""
    access_logger.setLevel(logging.INFO)
    if not access_logger.hasHandlers():
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        access_logger.addHandler(handler)


def serve_process(app: App, fd: int, graceful_timeout: float, config: Config) -> None:
    """Serves in this process: one event loop, or `config.threads`, each in
    its own thread with its own server on a copy of the socket."""
    if config.threads == 1:
        asyncio.run(serve(app, fd, graceful_timeout, config))
        return
    lock = threading.Lock()
    loops: list[tuple[asyncio.AbstractEventLoop, asyncio.Event]] = []
    stopping = threading.Event()
    failures: list[BaseException] = []

    def stop_all(signum: int | None = None, frame: object = None) -> None:
        if signum is not None and stopping.is_set():
            _exit_now(1)  # a second signal: no more waiting
        stopping.set()
        with lock:
            for loop, stop in loops:
                loop.call_soon_threadsafe(stop.set)

    def joined(loop: asyncio.AbstractEventLoop, stop: asyncio.Event) -> None:
        with lock:
            loops.append((loop, stop))
        if stopping.is_set():  # asked to stop before this one was running
            loop.call_soon_threadsafe(stop.set)

    def run_loop(thread_fd: int) -> None:
        try:
            asyncio.run(serve(app, thread_fd, graceful_timeout, config, joined=joined, counter=counter))
        except BaseException as error:  # a failing startup hook, say: stop the others too
            failures.append(error)
            stop_all()

    counter = _request_counter(config.max_requests, stop_all)
    fds = [fd] + [os.dup(fd) for _ in range(config.threads - 1)]
    previous = {sig: signal.signal(sig, stop_all) for sig in (signal.SIGINT, signal.SIGTERM)}
    threads = [
        threading.Thread(target=run_loop, args=(f,), name=f"korvex-loop-{n}", daemon=True) for n, f in enumerate(fds)
    ]
    try:
        for thread in threads:
            thread.start()
        while any(thread.is_alive() and thread.ident not in _abandoned for thread in threads):
            for thread in threads:
                thread.join(0.1)  # in short steps, so signal handlers get to run
        if _abandoned:  # a loop left stuck work behind: it would block the exit
            _exit_now(0)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    if failures:
        raise failures[0]


async def serve(
    app: App,
    fd: int,
    graceful_timeout: float,
    config: Config,
    *,
    joined: Callable[[asyncio.AbstractEventLoop, asyncio.Event], None] | None = None,
    counter: Callable[[], None] | None = None,
) -> None:
    """Serves on the listening socket `fd` until SIGINT or SIGTERM, then
    lets requests in progress finish for up to `graceful_timeout` seconds.
    In a thread, signals go to the main thread, which sets the event given
    to `joined(loop, event)`. `counter()` is called for each request when
    the worker counts them for `max_requests`."""

    # Handled from here on, startup included: a SIGTERM during startup stops
    # gracefully, shutdown hooks included, instead of killing the server.
    loop = asyncio.get_running_loop()
    stopping = asyncio.Event()
    threads_pool = _TaskThreads()  # sync background tasks: shutdown never joins its threads
    loop.set_default_executor(threads_pool)
    if joined is None:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _stop_or_exit, stopping)
    else:
        joined(loop, stopping)
    try:
        for hook in app._on_startup:  # if one raises, the server doesn't start
            await _call(hook)
    except BaseException:
        os.close(fd)
        raise
    server = _core.Server(
        fd,
        config.uds is not None,
        config.max_body,
        config.read_timeout,
        config.body_limits,
        config.trusted_proxies,
        config.ws_ping_interval,
        config.forwarded_host,
        list(config.constraints),
        config.max_connections,
    )
    background = _Background()  # background tasks still running
    handle = functools.partial(_serve_http, app, root_path=config.root_path, background=background)
    if config.access_log:
        handle = _logged(handle)
    counter = counter or _request_counter(config.max_requests, stopping.set)
    if counter is not None:
        handle = _counted(handle, counter)
    tasks: set[asyncio.Task] = set()
    websockets: dict[int, asyncio.Queue] = {}  # connection id -> its incoming messages
    waiting: dict[int, asyncio.Future] = {}  # stream or WebSocket id -> woken when its client catches up

    async def writable(id: int) -> None:
        """Waits until the client of stream or WebSocket `id` has caught up."""
        future = waiting[id] = loop.create_future()
        try:
            await future
        finally:
            waiting.pop(id, None)

    def start(coroutine: Awaitable) -> asyncio.Task:
        task = _start_task(loop, coroutine)
        if not task.done():  # the loop only keeps weak references to tasks
            tasks.add(task)
            task.add_done_callback(tasks.discard)
        return task

    def take_requests() -> None:
        for kind, *event in server.take():
            if kind == "http":
                method, path, query, headers, body, client, scheme, responder, error = event
                start(handle(method, path, query, headers, body, client, scheme, responder, error, writable))
            elif kind == "ws":
                path, query, headers, client, scheme, connection = event
                queue: asyncio.Queue = asyncio.Queue()
                websockets[connection.id] = queue
                task = start(
                    _serve_websocket(
                        app, path, query, headers, client, scheme, connection, queue, writable, config.root_path
                    )
                )
                task.add_done_callback(lambda _, id=connection.id: websockets.pop(id, None))
            elif kind == "ws_message" and event[0] in websockets:
                key = "text" if isinstance(event[1], str) else "bytes"
                websockets[event[0]].put_nowait({"type": "websocket.receive", key: event[1]})
            elif kind == "ws_closed" and event[0] in websockets:
                websockets[event[0]].put_nowait({"type": "websocket.disconnect", "code": event[1]})
            elif kind == "writable":
                future = waiting.get(event[0])
                if future is not None and not future.done():
                    future.set_result(None)

    loop.add_reader(server.fileno(), take_requests)
    try:
        await stopping.wait()
        # Keep answering while open connections finish their current request.
        server.stop(graceful_timeout)
        deadline = loop.time() + graceful_timeout
        while not server.finished():
            await asyncio.sleep(0.01)
        background.closed = True  # a stream ending from now on starts no task
        stuck = await _finish_background(background, max(0.0, deadline - loop.time()))
    finally:
        loop.remove_reader(server.fileno())
        server.close()
        if joined is None:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.remove_signal_handler(sig)
        for hook in reversed(app._on_shutdown):
            await _call(hook)
    if stuck or threads_pool.busy:
        # asyncio.run would wait for these forever, and so would the exit.
        if joined is None:
            _exit_now(0)
        _abandoned.add(threading.get_ident())
        await asyncio.Event().wait()  # this thread is left to the exit of the process


_abandoned: set[int | None] = set()  # threads of loops that left stuck work behind


def _exit_now(code: int) -> None:
    """Ends the process at once, after flushing the logs: used when work
    that ignores cancellation, or a thread that never returns, would hold
    the shutdown up, and at a second SIGINT or SIGTERM."""
    logging.shutdown()
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):  # a broken pipe must not stop the exit
            stream.flush()
    os._exit(code)


def _stop_or_exit(stopping: asyncio.Event) -> None:
    """Signal handler of a single-loop worker: the first signal starts the
    graceful shutdown, a second one exits at once."""
    if stopping.is_set():
        _exit_now(1)
    stopping.set()


class _TaskThreads(ThreadPoolExecutor):
    """The default thread pool of a worker. It counts the calls still
    running, and its shutdown does not wait for them."""

    def __init__(self) -> None:
        super().__init__(thread_name_prefix="korvex-task")
        self._running = 0
        self._count = threading.Lock()

    @property
    def busy(self) -> int:
        """How many calls are still running in the pool's threads."""
        return self._running

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        """Like the standard `submit`, and counts the call until it ends."""
        with self._count:
            self._running += 1
        try:
            future = super().submit(fn, *args, **kwargs)
        except BaseException:
            self._finished(None)
            raise
        future.add_done_callback(self._finished)
        return future

    def _finished(self, _future: Any) -> None:
        """Counts a call out."""
        with self._count:
            self._running -= 1

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        """Stops the pool without joining its threads."""
        super().shutdown(wait=False, cancel_futures=True)


class _Background(set):
    """The background tasks of a worker still running. `closed` is set when
    the shutdown stopped waiting: no new task starts after that."""

    closed = False


async def _finish_background(background: set[asyncio.Task], timeout: float) -> int:
    """Waits up to `timeout` seconds for the background tasks, then cancels
    what is left, waits at most 1 s more, and logs how many were cut off.
    Returns how many ignored the cancellation."""
    if not background:
        return 0
    _, left = await asyncio.wait(background, timeout=timeout)
    if not left:
        return 0
    logger.warning("shutdown cut off %d background task run(s) still running", len(left))
    for task in left:
        task.cancel()
    _, stuck = await asyncio.wait(left, timeout=CANCEL_GRACE)
    return len(stuck)


def _request_counter(max_requests: int, stop: Callable[[], None]) -> Callable[[], None] | None:
    """Returns a function to call once per request; it calls `stop()` after
    `max_requests` plus a random 0 to 10% (so workers don't restart
    together). None when there's no limit."""
    if max_requests == 0:
        return None
    limit = max_requests + random.randint(0, max_requests // 10)
    served = 0

    def count() -> None:
        nonlocal served
        served += 1
        if served >= limit:
            stop()

    return count


def _counted(serve_one: Callable[..., Awaitable[int]], counter: Callable[[], None]) -> Callable[..., Awaitable[int]]:
    """Wraps _serve_http to count each request it serves."""

    def serve_counted(*args: Any) -> Awaitable[int]:
        counter()
        return serve_one(*args)

    return serve_counted


async def _serve_http(
    app: App,
    method: str,
    path: str,
    query_string: bytes,
    headers: list,
    body: bytes,
    client: tuple[str, int] | None,
    scheme: str,
    responder: Any,
    error: int = 0,
    writable: Callable[[int], Awaitable[None]] | None = None,
    root_path: str = "",
    background: set[asyncio.Task] | None = None,
) -> int:
    """Runs one request from the built-in server through the same dispatch
    as under ASGI, hands the response to Rust, and returns its status.
    `error` is a status the server already decided on (413, 408, 400): the
    request goes through the before hooks, then gets that error's response,
    as if the route had raised `HTTPException`. A streamed response waits
    on `writable(responder.id)` while its client is behind. Background
    tasks run in their own task once the response is out, tracked in
    `background` so shutdown can wait for them."""
    decoded = unquote(path) if "%" in path else path
    request = Request._from_server(method, decoded, query_string, headers, client, scheme, body, app, root_path)
    response = await app._respond(request, error)
    head = method == "HEAD"
    try:
        raw_headers = _raw_headers(response, head)
        if head:
            responder.respond(response.status, raw_headers, b"")
        elif isinstance(response, StreamingResponse):
            responder.start_stream(response.status, raw_headers)
            try:
                async for chunk in response:
                    sent = responder.send_chunk(chunk)
                    if sent == 0:
                        break  # the client is gone
                    if sent == 2 and writable is not None:
                        await writable(responder.id)  # the client is behind: let it catch up
            except Exception:
                responder.abort_stream()  # no terminating chunk: the client sees a cut body
                logger.exception("error while streaming %s %s", method, path)
            finally:
                responder.end_stream()
                if isinstance(response, EventStream):
                    await response.aclose()  # the client left or the source failed: run its finally
                await _finish(response, path)
        else:
            responder.respond(response.status, raw_headers, response.body)
        if head and isinstance(response, StreamingResponse):
            await _finish(response, path)
    finally:
        if request._tasks is not None:  # sent or not, the client never waits for them
            _run_background(request._tasks, background)
    return response.status


def _run_background(tasks: Any, background: set[asyncio.Task] | None) -> None:
    """Starts the request's background tasks in a task of their own, tracked
    in `background` so a shutdown can wait for them."""
    if getattr(background, "closed", False):  # past the shutdown deadline
        logger.warning("shutdown dropped %d background task(s) that had not started", len(tasks._items))
        return
    task = asyncio.ensure_future(tasks._run())
    if background is not None:
        background.add(task)
        task.add_done_callback(background.discard)


async def _serve_websocket(
    app: App,
    path: str,
    query_string: bytes,
    headers: list,
    client: tuple[str, int] | None,
    scheme: str,
    connection: Any,
    incoming: asyncio.Queue,
    writable: Callable[[int], Awaitable[None]] | None = None,
    root_path: str = "",
) -> None:
    """Runs a WebSocket from the built-in server through the same code as
    under ASGI, speaking ASGI messages over the Rust connection. A send
    waits on `writable(connection.id)` while the client is behind."""
    offered = next((v.decode("latin-1") for k, v in headers if k == b"sec-websocket-protocol"), "")
    scope = {
        "type": "websocket",
        "path": root_path + unquote(path),  # ASGI: includes root_path; the app strips it once
        "root_path": root_path,
        "query_string": query_string,
        "headers": headers,
        "client": client,
        "scheme": scheme,
        "subprotocols": [p.strip() for p in offered.split(",") if p.strip()],
    }
    connected = False

    async def receive() -> dict:
        nonlocal connected
        if not connected:
            connected = True
            return {"type": "websocket.connect"}
        message = await incoming.get()
        data = message.get("text") if "text" in message else message.get("bytes")
        if data is not None:  # the server may read more from the client
            size = len(data) if isinstance(data, bytes) or data.isascii() else len(data.encode())
            connection.received(size)
        return message

    async def send(message: dict) -> None:
        kind = message["type"]
        if kind == "websocket.accept":
            connection.accept(message.get("subprotocol"), message.get("headers") or [])
        elif kind == "websocket.send":
            if message.get("text") is not None:
                sent = connection.send_text(message["text"])
            else:
                sent = connection.send_bytes(message.get("bytes") or b"")
            if sent == 2 and writable is not None:
                await writable(connection.id)  # the client is behind: let it catch up
        elif kind == "websocket.close":
            connection.close(message.get("code", 1000), message.get("reason") or "")

    try:
        await app._websocket(scope, receive, send)
    finally:
        connection.finish()


def _logged(serve_one: Callable[..., Awaitable[int]]) -> Callable[..., Awaitable[int]]:
    """Wraps _serve_http to log client, request line, status and duration."""

    async def serve_logged(
        method: str,
        path: str,
        query: bytes,
        headers: list,
        body: bytes,
        client,
        scheme,
        responder,
        error: int = 0,
        writable=None,
    ) -> int:
        started = time.perf_counter()
        status = await serve_one(method, path, query, headers, body, client, scheme, responder, error, writable)
        target = f"{path}?{query.decode('latin-1')}" if query else path
        host = client[0] if client else "-"
        duration = (time.perf_counter() - started) * 1000
        request_id = current_request_id()
        if request_id is None:
            access_logger.info('%s "%s %s" %d %.1fms', host, method, target, status, duration)
        else:
            access_logger.info('%s "%s %s" %d %.1fms %s', host, method, target, status, duration, request_id)
        return status

    return serve_logged


BACKOFF_START = 0.1  # first restart delay, in seconds; doubles while workers keep crashing
BACKOFF_MAX = 30.0
BACKOFF_RESET = 30.0  # a worker that lived this long clears the backoff
CANCEL_GRACE = 1.0  # after graceful_timeout, for cancelled tasks to end
KILL_GRACE = 5.0  # after graceful_timeout, before SIGKILL


def restart_delay(previous: float, lived: float) -> float:
    """The delay before restarting a crashed worker: 0.1 s, doubling up to
    30 s, reset to 0.1 s if the worker had lived 30 s."""
    if previous <= 0 or lived >= BACKOFF_RESET:
        return BACKOFF_START
    return min(previous * 2, BACKOFF_MAX)


class _Supervisor:
    """The state behind `supervise`: workers, pending restarts, shutdown progress."""

    def __init__(self, app: App, listener: socket.socket, graceful_timeout: float, config: Config) -> None:
        self.app, self.listener, self.graceful_timeout, self.config = app, listener, graceful_timeout, config
        self.children: dict[int, float] = {}  # pid -> start time
        self.restarts: list[float] = []  # times at which to start a worker
        self.delay = 0.0
        self.stopping_since: float | None = None
        self.failed = False

    def spawn(self) -> None:
        """Forks one worker."""
        pid = os.fork()
        if pid == 0:  # the worker
            code = 1
            try:
                signal.signal(signal.SIGINT, signal.SIG_DFL)
                signal.signal(signal.SIGTERM, signal.SIG_DFL)
                serve_process(self.app, self.listener.detach(), self.graceful_timeout, self.config)
                code = 0
            except BaseException:
                traceback.print_exc()
            finally:
                sys.stdout.flush()
                sys.stderr.flush()
                os._exit(code)
        self.children[pid] = time.monotonic()
        if self.stopping_since is not None:  # a signal arrived while forking
            os.kill(pid, signal.SIGTERM)

    def signal_all(self, signum: int) -> None:
        """Sends `signum` to every worker still there."""
        for pid in list(self.children):
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signum)

    def stop(self, signum: int | None = None, frame: object = None) -> None:
        """Starts the shutdown (SIGTERM to the workers); a further signal
        kills them at once."""
        if self.stopping_since is not None:
            self.signal_all(signal.SIGKILL)
            return
        self.stopping_since = time.monotonic()
        self.restarts.clear()
        # Stop the kernel queueing connections no worker would take.
        self.listener.close()
        self.signal_all(signal.SIGTERM)

    def escalate(self) -> None:
        """Kills the workers that outlast the graceful timeout by 5 s."""
        if (
            self.stopping_since is not None
            and time.monotonic() - self.stopping_since > self.graceful_timeout + KILL_GRACE
        ):
            self.signal_all(signal.SIGKILL)

    def on_exit(self, pid: int, status: int) -> None:
        """Handles a worker's exit: stop on a failed start; replace a clean
        exit (code 0, `max_requests`) at once; else restart after the backoff."""
        started = self.children.pop(pid, None)
        if started is None or self.stopping_since is not None:
            return
        code = os.waitstatus_to_exitcode(status)
        lived = time.monotonic() - started
        if code != 0 and lived < STARTUP_WINDOW:
            logger.error("worker %d failed to start (exit code %d); stopping", pid, code)
            self.failed = True
            self.stop()
            return
        if code == 0:
            logger.info("worker %d stopped after its requests; starting a new one", pid)
            self.restarts.append(time.monotonic())
            return
        self.delay = restart_delay(self.delay, lived)
        logger.warning("worker %d exited (code %d); starting a new one in %.2fs", pid, code, self.delay)
        self.restarts.append(time.monotonic() + self.delay)

    def start_due(self) -> None:
        """Forks the workers whose restart delay has passed."""
        now = time.monotonic()
        due = [at for at in self.restarts if at <= now]
        self.restarts = [at for at in self.restarts if at > now]
        for _ in due:
            self.spawn()

    def run(self, workers: int) -> None:
        """Starts the workers and watches them until all are gone."""
        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)
        for _ in range(workers):
            self.spawn()
        while self.children or self.restarts:
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                if not self.restarts:
                    break
                pid = status = 0
            if pid:
                self.on_exit(pid, status)
                continue
            self.escalate()
            self.start_due()
            time.sleep(0.05)


def supervise(app: App, listener: socket.socket, workers: int, graceful_timeout: float, config: Config) -> None:
    """Forks `workers` processes serving on `listener` until SIGINT or
    SIGTERM, which is passed on to them. Workers alive 5 s after
    `graceful_timeout`, or at a second signal, are killed; a crashing
    worker is restarted after a growing delay."""
    supervisor = _Supervisor(app, listener, graceful_timeout, config)
    try:
        supervisor.run(workers)
    finally:
        listener.close()
    if supervisor.failed:
        sys.exit(1)
