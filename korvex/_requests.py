"""What a handler gets: `Request`, `WebSocket` (and `WebSocketDisconnect`),
and the `UploadedFile`s of a multipart form."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping
from email.message import Message
from typing import TYPE_CHECKING, Any, Union
from urllib.parse import parse_qs, unquote

from korvex._errors import HTTPException
from korvex._response import Headers, JSONValue

if TYPE_CHECKING:
    from korvex._app import App


class UploadedFile:
    """A file sent in a `multipart/form-data` form."""

    __slots__ = ("filename", "content_type", "data")

    def __init__(self, filename: str, content_type: str, data: bytes) -> None:
        self.filename = filename
        self.content_type = content_type
        self.data = data

    def __repr__(self) -> str:
        return f"UploadedFile({self.filename!r}, {self.content_type!r}, {len(self.data)} bytes)"


FormData = dict[str, list[Union[str, UploadedFile]]]


class Request:
    """An incoming HTTP request."""

    __slots__ = (
        "method",
        "path",
        "headers",
        "query_params",
        "path_params",
        "client",
        "scheme",
        "app",
        "_receive",
        "_cookies",
        "_body",
        "_form",
    )

    def __init__(
        self,
        scope: dict,
        receive: Callable[[], Awaitable[dict]],
        path_params: Mapping[str, Any],
        app: App | None = None,
    ) -> None:
        self.method: str = scope.get("method", "GET")  # a WebSocket handshake is a GET
        self.path: str = scope["path"]
        client = scope.get("client")
        self.client: tuple[str, int] | None = tuple(client) if client else None  # type: ignore[assignment]
        """`(host, port)` of the client, behind a trusted proxy too; `None` if unknown."""
        self.scheme: str = scope.get("scheme", "http")
        """`"https"` or `"http"`, as the client used it."""
        self.headers = Headers((k.decode("latin-1"), v.decode("latin-1")) for k, v in scope["headers"])
        self.query_params: dict[str, list[str]] = parse_qs(scope.get("query_string", b"").decode("latin-1"))
        self.path_params = path_params
        self.app = app
        """The `App` handling the request."""
        self._receive = receive
        self._cookies: dict[str, str] | None = None
        self._body: bytes | None = None
        self._form: FormData | None = None

    @property
    def cookies(self) -> dict[str, str]:
        """The cookies sent by the client, parsed on first use."""
        if self._cookies is None:
            self._cookies = {}
            for header in self.headers.get_all("cookie"):
                for pair in header.split(";"):
                    name, sep, value = pair.partition("=")
                    if sep and name.strip():
                        self._cookies[name.strip()] = unquote(value.strip().strip('"'))
        return self._cookies

    async def body(self) -> bytes:
        """Reads the whole request body (once: later calls get it again)."""
        if self._body is None:
            chunks = []
            more_body = True
            while more_body:
                message = await self._receive()
                chunks.append(message.get("body", b""))
                more_body = message.get("more_body", False)
            self._body = b"".join(chunks)
        return self._body

    async def json(self) -> JSONValue:
        """Reads the body and parses it as JSON. A body that isn't valid
        JSON is answered with a 400 error."""
        try:
            return json.loads(await self.body())
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(400, "invalid JSON body") from None

    async def form(self) -> FormData:
        """Reads the body as a form: each field name maps to its values, in
        order. Files from a `multipart/form-data` form are `UploadedFile`s.
        Any other content type is answered with a 415 error, and text that
        can't be decoded with a 400."""
        if self._form is None:
            self._form = await self._read_form()
        return self._form

    async def _read_form(self) -> FormData:
        content_type = Message()
        content_type["content-type"] = self.headers.get("content-type") or ""
        kind = content_type.get_content_type()
        try:
            if kind == "application/x-www-form-urlencoded":
                return parse_qs((await self.body()).decode(), keep_blank_values=True)  # type: ignore[return-value]
            if kind == "multipart/form-data" and content_type.get_param("boundary"):
                return _parse_multipart(await self.body(), str(content_type.get_param("boundary")))
        except (UnicodeDecodeError, LookupError):  # bad bytes, or an unknown charset
            raise HTTPException(400, "invalid form encoding") from None
        raise HTTPException(415, "expected a form (urlencoded or multipart)")

    def url_for(self, name: str, /, **params: Any) -> str:
        """The full URL of the route named `name`, like `App.url_for()`
        gives its path: `"https://example.com/users/42"`. The scheme is
        the request's (`ws` or `wss` for a WebSocket route). The host is
        the route's own for an `app.host()` route, its parameters given
        here too (`tenant="acme"`), with the request's port; for other
        routes, it's the request's `Host` header.

        That header comes from the client: for a link sent elsewhere (an
        email to reset a password), build it from a base URL you
        configure and `app.url_for()`, not from the request."""
        if self.app is None:
            raise RuntimeError("this request doesn't come from an App: url_for() needs one to find the route")
        from korvex import _urls as urls

        return urls.url_for(self, urls.lookup(self.app._url_index(), name), params)

    def __repr__(self) -> str:
        return f"Request({self.method} {self.path!r})"


