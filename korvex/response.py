"""Lightweight, framework-agnostic HTTP response helpers.

Pure Python, and entirely optional: `Router`/`RouteGroup` never construct
or require a `Response` — this exists purely as a convenience for handler
code, wherever `match_route`'s result ends up being dispatched. Korvex
itself never touches a socket or parses an HTTP request; that's for
whatever server (ASGI, WSGI, a test client, ...) you plug your handlers
into. Building this here means every project using Korvex doesn't have to
reinvent "a place to put a status code, some headers, and a body."
"""

from __future__ import annotations

import json as _json
from collections.abc import Iterable, Iterator
from typing import Union

HeaderItems = Iterable[tuple[str, str]]

# `Union`, not `X | Y`: this is a plain runtime assignment, not deferred by
# `from __future__ import annotations` (that only defers *annotations*), and
# the `|` union operator between builtin types needs Python 3.10+ — `Union`
# stays compatible with this package's 3.9 floor. The recursive `"JSONValue"`
# arms are forward-reference strings for the same reason `list[JSONValue]`
# can't name itself before its own assignment has finished.
JSONValue = Union[None, bool, int, float, str, "list[JSONValue]", "dict[str, JSONValue]"]


class Headers:
    """An ordered, case-insensitive collection of HTTP headers.

    Preserves insertion order and allows repeated names (e.g. multiple
    `Set-Cookie` headers) — the way HTTP actually works, which a plain
    `dict` can't represent. Lookups and single-value assignment
    (`headers["content-type"]`) are case-insensitive, per RFC 9110.
    """

    __slots__ = ("_items",)

    def __init__(self, items: HeaderItems | None = None) -> None:
        self._items: list[tuple[str, str]] = list(items) if items else []

    def get(self, name: str, default: str | None = None) -> str | None:
        """Returns the first value for `name`, or `default`."""
        key = name.lower()
        for existing_name, value in self._items:
            if existing_name.lower() == key:
                return value
        return default

    def get_all(self, name: str) -> list[str]:
        """Returns every value for `name`, in the order they were added."""
        key = name.lower()
        return [value for existing_name, value in self._items if existing_name.lower() == key]

    def set(self, name: str, value: str) -> None:
        """Replaces every existing value for `name` with a single `value`."""
        key = name.lower()
        self._items = [(n, v) for n, v in self._items if n.lower() != key]
        self._items.append((name, value))

    def setdefault(self, name: str, value: str) -> str:
        """If `name` is already set, returns its first value unchanged;
        otherwise sets it to `value` and returns `value` — mirrors
        `dict.setdefault`."""
        existing = self.get(name)
        if existing is not None:
            return existing
        self.set(name, value)
        return value

    def add(self, name: str, value: str) -> None:
        """Appends `value` for `name` without removing existing values for
        it — use this for headers that may legitimately repeat, e.g.
        `Set-Cookie`; use `set` for everything else."""
        self._items.append((name, value))

    def items(self) -> list[tuple[str, str]]:
        """All `(name, value)` pairs, in insertion order, duplicates included."""
        return list(self._items)

    def __getitem__(self, name: str) -> str:
        value = self.get(name)
        if value is None:
            raise KeyError(name)
        return value

    def __setitem__(self, name: str, value: str) -> None:
        self.set(name, value)

    def __delitem__(self, name: str) -> None:
        key = name.lower()
        self._items = [(n, v) for n, v in self._items if n.lower() != key]

    def __contains__(self, name: str) -> bool:
        return self.get(name) is not None

    def __iter__(self) -> Iterator[tuple[str, str]]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Headers):
            return NotImplemented
        return self._items == other._items

    def __repr__(self) -> str:
        return f"Headers({self._items!r})"


class Response:
    """A framework-agnostic HTTP response: status, headers, body.

    A plain data holder — build one in your handler and hand it to
    whatever's actually writing bytes to a socket. `str` bodies are
    UTF-8-encoded to `bytes` on construction so `.body` is always `bytes`,
    which is what most server layers want to write out directly.
    """

    __slots__ = ("status", "headers", "body")

    def __init__(self, body: str | bytes = b"", *, status: int = 200, headers: HeaderItems | None = None) -> None:
        self.status = status
        self.headers = Headers(headers) if headers is not None else Headers()
        self.body = body.encode() if isinstance(body, str) else body

    @classmethod
    def text(cls, text: str, *, status: int = 200, headers: HeaderItems | None = None) -> Response:
        """A `text/plain` response, unless `headers` already sets Content-Type."""
        response = cls(text, status=status, headers=headers)
        response.headers.setdefault("Content-Type", "text/plain; charset=utf-8")
        return response

    @classmethod
    def json(cls, data: JSONValue, *, status: int = 200, headers: HeaderItems | None = None) -> Response:
        """An `application/json` response serializing `data`, unless
        `headers` already sets Content-Type."""
        response = cls(_json.dumps(data), status=status, headers=headers)
        response.headers.setdefault("Content-Type", "application/json")
        return response

    @classmethod
    def redirect(cls, location: str, *, status: int = 302, headers: HeaderItems | None = None) -> Response:
        """An empty-bodied redirect to `location` (default `302 Found`)."""
        response = cls(b"", status=status, headers=headers)
        response.headers.set("Location", location)
        return response

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Response):
            return NotImplemented
        return self.status == other.status and self.headers == other.headers and self.body == other.body

    def __repr__(self) -> str:
        return f"Response(status={self.status}, headers={self.headers!r}, body={self.body!r})"
