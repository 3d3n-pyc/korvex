"""Registering routes on an `App`: the `get`/`post`/... decorators, groups
(`AppGroup`), hosts, and the checks their options go through."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from korvex._requests import Request
from korvex._schema import Plan, _library, path_params
from korvex.router import RouteGroup, Router

if TYPE_CHECKING:
    from korvex._app import App


Handler = Callable[..., Any]
"""`(request, **path_params) -> Response`, sync or async. Path params come
converted: `int` for `{id:int}`, `uuid.UUID` for `{id:uuid}`, else `str`.
With a model return annotation, it can return the model instead."""

WebSocketHandler = Callable[..., Awaitable[None]]
"""`async (websocket, **path_params) -> None`."""


class _RouteDecorators:
    """`@get(path)` and friends, for any class with a `route()` decorator.

    - `name`: what `url_for()` knows the route by (the function's name by
      default).
    - `status`: the status of the response made from what the handler
      returns, a model or `None` (annotated `-> None`, for an empty
      response: `status=204`). A `Response` it returns keeps its own.
      OpenAPI documents it as the route's success.
    - `dependencies` (`[Depends(func), ...]`): run before the handler, for
      what they check rather than what they return.
    - `tags`, `deprecated`, `responses` and `include_in_schema`: how the
      route shows in OpenAPI. `responses` documents other answers, by
      status: `{404: "No such task"}`, `{409: Conflict}` (a model), or
      `{409: ("Name taken", Conflict)}`.
    - `max_body` (bytes): raises or lowers the built-in server's body size
      limit for that route; ASGI servers ignore it."""

    __slots__ = ()

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
        raise NotImplementedError

    def get(
        self,
        path: str,
        *,
        name: str | None = None,
        status: int = 200,
        dependencies: Sequence[Any] = (),
        tags: Sequence[str] = (),
        deprecated: bool = False,
        responses: Mapping[int | str, Any] | None = None,
        include_in_schema: bool = True,
        max_body: int | None = None,
    ) -> Callable[[Handler], Handler]:
        return self.route(
            path,
            method="GET",
            name=name,
            status=status,
            dependencies=dependencies,
            tags=tags,
            deprecated=deprecated,
            responses=responses,
            include_in_schema=include_in_schema,
            max_body=max_body,
        )

    def post(
        self,
        path: str,
        *,
        name: str | None = None,
        status: int = 200,
        dependencies: Sequence[Any] = (),
        tags: Sequence[str] = (),
        deprecated: bool = False,
        responses: Mapping[int | str, Any] | None = None,
        include_in_schema: bool = True,
        max_body: int | None = None,
    ) -> Callable[[Handler], Handler]:
        return self.route(
            path,
            method="POST",
            name=name,
            status=status,
            dependencies=dependencies,
            tags=tags,
            deprecated=deprecated,
            responses=responses,
            include_in_schema=include_in_schema,
            max_body=max_body,
        )

    def put(
        self,
        path: str,
        *,
        name: str | None = None,
        status: int = 200,
        dependencies: Sequence[Any] = (),
        tags: Sequence[str] = (),
        deprecated: bool = False,
        responses: Mapping[int | str, Any] | None = None,
        include_in_schema: bool = True,
        max_body: int | None = None,
    ) -> Callable[[Handler], Handler]:
        return self.route(
            path,
            method="PUT",
            name=name,
            status=status,
            dependencies=dependencies,
            tags=tags,
            deprecated=deprecated,
            responses=responses,
            include_in_schema=include_in_schema,
            max_body=max_body,
        )

    def patch(
        self,
        path: str,
        *,
        name: str | None = None,
        status: int = 200,
        dependencies: Sequence[Any] = (),
        tags: Sequence[str] = (),
        deprecated: bool = False,
        responses: Mapping[int | str, Any] | None = None,
        include_in_schema: bool = True,
        max_body: int | None = None,
    ) -> Callable[[Handler], Handler]:
        return self.route(
            path,
            method="PATCH",
            name=name,
            status=status,
            dependencies=dependencies,
            tags=tags,
            deprecated=deprecated,
            responses=responses,
            include_in_schema=include_in_schema,
            max_body=max_body,
        )

    def delete(
        self,
        path: str,
        *,
        name: str | None = None,
        status: int = 200,
        dependencies: Sequence[Any] = (),
        tags: Sequence[str] = (),
        deprecated: bool = False,
        responses: Mapping[int | str, Any] | None = None,
        include_in_schema: bool = True,
        max_body: int | None = None,
    ) -> Callable[[Handler], Handler]:
        return self.route(
            path,
            method="DELETE",
            name=name,
            status=status,
            dependencies=dependencies,
            tags=tags,
            deprecated=deprecated,
            responses=responses,
            include_in_schema=include_in_schema,
            max_body=max_body,
        )

    def head(
        self,
        path: str,
        *,
        name: str | None = None,
        status: int = 200,
        dependencies: Sequence[Any] = (),
        tags: Sequence[str] = (),
        deprecated: bool = False,
        responses: Mapping[int | str, Any] | None = None,
        include_in_schema: bool = True,
        max_body: int | None = None,
    ) -> Callable[[Handler], Handler]:
        return self.route(
            path,
            method="HEAD",
            name=name,
            status=status,
            dependencies=dependencies,
            tags=tags,
            deprecated=deprecated,
            responses=responses,
            include_in_schema=include_in_schema,
            max_body=max_body,
        )

    def options(
        self,
        path: str,
        *,
        name: str | None = None,
        status: int = 200,
        dependencies: Sequence[Any] = (),
        tags: Sequence[str] = (),
        deprecated: bool = False,
        responses: Mapping[int | str, Any] | None = None,
        include_in_schema: bool = True,
        max_body: int | None = None,
    ) -> Callable[[Handler], Handler]:
        return self.route(
            path,
            method="OPTIONS",
            name=name,
            status=status,
            dependencies=dependencies,
            tags=tags,
            deprecated=deprecated,
            responses=responses,
            include_in_schema=include_in_schema,
            max_body=max_body,
        )


class _Route:
    """What the App keeps about one route, under its handler name in the
    router: the function, what its annotations ask for (worked out on
    first use for a function attached with `@app.handler`), the name
    `url_for()` knows it by, how OpenAPI shows it, and which host
    parameters the function takes (None: all of them, or not on a host)."""

    __slots__ = ("func", "plan", "name", "explicit", "docs", "host_args")

    def __init__(
        self,
        func: Callable[..., Any],
        plan: Plan | None = None,
        *,
        name: str | None = None,
        explicit: bool = False,
        docs: _Docs | None = None,
        host_args: frozenset[str] | None = None,
    ) -> None:
        self.func = func
        self.plan = plan
        self.name = name  # None: url_for() can't link to it
        self.explicit = explicit  # given with name=, rather than the function's
        self.docs = docs
        self.host_args = host_args


class _Docs:
    """How a route, or a group's routes, show in OpenAPI."""

    __slots__ = ("tags", "deprecated", "responses", "include")

    def __init__(
        self,
        tags: Sequence[str] = (),
        deprecated: bool = False,
        responses: Mapping[int | str, Any] | None = None,
        include: bool = True,
    ) -> None:
        if isinstance(tags, str):
            raise TypeError(f"tags takes a list of names, not a string: tags=[{tags!r}]")
        self.tags = tuple(tags)
        self.deprecated = deprecated
        self.responses = _checked_responses(responses or {})
        self.include = include

    def merge(self, other: _Docs) -> _Docs:
        """These (a group's) with `other`'s (a route's, or a nested group's):
        tags added, responses replaced by status, deprecated or left out
        if either is."""
        merged = _Docs()
        merged.tags = (*self.tags, *(tag for tag in other.tags if tag not in self.tags))
        merged.deprecated = self.deprecated or other.deprecated
        merged.responses = {**self.responses, **other.responses}
        merged.include = self.include and other.include
        return merged


