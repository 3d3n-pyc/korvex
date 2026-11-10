"""Tests for templates wired into `App`."""

from __future__ import annotations

from pathlib import Path

import pytest

from korvex import App, Response, Templates
from korvex.testing import TestClient


def make_app(tmp_path: Path, files: dict) -> tuple[App, Templates]:
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    app = App()
    return app, app.templates(tmp_path)


def test_page_is_escaped_html(tmp_path):
    app, templates = make_app(tmp_path, {"hi.html": "<p>{{ who }}</p>"})

    @app.get("/")
    def index(request):
        return templates.response("hi.html", who="<b>Ada</b>")

    with TestClient(app) as client:
        response = client.get("/")
    assert response.status == 200
    assert response.headers["Content-Type"] == "text/html; charset=utf-8"
    assert response.text == "<p>&lt;b&gt;Ada&lt;/b&gt;</p>"


def test_extends_include_and_url_for(tmp_path):
    app, templates = make_app(
        tmp_path,
        {
            "base.html": "[{% block body %}{% endblock %}]",
            "nav.html": "<a href='{{ url_for('index') }}'>home</a>",
            "page.html": "{% extends 'base.html' %}{% block body %}{% include 'nav.html' %}{% endblock %}",
        },
    )

    @app.get("/", name="index")
    def index(request):
        return templates.response("page.html")

    with TestClient(app) as client:
        assert client.get("/").text == "[<a href='/'>home</a>]"


def test_undefined_variable_is_a_500_with_the_line(tmp_path):
    app, templates = make_app(tmp_path, {"bad.html": "a\n{{ nope }}"})

    @app.get("/")
    def index(request):
        return templates.response("bad.html")

    with TestClient(app) as client, pytest.raises(Exception, match=r"bad.html, line 2: 'nope' is undefined"):
        client.get("/")
    with TestClient(app, raise_server_errors=False) as client:
        assert client.get("/").status == 500


def test_status_and_headers(tmp_path):
    app, templates = make_app(tmp_path, {"a.html": "x"})

    @app.get("/")
    def index(request):
        return templates.response("a.html", status=201, headers={"X-A": "1"})

    @app.get("/t")
    def typed(request):
        return templates.response("a.html", headers={"Content-Type": "text/plain"})

    with TestClient(app) as client:
        response = client.get("/")
        assert (response.status, response.headers["X-A"]) == (201, "1")
        assert client.get("/t").headers["Content-Type"] == "text/plain"


IMMUTABLE = "public, max-age=31536000, immutable"


def static_app(tmp_path: Path) -> tuple[App, Templates]:
    (tmp_path / "css").mkdir()
    (tmp_path / "css" / "app.css").write_text("body{}", encoding="utf-8")
    app, templates = make_app(tmp_path, {"p.html": "{{ static_url('app.css') }}"})
    app.static("/static", tmp_path / "css")
    return app, templates


def test_static_url_is_hashed_and_served_immutable(tmp_path):
    app, templates = static_app(tmp_path)
    url = templates.render("p.html")
    assert url.startswith("/static/") and url.endswith(".css")
    with TestClient(app) as client:
        hashed = client.get(url)
        assert (hashed.status, hashed.text) == (200, "body{}")
        assert hashed.headers["Cache-Control"] == IMMUTABLE
        assert "Cache-Control" not in client.get("/static/app.css").headers


def test_stale_hash_is_served_without_immutable(tmp_path):
    app, _ = static_app(tmp_path)
    with TestClient(app) as client:
        response = client.get("/static/app.00000000.css")
    assert response.status == 200
    assert "Cache-Control" not in response.headers


def test_edited_file_gets_a_new_url(tmp_path):
    app, _ = static_app(tmp_path)
    first = app.static_url("app.css")
    (tmp_path / "css" / "app.css").write_text("body{color:red}", encoding="utf-8")
    assert app.static_url("app.css") != first


def test_real_file_named_like_a_hash_is_served_as_is(tmp_path):
    app, _ = static_app(tmp_path)
    (tmp_path / "css" / "app.12345678.css").write_text("real", encoding="utf-8")
    with TestClient(app) as client:
        assert client.get("/static/app.12345678.css").text == "real"


def test_two_mounts_picked_by_name(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "x.js").write_text("a", encoding="utf-8")
    (tmp_path / "b" / "x.js").write_text("b", encoding="utf-8")
    app = App()
    app.static("/static", tmp_path / "a")
    app.static("/assets", tmp_path / "b", name="assets")
    assert app.static_url("x.js").startswith("/static/x.")
    assert app.static_url("x.js", name="assets").startswith("/assets/x.")
    with pytest.raises(LookupError):
        app.static_url("x.js", name="nope")


def test_missing_static_file_is_an_error(tmp_path):
    app, _ = static_app(tmp_path)
    with pytest.raises(FileNotFoundError):
        app.static_url("nope.css")


@pytest.mark.parametrize("path", ["../x.12345678.css", "%2e%2e/x.12345678.css", "..%2Fx.12345678.css"])
def test_hash_like_traversal_is_404(tmp_path, path):
    app, _ = static_app(tmp_path)
    (tmp_path / "x.css").write_text("secret", encoding="utf-8")
    with TestClient(app) as client:
        assert client.get(f"/static/{path}").status == 404


@pytest.mark.parametrize("path", ["LICENSE", ".well", "dir.v2/README", "dir.v2/a.b.js"])
def test_static_url_round_trips_for_any_name(tmp_path, path):
    root = tmp_path / "files"
    (root / path).parent.mkdir(parents=True, exist_ok=True)
    (root / path).write_text("data", encoding="utf-8")
    app = App()
    app.static("/static", root)
    with TestClient(app) as client:
        response = client.get(app.static_url(path))
        assert (response.status, response.text) == (200, "data")
        assert response.headers["Cache-Control"] == IMMUTABLE


def test_flashed_messages_show_once(tmp_path):
    from korvex.middleware import flash, sessions

    page = "{% for category, message in get_flashed_messages() %}{{ category }}:{{ message }};{% endfor %}"
    app, templates = make_app(tmp_path, {"page.html": page})
    sessions(app, "k" * 32, secure=False)

    @app.post("/go")
    def go(request):
        flash("saved", "ok")
        return Response.redirect("/", status=303)

    @app.get("/")
    def index(request):
        return templates.response("page.html")

    with TestClient(app) as client:
        client.post("/go")
        assert client.get("/").text == "ok:saved;"
        assert client.get("/").text == ""
