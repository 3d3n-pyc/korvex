"""CORS: letting browsers call the app from other origins."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from korvex._response import Response

if TYPE_CHECKING:
    from korvex._app import App
    from korvex._requests import Connection, Request


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
    """Lets browsers call the app from other origins (CORS).

    - `allow_origins`: origins like `"https://example.com"`, or `"*"`.
    - `allow_methods`: methods a cross-origin request may use.
    - `allow_headers`: request headers allowed beyond the basic ones, or `"*"`.
    - `allow_credentials`: whether cookies may be sent. With `"*"` origins, the
      request's origin is sent back instead of `*`, as browsers require.
    - `expose_headers`: response headers the page's code may read.
    - `max_age`: seconds a preflight answer may be cached.

    Guide: https://3d3n-pyc.github.io/korvex/middleware/#cors-cors-app-allow-origins
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
    def preflight(request: Connection) -> Response | None:
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
