"""Compressing responses: zstd, brotli or gzip."""

from __future__ import annotations

import sys
from collections.abc import AsyncIterable, AsyncIterator, Callable, Sequence
from typing import TYPE_CHECKING

from korvex._response import Response

if TYPE_CHECKING:
    from korvex._app import App
    from korvex._requests import Request


COMPRESSIBLE = (
    "text/",
    "application/json",
    "application/javascript",
    "application/xml",
    "application/x-ndjson",
    "application/wasm",
    "image/svg+xml",
    "+json",
    "+xml",
)


class _Encoder:
    """One content encoding: whole bodies, or a stream piece by piece (each
    piece flushed, so a stream isn't held back)."""

    def __init__(
        self, name: str, whole: Callable[[bytes], bytes], stream: Callable[[], tuple[Callable, Callable]]
    ) -> None:
        self.name = name
        self.whole = whole
        self.stream = stream  # -> (compress_and_flush(piece), finish())


def _gzip(level: int) -> _Encoder:
    import zlib

    def whole(data: bytes) -> bytes:
        compressor = zlib.compressobj(level, zlib.DEFLATED, 16 + zlib.MAX_WBITS)  # 16+: a gzip header
        return compressor.compress(data) + compressor.flush()

    def stream() -> tuple[Callable, Callable]:
        compressor = zlib.compressobj(level, zlib.DEFLATED, 16 + zlib.MAX_WBITS)
        return (lambda piece: compressor.compress(piece) + compressor.flush(zlib.Z_SYNC_FLUSH)), compressor.flush

    return _Encoder("gzip", whole, stream)


def _brotli(level: int) -> _Encoder | None:
    try:
        import brotli  # type: ignore[import-not-found]
    except ImportError:
        return None

    def stream() -> tuple[Callable, Callable]:
        compressor = brotli.Compressor(quality=level)
        return (lambda piece: compressor.process(piece) + compressor.flush()), compressor.finish

    return _Encoder("br", lambda data: brotli.compress(data, quality=level), stream)


def _zstd(level: int) -> _Encoder | None:
    try:
        from compression import zstd  # type: ignore[import-not-found]  # Python 3.14+
    except ImportError:
        return None

    def stream() -> tuple[Callable, Callable]:
        compressor = zstd.ZstdCompressor(level=level)
        return (
            lambda piece: compressor.compress(piece, zstd.ZstdCompressor.FLUSH_BLOCK),
            lambda: compressor.flush(zstd.ZstdCompressor.FLUSH_FRAME),
        )

    return _Encoder("zstd", lambda data: zstd.compress(data, level=level), stream)


def _gil_disabled() -> bool:
    """Whether this is a free-threaded Python running without the GIL."""
    return not getattr(sys, "_is_gil_enabled", lambda: True)()


def _accepted(header: str) -> dict[str, float]:
    """`Accept-Encoding: gzip, br;q=0.8, *;q=0` -> `{"gzip": 1.0, "br": 0.8, "*": 0.0}`."""
    accepted: dict[str, float] = {}
    for item in header.split(","):
        name, _, params = item.strip().partition(";")
        if not name:
            continue
        q = 1.0
        key, _, value = params.strip().partition("=")
        if key.strip().lower() == "q":
            try:
                q = float(value)
            except ValueError:
                q = 0.0
        accepted[name.strip().lower()] = q
    return accepted


def compress(
    app: App,
    *,
    minimum_size: int = 500,
    encodings: Sequence[str] | None = None,
    levels: dict[str, int] | None = None,
    content_types: Sequence[str] = COMPRESSIBLE,
) -> None:
    """Compresses responses for clients that accept it.

    - `encodings`: in order of preference; `("zstd", "br", "gzip")` by default
      (`br` is left out of the default on free-threaded Python, but can be
      asked for). Unavailable ones are skipped.
    - `minimum_size`: smaller bodies are sent as they are; streams are always
      compressed.
    - `levels`: per encoding, like `{"gzip": 9}`.
    - `content_types`: prefixes like `text/` or suffixes like `+json`.

    Call it first: after hooks run last-declared first. Compressed responses
    get `Vary: Accept-Encoding`. Don't compress pages mixing a secret with
    client input (BREACH), over HTTPS too.
    Guide: https://3d3n-pyc.github.io/korvex/middleware/#compression-compressapp
    """
    if encodings is None:
        encodings = ("zstd", "gzip") if _gil_disabled() else ("zstd", "br", "gzip")
    chosen = {**{"gzip": 6, "br": 4, "zstd": 3}, **(levels or {})}
    makers = {"gzip": _gzip, "br": _brotli, "zstd": _zstd}
    unknown = [name for name in encodings if name not in makers]
    if unknown:
        raise ValueError(f"unknown encodings {unknown}: use zstd, br or gzip")
    available = [encoder for name in encodings if (encoder := makers[name](chosen[name])) is not None]
    prefixes = tuple(t for t in content_types if not t.startswith("+"))
    suffixes = tuple(t for t in content_types if t.startswith("+"))

    def pick(request: Request) -> _Encoder | None:
        accepted = _accepted(request.headers.get("accept-encoding") or "")
        anything = accepted.get("*", 0.0) > 0
        best, best_q = None, 0.0
        for encoder in available:  # in our order of preference, at the client's best q
            q = accepted.get(encoder.name, accepted.get("*", 0.0) if anything else 0.0)
            if q > best_q:
                best, best_q = encoder, q
        return best

    @app.after_request
    def compress_response(request: Request, response: Response) -> None:
        from korvex._response import StreamingResponse

        if response.status in (204, 304) or response.status < 200 or "Content-Encoding" in response.headers:
            return
        if "no-transform" in (response.headers.get("cache-control") or "").lower():
            return
        content_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
        if not (content_type.startswith(prefixes) or content_type.endswith(suffixes)):
            return
        streamed = isinstance(response, StreamingResponse)
        if not streamed and len(response.body) < minimum_size:
            return
        encoder = pick(request)
        # The answer depends on Accept-Encoding even when it's not compressed.
        if "accept-encoding" not in (", ".join(response.headers.get_all("vary"))).lower():
            response.headers.add("Vary", "Accept-Encoding")
        if encoder is None:
            return
        response.headers.set("Content-Encoding", encoder.name)
        response.headers.remove("Content-Length")  # the length changes
        if isinstance(response, StreamingResponse):
            response.chunks = _compressed(StreamingResponse(response.chunks), encoder)
        else:
            response.body = encoder.whole(response.body)


async def _compressed(source: AsyncIterable[bytes], encoder: _Encoder) -> AsyncIterator[bytes]:
    """`source`'s chunks, compressed piece by piece."""
    piece, finish = encoder.stream()
    async for chunk in source:
        out = piece(chunk)
        if out:
            yield out
    yield finish()
