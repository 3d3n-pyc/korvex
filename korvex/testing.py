"""A test client: runs an ASGI app in-process, the way a browser would use
it, with no server and no network.

    from korvex.testing import TestClient

    def test_login():
        with TestClient(app) as client:          # startup hooks; shutdown on exit
            response = client.post("/login", form={"password": "s3cret"})
            assert response.status == 303
            assert client.get("/api/me").json() == {"name": "admin"}

`TestClient` is for regular (sync) tests; `AsyncTestClient` does the same
from `async` tests (pytest-asyncio, anyio), awaited:

    async with AsyncTestClient(app) as client:
        response = await client.get("/health")

It takes any ASGI app, Korvex's or not.
"""

from __future__ import annotations

import asyncio
import contextlib
import json as jsonlib
import logging
import mimetypes
import threading
import uuid
import weakref
from collections.abc import AsyncIterator, Awaitable, Iterable, Iterator, Mapping
from email.utils import parsedate_to_datetime
from http.cookies import CookieError, SimpleCookie
from types import TracebackType
from typing import Any, Callable, TypeVar, Union
from urllib.parse import quote, unquote, urlencode, urljoin, urlsplit

from korvex._requests import WebSocketDisconnect
from korvex._response import Headers

__all__ = ["AsyncTestClient", "AsyncTestWebSocket", "TestClient", "TestResponse", "TestWebSocket"]

# `Union` rather than `|`: these lines run at import time.
HeadersLike = Union[Mapping[str, str], Iterable[tuple[str, str]]]
FileSpec = Union[tuple[str, Union[bytes, str]], tuple[str, Union[bytes, str], str]]
Files = Union[Mapping[str, FileSpec], Iterable[tuple[str, FileSpec]]]
Form = Mapping[str, Union[str, Iterable[str]]]
Query = Union[Mapping[str, Any], Iterable[tuple[str, Any]], str]

_UNSET: Any = object()
_DONE = {"type": "korvex.testing.done"}  # the app's task ended
_REDIRECTS = (301, 302, 303, 307, 308)
_MAX_REDIRECTS = 20
T = TypeVar("T")


class TestResponse:
    """A response the app sent: `status`, `headers` (case-insensitive,
    repeated names kept), `body` (bytes), `text` and `json()`. `url` is
    the URL it answered, the last one when redirects were followed."""

    __test__ = False  # not a test class, despite its name
    __slots__ = ("status", "headers", "body", "url")

    def __init__(self, status: int, headers: Headers, body: bytes, url: str) -> None:
        self.status = status
        self.headers = headers
        self.body = body
        self.url = url

    @property
    def text(self) -> str:
        """The body, decoded with the charset of its `Content-Type` (UTF-8 by default)."""
        return self.body.decode(_charset(self.headers))

    def json(self) -> Any:
        """The body, parsed as JSON."""
        return jsonlib.loads(self.body)

    def __repr__(self) -> str:
        return f"TestResponse({self.status}, {len(self.body)} bytes)"


class AsyncTestStream:
    """A response read as the app sends it, from `AsyncTestClient.stream()`:
    `status` and `headers` right away, then the body with `aiter_bytes()`,
    `aiter_text()` or `aread()`."""

    __test__ = False

    def __init__(self, call: _Call, status: int, headers: Headers, url: str) -> None:
        self._call = call
        self.status = status
        self.headers = headers
        self.url = url

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        """The body, chunk by chunk, as the app sends it."""
        while not self._call.complete:
            chunk = await self._call.next_chunk()
            if chunk:
                yield chunk

    async def aiter_text(self) -> AsyncIterator[str]:
        async for chunk in self.aiter_bytes():
            yield chunk.decode(_charset(self.headers))

    async def aread(self) -> bytes:
        """The rest of the body, once the app has sent it all."""
        return b"".join([chunk async for chunk in self.aiter_bytes()])


class TestStream:
    """`AsyncTestStream` for `TestClient.stream()`: `iter_bytes()`,
    `iter_text()` and `read()`."""

    __test__ = False

    def __init__(self, stream: AsyncTestStream, run: Callable[[Awaitable[Any]], Any]) -> None:
        self._stream = stream
        self._run = run
        self.status = stream.status
        self.headers = stream.headers
        self.url = stream.url

    def iter_bytes(self) -> Iterator[bytes]:
        chunks = self._stream.aiter_bytes()
        while True:
            try:
                yield self._run(chunks.__anext__())
            except StopAsyncIteration:
                return

    def iter_text(self) -> Iterator[str]:
        for chunk in self.iter_bytes():
            yield chunk.decode(_charset(self.headers))

    def read(self) -> bytes:
        return self._run(self._stream.aread())


