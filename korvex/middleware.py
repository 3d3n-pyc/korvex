"""Ready-made hooks for common jobs. Each function registers its hooks
on an App:

    from korvex.middleware import cors

    cors(app, allow_origins=["https://example.com"])
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import math
import re
import sys
import threading
import time
import uuid
import weakref
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable, Hashable, Sequence
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, NamedTuple, Optional, Protocol

from korvex.response import Response

if TYPE_CHECKING:
    from korvex.app import App, Request

logger = logging.getLogger("korvex")

ALL_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")


def cors(
    app: App,
    *,
    allow_origins: Sequence[str] | str,
    allow_methods: Sequence[str] = ALL_METHODS,
    allow_headers: Sequence[str] | str = (),
    allow_credentials: bool = False,
    expose_headers: Sequence[str] = (),
    max_age: int = 600,
) -> None:
    """Lets browsers call the app from other origins (Cross-Origin Resource
    Sharing).

    - `allow_origins`: origins like `"https://example.com"`, or `"*"`.
    - `allow_methods`: methods a cross-origin request may use.
    - `allow_headers`: request headers it may send beyond the basic ones,
      or `"*"` for any.
    - `allow_credentials`: whether it may send cookies. With `"*"` origins,
      the request's origin is sent back instead of `*`, as browsers require.
    - `expose_headers`: response headers the page's code may read.
    - `max_age`: seconds the browser may cache a preflight answer.

    Preflight requests (`OPTIONS` with `Access-Control-Request-Method`) are
    answered before routing: 204 if allowed, 400 if not. Other responses
    from an allowed origin, errors included, get the CORS headers.
    """
    any_origin = allow_origins == "*"
    origins = set() if any_origin else set(allow_origins)
    methods = {method.upper() for method in allow_methods}
    any_header = allow_headers == "*"
    headers = set() if any_header else {header.lower() for header in allow_headers}

    def allowed_origin(origin: str) -> str | None:
        """The value of Access-Control-Allow-Origin for `origin`, or None."""
        if any_origin:
            return origin if allow_credentials else "*"
        return origin if origin in origins else None

    def common_headers(response: Response, origin_value: str) -> None:
        response.headers.set("Access-Control-Allow-Origin", origin_value)
        if origin_value != "*":
            response.headers.add("Vary", "Origin")  # the answer depends on who asks
        if allow_credentials:
            response.headers.set("Access-Control-Allow-Credentials", "true")

    @app.before_request
    def preflight(request: Request) -> Response | None:
        origin = request.headers.get("origin")
        requested_method = request.headers.get("access-control-request-method")
        if request.method != "OPTIONS" or origin is None or requested_method is None:
            return None  # not a preflight
        origin_value = allowed_origin(origin)
        requested_headers = [
            h.strip().lower()
            for h in (request.headers.get("access-control-request-headers") or "").split(",")
            if h.strip()
        ]
        if origin_value is None:
            return Response.text("Disallowed CORS origin", status=400)
        if requested_method.upper() not in methods:
            return Response.text("Disallowed CORS method", status=400)
        if not any_header and any(h not in headers for h in requested_headers):
            return Response.text("Disallowed CORS headers", status=400)
        response = Response(status=204)
        common_headers(response, origin_value)
        response.headers.set("Access-Control-Allow-Methods", ", ".join(sorted(methods)))
        if requested_headers:
            response.headers.set("Access-Control-Allow-Headers", ", ".join(requested_headers))
        response.headers.set("Access-Control-Max-Age", str(max_age))
        return response

    @app.after_request
    def add_headers(request: Request, response: Response) -> None:
        origin = request.headers.get("origin")
        if origin is None or "Access-Control-Allow-Origin" in response.headers:
            return  # not cross-origin, or already handled (a preflight)
        origin_value = allowed_origin(origin)
        if origin_value is None:
            return  # the browser will block the response
        common_headers(response, origin_value)
        if expose_headers:
            response.headers.set("Access-Control-Expose-Headers", ", ".join(expose_headers))


_request_id: ContextVar[str | None] = ContextVar("korvex_request_id", default=None)
# A request ID from outside is reused only if it's short and plain, so it
# can't inject newlines or huge values into the logs.
_SAFE_ID = re.compile(r"[A-Za-z0-9._:-]{1,200}")


def request_id(app: App, *, header: str = "X-Request-ID", trust_incoming: bool = True) -> None:
    """Gives every request an ID: the incoming `header` (from the client or
    a proxy) when `trust_incoming` and it's safe, otherwise a new random
    one. The ID goes back in the response's `header`, `current_request_id()`
    returns it while the request runs, and `RequestIdFilter` adds it to
    log records. The built-in server's access logs show it too."""

    @app.before_request
    def assign(request: Request) -> None:
        incoming = request.headers.get(header) if trust_incoming else None
        _request_id.set(incoming if incoming and _SAFE_ID.fullmatch(incoming) else uuid.uuid4().hex)

    @app.after_request
    def send_back(request: Request, response: Response) -> None:
        value = _request_id.get()
        if value is not None:
            response.headers.set(header, value)


