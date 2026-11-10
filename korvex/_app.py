"""`App`: an ASGI application built on `Router`.

Run it with any ASGI server (uvicorn, hypercorn, granian...) or with the
built-in one (`App.run()`).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import threading
import weakref
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from inspect import isawaitable
from pathlib import Path
from typing import Any, Optional, Union, overload
from urllib.parse import quote

from korvex._core import MethodNotAllowed
from korvex._errors import Abort, HTTPException, RequestValidationError
from korvex._mount import Lifespan, child_scope, child_ws_scope
from korvex._requests import Connection, Request, WebSocket, WebSocketDisconnect
from korvex._response import EventStream, Response, StreamingResponse
from korvex._routing import (
    Group,
    Handler,
    WebSocketHandler,
    _check_name_type,
    _Docs,
    _fast_call,
    _Host,
    _host_labels,
    _Route,
    _RouteDecorators,
)
from korvex._schema import Plan, Run, needs_plan, plan_for
from korvex._templates.env import Templates
from korvex.router import Router

BeforeHook = Callable[["Connection"], Union[Optional[Response], Awaitable[Optional[Response]]]]
"""`(request) -> Response | None`, sync or async. A `Response` is sent
right away; `None` moves on."""

Hook = Callable[[], Union[None, Awaitable[None]]]
"""`() -> None`, sync or async, run when the server starts or stops."""

AfterHook = Callable[["Request", Response], Union[Optional[Response], Awaitable[Optional[Response]]]]
"""`(request, response) -> Response | None`, sync or async. Returns the
response to send, or `None` to keep it."""

ExceptionHandler = Callable[["Request", Any], Union[Response, Awaitable[Response]]]
"""`(request, exception) -> Response`, sync or async. The exception can be
annotated with the class the handler is registered for."""

logger = logging.getLogger("korvex")


class LoopLocal:
    """Attributes kept per event loop: `app.local`, for what a startup hook opens (a pool, a client).

    Use it from a running loop only. Guide: https://3d3n-pyc.github.io/korvex/server/#threads"""

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


_MOUNT_PREFIX = re.compile(r"(?:/[^/{}*?#%\\\s\x00-\x1f\x7f]+)+")  # /admin, /api/v2: no parameter, query or space
_MOUNT_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
_QUERY_SAFE = "%&=+;,:@/?!$'()*~-._"  # kept as is in a redirect's query


class MountMatch(BaseException):
    """Raised by a mount's route: the request belongs to `app`, mounted at `prefix`.

    Not an `Exception`, so error handlers and after hooks never see it; the
    code that answered with `App._respond()` hands the request to the mounted app."""

    def __init__(self, app: Callable, prefix: str) -> None:
        self.app = app
        self.prefix = prefix


_HASHED = re.compile(r"((?:.*/)?[^/]+)\.([0-9a-f]{8})(\.[^./]+)?\Z")  # app.3f2a1c9b.css, LICENSE.3f2a1c9b


def _flashed_messages() -> list[tuple[str, str]]:
    """Template global: the flashed `(category, message)` pairs (needs `sessions()`)."""
    from korvex.middleware._sessions import get_flashed_messages

    return get_flashed_messages()


