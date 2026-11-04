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
import signal
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from email.message import Message
from http import HTTPStatus
from inspect import isawaitable
from pathlib import Path
from typing import Any, Optional, Union, overload
from urllib.parse import parse_qs, unquote

from korvex import _core
from korvex._core import MethodNotAllowed
from korvex.response import Headers, JSONValue, Response, StreamingResponse
from korvex.router import RouteGroup, Router

# `Union`/`Optional` rather than `|`: these lines run at import time, and
# `|` between types needs Python 3.10+.
Handler = Callable[..., Union[Response, Awaitable[Response]]]
"""`(request, **path_params) -> Response`, sync or async. Path params come
converted: `int` for `{id:int}`, `uuid.UUID` for `{id:uuid}`, else `str`."""

Middleware = Callable[["Request"], Union[Optional[Response], Awaitable[Optional[Response]]]]
"""`(request) -> Response | None`, sync or async. Returning a `Response`
sends it right away; returning `None` moves on to the next step."""

Hook = Callable[[], Union[None, Awaitable[None]]]
"""`() -> None`, sync or async, run when the server starts or stops."""

ExceptionHandler = Callable[["Request", Exception], Union[Response, Awaitable[Response]]]
"""`(request, exception) -> Response`, sync or async."""

logger = logging.getLogger("korvex")


class HTTPException(Exception):
    """Raise it in a handler or middleware to answer with an HTTP error:
    `raise HTTPException(404, "user not found")`. Without a `detail`, the
    standard reason (`Not Found`) is used."""

    def __init__(self, status: int, detail: str | None = None, *, headers: Iterable[tuple[str, str]] = ()) -> None:
        self.status = status
        self.detail = detail if detail is not None else HTTPStatus(status).phrase
        self.headers = list(headers)
        super().__init__(status, self.detail)


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

    __slots__ = ("method", "path", "headers", "query_params", "path_params", "_receive", "_cookies")

    def __init__(self, scope: dict, receive: Callable[[], Awaitable[dict]], path_params: Mapping[str, Any]) -> None:
        self.method: str = scope["method"]
        self.path: str = scope["path"]
        self.headers = Headers((k.decode("latin-1"), v.decode("latin-1")) for k, v in scope["headers"])
        self.query_params: dict[str, list[str]] = parse_qs(scope.get("query_string", b"").decode("latin-1"))
        self.path_params = path_params
        self._receive = receive
        self._cookies: dict[str, str] | None = None

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
        """Reads the whole request body."""
        chunks = []
        more_body = True
        while more_body:
            message = await self._receive()
            chunks.append(message.get("body", b""))
            more_body = message.get("more_body", False)
        return b"".join(chunks)

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


class _RouteDecorators:
    """`@get(path)` and friends, for any class with a `route()` decorator."""

    __slots__ = ()

    def route(
        self, path: str, *, method: str = "GET", middleware: Sequence[str] | None = None
    ) -> Callable[[Handler], Handler]:
        raise NotImplementedError

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


