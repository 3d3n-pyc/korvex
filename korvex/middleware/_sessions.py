"""Sessions: a small dict kept in a signed cookie, and flash messages on top."""

from __future__ import annotations

import json
from collections.abc import Sequence
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Optional, Union

from korvex.middleware._signing import derive_keys, sign, unsign

if TYPE_CHECKING:
    from korvex._app import App
    from korvex._requests import Connection
    from korvex._response import Response

_MAX_COOKIE = 4096
_FLASH_KEY = "_korvex_flash"
_MARK = "_korvex_sessions"


class Session(dict):  # type: ignore[type-arg]
    """The data kept for one browser between requests, saved in a signed cookie.

    Use it like a dict. Values must be JSON-serialisable.
    Setting, deleting or clearing a key sets `modified`; a change inside a nested
    value is not seen, so set `session.modified = True` yourself after it.
    Guide: https://3d3n-pyc.github.io/korvex/sessions/
    """

    __slots__ = ("modified",)

    def __init__(self, data: Optional[dict[str, Any]] = None) -> None:
        """Starts with `data`, not modified."""
        super().__init__(data or {})
        self.modified = False

    def __setitem__(self, key: str, value: Any) -> None:
        """Sets a key and marks the session modified."""
        self.modified = True
        super().__setitem__(key, value)

    def __delitem__(self, key: str) -> None:
        """Deletes a key and marks the session modified."""
        self.modified = True
        super().__delitem__(key)

    def clear(self) -> None:
        """Removes everything and marks the session modified."""
        self.modified = True
        super().clear()

    def pop(self, *args: Any) -> Any:
        """Removes a key and returns its value, marking the session modified."""
        self.modified = True
        return super().pop(*args)

    def popitem(self) -> tuple[str, Any]:
        """Removes the last item, marking the session modified."""
        self.modified = True
        return super().popitem()

    def setdefault(self, key: str, default: Any = None) -> Any:
        """Returns a key's value, setting it first if missing."""
        if key not in self:
            self.modified = True
        return super().setdefault(key, default)

    def __ior__(self, other: Any) -> Session:  # type: ignore[override,misc]
        """Merges a mapping in place (`session |= {...}`), marking the session modified."""
        self.modified = True
        super().update(other)
        return self

    def update(self, *args: Any, **kwargs: Any) -> None:
        """Updates from a mapping or pairs, marking the session modified."""
        self.modified = True
        super().update(*args, **kwargs)


class _State:
    """What one request knows: its connection and, once asked for, its session."""

    __slots__ = ("connection", "config", "session", "had_cookie")

    def __init__(self, connection: Connection, config: _Config) -> None:
        """Remembers the connection and settings; the session is decoded later."""
        self.connection = connection
        self.config = config
        self.session: Optional[Session] = None
        self.had_cookie = False


class _Config:
    """The settings of one `sessions(app, ...)` call."""

    __slots__ = ("keys", "max_age", "cookie", "secure", "samesite", "path", "domain")

    def __init__(self, keys: list[bytes], **options: Any) -> None:
        """Keeps the keys and cookie options."""
        self.keys = keys
        self.max_age: int = options["max_age"]
        self.cookie: str = options["cookie"]
        self.secure: bool = options["secure"]
        self.samesite: Any = options["samesite"]
        self.path: str = options["path"]
        self.domain: Optional[str] = options["domain"]


_state: ContextVar[Optional[_State]] = ContextVar("korvex_session", default=None)


