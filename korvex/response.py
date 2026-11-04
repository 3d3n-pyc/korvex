"""`Headers` and `Response`: plain data holders for HTTP responses."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from typing import Union

# `Union` rather than `|`: this line runs at import time, and `|` between
# types needs Python 3.10+.
JSONValue = Union[None, bool, int, float, str, "list[JSONValue]", "dict[str, JSONValue]"]


class Headers:
    """HTTP headers: ordered, case-insensitive, and allowing repeated names
    (e.g. several `Set-Cookie`)."""

    __slots__ = ("_items",)

    def __init__(self, items: Iterable[tuple[str, str]] = ()) -> None:
        self._items = list(items)

    def get(self, name: str, default: str | None = None) -> str | None:
        """The first value for `name`, or `default`."""
        values = self.get_all(name)
        return values[0] if values else default

    def get_all(self, name: str) -> list[str]:
        """Every value for `name`, in order."""
        key = name.lower()
        return [v for n, v in self._items if n.lower() == key]

    def set(self, name: str, value: str) -> None:
        """Replaces every value for `name` with `value`."""
        key = name.lower()
        self._items = [(n, v) for n, v in self._items if n.lower() != key]
        self._items.append((name, value))

    def add(self, name: str, value: str) -> None:
        """Adds a value for `name`, keeping existing ones."""
        self._items.append((name, value))

    def __getitem__(self, name: str) -> str:
        value = self.get(name)
        if value is None:
            raise KeyError(name)
        return value

    def __setitem__(self, name: str, value: str) -> None:
        self.set(name, value)

    def __contains__(self, name: str) -> bool:
        return self.get(name) is not None

    def __iter__(self) -> Iterator[tuple[str, str]]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Headers) and self._items == other._items

    def __repr__(self) -> str:
        return f"Headers({self._items!r})"


class Response:
    """An HTTP response. A `str` body is encoded to UTF-8, so `body` is
    always `bytes`."""

    __slots__ = ("status", "headers", "body")

    def __init__(
        self, body: str | bytes = b"", *, status: int = 200, headers: Iterable[tuple[str, str]] = ()
    ) -> None:
        self.status = status
        self.headers = Headers(headers)
        self.body = body.encode() if isinstance(body, str) else body

    @classmethod
    def text(cls, text: str, *, status: int = 200, headers: Iterable[tuple[str, str]] = ()) -> Response:
        """A `text/plain` response (unless `headers` sets Content-Type)."""
        return cls._with_type(text, "text/plain; charset=utf-8", status, headers)

    @classmethod
    def json(cls, data: JSONValue, *, status: int = 200, headers: Iterable[tuple[str, str]] = ()) -> Response:
        """An `application/json` response (unless `headers` sets Content-Type)."""
        return cls._with_type(json.dumps(data), "application/json", status, headers)

    @classmethod
    def redirect(cls, location: str, *, status: int = 302, headers: Iterable[tuple[str, str]] = ()) -> Response:
        """An empty redirect to `location`."""
        response = cls(status=status, headers=headers)
        response.headers.set("Location", location)
        return response

    @classmethod
    def _with_type(cls, body: str, content_type: str, status: int, headers: Iterable[tuple[str, str]]) -> Response:
        response = cls(body, status=status, headers=headers)
        if "Content-Type" not in response.headers:
            response.headers.set("Content-Type", content_type)
        return response

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, Response)
            and (self.status, self.headers, self.body) == (other.status, other.headers, other.body)
        )

    def __repr__(self) -> str:
        return f"Response(status={self.status}, headers={self.headers!r}, body={self.body!r})"