class AppGroup(_RouteDecorators):
    """Registers handlers under a shared prefix and middleware.

        api = app.group("/api", middleware=["auth"])

        @api.get("/users")           # GET /api/users, middleware ("auth",)
        def list_users(request): ...
    """

    __slots__ = ("_app", "_routes")

    def __init__(self, app: App, routes: RouteGroup) -> None:
        self._app = app
        self._routes = routes

    def route(
        self, path: str, *, method: str = "GET", middleware: Sequence[str] | None = None
    ) -> Callable[[Handler], Handler]:
        """Decorator: registers the function as the handler for `path`, under
        this group's prefix. Its own `middleware` runs after the group's."""

        def register(func: Handler) -> Handler:
            name = f"{func.__module__}.{func.__qualname__}"
            self._routes.route(path, name, method=method, middleware=middleware)
            self._app._handlers[name] = func
            return func

        return register

    def group(self, prefix: str = "", *, middleware: Sequence[str] | None = None) -> AppGroup:
        """Returns a nested group. Its `middleware` runs after this group's."""
        return AppGroup(self._app, self._routes.group(prefix, middleware=middleware))

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
        self._root = AppGroup(self, self.router.group())
        self._handlers: dict[str, Handler] = {}
        self._middleware: dict[str, Middleware] = {}
        self._on_startup: list[Hook] = []
        self._on_shutdown: list[Hook] = []
        self._exception_handlers: dict[int | type[Exception], ExceptionHandler] = {}

    def route(
        self, path: str, *, method: str = "GET", middleware: Sequence[str] | None = None
    ) -> Callable[[Handler], Handler]:
        """Decorator: registers the function as the handler for `path`."""
        return self._root.route(path, method=method, middleware=middleware)

    def group(self, prefix: str = "", *, middleware: Sequence[str] | None = None) -> AppGroup:
        """Returns an `AppGroup` that registers handlers under `prefix`."""
        return self._root.group(prefix, middleware=middleware)

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
        if scope["type"] != "http":
            raise NotImplementedError(f"unsupported ASGI scope type: {scope['type']!r}")

        response = await self._respond(scope, receive)
        head = scope["method"] == "HEAD"
        await send({"type": "http.response.start", "status": response.status, "headers": _raw_headers(response, head)})
        if head:  # same headers as GET, no body
            await send({"type": "http.response.body", "body": b""})
        elif isinstance(response, StreamingResponse):
            async for chunk in response:
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b""})
        else:
            await send({"type": "http.response.body", "body": response.body})

    async def _respond(self, scope: dict, receive: Callable) -> Response:
        request = None
        try:
            try:
                found = self.router.match(scope["path"], method=scope["method"])
            except MethodNotAllowed as exc:
                if scope["method"] == "HEAD" and "GET" in exc.allowed:
                    found = self.router.match(scope["path"], method="GET")  # HEAD is GET without the body
                else:
                    raise HTTPException(405, headers=[("Allow", ", ".join(exc.allowed))]) from None
            if found is None:
                raise HTTPException(404)

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
        except Exception as exc:
            # Unknown paths fail before the request is built: build it now.
            return await self._handle_error(request or Request(scope, receive, {}), exc)

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

    def run(self, host: str = "127.0.0.1", port: int = 8000) -> None:
        """Serves the app with Korvex's built-in server until Ctrl+C or
        SIGTERM: startup hooks first, shutdown hooks at the end. Linux and
        macOS only; on Windows, use an ASGI server such as uvicorn."""
        if not hasattr(_core, "Server"):
            raise NotImplementedError("the built-in server needs Linux or macOS; use uvicorn on Windows")
        asyncio.run(self._serve(host, port))

    async def _serve(self, host: str, port: int) -> None:
        for hook in self._on_startup:  # if one raises, the server doesn't start
            await _call(hook)
        loop = asyncio.get_running_loop()
        server = _core.Server(host, port)
        stopping = asyncio.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stopping.set)
        tasks: set[asyncio.Task] = set()

        def take_requests() -> None:
            for request in server.take():
                task = _start_task(loop, self._serve_one(*request))
                if not task.done():  # the loop only keeps weak references to tasks
                    tasks.add(task)
                    task.add_done_callback(tasks.discard)

        loop.add_reader(server.fileno(), take_requests)
        try:
            await stopping.wait()
        finally:
            loop.remove_reader(server.fileno())
            server.close()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.remove_signal_handler(sig)
            for hook in reversed(self._on_shutdown):
                await _call(hook)

    async def _serve_one(
        self, method: str, path: str, query_string: bytes, headers: list, body: bytes, responder: Any
    ) -> None:
        """Runs one request taken from the built-in server, through the same
        dispatch as under ASGI, and hands the response back to Rust."""
        scope = {"type": "http", "method": method, "path": unquote(path), "query_string": query_string, "headers": headers}

        async def receive() -> dict:
            return {"type": "http.request", "body": body, "more_body": False}

        response = await self._respond(scope, receive)
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
        else:
            responder.respond(response.status, raw_headers, response.body)

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


async def _call(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Calls a sync or async function and returns its result."""
    result = func(*args, **kwargs)
    return await result if isawaitable(result) else result


def _add_hook(hooks: list[Hook], func: Hook | None) -> Hook | Callable[[Hook], Hook]:
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
