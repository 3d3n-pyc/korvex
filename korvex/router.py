"""The standalone router: maps a path and method to a handler name.

Guide: https://3d3n-pyc.github.io/korvex/router/
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache

from korvex._core import MethodNotAllowed
from korvex._core import Router as _CoreRouter

__all__ = ["MethodNotAllowed", "RouteGroup", "Router"]


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


CACHE_SIZE = 1024


class Router(_CoreRouter, _Shortcuts):
    """Maps a path and method to a handler name; never calls your code.

    `match` caches the last `CACHE_SIZE` lookups; results are read-only.

    Guide: https://3d3n-pyc.github.io/korvex/router/
    """

    def __init__(self) -> None:
        self._cache = lru_cache(maxsize=CACHE_SIZE)(super().match)
        self.match = self._cache
        self.version = 0
        """Goes up each time a route is added or removed."""

    def route(self, path: str, handler: str, *, method: str = "GET", middleware: Sequence[str] | None = None) -> None:
        super().route(path, handler, method=method, middleware=middleware)
        self._cache.cache_clear()
        self.version += 1

    def remove(self, path: str, method: str = "GET") -> bool:
        removed = super().remove(path, method)
        self._cache.cache_clear()
        self.version += 1
        return removed

    def group(self, prefix: str = "", *, middleware: Sequence[str] | None = None) -> RouteGroup:
        """Returns a `RouteGroup` that registers routes under `prefix`."""
        return RouteGroup(self, prefix, middleware=middleware)


class RouteGroup(_Shortcuts):
    """Registers routes on a router under a prefix and middleware, from `Router.group()`.

    Guide: https://3d3n-pyc.github.io/korvex/router/
    """

    __slots__ = ("router", "prefix", "middleware")

    def __init__(self, router: Router, prefix: str = "", *, middleware: Sequence[str] | None = None) -> None:
        self.router = router
        self.prefix = "/" + prefix.strip("/") if prefix.strip("/") else ""
        self.middleware = list(middleware or [])

    def route(self, path: str, handler: str, *, method: str = "GET", middleware: Sequence[str] | None = None) -> None:
        """Registers a route. Its own `middleware` runs after the group's."""
        self.router.route(
            self.full_path(path),
            handler,
            method=method,
            middleware=[*self.middleware, *(middleware or [])],
        )

    def full_path(self, path: str) -> str:
        """`path` with this group's prefix: `/api` + `users` -> `/api/users`."""
        return f"{self.prefix}/{path.lstrip('/')}"

    def group(self, prefix: str = "", *, middleware: Sequence[str] | None = None) -> RouteGroup:
        """Returns a nested group. Its `middleware` runs after this group's."""
        return RouteGroup(
            self.router,
            f"{self.prefix}/{prefix.strip('/')}",
            middleware=[*self.middleware, *(middleware or [])],
        )

    def __repr__(self) -> str:
        return f"RouteGroup(prefix={self.prefix!r}, middleware={self.middleware!r})"
