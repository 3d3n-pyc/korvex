"""`App`: an ASGI application built on `Router`.

Run it with any ASGI server (uvicorn, hypercorn, granian...). For each
request, `App` matches the path, runs the route's middleware, calls the
handler and sends its `Response`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping, Sequence
from email.message import Message
from inspect import isawaitable
from pathlib import Path
from typing import Any, Optional, Union, overload
from urllib.parse import parse_qs, unquote

from korvex._core import MethodNotAllowed
from korvex.errors import HTTPException, ValidationError
from korvex.response import Headers, JSONValue, Response, StreamingResponse
from korvex.schema import Plan, Run, needs_plan, plan_for
from korvex.router import RouteGroup, Router

# `Union`/`Optional` rather than `|`: these lines run at import time, and
# `|` between types needs Python 3.10+.
Handler = Callable[..., Any]
"""`(request, **path_params) -> Response`, sync or async. Path params come
converted: `int` for `{id:int}`, `uuid.UUID` for `{id:uuid}`, else `str`.
With a model return annotation, it can return the model instead."""

Middleware = Callable[["Request"], Union[Optional[Response], Awaitable[Optional[Response]]]]
"""`(request) -> Response | None`, sync or async. Returning a `Response`
sends it right away; returning `None` moves on to the next step."""

Hook = Callable[[], Union[None, Awaitable[None]]]
"""`() -> None`, sync or async, run when the server starts or stops."""

AfterHook = Callable[["Request", Response], Union[Optional[Response], Awaitable[Optional[Response]]]]
"""`(request, response) -> Response | None`, sync or async. Returns the
response to send, or `None` to keep it as it is."""

ExceptionHandler = Callable[["Request", Any], Union[Response, Awaitable[Response]]]
"""`(request, exception) -> Response`, sync or async. The exception can be
annotated with the class the handler is registered for."""

logger = logging.getLogger("korvex")




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

    __slots__ = ("method", "path", "headers", "query_params", "path_params", "client", "scheme", "_receive", "_cookies", "_body", "_form")

    def __init__(self, scope: dict, receive: Callable[[], Awaitable[dict]], path_params: Mapping[str, Any]) -> None:
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

    def __init__(self, scope: dict, receive: Callable, send: Callable, path_params: Mapping[str, Any]) -> None:
        super().__init__(scope, receive, path_params)
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


WebSocketHandler = Callable[..., Awaitable[None]]
"""`async (websocket, **path_params) -> None`."""


class _RouteDecorators:
    """`@get(path)` and friends, for any class with a `route()` decorator.
    `dependencies` (`[Depends(func), ...]`) run before the handler, for
    what they check rather than what they return. `max_body` (bytes)
    raises or lowers the built-in server's body size limit for that route;
    ASGI servers ignore it."""

    __slots__ = ()

    def route(
        self,
        path: str,
        *,
        method: str = "GET",
        middleware: Sequence[str] | None = None,
        dependencies: Sequence[Any] = (),
        max_body: int | None = None,
    ) -> Callable[[Handler], Handler]:
        raise NotImplementedError

    def get(
        self,
        path: str,
        *,
        middleware: Sequence[str] | None = None,
        dependencies: Sequence[Any] = (),
        max_body: int | None = None,
    ) -> Callable[[Handler], Handler]:
        return self.route(path, method="GET", middleware=middleware, dependencies=dependencies, max_body=max_body)

    def post(
        self,
        path: str,
        *,
        middleware: Sequence[str] | None = None,
        dependencies: Sequence[Any] = (),
        max_body: int | None = None,
    ) -> Callable[[Handler], Handler]:
        return self.route(path, method="POST", middleware=middleware, dependencies=dependencies, max_body=max_body)

    def put(
        self,
        path: str,
        *,
        middleware: Sequence[str] | None = None,
        dependencies: Sequence[Any] = (),
        max_body: int | None = None,
    ) -> Callable[[Handler], Handler]:
        return self.route(path, method="PUT", middleware=middleware, dependencies=dependencies, max_body=max_body)

    def patch(
        self,
        path: str,
        *,
        middleware: Sequence[str] | None = None,
        dependencies: Sequence[Any] = (),
        max_body: int | None = None,
    ) -> Callable[[Handler], Handler]:
        return self.route(path, method="PATCH", middleware=middleware, dependencies=dependencies, max_body=max_body)

    def delete(
        self,
        path: str,
        *,
        middleware: Sequence[str] | None = None,
        dependencies: Sequence[Any] = (),
        max_body: int | None = None,
    ) -> Callable[[Handler], Handler]:
        return self.route(path, method="DELETE", middleware=middleware, dependencies=dependencies, max_body=max_body)

    def head(
        self,
        path: str,
        *,
        middleware: Sequence[str] | None = None,
        dependencies: Sequence[Any] = (),
        max_body: int | None = None,
    ) -> Callable[[Handler], Handler]:
        return self.route(path, method="HEAD", middleware=middleware, dependencies=dependencies, max_body=max_body)

    def options(
        self,
        path: str,
        *,
        middleware: Sequence[str] | None = None,
        dependencies: Sequence[Any] = (),
        max_body: int | None = None,
    ) -> Callable[[Handler], Handler]:
        return self.route(path, method="OPTIONS", middleware=middleware, dependencies=dependencies, max_body=max_body)


class AppGroup(_RouteDecorators):
    """Registers handlers under a shared prefix, middleware and dependencies.

        api = app.group("/api", middleware=["timing"], dependencies=[Depends(signed_in)])

        @api.get("/users")           # GET /api/users, after "timing" and signed_in
        def list_users(request): ...
    """

    __slots__ = ("_app", "_routes", "_dependencies")

    def __init__(self, app: App, routes: RouteGroup, dependencies: Sequence[Any] = ()) -> None:
        self._app = app
        self._routes = routes
        self._dependencies = tuple(dependencies)

    def route(
        self,
        path: str,
        *,
        method: str = "GET",
        middleware: Sequence[str] | None = None,
        dependencies: Sequence[Any] = (),
        max_body: int | None = None,
    ) -> Callable[[Handler], Handler]:
        """Decorator: registers the function as the handler for `path`, under
        this group's prefix. Its own `middleware` and `dependencies` run
        after the group's."""

        def register(func: Handler) -> Handler:
            name = _route_name(func, self._app._handlers)
            full_path = self._routes.full_path(path)
            # Bad annotations fail here, at import.
            plan = self._app._plan(func, full_path, (*self._dependencies, *dependencies))
            self._routes.route(path, name, method=method, middleware=middleware)
            self._app._handlers[name] = func
            self._app._plans[name] = plan
            if max_body is not None:
                self._app._body_limits.append((method, full_path, max_body))
            return func

        return register

    def websocket(self, path: str, *, dependencies: Sequence[Any] = ()) -> Callable[[WebSocketHandler], WebSocketHandler]:
        """Decorator: registers the function as the WebSocket handler for
        `path`, under this group's prefix. Its parameters and dependencies
        work as for HTTP handlers (no body), the group's dependencies run
        too, and one raising `HTTPException` refuses the connection (403).
        Group middleware and app hooks don't run for WebSockets."""

        def register(func: WebSocketHandler) -> WebSocketHandler:
            name = _route_name(func, self._app._ws_handlers)
            full_path = self._routes.full_path(path)
            plan = self._app._plan(func, full_path, (*self._dependencies, *dependencies), websocket=True)
            self._app._ws_router.route(full_path, name)
            self._app._ws_handlers[name] = func
            self._app._ws_plans[name] = plan
            return func

        return register

    def group(
        self, prefix: str = "", *, middleware: Sequence[str] | None = None, dependencies: Sequence[Any] = ()
    ) -> AppGroup:
        """Returns a nested group. Its `middleware` and `dependencies` run
        after this group's."""
        return AppGroup(self._app, self._routes.group(prefix, middleware=middleware), (*self._dependencies, *dependencies))

    def __repr__(self) -> str:
        return f"AppGroup(prefix={self._routes.prefix!r}, middleware={self._routes.middleware!r})"