def sessions(
    app: App,
    *,
    secret_key: Union[str, bytes, Sequence[Union[str, bytes]]],
    max_age: int = 14 * 24 * 3600,
    cookie: str = "session",
    secure: bool = True,
    samesite: str = "lax",
    path: str = "/",
    domain: Optional[str] = None,
) -> None:
    """Keeps a `Session` per browser in a signed, HttpOnly cookie; read it with `current_session()`.

    - `secret_key`: at least 32 bytes; a list rotates keys (the first signs, all verify).
    - `max_age`: seconds a session stays valid.
    - `cookie`, `secure`, `samesite`, `path`, `domain`: the cookie's name and attributes.

    Only a session that was read and changed is sent; the cookie is limited to 4096 bytes.
    Guide: https://3d3n-pyc.github.io/korvex/sessions/
    """
    if any(getattr(h, _MARK, False) for h in app._before_request):
        raise RuntimeError("sessions() is already set up on this app")
    config = _Config(
        derive_keys(secret_key, b"session"),
        max_age=max_age,
        cookie=cookie,
        secure=secure,
        samesite=samesite,
        path=path,
        domain=domain,
    )

    def start(request: Connection) -> None:
        """Remembers the request for `current_session()`."""
        _state.set(_State(request, config))

    setattr(start, _MARK, True)
    app._before_request.insert(0, start)  # first, so every other hook and handler can read the session
    app.after_request(lambda request, response: _save(config, request, response))  # type: ignore[arg-type,return-value]


def _decode(config: _Config, connection: Connection) -> tuple[Session, bool]:
    """Reads the session from the cookie; any bad cookie gives an empty session."""
    raw = connection.cookies.get(config.cookie)
    if raw is None:
        return Session(), False
    payload = unsign(raw, config.keys, config.max_age)
    try:
        data = json.loads(payload) if payload is not None else None
    except (ValueError, RecursionError):
        data = None
    return Session(data if isinstance(data, dict) else None), True


def _save(config: _Config, request: Connection, response: Response) -> None:
    """Writes the session cookie if this request's session was read and changed."""
    state = _state.get()
    if state is None or state.connection is not request:
        return  # nothing started for this request: never use another one's state
    session = state.session
    if session is None or not session.modified:
        return
    if not session:
        if state.had_cookie:
            _set_cookie(config, response, "", 0)
        return
    payload = json.dumps(session, separators=(",", ":")).encode("utf-8")
    value = sign(payload, config.keys[0])
    size = len(config.cookie) + 1 + len(value)
    if size > _MAX_COOKIE:
        raise ValueError(f"the session cookie would be {size} bytes; the limit is {_MAX_COOKIE}")
    _set_cookie(config, response, value, config.max_age)


def _set_cookie(config: _Config, response: Response, value: str, max_age: int) -> None:
    """Sets (or, with `max_age=0`, deletes) the cookie with the configured attributes."""
    response.set_cookie(
        config.cookie,
        value,
        max_age=max_age,
        path=config.path,
        domain=config.domain,
        secure=config.secure,
        httponly=True,
        samesite=config.samesite,  # type: ignore[arg-type]
    )


def current_session() -> Session:
    """The session of the request being handled, read from its cookie on first use.

    A missing, expired or tampered cookie gives an empty session.
    Raises `RuntimeError` outside a request or without `sessions(app, ...)`.
    In a WebSocket it reads the handshake's cookie; changes cannot be saved.
    Guide: https://3d3n-pyc.github.io/korvex/sessions/
    """
    state = _state.get()
    if state is None:
        raise RuntimeError("current_session() needs sessions(app, secret_key) and a running request")
    if state.session is None:
        state.session, state.had_cookie = _decode(state.config, state.connection)
    return state.session


def flash(message: str, category: str = "info") -> None:
    """Keeps a message in the session to show on the next page.

    - `message`: the text.
    - `category`: a label such as "info" or "error".

    Guide: https://3d3n-pyc.github.io/korvex/sessions/
    """
    session = current_session()
    session.setdefault(_FLASH_KEY, []).append([category, message])
    session.modified = True


def get_flashed_messages() -> list[tuple[str, str]]:
    """Returns the `(category, message)` pairs kept by `flash()` and forgets them.

    Guide: https://3d3n-pyc.github.io/korvex/sessions/
    """
    session = current_session()
    if _FLASH_KEY not in session:
        return []
    return [(str(c), str(m)) for c, m in session.pop(_FLASH_KEY)]
