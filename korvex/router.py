"""The public `Router` — a thin, pure-Python subclass of the compiled
`korvex._core.Router` that adds `.group()` for route-group registration.

Subclassing (rather than the alternative of monkeypatching a method onto
the compiled class after the fact) keeps this discoverable: `class
Router(_CoreRouter)` reads as exactly what it is, shows up correctly in
`Router.__mro__` and `help(Router)`, and type checkers resolve it like any
other class — no special-casing needed. `add_route`, `get`/`post`/...,
`match_route`, `remove_route`, and `routes` are all inherited unchanged
from the compiled base; `group` is the only method actually defined here.
"""

from __future__ import annotations

from collections.abc import Sequence

from korvex._core import Router as _CoreRouter
from korvex.group import RouteGroup


class Router(_CoreRouter):
    def group(self, prefix: str = "", *, middleware: Sequence[str] | None = None) -> RouteGroup:
        """Returns a `RouteGroup` rooted at `prefix` on this router, for
        registering many routes under a shared path prefix and/or
        middleware. See `RouteGroup` for the fluent API this returns."""
        return RouteGroup(self, prefix, middleware=middleware)
