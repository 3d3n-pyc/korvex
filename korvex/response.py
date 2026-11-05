"""`Headers` and `Response`: plain data holders for HTTP responses."""

from __future__ import annotations

import asyncio
import json
import mimetypes
import os
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable, Iterable, Iterator
from http.cookies import SimpleCookie
from typing import Any, Literal, Optional, Union
from urllib.parse import quote

# `Union` rather than `|`: this line runs at import time, and `|` between
# types needs Python 3.10+.
JSONValue = Union[None, bool, int, float, str, "list[JSONValue]", "dict[str, JSONValue]"]
Chunks = Union[Iterable[Union[bytes, str]], AsyncIterable[Union[bytes, str]]]

FILE_CHUNK_SIZE = 64 * 1024


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

    def remove(self, name: str) -> None:
        """Removes every value for `name`."""
        key = name.lower()
        self._items = [(n, v) for n, v in self._items if n.lower() != key]

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
    def json(cls, data: Any, *, status: int = 200, headers: Iterable[tuple[str, str]] = ()) -> Response:
        """An `application/json` response (unless `headers` sets Content-Type).
        `data` is anything `json.dumps` takes. (Typed `Any`, not `JSONValue`:
        type checkers reject a `dict[str, list[dict]]` as a `JSONValue`.)"""
        return cls._with_type(json.dumps(data), "application/json", status, headers)

    @classmethod
    def redirect(cls, location: str, *, status: int = 302, headers: Iterable[tuple[str, str]] = ()) -> Response:
        """An empty redirect to `location`."""
        response = cls(status=status, headers=headers)
        response.headers.set("Location", location)
        return response

    def set_cookie(
        self,
        name: str,
        value: str,
        *,
        max_age: Optional[int] = None,
        path: str = "/",
        domain: Optional[str] = None,
        secure: bool = False,
        httponly: bool = False,
        samesite: Optional[Literal["lax", "strict", "none"]] = "lax",
    ) -> None:
        """Adds a `Set-Cookie` header. `max_age` is in seconds; without it,
        the cookie lasts until the browser closes."""
        cookie: SimpleCookie = SimpleCookie()
        cookie[name] = value
        morsel = cookie[name]
        morsel["path"] = path
        if max_age is not None:
            morsel["max-age"] = max_age
        if domain:
            morsel["domain"] = domain
        if secure:
            morsel["secure"] = True
        if httponly:
            morsel["httponly"] = True
        if samesite:
            morsel["samesite"] = samesite
        self.headers.add("Set-Cookie", morsel.OutputString())

    def delete_cookie(self, name: str, *, path: str = "/", domain: Optional[str] = None) -> None:
        """Tells the browser to remove a cookie (same `path` and `domain`
        as when it was set)."""
        self.set_cookie(name, "", max_age=0, path=path, domain=domain)

    @classmethod
    def stream(
        cls, chunks: Chunks, *, status: int = 200, headers: Iterable[tuple[str, str]] = ()
    ) -> StreamingResponse:
        """A response sent piece by piece, from an iterable or a generator
        (sync or async) of `bytes` or `str`."""
        return StreamingResponse(chunks, status=status, headers=headers)

    @classmethod
    def file(
        cls,
        path: str | os.PathLike[str],
        *,
        filename: Optional[str] = None,
        content_type: Optional[str] = None,
        status: int = 200,
        headers: Iterable[tuple[str, str]] = (),
    ) -> StreamingResponse:
        """Sends a file, read in chunks without blocking. The content type
        is guessed from the name. With `filename`, the browser downloads it
        under that name instead of showing it."""
        size = os.stat(path).st_size  # raises FileNotFoundError now, not mid-response
        response = StreamingResponse(_read_file(path), status=status, headers=headers)
        guessed = content_type or mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        if "Content-Type" not in response.headers:
            response.headers.set("Content-Type", guessed)
        response.headers.set("Content-Length", str(size))
        if filename is not None:
            response.headers.set("Content-Disposition", _attachment(filename))
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


class StreamingResponse(Response):
    """A response whose body is sent piece by piece: see `Response.stream()`
    and `Response.file()`."""

    __slots__ = ("chunks", "_on_close")

    def __init__(self, chunks: Chunks, *, status: int = 200, headers: Iterable[tuple[str, str]] = ()) -> None:
        super().__init__(status=status, headers=headers)
        self.chunks = chunks
        self._on_close: list[Callable[[], Awaitable[None]]] = []  # run once sent, or not sent after all

    async def __aiter__(self) -> AsyncIterator[bytes]:
        """The chunks as bytes, whether they were sync or async, str or bytes."""
        if isinstance(self.chunks, AsyncIterable):
            async for chunk in self.chunks:
                yield chunk.encode() if isinstance(chunk, str) else chunk
        else:
            for chunk in self.chunks:
                yield chunk.encode() if isinstance(chunk, str) else chunk

    def __repr__(self) -> str:
        return f"StreamingResponse(status={self.status}, headers={self.headers!r})"


async def _read_file(path: str | os.PathLike[str]) -> AsyncIterator[bytes]:
    """Reads a file in chunks, each in a thread so the event loop keeps going."""
    with open(path, "rb") as file:
        while chunk := await asyncio.to_thread(file.read, FILE_CHUNK_SIZE):
            yield chunk


def _attachment(filename: str) -> str:
    """A `Content-Disposition` for a download, non-ASCII names included.
    Quotes, backslashes and control characters are dropped from ASCII
    names; non-ASCII names are percent-encoded, which covers them."""
    if filename.isascii():
        safe = "".join(c for c in filename if c.isprintable() and c not in '"\\')
        return f'attachment; filename="{safe}"'
    return f"attachment; filename*=UTF-8''{quote(filename)}"