class App(_RouteDecorators):
    """The application: routes, hooks, error handlers, OpenAPI, and `run()`. It is an ASGI app.

    Guide: https://3d3n-pyc.github.io/korvex/routes/"""

    def __init__(self) -> None:
        self._router = Router()  # HTTP routes
        self._ws_router = Router()  # WebSocket routes: a path can have both
        # Each route, under its handler name in the router: HTTP, WebSocket.
        self._routes: dict[str, _Route] = {}
        self._ws_routes: dict[str, _Route] = {}
        self._statics: dict[str, tuple[str, Path]] = {}  # app.static() name -> (prefix, root)
        self._digests: dict[Path, tuple[int, int, str]] = {}  # file -> (mtime_ns, size, hash)
        self._hosts: list[_Host] = []  # app.host() patterns, the ones without parameters first
        self.local = LoopLocal()
        """Attributes per event loop: see `LoopLocal`."""
        self._dependency_plans: dict[tuple, Plan] = {}  # (function, pattern, websocket) -> its plan
        self.dependency_overrides: dict[Callable[..., Any], Callable[..., Any]] = {}
        """`{dependency: replacement}`: called instead of the dependency, for tests."""
        self._root = Group(self, self._router.group())
        self._on_startup: list[Hook] = []
        self._on_shutdown: list[Hook] = []
        self._exception_handlers: dict[int | type[Exception], ExceptionHandler] = {}
        self._body_limits: list[tuple[str, str, int]] = []  # (method, path, bytes), for the built-in server
        self._before_request: list[BeforeHook] = []
        self._after_request: list[AfterHook] = []
        # Where each name given with name= points: (websocket, host, path).
        self._explicit_names: dict[str, tuple[bool, str | None, str]] = {}
        self._root_path = ""  # the root_path last seen in a request (an app is served under one)
        self._url_cache: tuple[tuple, Any] | None = None
        self._mounts: dict[str, str | None] = {}  # app.mount() prefix -> its name
        self._mount_names: dict[str, str] = {}  # name -> prefix

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
        """Decorator: the function answers `path` for `method`. `@app.get(path)` and the others take the same options.

        - `name`: what `url_for()` knows the route by (the function's name by default).
        - `status`: the status of a response made from a model or `None` (`-> None` with `204`: empty).
        - `dependencies`: `[Depends(func), ...]`, run before the handler.
        - `tags`, `deprecated`, `responses`, `include_in_schema`: the route's OpenAPI entry.
        - `max_body` (bytes): the built-in server's body limit for this route; ASGI servers ignore it.

        Guide: https://3d3n-pyc.github.io/korvex/routes/#route-options"""
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
        """Returns a `Group` that registers handlers under `prefix`, with `dependencies` and OpenAPI options for all.

        Guide: https://3d3n-pyc.github.io/korvex/routes/#groups"""
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
        """Returns a group whose routes only answer for requests to `pattern`, a host (`{param}` takes one label).

        Calling it again with the same pattern adds to the same routes; a route's `max_body`
        applies to its path on every host.
        Guide: https://3d3n-pyc.github.io/korvex/routes/#hosts"""
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
        """Decorator: registers the function as the WebSocket handler for `path`.

        It gets a `WebSocket`, path params, and parameters and dependencies as HTTP
        handlers do (no body). `name`: for `url_for()`.
        Guide: https://3d3n-pyc.github.io/korvex/websockets/"""
        return self._root.websocket(path, name=name, dependencies=dependencies)

    def url_for(self, name: str, /, **params: Any) -> str:
        """The path of the route named `name`, with `params` for its path parameters.

        Raises LookupError if several routes share a function's name (give a `name=`; routes
        share a `name=` only on one path, for different methods). Raises ValueError for a value
        that doesn't fit its constraint or the handler's annotation, is empty, `.` or `..`,
        or has a `/` outside a `*wildcard`, or when another route answers the path first
        (TypeError for an unsupported type). Every path parameter must be given, and nothing
        else: add a query string yourself. Values are percent-encoded; a host route gives the
        path only. An app is served under one `root_path`: it prefixes the path.
        Guide: https://3d3n-pyc.github.io/korvex/routes/#links-to-a-route"""
        if name in self._mount_names:
            return self._root_path + _mount_url(name, self._mount_names[name], params)
        from korvex import _urls as urls

        return self._root_path + urls.path_for(self, urls.lookup(self._url_index(), name), params)

    def _url_index(self) -> Any:
        """The route names, rebuilt when routes change."""
        from korvex import _urls as urls

        routers = [self._router, self._ws_router] + [r for h in self._hosts for r in (h.router, h.ws_router)]
        signature = (len(self._routes), len(self._ws_routes), *(getattr(r, "version", None) for r in routers))
        if self._url_cache is None or self._url_cache[0] != signature:
            self._url_cache = (signature, urls.build_index(self))
        return self._url_cache[1]

    def _check_name(self, name: str | None, websocket: bool, host: _Host | None, path: str) -> str | None:
        """Checks a route's `name=` before registration: one name, one path
        (methods aside)."""
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
        """Stores a registered route under its handler name."""
        (self._ws_routes if websocket else self._routes)[key] = route
        if route.explicit and route.name is not None:
            self._explicit_names[route.name] = (websocket, host.pattern if host else None, path)

    def _plan(self, func: Callable, pattern: str, dependencies: Sequence[Any] = (), *, websocket: bool = False) -> Plan:
        return plan_for(func, pattern, dependencies=dependencies, websocket=websocket, cache=self._dependency_plans)

    def _dependency_plan(self, func: Callable, pattern: str, websocket: bool) -> Plan:
        """The plan of a dependency override, built on first use."""
        key = (func, pattern, websocket)
        plan = self._dependency_plans.get(key)
        if plan is None:
            plan = plan_for(func, pattern, websocket=websocket, handler=False, cache=self._dependency_plans)
            self._dependency_plans[key] = plan
        return plan

    def openapi(
        self, *, title: str = "API", version: str = "0.1.0", description: str | None = None, host: str | None = None
    ) -> dict:
        """An OpenAPI 3.1 description of the app's routes (static files and WebSockets left out).

        With `host`, the routes that answer there; without, only those with no host.
        Guide: https://3d3n-pyc.github.io/korvex/openapi/"""
        from korvex import _openapi as openapi

        return openapi.build(self, title=title, version=version, description=description, host=host)

    def _host_for(self, labels: list[str]) -> tuple[_Host, dict[str, str]] | None:
        """The first host pattern the request's host fits, and its
        parameters. Only its routes, then those without a host, can answer:
        a literal `api.example.com` never gets `{tenant}.example.com`'s
        routes."""
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
        """Serves the OpenAPI document at `openapi_path` and Swagger UI at `docs_path` (`None`: none).

        `title`, `version` and `description` go in the document.
        Guide: https://3d3n-pyc.github.io/korvex/openapi/"""
        from korvex import _openapi as openapi

        def serve_openapi(request: Request) -> Response:
            # Each host documents the routes that answer there.
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
        """Defines a route constraint `{param:name}`, matching the whole value against `pattern`.

        Rust `regex` syntax. Constrained parameters are tried in registration order, before
        plain ones; the handler gets a `str`. Define it before the routes that use it; it
        can't be redefined differently.
        Guide: https://3d3n-pyc.github.io/korvex/routes/#your-own-constraints"""
        self._router.constraint(name, pattern)
        for host in self._hosts:
            host.router.constraint(name, pattern)
            host.ws_router.constraint(name, pattern)

    def static(self, prefix: str, directory: str | os.PathLike[str], *, name: str | None = None) -> None:
        """Serves the files in `directory` under `prefix`; paths leaving it get a 404.

        `name` (`"static"` by default) is what `url_for()` knows it by.
        Guide: https://3d3n-pyc.github.io/korvex/routes/#static-files"""
        root = Path(directory).resolve()
        if not root.is_dir():
            raise NotADirectoryError(f"not a directory: {directory}")

        def find(path: str) -> Path | None:
            try:
                target = (root / path).resolve()
            except (OSError, ValueError):  # e.g. a null byte in the path
                return None
            return target if target.is_relative_to(root) and target.is_file() else None

        def serve(request: Request, path: str) -> Response:
            target, digest = find(path), None
            if target is None and (hashed := _HASHED.match(path)):
                target, digest = find(hashed[1] + (hashed[3] or "")), hashed[2]
            if target is None:
                raise HTTPException(404)
            immutable = digest is not None and digest == self._digest(target)
            cache = "public, max-age=31536000, immutable" if immutable else "no-cache"
            return Response.file(target, headers={"Cache-Control": cache}, request=request)

        key = f"korvex.static:{prefix}"  # all serve() share a qualname
        pattern = f"{prefix.rstrip('/')}/*path"
        self._check_name(name, False, None, pattern)
        self._router.route(pattern, key)
        self._add_route(False, key, _Route(serve, name=name or "static", explicit=name is not None), None, pattern)
        self._statics.setdefault(name or "static", (prefix.rstrip("/"), root))

    def mount(self, prefix: str, asgi_app: Callable, *, name: str | None = None) -> None:
        """Serves another ASGI app (a Starlette app, a Korvex `App`...) under `prefix`.

        The app sees paths without `prefix` (its `root_path` has it). A route of this
        app that matches wins. This app's before hooks run first (they protect the mount);
        its after hooks and exception handlers don't run for the mount. The mounted app's
        lifespan runs with this app's startup and shutdown. The bare `prefix` redirects (308)
        to `prefix/`. `name`: `url_for(name)` gives `prefix/`, `url_for(name, path="a/b")` gives
        `prefix/a/b`. Raises ValueError for an invalid or already mounted `prefix`.
        Guide: https://3d3n-pyc.github.io/korvex/routes/#mounting-another-app"""
        prefix = _mount_prefix(prefix)
        if prefix in self._mounts:
            raise ValueError(f"{prefix!r} is already mounted")
        if not callable(asgi_app):
            raise TypeError("mount() takes an ASGI app: a callable (scope, receive, send)")
        pattern = f"{prefix}/*path"
        self._check_name(name, False, None, pattern)

        def serve(request: Request, path: str) -> Response:
            if not _in_mount(request.path, prefix):
                raise HTTPException(404)
            raise MountMatch(asgi_app, prefix)

        def redirect(request: Request) -> Response:
            if request.path != prefix:  # `prefix/` (the router ignores the slash) is the mount's root
                return serve(request, "")
            query = request._query_string.decode("latin-1")
            suffix = f"?{quote(query, safe=_QUERY_SAFE)}" if query else ""
            return Response.redirect(f"{request.root_path}{prefix}/{suffix}", status=308)

        async def serve_ws(websocket: WebSocket, path: str) -> None:
            raise MountMatch(asgi_app, prefix)  # never called: _websocket() hands it over

        serve_ws.mounted = (asgi_app, prefix)  # type: ignore[attr-defined]
        key, bare = f"korvex.mount:{prefix}", f"korvex.mount-redirect:{prefix}"
        for method in _MOUNT_METHODS:
            self._router.route(pattern, key, method=method)
            self._router.route(prefix, bare, method=method)
        self._ws_router.route(pattern, key)
        self._routes[key], self._routes[bare] = _Route(serve), _Route(redirect)
        self._ws_routes[key] = _Route(serve_ws, self._plan(serve_ws, pattern, websocket=True))
        self._mounts[prefix] = name
        if name is not None:
            self._mount_names[name] = prefix
            self._explicit_names[name] = (False, None, pattern)
        self._mount_lifespan(asgi_app, prefix)

    def _mount_lifespan(self, asgi_app: Callable, prefix: str) -> None:
        """Runs the mounted app's lifespan with this app's startup and shutdown."""
        key = f"_mount_lifespan:{prefix}"

        async def startup() -> None:
            lifespan = Lifespan(asgi_app)  # made in the loop: queues belong to it
            await lifespan.startup()
            setattr(self.local, key, lifespan)

        async def shutdown() -> None:
            lifespan = getattr(self.local, key, None)
            if lifespan is not None:
                delattr(self.local, key)
                await lifespan.shutdown()

        self._on_startup.append(startup)
        self._on_shutdown.append(shutdown)

    def static_url(self, path: str, /, *, name: str | None = None) -> str:
        """The URL of a static file, with a hash of its content: `/static/app.3f2a1c9b.css`.

        `path`: the file, relative to the folder given to `static()`.
        `name`: which `static()` mount, when there are several (`"static"` by default).
        Raises LookupError for an unknown mount, FileNotFoundError for a missing file.
        An app is served under one `root_path`: it prefixes the URL.
        Guide: https://3d3n-pyc.github.io/korvex/templates/"""
        mount = self._statics.get(name or "static")
        if mount is None:
            raise LookupError(f"no static mount named {name or 'static'!r}")
        prefix, root = mount
        target = (root / path).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise FileNotFoundError(f"no static file {path!r}")
        head, slash, base = path.rpartition("/")
        digest, dot = self._digest(target), base.rfind(".")
        hashed = f"{head}{slash}{base[:dot]}.{digest}{base[dot:]}" if dot > 0 else f"{path}.{digest}"
        return f"{self._root_path}{prefix}/{quote(hashed)}"

    def _digest(self, target: Path) -> str:
        """The first 8 hex digits of the SHA-256 of a file, cached until its mtime or size changes."""
        info = target.stat()
        stamp = (info.st_mtime_ns, info.st_size)
        cached = self._digests.get(target)
        if cached is None or cached[:2] != stamp:
            cached = (*stamp, hashlib.sha256(target.read_bytes()).hexdigest()[:8])
            self._digests[target] = cached
        return cached[2]

    def templates(self, directory: str | os.PathLike[str] = "templates", *, reload: bool = False) -> Templates:
        """Returns the `Templates` for `directory`, with `url_for`, `static_url` and `get_flashed_messages` available in every template.

        `directory`: the template folder, relative to the current directory.
        `reload`: recompile templates when their files change.
        Guide: https://3d3n-pyc.github.io/korvex/templates/"""
        templates = Templates(Path(directory), reload=reload)
        templates.globals["url_for"] = self.url_for
        templates.globals["static_url"] = self.static_url
        templates.globals["get_flashed_messages"] = _flashed_messages
        return templates

    # The overloads let `@app.on_startup` and `@app.on_startup()` both type-check.
    @overload
    def on_startup(self, func: Hook) -> Hook: ...
    @overload
    def on_startup(self, func: None = None) -> Callable[[Hook], Hook]: ...
    def on_startup(self, func: Hook | None = None) -> Hook | Callable[[Hook], Hook]:
        """Decorator: runs the function at server start; if it raises, the server doesn't start.

        Guide: https://3d3n-pyc.github.io/korvex/errors-and-hooks/#startup-and-shutdown"""
        return _add_hook(self._on_startup, func)

    @overload
    def on_shutdown(self, func: Hook) -> Hook: ...
    @overload
    def on_shutdown(self, func: None = None) -> Callable[[Hook], Hook]: ...
    def on_shutdown(self, func: Hook | None = None) -> Hook | Callable[[Hook], Hook]:
        """Decorator: runs the function at server stop, in reverse order of declaration.

        Guide: https://3d3n-pyc.github.io/korvex/errors-and-hooks/#startup-and-shutdown"""
        return _add_hook(self._on_shutdown, func)

    @overload
    def before_request(self, func: BeforeHook) -> BeforeHook: ...
    @overload
    def before_request(self, func: None = None) -> Callable[[BeforeHook], BeforeHook]: ...
    def before_request(self, func: BeforeHook | None = None) -> BeforeHook | Callable[[BeforeHook], BeforeHook]:
        """Decorator: runs the function before routing, so also for unknown paths and handshakes.

        It gets the request (`path_params` empty) and returns a `Response` to answer, or `None`.
        For a WebSocket handshake, answering or raising `HTTPException` or `Abort` refuses it.
        Guide: https://3d3n-pyc.github.io/korvex/errors-and-hooks/#hooks-around-every-request"""
        return _add_hook(self._before_request, func)

    @overload
    def after_request(self, func: AfterHook) -> AfterHook: ...
    @overload
    def after_request(self, func: None = None) -> Callable[[AfterHook], AfterHook]: ...
    def after_request(self, func: AfterHook | None = None) -> AfterHook | Callable[[AfterHook], AfterHook]:
        """Decorator: runs the function on every response, errors included, in reverse order.

        It returns the response to send, or `None` to keep it.
        Guide: https://3d3n-pyc.github.io/korvex/errors-and-hooks/#hooks-around-every-request"""
        return _add_hook(self._after_request, func)

    def exception_handler(self, key: int | type[Exception]) -> Callable[[ExceptionHandler], ExceptionHandler]:
        """Decorator: answers errors with the function's `Response`.

        - `key`: an HTTP status (`404` also covers unknown paths) or an exception class.

        Guide: https://3d3n-pyc.github.io/korvex/errors-and-hooks/#answering-errors-your-way"""

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

        request = Request(scope, receive, {}, self)
        try:
            response = await self._respond(request)
        except MountMatch as matched:
            mount = matched
        else:  # outside the except: errors of a mounted app don't show the MountMatch
            head = scope["method"] == "HEAD"
            if request._tasks is not None:  # rare: background tasks run once the response is out
                await self._send_with_tasks(request, response, send, head, receive)
            elif isinstance(response, StreamingResponse):
                await self._send_stream(response, send, head, request.path, receive)
            else:
                await send(
                    {"type": "http.response.start", "status": response.status, "headers": _raw_headers(response, head)}
                )
                # HEAD: GET's headers, no body.
                await send({"type": "http.response.body", "body": b"" if head else response.body})
            return
        await self._call_mount(mount, request, scope, receive, send)

    async def _send_with_tasks(
        self, request: Request, response: Response, send: Callable, head: bool, receive: Callable
    ) -> None:
        """Sends the response, then runs the request's background tasks, sent or not."""
        try:
            if isinstance(response, StreamingResponse):
                await self._send_stream(response, send, head, request.path, receive)
                return
            await send(
                {"type": "http.response.start", "status": response.status, "headers": _raw_headers(response, head)}
            )
            await send({"type": "http.response.body", "body": b"" if head else response.body})
        finally:
            await request._tasks._run()

    def _mount_state(self, prefix: str) -> Optional[dict]:
        """The lifespan state of the app mounted at `prefix`, in this event loop."""
        lifespan = getattr(self.local, f"_mount_lifespan:{prefix}", None)
        return lifespan.state if lifespan is not None else None

    async def _call_mount(
        self, mount: MountMatch, request: Request, scope: dict, receive: Callable, send: Callable
    ) -> None:
        """Calls the mounted app with its own scope; a body a hook read is replayed."""
        state = self._mount_state(mount.prefix)
        child = child_scope(request, mount.prefix, _raw_path(scope, request), scope.get("server"), state)
        if request._body is not None:
            receive = _replay(request._body, receive)
        await mount.app(child, receive, send)

    @staticmethod
    async def _send_stream(
        response: StreamingResponse, send: Callable, head: bool, path: str, receive: Callable
    ) -> None:
        try:
            await send(
                {"type": "http.response.start", "status": response.status, "headers": _raw_headers(response, head)}
            )
            if not head:
                if isinstance(response, EventStream):
                    if not await _send_events(response, send, receive):
                        return  # the client left
                else:
                    async for chunk in response:
                        await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b""})
        finally:
            await _finish(response, path)  # sent or not, what waited for it runs

    async def _respond(self, request: Request, error: int = 0) -> Response:
        """The response to one request: before hooks, the route, then after
        hooks, with errors turned into responses at each step. With `error`,
        the route is replaced by that HTTP error."""
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
            if request._tasks is not None:
                request._tasks._dropped = True  # the handler raised: its tasks never run
            response = await self._handle_error(request, exc)
        for hook in reversed(self._after_request):
            try:
                changed = await _call(hook, request, response)
            except Exception:
                logger.exception("after_request hook failed for %s %s", request.method, request.path)
                return _server_error()
            if changed is not None:
                if changed is not response and isinstance(response, StreamingResponse):
                    await _finish(response, request.path)  # replaced, never sent
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
        fast = route.fast
        if fast is not None:  # nothing to bind: call it directly
            name, is_async = fast
            result = route.func(**path_params, **{name: request}) if name is not None else route.func(**path_params)
            if is_async:
                return await result
            return await result if isawaitable(result) else result  # a sync wrapper of an async function
        handler = route.func
        plan = route.plan if route.plan is not None else self._plan_of(handler_name)
        if not needs_plan(plan):  # no annotations to act on: no binding cost
            if plan.request_name is not None:
                return await _call(handler, **path_params, **{plan.request_name: request})
            if plan.request:  # several names
                return await _call(handler, **path_params, **dict.fromkeys(plan.request, request))
            return await _call(handler, **path_params)
        if not plan.dependencies:
            arguments = {**path_params, **await plan.bind(request, values, None)}
            return plan.respond(await _call(handler, **arguments))
        run = Run(self.dependency_overrides, self._dependency_plan)
        try:
            arguments = {**path_params, **await plan.bind(request, values, run)}
            response = plan.respond(await _call(handler, **arguments))
        except BaseException as exc:
            await run.close(exc)  # raised at the yields
            raise
        if run.open:
            if isinstance(response, StreamingResponse):
                response._on_close.append(run.close)  # once the stream is sent
            else:
                await run.close()
        return response

    def _plan_of(self, handler_name: str) -> Plan:
        """The plan of an HTTP route's handler. The App's own routes (static
        files, docs) get theirs on first use."""
        route = self._routes[handler_name]
        if route.plan is None:
            pattern = next((path for path, _, name, _ in self._router.routes() if name == handler_name), "")
            route.plan = self._plan(route.func, pattern)
            route.fast = _fast_call(route.plan)
        return route.plan

    async def _websocket(self, scope: dict, receive: Callable, send: Callable) -> None:
        """Runs a WebSocket connection: its handler, or a refusal (403) for
        an unknown path, a before hook answering, or a dependency raising
        HTTPException. Leaves nothing open."""
        found, host_params = None, None
        websocket = WebSocket(scope, receive, send, {}, self)
        host = self._host_for(_host_labels(Request(scope, receive, {}))) if self._hosts else None
        if host is not None:
            found = host[0].ws_router.match(websocket.path)
            host_params = host[1] if found is not None else None
        if found is None:
            found = self._ws_router.match(websocket.path)
        values = found[1] if found else {}
        if found is not None and host_params:
            values = {**host_params, **found[1]}
        websocket.path_params = values
        if self._before_request and not await self._websocket_hooks_allow(websocket):
            await websocket.close()  # before accept: the client gets a 403
            return
        route = self._ws_routes.get(found[0]) if found is not None else None
        if found is None or route is None:
            await websocket.close()
            return
        mounted = getattr(route.func, "mounted", None)
        if mounted is not None:
            if not _in_mount(websocket.path, mounted[1]):
                await websocket.close()
                return
            child = child_ws_scope(
                websocket, mounted[1], _raw_path(scope, websocket), scope.get("server"), self._mount_state(mounted[1])
            )
            await mounted[0](child, receive, send)
            return
        path_params = found[1]
        if host_params:
            path_params = {**_host_params_for(route, host_params), **path_params}
        handler = route.func
        plan = route.plan
        assert plan is not None  # built at registration
        run = Run(self.dependency_overrides, self._dependency_plan) if plan.dependencies else None
        try:
            arguments: dict[str, Any] = dict(path_params)
            if not needs_plan(plan):
                arguments.update(dict.fromkeys(plan.request, websocket))
            else:
                try:
                    arguments.update(await plan.bind(websocket, values, run))
                except (HTTPException, Abort) as exc:  # a 422 too
                    status = exc.response.status if isinstance(exc, Abort) else exc.status
                    logger.info("WebSocket %s refused: %s", websocket.path, status)
                    if run is not None:
                        await run.close(exc)
                    await websocket.close()  # before accept: the client gets a 403
                    return
            await handler(**arguments)
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
        """Tears dependencies down with no response left to change; a
        teardown error is logged."""
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
        if isinstance(exc, RequestValidationError):
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
        max_connections: int = 0,
        max_requests: int = 0,
        root_path: str = "",
    ) -> None:
        """Serves the app with the built-in server until Ctrl+C or SIGTERM (Linux and macOS only).

        - `workers`: processes sharing the port, each running the startup and shutdown hooks.
        - `threads`: event loops per worker; keep what they open in `app.local`.
        - `graceful_timeout`: seconds requests in progress get on stop.
        - `max_body`: bytes; larger bodies get a 413 (a route's `max_body` overrides it).
        - `read_timeout`: seconds a client gets to send headers, then body (408).
        - `uds`: a Unix socket path to listen on instead of host:port.
        - `forwarded_allow_ips`: proxies trusted for `X-Forwarded-For`/`-Proto` (`"*"`: any).
        - `forwarded_host`: also take the forwarded host, only if your proxy sets it itself.
        - `access_log`: log each request to `korvex.access`.
        - `ws_ping_interval`: seconds between WebSocket pings; `0` turns them off.
        - `max_connections`: open connections per worker and thread; `0`: no limit.
        - `max_requests`: a worker restarts after about that many requests; `0`: never.
        - `root_path`: the prefix a proxy serves the app under (`"/api"`), for links.

        Guide: https://3d3n-pyc.github.io/korvex/server/"""
        from korvex import _serve as serve

        self._root_path = root_path  # for links made outside a request, like in templates
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
                max_connections,
                max_requests,
                root_path,
            ),
        )

    def __repr__(self) -> str:
        return f"App({len(self._routes)} routes, {len(self._ws_routes)} WebSocket routes)"