class App(_RouteDecorators):
    """An ASGI application.

        app = App()

        @app.get("/users/{id:int}")
        def get_user(request, id):
            return Response.json({"id": id})

        # uvicorn mymodule:app
    """

    def __init__(self, router: Router | None = None) -> None:
        self.router = router if router is not None else Router()
        self._ws_router = Router()  # WebSocket routes: a path can have both
        self._ws_handlers: dict[str, WebSocketHandler] = {}
        self._plans: dict[str, Plan] = {}  # what each handler's annotations ask for
        self._ws_plans: dict[str, Plan] = {}
        self._dependency_plans: dict[tuple, Plan] = {}  # (function, pattern, websocket) -> its plan
        self.dependency_overrides: dict[Callable[..., Any], Callable[..., Any]] = {}
        """`{dependency: replacement}`: the replacement is called instead,
        wherever the dependency is used. For tests:
        `app.dependency_overrides[current_user] = lambda: test_user`."""
        self._root = AppGroup(self, self.router.group())
        self._handlers: dict[str, Handler] = {}
        self._middleware: dict[str, Middleware] = {}
        self._on_startup: list[Hook] = []
        self._on_shutdown: list[Hook] = []
        self._exception_handlers: dict[int | type[Exception], ExceptionHandler] = {}
        self._body_limits: list[tuple[str, str, int]] = []  # (method, path, bytes), for the built-in server
        self._before_request: list[Middleware] = []
        self._after_request: list[AfterHook] = []

    def route(
        self,
        path: str,
        *,
        method: str = "GET",
        middleware: Sequence[str] | None = None,
        dependencies: Sequence[Any] = (),
        max_body: int | None = None,
    ) -> Callable[[Handler], Handler]:
        """Decorator: registers the function as the handler for `path`."""
        return self._root.route(path, method=method, middleware=middleware, dependencies=dependencies, max_body=max_body)

    def group(
        self, prefix: str = "", *, middleware: Sequence[str] | None = None, dependencies: Sequence[Any] = ()
    ) -> AppGroup:
        """Returns an `AppGroup` that registers handlers under `prefix`, with
        `middleware` and `dependencies` for all of them."""
        return self._root.group(prefix, middleware=middleware, dependencies=dependencies)

    def websocket(self, path: str, *, dependencies: Sequence[Any] = ()) -> Callable[[WebSocketHandler], WebSocketHandler]:
        """Decorator: registers the function as the WebSocket handler for
        `path`; it gets a `WebSocket`, the path params, and parameters and
        dependencies as HTTP handlers do (no body). A dependency raising
        `HTTPException` refuses the connection (403). App hooks don't run
        for WebSockets."""
        return self._root.websocket(path, dependencies=dependencies)

    def _plan(self, func: Callable, pattern: str, dependencies: Sequence[Any] = (), *, websocket: bool = False) -> Plan:
        return plan_for(func, pattern, dependencies=dependencies, websocket=websocket, cache=self._dependency_plans)

    def _dependency_plan(self, func: Callable, pattern: str, websocket: bool) -> Plan:
        """The plan of a dependency override, worked out on first use."""
        key = (func, pattern, websocket)
        plan = self._dependency_plans.get(key)
        if plan is None:
            plan = plan_for(func, pattern, websocket=websocket, handler=False, cache=self._dependency_plans)
            self._dependency_plans[key] = plan
        return plan

    def openapi(self, *, title: str = "API", version: str = "0.1.0", description: str | None = None) -> dict:
        """An OpenAPI 3.1 description of the app's routes: paths, typed path
        and query parameters, request and response models, and each
        handler's docstring (first line as summary). Static files and
        WebSockets aren't included."""
        from korvex import openapi

        return openapi.build(self, title=title, version=version, description=description)

    def docs(
        self,
        *,
        title: str = "API",
        version: str = "0.1.0",
        description: str | None = None,
        openapi_path: str = "/openapi.json",
        docs_path: str | None = "/docs",
    ) -> None:
        """Serves the OpenAPI document at `openapi_path` (recomputed on each
        call, so it follows route changes) and Swagger UI at `docs_path`
        (loaded from a CDN; `None` to leave it out)."""
        from korvex import openapi

        def serve_openapi(request: Request) -> Response:
            return Response.json(self.openapi(title=title, version=version, description=description))

        self.router.route(openapi_path, "korvex.openapi")
        self._handlers["korvex.openapi"] = serve_openapi
        if docs_path is not None:
            page = openapi.swagger_ui(title, openapi_path)

            def serve_docs(request: Request) -> Response:
                return Response(page, headers=[("Content-Type", "text/html; charset=utf-8")])

            self.router.route(docs_path, "korvex.docs")
            self._handlers["korvex.docs"] = serve_docs

    def handler(self, name: str) -> Callable[[Handler], Handler]:
        """Decorator: attaches a function to a handler name already
        registered on `app.router` (e.g. through a route group)."""

        def register(func: Handler) -> Handler:
            self._handlers[name] = func
            return func

        return register

    def middleware(self, name: str) -> Callable[[Middleware], Middleware]:
        """Decorator: attaches a function to a middleware name used in routes."""

        def register(func: Middleware) -> Middleware:
            self._middleware[name] = func
            return func

        return register

    def static(self, prefix: str, directory: str | os.PathLike[str]) -> None:
        """Serves the files in `directory` under `prefix`:
        `app.static("/static", "public")` answers `/static/css/app.css` with
        `public/css/app.css`. Paths that leave the directory get a 404."""
        root = Path(directory).resolve()
        if not root.is_dir():
            raise NotADirectoryError(f"not a directory: {directory}")

        def serve(request: Request, path: str) -> Response:
            try:
                target = (root / path).resolve()
            except (OSError, ValueError):  # e.g. a null byte in the path
                raise HTTPException(404) from None
            if not target.is_relative_to(root) or not target.is_file():
                raise HTTPException(404)
            return Response.file(target)

        name = f"korvex.static:{prefix}"  # unique per prefix: all serve() share a qualname
        self.router.route(f"{prefix.rstrip('/')}/*path", name)
        self._handlers[name] = serve

    # Both `@app.on_startup` and `@app.on_startup()` work: the overloads
    # tell type checkers so, and _add_hook handles the two calls.
    @overload
    def on_startup(self, func: Hook) -> Hook: ...
    @overload
    def on_startup(self, func: None = None) -> Callable[[Hook], Hook]: ...
    def on_startup(self, func: Hook | None = None) -> Hook | Callable[[Hook], Hook]:
        """Decorator: runs the function when the server starts, before the
        first request (to open a database pool, for example). If it raises,
        the server doesn't start. Works with or without parentheses."""
        return _add_hook(self._on_startup, func)

    @overload
    def on_shutdown(self, func: Hook) -> Hook: ...
    @overload
    def on_shutdown(self, func: None = None) -> Callable[[Hook], Hook]: ...
    def on_shutdown(self, func: Hook | None = None) -> Hook | Callable[[Hook], Hook]:
        """Decorator: runs the function when the server stops. Shutdown
        hooks run in reverse order, so what was opened first closes last.
        Works with or without parentheses."""
        return _add_hook(self._on_shutdown, func)

    @overload
    def before_request(self, func: Middleware) -> Middleware: ...
    @overload
    def before_request(self, func: None = None) -> Callable[[Middleware], Middleware]: ...
    def before_request(self, func: Middleware | None = None) -> Middleware | Callable[[Middleware], Middleware]:
        """Decorator: runs the function before every request, ahead of
        routing, so it sees unknown paths too. It gets the request
        (`path_params` not filled yet) and returns a `Response` to answer
        right away, or `None` to carry on. Hooks run in the order declared."""
        return _add_hook(self._before_request, func)  # type: ignore[arg-type]

    @overload
    def after_request(self, func: AfterHook) -> AfterHook: ...
    @overload
    def after_request(self, func: None = None) -> Callable[[AfterHook], AfterHook]: ...
    def after_request(self, func: AfterHook | None = None) -> AfterHook | Callable[[AfterHook], AfterHook]:
        """Decorator: runs the function on every response, errors included.
        It gets the request and the response, and returns the response to
        send, or `None` to keep it. Hooks run in reverse order, like layers:
        the first declared sees the response last."""
        return _add_hook(self._after_request, func)  # type: ignore[arg-type]

    def exception_handler(self, key: int | type[Exception]) -> Callable[[ExceptionHandler], ExceptionHandler]:
        """Decorator: answers errors with the function's `Response`. `key` is
        an HTTP status (`404`, also used for unknown paths) or an exception
        class (`ValueError`, which also catches its subclasses)."""

        def register(func: ExceptionHandler) -> ExceptionHandler:
            self._exception_handlers[key] = func
            return func

        return register

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] == "lifespan":
            await self._lifespan(receive, send)
            return
        if scope["type"] == "websocket":
            await self._websocket(scope, receive, send)
            return
        if scope["type"] != "http":
            raise NotImplementedError(f"unsupported ASGI scope type: {scope['type']!r}")

        response = await self._respond(scope, receive)
        head = scope["method"] == "HEAD"
        if isinstance(response, StreamingResponse):
            await self._send_stream(response, send, head, scope["path"])
            return
        await send({"type": "http.response.start", "status": response.status, "headers": _raw_headers(response, head)})
        # HEAD: same headers as GET, no body.
        await send({"type": "http.response.body", "body": b"" if head else response.body})

    @staticmethod
    async def _send_stream(response: StreamingResponse, send: Callable, head: bool, path: str) -> None:
        try:
            await send({"type": "http.response.start", "status": response.status, "headers": _raw_headers(response, head)})
            if not head:
                async for chunk in response:
                    await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b""})
        finally:
            await _finish(response, path)  # sent or not, what waited for it runs

    async def _respond(self, scope: dict, receive: Callable, error: int = 0) -> Response:
        """The response to one request: before hooks, then the route (its
        middleware and handler), then after hooks, with errors turned into
        responses at each step. With `error`, the route is replaced by that
        HTTP error."""
        request = Request(scope, receive, {})
        try:
            response = None
            for hook in self._before_request:
                response = await _call(hook, request)
                if response is not None:
                    break
            if response is None:
                if error:
                    raise HTTPException(error)
                response = await self._dispatch(request)
        except Exception as exc:
            response = await self._handle_error(request, exc)
        for hook in reversed(self._after_request):
            try:
                changed = await _call(hook, request, response)
            except Exception:
                logger.exception("after_request hook failed for %s %s", request.method, request.path)
                return Response.text("Internal Server Error", status=500)
            if changed is not None:
                if changed is not response and isinstance(response, StreamingResponse):
                    await _finish(response, request.path)  # replaced: it won't be sent
                response = changed
        return response

    async def _dispatch(self, request: Request) -> Response:
        """Routes the request and runs its middleware and handler."""
        try:
            found = self.router.match(request.path, method=request.method)
        except MethodNotAllowed as exc:
            if request.method == "HEAD" and "GET" in exc.allowed:
                found = self.router.match(request.path, method="GET")  # HEAD is GET without the body
            else:
                raise HTTPException(405, headers=[("Allow", ", ".join(exc.allowed))]) from None
        if found is None:
            raise HTTPException(404)

        handler_name, path_params, middleware_names = found
        request.path_params = path_params
        for name in middleware_names:
            if name not in self._middleware:
                raise LookupError(f"no middleware named {name!r}; register one with @app.middleware({name!r})")
            early_response = await _call(self._middleware[name], request)
            if early_response is not None:
                return early_response

        if handler_name not in self._handlers:
            raise LookupError(f"no handler named {handler_name!r}; register one with @app.handler({handler_name!r})")
        handler = self._handlers[handler_name]
        plan = self._plan_of(handler_name)
        if not needs_plan(plan):  # no annotations to act on: called as before, at no cost
            return await _call(handler, request, **path_params)
        if not plan.dependencies:
            arguments = {**path_params, **await plan.bind(request, path_params, None)}
            return plan.respond(await _call(handler, request, **arguments))
        run = Run(self.dependency_overrides, self._dependency_plan)
        try:
            arguments = {**path_params, **await plan.bind(request, path_params, run)}
            response = plan.respond(await _call(handler, request, **arguments))
        except BaseException as exc:
            await run.close(exc)  # raised at the yields; still answered as usual
            raise
        if run.open:
            if isinstance(response, StreamingResponse):
                response._on_close.append(run.close)  # once the stream is sent
            else:
                await run.close()
        return response

    def _plan_of(self, handler_name: str) -> Plan:
        """The handler's plan. Handlers attached with @app.handler get theirs
        on first use, once their route's pattern is known."""
        plan = self._plans.get(handler_name)
        if plan is None:
            pattern = next((path for path, _, name, _ in self.router.routes() if name == handler_name), "")
            plan = self._plans[handler_name] = self._plan(self._handlers[handler_name], pattern)
        return plan

    async def _serve_websocket(
        self,
        path: str,
        query_string: bytes,
        headers: list,
        client: tuple[str, int] | None,
        scheme: str,
        connection: Any,
        incoming: asyncio.Queue,
    ) -> None:
        """Runs a WebSocket from the built-in server through the same code
        as under ASGI: it speaks ASGI messages over the Rust connection."""
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
                    connection.send_text(message["text"])
                else:
                    connection.send_bytes(message.get("bytes") or b"")
            elif kind == "websocket.close":
                connection.close(message.get("code", 1000), message.get("reason") or "")

        try:
            await self._websocket(scope, receive, send)
        finally:
            connection.finish()

    async def _websocket(self, scope: dict, receive: Callable, send: Callable) -> None:
        """Runs a WebSocket connection: its handler, or a refusal (403) for
        an unknown path or a dependency raising HTTPException. Whatever the
        handler leaves open gets closed."""
        found = self._ws_router.match(scope["path"])
        websocket = WebSocket(scope, receive, send, found[1] if found else {})
        if found is None or found[0] not in self._ws_handlers:
            await websocket.close()  # before accept: the client gets a 403
            return
        name, path_params = found[0], found[1]
        handler = self._ws_handlers[name]
        plan = self._ws_plans.get(name)
        run = Run(self.dependency_overrides, self._dependency_plan) if plan is not None and plan.dependencies else None
        try:
            arguments: dict[str, Any] = dict(path_params)
            if plan is not None and needs_plan(plan):
                try:
                    arguments.update(await plan.bind(websocket, path_params, run))
                except HTTPException as exc:  # a 422 too
                    logger.info("WebSocket %s refused: %s %s", websocket.path, exc.status, exc.detail)
                    if run is not None:
                        await run.close(exc)
                    await websocket.close()  # before accept: the client gets a 403
                    return
            await handler(websocket, **arguments)
        except WebSocketDisconnect as exc:
            if run is not None:
                await self._close_quietly(run, exc, websocket.path)
        except Exception as exc:
            logger.exception("unhandled error in WebSocket %s", websocket.path)
            if run is not None:
                await self._close_quietly(run, exc, websocket.path)
            await websocket.close(1011 if websocket._accepted else 1000)  # 1011: server error
            return
        else:
            if run is not None:
                await self._close_quietly(run, None, websocket.path)
        await websocket.close()

    @staticmethod
    async def _close_quietly(run: Run, error: BaseException | None, where: str) -> None:
        """Tears dependencies down when there's no response left to change:
        a teardown error is logged."""
        try:
            await run.close(error)
        except Exception as raised:
            if raised is not error:
                logger.exception("dependency teardown failed for %s", where)

    async def _handle_error(self, request: Request, exc: Exception) -> Response:
        """Turns an exception into a response: the matching exception
        handler's, the HTTPException's own, or a logged 500."""
        handler = self._find_exception_handler(exc)
        if handler is not None:
            try:
                return await _call(handler, request, exc)
            except Exception:
                logger.exception("exception handler failed for %s %s", request.method, request.path)
                return Response.text("Internal Server Error", status=500)
        if isinstance(exc, ValidationError):
            return Response.json({"detail": exc.errors}, status=422, headers=exc.headers)
        if isinstance(exc, HTTPException):
            return Response.text(exc.detail, status=exc.status, headers=exc.headers)
        logger.exception("unhandled error in %s %s", request.method, request.path, exc_info=exc)
        return Response.text("Internal Server Error", status=500)

    def _find_exception_handler(self, exc: Exception) -> ExceptionHandler | None:
        """By status for an HTTPException, then by class, closest first."""
        if isinstance(exc, HTTPException) and exc.status in self._exception_handlers:
            return self._exception_handlers[exc.status]
        for cls in type(exc).__mro__:
            if cls in self._exception_handlers:
                return self._exception_handlers[cls]
        return None

    async def _lifespan(self, receive: Callable, send: Callable) -> None:
        """Runs the hooks when the server sends its startup and shutdown events."""
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                if not await _run_hooks(self._on_startup, "startup", send):
                    return
            elif message["type"] == "lifespan.shutdown":
                await _run_hooks(reversed(self._on_shutdown), "shutdown", send)
                return

    def run(
        self,
        host: str = "127.0.0.1",
        port: int = 8000,
        *,
        workers: int = 1,
        graceful_timeout: float = 30.0,
        max_body: int = 1024 * 1024,
        timeout: float = 30.0,
        uds: str | None = None,
        forwarded_allow_ips: Sequence[str] = ("127.0.0.1", "::1"),
        access_log: bool = False,
    ) -> None:
        """Serves the app with Korvex's built-in server until Ctrl+C or
        SIGTERM. Linux and macOS only; on Windows, use an ASGI server.

        - `workers` processes share the port, each running the startup and
          shutdown hooks. About 3 per 4 CPU cores works best.
        - On stop, requests in progress get up to `graceful_timeout` seconds.
        - Request bodies over `max_body` bytes get a 413 (a route's own
          `max_body` overrides it); clients get `timeout` seconds to send
          the headers, then the body (408 after that).
        - `uds` listens on a Unix socket at that path instead of host:port,
          for a reverse proxy on the same machine.
        - `X-Forwarded-For` and `X-Forwarded-Proto` set `request.client` and
          `request.scheme` when they come from `forwarded_allow_ips` (`"*"`
          for any) or a Unix socket.
        - `access_log` logs each request to the `korvex.access` logger.
        """
        from korvex import serve

        serve.run(
            self,
            host,
            port,
            workers=workers,
            graceful_timeout=graceful_timeout,
            config=serve.Config(max_body, timeout, list(self._body_limits), list(forwarded_allow_ips), uds, access_log),
        )

    async def _serve_one(
        self,
        method: str,
        path: str,
        query_string: bytes,
        headers: list,
        body: bytes,
        client: tuple[str, int] | None,
        scheme: str,
        responder: Any,
        error: int = 0,
    ) -> int:
        """Runs one request taken from the built-in server, through the same
        dispatch as under ASGI, hands the response back to Rust, and returns
        its status. `error` is a status the server already decided on (413,
        408, 400): the request goes through the before hooks, then gets that
        error's response, as if the route had raised `HTTPException`."""
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

        response = await self._respond(scope, receive, error)
        head = method == "HEAD"
        raw_headers = _raw_headers(response, head)
        if head:
            responder.respond(response.status, raw_headers, b"")
        elif isinstance(response, StreamingResponse):
            responder.start_stream(response.status, raw_headers)
            try:
                async for chunk in response:
                    if not responder.send_chunk(chunk):
                        break  # the client is gone
            finally:
                responder.end_stream()
                await _finish(response, path)
        else:
            responder.respond(response.status, raw_headers, response.body)
        if head and isinstance(response, StreamingResponse):
            await _finish(response, path)
        return response.status

    def __repr__(self) -> str:
        return f"App(router={self.router!r})"


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


