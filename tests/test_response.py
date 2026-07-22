"""Tests for the pure-Python korvex.Headers / korvex.Response helpers."""

import pytest

from korvex import Headers, Response


def test_headers_get_is_case_insensitive():
    headers = Headers([("Content-Type", "text/plain")])

    assert headers.get("content-type") == "text/plain"
    assert headers.get("CONTENT-TYPE") == "text/plain"


def test_headers_get_returns_default_when_missing():
    headers = Headers()

    assert headers.get("x-missing") is None
    assert headers.get("x-missing", "fallback") == "fallback"


def test_headers_set_replaces_all_existing_values():
    headers = Headers([("X-Tag", "a"), ("X-Tag", "b")])
    headers.set("X-Tag", "c")

    assert headers.get_all("X-Tag") == ["c"]


def test_headers_add_preserves_repeated_values():
    headers = Headers()
    headers.add("Set-Cookie", "a=1")
    headers.add("Set-Cookie", "b=2")

    assert headers.get_all("Set-Cookie") == ["a=1", "b=2"]
    assert headers.get("Set-Cookie") == "a=1"


def test_headers_dunder_item_access():
    headers = Headers()
    headers["X-Custom"] = "value"

    assert headers["x-custom"] == "value"
    assert "X-CUSTOM" in headers

    del headers["x-custom"]
    assert "X-Custom" not in headers
    with pytest.raises(KeyError):
        headers["X-Custom"]


def test_headers_preserves_insertion_order():
    headers = Headers([("A", "1"), ("B", "2"), ("C", "3")])

    assert headers.items() == [("A", "1"), ("B", "2"), ("C", "3")]


def test_headers_len_and_iteration():
    headers = Headers([("A", "1"), ("B", "2")])

    assert len(headers) == 2
    assert list(headers) == [("A", "1"), ("B", "2")]


def test_response_defaults_to_200_and_empty_body():
    response = Response()

    assert response.status == 200
    assert response.body == b""
    assert len(response.headers) == 0


def test_response_encodes_str_body_to_bytes():
    response = Response("hello")

    assert response.body == b"hello"


def test_response_accepts_bytes_body_unchanged():
    response = Response(b"\x00\x01")

    assert response.body == b"\x00\x01"


def test_response_text_sets_default_content_type():
    response = Response.text("hi", status=201)

    assert response.status == 201
    assert response.body == b"hi"
    assert response.headers["Content-Type"] == "text/plain; charset=utf-8"


def test_response_text_respects_explicit_content_type():
    response = Response.text("<p>hi</p>", headers=[("Content-Type", "text/html")])

    assert response.headers["Content-Type"] == "text/html"


def test_response_json_serializes_and_sets_content_type():
    response = Response.json({"id": 42})

    assert response.body == b'{"id": 42}'
    assert response.headers["Content-Type"] == "application/json"


def test_response_redirect_sets_location_and_default_status():
    response = Response.redirect("/login")

    assert response.status == 302
    assert response.headers["Location"] == "/login"
    assert response.body == b""


def test_response_redirect_accepts_custom_status():
    response = Response.redirect("/new-path", status=301)

    assert response.status == 301


def test_response_repr_is_informative():
    response = Response.text("hi", status=404)

    assert "404" in repr(response)
    assert "hi" in repr(response) or "b'hi'" in repr(response)


def test_headers_setdefault_sets_when_missing():
    headers = Headers()
    result = headers.setdefault("Content-Type", "text/plain")

    assert result == "text/plain"
    assert headers["Content-Type"] == "text/plain"


def test_headers_setdefault_leaves_existing_value_untouched():
    headers = Headers([("Content-Type", "application/json")])
    result = headers.setdefault("content-type", "text/plain")

    assert result == "application/json"
    assert headers["Content-Type"] == "application/json"


def test_headers_equality_compares_by_content():
    assert Headers([("A", "1")]) == Headers([("A", "1")])
    assert Headers([("A", "1")]) != Headers([("A", "2")])
    assert Headers() != "not a Headers instance"


def test_response_equality_compares_status_headers_and_body():
    a = Response.json({"id": 1})
    b = Response.json({"id": 1})
    c = Response.json({"id": 2})

    assert a == b
    assert a != c
    assert a != "not a Response instance"
