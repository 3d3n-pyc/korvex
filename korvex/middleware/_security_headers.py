"""The headers that tell browsers to protect the page."""

from __future__ import annotations

from typing import TYPE_CHECKING

from korvex._response import Response

if TYPE_CHECKING:
    from korvex._app import App
    from korvex._requests import Request


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
    """Adds the usual security headers to every response; one a response sets keeps its own.

    - `hsts`: `Strict-Transport-Security` max-age in seconds, `None` to leave it
      out; sent only when `request.scheme` is `https` (set by a trusted proxy);
      raises ValueError if negative.
    - `hsts_subdomains`, `hsts_preload`: add those directives.
    - `content_security_policy`: off by default, like `"default-src 'self'"`.
    - `frame_options`: `X-Frame-Options`.
    - `content_type_options`: send `X-Content-Type-Options: nosniff`.
    - `referrer_policy`, `cross_origin_opener_policy`, `permissions_policy`:
      those headers; `None` leaves one out.

    Guide: https://3d3n-pyc.github.io/korvex/middleware/#security-headers-security_headersapp
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