def _in_mount(path: str, prefix: str) -> bool:
    """True if `path` (as the hooks see it) is `prefix` or below it."""
    return path == prefix or path.startswith(prefix + "/")


def _mount_prefix(prefix: str) -> str:
    """A valid mount prefix without its trailing slash; ValueError otherwise."""
    cleaned = prefix.rstrip("/") if isinstance(prefix, str) else ""
    if not _MOUNT_PREFIX.fullmatch(cleaned) or any(p in (".", "..") for p in cleaned.split("/")):
        raise ValueError(
            f"invalid mount prefix {prefix!r}: it starts with '/', is not '/' itself, "
            "and has no {}, *, ?, #, % or empty, '.' or '..' part"
        )
    return cleaned


def _mount_url(name: str, prefix: str, params: dict[str, Any]) -> str:
    """`url_for()` of a named mount: `prefix/` plus the optional `path`."""
    extra = sorted(set(params) - {"path"})
    if extra:
        raise ValueError(f"url_for({name!r}): unexpected {', '.join(extra)}; a mount only takes path")
    path = params.get("path", "")
    if not isinstance(path, str):
        raise TypeError(f"url_for({name!r}): path must be a str")
    parts = path.split("/") if path else []
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"url_for({name!r}): path={path!r} has an empty, '.' or '..' part")
    return prefix + "/" + "/".join(quote(part, safe="") for part in parts)