class AppGroup(_RouteDecorators):
    """Registers handlers under a shared prefix and dependencies, and
    OpenAPI's `tags`, `deprecated`, `responses` and `include_in_schema`
    for all of them.

        api = app.group("/api", dependencies=[Depends(signed_in)], tags=["api"])

        @api.get("/users")           # GET /api/users, after signed_in
        def list_users(request): ...
    """

    __slots__ = ("_app", "_routes", "_dependencies", "_host", "_docs")

    def __init__(
        self,
        app: App,
        routes: RouteGroup,
        dependencies: Sequence[Any] = (),
        host: _Host | None = None,
        docs: _Docs | None = None,
    ) -> None:
        self._app = app
        self._routes = routes
        self._dependencies = tuple(dependencies)
        self._host = host  # for `app.host()` groups: the host their routes answer on
        self._docs = docs if docs is not None else _Docs()

    def _plan_pattern(self, full_path: str) -> str:
        """What a handler's plan is read against: the path, after the host
        pattern for a host's routes, so `{tenant}` in the host is a
        parameter like those of the path."""
        if self._host is None:
            return full_path
        clash = set(self._host.params) & set(path_params(full_path))
        if clash:
            raise ValueError(f"{full_path!r}: {sorted(clash)} is both in the host {self._host.pattern!r} and the path")
        return self._host.pattern + full_path

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
        """Decorator: registers the function as the handler for `path`, under
        this group's prefix. Its own `dependencies` run after the group's;
        its OpenAPI options add to the group's."""
        _check_name_type(name)
        _check_status(status)
        docs = self._docs.merge(_Docs(tags, deprecated, responses, include_in_schema))

        def register(func: Handler) -> Handler:
            key = _route_name(func, self._app._routes)
            full_path = self._routes.full_path(path)
            # Bad annotations fail here, at import.
            plan = self._app._plan(func, self._plan_pattern(full_path), (*self._dependencies, *dependencies))
            plan.status = status
            if plan.response is not None and not _has_body(status):
                raise TypeError(f"{plan.func.__qualname__}: a {status} response has no body, so no return model")
            self._app._check_name(name, False, self._host, full_path)
            self._routes.route(path, key, method=method)
            route = _Route(
                func, plan, name=name or getattr(func, "__name__", None), explicit=name is not None, docs=docs
            )
            self._add(False, key, route, full_path)
            if max_body is not None:
                self._app._body_limits.append((method, full_path, max_body))
            return func

        return register

    def websocket(
        self, path: str, *, name: str | None = None, dependencies: Sequence[Any] = ()
    ) -> Callable[[WebSocketHandler], WebSocketHandler]:
        """Decorator: registers the function as the WebSocket handler for
        `path`, under this group's prefix. Its parameters and dependencies
        work as for HTTP handlers (no body), the group's dependencies run
        too, and one raising `HTTPException` refuses the connection (403).
        Before hooks run on the handshake; after hooks don't. `name` is
        what `url_for()` knows it by (the function's name by default)."""
        _check_name_type(name)

        def register(func: WebSocketHandler) -> WebSocketHandler:
            key = _route_name(func, self._app._ws_routes)
            full_path = self._routes.full_path(path)
            plan = self._app._plan(
                func, self._plan_pattern(full_path), (*self._dependencies, *dependencies), websocket=True
            )
            self._app._check_name(name, True, self._host, full_path)
            (self._host.ws_router if self._host else self._app._ws_router).route(full_path, key)
            route = _Route(func, plan, name=name or getattr(func, "__name__", None), explicit=name is not None)
            self._add(True, key, route, full_path)
            return func

        return register

    def _add(self, websocket: bool, key: str, route: _Route, path: str) -> None:
        """Hands a route just registered in the router to the App."""
        if self._host is not None:
            route.host_args = _takes(route.func, self._host.params)
        self._app._add_route(websocket, key, route, self._host, path)

    def group(
        self,
        prefix: str = "",
        *,
        dependencies: Sequence[Any] = (),
        tags: Sequence[str] = (),
        deprecated: bool = False,
        responses: Mapping[int | str, Any] | None = None,
        include_in_schema: bool = True,
    ) -> AppGroup:
        """Returns a nested group. Its `dependencies` run after this
        group's; its OpenAPI options add to this group's."""
        docs = self._docs.merge(_Docs(tags, deprecated, responses, include_in_schema))
        return AppGroup(self._app, self._routes.group(prefix), (*self._dependencies, *dependencies), self._host, docs)

    def __repr__(self) -> str:
        host = f", host={self._host.pattern!r}" if self._host else ""
        return f"AppGroup(prefix={self._routes.prefix!r}{host}, dependencies={list(self._dependencies)!r})"