class AsyncTestClient:
    """Calls an ASGI app in-process, from `async` tests. As an async
    context manager, it runs the app's lifespan: startup hooks on entry,
    shutdown hooks on exit. Without it, requests work, but no hook runs.

    - `base_url`: the scheme and host requests go to, unless a request
      gives a full URL (`"https://acme.example.com/"`).
    - `headers` and `cookies`: sent with every request. Cookies the app
      sets are kept in `cookies` (a dict), and sent back.
    - `raise_server_errors`: an error the app logs as unhandled (the ones
      it answers with a 500, a failed hook or dependency teardown) is
      raised in the test, with its traceback. False gets the 500 instead.
    - `timeout`: seconds to wait for a response or a WebSocket message,
      then `TimeoutError`, so a stuck handler fails the test rather than
      hanging it. None waits forever.
    """

    __test__ = False

    def __init__(
        self,
        app: Callable[..., Awaitable[None]],
        *,
        base_url: str = "http://testserver",
        headers: HeadersLike | None = None,
        cookies: Mapping[str, str] | None = None,
        raise_server_errors: bool = True,
        timeout: float | None = 5.0,
    ) -> None:
        self.app = app
        self.base_url = base_url.rstrip("/")
        self.headers = Headers(_pairs(headers))
        self.cookies: dict[str, str] = dict(cookies or {})
        self._secure: set[str] = set()  # cookies sent over https/wss only
        self.raise_server_errors = raise_server_errors
        self.timeout = timeout
        self._lifespan: _Lifespan | None = None

    async def __aenter__(self) -> AsyncTestClient:
        self._lifespan = _Lifespan(self.app, self.timeout)
        await self._lifespan.startup()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        lifespan, self._lifespan = self._lifespan, None
        if lifespan is not None:
            await lifespan.shutdown()

    async def request(
        self,
        method: str,
        url: str,
        *,
        query: Query | None = None,
        headers: HeadersLike | None = None,
        cookies: Mapping[str, str] | None = None,
        json: Any = _UNSET,
        form: Form | None = None,
        files: Files | None = None,
        body: bytes | str | None = None,
        follow_redirects: bool = False,
    ) -> TestResponse:
        """Sends a request and returns the whole response. Give the body as
        `json` (any JSON value), `form` (fields, with `files` for a
        multipart form: `{"avatar": ("me.png", data, "image/png")}`), or
        raw `body`. With `follow_redirects`, a redirect is followed (a 303,
        or a 301/302 after a POST, as a GET without body), up to 20."""
        content_type, data = _encode_body(json, form, files, body)
        for _ in range(_MAX_REDIRECTS + 1):
            call = self._call(method, url, query, headers, cookies, content_type, data)
            with _ErrorCatcher(self.raise_server_errors) as errors:
                try:
                    status, response_headers = await call.start()
                    chunks = [chunk async for chunk in _chunks(call)]
                finally:
                    await call.finish()
                errors.check()
            response = TestResponse(status, response_headers, b"".join(chunks), call.url)
            self._keep_cookies(response.headers)
            location = response.headers.get("location")
            if not follow_redirects or status not in _REDIRECTS or location is None:
                return response
            url, query = urljoin(call.url, location), None
            if status == 303 or (status in (301, 302) and method == "POST"):
                method, content_type, data = "GET", None, b""
        raise RuntimeError(f"more than {_MAX_REDIRECTS} redirects, last to {url}")

    def stream(
        self,
        method: str,
        url: str,
        *,
        query: Query | None = None,
        headers: HeadersLike | None = None,
        cookies: Mapping[str, str] | None = None,
        json: Any = _UNSET,
        form: Form | None = None,
        files: Files | None = None,
        body: bytes | str | None = None,
    ) -> _StreamContext:
        """Sends a request and gives the response before its body is read,
        to read it as it comes, for a stream that doesn't end (events):

            async with client.stream("GET", "/events") as response:
                async for chunk in response.aiter_bytes(): ...

        Leaving the block stops the app's handler, as a client going away
        would: its `finally` blocks and dependency teardowns run."""
        content_type, data = _encode_body(json, form, files, body)
        return _StreamContext(self, self._call(method, url, query, headers, cookies, content_type, data))

    async def get(self, url: str, **kwargs: Any) -> TestResponse:
        """A GET request; takes the same arguments as `request()`."""
        return await self.request("GET", url, **kwargs)

    async def head(self, url: str, **kwargs: Any) -> TestResponse:
        return await self.request("HEAD", url, **kwargs)

    async def options(self, url: str, **kwargs: Any) -> TestResponse:
        return await self.request("OPTIONS", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> TestResponse:
        return await self.request("POST", url, **kwargs)

    async def put(self, url: str, **kwargs: Any) -> TestResponse:
        return await self.request("PUT", url, **kwargs)

    async def patch(self, url: str, **kwargs: Any) -> TestResponse:
        return await self.request("PATCH", url, **kwargs)

    async def delete(self, url: str, **kwargs: Any) -> TestResponse:
        return await self.request("DELETE", url, **kwargs)

    def websocket(
        self,
        url: str,
        *,
        subprotocols: Iterable[str] = (),
        query: Query | None = None,
        headers: HeadersLike | None = None,
        cookies: Mapping[str, str] | None = None,
    ) -> AsyncTestWebSocket:
        """Opens a WebSocket, as an async context manager:

            async with client.websocket("/ws") as ws:
                await ws.send_text("hi")
                assert await ws.receive_text() == "hi"

        Entering raises `WebSocketDisconnect` if the app refuses the
        connection (closes it before accepting)."""
        scope = self._scope("websocket", "GET", url, query, headers, cookies)
        scope["subprotocols"] = list(subprotocols)
        return AsyncTestWebSocket(self, scope)

    # --- internals ---

    def _call(
        self,
        method: str,
        url: str,
        query: Query | None,
        headers: HeadersLike | None,
        cookies: Mapping[str, str] | None,
        content_type: str | None,
        data: bytes,
    ) -> _Call:
        extra = list(_pairs(headers))
        names = {name.lower() for name, _ in extra}
        if content_type is not None and "content-type" not in names:
            extra.append(("content-type", content_type))
        if data and "content-length" not in names:
            extra.append(("content-length", str(len(data))))
        scope = self._scope("http", method.upper(), url, query, extra, cookies)
        return _Call(self.app, scope, data, self.timeout)

    def _scope(
        self,
        kind: str,
        method: str,
        url: str,
        query: Query | None,
        headers: HeadersLike | None,
        cookies: Mapping[str, str] | None,
    ) -> dict[str, Any]:
        base = urlsplit(self.base_url)
        target = urlsplit(urljoin(self.base_url + "/", url))
        scheme = target.scheme or base.scheme or "http"
        secure = scheme in ("https", "wss")
        if kind == "websocket":
            scheme = "wss" if secure else "ws"
        elif scheme in ("ws", "wss"):
            scheme = "https" if secure else "http"
        netloc = target.netloc or base.netloc or "testserver"
        query_string = target.query
        if query:
            extra = query if isinstance(query, str) else urlencode(list(_pairs(query)), doseq=True)
            query_string = f"{query_string}&{extra}" if query_string else extra
        given = list(self.headers) + list(_pairs(headers))
        names = {name.lower() for name, _ in given}
        all_headers = [("host", netloc)] if "host" not in names else []
        all_headers += [("user-agent", "testclient")] if "user-agent" not in names else []
        all_headers += given
        jar = {name: value for name, value in self.cookies.items() if secure or name not in self._secure}
        jar.update(cookies or {})
        if jar and "cookie" not in names:
            all_headers.append(("cookie", "; ".join(f"{name}={value}" for name, value in jar.items())))
        host, _, port = netloc.rpartition(":") if not netloc.endswith("]") else (netloc, "", "")
        default_port = 443 if secure else 80
        path = target.path or "/"
        raw_path = quote(path, safe="/%:@!$&'()*+,;=-._~")
        query_string = quote(query_string, safe="=&%:@!$'()*+,;/?-._~")
        scope: dict[str, Any] = {
            "type": kind,
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "scheme": scheme,
            "path": unquote(path),
            "raw_path": raw_path.encode("ascii"),
            "query_string": query_string.encode("ascii"),
            "root_path": "",
            "headers": [(name.lower().encode("latin-1"), str(value).encode("latin-1")) for name, value in all_headers],
            "client": ("testclient", 50000),
            "server": (host or netloc, int(port) if port.isdigit() else default_port),
        }
        if kind == "http":
            scope["method"] = method
        return scope

    def _keep_cookies(self, headers: Headers) -> None:
        """Keeps the cookies a response sets, and forgets those it deletes."""
        for header in headers.get_all("set-cookie"):
            try:
                parsed = SimpleCookie(header)
            except CookieError:
                continue
            for morsel in parsed.values():
                if _expired(morsel):
                    self.cookies.pop(morsel.key, None)
                    self._secure.discard(morsel.key)
                    continue
                self.cookies[morsel.key] = morsel.value
                if morsel["secure"]:
                    self._secure.add(morsel.key)
                else:
                    self._secure.discard(morsel.key)


class _StreamContext:
    """What `AsyncTestClient.stream()` returns: an async context manager."""

    def __init__(self, client: AsyncTestClient, call: _Call) -> None:
        self._client = client
        self._call = call
        self._catcher = _ErrorCatcher(client.raise_server_errors)

    async def __aenter__(self) -> AsyncTestStream:
        self._catcher.__enter__()
        try:
            status, headers = await self._call.start()
        except BaseException:
            await self._call.finish()
            self._catcher.__exit__(None, None, None)
            raise
        self._client._keep_cookies(headers)
        return AsyncTestStream(self._call, status, headers, self._call.url)

    async def __aexit__(self, *exc_info: object) -> None:
        try:
            await self._call.finish()
        finally:
            self._catcher.__exit__(None, None, None)
        if exc_info[0] is None:
            self._catcher.check()


class _Call:
    """One HTTP request running in the app: its task, and the messages the
    app sends, as they come."""

    def __init__(self, app: Callable[..., Awaitable[None]], scope: dict, body: bytes, timeout: float | None) -> None:
        self.app = app
        self.scope = scope
        self.body = body
        self.timeout = timeout
        self.complete = False  # the whole body has been received
        scheme, host = scope["scheme"], dict(scope["headers"]).get(b"host", b"").decode("latin-1")
        query = scope["query_string"].decode("latin-1")
        self.url = f"{scheme}://{host}{scope['raw_path'].decode('latin-1')}" + (f"?{query}" if query else "")
        self._task: asyncio.Task | None = None

    async def start(self) -> tuple[int, Headers]:
        """Starts the app; returns the response's status and headers."""
        self._sent: asyncio.Queue = asyncio.Queue()
        self._gone = asyncio.Event()  # the client is gone: the app gets http.disconnect
        body_sent = False

        async def receive() -> dict:
            nonlocal body_sent
            if not body_sent:
                body_sent = True
                return {"type": "http.request", "body": self.body, "more_body": False}
            await self._gone.wait()
            return {"type": "http.disconnect"}

        self._task = asyncio.ensure_future(self.app(self.scope, receive, self._sent.put))
        self._task.add_done_callback(lambda _: self._sent.put_nowait(_DONE))
        message = await self._next()
        if message["type"] != "http.response.start":
            raise RuntimeError(f"the app sent {message['type']!r} before http.response.start")
        headers = Headers((k.decode("latin-1"), v.decode("latin-1")) for k, v in message.get("headers", []))
        return message["status"], headers

    async def next_chunk(self) -> bytes:
        message = await self._next()
        if message["type"] != "http.response.body":
            raise RuntimeError(f"the app sent {message['type']!r} in the middle of the body")
        self.complete = not message.get("more_body", False)
        return message.get("body", b"")

    async def finish(self) -> None:
        """Ends the exchange: the client is gone. An app still sending (an
        endless stream) is cancelled, so its cleanup runs."""
        task = self._task
        if task is None:
            return
        self._gone.set()
        if not self.complete and not task.done():
            task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), self.timeout)
        except asyncio.CancelledError:
            if not task.cancelled():
                raise  # we were cancelled ourselves
        except asyncio.TimeoutError:
            task.cancel()
            raise TimeoutError(f"{self._where()}: the app still runs {self.timeout}s after its response") from None

    async def _next(self) -> dict:
        try:
            message = await asyncio.wait_for(self._sent.get(), self.timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(f"{self._where()}: no response from the app after {self.timeout}s") from None
        if message is _DONE:
            assert self._task is not None
            if not self._task.cancelled() and self._task.exception() is not None:
                raise self._task.exception()  # type: ignore[misc]  # the app itself raised
            raise RuntimeError(f"{self._where()}: the app returned without completing its response")
        return message

    def _where(self) -> str:
        return f"{self.scope['method']} {self.scope['path']}"


async def _chunks(call: _Call) -> AsyncIterator[bytes]:
    while not call.complete:
        yield await call.next_chunk()


class AsyncTestWebSocket:
    """A WebSocket open on the app, from `AsyncTestClient.websocket()`.
    `subprotocol` and `headers` are those the app accepted with. Each
    `receive...()` waits up to the client's timeout (or `timeout`)."""

    __test__ = False

    def __init__(self, client: AsyncTestClient, scope: dict) -> None:
        self._client = client
        self._scope = scope
        self._task: asyncio.Task | None = None
        self._closed = False  # the app closed it, or we did
        self._gone = False  # we disconnected
        self.subprotocol: str | None = None
        self.headers = Headers()
        self._catcher = _ErrorCatcher(client.raise_server_errors)

    async def __aenter__(self) -> AsyncTestWebSocket:
        self._to_app: asyncio.Queue = asyncio.Queue()
        self._from_app: asyncio.Queue = asyncio.Queue()
        self._catcher.__enter__()
        try:
            self._task = asyncio.ensure_future(self._client.app(self._scope, self._to_app.get, self._send))
            self._task.add_done_callback(lambda _: self._from_app.put_nowait(_DONE))
            await self._to_app.put({"type": "websocket.connect"})
            message = await self._next(_UNSET)
            if message["type"] == "websocket.close":
                self._closed = True
                raise WebSocketDisconnect(message.get("code", 1000))
            if message["type"] != "websocket.accept":
                raise RuntimeError(f"the app sent {message['type']!r} before accepting the WebSocket")
        except BaseException:
            await self._end()
            self._catcher.__exit__(None, None, None)
            raise
        self.subprotocol = message.get("subprotocol")
        self.headers = Headers((k.decode("latin-1"), v.decode("latin-1")) for k, v in message.get("headers") or [])
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        try:
            await self.close()
        finally:
            self._catcher.__exit__(None, None, None)
        if exc_info[0] is None:
            self._catcher.check()

    async def _send(self, message: dict) -> None:
        """The app's `send`: once the client is gone, it raises, as a
        server's does."""
        if self._gone:
            raise OSError("the WebSocket is closed")
        await self._from_app.put(message)

    async def send_text(self, text: str) -> None:
        await self._to_app.put({"type": "websocket.receive", "text": text})

    async def send_bytes(self, data: bytes) -> None:
        await self._to_app.put({"type": "websocket.receive", "bytes": data})

    async def send_json(self, data: Any) -> None:
        await self.send_text(jsonlib.dumps(data))

    async def receive(self, timeout: float | None = _UNSET) -> str | bytes:
        """The next message the app sends, text or bytes. Raises
        `WebSocketDisconnect` once the app has closed the connection."""
        if self._closed:
            raise WebSocketDisconnect(1000)
        message = await self._next(timeout)
        self._catcher.check()
        if message["type"] == "websocket.close":
            self._closed = True
            raise WebSocketDisconnect(message.get("code", 1000))
        if message["type"] != "websocket.send":
            raise RuntimeError(f"unexpected message from the app: {message['type']!r}")
        return message["text"] if message.get("text") is not None else message.get("bytes") or b""

    async def receive_text(self, timeout: float | None = _UNSET) -> str:
        message = await self.receive(timeout)
        if not isinstance(message, str):
            raise TypeError("expected a text message, got binary")
        return message

    async def receive_bytes(self, timeout: float | None = _UNSET) -> bytes:
        message = await self.receive(timeout)
        if not isinstance(message, bytes):
            raise TypeError("expected a binary message, got text")
        return message

    async def receive_json(self, timeout: float | None = _UNSET) -> Any:
        return jsonlib.loads(await self.receive(timeout))

    async def close(self, code: int = 1000) -> None:
        """Disconnects (if the app hasn't closed first) and waits for the
        app's handler to end."""
        if not self._closed:
            self._closed = self._gone = True
            await self._to_app.put({"type": "websocket.disconnect", "code": code})
        await self._end()

    async def _end(self) -> None:
        task = self._task
        if task is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), self._client.timeout)
        except asyncio.TimeoutError:
            task.cancel()
            raise TimeoutError(f"WebSocket {self._scope['path']}: the app still runs after the disconnect") from None

    async def _next(self, timeout: float | None) -> dict:
        wait = self._client.timeout if timeout is _UNSET else timeout
        try:
            message = await asyncio.wait_for(self._from_app.get(), wait)
        except asyncio.TimeoutError:
            raise TimeoutError(f"WebSocket {self._scope['path']}: no message from the app after {wait}s") from None
        if message is _DONE:
            self._from_app.put_nowait(_DONE)  # for the next call too
            assert self._task is not None
            if not self._task.cancelled() and self._task.exception() is not None:
                raise self._task.exception()  # type: ignore[misc]
            self._closed = True
            raise WebSocketDisconnect(1000)  # the app ended without closing
        return message


