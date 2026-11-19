"""app.static(precompressed=True): serves `.zst`, `.br` and `.gz` siblings."""

from __future__ import annotations

import os

import pytest

from korvex import App
from korvex.middleware import compress
from korvex.testing import TestClient

IMMUTABLE = "public, max-age=31536000, immutable"
BODY = "body{color:red}\n" * 100
VARIANTS = {"zstd": ".zst", "br": ".br", "gzip": ".gz"}


def variant_bytes(encoding: str) -> bytes:
    return f"fake-{encoding}-payload".encode()


@pytest.fixture
def root(tmp_path):
    (tmp_path / "app.css").write_text(BODY, encoding="utf-8")
    for encoding, suffix in VARIANTS.items():
        (tmp_path / f"app.css{suffix}").write_bytes(variant_bytes(encoding))
    return tmp_path


@pytest.fixture
def app(root):
    application = App()
    application.static("/static", root, precompressed=True)
    return application


def pick(app, accept: str | None):
    headers = {} if accept is None else {"Accept-Encoding": accept}
    with TestClient(app) as client:
        return client.get("/static/app.css", headers=headers)


def variant_etag(app, encoding: str) -> str:
    """The ETag the server sends for one variant (checked to be strong and suffixed)."""
    tag = pick(app, encoding).headers["ETag"]
    assert tag.startswith('"')
    assert tag.endswith(f'-{encoding}"')
    return tag


@pytest.mark.parametrize(
    ("accept", "expected"),
    [
        ("zstd", "zstd"),
        ("br", "br"),
        ("gzip", "gzip"),
        ("gzip, br, zstd", "zstd"),
        ("gzip, br", "br"),
        ("gzip;q=1, br;q=0.5", "gzip"),
        ("gzip;q=0.5, br;q=0.9, zstd;q=0.1", "br"),
        ("zstd;q=0.2, gzip;q=0.8", "gzip"),
        ("zstd;q=0, br, gzip", "br"),
        ("zstd;q=0, br;q=0, gzip", "gzip"),
        ("*", "zstd"),
        ("br;q=0, *", "zstd"),
        ("zstd;q=0, *", "br"),
        ("zstd;q=0, br;q=0, *", "gzip"),
        ("gzip;q=0.2, *;q=0.8", "zstd"),
        ("br;q=0.9, *;q=0.5", "br"),
    ],
)
def test_negotiation(app, accept, expected):
    r = pick(app, accept)
    assert r.status == 200
    assert r.headers["Content-Encoding"] == expected
    assert r.body == variant_bytes(expected)


@pytest.mark.parametrize("accept", [None, "", "identity", "deflate", "*;q=0", "zstd;q=0, br;q=0, gzip;q=0"])
def test_plain_when_no_variant_is_acceptable(app, accept):
    r = pick(app, accept)
    assert r.status == 200
    assert "Content-Encoding" not in r.headers
    assert r.body == BODY.encode()
    assert r.headers["Vary"] == "Accept-Encoding"


def test_only_existing_variants_are_offered(root):
    (root / "app.css.zst").unlink()
    (root / "app.css.br").unlink()
    application = App()
    application.static("/static", root, precompressed=True)
    r = pick(application, "zstd, br, gzip")
    assert r.headers["Content-Encoding"] == "gzip"
    assert pick(application, "zstd, br").headers.get("Content-Encoding") is None


def test_variant_headers(app):
    plain = pick(app, None)
    r = pick(app, "br")
    assert r.headers["Content-Type"].startswith("text/css")
    assert r.headers["Content-Length"] == str(len(variant_bytes("br")))
    assert r.headers["ETag"].endswith('-br"')
    assert r.headers["ETag"] != plain.headers["ETag"]
    assert r.headers["ETag"] == pick(app, "br").headers["ETag"]
    assert not r.headers["ETag"].startswith("W/")
    assert "Accept-Ranges" not in r.headers
    assert r.headers["Vary"] == "Accept-Encoding"


def test_each_variant_has_its_own_etag(app):
    plain = pick(app, None).headers["ETag"]
    tags = {variant_etag(app, enc) for enc in VARIANTS}
    assert len(tags | {plain}) == 4


def test_plain_response_keeps_range_support_and_vary(app):
    r = pick(app, None)
    assert r.headers["Content-Type"].startswith("text/css")
    assert r.headers["Accept-Ranges"] == "bytes"
    assert r.headers["Vary"] == "Accept-Encoding"


def test_range_gets_identity_206(app):
    with TestClient(app) as client:
        r = client.get("/static/app.css", headers={"Accept-Encoding": "br", "Range": "bytes=0-3"})
    assert (r.status, r.body) == (206, b"body")
    assert "Content-Encoding" not in r.headers
    assert r.headers["Content-Range"] == f"bytes 0-3/{len(BODY.encode())}"
    assert r.headers["Vary"] == "Accept-Encoding"


def test_if_range_with_variant_etag_gives_full_200(app):
    tag = variant_etag(app, "br")
    with TestClient(app) as client:
        r = client.get(
            "/static/app.css",
            headers={"Accept-Encoding": "br", "Range": "bytes=0-3", "If-Range": tag},
        )
    assert r.status == 200
    assert "Content-Range" not in r.headers
    assert r.headers["Vary"] == "Accept-Encoding"


