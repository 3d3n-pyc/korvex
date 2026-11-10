"""Response.file(): conditional and range requests."""

from __future__ import annotations

from email.utils import formatdate

import pytest

from korvex import App, Response
from korvex.testing import TestClient

DATA = bytes(range(256)) * 4  # 1024 bytes


@pytest.fixture
def client(tmp_path):
    path = tmp_path / "data.bin"
    path.write_bytes(DATA)
    app = App()

    @app.get("/plain")
    def plain(request):
        return Response.file(path)

    @app.get("/cond")
    def cond(request):
        return Response.file(path, request=request, headers={"Cache-Control": "no-cache"})

    with TestClient(app) as test_client:
        test_client.mtime = path.stat().st_mtime
        yield test_client


def test_200_has_validators(client):
    r = client.get("/cond")
    assert (r.status, r.body) == (200, DATA)
    assert r.headers["ETag"].startswith('"')
    assert r.headers["Last-Modified"]
    assert r.headers["Accept-Ranges"] == "bytes"


def test_no_request_is_unchanged(client):
    r = client.get("/plain", headers={"Range": "bytes=0-1"})
    assert (r.status, r.body) == (200, DATA)
    assert "ETag" not in r.headers and "Accept-Ranges" not in r.headers


def test_304_by_etag(client):
    first = client.get("/cond")
    r = client.get("/cond", headers={"If-None-Match": first.headers["ETag"]})
    assert (r.status, r.body) == (304, b"")
    assert r.headers["ETag"] == first.headers["ETag"]
    assert r.headers["Last-Modified"] == first.headers["Last-Modified"]
    assert r.headers["Cache-Control"] == "no-cache"
    assert "Content-Type" not in r.headers and "Content-Length" not in r.headers


def test_304_by_date_and_head(client):
    date = formatdate(client.mtime + 10, usegmt=True)
    assert client.get("/cond", headers={"If-Modified-Since": date}).status == 304
    assert client.head("/cond", headers={"If-Modified-Since": date}).status == 304
    old = formatdate(client.mtime - 1000, usegmt=True)
    assert client.get("/cond", headers={"If-Modified-Since": old}).status == 200


def test_other_etag_is_200(client):
    assert client.get("/cond", headers={"If-None-Match": '"nope"'}).status == 200


@pytest.mark.parametrize(
    ("spec", "first", "last"),
    [("0-9", 0, 9), ("1000-", 1000, 1023), ("-24", 1000, 1023), ("10-5000", 10, 1023)],
)
def test_range_forms(client, spec, first, last):
    r = client.get("/cond", headers={"Range": f"bytes={spec}"})
    assert r.status == 206
    assert r.body == DATA[first : last + 1]
    assert r.headers["Content-Range"] == f"bytes {first}-{last}/1024"
    assert r.headers["Content-Length"] == str(last - first + 1)


def test_if_range(client):
    tag = client.get("/cond").headers["ETag"]
    assert client.get("/cond", headers={"Range": "bytes=0-3", "If-Range": tag}).status == 206
    assert client.get("/cond", headers={"Range": "bytes=0-3", "If-Range": '"old"'}).status == 200


def test_unsatisfiable(client):
    r = client.get("/cond", headers={"Range": "bytes=2000-"})
    assert (r.status, r.body) == (416, b"")
    assert r.headers["Content-Range"] == "bytes */1024"


def test_multiple_ranges_give_the_whole_file(client):
    r = client.get("/cond", headers={"Range": "bytes=0-1,5-6"})
    assert (r.status, r.body) == (200, DATA)


def test_range_spans_chunks(tmp_path, monkeypatch):
    import korvex._response as module

    monkeypatch.setattr(module, "FILE_CHUNK_SIZE", 7)
    path = tmp_path / "d.bin"
    path.write_bytes(DATA)
    app = App()
    app.get("/f")(lambda request: Response.file(path, request=request))
    with TestClient(app) as c:
        assert c.get("/f", headers={"Range": "bytes=3-100"}).body == DATA[3:101]


def test_truncated_file_is_logged(tmp_path, caplog):
    path = tmp_path / "t.bin"
    path.write_bytes(DATA)
    app = App()

    def handler(request):
        response = Response.file(path, request=request)
        path.write_bytes(DATA[:3])  # truncated after the stat
        return response

    app.get("/t")(handler)
    with caplog.at_level("WARNING", logger="korvex"), TestClient(app) as c:
        c.get("/t")
    assert any(path.name in m and f"expected {len(DATA)}" in m and "sent 3" in m for m in caplog.messages)
