"""Signed values: a payload, a timestamp and an HMAC, safe to put in a cookie."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import time
from collections.abc import Sequence
from typing import Union

_MIN_SECRET = 32
_MAX_FUTURE = 60.0


def _b64e(data: bytes) -> str:
    """Encodes bytes as URL-safe base64 without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    """Decodes URL-safe base64 without padding, raising ValueError if it is bad."""
    if not text.isascii():
        raise ValueError("not ASCII")
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError) as exc:
        raise ValueError("bad base64") from exc


def derive_keys(secrets: Union[str, bytes, Sequence[Union[str, bytes]]], purpose: bytes) -> list[bytes]:
    """Turns secrets into signing keys that are only good for `purpose`."""
    items = [secrets] if isinstance(secrets, (str, bytes)) else list(secrets)
    raw = [s.encode("utf-8") if isinstance(s, str) else s for s in items]
    if not raw or any(len(s) < _MIN_SECRET for s in raw):
        raise ValueError(
            f"a secret must be at least {_MIN_SECRET} bytes long; make one with: "
            'python -c "import secrets; print(secrets.token_urlsafe(32))"'
        )
    return [hmac.new(s, b"korvex:" + purpose, hashlib.sha256).digest() for s in raw]


def _mac(key: bytes, body: str) -> str:
    """Returns the encoded HMAC-SHA256 of `body`."""
    return _b64e(hmac.new(key, body.encode("ascii"), hashlib.sha256).digest())


def sign(payload: bytes, key: bytes, now: Union[float, None] = None) -> str:
    """Signs `payload` with `key` and the current time."""
    stamp = int(time.time() if now is None else now)
    body = _b64e(payload) + "." + _b64e(stamp.to_bytes(8, "big"))
    return body + "." + _mac(key, body)


def _check_age(stamp: bytes, max_age: Union[float, None], now: float) -> bool:
    """Tells whether a timestamp is neither too old nor too far in the future."""
    if len(stamp) != 8:
        return False
    age = now - int.from_bytes(stamp, "big")
    return age >= -_MAX_FUTURE and (max_age is None or age <= max_age)


def unsign(
    value: str,
    keys: Sequence[bytes],
    max_age: Union[float, None],
    now: Union[float, None] = None,
) -> Union[bytes, None]:
    """Returns the payload if `value` is genuine and fresh, else None."""
    parts = value.split(".", 3)
    if len(parts) != 3 or not value.isascii():
        return None
    body = parts[0] + "." + parts[1]
    if not any(hmac.compare_digest(_mac(k, body), parts[2]) for k in keys):
        return None
    try:
        payload, stamp = _b64d(parts[0]), _b64d(parts[1])
    except ValueError:
        return None
    return payload if _check_age(stamp, max_age, time.time() if now is None else now) else None