def _raw_path(scope: dict, connection: Connection) -> bytes | None:
    """The scope's `raw_path` without `root_path` (the form `child_scope` takes), if it has one."""
    raw = scope.get("raw_path")
    root = connection.root_path.encode()
    return raw[len(root) :] if raw and root and raw.startswith(root) else raw


def _replay(body: bytes, receive: Callable) -> Callable:
    """A `receive` that gives `body` again (a hook read it), then the real one."""
    replayed = False

    async def replay() -> dict:
        nonlocal replayed
        if replayed:
            return await receive()
        replayed = True
        return {"type": "http.request", "body": body, "more_body": False}

    return replay


def _server_error() -> Response:
    """The 500 for an unhandled error: the cause is logged, never sent."""
    return Response.json({"detail": "Internal Server Error"}, status=500)


def _host_params_for(route: _Route | None, host_params: dict[str, str]) -> dict[str, str]:
    """The host parameters a handler takes: those it names, or all."""
    taken = route.host_args if route is not None else None
    return host_params if taken is None else {k: v for k, v in host_params.items() if k in taken}


def _raw_headers(response: Response, head: bool) -> list[tuple[bytes, bytes]]:
    """The response headers as bytes. A HEAD response keeps the
    Content-Length its body would have had."""
    if head and not isinstance(response, StreamingResponse) and "Content-Length" not in response.headers:
        response.headers.set("Content-Length", str(len(response.body)))
    return [(name.encode("latin-1"), value.encode("latin-1")) for name, value in response.headers]


