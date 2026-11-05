"""Running an App with Korvex's built-in server (Linux and macOS).

With one worker, the current process serves the app. With several, it
opens the listening socket, forks the workers, which all accept
connections on that one socket, and supervises them: a worker that
crashes is restarted, one that dies right after starting (a failing
startup hook, for example) stops everything instead of looping.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import stat
import sys
import time
import traceback
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, NamedTuple

from korvex import _core
from korvex.middleware import current_request_id

if TYPE_CHECKING:
    from korvex.app import App

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


def run(app: App, host: str, port: int, *, workers: int, graceful_timeout: float, config: Config) -> None:
    if not hasattr(_core, "Server"):
        raise NotImplementedError("the built-in server needs Linux or macOS; use uvicorn on Windows")
    if workers < 1:
        raise ValueError("workers must be at least 1")
    if config.access_log:
        _enable_access_log()
    listener = _listen_unix(config.uds) if config.uds else _listen_tcp(host, port)
    try:
        if workers == 1:
            asyncio.run(serve(app, listener.detach(), graceful_timeout, config))
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


async def serve(app: App, fd: int, graceful_timeout: float, config: Config) -> None:
    """Serves on the listening socket `fd` until SIGINT or SIGTERM, then
    lets requests in progress finish for up to `graceful_timeout` seconds."""
    from korvex.app import _call, _start_task

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
    )
    handle = _logged(app._serve_one) if config.access_log else app._serve_one
    stopping = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopping.set)
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
                start(handle(*event, writable=writable))
            elif kind == "ws":
                path, query, headers, client, scheme, connection = event
                queue: asyncio.Queue = asyncio.Queue()
                websockets[connection.id] = queue
                task = start(app._serve_websocket(path, query, headers, client, scheme, connection, queue, writable))
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
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)
        for hook in reversed(app._on_shutdown):
            await _call(hook)


def _logged(serve_one: Callable[..., Awaitable[int]]) -> Callable[..., Awaitable[int]]:
    """Wraps App._serve_one to log each request: client, request line,
    status and duration."""

    async def serve_logged(method: str, path: str, query: bytes, headers: list, body: bytes, client, scheme, responder, error: int = 0, writable=None) -> int:
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
                asyncio.run(serve(app, listener.detach(), graceful_timeout, config))
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
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

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
