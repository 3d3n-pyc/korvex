"""`Headers` and `Response`: plain data holders for HTTP responses."""

from __future__ import annotations

import asyncio
import json
import mimetypes
import os
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable, Iterable, Iterator, Mapping
from http.cookies import SimpleCookie
from typing import Any, Literal, Optional, Union, cast
from urllib.parse import quote

# `Union` rather than `|`: this line runs at import time, and `|` between
# types needs Python 3.10+.
JSONValue = Union[None, bool, int, float, str, "list[JSONValue]", "dict[str, JSONValue]"]
Chunks = Union[Iterable[Union[bytes, str]], AsyncIterable[Union[bytes, str]]]
HeaderItems = Union[Mapping[str, str], Iterable[tuple[str, str]]]
"""Headers, as a dict (`{"X-Total": "3"}`) or as pairs, which can repeat a
name (`[("Set-Cookie", "a=1"), ("Set-Cookie", "b=2")]`)."""

FILE_CHUNK_SIZE = 64 * 1024
_SSE_HEADERS = (
    ("Content-Type", "text/event-stream; charset=utf-8"),
    ("Cache-Control", "no-cache"),
    ("X-Accel-Buffering", "no"),
)


class Headers:
    """HTTP headers: ordered, case-insensitive, repeated names allowed."""

    __slots__ = ("_items",)

    def __init__(self, items: HeaderItems = ()) -> None:
        self._items = header_pairs(items) if items else []

    @classmethod
    def _trusted(cls, pairs: list[tuple[str, str]]) -> Headers:
        """Headers from pairs already known to be right (a request's), unchecked."""
        headers = cls.__new__(cls)
        headers._items = pairs
        return headers

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
    """An HTTP response; a `str` body is encoded to UTF-8, so `body` is `bytes`.

    Guide: https://3d3n-pyc.github.io/korvex/requests-and-responses/#responses
    """

    __slots__ = ("status", "headers", "body")

    def __init__(self, body: str | bytes = b"", *, status: int = 200, headers: HeaderItems = ()) -> None:
        self.status = status
        self.headers = Headers(headers)
        self.body = body.encode() if isinstance(body, str) else body

    @classmethod
    def text(cls, text: str, *, status: int = 200, headers: HeaderItems = ()) -> Response:
        """A `text/plain` response (unless `headers` sets Content-Type)."""
        return cls._with_type(text, "text/plain; charset=utf-8", status, headers)

    @classmethod
    def json(cls, data: Any, *, status: int = 200, headers: HeaderItems = ()) -> Response:
        """An `application/json` response (unless `headers` sets Content-Type); `data` is anything `json.dumps` takes."""
        return cls._with_type(json.dumps(data), "application/json", status, headers)

    @classmethod
    def redirect(cls, location: str, *, status: int = 302, headers: HeaderItems = ()) -> Response:
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
        """Adds a `Set-Cookie` header; `max_age` is in seconds (none: until the browser closes)."""
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
        """Tells the browser to remove a cookie (same `path` and `domain` as when set)."""
        self.set_cookie(name, "", max_age=0, path=path, domain=domain)

    @classmethod
    def stream(cls, chunks: Chunks, *, status: int = 200, headers: HeaderItems = ()) -> StreamingResponse:
        """A response sent piece by piece, from a sync or async iterable of `bytes` or `str`."""
        return StreamingResponse(chunks, status=status, headers=headers)

    @classmethod
    def sse(
        cls,
        events: Union[Iterable[Any], AsyncIterable[Any]],
        *,
        ping: float = 15.0,
        status: int = 200,
        headers: HeaderItems = (),
    ) -> StreamingResponse:
        """A Server-Sent Events stream, for the browser's `EventSource`.

        Args:
            events: A sync or async iterable of `ServerSentEvent`, or of plain values (sent as the data of an event).
            ping: Seconds of silence before a keep-alive comment is sent, so proxies keep the connection open. 0 turns it off. Only async iterables get pings: a sync generator is streamed as is.
            status: The HTTP status.
            headers: Extra headers; they win over the defaults.

        ```python
        async def clock():
            while True:
                yield ServerSentEvent({"time": time.time()}, event="tick")
                await asyncio.sleep(1)
        return Response.sse(clock())
        ```

        Guide: https://3d3n-pyc.github.io/korvex/requests-and-responses/#server-sent-events
        """
        from korvex import _sse

        chunks: Chunks
        if isinstance(events, AsyncIterable):
            chunks = _sse.with_pings(events.__aiter__(), ping)
        else:
            chunks = (_sse.encode(item) for item in events)
        response = EventStream(chunks, status=status, headers=headers)
        for name, value in _SSE_HEADERS:
            if name not in response.headers:
                response.headers.set(name, value)
        return response

    @classmethod
    def file(
        cls,
        path: str | os.PathLike[str],
        *,
        filename: Optional[str] = None,
        content_type: Optional[str] = None,
        status: int = 200,
        headers: HeaderItems = (),
    ) -> StreamingResponse:
        """Sends a file in chunks, type guessed from its name; `filename` makes it a download."""
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
    def _with_type(cls, body: str, content_type: str, status: int, headers: HeaderItems) -> Response:
        response = cls(body, status=status, headers=headers)
        if not headers:  # the usual case: nothing to look through
            response.headers._items.append(("Content-Type", content_type))
        elif "Content-Type" not in response.headers:
            response.headers.set("Content-Type", content_type)
        return response

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Response) and (self.status, self.headers, self.body) == (
            other.status,
            other.headers,
            other.body,
        )

    def __repr__(self) -> str:
        return f"Response(status={self.status}, headers={self.headers!r}, body={self.body!r})"


class StreamingResponse(Response):
    """A response whose body is sent piece by piece: see `Response.stream()`
    and `Response.file()`."""

    __slots__ = ("chunks", "_on_close")

    def __init__(self, chunks: Chunks, *, status: int = 200, headers: HeaderItems = ()) -> None:
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


class EventStream(StreamingResponse):
    """A `StreamingResponse` from `Response.sse()`: the App watches the client and closes it when it leaves."""

    __slots__ = ()

    async def aclose(self) -> None:
        """Close the source of the chunks so its `finally` blocks run."""
        close = getattr(self.chunks, "aclose", None) or getattr(self.chunks, "close", None)
        if close is not None:
            result = close()
            if result is not None:
                await result


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


def header_pairs(headers: HeaderItems) -> list[tuple[str, str]]:
    """`(name, value)` pairs from a dict or from pairs."""
    if isinstance(headers, Mapping):
        return list(cast("Mapping[str, str]", headers).items())
    pairs: list[tuple[str, str]] = []
    for pair in headers:
        if not (isinstance(pair, tuple) and len(pair) == 2):
            raise TypeError(f"headers takes a dict or (name, value) pairs, not {pair!r} in a list")
        pairs.append(pair)
    return pairs
