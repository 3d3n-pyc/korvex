"""Where a parameter comes from when its annotation alone doesn't say:
`Header()`, `Cookie()`, `Query()`, `Form()`, and `Depends()` for values
computed by another function.

Give them as the parameter's default, or inside `Annotated`:

    async def tasks(request, user: User = Depends(current_user), x_client: str = Header()): ...

    CurrentUser = Annotated[User, Depends(current_user)]
    async def tasks(request, user: CurrentUser, theme: Annotated[str, Cookie()] = "light"): ...

They return `Any`, so `user: User = Depends(current_user)` type-checks.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class _Required:
    __slots__ = ()

    def __repr__(self) -> str:
        return "REQUIRED"


REQUIRED: Any = _Required()
"""The default of a marker given no default: the parameter is required."""


class Source:
    """Where a parameter's value is read from in the request."""

    __slots__ = ("kind", "default", "alias")

    def __init__(self, kind: str, default: Any, alias: str | None) -> None:
        self.kind = kind
        self.default = default
        self.alias = alias

    def __repr__(self) -> str:
        args = [] if self.default is REQUIRED else [repr(self.default)]
        if self.alias is not None:
            args.append(f"alias={self.alias!r}")
        return f"{self.kind.capitalize()}({', '.join(args)})"


class Dependency:
    """A parameter whose value is what `func` returns (or yields)."""

    __slots__ = ("func", "cache")

    def __init__(self, func: Callable[..., Any], cache: bool) -> None:
        if not callable(func):
            raise TypeError(f"Depends() takes a function, not {func!r}")
        self.func = func
        self.cache = cache

    def __repr__(self) -> str:
        name = getattr(self.func, "__qualname__", repr(self.func))
        return f"Depends({name})" if self.cache else f"Depends({name}, cache=False)"


def Query(default: Any = REQUIRED, *, alias: str | None = None) -> Any:
    """From the query string; `alias` gives a name that isn't a Python identifier.

    Guide: https://3d3n-pyc.github.io/korvex/validation/#headers-cookies-and-forms
    """
    return Source("query", default, alias)


def Header(default: Any = REQUIRED, *, alias: str | None = None) -> Any:
    """From a request header (`x_client` reads `X-Client`); `alias` gives the exact name.

    A `list[str]` gets every value of a repeated header.
    Guide: https://3d3n-pyc.github.io/korvex/validation/#headers-cookies-and-forms
    """
    return Source("header", default, alias)


def Cookie(default: Any = REQUIRED, *, alias: str | None = None) -> Any:
    """From a cookie of that name (or `alias`).

    Guide: https://3d3n-pyc.github.io/korvex/validation/#headers-cookies-and-forms
    """
    return Source("cookie", default, alias)


def Form(default: Any = REQUIRED, *, alias: str | None = None) -> Any:
    """From a form field, urlencoded or multipart; annotated `UploadedFile`, a file.

    Annotated with a msgspec or pydantic model, each of its fields is read from the form field of that name.
    Guide: https://3d3n-pyc.github.io/korvex/validation/#headers-cookies-and-forms
    """
    return Source("form", default, alias)


def Depends(dependency: Callable[..., Any], *, cache: bool = True) -> Any:
    """The value `dependency` returns (or yields), computed before the handler runs.

    - `cache`: call it once per request; `False` calls it for each parameter

    Guide: https://3d3n-pyc.github.io/korvex/dependencies/
    """
    return Dependency(dependency, cache)
