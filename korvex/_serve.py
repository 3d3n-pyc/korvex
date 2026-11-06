"""Running an App with Korvex's built-in server (Linux and macOS).

With one worker, the current process serves the app. With several, it
opens the listening socket, forks the workers, which all accept
connections on that one socket, and supervises them: a worker that
crashes is restarted, one that dies right after starting (a failing
startup hook, for example) stops everything instead of looping.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import os
import signal
import socket
import stat
import sys
import threading
import time
import traceback
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, NamedTuple
from urllib.parse import unquote

from korvex import _core
from korvex._app import _call, _finish, _raw_headers
from korvex._response import StreamingResponse
from korvex.middleware import current_request_id

# Python 3.12+: a request handler that doesn't wait completes right away,
# without going through the loop's queue. Older versions schedule it.
_eager_task_factory = getattr(asyncio, "eager_task_factory", None)


def _start_task(loop: asyncio.AbstractEventLoop, coroutine: Any) -> asyncio.Task:
    if _eager_task_factory is not None:
        return _eager_task_factory(loop, coroutine)
    return loop.create_task(coroutine)


if TYPE_CHECKING:
    from korvex._app import App

logger = logging.getLogger("korvex")
access_logger = logging.getLogger("korvex.access")

# A worker that exits with an error this soon after starting failed to
# start: restarting it would only fail again.
STARTUP_WINDOW = 5.0


class Config(NamedTuple):
    """How the server treats requests, set by App.run()."""

    max_body: int
    timeout: float
    body_limits: list[tuple[str, str, int]]  # (method, path, bytes)
    trusted_proxies: list[str]
    uds: str | None
    access_log: bool
    ws_ping_interval: float = 20.0
    forwarded_host: bool = False
    constraints: tuple[tuple[str, str], ...] = ()  # the app's own route constraints, for body_limits
    threads: int = 1  # event loops per worker, each in its own thread


def run(app: App, host: str, port: int, *, workers: int, graceful_timeout: float, config: Config) -> None:
    if not hasattr(_core, "Server"):
        raise NotImplementedError("the built-in server needs Linux or macOS; use uvicorn on Windows")
    if workers < 1:
        raise ValueError("workers must be at least 1")
    if config.threads < 1:
        raise ValueError("threads must be at least 1")
    if config.threads > 1 and getattr(sys, "_is_gil_enabled", lambda: True)():
        logger.warning(
            "threads=%d with the GIL on: the threads share one core. A free-threaded Python (3.14t) runs them in parallel",
            config.threads,
        )
    if config.access_log:
        _enable_access_log()
    listener = _listen_unix(config.uds) if config.uds else _listen_tcp(host, port)
    try:
        if workers == 1:
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
    # A socket file left by a server that was killed would block the bind.
    # Only ever remove a socket, never a regular file someone put there.
    if os.path.exists(path) and stat.S_ISSOCK(os.stat(path).st_mode):
        os.unlink(path)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(path)
    # Let a proxy running as another user (nginx's, say) connect, as uvicorn
    # does; restrict access with the directory's permissions if needed.
    os.chmod(path, 0o666)
    listener.listen(2048)
    return listener


def _enable_access_log() -> None:
    """Shows access logs even when logging isn't configured; respects the
    configuration when it is."""
    access_logger.setLevel(logging.INFO)
    if not access_logger.hasHandlers():
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        access_logger.addHandler(handler)


def serve_process(app: App, fd: int, graceful_timeout: float, config: Config) -> None:
    """Serves in this process: one event loop, or `config.threads` of them,
    each in its own thread with its own server on a copy of the socket."""
    if config.threads == 1:
        asyncio.run(serve(app, fd, graceful_timeout, config))
        return
    lock = threading.Lock()
    loops: list[tuple[asyncio.AbstractEventLoop, asyncio.Event]] = []
    stopping = threading.Event()
    failures: list[BaseException] = []

    def stop_all(signum: int | None = None, frame: object = None) -> None:
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
            asyncio.run(serve(app, thread_fd, graceful_timeout, config, joined=joined))
        except BaseException as error:  # a failing startup hook, say: stop the others too
            failures.append(error)
            stop_all()

    fds = [fd] + [os.dup(fd) for _ in range(config.threads - 1)]
    previous = {sig: signal.signal(sig, stop_all) for sig in (signal.SIGINT, signal.SIGTERM)}
    threads = [threading.Thread(target=run_loop, args=(f,), name=f"korvex-loop-{n}") for n, f in enumerate(fds)]
    try:
        for thread in threads:
            thread.start()
        while any(thread.is_alive() for thread in threads):
            for thread in threads:
                thread.join(0.1)  # in short steps, so signal handlers get to run
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
) -> None:
    """Serves on the listening socket `fd` until SIGINT or SIGTERM, then
    lets requests in progress finish for up to `graceful_timeout` seconds.
    In a thread of its own, signals go to the main thread instead: it sets
    the event `joined(loop, event)` was given."""

    try:
        for hook in app._on_startup:  # if one raises, the server doesn't start
            await _call(hook)
    except BaseException:
        os.close(fd)
        raise
    loop = asyncio.get_running_loop()
    server = _core.Server(
        fd,
        config.uds is not None,
        config.max_body,
        config.timeout,
        config.body_limits,
        config.trusted_proxies,
        config.ws_ping_interval,
        config.forwarded_host,
        list(config.constraints),
    )
    handle = functools.partial(_serve_http, app)
    if config.access_log:
        handle = _logged(handle)
    stopping = asyncio.Event()
    if joined is None:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stopping.set)
    else:
        joined(loop, stopping)
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
                task = start(_serve_websocket(app, path, query, headers, client, scheme, connection, queue, writable))
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
        while not server.finished():
            await asyncio.sleep(0.01)
    finally:
        loop.remove_reader(server.fileno())
        server.close()
        if joined is None:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.remove_signal_handler(sig)
        for hook in reversed(app._on_shutdown):
            await _call(hook)


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
) -> int:
    """Runs one request taken from the built-in server, through the same
    dispatch as under ASGI, hands the response back to Rust, and returns
    its status. `error` is a status the server already decided on (413,
    408, 400): the request goes through the before hooks, then gets that
    error's response, as if the route had raised `HTTPException`. A
    streamed response waits on `writable(responder.id)` whenever its
    client falls behind."""
    scope = {
        "type": "http",
        "method": method,
        "path": unquote(path),
        "query_string": query_string,
        "headers": headers,
        "client": client,
        "scheme": scheme,
    }

    async def receive() -> dict:
        return {"type": "http.request", "body": body, "more_body": False}

    response = await app._respond(scope, receive, error)
    head = method == "HEAD"
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
        finally:
            responder.end_stream()
            await _finish(response, path)
    else:
        responder.respond(response.status, raw_headers, response.body)
    if head and isinstance(response, StreamingResponse):
        await _finish(response, path)
    return response.status


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
) -> None:
    """Runs a WebSocket from the built-in server through the same code
    as under ASGI: it speaks ASGI messages over the Rust connection. A
    send waits on `writable(connection.id)` while the client is behind."""
    offered = next((v.decode("latin-1") for k, v in headers if k == b"sec-websocket-protocol"), "")
    scope = {
        "type": "websocket",
        "path": unquote(path),
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
        return await incoming.get()

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
    """Wraps _serve_http to log each request: client, request line,
    status and duration."""

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


def supervise(app: App, listener: socket.socket, workers: int, graceful_timeout: float, config: Config) -> None:
    """Forks `workers` processes serving on `listener` and keeps them
    running until SIGINT or SIGTERM, which is passed on to them."""
    children: dict[int, float] = {}  # pid -> start time
    stopping = False
    failed = False

    def spawn() -> None:
        pid = os.fork()
        if pid == 0:  # the worker
            code = 1
            try:
                signal.signal(signal.SIGINT, signal.SIG_DFL)
                signal.signal(signal.SIGTERM, signal.SIG_DFL)
                serve_process(app, listener.detach(), graceful_timeout, config)
                code = 0
            except BaseException:
                traceback.print_exc()
            finally:
                sys.stdout.flush()
                sys.stderr.flush()
                os._exit(code)
        children[pid] = time.monotonic()
        if stopping:  # a signal arrived while forking
            os.kill(pid, signal.SIGTERM)

    def stop(signum: int | None = None, frame: object = None) -> None:
        nonlocal stopping
        stopping = True
        # Stop the kernel from queueing new connections no worker would take.
        listener.close()
        for pid in list(children):
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGTERM)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    for _ in range(workers):
        spawn()
    try:
        while children:
            try:
                pid, status = os.wait()
            except ChildProcessError:
                break
            started = children.pop(pid, None)
            if started is None or stopping:
                continue
            code = os.waitstatus_to_exitcode(status)
            if code != 0 and time.monotonic() - started < STARTUP_WINDOW:
                logger.error("worker %d failed to start (exit code %d); stopping", pid, code)
                failed = True
                stop()
            else:
                logger.warning("worker %d exited (code %d); starting a new one", pid, code)
                spawn()
    finally:
        listener.close()
    if failed:
        sys.exit(1)
