"""What a handler gets: `Request`, `WebSocket` (and `WebSocketDisconnect`),
and the `UploadedFile`s of a multipart form."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping
from email.message import Message
from typing import TYPE_CHECKING, Any, Generic, TypeVar, Union
from urllib.parse import parse_qs, unquote

from korvex._errors import HTTPException
from korvex._response import HeaderItems, Headers, JSONValue, header_pairs

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


_V = TypeVar("_V")


class MultiDict(Mapping[str, _V], Generic[_V]):
    """Names with one or more values, in order: `[name]` and `get()` give the first, `get_all()` all."""

    __slots__ = ("_lists",)

    def __init__(self, lists: dict[str, list[_V]] | None = None) -> None:
        self._lists: dict[str, list[_V]] = lists if lists is not None else {}

    def __getitem__(self, name: str) -> _V:
        return self._lists[name][0]

    def __iter__(self) -> Iterator[str]:
        return iter(self._lists)

    def __len__(self) -> int:
        return len(self._lists)

    def __contains__(self, name: object) -> bool:
        return name in self._lists

    def get_all(self, name: str) -> list[_V]:
        """Every value of `name`, in order; `[]` if there's none."""
        return list(self._lists.get(name, ()))

    def multi_items(self) -> list[tuple[str, _V]]:
        """Every `(name, value)`, a name repeated for each of its values."""
        return [(name, value) for name, values in self._lists.items() for value in values]

    def __repr__(self) -> str:
        return f"MultiDict({self.multi_items()!r})"


FormData = MultiDict[Union[str, UploadedFile]]


class Connection:
    """What an HTTP request and a WebSocket share: path, headers, cookies, query, client, scheme.

    Guide: https://3d3n-pyc.github.io/korvex/requests-and-responses/
    """

    __slots__ = (
        "method",
        "path",
        "root_path",
        "headers",
        "_query_string",
        "_query",
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
        self.method: str = scope.get("method", "GET")
        """The HTTP method; `GET` for a WebSocket's handshake."""
        path, root = scope["path"], scope.get("root_path", "")
        if root:
            root = root.rstrip("/")  # uvicorn accepts `--root-path /api/`
        if root and (path == root or path.startswith(root + "/")):
            path = path[len(root) :] or "/"  # ASGI: `path` includes `root_path`; older servers strip it
        self.path: str = path
        """The path the app routes on, without `root_path`."""
        self.root_path: str = root
        """The prefix the app is served under (ASGI `root_path`); `""` if none."""
        client = scope.get("client")
        self.client: tuple[str, int] | None = tuple(client) if client else None  # type: ignore[assignment]
        """`(host, port)` of the client, behind a trusted proxy too; `None` if unknown."""
        self.scheme: str = scope.get("scheme", "http")
        """`"https"` or `"http"` (`"wss"` or `"ws"` for a WebSocket), as the
        client used it."""
        self.headers = Headers._trusted([(k.decode("latin-1"), v.decode("latin-1")) for k, v in scope["headers"]])
        self._query_string: bytes = scope.get("query_string", b"")
        self._query: MultiDict[str] | None = None
        self.path_params = path_params
        self.app = app
        """The `App` handling it."""
        self._receive = receive
        self._cookies: dict[str, str] | None = None
        self._body: bytes | None = None  # a Request's, read once
        self._form: FormData | None = None

    @property
    def query(self) -> MultiDict[str]:
        """The query string's fields, parsed on first use."""
        if self._query is None:
            self._query = MultiDict(parse_qs(self._query_string.decode("latin-1")))
        return self._query

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

    def url_for(self, name: str, /, **params: Any) -> str:
        """The full URL of the route named `name`, with the request's scheme and host.

        The `Host` header comes from the client: for an emailed link, use a configured base URL.
        Guide: https://3d3n-pyc.github.io/korvex/routes/#links-to-a-route
        """
        if self.app is None:
            raise RuntimeError("this request doesn't come from an App: url_for() needs one to find the route")
        from korvex import _urls as urls

        url = urls.url_for(self, urls.lookup(self.app._url_index(), name), params)
        if not self.root_path:
            return url
        at = url.index("/", url.index("://") + 3)
        return f"{url[:at]}{self.root_path}{url[at:]}"

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.method} {self.path!r})"