class TestClient:
    """Calls an ASGI app in-process, from regular tests: `AsyncTestClient`
    run on an event loop of its own. As a context manager, it runs the
    app's lifespan (startup hooks on entry, shutdown hooks on exit);
    without it, requests work, but no hook runs.

        with TestClient(app) as client:
            assert client.get("/health").status == 200

    Every request runs on the same event loop, in the test's thread, so
    what startup hooks keep in `app.local` is there for the handlers. The
    loop runs during the client's calls only: a task the app starts in
    the background makes progress while the test sends a request or
    waits for a WebSocket message. In an `async` test, where a loop is
    already running, use `AsyncTestClient`. See it for the arguments."""

    __test__ = False

    def __init__(
        self,
        app: Callable[..., Awaitable[None]],
        *,
        base_url: str = "http://testserver",
        headers: HeadersLike | None = None,
        cookies: Mapping[str, str] | None = None,
        raise_server_errors: bool = True,
        timeout: float | None = 5.0,
    ) -> None:
        self._client = AsyncTestClient(
            app,
            base_url=base_url,
            headers=headers,
            cookies=cookies,
            raise_server_errors=raise_server_errors,
            timeout=timeout,
        )
        self._loop: asyncio.AbstractEventLoop | None = None
        self._close_loop: weakref.finalize | None = None

    @property
    def app(self) -> Callable[..., Awaitable[None]]:
        return self._client.app

    @property
    def cookies(self) -> dict[str, str]:
        """The cookies sent with each request: those the app set, and any
        added here."""
        return self._client.cookies

    @property
    def headers(self) -> Headers:
        """Headers sent with each request."""
        return self._client.headers

    def __enter__(self) -> TestClient:
        self._run(self._client.__aenter__())
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        try:
            self._run(self._client.__aexit__(exc_type, exc, traceback))
        finally:
            self.close()

    def close(self) -> None:
        """Closes the client's event loop, cancelling what the app left
        running on it. Leaving the `with` block does it."""
        if self._close_loop is not None:
            self._close_loop()
        self._loop = None

    def request(self, method: str, url: str, **kwargs: Any) -> TestResponse:
        """Sends a request and returns the whole response: see
        `AsyncTestClient.request()` for the arguments."""
        return self._run(self._client.request(method, url, **kwargs))

    def get(self, url: str, **kwargs: Any) -> TestResponse:
        return self.request("GET", url, **kwargs)

    def head(self, url: str, **kwargs: Any) -> TestResponse:
        return self.request("HEAD", url, **kwargs)

    def options(self, url: str, **kwargs: Any) -> TestResponse:
        return self.request("OPTIONS", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> TestResponse:
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> TestResponse:
        return self.request("PUT", url, **kwargs)

    def patch(self, url: str, **kwargs: Any) -> TestResponse:
        return self.request("PATCH", url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> TestResponse:
        return self.request("DELETE", url, **kwargs)

    def stream(self, method: str, url: str, **kwargs: Any) -> _SyncStreamContext:
        """Sends a request and gives the response before its body is read:

            with client.stream("GET", "/events") as response:
                for chunk in response.iter_bytes(): ...

        Leaving the block stops the app's handler, as a client going away
        would. See `AsyncTestClient.stream()` for the arguments."""
        return _SyncStreamContext(self, self._client.stream(method, url, **kwargs))

    def websocket(self, url: str, **kwargs: Any) -> TestWebSocket:
        """Opens a WebSocket, as a context manager:

            with client.websocket("/ws") as ws:
                ws.send_text("hi")
                assert ws.receive_text() == "hi"

        Entering raises `WebSocketDisconnect` if the app refuses the
        connection. See `AsyncTestClient.websocket()` for the arguments."""
        return TestWebSocket(self._client.websocket(url, **kwargs), self._run)

    def _run(self, awaitable: Awaitable[T]) -> T:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            if asyncio.iscoroutine(awaitable):
                awaitable.close()  # never awaited: no "never awaited" warning
            raise RuntimeError("TestClient can't run inside a running event loop (an async test): use AsyncTestClient")
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
            self._close_loop = weakref.finalize(self, _close_loop, self._loop)
        return self._loop.run_until_complete(awaitable)  # type: ignore[arg-type]


class _SyncStreamContext:
    def __init__(self, client: TestClient, context: _StreamContext) -> None:
        self._client = client
        self._context = context

    def __enter__(self) -> TestStream:
        return TestStream(self._client._run(self._context.__aenter__()), self._client._run)

    def __exit__(self, *exc_info: object) -> None:
        self._client._run(self._context.__aexit__(*exc_info))


class TestWebSocket:
    """A WebSocket open on the app, from `TestClient.websocket()`: the
    methods of `AsyncTestWebSocket`, not awaited."""

    __test__ = False

    def __init__(self, websocket: AsyncTestWebSocket, run: Callable[[Awaitable[Any]], Any]) -> None:
        self._websocket = websocket
        self._run = run

    @property
    def subprotocol(self) -> str | None:
        return self._websocket.subprotocol

    @property
    def headers(self) -> Headers:
        return self._websocket.headers

    def __enter__(self) -> TestWebSocket:
        self._run(self._websocket.__aenter__())
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._run(self._websocket.__aexit__(*exc_info))

    def send_text(self, text: str) -> None:
        self._run(self._websocket.send_text(text))

    def send_bytes(self, data: bytes) -> None:
        self._run(self._websocket.send_bytes(data))

    def send_json(self, data: Any) -> None:
        self._run(self._websocket.send_json(data))

    def receive(self, timeout: float | None = _UNSET) -> str | bytes:
        return self._run(self._websocket.receive(timeout))

    def receive_text(self, timeout: float | None = _UNSET) -> str:
        return self._run(self._websocket.receive_text(timeout))

    def receive_bytes(self, timeout: float | None = _UNSET) -> bytes:
        return self._run(self._websocket.receive_bytes(timeout))

    def receive_json(self, timeout: float | None = _UNSET) -> Any:
        return self._run(self._websocket.receive_json(timeout))

    def close(self, code: int = 1000) -> None:
        self._run(self._websocket.close(code))


class _Lifespan:
    """The app's lifespan: startup, then shutdown. An app that doesn't
    support it (raises on the `lifespan` scope) runs without."""

    def __init__(self, app: Callable[..., Awaitable[None]], timeout: float | None) -> None:
        self.app = app
        self.timeout = timeout
        self._task: asyncio.Task | None = None

    async def startup(self) -> None:
        self._to_app: asyncio.Queue = asyncio.Queue()
        self._from_app: asyncio.Queue = asyncio.Queue()
        scope = {"type": "lifespan", "asgi": {"version": "3.0", "spec_version": "2.0"}, "state": {}}
        self._task = asyncio.ensure_future(self.app(scope, self._to_app.get, self._from_app.put))
        self._task.add_done_callback(lambda _: self._from_app.put_nowait(_DONE))
        await self._to_app.put({"type": "lifespan.startup"})
        message = await self._next("startup")
        if message is _DONE:
            self._task = None  # no lifespan support: carry on without
            return
        if message["type"] == "lifespan.startup.failed":
            await self._wait()
            raise RuntimeError(f"the app's startup failed: {message.get('message', '')}")

    async def shutdown(self) -> None:
        if self._task is None:
            return
        await self._to_app.put({"type": "lifespan.shutdown"})
        message = await self._next("shutdown")
        await self._wait()
        if message is not _DONE and message["type"] == "lifespan.shutdown.failed":
            raise RuntimeError(f"the app's shutdown failed: {message.get('message', '')}")

    async def _next(self, event: str) -> dict:
        try:
            return await asyncio.wait_for(self._from_app.get(), self.timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(f"the app's {event} took more than {self.timeout}s") from None

    async def _wait(self) -> None:
        if self._task is not None:
            with contextlib.suppress(Exception):  # its outcome was in the message
                await asyncio.wait_for(self._task, self.timeout)


class _ErrorCatcher(logging.Handler):
    """Collects the errors Korvex logs as unhandled while it's installed,
    from this thread, to raise the first one in the test."""

    def __init__(self, enabled: bool) -> None:
        super().__init__(logging.ERROR)
        self.enabled = enabled
        self.errors: list[BaseException] = []
        self._thread = threading.get_ident()
        self._installed = False

    def __enter__(self) -> _ErrorCatcher:
        if self.enabled and not self._installed:
            logging.getLogger("korvex").addHandler(self)
            self._installed = True
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._installed:
            logging.getLogger("korvex").removeHandler(self)
            self._installed = False

    def emit(self, record: logging.LogRecord) -> None:
        if record.thread == self._thread and record.exc_info and record.exc_info[1] is not None:
            self.errors.append(record.exc_info[1])

    def check(self) -> None:
        if self.errors:
            error, self.errors = self.errors[0], []
            raise error


def _close_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Cancels what's left on the loop (WebSockets left open, the app's
    background tasks), lets it finish, and closes the loop."""
    if loop.is_closed() or loop.is_running():
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:  # none: ours can run, to clean up
        pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.run_until_complete(loop.shutdown_asyncgens())
    loop.close()  # with another loop running (collected mid-test): just closed


def _pairs(items: Any) -> Iterator[tuple[str, Any]]:
    """`(name, value)` pairs from a mapping or an iterable of pairs."""
    if items is None:
        return iter(())
    if isinstance(items, Headers):
        return iter(items)
    if isinstance(items, Mapping):
        return iter(items.items())
    return iter(items)


def _encode_body(
    json: Any, form: Form | None, files: Files | None, body: bytes | str | None
) -> tuple[str | None, bytes]:
    """The content type and bytes of a request body given one way or another."""
    given = [
        name
        for name, value in (
            ("json", json is not _UNSET),
            ("form", form is not None or files is not None),
            ("body", body is not None),
        )
        if value
    ]
    if len(given) > 1:
        raise TypeError(f"give the body one way only, not {' and '.join(given)}")
    if json is not _UNSET:
        return "application/json", jsonlib.dumps(json).encode()
    if files is not None:
        return _multipart(form or {}, files)
    if form is not None:
        fields = [
            (name, value)
            for name, values in form.items()
            for value in ([values] if isinstance(values, str) else values)
        ]
        return "application/x-www-form-urlencoded", urlencode(fields).encode()
    if body is not None:
        return None, body.encode() if isinstance(body, str) else body
    return None, b""


def _multipart(form: Form, files: Files) -> tuple[str, bytes]:
    boundary = uuid.uuid4().hex
    parts = []
    for name, values in form.items():
        for value in [values] if isinstance(values, str) else values:
            head = f'--{boundary}\r\nContent-Disposition: form-data; name="{_quoted(name)}"\r\n\r\n'
            parts.append(head.encode() + str(value).encode() + b"\r\n")
    for name, spec in _pairs(files):
        filename, data = spec[0], spec[1]
        content_type = spec[2] if len(spec) > 2 else mimetypes.guess_type(filename)[0] or "application/octet-stream"  # type: ignore[misc]
        head = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{_quoted(name)}"; filename="{_quoted(filename)}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        )
        parts.append(head.encode() + (data.encode() if isinstance(data, str) else data) + b"\r\n")
    return f"multipart/form-data; boundary={boundary}", b"".join(parts) + f"--{boundary}--\r\n".encode()


def _quoted(value: str) -> str:
    """A name inside a quoted header parameter, as browsers write it."""
    return value.replace("\r", "%0D").replace("\n", "%0A").replace('"', "%22")


def _charset(headers: Headers) -> str:
    from email.message import Message

    message = Message()
    message["content-type"] = headers.get("content-type") or "text/plain"
    return str(message.get_content_charset() or "utf-8")


def _expired(morsel: Any) -> bool:
    """Whether a `Set-Cookie` deletes its cookie: `Max-Age` 0 or less, or
    an `Expires` in the past."""
    max_age = morsel["max-age"]
    if max_age not in ("", None):
        try:
            return int(max_age) <= 0
        except ValueError:
            return False
    if morsel["expires"]:
        try:
            import datetime

            when = parsedate_to_datetime(morsel["expires"])
            if when.tzinfo is None:
                when = when.replace(tzinfo=datetime.timezone.utc)
            return when <= datetime.datetime.now(datetime.timezone.utc)
        except (TypeError, ValueError):
            return False
    return False