def current_request_id() -> str | None:
    """The ID of the request being handled, if `request_id()` is set up."""
    return _request_id.get()


class RequestIdFilter(logging.Filter):
    """A logging filter that adds `request_id` to every record (`-` outside
    a request), for formats like `"%(request_id)s %(message)s"`:

        handler.addFilter(RequestIdFilter())
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get() or "-"
        return True


class Decision(NamedTuple):
    """A rate limit store's answer for one request."""

    allowed: bool
    remaining: int  # requests left before the limit
    retry_after: float  # seconds until the next request would be allowed (0 if allowed)
    reset: float  # seconds until the key's whole allowance is back


class RateLimitStore(Protocol):
    """Where rate limit state lives. The store decides, rather than just
    storing numbers, so a shared store (Redis, say) can do it atomically.
    `hit` can be a coroutine function, for a store over the network."""

    def hit(self, key: Hashable, requests: int, per: float) -> Decision | Awaitable[Decision]: ...


class MemoryStore:
    """Rate limit state in this process's memory. With several workers,
    each has its own: a client can get up to `requests` x workers.

    Uses GCRA (a token bucket kept as one timestamp per key): up to
    `requests` at once, then one every `per / requests` seconds. Safe with
    several threads (`threads=` on free-threaded Python): one at a time."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._next_free: dict[Hashable, float] = {}  # key -> when its allowance is fully back
        self._calls = 0
        self._lock = threading.Lock()  # read, decide, write: as one step

    def hit(self, key: Hashable, requests: int, per: float) -> Decision:
        with self._lock:
            return self._hit(key, requests, per)

    def _hit(self, key: Hashable, requests: int, per: float) -> Decision:
        now = self._clock()
        interval = per / requests
        full_back = max(self._next_free.get(key, now), now)
        allowed_at = full_back - per + interval  # a burst of `requests` fits in `per`
        if now < allowed_at:
            return Decision(False, 0, allowed_at - now, full_back - now)
        full_back += interval
        self._next_free[key] = full_back
        self._forget_old(now)
        remaining = int((per - (full_back - now)) / interval)
        return Decision(True, max(remaining, 0), 0.0, full_back - now)

    def _forget_old(self, now: float) -> None:
        """Drops keys whose allowance is fully back, now and then, so memory
        doesn't grow with every client ever seen."""
        self._calls += 1
        if self._calls % 1000 == 0:
            self._next_free = {k: t for k, t in self._next_free.items() if t > now}


# GCRA as MemoryStore does it, in Redis, atomically: one script, run by
# Redis alone, so concurrent workers and machines can't race. The clock is
# Redis's own, the same for all of them. Numbers go back as strings: Redis
# would truncate Lua floats to integers.
_REDIS_GCRA = """
local requests = tonumber(ARGV[1])
local per = tonumber(ARGV[2])
local interval = per / requests
local time = redis.call('TIME')
local now = tonumber(time[1]) + tonumber(time[2]) / 1000000
local full_back = tonumber(redis.call('GET', KEYS[1])) or now
if full_back < now then full_back = now end
local allowed_at = full_back - per + interval
if now < allowed_at then
  return {0, 0, tostring(allowed_at - now), tostring(full_back - now)}
end
full_back = full_back + interval
redis.call('SET', KEYS[1], tostring(full_back), 'PX', math.ceil((full_back - now) * 1000))
local remaining = math.floor((per - (full_back - now)) / interval)
if remaining < 0 then remaining = 0 end
return {1, remaining, '0', tostring(full_back - now)}
"""


