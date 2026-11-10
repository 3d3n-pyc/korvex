"""Pieces of `app.mount()`: the scope, receive and send of a mounted ASGI app, and its lifespan."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import Any, Optional
from urllib.parse import quote

_DONE: dict = {"type": "korvex.mount.done"}  # the sub-app's task ended


def _base_scope(
    request: Any, prefix: str, kind: str, raw_path: Optional[bytes], server: Any, state: Optional[dict] = None
) -> dict:
    """The scope fields HTTP and WebSocket share."""
    root_path = request.root_path + prefix
    full_raw = request.root_path.encode() + (raw_path if raw_path is not None else quote(request.path).encode())
    scope: dict = {
        "type": kind,
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "scheme": request.scheme,
        "path": request.root_path + request.path,
        "raw_path": full_raw,
        "root_path": root_path,
        "query_string": request._query_string,
        "headers": [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in request.headers],
        "client": request.client,
        "state": dict(state) if state else {},
    }
    if server is not None:
        scope["server"] = server
    return scope


def child_scope(
    request: Any,
    prefix: str,
    raw_path: Optional[bytes] = None,
    server: Optional[tuple] = None,
    state: Optional[dict] = None,
) -> dict:
    """The ASGI HTTP scope of the app mounted at `prefix`, from a Korvex request.
    `state`: the mounted app's lifespan state; the scope gets a shallow copy."""
    scope = _base_scope(request, prefix, "http", raw_path, server, state)
    scope["method"] = request.method
    return scope


def child_ws_scope(
    request: Any,
    prefix: str,
    raw_path: Optional[bytes] = None,
    server: Optional[tuple] = None,
    state: Optional[dict] = None,
) -> dict:
    """The ASGI WebSocket scope of the app mounted at `prefix`."""
    scope = _base_scope(request, prefix, "websocket", raw_path, server, state)
    scope["subprotocols"] = list(getattr(request, "subprotocols", ()))
    return scope


class Receive:
    """The `receive` of a mounted app: the whole body once, then `http.disconnect` after `close()`."""

    def __init__(self, body: bytes) -> None:
        self._body: Optional[bytes] = body
        self._closed = asyncio.Event()

    def close(self) -> None:
        """Marks the client as gone (or the request as over)."""
        self._closed.set()

    async def __call__(self) -> dict:
        """Returns the next ASGI message."""
        if self._body is not None:
            body, self._body = self._body, None
            return {"type": "http.request", "body": body, "more_body": False}
        await self._closed.wait()
        return {"type": "http.disconnect"}


class ResponderSend:
    """The `send` of a mounted app, writing to the built-in server's responder."""

    def __init__(
        self,
        responder: Any,
        receive: Receive,
        writable: Optional[Callable[[int], Awaitable[None]]] = None,
    ) -> None:
        self._responder = responder
        self._receive = receive
        self._writable = writable
        self._head: Optional[tuple] = None
        self._streaming = False
        self._done = False  # the response is complete (or the client is gone)

    @property
    def status(self) -> Optional[int]:
        """The status the app started its response with, if it did."""
        return self._head[0] if self._head else None

    @property
    def unanswered(self) -> bool:
        """True if nothing reached the client: the caller answers 500."""
        return not self._done and not self._streaming

    async def __call__(self, message: dict) -> None:
        """Sends one ASGI message to the client."""
        kind = message["type"]
        if kind == "http.response.start":
            if self._head is not None:
                raise RuntimeError("http.response.start sent twice")
            headers = [(bytes(k), bytes(v)) for k, v in message.get("headers", ())]
            self._head = (message["status"], headers)
        elif kind == "http.response.body":
            if self._head is None:
                raise RuntimeError("http.response.body sent before http.response.start")
            await self._body(message.get("body", b""), message.get("more_body", False))

    async def _body(self, body: bytes, more_body: bool) -> None:
        """Maps one body message to `respond` or to the stream."""
        if self._done:
            return
        status, headers = self._head  # type: ignore[misc]
        if not self._streaming:
            if not more_body:
                self._responder.respond(status, headers, bytes(body))
                self._done = True
                return
            self._responder.start_stream(status, headers)
            self._streaming = True
        await self._chunk(bytes(body), more_body)

    async def _chunk(self, body: bytes, more_body: bool) -> None:
        """Sends a streamed piece, waits if the client is behind, ends the stream on the last."""
        sent = self._responder.send_chunk(body) if body else 1
        if sent == 0:
            self._gone()
            return
        if not more_body:
            self._responder.end_stream()
            self._streaming, self._done = False, True
        elif sent == 2 and self._writable is not None:
            await self._writable(self._responder.id)

    def _gone(self) -> None:
        """The client left: stop streaming and tell the app."""
        self._streaming, self._done = False, True
        self._responder.end_stream()
        self._receive.close()

    def finish(self) -> None:
        """Called once the app returned or raised: cuts a stream left unfinished."""
        if self._streaming:
            self._responder.abort_stream()
            self._streaming = False
            self._done = True


class Lifespan:
    """The lifespan of a mounted app; an app that raises on the scope runs without."""

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app
        self.state: dict = {}  # the lifespan fills it; requests get a copy
        self._task: Optional[asyncio.Task] = None
        self._to_app: asyncio.Queue = asyncio.Queue()
        self._from_app: asyncio.Queue = asyncio.Queue()

    async def startup(self) -> None:
        """Sends lifespan.startup; `RuntimeError` if the app reports a failure."""
        scope = {"type": "lifespan", "asgi": {"version": "3.0", "spec_version": "2.0"}, "state": self.state}
        self._task = asyncio.ensure_future(self.app(scope, self._to_app.get, self._from_app.put))
        self._task.add_done_callback(lambda _: self._from_app.put_nowait(_DONE))
        await self._to_app.put({"type": "lifespan.startup"})
        message = await self._from_app.get()
        if message is _DONE:
            self._task = None  # no lifespan support
        elif message["type"] == "lifespan.startup.failed":
            await self._wait()
            raise RuntimeError(f"the mounted app's startup failed: {message.get('message', '')}")

    async def shutdown(self) -> None:
        """Sends lifespan.shutdown; `RuntimeError` if the app reports a failure."""
        if self._task is None:
            return
        await self._to_app.put({"type": "lifespan.shutdown"})
        message = await self._from_app.get()
        await self._wait()
        if message is not _DONE and message["type"] == "lifespan.shutdown.failed":
            raise RuntimeError(f"the mounted app's shutdown failed: {message.get('message', '')}")

    async def _wait(self) -> None:
        """Waits for the app's task, ignoring its outcome (it was in the message)."""
        if self._task is not None:
            with contextlib.suppress(Exception):
                await self._task