class Request(Connection):
    """An incoming HTTP request: a `Connection`, and its body.

    Guide: https://3d3n-pyc.github.io/korvex/requests-and-responses/
    """

    __slots__ = ()

    @classmethod
    def _from_server(
        cls,
        method: str,
        path: str,
        query_string: bytes,
        headers: list[tuple[bytes, bytes]],
        client: tuple[str, int] | None,
        scheme: str,
        body: bytes,
        app: App,
        root_path: str = "",
    ) -> Request:
        """A request from the built-in server, read in full. Sets the fields
        of `Connection.__init__` directly, skipping the ASGI scope (hot path)."""
        request = cls.__new__(cls)
        request.method = method
        request.path = path
        request.root_path = root_path
        request.client = client
        request.scheme = scheme
        request.headers = Headers._trusted([(k.decode("latin-1"), v.decode("latin-1")) for k, v in headers])
        request._query_string = query_string
        request._query = None
        request.path_params = {}
        request.app = app
        request._receive = _read_already
        request._cookies = None
        request._body = body  # body() returns it without receiving
        request._form = None
        return request

    async def body(self) -> bytes:
        """Reads the whole body, once; later calls return it again."""
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
        """Reads the body as JSON; invalid JSON answers a 400."""
        try:
            return json.loads(await self.body())
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(400, "invalid JSON body") from None

    async def form(self) -> FormData:
        """Reads the body as a urlencoded or multipart form; files are `UploadedFile`s.

        Any other content type answers a 415, undecodable text a 400.
        """
        if self._form is None:
            self._form = await self._read_form()
        return self._form

    async def _read_form(self) -> FormData:
        content_type = Message()
        content_type["content-type"] = self.headers.get("content-type") or ""
        kind = content_type.get_content_type()
        try:
            if kind == "application/x-www-form-urlencoded":
                return MultiDict(parse_qs((await self.body()).decode(), keep_blank_values=True))  # type: ignore[arg-type]
            if kind == "multipart/form-data" and content_type.get_param("boundary"):
                return _parse_multipart(await self.body(), str(content_type.get_param("boundary")))
        except (UnicodeDecodeError, LookupError):  # bad bytes, or an unknown charset
            raise HTTPException(400, "invalid form encoding") from None
        raise HTTPException(415, "expected a form (urlencoded or multipart)")


async def _read_already() -> dict:
    """The `receive` of a request whose body came with it: never called."""
    raise RuntimeError("the body of a request from the built-in server is already read")


class WebSocketDisconnect(Exception):
    """Raised by `WebSocket.receive()` once the client has disconnected."""

    def __init__(self, code: int = 1000) -> None:
        self.code = code
        super().__init__(code)


class WebSocket(Connection):
    """A WebSocket connection, passed to `@app.websocket` handlers.

    Guide: https://3d3n-pyc.github.io/korvex/websockets/
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

    async def accept(self, subprotocol: str | None = None, *, headers: HeaderItems = ()) -> None:
        """Accepts the connection, optionally with one of `subprotocols`."""
        await self._wait_connect()
        await self._send(
            {
                "type": "websocket.accept",
                "subprotocol": subprotocol,
                "headers": [
                    (name.lower().encode("latin-1"), value.encode("latin-1")) for name, value in header_pairs(headers)
                ],
            }
        )
        self._accepted = True

    async def receive(self) -> str | bytes:
        """The next message, text or binary; raises `WebSocketDisconnect` once the client has gone."""
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
        """Closes the connection. Before `accept()`, it refuses it (403)."""
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

    def __repr__(self) -> str:
        return f"WebSocket({self.path!r})"


_PART_HEADER = re.compile(r"([!-9;-~]+):([^\r\n]*)")  # a name of printable ASCII, a colon, a value


def _parse_multipart(body: bytes, boundary: str) -> FormData:
    """Splits a `multipart/form-data` body on its boundary into parts: header
    lines, a blank line, the value."""
    form: dict[str, list[Union[str, UploadedFile]]] = {}
    for part in body.split(b"--" + boundary.encode())[1:]:
        if part.startswith(b"--"):  # the closing boundary
            break
        head, _, data = part.removeprefix(b"\r\n").partition(b"\r\n\r\n")
        headers = Message()
        for line in head.decode("utf-8", "replace").split("\r\n"):
            # Checked here, not left to Message: Python 3.14 refuses what 3.9-3.13 accept.
            if (header := _PART_HEADER.fullmatch(line)) is None:
                raise HTTPException(400, "invalid multipart part headers")
            headers[header[1]] = header[2].strip()
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
    return MultiDict(form)
