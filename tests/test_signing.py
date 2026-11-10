"""Tests for the signing primitive."""

from __future__ import annotations

import pytest

from korvex.middleware._signing import derive_keys, sign, unsign

SECRET = "s" * 40


def test_round_trip() -> None:
    key = derive_keys(SECRET, b"session")[0]
    assert unsign(sign(b"hello", key), [key], 100) == b"hello"
    assert unsign(sign(b"", key), [key], None) == b""


@pytest.mark.parametrize("index", [0, 1, 2])
def test_tamper_each_part(index: int) -> None:
    key = derive_keys(SECRET, b"session")[0]
    parts = sign(b"hello", key).split(".")
    parts[index] = ("A" if parts[index][0] != "A" else "B") + parts[index][1:]
    assert unsign(".".join(parts), [key], None) is None


def test_rotation() -> None:
    old, new = derive_keys(["o" * 32, "n" * 32], b"session")
    assert unsign(sign(b"x", old), [new, old], None) == b"x"
    assert unsign(sign(b"x", old), [new], None) is None


def test_unknown_key() -> None:
    a = derive_keys("a" * 32, b"p")[0]
    b = derive_keys("b" * 32, b"p")[0]
    assert unsign(sign(b"x", a), [b], None) is None


def test_expired_and_future() -> None:
    key = derive_keys(SECRET, b"p")[0]
    value = sign(b"x", key, now=1000)
    assert unsign(value, [key], 10, now=1005) == b"x"
    assert unsign(value, [key], 10, now=1011) is None
    assert unsign(value, [key], 10, now=939) is None
    assert unsign(value, [key], 10, now=1000 - 59) == b"x"


@pytest.mark.parametrize("junk", ["", ".", "..", "a.b.c.d", "é.é.é", "a" * 1_000_000, "." * 1000, "!!.!!.!!"])
def test_garbage(junk: str) -> None:
    key = derive_keys(SECRET, b"p")[0]
    assert unsign(junk, [key], 10) is None


def test_short_secret() -> None:
    with pytest.raises(ValueError, match="token_urlsafe"):
        derive_keys("short", b"p")
    with pytest.raises(ValueError):
        derive_keys([], b"p")
    with pytest.raises(ValueError):
        derive_keys([SECRET, "short"], b"p")


def test_purpose_separation() -> None:
    a = derive_keys(SECRET, b"session")[0]
    b = derive_keys(SECRET, b"flash")[0]
    assert a != b
    assert sign(b"x", a, now=1) != sign(b"x", b, now=1)
