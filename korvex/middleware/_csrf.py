"""CSRF protection without tokens: refuses cross-site browser requests."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from korvex._errors import HTTPException
from korvex._requests import WebSocket

if TYPE_CHECKING:
    from korvex._app import App
    from korvex._requests import Connection

_SAFE_METHODS = frozenset(("GET", "HEAD", "OPTIONS"))


def _clean_origin(value: str) -> str:
    """`value` as lower-case `scheme://host[:port]`, or ValueError if it isn't one."""
    try:
        parts = urlsplit(value)
        parts.port  # noqa: B018 - raises ValueError on a bad port
    except ValueError:
        raise ValueError(f"invalid trusted origin: {value!r}") from None
    if parts.scheme not in ("http", "https") or not parts.hostname or value != f"{parts.scheme}://{parts.netloc}":
        raise ValueError(f"invalid trusted origin {value!r}: expected 'https://host[:port]'")
    return value.lower()


def _split_exempt(entries: Iterable[str]) -> tuple[frozenset[str], tuple[str, ...]]:
    """Splits exempt entries into exact paths and prefixes (ending in `/`)."""
    exact, prefixes = set(), []
    for entry in entries:
        if not isinstance(entry, str) or not entry.startswith("/"):
            raise ValueError(f"invalid exempt path {entry!r}: it must start with '/'")
        if entry.endswith("/") and entry != "/":
            prefixes.append(entry)
        else:
            exact.add(entry)
    return frozenset(exact), tuple(prefixes)


def csrf(app: App, *, trusted_origins: Iterable[str] = (), exempt: Iterable[str] = ()) -> None:
    """Refuses cross-site requests that change things, without tokens (CSRF).

    It trusts what the browser sends (`Sec-Fetch-Site`, else `Origin` against `Host`).
    GET, HEAD and OPTIONS pass; WebSocket handshakes are always checked. Requests with neither header pass.

    - `trusted_origins`: other origins allowed, like `"https://admin.example.com"`.
    - `exempt`: paths to skip: exact (`"/hook"`) or ending in `/` for a prefix (`"/webhooks/"`).

    Guide: https://3d3n-pyc.github.io/korvex/sessions/
    """
    trusted = frozenset(_clean_origin(origin) for origin in trusted_origins)
    exact, prefixes = _split_exempt(exempt)

    @app.before_request
    def check(request: Connection) -> None:
        headers = request.headers
        if request.method in _SAFE_METHODS and not isinstance(request, WebSocket):
            return
        path = request.path
        if path in exact or (prefixes and path.startswith(prefixes)):
            return
        site = headers.get("sec-fetch-site")
        origin = headers.get("origin")
        if site is not None:
            if site in ("same-origin", "none") or (origin is not None and origin.lower() in trusted):
                return
        elif origin is None:
            return  # not a browser
        elif origin.lower() in trusted or _same_host(origin, headers.get("host")):
            return
        raise HTTPException(403, "cross-origin request refused")


def _same_host(origin: str, host: str | None) -> bool:
    """Whether the `Origin` header's host[:port] is the request's `Host`."""
    if host is None or origin == "null":
        return False
    _, separator, netloc = origin.partition("://")
    return bool(separator) and netloc.lower() == host.lower()
