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
    """From the query string. Typed parameters come from there anyway: use
    it for a name that isn't a Python identifier (`alias="page-size"`)."""
    return Source("query", default, alias)


def Header(default: Any = REQUIRED, *, alias: str | None = None) -> Any:
    """From a request header. `x_client` reads `X-Client`: underscores
    become hyphens and case doesn't matter. `alias` gives the name as is.
    A `list[str]` gets every value of a repeated header."""
    return Source("header", default, alias)


def Cookie(default: Any = REQUIRED, *, alias: str | None = None) -> Any:
    """From a cookie of that name (or `alias`)."""
    return Source("cookie", default, alias)


def Form(default: Any = REQUIRED, *, alias: str | None = None) -> Any:
    """From a form field, urlencoded or multipart. Annotated `UploadedFile`
    (or `list[UploadedFile]`), it's a file. An empty field counts as
    missing, except for `str`. Annotated with a msgspec or pydantic model,
    it's the model, each of its fields from the form field of that name."""
    return Source("form", default, alias)


def Depends(dependency: Callable[..., Any], *, cache: bool = True) -> Any:
    """The value `dependency` returns, computed before the handler runs.

    A dependency takes parameters like a handler does (query, headers,
    cookies, body, path, other dependencies), plus the request if it asks
    for one (annotated `Request`, or named `request`). It can be sync or
    async, and can raise `HTTPException` to stop the request.

    - One that `yield`s instead of returning is torn down once the handler
      is done: the code after `yield` runs when the handler returns (for a
      streamed response, once the stream is sent), and an error from the
      handler is raised at the `yield`, so `try`/`except`/`finally` work
      there. The error still gets its usual response afterwards.
    - Within one request, a dependency is called once however many
      parameters ask for it; `cache=False` calls it for each.
    - `app.dependency_overrides[dependency] = other` replaces it, in tests.
    """
    return Dependency(dependency, cache)
