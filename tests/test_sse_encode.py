from __future__ import annotations

import asyncio
import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from korvex._sse import PING, ServerSentEvent, encode, parse, with_pings


def test_fields() -> None:
    sse = ServerSentEvent("hi", event="e", id="7", retry=1500)
    assert encode(sse) == b"event: e\nid: 7\nretry: 1500\ndata: hi\n\n"


def test_no_data() -> None:
    assert encode(ServerSentEvent(event="x")) == b"event: x\n\n"


def test_json_data() -> None:
    assert encode({"a": [1, 2]}) == b'data: {"a": [1, 2]}\n\n'
    assert parse(encode([1, 2]).decode()) == [ServerSentEvent("[1, 2]")]


def test_multiline_data() -> None:
    assert encode("a\r\nb\rc\nd") == b"data: a\ndata: b\ndata: c\ndata: d\n\n"
    assert parse(encode("a\nb").decode()) == [ServerSentEvent("a\nb")]


@pytest.mark.parametrize(
    "kwargs",
    [{"event": "a\nb"}, {"event": "a\rb"}, {"id": "a\nb"}, {"id": "a\rb"}, {"id": "a\0b"}, {"event": 1}],
)
def test_injection(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        ServerSentEvent("x", **kwargs)


@pytest.mark.parametrize("retry", [-1, 1.5, "10", True])
def test_retry_invalid(retry: object) -> None:
    with pytest.raises(ValueError):
        ServerSentEvent("x", retry=retry)  # type: ignore[arg-type]


def test_retry_zero_and_repr_eq() -> None:
    sse = ServerSentEvent("x", retry=0)
    assert b"retry: 0\n" in encode(sse)
    assert repr(sse) == "ServerSentEvent(data='x', retry=0)"
    assert sse == ServerSentEvent("x", retry=0)
    assert sse != ServerSentEvent("x")


def test_parse_rules() -> None:
    text = ": comment\nevent:  a\ndata:x\ndata: y\nretry: 5\nid: 1\n\ndata\n\n"
    assert parse(text) == [
        ServerSentEvent("x\ny", event=" a", id="1", retry=5),
        ServerSentEvent(""),
    ]


@given(st.text())
def test_round_trip(data: str) -> None:
    [event] = parse(encode(data).decode())
    assert event.data == re.sub(r"\r\n|\r", "\n", data)


async def _slow(log: list) -> object:
    try:
        await asyncio.sleep(0.12)
        yield "late"
        await asyncio.sleep(10)
    finally:
        log.append("closed")


def test_with_pings_and_aclose() -> None:
    asyncio.run(_pings_and_aclose())


async def _pings_and_aclose() -> None:
    log: list = []
    stream = with_pings(_slow(log), 0.03)
    out = []
    async for chunk in stream:
        out.append(chunk)
        if chunk != PING:
            break
    await stream.aclose()  # type: ignore[attr-defined]
    assert out.count(PING) >= 2
    assert out[-1] == b"data: late\n\n"
    assert log == ["closed"]


def test_with_pings_finishes_and_no_pings() -> None:
    asyncio.run(_finishes_and_no_pings())


async def _finishes_and_no_pings() -> None:
    async def gen() -> object:
        yield 1
        yield "b"

    assert [c async for c in with_pings(gen(), 0)] == [b"data: 1\n\n", b"data: b\n\n"]


def test_bytes_data_fails_at_construction() -> None:
    with pytest.raises(TypeError, match="decode it first"):
        ServerSentEvent(b"x")
    with pytest.raises(TypeError):
        encode(b"x")


def test_surrogate_fails_at_construction() -> None:
    with pytest.raises(ValueError, match="surrogate"):
        ServerSentEvent("\ud800")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), {"a": float("-inf")}])
def test_nan_fails_at_construction(value: object) -> None:
    with pytest.raises(ValueError):
        ServerSentEvent(value)


def test_parse_strips_bom() -> None:
    assert parse("﻿data: a\n\n") == [ServerSentEvent("a")]


def test_parse_ignores_id_with_nul() -> None:
    assert parse("id: a\0b\ndata: x\n\n") == [ServerSentEvent("x")]