class WebSocketDisconnect(Exception):
    """Raised by `WebSocket.receive()` once the client has disconnected."""

    def __init__(self, code: int = 1000) -> None:
        self.code = code
        super().__init__(code)


class WebSocket(Request):
    """A WebSocket connection, passed to `@app.websocket` handlers. It has
    the handshake request's headers, cookies, params, client and scheme
    (`"ws"` or `"wss"`), so the handler can check who connects before
    calling `accept()`.

        @app.websocket("/ws")
        async def echo(ws):
            await ws.accept()
            async for message in ws:
                await ws.send_text(message)
    """

    __slots__ = ("subprotocols", "_send", "_connected", "_accepted", "_closed")

    def __init__(
        self, scope: dict, receive: Callable, send: Callable, path_params: Mapping[str, Any], app: App | None = None
    ) -> None:
        super().__init__(scope, receive, path_params, app)
        self._send = send
        self._connected = False
        self._accepted = False
        self._closed = False
        self.subprotocols: list[str] = list(scope.get("subprotocols", []))
        """Subprotocols the client asked for, to pick one in `accept()`."""

    async def accept(self, subprotocol: str | None = None, headers: Iterable[tuple[str, str]] = ()) -> None:
        """Accepts the connection, optionally with one of `subprotocols`."""
        await self._wait_connect()
        await self._send(
            {
                "type": "websocket.accept",
                "subprotocol": subprotocol,
                "headers": [(name.lower().encode("latin-1"), value.encode("latin-1")) for name, value in headers],
            }
        )
        self._accepted = True

    async def receive(self) -> str | bytes:
        """The next message, text or binary. Raises `WebSocketDisconnect`
        when the client has gone."""
        message = await self._receive()
        if message["type"] == "websocket.disconnect":
            self._closed = True
            raise WebSocketDisconnect(message.get("code", 1000))
        if message.get("text") is not None:
            return message["text"]
        return message.get("bytes") or b""

    async def receive_text(self) -> str:
        message = await self.receive()
        if not isinstance(message, str):
            raise TypeError("expected a text message, got binary")
        return message

    async def receive_bytes(self) -> bytes:
        message = await self.receive()
        if not isinstance(message, bytes):
            raise TypeError("expected a binary message, got text")
        return message

    async def receive_json(self) -> JSONValue:
        return json.loads(await self.receive())

    async def send_text(self, text: str) -> None:
        await self._send({"type": "websocket.send", "text": text})

    async def send_bytes(self, data: bytes) -> None:
        await self._send({"type": "websocket.send", "bytes": data})

    async def send_json(self, data: Any) -> None:
        await self.send_text(json.dumps(data))

    async def close(self, code: int = 1000, reason: str = "") -> None:
        """Closes the connection. Before `accept()`, this refuses it: the
        client gets a 403."""
        if self._closed:
            return
        await self._wait_connect()
        await self._send({"type": "websocket.close", "code": code, "reason": reason})
        self._closed = True

    async def __aiter__(self) -> AsyncIterator[str | bytes]:
        """The messages, until the client disconnects."""
        while True:
            try:
                yield await self.receive()
            except WebSocketDisconnect:
                return

    async def _wait_connect(self) -> None:
        """ASGI starts with a `websocket.connect` message to receive first."""
        if not self._connected:
            self._connected = True
            await self._receive()

    async def body(self) -> bytes:
        raise TypeError("a WebSocket has no body: use receive()")

    async def json(self) -> JSONValue:
        raise TypeError("a WebSocket has no body: use receive_json()")

    async def form(self) -> FormData:
        raise TypeError("a WebSocket has no form")

    def __repr__(self) -> str:
        return f"WebSocket({self.path!r})"


def _parse_multipart(body: bytes, boundary: str) -> FormData:
    """Splits a `multipart/form-data` body on its boundary. Each part is a
    few header lines, a blank line, then the field's value."""
    form: FormData = {}
    for part in body.split(b"--" + boundary.encode())[1:]:
        if part.startswith(b"--"):  # the closing boundary
            break
        head, _, data = part.removeprefix(b"\r\n").partition(b"\r\n\r\n")
        headers = Message()
        for line in head.decode("utf-8", "replace").split("\r\n"):
            name, _, value = line.partition(":")
            headers[name.strip()] = value.strip()
        field = headers.get_param("name", header="content-disposition")
        if not isinstance(field, str):
            continue
        data = data.removesuffix(b"\r\n")
        filename = headers.get_filename()
        value: str | UploadedFile
        if filename is None:
            value = data.decode(headers.get_content_charset() or "utf-8")
        else:
            value = UploadedFile(filename, headers.get_content_type(), data)
        form.setdefault(field, []).append(value)
    return form
