"""Tests for the conditional and range request helpers."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from korvex import Headers
from korvex._conditional import (
    UNSATISFIABLE,
    byte_range,
    etag,
    last_modified,
    not_modified,
)

MTIME = 1_700_000_000.0
TAG = etag(int(MTIME) * 10**9, 100)
DATE = last_modified(MTIME)


def h(**items: str) -> Headers:
    return Headers([(k.replace("_", "-"), v) for k, v in items.items()])


def rng(value: str, size: int = 100, method: str = "GET", **extra: str):
    return byte_range(method, h(range=value, **extra), TAG, MTIME, size)


def test_etag_and_date_format():
    assert etag(255, 16) == '"ff-10"'
    assert last_modified(0) == "Thu, 01 Jan 1970 00:00:00 GMT"


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_if_none_match_hit(method):
    assert not_modified(method, h(if_none_match=TAG), TAG, MTIME)


def test_if_none_match_other_methods_ignored():
    assert not not_modified("POST", h(if_none_match=TAG), TAG, MTIME)


@pytest.mark.parametrize(
    "value",
    ["*", f"W/{TAG}", f'"a", {TAG}', f'"a" ,W/{TAG} , "b"'],
)
def test_if_none_match_forms_match(value):
    assert not_modified("GET", h(if_none_match=value), TAG, MTIME)


def test_if_none_match_miss_and_priority_over_date():
    headers = h(if_none_match='"zz"', if_modified_since=DATE)
    assert not not_modified("GET", headers, TAG, MTIME)


def test_if_modified_since():
    assert not_modified("GET", h(if_modified_since=DATE), TAG, MTIME)
    assert not_modified("GET", h(if_modified_since=last_modified(MTIME + 60)), TAG, MTIME)
    assert not not_modified("GET", h(if_modified_since=last_modified(MTIME - 60)), TAG, MTIME)
    assert not not_modified("GET", h(), TAG, MTIME)


@pytest.mark.parametrize("value", ["", "garbage", "Thu, 99 Foo 2020", "0", "\x00"])
def test_if_modified_since_bad_dates_ignored(value):
    assert not not_modified("GET", h(if_modified_since=value), TAG, MTIME)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("bytes=0-9", (0, 9)),
        ("bytes=10-", (10, 99)),
        ("bytes=-10", (90, 99)),
        ("bytes=0-0", (0, 0)),
        ("bytes=99-99", (99, 99)),
        ("bytes=90-500", (90, 99)),
        ("bytes=-500", (0, 99)),
        ("bytes=-100", (0, 99)),
        ("BYTES=1-2", (1, 2)),
        (" bytes = 1-2 ", (1, 2)),
        ("bytes= 1-2", (1, 2)),
    ],
)
def test_valid_ranges(value, expected):
    assert rng(value) == expected


@pytest.mark.parametrize("value", ["bytes=100-", "bytes=100-200", "bytes=-0", "bytes=500-"])
def test_unsatisfiable(value):
    assert rng(value) == UNSATISFIABLE


def test_empty_file_is_unsatisfiable():
    assert rng("bytes=0-", size=0) == UNSATISFIABLE
    assert rng("bytes=-5", size=0) == UNSATISFIABLE


@pytest.mark.parametrize(
    "value",
    [
        "bytes=",
        "bytes=-",
        "bytes=5-3",
        "bytes=0-1,5-6",
        "bytes=1 -2",
        "bytes=a-b",
        "bytes=+1-2",
        "bytes=1-2-3",
        "items=0-1",
        "0-1",
        "",
        "bytes=" + "9" * 20 + "-",
        "bytes=0-" + "9" * 20,
        "bytes=-" + "9" * 20,
        "bytes=0-" + "1" * 300,
        "bytes=١-٢",
    ],
)
def test_invalid_ranges_serve_whole_file(value):
    assert rng(value) is None


def test_nineteen_digits_allowed():
    assert rng("bytes=" + "9" * 19 + "-") == UNSATISFIABLE
    assert rng("bytes=0-" + "9" * 19) == (0, 99)


def test_only_get_and_header_needed():
    assert rng("bytes=0-1", method="HEAD") is None
    assert rng("bytes=0-1", method="POST") is None
    assert byte_range("GET", h(), TAG, MTIME, 100) is None


def test_if_range_etag_and_date():
    assert rng("bytes=0-1", if_range=TAG) == (0, 1)
    assert rng("bytes=0-1", if_range=DATE) == (0, 1)


@pytest.mark.parametrize("value", ['"other"', f"W/{TAG}", last_modified(MTIME + 1), "", "x"])
def test_if_range_mismatch_serves_whole_file(value):
    assert rng("bytes=0-1", if_range=value) is None
    assert rng("bytes=500-", if_range=value) is None


@given(st.text(max_size=300), st.integers(min_value=0, max_value=10**6))
def test_byte_range_never_raises_and_stays_in_bounds(value, size):
    result = byte_range("GET", h(range=value), TAG, MTIME, size)
    if isinstance(result, tuple):
        start, end = result
        assert 0 <= start <= end <= size - 1


@given(
    st.integers(0, 10**21),
    st.integers(0, 10**21),
    st.integers(0, 1000),
    st.sampled_from(["bytes={a}-{b}", "bytes={a}-", "bytes=-{b}"]),
)
def test_structured_ranges_stay_in_bounds(a, b, size, template):
    result = rng(template.format(a=a, b=b), size=size)
    if isinstance(result, tuple):
        assert 0 <= result[0] <= result[1] <= size - 1