def _raw_headers(response: Response, head: bool) -> list[tuple[bytes, bytes]]:
    """The response headers as bytes. A HEAD response keeps the Content-Length
    its body would have had, since the body itself isn't sent."""
    if head and not isinstance(response, StreamingResponse) and "Content-Length" not in response.headers:
        response.headers.set("Content-Length", str(len(response.body)))
    return [(name.encode("latin-1"), value.encode("latin-1")) for name, value in response.headers]


# Python 3.12+: a request handler that doesn't wait completes right away,
# without going through the loop's queue. Older versions schedule it.
_eager_task_factory = getattr(asyncio, "eager_task_factory", None)


def _start_task(loop: asyncio.AbstractEventLoop, coroutine: Any) -> asyncio.Task:
    if _eager_task_factory is not None:
        return _eager_task_factory(loop, coroutine)
    return loop.create_task(coroutine)


def _route_name(func: Callable[..., Any], taken: Mapping[str, Any]) -> str:
    """The handler name a route is registered under: the function's own,
    `module.qualname`, with `#2`, `#3`... once that's taken. Each route gets
    its own name, so its own plan (dependencies included) and its own
    function, even when one function serves two routes (stacked decorators)
    or a factory registers functions of the same name in several groups."""
    base = f"{func.__module__}.{func.__qualname__}"
    name, n = base, 2
    while name in taken:
        name, n = f"{base}#{n}", n + 1
    return name


