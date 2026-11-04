from collections.abc import Mapping, Sequence
from uuid import UUID

Param = str | int | UUID
"""A captured value: `int` for `{id:int}`, `UUID` for `{id:uuid}`, else `str`."""

class MethodNotAllowed(Exception):
    """The path exists, but not for this method (HTTP 405)."""

    allowed: list[str]
    """The methods registered for this path."""

class Router:
    def __init__(self) -> None: ...
    def route(
        self, path: str, handler: str, *, method: str = "GET", middleware: Sequence[str] | None = None
    ) -> None: ...
    def match(self, path: str, method: str = "GET") -> tuple[str, Mapping[str, Param], tuple[str, ...]] | None: ...
    def remove(self, path: str, method: str = "GET") -> bool: ...
    def routes(self) -> list[tuple[str, str, str, tuple[str, ...]]]: ...
