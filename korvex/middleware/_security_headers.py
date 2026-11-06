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
