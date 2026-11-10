"""Tests for the ASGI `root_path`: routing, `request.path`, and links."""

from __future__ import annotations

from support import make_scope, response_from, run_app, run_ws, ws_scope

from korvex import App, Response


def get(app, path, root="/api"):
    scope = make_scope(path=path, headers=[("host", "example.com")])
    scope["root_path"] = root
    return response_from(run_app(app, scope))


def make_app():
    app = App()

    @app.get("/", name="index")
    def index(request) -> Response:
        return Response.text(
            f"{request.path}|{request.root_path}|{request.url_for('item', id=3)}|{app.url_for('item', id=3)}"
        )

    @app.get("/items/{id}", name="item")
    def item(id: str) -> Response:
        return Response.text(id)

    return app


def test_routes_under_root_path():
    app = make_app()
    status, _, body = get(app, "/api/items/7")
    assert (status, body) == (200, b"7")


def test_exact_prefix_is_root():
    status, _, body = get(make_app(), "/api")
    assert status == 200
    assert body == b"/|/api|http://example.com/api/items/3|/api/items/3"


def test_prefix_needs_segment_boundary():
    assert get(make_app(), "/api2/items/7")[0] == 404


def test_old_server_path_without_prefix():
    status, _, body = get(make_app(), "/items/7")
    assert (status, body) == (200, b"7")


def test_no_root_path():
    status, _, body = get(make_app(), "/", root="")
    assert body == b"/||http://example.com/items/3|/items/3"


def test_template_links(tmp_path):
    (tmp_path / "s").mkdir()
    (tmp_path / "s" / "a.css").write_text("x")
    (tmp_path / "t.html").write_text("{{ url_for('item', id=1) }} {{ static_url('a.css') }}")
    app = make_app()
    templates = app.templates(tmp_path)
    app.static("/static", tmp_path / "s")

    @app.get("/page")
    def page(request):
        return templates.response("t.html")

    _, _, body = get(app, "/api/page")
    assert body.decode().startswith("/api/items/1 /api/static/a.")


def test_websocket_under_root_path():
    app = App()

    @app.websocket("/ws")
    async def ws(websocket):
        await websocket.accept()
        await websocket.send_text(f"{websocket.path}|{websocket.root_path}")

    scope = ws_scope("/api/ws")
    scope["root_path"] = "/api"
    sent = run_ws(app, scope)
    assert sent[0]["type"] == "websocket.accept"
    assert sent[1]["text"] == "/ws|/api"


def test_root_path_with_trailing_slash():
    app = make_app()
    status, _, body = get(app, "/api/", root="/api/")
    assert (status, body) == (200, b"/|/api|http://example.com/api/items/3|/api/items/3")
    assert get(app, "/api/items/7", root="/api/")[2] == b"7"