def _match(router: Router, request: Request) -> Any:
    """`router.match()` for the request: `None` if no route has the path; a
    405 if routes have it, but not for this method; 501 for a method the router
    does not know. HEAD is GET without the
    body."""
    try:
        return router.match(request.path, method=request.method)
    except MethodNotAllowed as exc:
        if request.method == "HEAD" and "GET" in exc.allowed:
            return router.match(request.path, method="GET")
        raise HTTPException(405, headers=[("Allow", ", ".join(exc.allowed))]) from None
    except ValueError as exc:
        if "unknown HTTP method" in str(exc):
            raise HTTPException(501) from None
        raise


async def _pump(response: StreamingResponse, send: Callable) -> None:
    """Sends every chunk of the response."""
    async for chunk in response:
        await send({"type": "http.response.body", "body": chunk, "more_body": True})


async def _wait_disconnect(receive: Callable) -> None:
    """Returns when the client disconnects."""
    while (await receive())["type"] != "http.disconnect":
        pass


async def _send_events(response: EventStream, send: Callable, receive: Callable) -> bool:
    """Sends an event stream while watching the client. Returns False if the
    client left first; the source is then closed, so its `finally` runs."""
    pump = asyncio.ensure_future(_pump(response, send))
    watch = asyncio.ensure_future(_wait_disconnect(receive))
    try:
        await asyncio.wait({pump, watch}, return_when=asyncio.FIRST_COMPLETED)
        if not pump.done():
            return False
        pump.result()  # raises what the source or the send raised
        return True
    finally:
        for task in (pump, watch):
            task.cancel()
        await asyncio.gather(pump, watch, return_exceptions=True)
        await response.aclose()


async def _finish(response: StreamingResponse, path: str) -> None:
    """Runs what waits for a streamed response to be sent (its dependencies'
    teardown). Errors are logged."""
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
    does (`@app.on_startup()`)."""
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
