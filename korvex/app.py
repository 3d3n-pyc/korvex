"""A minimal ASGI application wired to a `Router` — the thin layer that
lets you actually serve requests, e.g. `uvicorn mymodule:app`.

Korvex still never touches a socket, speaks HTTP, or manages a request's
lifecycle beyond this: that's the ASGI server's job (uvicorn, hypercorn,
daphne, ...), which already does it well and fast. `App` is only the
glue — match a path, run middleware, call a handler, send a `Response` —
built entirely from the standard library (`json`, `urllib.parse`,
`inspect`) plus `Router`/`Response`/`Headers`, already in this package.
No new dependency, sync or async servers all work the same way, because
ASGI is a calling convention, not a library.
"""

from __future__ import annotations

import json as _json
from collections.abc import Awaitable, Callable, Sequence
from inspect import isawaitable
from typing import Any, TypeVar, Union
from urllib.parse import parse_qs

from korvex._core import MethodNotAllowed
from korvex.response import Headers, JSONValue, Response
from korvex.router import Router

# `Union`, not `X | Y`: see the note by `JSONValue` in response.py — these
# are plain runtime assignments, not deferred by `from __future__ import
# annotations`, so they need to stay 3.9-compatible themselves.
Handler = Callable[..., Union[Response, Awaitable[Response]]]
"""A route handler: `(request, **path_params) -> Response`, sync or
async. `path_params` are always `str` — Korvex validates a constrained
segment's *shape* (`{id:int}`) but never converts its type."""

MiddlewareFn = Callable[["Request"], Union[Response, None, Awaitable[Union[Response, None]]]]
"""A middleware hook: `(request) -> Response | None`, sync or async.
Returning a `Response` short-circuits — the handler (and any remaining
middleware) never runs; returning `None` continues to the next one."""

_T = TypeVar("_T")


class Request:
    """A read-only view of one incoming ASGI HTTP request."""

    __slots__ = ("method", "path", "headers", "query_params", "path_params", "_receive")

    def __init__(self, scope: dict, receive: Callable[[], Awaitable[dict]], path_params: dict[str, str]) -> None:
        self.method: str = scope["method"]
        self.path: str = scope["path"]
        self.headers = Headers((k.decode("latin-1"), v.decode("latin-1")) for k, v in scope["headers"])
        self.query_params: dict[str, list[str]] = parse_qs(scope.get("query_string", b"").decode("latin-1"))
        self.path_params = path_params
        self._receive = receive

    async def body(self) -> bytes:
        """Reads and returns the full request body, buffering as many
        ASGI `http.request` messages as the server sends."""
        chunks = []
        more_body = True
        while more_body:
            message = await self._receive()
            chunks.append(message.get("body", b""))
            more_body = message.get("more_body", False)
        return b"".join(chunks)

    async def json(self) -> JSONValue:
        """Reads the body and parses it as JSON."""
        return _json.loads(await self.body())

    def __repr__(self) -> str:
        return f"Request({self.method} {self.path!r})"


