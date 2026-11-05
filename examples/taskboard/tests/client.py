"""A test client: calls an ASGI app in-process, like a browser would,
with its startup and shutdown, cookies and WebSockets. No server, no
network, no extra dependency."""

import asyncio
import json as jsonlib
import uuid
from http.cookies import SimpleCookie
from typing import Any, Optional
from urllib.parse import urlencode


class Result:
    def __init__(self, status: int, headers: list[tuple[str, str]], body: bytes) -> None:
        self.status = status
        self.headers = {name.lower(): value for name, value in headers}  # last value wins
        self.all_headers = headers
        self.body = body

    @property
    def text(self) -> str:
        return self.body.decode()

    def json(self) -> Any:
        return jsonlib.loads(self.body)


class Client:
    """Use as a context manager: entering starts the app (startup hooks),
    leaving stops it (shutdown hooks)."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self.cookies: dict[str, str] = {}
        self.loop = asyncio.new_event_loop()

    def __enter__(self) -> "Client":
        self.loop.run_until_complete(self._startup())
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.loop.run_until_complete(self._shutdown())
        self.loop.close()

    async def _startup(self) -> None:
        self._to_app: asyncio.Queue = asyncio.Queue()
        self._from_app: asyncio.Queue = asyncio.Queue()
        self._lifespan = asyncio.ensure_future(self.app({"type": "lifespan"}, self._to_app.get, self._from_app.put))
        await self._to_app.put({"type": "lifespan.startup"})
        message = await self._from_app.get()
        assert message["type"] == "lifespan.startup.complete", message

    async def _shutdown(self) -> None:
        await self._to_app.put({"type": "lifespan.shutdown"})
        await self._from_app.get()
        await self._lifespan

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        form: Optional[dict[str, str]] = None,
        files: Optional[dict[str, tuple[str, bytes, str]]] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> Result:
        all_headers = dict(headers or {})
        body = b""
        if json is not None:
            body, all_headers["content-type"] = jsonlib.dumps(json).encode(), "application/json"
        elif files is not None:
            body, all_headers["content-type"] = _multipart(form or {}, files)
        elif form is not None:
            body, all_headers["content-type"] = urlencode(form).encode(), "application/x-www-form-urlencoded"
        path, _, query = path.partition("?")
        scope = {
            "type": "http",
            "method": method,
            "path": path,
            "query_string": query.encode(),
            "headers": self._headers(all_headers),
            "client": ("127.0.0.1", 50000),
            "scheme": "http",
        }
        result = self.loop.run_until_complete(self._call(scope, body))
        self._keep_cookies(result)
        return result

    def get(self, path: str, **kwargs: Any) -> Result:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> Result:
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> Result:
        return self.request("PUT", path, **kwargs)

    def patch(self, path: str, **kwargs: Any) -> Result:
        return self.request("PATCH", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> Result:
        return self.request("DELETE", path, **kwargs)

    def websocket(self, path: str) -> "WebSocketSession":
        return WebSocketSession(self, path)

    async def _call(self, scope: dict, body: bytes) -> Result:
        sent: list[dict] = []
        requested = False

        async def receive() -> dict:
            nonlocal requested
            if requested:
                await asyncio.Event().wait()  # the client never disconnects
            requested = True
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message: dict) -> None:
            sent.append(message)

        await self.app(scope, receive, send)
        start = next(m for m in sent if m["type"] == "http.response.start")
        headers = [(name.decode("latin-1"), value.decode("latin-1")) for name, value in start["headers"]]
        return Result(start["status"], headers, b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body"))

    def _headers(self, headers: dict[str, str]) -> list[tuple[bytes, bytes]]:
        if self.cookies:
            headers.setdefault("cookie", "; ".join(f"{name}={value}" for name, value in self.cookies.items()))
        return [(name.lower().encode("latin-1"), value.encode("latin-1")) for name, value in headers.items()]

    def _keep_cookies(self, result: Result) -> None:
        for name, value in result.all_headers:
            if name.lower() != "set-cookie":
                continue
            for morsel in SimpleCookie(value).values():
                if morsel["max-age"] == "0":
                    self.cookies.pop(morsel.key, None)
                else:
                    self.cookies[morsel.key] = morsel.value


class WebSocketSession:
    def __init__(self, client: Client, path: str) -> None:
        self.client = client
        self.path = path

    def __enter__(self) -> "WebSocketSession":
        self.client.loop.run_until_complete(self._connect())
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.client.loop.run_until_complete(self._disconnect())

    def receive_json(self, timeout: float = 1.0) -> Any:
        message = self.client.loop.run_until_complete(asyncio.wait_for(self._from_app.get(), timeout))
        assert message["type"] == "websocket.send", message
        return jsonlib.loads(message["text"])

    async def _connect(self) -> None:
        self._to_app: asyncio.Queue = asyncio.Queue()
        self._from_app: asyncio.Queue = asyncio.Queue()
        scope = {
            "type": "websocket",
            "path": self.path,
            "query_string": b"",
            "headers": self.client._headers({}),
            "client": ("127.0.0.1", 50001),
            "scheme": "ws",
            "subprotocols": [],
        }
        self._task = asyncio.ensure_future(self.client.app(scope, self._to_app.get, self._from_app.put))
        await self._to_app.put({"type": "websocket.connect"})
        accepted = await self._from_app.get()
        assert accepted["type"] == "websocket.accept", accepted

    async def _disconnect(self) -> None:
        await self._to_app.put({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(self._task, 1)


def _multipart(form: dict[str, str], files: dict[str, tuple[str, bytes, str]]) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode() for name, value in form.items()]
    for name, (filename, data, content_type) in files.items():
        head = f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\nContent-Type: {content_type}\r\n\r\n'
        parts.append(head.encode() + data + b"\r\n")
    return b"".join(parts) + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"