def test_if_range_with_plain_etag_still_gives_206(app):
    tag = pick(app, None).headers["ETag"]
    with TestClient(app) as client:
        r = client.get(
            "/static/app.css",
            headers={"Accept-Encoding": "br", "Range": "bytes=0-3", "If-Range": tag},
        )
    assert (r.status, r.body) == (206, b"body")


def test_head_matches_get_without_body(app):
    with TestClient(app) as client:
        got = client.get("/static/app.css", headers={"Accept-Encoding": "br"})
        head = client.head("/static/app.css", headers={"Accept-Encoding": "br"})
    assert head.status == 200
    assert head.body == b""
    for name in ("Content-Encoding", "Content-Length", "Content-Type", "ETag", "Vary"):
        assert head.headers[name] == got.headers[name]
    assert "Accept-Ranges" not in head.headers


def test_if_none_match_with_variant_etag_is_304(app):
    tag = variant_etag(app, "br")
    with TestClient(app) as client:
        r = client.get("/static/app.css", headers={"Accept-Encoding": "br", "If-None-Match": tag})
    assert (r.status, r.body) == (304, b"")
    assert r.headers["ETag"] == tag
    assert r.headers["Vary"] == "Accept-Encoding"
    assert "Content-Encoding" not in r.headers


def test_plain_304_has_vary(app):
    tag = pick(app, None).headers["ETag"]
    with TestClient(app) as client:
        r = client.get("/static/app.css", headers={"If-None-Match": tag})
    assert (r.status, r.body) == (304, b"")
    assert r.headers["Vary"] == "Accept-Encoding"


def test_variant_etag_does_not_match_other_encoding(app):
    tag = variant_etag(app, "gzip")
    with TestClient(app) as client:
        r = client.get("/static/app.css", headers={"Accept-Encoding": "br", "If-None-Match": tag})
    assert r.status == 200
    assert r.headers["Content-Encoding"] == "br"


def test_stale_variant_is_ignored(root, app):
    original = (root / "app.css").stat().st_mtime
    for suffix in VARIANTS.values():
        os.utime(root / f"app.css{suffix}", (original - 100, original - 100))
    r = pick(app, "zstd, br, gzip")
    assert r.status == 200
    assert "Content-Encoding" not in r.headers
    assert r.body == BODY.encode()
    assert r.headers["Vary"] == "Accept-Encoding"


def test_only_the_stale_variant_is_skipped(root, app):
    original = (root / "app.css").stat().st_mtime
    os.utime(root / "app.css.zst", (original - 100, original - 100))
    r = pick(app, "zstd, br, gzip")
    assert r.headers["Content-Encoding"] == "br"


def test_symlink_variant_outside_root_is_ignored(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.br"
    secret.write_bytes(b"secret")
    root = tmp_path / "root"
    root.mkdir()
    (root / "app.css").write_text(BODY, encoding="utf-8")
    try:
        (root / "app.css.br").symlink_to(secret)
    except OSError:
        pytest.skip("symlinks not supported")
    application = App()
    application.static("/static", root, precompressed=True)
    r = pick(application, "br")
    assert r.status == 200
    assert "Content-Encoding" not in r.headers
    assert r.body == BODY.encode()


def test_variant_file_is_a_plain_download(app):
    with TestClient(app) as client:
        r = client.get("/static/app.css.br", headers={"Accept-Encoding": "br"})
    assert r.status == 200
    assert r.headers["Content-Type"].startswith("application/octet-stream")
    assert "Content-Encoding" not in r.headers
    assert r.body == variant_bytes("br")


def test_compress_middleware_does_not_double_compress(app):
    compress(app)
    r = pick(app, "gzip, br, zstd")
    assert r.headers["Content-Encoding"] == "zstd"
    assert r.body == variant_bytes("zstd")
    assert r.headers["ETag"].endswith('-zstd"')
    assert not r.headers["ETag"].startswith("W/")
    assert r.headers["Vary"] == "Accept-Encoding"


def test_compress_middleware_still_compresses_without_variant(root):
    (root / "plain.css").write_text(BODY, encoding="utf-8")
    application = App()
    application.static("/static", root, precompressed=True)
    compress(application)
    with TestClient(application) as client:
        r = client.get("/static/plain.css", headers={"Accept-Encoding": "gzip"})
    assert r.headers["Content-Encoding"] == "gzip"
    assert len(r.body) < len(BODY)


def test_hashed_url_serves_variant_as_immutable(app):
    url = app.static_url("app.css")
    with TestClient(app) as client:
        r = client.get(url, headers={"Accept-Encoding": "br"})
    assert r.status == 200
    assert r.headers["Content-Encoding"] == "br"
    assert r.body == variant_bytes("br")
    assert r.headers["Cache-Control"] == IMMUTABLE
    assert r.headers["Vary"] == "Accept-Encoding"


def test_default_ignores_siblings_and_has_no_vary(root):
    application = App()
    application.static("/static", root)
    r = pick(application, "zstd, br, gzip")
    assert r.status == 200
    assert "Content-Encoding" not in r.headers
    assert "Vary" not in r.headers
    assert r.body == BODY.encode()


def test_explicit_false_ignores_siblings_and_has_no_vary(root):
    application = App()
    application.static("/static", root, precompressed=False)
    r = pick(application, "br")
    assert "Content-Encoding" not in r.headers
    assert "Vary" not in r.headers


def test_missing_file_is_404(app):
    with TestClient(app) as client:
        r = client.get("/static/nope.css", headers={"Accept-Encoding": "br"})
    assert r.status == 404