class RedisStore:
    """Rate limit state in Redis, shared by every worker and machine, so a
    limit holds for the whole deployment. Needs the `redis` package
    (`pip install korvex[redis]`).

        rate_limit(app, requests=100, per=60, store=RedisStore("redis://localhost:6379/0"))

    - `client`: a URL, or a `redis.asyncio.Redis` you made yourself (TLS,
      a password, Sentinel...). Connections open on first use, so creating
      the store at import time is fine with several workers. From a URL,
      each event loop (`threads=`) gets its own client; one you made
      belongs to one loop.
    - `prefix`: put before each key, to keep them apart from your own.
    - `fail_open`: if Redis can't be reached, let requests through (the
      default, so an outage of Redis isn't one of the app) or refuse them
      with a 429. Either way, the error is logged.

    Same algorithm as `MemoryStore`, run atomically in Redis on its clock.
    """

    def __init__(
        self, client: Any = "redis://localhost:6379/0", *, prefix: str = "korvex:ratelimit:", fail_open: bool = True
    ) -> None:
        self._url: str | None = None
        self.client = None
        if isinstance(client, str):
            try:
                import redis.asyncio  # noqa: F401
            except ImportError:
                raise ImportError("RedisStore needs the redis package: pip install korvex[redis]") from None
            self._url = client
        else:
            self.client = client
            self._given = client.register_script(_REDIS_GCRA)
        self._prefix = prefix
        self._fail_open = fail_open
        # Event loop -> its script: a redis.asyncio client's connections
        # belong to the loop that opened them.
        self._scripts: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
        self._scripts_lock = threading.Lock()  # loops in several threads: threads=N

    def _script(self) -> Any:
        if self._url is None:
            return self._given
        loop = asyncio.get_running_loop()
        with self._scripts_lock:
            script = self._scripts.get(loop)
            if script is None:
                import redis.asyncio

                script = self._scripts[loop] = redis.asyncio.Redis.from_url(self._url).register_script(_REDIS_GCRA)
        return script

    async def hit(self, key: Hashable, requests: int, per: float) -> Decision:
        try:
            allowed, remaining, retry_after, reset = await self._script()(
                keys=[f"{self._prefix}{key}"], args=[requests, repr(float(per))]
            )
        except Exception:  # a connection error, a timeout...: Redis isn't answering
            logger.exception(
                "rate limit store unreachable; %s the request", "allowing" if self._fail_open else "refusing"
            )
            if self._fail_open:
                return Decision(True, requests, 0.0, 0.0)
            return Decision(False, 0, 1.0, 1.0)
        return Decision(bool(int(allowed)), int(remaining), float(retry_after), float(reset))


def _client_address(request: Request) -> Optional[Hashable]:
    # An unknown address shares one limit rather than escaping it: behind a
    # proxy that doesn't send X-Forwarded-For, every address is unknown.
    return request.client[0] if request.client else "unknown"


_decision: ContextVar[Optional[Decision]] = ContextVar("korvex_rate_limit", default=None)


def rate_limit(
    app: App,
    *,
    requests: int,
    per: float,
    key: Callable[[Request], Optional[Hashable]] = _client_address,
    store: RateLimitStore | None = None,
    headers: bool = True,
) -> None:
    """Allows each key `requests` requests per `per` seconds; beyond that,
    requests get a 429 with Retry-After.

    - `key(request)`: who the limit applies to. The client's address by
      default (requests with an unknown address share one limit); an API
      key or user ID is fairer behind shared addresses. Return `None` to
      exempt a request (a health check, say).
    - `store`: where the state lives; a `MemoryStore` by default, one per
      worker process. `RedisStore` shares it between workers and machines.
    - `headers`: send RateLimit-Limit, RateLimit-Remaining and
      RateLimit-Reset on every limited response, so clients can slow down.

    Runs as a before hook, so it sees every request; hooks declared before
    it (like `cors()`, for preflight requests) run first.
    """
    if requests < 1 or per <= 0:
        raise ValueError("requests must be at least 1 and per positive")
    state = store if store is not None else MemoryStore()

    def answer(decision: Decision) -> Response | None:
        _decision.set(decision)
        if decision.allowed:
            return None
        return Response.text(
            "Too Many Requests", status=429, headers=[("Retry-After", str(math.ceil(decision.retry_after)))]
        )

    if inspect.iscoroutinefunction(state.hit):  # a store over the network: don't block the loop

        @app.before_request
        async def check_async(request: Request) -> Response | None:
            who = key(request)
            if who is None:
                _decision.set(None)
                return None
            return answer(await state.hit(who, requests, per))  # type: ignore[misc]

    else:

        @app.before_request
        def check(request: Request) -> Response | None:
            who = key(request)
            if who is None:
                _decision.set(None)
                return None
            return answer(state.hit(who, requests, per))  # type: ignore[arg-type]

    if headers:

        @app.after_request
        def add_headers(request: Request, response: Response) -> None:
            decision = _decision.get()
            if decision is not None:
                response.headers.set("RateLimit-Limit", str(requests))
                response.headers.set("RateLimit-Remaining", str(decision.remaining))
                response.headers.set("RateLimit-Reset", str(math.ceil(decision.reset)))


