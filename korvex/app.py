"""`App`: an ASGI application built on `Router`.

Run it with any ASGI server (uvicorn, hypercorn, granian...). For each
request, `App` matches the path, runs the route's middleware, calls the
handler and sends its `Response`.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from inspect import isawaitable
from typing import Any, Optional, Union
from urllib.parse import parse_qs

from korvex._core import MethodNotAllowed
from korvex.response import Headers, JSONValue, Response
from korvex.router import Router

# `Union`/`Optional` rather than `|`: these lines run at import time, and
# `|` between types needs Python 3.10+.
Handler = Callable[..., Union[Response, Awaitable[Response]]]
"""`(request, **path_params) -> Response`, sync or async. Path params come
converted: `int` for `{id:int}`, `uuid.UUID` for `{id:uuid}`, else `str`."""

Middleware = Callable[["Request"], Union[Optional[Response], Awaitable[Optional[Response]]]]
"""`(request) -> Response | None`, sync or async. Returning a `Response`
sends it right away; returning `None` moves on to the next step."""


class Request:
    """An incoming HTTP request."""

    __slots__ = ("method", "path", "headers", "query_params", "path_params", "_receive")

    def __init__(self, scope: dict, receive: Callable[[], Awaitable[dict]], path_params: Mapping[str, Any]) -> None:
        self.method: str = scope["method"]
        self.path: str = scope["path"]
        self.headers = Headers((k.decode("latin-1"), v.decode("latin-1")) for k, v in scope["headers"])
        self.query_params: dict[str, list[str]] = parse_qs(scope.get("query_string", b"").decode("latin-1"))
        self.path_params = path_params
        self._receive = receive

    async def body(self) -> bytes:
        """Reads the whole request body."""
        chunks = []
        more_body = True
        while more_body:
            message = await self._receive()
            chunks.append(message.get("body", b""))
            more_body = message.get("more_body", False)
        return b"".join(chunks)

    async def json(self) -> JSONValue:
        """Reads the body and parses it as JSON."""
        return json.loads(await self.body())

    def __repr__(self) -> str:
        return f"Request({self.method} {self.path!r})"


class App:
    """An ASGI application.

        app = App()

        @app.get("/users/{id:int}")
        def get_user(request, id):
            return Response.json({"id": id})

        # uvicorn mymodule:app
    """

    def __init__(self, router: Router | None = None) -> None:
        self.router = router if router is not None else Router()
        self._handlers: dict[str, Handler] = {}
        self._middleware: dict[str, Middleware] = {}

    def route(
        self, path: str, *, method: str = "GET", middleware: Sequence[str] | None = None
    ) -> Callable[[Handler], Handler]:
        """Decorator: registers the function as the handler for `path`."""

        def register(func: Handler) -> Handler:
            name = f"{func.__module__}.{func.__qualname__}"
            self.router.route(path, name, method=method, middleware=middleware)
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

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] == "lifespan":
            await _acknowledge_lifespan(receive, send)
            return
        if scope["type"] != "http":
            raise NotImplementedError(f"unsupported ASGI scope type: {scope['type']!r}")

        response = await self._respond(scope, receive)
        await send(
            {
                "type": "http.response.start",
                "status": response.status,
                "headers": [(n.encode("latin-1"), v.encode("latin-1")) for n, v in response.headers],
            }
        )
        await send({"type": "http.response.body", "body": response.body})

    async def _respond(self, scope: dict, receive: Callable) -> Response:
        try:
            found = self.router.match(scope["path"], method=scope["method"])
        except MethodNotAllowed as exc:
            return Response.text("Method Not Allowed", status=405, headers=[("Allow", ", ".join(exc.allowed))])
        if found is None:
            return Response.text("Not Found", status=404)

        handler_name, path_params, middleware_names = found
        request = Request(scope, receive, path_params)

        for name in middleware_names:
            if name not in self._middleware:
                raise LookupError(f"no middleware named {name!r}; register one with @app.middleware({name!r})")
            early_response = await _call(self._middleware[name], request)
            if early_response is not None:
                return early_response

        if handler_name not in self._handlers:
            raise LookupError(f"no handler named {handler_name!r}; register one with @app.handler({handler_name!r})")
        return await _call(self._handlers[handler_name], request, **path_params)

    def __repr__(self) -> str:
        return f"App(router={self.router!r})"


async def _call(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Calls a sync or async function and returns its result."""
    result = func(*args, **kwargs)
    return await result if isawaitable(result) else result


async def _acknowledge_lifespan(receive: Callable, send: Callable) -> None:
    """Answers the server's startup/shutdown events (there are no hooks)."""
    while True:
        message = await receive()
        if message["type"] == "lifespan.startup":
            await send({"type": "lifespan.startup.complete"})
        elif message["type"] == "lifespan.shutdown":
            await send({"type": "lifespan.shutdown.complete"})
            return
