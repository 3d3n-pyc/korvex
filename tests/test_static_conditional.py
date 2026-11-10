"""app.static(): revalidation, ranges and compression."""

from __future__ import annotations

import pytest

from korvex import App
from korvex.middleware import compress
from korvex.testing import TestClient

IMMUTABLE = "public, max-age=31536000, immutable"
BODY = "body{color:red}\n" * 100


@pytest.fixture
def app(tmp_path):
    (tmp_path / "app.css").write_text(BODY, encoding="utf-8")
    application = App()
    application.static("/static", tmp_path)
    return application


def test_plain_url_revalidates(app):
    with TestClient(app) as client:
        first = client.get("/static/app.css")
        assert first.headers["Cache-Control"] == "no-cache"
        second = client.get("/static/app.css", headers={"If-None-Match": first.headers["ETag"]})
    assert (second.status, second.body) == (304, b"")
    assert second.headers["Cache-Control"] == "no-cache"


def test_range(app):
    with TestClient(app) as client:
        r = client.get("/static/app.css", headers={"Range": "bytes=0-3"})
    assert (r.status, r.body) == (206, b"body")


def test_hashed_url_is_immutable_with_etag(app):
    url = app.static_url("app.css")
    with TestClient(app) as client:
        r = client.get(url)
    assert r.headers["Cache-Control"] == IMMUTABLE
    assert "ETag" in r.headers


def test_compress_weakens_etag_and_skips_206_304(app):
    compress(app)
    with TestClient(app) as client:
        full = client.get("/static/app.css", headers={"Accept-Encoding": "gzip"})
        assert full.headers["Content-Encoding"] == "gzip"
        assert full.headers["ETag"].startswith("W/")
        plain = client.get("/static/app.css").headers["ETag"]
        part = client.get("/static/app.css", headers={"Accept-Encoding": "gzip", "Range": "bytes=0-3"})
        assert (part.status, part.body) == (206, b"body")
        assert "Content-Encoding" not in part.headers
        cached = client.get("/static/app.css", headers={"Accept-Encoding": "gzip", "If-None-Match": plain})
        assert cached.status == 304 and "Content-Encoding" not in cached.headers


def test_compress_drops_accept_ranges_and_304_varies(app):
    compress(app)
    with TestClient(app) as client:
        full = client.get("/static/app.css", headers={"Accept-Encoding": "gzip"})
        assert "Accept-Ranges" not in full.headers
        tag = client.get("/static/app.css").headers["ETag"]
        cached = client.get("/static/app.css", headers={"Accept-Encoding": "gzip", "If-None-Match": tag})
    assert cached.status == 304 and cached.headers["Vary"] == "Accept-Encoding"
