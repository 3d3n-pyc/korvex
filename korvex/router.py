"""`Router` and `RouteGroup`: registering routes.

Matching happens in Rust (`korvex._core.Router`). This module only adds
the `get`/`post`/... shortcuts and route groups on top.
"""

from __future__ import annotations

from collections.abc import Sequence

from korvex._core import Router as _CoreRouter


class _Shortcuts:
    """`get(path, handler)` and friends, for any class with a `route()`."""

    __slots__ = ()

    def route(self, path: str, handler: str, *, method: str = "GET", middleware: Sequence[str] | None = None) -> None:
        raise NotImplementedError

    def get(self, path: str, handler: str, *, middleware: Sequence[str] | None = None) -> None:
        self.route(path, handler, method="GET", middleware=middleware)

    def post(self, path: str, handler: str, *, middleware: Sequence[str] | None = None) -> None:
        self.route(path, handler, method="POST", middleware=middleware)

    def put(self, path: str, handler: str, *, middleware: Sequence[str] | None = None) -> None:
        self.route(path, handler, method="PUT", middleware=middleware)

    def patch(self, path: str, handler: str, *, middleware: Sequence[str] | None = None) -> None:
        self.route(path, handler, method="PATCH", middleware=middleware)

    def delete(self, path: str, handler: str, *, middleware: Sequence[str] | None = None) -> None:
        self.route(path, handler, method="DELETE", middleware=middleware)

    def head(self, path: str, handler: str, *, middleware: Sequence[str] | None = None) -> None:
        self.route(path, handler, method="HEAD", middleware=middleware)

    def options(self, path: str, handler: str, *, middleware: Sequence[str] | None = None) -> None:
        self.route(path, handler, method="OPTIONS", middleware=middleware)


class Router(_CoreRouter, _Shortcuts):
    """Maps a path and method to a handler name.

    `route`, `match`, `remove` and `routes` come from the Rust base class
    (it is listed first, so its `route` wins over the placeholder in
    `_Shortcuts`).

        router = Router()
        router.get("/users/{id:int}", "get_user")
        router.match("/users/42")  # ("get_user", {"id": "42"}, [])
    """

    def group(self, prefix: str = "", *, middleware: Sequence[str] | None = None) -> RouteGroup:
        """Returns a `RouteGroup` that registers routes under `prefix`."""
        return RouteGroup(self, prefix, middleware=middleware)


class RouteGroup(_Shortcuts):
    """Registers routes on a router under a shared prefix and middleware.

        api = router.group("/api", middleware=["auth"])
        api.get("/users", "list_users")       # GET /api/users, ["auth"]

        admin = api.group("/admin", middleware=["admin"])
        admin.get("/stats", "stats")          # GET /api/admin/stats, ["auth", "admin"]
    """

    __slots__ = ("router", "prefix", "middleware")

    def __init__(self, router: Router, prefix: str = "", *, middleware: Sequence[str] | None = None) -> None:
        self.router = router
        self.prefix = "/" + prefix.strip("/") if prefix.strip("/") else ""
        self.middleware = list(middleware or [])

    def route(self, path: str, handler: str, *, method: str = "GET", middleware: Sequence[str] | None = None) -> None:
        """Registers a route. Its own `middleware` runs after the group's."""
        self.router.route(
            f"{self.prefix}/{path.lstrip('/')}",
            handler,
            method=method,
            middleware=[*self.middleware, *(middleware or [])],
        )

    def group(self, prefix: str = "", *, middleware: Sequence[str] | None = None) -> RouteGroup:
        """Returns a nested group. Its `middleware` runs after this group's."""
        return RouteGroup(
            self.router,
            f"{self.prefix}/{prefix.strip('/')}",
            middleware=[*self.middleware, *(middleware or [])],
        )

    def __repr__(self) -> str:
        return f"RouteGroup(prefix={self.prefix!r}, middleware={self.middleware!r})"
