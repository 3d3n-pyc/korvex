"""`App`: an ASGI application built on `Router`.

Run it with any ASGI server (uvicorn, hypercorn, granian...), or its own
(`App.run()`). For each request, `App` matches the path, resolves the
route's dependencies, calls the handler and sends its `Response`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import weakref
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from inspect import isawaitable
from pathlib import Path
from typing import Any, Optional, Union, overload

from korvex._core import MethodNotAllowed
from korvex._errors import Abort, HTTPException, ValidationError
from korvex._requests import Connection, Request, WebSocket, WebSocketDisconnect
from korvex._response import Response, StreamingResponse
from korvex._routing import (
    Group,
    Handler,
    WebSocketHandler,
    _check_name_type,
    _Docs,
    _Host,
    _host_labels,
    _Route,
    _RouteDecorators,
)
from korvex._schema import Plan, Run, needs_plan, plan_for
from korvex.router import Router

BeforeHook = Callable[["Connection"], Union[Optional[Response], Awaitable[Optional[Response]]]]
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


class LoopLocal:
    """Attributes kept per event loop: `app.local`. What a startup hook
    opens for its loop (a connection pool, a client) goes there, and the
    handlers and dependencies running on that loop find it:

        @app.on_startup
        async def connect():
            app.local.pool = await asyncpg.create_pool(DATABASE_URL)

        @app.on_shutdown
        async def disconnect():
            await app.local.pool.close()

        async def db():                        # a dependency
            async with app.local.pool.acquire() as connection:
                yield connection

    With `threads=`, each loop runs the hooks, and a module-level global
    would end up holding one loop's pool for all of them: asyncio objects
    only work on the loop that made them. With one loop, it's simply an
    attribute. Use it from a running loop only."""

    __slots__ = ("_by_loop", "_lock")
    _by_loop: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, Any]]
    _lock: threading.Lock

    def __init__(self) -> None:
        # Set past __setattr__, which stores attributes per loop.
        object.__setattr__(self, "_by_loop", weakref.WeakKeyDictionary())
        object.__setattr__(self, "_lock", threading.Lock())

    def _values(self) -> dict[str, Any]:
        loop = asyncio.get_running_loop()  # outside a loop: RuntimeError, as asyncio does
        with self._lock:
            return self._by_loop.setdefault(loop, {})

    def __getattr__(self, name: str) -> Any:
        try:
            return self._values()[name]
        except KeyError:
            raise AttributeError(f"app.local has no {name!r} on this event loop (set it in a startup hook)") from None

    def __setattr__(self, name: str, value: Any) -> None:
        self._values()[name] = value

    def __delattr__(self, name: str) -> None:
        try:
            del self._values()[name]
        except KeyError:
            raise AttributeError(name) from None


class App(_RouteDecorators):
    """An ASGI application.

    app = App()

    @app.get("/users/{id:int}")
    def get_user(request, id):
        return Response.json({"id": id})

    # uvicorn mymodule:app
    """

    def __init__(self) -> None:
        self._router = Router()  # HTTP routes
        self._ws_router = Router()  # WebSocket routes: a path can have both
        # Each route, under its handler name in the router: HTTP, WebSocket.
        self._routes: dict[str, _Route] = {}
        self._ws_routes: dict[str, _Route] = {}
        self._hosts: list[_Host] = []  # app.host() patterns, the ones without parameters first
        self.local = LoopLocal()
        """Attributes per event loop, for what startup hooks open: see `LoopLocal`."""
        self._dependency_plans: dict[tuple, Plan] = {}  # (function, pattern, websocket) -> its plan
        self.dependency_overrides: dict[Callable[..., Any], Callable[..., Any]] = {}
        """`{dependency: replacement}`: the replacement is called instead,
        wherever the dependency is used. For tests:
        `app.dependency_overrides[current_user] = lambda: test_user`."""
        self._root = Group(self, self._router.group())
        self._on_startup: list[Hook] = []
        self._on_shutdown: list[Hook] = []
        self._exception_handlers: dict[int | type[Exception], ExceptionHandler] = {}
        self._body_limits: list[tuple[str, str, int]] = []  # (method, path, bytes), for the built-in server
        self._before_request: list[BeforeHook] = []
        self._after_request: list[AfterHook] = []
        # Where each name given with name= points: (websocket, host, path).
        self._explicit_names: dict[str, tuple[bool, str | None, str]] = {}
        self._url_cache: tuple[tuple, Any] | None = None

    def route(
        self,
        path: str,
        *,
        method: str = "GET",
        name: str | None = None,
        status: int = 200,
        dependencies: Sequence[Any] = (),
        tags: Sequence[str] = (),
        deprecated: bool = False,
        responses: Mapping[int | str, Any] | None = None,
        include_in_schema: bool = True,
        max_body: int | None = None,
    ) -> Callable[[Handler], Handler]:
        """Decorator: registers the function as the handler for `path`
        (see `get()` for the options)."""
        return self._root.route(
            path,
            method=method,
            name=name,
            status=status,
            dependencies=dependencies,
            tags=tags,
            deprecated=deprecated,
            responses=responses,
            include_in_schema=include_in_schema,
            max_body=max_body,
        )

    def group(
        self,
        prefix: str = "",
        *,
        dependencies: Sequence[Any] = (),
        tags: Sequence[str] = (),
        deprecated: bool = False,
        responses: Mapping[int | str, Any] | None = None,
        include_in_schema: bool = True,
    ) -> Group:
        """Returns an `Group` that registers handlers under `prefix`, with
        `dependencies` for all of them, and the OpenAPI options (`tags`,
        `deprecated`, `responses`, `include_in_schema`) their own add to."""
        return self._root.group(
            prefix,
            dependencies=dependencies,
            tags=tags,
            deprecated=deprecated,
            responses=responses,
            include_in_schema=include_in_schema,
        )

    def host(
        self,
        pattern: str,
        *,
        dependencies: Sequence[Any] = (),
        tags: Sequence[str] = (),
        deprecated: bool = False,
        responses: Mapping[int | str, Any] | None = None,
        include_in_schema: bool = True,
    ) -> Group:
        """Returns a group whose routes only answer for requests to that
        host (its `Host` header, without the port):

            api = app.host("api.example.com")
            @api.get("/users")                      # https://api.example.com/users

            tenants = app.host("{tenant}.example.com")
            @tenants.get("/")                       # acme.example.com: tenant="acme"
            def home(request, tenant): ...

        A `{param}` takes one whole label of the host name, and reaches
        the dependencies and handlers that take it, like a path parameter
        (and `request.path_params`). The first pattern
        that fits owns the request (those without parameters are tried
        first, then in the order declared): its routes answer, and routes
        declared without a host when it doesn't have the path; another
        pattern's never do. Calling `host()` again with the same pattern
        adds to the same routes. In the built-in server, a route's
        `max_body` applies to its path on every host. The OpenAPI options
        (`tags`...) work as for `group()`."""
        normalized = pattern.lower().rstrip(".")
        found = next((h for h in self._hosts if h.pattern == normalized), None)
        if found is None:
            found = _Host(pattern, self._router.constraints())
            self._hosts.append(found)
            self._hosts.sort(key=lambda h: bool(h.params))  # stable: literal hosts first, in order
        return Group(
            self, found.router.group(), dependencies, found, _Docs(tags, deprecated, responses, include_in_schema)
        )

    def websocket(
        self, path: str, *, name: str | None = None, dependencies: Sequence[Any] = ()
    ) -> Callable[[WebSocketHandler], WebSocketHandler]:
        """Decorator: registers the function as the WebSocket handler for
        `path`; it gets a `WebSocket`, the path params, and parameters and
        dependencies as HTTP handlers do (no body). A dependency raising
        `HTTPException` refuses the connection (403). Before hooks run on
        the handshake; after hooks don't. `name` is what `url_for()` knows
        it by (the function's name by default)."""
        return self._root.websocket(path, name=name, dependencies=dependencies)

    def url_for(self, name: str, /, **params: Any) -> str:
        """The path of the route named `name`, with `params` for its path
        parameters:

            @app.get("/users/{id:int}")
            def get_user(request, id): ...

            app.url_for("get_user", id=42)           # "/users/42"
            app.url_for("static", path="css/a.css")  # "/static/css/a.css"

        A route's name is its `name=`, or else its function's name (an
        `app.static()` directory's is `"static"`). Two routes can share a
        function's name; `url_for()` then asks for a `name=` to tell them
        apart (LookupError). Two can share a `name=` only on the same path,
        for different methods.

        Values can be `str`, `int`, `float`, `bool`, `UUID`, `date` or
        `datetime`, and are percent-encoded. Each must fit its constraint
        (`{id:int}`, `{id:uuid}`, a custom one) and the handler's
        annotation, can't be empty, `.` or `..`, and only a `*wildcard`
        takes a `/`. The path must also reach that route, not another
        that answers it first (`/users/me` rather than `/users/{name}`).
        Otherwise, ValueError (TypeError for a value of another type).
        Every path parameter must be given, and nothing else: add a query
        string yourself (`urllib.parse.urlencode`).

        For a route of `app.host()`, this is the path: a link on that host.
        `request.url_for()` gives the full URL, host included."""
        from korvex import _urls as urls

        return urls.path_for(self, urls.lookup(self._url_index(), name), params)

    def _url_index(self) -> Any:
        """The names of the routes, worked out again once routes change."""
        from korvex import _urls as urls

        routers = [self._router, self._ws_router] + [r for h in self._hosts for r in (h.router, h.ws_router)]
        signature = (len(self._routes), len(self._ws_routes), *(getattr(r, "version", None) for r in routers))
        if self._url_cache is None or self._url_cache[0] != signature:
            self._url_cache = (signature, urls.build_index(self))
        return self._url_cache[1]

    def _check_name(self, name: str | None, websocket: bool, host: _Host | None, path: str) -> str | None:
        """A route's `name=`, checked before it's registered: one name, one
        path (methods aside)."""
        if name is None:
            return None
        _check_name_type(name)
        where = (websocket, host.pattern if host else None, path)
        taken = self._explicit_names.get(name)
        if taken is not None and taken != where:
            kind = "WebSocket " if taken[0] else ""
            raise ValueError(f"route name {name!r} is already taken by {kind}{taken[1] or ''}{taken[2]}")
        return name

    def _add_route(self, websocket: bool, key: str, route: _Route, host: _Host | None, path: str) -> None:
        """Keeps a route just registered in a router, under its handler name."""
        (self._ws_routes if websocket else self._routes)[key] = route
        if route.explicit and route.name is not None:
            self._explicit_names[route.name] = (websocket, host.pattern if host else None, path)

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

    def openapi(
        self, *, title: str = "API", version: str = "0.1.0", description: str | None = None, host: str | None = None
    ) -> dict:
        """An OpenAPI 3.1 description of the app's routes: paths, typed path
        and query parameters, request and response models, and each
        handler's docstring (first line as summary). Static files and
        WebSockets aren't included. With `host`, the routes that answer on
        that host: its own (`app.host()`), then those without a host;
        without, only those without a host."""
        from korvex import _openapi as openapi

        return openapi.build(self, title=title, version=version, description=description, host=host)

    def _host_for(self, labels: list[str]) -> tuple[_Host, dict[str, str]] | None:
        """The first host pattern the request's host fits, and its
        parameters. It owns the request: only its routes, then those
        without a host, can answer, never another host pattern's, so a
        literal `api.example.com` isn't served `{tenant}.example.com`'s
        routes as tenant "api"."""
        for host in self._hosts:
            params = host.match(labels)
            if params is not None:
                return host, params
        return None

    def _routes_for(self, host: str | None) -> list[tuple[str, str, str, tuple[str, ...]]]:
        """`(pattern, method, handler, middleware)` for every route that
        answers on `host`, in the order they'd be tried."""
        routes = []
        found = self._host_for(host.lower().rsplit(":", 1)[0].rstrip(".").split(".")) if host is not None else None
        if found is not None:
            routes += found[0].router.routes()
        seen = {(pattern, method) for pattern, method, _, _ in routes}
        return routes + [route for route in self._router.routes() if (route[0], route[1]) not in seen]

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
        from korvex import _openapi as openapi

        def serve_openapi(request: Request) -> Response:
            # Each host gets its own API: the routes that answer there.
            host = request.headers.get("host") if self._hosts else None
            return Response.json(self.openapi(title=title, version=version, description=description, host=host))

        self._router.route(openapi_path, "korvex.openapi")
        self._routes["korvex.openapi"] = _Route(serve_openapi)
        if docs_path is not None:
            page = openapi.swagger_ui(title, openapi_path)

            def serve_docs(request: Request) -> Response:
                return Response(page, headers=[("Content-Type", "text/html; charset=utf-8")])

            self._router.route(docs_path, "korvex.docs")
            self._routes["korvex.docs"] = _Route(serve_docs)

    def constraint(self, name: str, pattern: str) -> None:
        """Defines a route constraint, beyond `int`, `uuid` and `str`:

            app.constraint("slug", r"[a-z0-9]+(?:-[a-z0-9]+)*")

            @app.get("/posts/{post:slug}")       # /posts/hello-world, not /posts/Hello

        A value fits when the whole of it matches the regex (Rust's `regex`
        syntax: no look-around or backreferences, so matching takes linear
        time). One that doesn't gives a 404, or another route's match:
        constrained parameters are tried in the order they were registered,
        before plain ones. The value reaches the handler as a `str`; an
        annotation converts it further. Define a constraint before the
        routes that use it; it can't be redefined differently."""
        self._router.constraint(name, pattern)
        for host in self._hosts:
            host.router.constraint(name, pattern)
            host.ws_router.constraint(name, pattern)

    def static(self, prefix: str, directory: str | os.PathLike[str], *, name: str | None = None) -> None:
        """Serves the files in `directory` under `prefix`:
        `app.static("/static", "public")` answers `/static/css/app.css` with
        `public/css/app.css`. Paths that leave the directory get a 404.
        `url_for("static", path="css/app.css")` links to a file; `name`
        tells several directories apart (`"static"` by default)."""
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

        key = f"korvex.static:{prefix}"  # unique per prefix: all serve() share a qualname
        pattern = f"{prefix.rstrip('/')}/*path"
        self._check_name(name, False, None, pattern)
        self._router.route(pattern, key)
        self._add_route(False, key, _Route(serve, name=name or "static", explicit=name is not None), None, pattern)

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
    def before_request(self, func: BeforeHook) -> BeforeHook: ...
    @overload
    def before_request(self, func: None = None) -> Callable[[BeforeHook], BeforeHook]: ...
    def before_request(self, func: BeforeHook | None = None) -> BeforeHook | Callable[[BeforeHook], BeforeHook]:
        """Decorator: runs the function before every request, ahead of
        routing, so it sees unknown paths too. It gets the request
        (`path_params` not filled yet) and returns a `Response` to answer
        right away, or `None` to carry on. Hooks run in the order declared.

        They run for WebSocket handshakes too, with the `WebSocket` as the
        request (`isinstance(request, WebSocket)` tells them apart): a hook
        answering, or raising `HTTPException` or `Abort`, refuses the
        connection (403). That's the place to check a handshake's `Origin`,
        which browsers don't check for WebSockets."""
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
            await send(
                {"type": "http.response.start", "status": response.status, "headers": _raw_headers(response, head)}
            )
            if not head:
                async for chunk in response:
                    await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b""})
        finally:
            await _finish(response, path)  # sent or not, what waited for it runs

    async def _respond(self, scope: dict, receive: Callable, error: int = 0) -> Response:
        """The response to one request: before hooks, then the route (its
        dependencies and handler), then after hooks, with errors turned into
        responses at each step. With `error`, the route is replaced by that
        HTTP error."""
        request = Request(scope, receive, {}, self)
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
                if not isinstance(response, Response):
                    raise TypeError(
                        f"{request.method} {request.path}: the handler returned {type(response).__name__}, not a Response "
                        "(a model needs a return annotation; for an empty response, annotate it `-> None`)"
                    )
        except Exception as exc:
            response = await self._handle_error(request, exc)
        for hook in reversed(self._after_request):
            try:
                changed = await _call(hook, request, response)
            except Exception:
                logger.exception("after_request hook failed for %s %s", request.method, request.path)
                return _server_error()
            if changed is not None:
                if changed is not response and isinstance(response, StreamingResponse):
                    await _finish(response, request.path)  # replaced: it won't be sent
                response = changed
        return response

    async def _dispatch(self, request: Request) -> Response:
        """Routes the request and runs its dependencies and handler."""
        found, host_params = None, None
        host = self._host_for(_host_labels(request)) if self._hosts else None
        if host is not None:
            found = _match(host[0].router, request)
            host_params = host[1] if found is not None else None  # only for the host's own routes
        if found is None:
            found = _match(self._router, request)
        if found is None:
            raise HTTPException(404)

        handler_name, path_params, _ = found
        route = self._routes[handler_name]
        values = path_params  # what dependencies can read: the host's parameters too
        if host_params:
            values = {**host_params, **path_params}
            path_params = {**_host_params_for(route, host_params), **path_params}
        request.path_params = values
        handler = route.func
        plan = route.plan if route.plan is not None else self._plan_of(handler_name)
        if not needs_plan(plan):  # no annotations to act on: called as before, at no cost
            return await _call(handler, request, **path_params)
        if not plan.dependencies:
            arguments = {**path_params, **await plan.bind(request, values, None)}
            return plan.respond(await _call(handler, request, **arguments))
        run = Run(self.dependency_overrides, self._dependency_plan)
        try:
            arguments = {**path_params, **await plan.bind(request, values, run)}
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
        """The plan of an HTTP route's handler: the App's own routes (static
        files, the docs) get theirs on first use."""
        route = self._routes[handler_name]
        if route.plan is None:
            pattern = next((path for path, _, name, _ in self._router.routes() if name == handler_name), "")
            route.plan = self._plan(route.func, pattern)
        return route.plan

    async def _websocket(self, scope: dict, receive: Callable, send: Callable) -> None:
        """Runs a WebSocket connection: its handler, or a refusal (403) for
        an unknown path, a before hook answering, or a dependency raising
        HTTPException. Whatever the handler leaves open gets closed."""
        found, host_params = None, None
        host = self._host_for(_host_labels(Request(scope, receive, {}))) if self._hosts else None
        if host is not None:
            found = host[0].ws_router.match(scope["path"])
            host_params = host[1] if found is not None else None
        if found is None:
            found = self._ws_router.match(scope["path"])
        values = found[1] if found else {}
        if found is not None and host_params:
            values = {**host_params, **found[1]}
        websocket = WebSocket(scope, receive, send, values, self)
        if self._before_request and not await self._websocket_hooks_allow(websocket):
            await websocket.close()  # before accept: the client gets a 403
            return
        route = self._ws_routes.get(found[0]) if found is not None else None
        if found is None or route is None:
            await websocket.close()
            return
        path_params = found[1]
        if host_params:
            path_params = {**_host_params_for(route, host_params), **path_params}
        handler = route.func
        plan = route.plan
        run = Run(self.dependency_overrides, self._dependency_plan) if plan is not None and plan.dependencies else None
        try:
            arguments: dict[str, Any] = dict(path_params)
            if plan is not None and needs_plan(plan):
                try:
                    arguments.update(await plan.bind(websocket, values, run))
                except (HTTPException, Abort) as exc:  # a 422 too
                    status = exc.response.status if isinstance(exc, Abort) else exc.status
                    logger.info("WebSocket %s refused: %s", websocket.path, status)
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

    async def _websocket_hooks_allow(self, websocket: WebSocket) -> bool:
        """Runs the before hooks on a handshake: False if one refuses it
        (answers, or raises HTTPException or Abort)."""
        for hook in self._before_request:
            try:
                answer = await _call(hook, websocket)
            except (HTTPException, Abort) as exc:
                answer = (
                    exc.response if isinstance(exc, Abort) else Response.json({"detail": exc.detail}, status=exc.status)
                )
            except Exception:
                logger.exception("before_request hook failed for WebSocket %s", websocket.path)
                return False
            if answer is not None:
                logger.info("WebSocket %s refused by a before hook: %s", websocket.path, answer.status)
                return False
        return True

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
        handler's, the HTTPException's own, or a logged 500. An `Abort`
        carries its own response."""
        if isinstance(exc, Abort):
            return exc.response
        handler = self._find_exception_handler(exc)
        if handler is not None:
            try:
                return await _call(handler, request, exc)
            except Exception:
                logger.exception("exception handler failed for %s %s", request.method, request.path)
                return _server_error()
        if isinstance(exc, ValidationError):
            return Response.json({"detail": exc.errors}, status=422, headers=exc.headers)
        if isinstance(exc, HTTPException):
            return Response.json({"detail": exc.detail}, status=exc.status, headers=exc.headers)
        logger.exception("unhandled error in %s %s", request.method, request.path, exc_info=exc)
        return _server_error()

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
        read_timeout: float = 30.0,
        uds: str | None = None,
        forwarded_allow_ips: Sequence[str] = ("127.0.0.1", "::1"),
        access_log: bool = False,
        ws_ping_interval: float = 20.0,
        forwarded_host: bool = False,
        threads: int = 1,
    ) -> None:
        """Serves the app with Korvex's built-in server until Ctrl+C or
        SIGTERM. Linux and macOS only; on Windows, use an ASGI server.

        - `workers` processes share the port, each running the startup and
          shutdown hooks. About 3 per 4 CPU cores works best.
        - `threads` event loops per worker, each in its own thread, each
          running the startup and shutdown hooks like a worker does: keep
          what they open in `app.local`, one per loop. With a free-threaded
          Python (3.14t), they run in parallel: one process uses several
          cores and shares its memory. With the GIL, they take turns on one
          core.
        - On stop, requests in progress get up to `graceful_timeout` seconds.
        - Request bodies over `max_body` bytes get a 413 (a route's own
          `max_body` overrides it); clients get `read_timeout` seconds to send
          the headers, then the body (408 after that).
        - `uds` listens on a Unix socket at that path instead of host:port,
          for a reverse proxy on the same machine.
        - From `forwarded_allow_ips` (`"*"` for any) or a Unix socket,
          `X-Forwarded-For` and `-Proto` (or `Forwarded`, when neither is
          there) set `request.client` and `request.scheme`. With
          `forwarded_host`, for a proxy that sets `X-Forwarded-Host` itself,
          the host they forward replaces the `Host` header too.
        - `access_log` logs each request to the `korvex.access` logger.
        - Open WebSockets get a ping every `ws_ping_interval` seconds, and
          are closed (1011) if the client stays silent until the next one;
          0 turns pings off.
        - A streamed response or a WebSocket waits whenever its client is
          1 MiB behind, until it catches up.
        """
        from korvex import _serve as serve

        serve.run(
            self,
            host,
            port,
            workers=workers,
            graceful_timeout=graceful_timeout,
            config=serve.Config(
                max_body,
                read_timeout,
                list(self._body_limits),
                list(forwarded_allow_ips),
                uds,
                access_log,
                ws_ping_interval,
                forwarded_host,
                tuple(self._router.constraints()),
                threads,
            ),
        )

    def __repr__(self) -> str:
        return f"App({len(self._routes)} routes, {len(self._ws_routes)} WebSocket routes)"


def _server_error() -> Response:
    """The answer to an error nothing handled: the cause is logged, never sent."""
    return Response.json({"detail": "Internal Server Error"}, status=500)


def _host_params_for(route: _Route | None, host_params: dict[str, str]) -> dict[str, str]:
    """The host parameters a handler takes: those it names, or all."""
    taken = route.host_args if route is not None else None
    return host_params if taken is None else {k: v for k, v in host_params.items() if k in taken}


def _raw_headers(response: Response, head: bool) -> list[tuple[bytes, bytes]]:
    """The response headers as bytes. A HEAD response keeps the Content-Length
    its body would have had, since the body itself isn't sent."""
    if head and not isinstance(response, StreamingResponse) and "Content-Length" not in response.headers:
        response.headers.set("Content-Length", str(len(response.body)))
    return [(name.encode("latin-1"), value.encode("latin-1")) for name, value in response.headers]


def _match(router: Router, request: Request) -> Any:
    """`router.match()` for the request: `None` if no route has the path; a
    405 if routes have it, but not for this method. HEAD is GET without the
    body."""
    try:
        return router.match(request.path, method=request.method)
    except MethodNotAllowed as exc:
        if request.method == "HEAD" and "GET" in exc.allowed:
            return router.match(request.path, method="GET")
        raise HTTPException(405, headers=[("Allow", ", ".join(exc.allowed))]) from None


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
