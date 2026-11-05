"""What runs around every request: timing, request IDs, CORS, rate limits.
Hooks run in the order installed."""

import time
from contextvars import ContextVar
from typing import Optional

from korvex import App, Request, Response
from korvex.middleware import cors, rate_limit, request_id

from taskboard.config import Settings

_started: ContextVar[float] = ContextVar("started")


def install(app: App, settings: Settings) -> None:
    @app.before_request
    def start_timer(request: Request) -> None:
        _started.set(time.perf_counter())

    @app.after_request
    def add_timing(request: Request, response: Response) -> None:
        # Shown by the browser's developer tools, next to the network timings.
        elapsed = (time.perf_counter() - _started.get()) * 1000
        response.headers.set("Server-Timing", f"app;dur={elapsed:.2f}")

    request_id(app)  # X-Request-ID on every response, and in every log line
    if settings.cors_origins:
        cors(app, allow_origins=settings.cors_origins)
    rate_limit(app, requests=settings.rate_limit, per=60, key=_limited_key)


def _limited_key(request: Request) -> Optional[str]:
    """Rate limits apply per client address, except to health checks and
    static files."""
    if request.path == "/health" or request.path.startswith("/static/"):
        return None
    return request.client[0] if request.client else "unknown"
