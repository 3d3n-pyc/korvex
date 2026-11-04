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
import sys
import time
import traceback
from typing import TYPE_CHECKING

from korvex import _core

if TYPE_CHECKING:
    from korvex.app import App

logger = logging.getLogger("korvex")

# A worker that exits with an error this soon after starting failed to
# start: restarting it would only fail again.
STARTUP_WINDOW = 5.0


def run(app: App, host: str, port: int, workers: int, graceful_timeout: float) -> None:
    if not hasattr(_core, "Server"):
        raise NotImplementedError("the built-in server needs Linux or macOS; use uvicorn on Windows")
    if workers < 1:
        raise ValueError("workers must be at least 1")
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    listener = socket.create_server((host, port), family=family, backlog=2048)
    if workers == 1:
        asyncio.run(serve(app, listener.detach(), graceful_timeout))
    else:
        supervise(app, listener, workers, graceful_timeout)


async def serve(app: App, fd: int, graceful_timeout: float) -> None:
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
    server = _core.Server(fd)
    stopping = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopping.set)
    tasks: set[asyncio.Task] = set()

    def take_requests() -> None:
        for request in server.take():
            task = _start_task(loop, app._serve_one(*request))
            if not task.done():  # the loop only keeps weak references to tasks
                tasks.add(task)
                task.add_done_callback(tasks.discard)

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


def supervise(app: App, listener: socket.socket, workers: int, graceful_timeout: float) -> None:
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
                asyncio.run(serve(app, listener.detach(), graceful_timeout))
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
