"""Prefix- and middleware-scoped route registration, layered over `Router`.

Pure Python: nothing here ever runs on the matching hot path
(`Router.match_route`) — every `RouteGroup` method resolves a full path
and middleware list, then calls straight through to `Router.add_route`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from korvex._core import Router


class RouteGroup:
    """A view over a `Router` that prefixes paths and prepends middleware.

    Not usually constructed directly — use `router.group(prefix, ...)`, or
    `some_group.group(prefix, ...)` for a nested sub-group. Every method
    returns `self` (or a new nested `RouteGroup`), so registration reads
    top to bottom::

        api = router.group("/api/v1", middleware=["auth"])
        api.get("/users/{id:int}", "get_user")
        api.post("/users", "create_user", middleware=["rate_limit"])

        admin = api.group("/admin", middleware=["require_admin"])
        admin.get("/stats", "get_stats")
        # registers, in order: auth -> require_admin, on GET /api/v1/admin/stats
    """

    __slots__ = ("_router", "_prefix", "_middleware")

    def __init__(self, router: Router, prefix: str = "", *, middleware: Sequence[str] | None = None) -> None:
        self._router = router
        stripped_prefix = prefix.strip("/")
        self._prefix = f"/{stripped_prefix}" if stripped_prefix else ""
        self._middleware = list(middleware) if middleware else []

    def __repr__(self) -> str:
        return f"RouteGroup(prefix={self._prefix!r}, middleware={self._middleware!r})"

    def group(self, prefix: str, *, middleware: Sequence[str] | None = None) -> RouteGroup:
        """Returns a nested group under `prefix`. Inherits this group's
        prefix and middleware; this group's middleware runs first."""
        return RouteGroup(
            self._router,
            f"{self._prefix}/{prefix.strip('/')}",
            middleware=[*self._middleware, *(middleware or [])],
        )

    def route(
        self, path: str, handler_name: str, *, method: str = "GET", middleware: Sequence[str] | None = None
    ) -> RouteGroup:
        """Registers a route under this group's prefix and method. Route-level
        `middleware`, if given, is appended after the group's own."""
        full_path = f"{self._prefix}/{path.lstrip('/')}"
        full_middleware = [*self._middleware, *(middleware or [])]
        self._router.add_route(full_path, handler_name, method=method, middleware=full_middleware or None)
        return self

    def get(self, path: str, handler_name: str, *, middleware: Sequence[str] | None = None) -> RouteGroup:
        return self.route(path, handler_name, method="GET", middleware=middleware)

    def post(self, path: str, handler_name: str, *, middleware: Sequence[str] | None = None) -> RouteGroup:
        return self.route(path, handler_name, method="POST", middleware=middleware)

    def put(self, path: str, handler_name: str, *, middleware: Sequence[str] | None = None) -> RouteGroup:
        return self.route(path, handler_name, method="PUT", middleware=middleware)

    def patch(self, path: str, handler_name: str, *, middleware: Sequence[str] | None = None) -> RouteGroup:
        return self.route(path, handler_name, method="PATCH", middleware=middleware)

    def delete(self, path: str, handler_name: str, *, middleware: Sequence[str] | None = None) -> RouteGroup:
        return self.route(path, handler_name, method="DELETE", middleware=middleware)

    def head(self, path: str, handler_name: str, *, middleware: Sequence[str] | None = None) -> RouteGroup:
        return self.route(path, handler_name, method="HEAD", middleware=middleware)

    def options(self, path: str, handler_name: str, *, middleware: Sequence[str] | None = None) -> RouteGroup:
        return self.route(path, handler_name, method="OPTIONS", middleware=middleware)
