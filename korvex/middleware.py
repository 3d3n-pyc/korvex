"""Ready-made hooks for common jobs. Each function registers its hooks
on an App:

    from korvex.middleware import cors

    cors(app, allow_origins=["https://example.com"])
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Sequence
from contextvars import ContextVar
from typing import TYPE_CHECKING

from korvex.response import Response

if TYPE_CHECKING:
    from korvex.app import App, Request

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
            h.strip().lower() for h in (request.headers.get("access-control-request-headers") or "").split(",") if h.strip()
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