class _Host:
    """The routes of one host pattern, `api.example.com` or
    `{tenant}.example.com`: a router for HTTP and one for WebSockets."""

    __slots__ = ("pattern", "params", "_labels", "router", "ws_router")

    def __init__(self, pattern: str, constraints: Iterable[tuple[str, str]]) -> None:
        self.pattern = pattern.lower().rstrip(".")
        self._labels: list[tuple[bool, str]] = []  # (is a parameter, name or literal)
        for label in self.pattern.split("."):
            if label.startswith("{") and label.endswith("}") and label[1:-1].isidentifier():
                self._labels.append((True, label[1:-1]))
            elif label and label.isascii() and all(c.isalnum() or c in "-_" for c in label):
                self._labels.append((False, label))
            else:
                raise ValueError(f"not a host pattern: {pattern!r} (labels are names, or {{param}} for any one)")
        self.params = [name for is_param, name in self._labels if is_param]
        self.router, self.ws_router = Router(), Router()
        for name, regex in constraints:
            self.router.constraint(name, regex)
            self.ws_router.constraint(name, regex)

    def match(self, labels: list[str]) -> dict[str, str] | None:
        """The host's parameters if `labels` (`["acme", "example", "com"]`)
        fit the pattern, else None. A parameter takes one whole label."""
        if len(labels) != len(self._labels):
            return None
        params = {}
        for (is_param, expected), label in zip(self._labels, labels):
            if is_param:
                if not label:  # `.example.com`: no tenant at all, not an empty one
                    return None
                params[expected] = label
            elif label != expected:
                return None
        return params