async def _finish(response: StreamingResponse, path: str) -> None:
    """Runs what waits for a streamed response to be sent (the teardown of
    its handler's dependencies). The response is gone: errors are logged."""
    callbacks, response._on_close = response._on_close, []
    for callback in callbacks:
        try:
            await callback()
        except Exception:
            logger.exception("dependency teardown failed for %s", path)


async def _call(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Calls a sync or async function and returns its result."""
    result = func(*args, **kwargs)
    return await result if isawaitable(result) else result


def _add_hook(hooks: list, func: Callable | None) -> Any:
    """Registers `func` (`@app.on_startup`), or returns a decorator that
    will (`@app.on_startup()`)."""
    if func is None:
        return lambda f: _add_hook(hooks, f)  # type: ignore[return-value]
    hooks.append(func)
    return func


async def _run_hooks(hooks: Iterable[Hook], event: str, send: Callable) -> bool:
    """Runs `hooks` in order and tells the server whether `event` (startup
    or shutdown) succeeded. Stops at the first hook that raises."""
    try:
        for hook in hooks:
            await _call(hook)
    except Exception as exc:
        await send({"type": f"lifespan.{event}.failed", "message": f"{type(exc).__name__}: {exc}"})
        return False
    await send({"type": f"lifespan.{event}.complete"})
    return True