# --- compression ---

# Text-like content worth compressing; images, video, archives and fonts
# (woff2) are compressed already.
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
    """One content encoding: whole bodies at once, or a stream piece by
    piece (each piece flushed, so a streamed response isn't held back)."""

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
    """Compresses responses for clients that accept it, for deployments
    without a proxy to do it.

    - `encodings`: in order of preference, among those the client accepts
      (`Accept-Encoding`); `("zstd", "br", "gzip")` by default. `gzip`
      always works; `br` needs the `brotli` package (`pip install
      korvex[brotli]`), `zstd` Python 3.14 or later. Those not available
      are skipped. On a free-threaded Python, `br` is left out of the
      default: the `brotli` package turns the GIL back on for the whole
      process when imported. Ask for it explicitly to use it anyway.
    - `minimum_size`: smaller bodies are sent as they are (compressing them
      gains little). Streamed responses are compressed whatever their size,
      each piece flushed as it goes, so a live stream isn't held back.
    - `levels`: per encoding (`{"gzip": 9}`); the defaults trade a little
      size for speed: gzip 6, brotli 4, zstd 3.
    - `content_types`: what's worth compressing: prefixes like `text/`, or
      suffixes like `+json`.

    Responses that already have a `Content-Encoding`, ask for no change
    (`Cache-Control: no-transform`), or carry no body (204, 304) are left
    alone. Compressed responses get `Vary: Accept-Encoding`.

    Call it before the other hooks: after hooks run last-declared first,
    so it then sees each response once the others have changed it.

    Mind BREACH: compressing a response that contains both a secret (a
    CSRF token) and something the client sent lets an attacker guess the
    secret, over HTTPS too. Don't compress such pages, or change the
    secret on every response.
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
        from korvex.response import StreamingResponse

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


# --- security headers ---


def security_headers(
    app: App,
    *,
    hsts: int | None = 31_536_000,
    hsts_subdomains: bool = False,
    hsts_preload: bool = False,
    content_security_policy: str | None = None,
    frame_options: str | None = "DENY",
    content_type_options: bool = True,
    referrer_policy: str | None = "strict-origin-when-cross-origin",
    cross_origin_opener_policy: str | None = "same-origin",
    permissions_policy: str | None = None,
) -> None:
    """Adds the usual security headers to every response, for deployments
    without a proxy to do it. A response that sets one itself keeps its own.

    - `hsts`: `Strict-Transport-Security` with this `max-age` (one year by
      default), so browsers stick to HTTPS. Only on HTTPS requests
      (`request.scheme`, which a trusted proxy sets): browsers ignore it
      over HTTP. `hsts_subdomains` and `hsts_preload` add those directives;
      only use them once every subdomain serves HTTPS.
    - `content_security_policy`: off by default, since the right policy
      depends on the pages: `"default-src 'self'"` is a strict start.
      `/docs` (Swagger UI) loads its script and style from
      cdn.jsdelivr.net, which a strict policy blocks.
    - `frame_options`: `X-Frame-Options`, `DENY` so other sites can't put
      the pages in a frame (clickjacking); `SAMEORIGIN` to allow your own.
    - `content_type_options`: `X-Content-Type-Options: nosniff`, so
      browsers trust `Content-Type` instead of guessing.
    - `referrer_policy`, `cross_origin_opener_policy`, `permissions_policy`:
      those headers, `None` to leave one out.
    """
    always = []
    if frame_options:
        always.append(("X-Frame-Options", frame_options))
    if content_type_options:
        always.append(("X-Content-Type-Options", "nosniff"))
    if referrer_policy:
        always.append(("Referrer-Policy", referrer_policy))
    if cross_origin_opener_policy:
        always.append(("Cross-Origin-Opener-Policy", cross_origin_opener_policy))
    if content_security_policy:
        always.append(("Content-Security-Policy", content_security_policy))
    if permissions_policy:
        always.append(("Permissions-Policy", permissions_policy))
    transport = None
    if hsts is not None:
        if hsts < 0:
            raise ValueError("hsts is a max-age in seconds, or None")
        transport = (
            f"max-age={hsts}"
            + ("; includeSubDomains" if hsts_subdomains else "")
            + ("; preload" if hsts_preload else "")
        )

    @app.after_request
    def add_security_headers(request: Request, response: Response) -> None:
        for name, value in always:
            if name not in response.headers:
                response.headers.set(name, value)
        if transport is not None and request.scheme == "https" and "Strict-Transport-Security" not in response.headers:
            response.headers.set("Strict-Transport-Security", transport)
