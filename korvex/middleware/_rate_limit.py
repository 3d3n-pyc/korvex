"""Rate limiting, in memory or shared through Redis."""

from __future__ import annotations

import asyncio
import inspect
import logging
import math
import threading
import time
import weakref
from collections.abc import Awaitable, Callable, Hashable
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, NamedTuple, Optional, Protocol

from korvex.response import Response

if TYPE_CHECKING:
    from korvex.app import App
    from korvex.requests import Request


logger = logging.getLogger("korvex")


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