def _host_labels(request: Request) -> list[str]:
    """The labels of the host the request is for, from its `Host` header,
    without the port: `"Acme.example.com:8000"` -> `["acme", "example", "com"]`."""
    host = (request.headers.get("host") or "").lower()
    if host.startswith("["):  # an IPv6 address
        return [host[: host.find("]") + 1]]
    return host.rsplit(":", 1)[0].rstrip(".").split(".")


def _takes(func: Callable[..., Any], names: Iterable[str]) -> frozenset[str] | None:
    """Which of `names` `func` takes as keyword arguments; None if it takes
    any (`**kwargs`)."""
    parameters = inspect.signature(func).parameters
    if any(p.kind is p.VAR_KEYWORD for p in parameters.values()):
        return None
    return frozenset(name for name in names if name in parameters)


def _check_status(status: Any) -> None:
    if not isinstance(status, int) or isinstance(status, bool) or not 100 <= status <= 599:
        raise ValueError(f"status must be an HTTP status, 100 to 599, not {status!r}")


def _has_body(status: int) -> bool:
    """Whether a response with that status can have a body (RFC 9110)."""
    return status >= 200 and status not in (204, 205, 304)


def _checked_responses(responses: Mapping[Any, Any]) -> dict[str, Any]:
    """`responses=`, checked when the route is registered, keyed by the
    status as OpenAPI writes it (`"404"`, `"4XX"`, `"default"`)."""
    checked: dict[str, Any] = {}
    for status, value in responses.items():
        if isinstance(status, bool) or not (
            (isinstance(status, int) and 100 <= status <= 599)
            or (
                isinstance(status, str)
                and (status == "default" or (len(status) == 3 and status[0] in "12345" and status[1:] == "XX"))
            )
        ):
            raise ValueError(f'responses: {status!r} isn\'t a status (404), a range ("4XX") or "default"')
        description, model = (value, None) if isinstance(value, str) else (None, value)
        if isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], str):
            description, model = value
        if model is not None and _library(model) is None:
            raise TypeError(
                f"responses[{status!r}]: expected a description, a msgspec or pydantic model, or (description, model), not {value!r}"
            )
        checked[str(status)] = (description, model)
    return checked


def _check_name_type(name: Any) -> None:
    if name is not None and (not isinstance(name, str) or not name):
        raise ValueError(f"a route's name must be a non-empty string, not {name!r}")


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