class App:
    """An ASGI application: matches requests with `router`, dispatches to
    handlers/middleware registered here under the same opaque names
    `Router` deals in. `App.get`/`.post`/etc. register a route and its
    handler in one step; `Router` itself stays name-only and doesn't know
    `App`, `Request`, or `Response` exist — this is purely an optional
    layer on top, for whoever wants to actually run a server rather than
    dispatch by hand.

    Run it with any ASGI server, e.g.::

        app = App()

        @app.get("/users/{id:int}")
        async def get_user(request, id):
            return Response.json({"id": id})

        # uvicorn mymodule:app
    """

    def __init__(self, router: Router | None = None) -> None:
        self.router = router if router is not None else Router()
        self._handlers: dict[str, Handler] = {}
        self._middleware: dict[str, MiddlewareFn] = {}

    def handler(self, name: str) -> Callable[[Handler], Handler]:
        """Attaches `name` (as already used in `router.add_route`/`router.group(...)`)
        to a handler function. Use this when routes were registered with
        explicit string names rather than through `.get`/`.post`/etc."""

        def register(func: Handler) -> Handler:
            self._handlers[name] = func
            return func

        return register

    def middleware(self, name: str) -> Callable[[MiddlewareFn], MiddlewareFn]:
        """Attaches `name` (as used in a route's `middleware=[...]`) to a
        middleware hook."""

        def register(func: MiddlewareFn) -> MiddlewareFn:
            self._middleware[name] = func
            return func

        return register

    def route(
        self, path: str, *, method: str = "GET", middleware: Sequence[str] | None = None
    ) -> Callable[[Handler], Handler]:
        """Registers `path`/`method` on `router` and attaches the decorated
        function as its handler in one step, under an opaque name derived
        from the function itself (visible via `router.routes()` for
        debugging)."""

        def register(func: Handler) -> Handler:
            name = f"{func.__module__}.{func.__qualname__}"
            self.router.add_route(path, name, method=method, middleware=middleware)
            self._handlers[name] = func
            return func

        return register

    def get(self, path: str, *, middleware: Sequence[str] | None = None) -> Callable[[Handler], Handler]:
        return self.route(path, method="GET", middleware=middleware)

    def post(self, path: str, *, middleware: Sequence[str] | None = None) -> Callable[[Handler], Handler]:
        return self.route(path, method="POST", middleware=middleware)

    def put(self, path: str, *, middleware: Sequence[str] | None = None) -> Callable[[Handler], Handler]:
        return self.route(path, method="PUT", middleware=middleware)

    def patch(self, path: str, *, middleware: Sequence[str] | None = None) -> Callable[[Handler], Handler]:
        return self.route(path, method="PATCH", middleware=middleware)

    def delete(self, path: str, *, middleware: Sequence[str] | None = None) -> Callable[[Handler], Handler]:
        return self.route(path, method="DELETE", middleware=middleware)

    def head(self, path: str, *, middleware: Sequence[str] | None = None) -> Callable[[Handler], Handler]:
        return self.route(path, method="HEAD", middleware=middleware)

    def options(self, path: str, *, middleware: Sequence[str] | None = None) -> Callable[[Handler], Handler]:
        return self.route(path, method="OPTIONS", middleware=middleware)

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] == "lifespan":
            await self._run_lifespan(receive, send)
            return
        if scope["type"] != "http":
            raise NotImplementedError(f"App only handles ASGI 'http' and 'lifespan' scopes, got {scope['type']!r}")

        try:
            result = self.router.match_route(scope["path"], method=scope["method"])
        except MethodNotAllowed as exc:
            allowed = ", ".join(exc.args[0])
            await self._send(send, Response.text("Method Not Allowed", status=405, headers=[("Allow", allowed)]))
            return

        if result is None:
            await self._send(send, Response.text("Not Found", status=404))
            return

        handler_name, path_params, middleware_names = result
        request = Request(scope, receive, path_params)

        for name in middleware_names:
            middleware = self._middleware.get(name)
            if middleware is None:
                raise LookupError(f"no middleware registered for {name!r}; use @app.middleware({name!r})")
            short_circuit = await _call(middleware, request)
            if short_circuit is not None:
                await self._send(send, short_circuit)
                return

        handler = self._handlers.get(handler_name)
        if handler is None:
            raise LookupError(f"no handler registered for {handler_name!r}; use @app.handler({handler_name!r})")
        response = await _call(handler, request, **path_params)
        await self._send(send, response)

    @staticmethod
    async def _send(send: Callable, response: Response) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": response.status,
                "headers": [(name.encode("latin-1"), value.encode("latin-1")) for name, value in response.headers],
            }
        )
        await send({"type": "http.response.body", "body": response.body})

    @staticmethod
    async def _run_lifespan(receive: Callable, send: Callable) -> None:
        """The minimal correct handling of ASGI's `lifespan` scope: no
        startup/shutdown hooks yet, just acknowledging each event so
        servers that send them (most do, by default) don't hang or fail."""
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return

    def __repr__(self) -> str:
        return f"App(router={self.router!r}, handlers={len(self._handlers)}, middleware={len(self._middleware)})"


async def _call(func: Callable[..., Union[_T, Awaitable[_T]]], *args: Any, **kwargs: Any) -> _T:
    """Calls `func`, awaiting its result if it returned one (an `async
    def` function) rather than the value itself (a plain `def`) — this is
    the one spot in this module allowed to stay generic over "any handler
    or middleware shape", so its `*args`/`**kwargs` are the one legitimate
    `Any` left: forwarding arbitrary arguments to an arbitrary `Callable[...,
    ...]` needs `ParamSpec` to type more precisely, which needs Python
    3.10+ (this package supports 3.9+) without an extra dependency."""
    result = func(*args, **kwargs)
    return await result if isawaitable(result) else result
