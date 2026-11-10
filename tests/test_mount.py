"""app.mount(): another ASGI app under a prefix."""

from __future__ import annotations

import asyncio
import json

import pytest

from korvex import App, Response, WebSocket, WebSocketDisconnect
from korvex.middleware import csrf, rate_limit
from korvex.testing import TestClient


def raw_app(seen: list):
    """A bare ASGI app that records its scope and body and answers 200."""

    async def asgi(scope, receive, send):
        if scope["type"] == "lifespan":
            raise NotImplementedError
        body = b""
        more = True
        while more:
            message = await receive()
            body += message.get("body", b"")
            more = message.get("more_body", False)
        seen.append((scope, body))
        await send({"type": "http.response.start", "status": 200, "headers": [(b"x-sub", b"1")]})
        await send({"type": "http.response.body", "body": b"sub"})

    return asgi


def test_raw_app_sees_its_scope_and_body():
    seen: list = []
    app = App()
    app.mount("/sub/", raw_app(seen))
    with TestClient(app) as client:
        r = client.post("/sub/a%20b/c?x=1&y=2", headers={"X-Test": "yes"}, body=b"payload")
    assert (r.status, r.body, r.headers["x-sub"]) == (200, b"sub", "1")
    scope, body = seen[0]
    assert (scope["type"], scope["method"]) == ("http", "POST")
    assert scope["path"] == "/sub/a b/c"
    assert scope["root_path"] == "/sub"
    assert scope["raw_path"] == b"/sub/a%20b/c"
    assert scope["query_string"] == b"x=1&y=2"
    assert (b"x-test", b"yes") in scope["headers"]
    assert body == b"payload"


async def call_under_api(app, path, query=b""):
    """Calls the app with ASGI `root_path="/api"`; returns the response messages."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api" + path,
        "raw_path": b"/api" + path.encode(),
        "root_path": "/api",
        "query_string": query,
        "headers": [],
        "scheme": "http",
    }
    sent: list = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    return sent


def test_root_path_is_kept_in_front_of_the_prefix():
    seen: list = []
    app = App()
    app.mount("/sub", raw_app(seen))
    asyncio.run(call_under_api(app, "/sub/x"))
    scope = seen[0][0]
    assert (scope["root_path"], scope["path"], scope["raw_path"]) == ("/api/sub", "/api/sub/x", b"/api/sub/x")


def test_mounted_korvex_app_has_its_own_routes_hooks_and_url_for():
    admin = App()

    ran: list = []

    @admin.before_request
    def stamp(request):
        ran.append(request.path)  # a hook of the sub-app runs, on the path it sees

    @admin.get("/users/{id}", name="user")
    def user(request, id):
        return Response.text(f"{id} {admin.url_for('user', id=id)}")

    app = App()
    app.mount("/admin", admin)
    with TestClient(app) as client:
        assert client.get("/admin/users/7").text == "7 /admin/users/7"
        assert client.get("/admin/nothing").status == 404
    assert ran == ["/users/7", "/nothing"]


def test_bare_prefix_redirects_keeping_the_query():
    app = App()
    app.mount("/sub", raw_app([]))
    with TestClient(app) as client:
        r = client.get("/sub?a=1&b=%20x")
        post = client.post("/sub")
    assert (r.status, r.headers["Location"]) == (308, "/sub/?a=1&b=%20x")
    assert post.status == 308


def test_bare_prefix_redirect_starts_with_root_path():
    app = App()
    app.mount("/sub", raw_app([]))
    sent = asyncio.run(call_under_api(app, "/sub", b"a=1"))
    assert sent[0]["status"] == 308
    assert (b"Location", b"/api/sub/?a=1") in sent[0]["headers"]


def test_bare_prefix_redirect_cannot_inject_headers():
    app = App()
    app.mount("/sub", raw_app([]))
    with TestClient(app) as client:
        r = client.get("/sub", query="a=1\r\nX: y")
    assert "\r" not in r.headers["Location"] and "\n" not in r.headers["Location"]


def test_a_parent_route_wins():
    app = App()
    app.mount("/sub", raw_app([]))

    @app.get("/sub/special")
    def special():
        return Response.text("parent")

    with TestClient(app) as client:
        assert client.get("/sub/special").text == "parent"
        assert client.get("/sub/other").text == "sub"


def test_parent_csrf_protects_the_mount():
    seen: list = []
    app = App()
    csrf(app)
    app.mount("/sub", raw_app(seen))
    with TestClient(app) as client:
        refused = client.post("/sub/x", headers={"Sec-Fetch-Site": "cross-site"})
        allowed = client.post("/sub/x", headers={"Sec-Fetch-Site": "same-origin"})
    assert (refused.status, allowed.status) == (403, 200)
    assert len(seen) == 1


def test_parent_rate_limit_counts_the_mount():
    app = App()
    rate_limit(app, requests=2, per=60)
    app.mount("/sub", raw_app([]))
    with TestClient(app) as client:
        assert [client.get("/sub/x").status for _ in range(3)] == [200, 200, 429]


def test_a_body_read_by_a_hook_is_replayed():
    seen: list = []
    app = App()

    @app.before_request
    async def read(request):
        assert await request.body() == b"abc"

    app.mount("/sub", raw_app(seen))
    with TestClient(app) as client:
        client.post("/sub/x", body=b"abc")
    assert seen[0][1] == b"abc"


def test_parent_after_hooks_and_error_handlers_do_not_run_for_a_mount():
    app = App()
    calls: list = []

    @app.after_request
    def after(request, response):
        calls.append(request.path)

    app.mount("/sub", raw_app([]))

    @app.get("/")
    def home():
        return Response.text("home")

    with TestClient(app) as client:
        client.get("/")
        client.get("/sub/x")
    assert calls == ["/"]


def lifespan_app(events: list, fail: bool = False):
    async def asgi(scope, receive, send):
        assert scope["type"] == "lifespan"
        await receive()
        if fail:
            await send({"type": "lifespan.startup.failed", "message": "nope"})
            return
        events.append("startup")
        await send({"type": "lifespan.startup.complete"})
        await receive()
        events.append("shutdown")
        await send({"type": "lifespan.shutdown.complete"})

    return asgi


def test_lifespan_of_the_mounted_app_runs_with_the_parent():
    events: list = []
    app = App()
    app.mount("/sub", lifespan_app(events))
    with TestClient(app):
        assert events == ["startup"]
    assert events == ["startup", "shutdown"]


def test_mounted_korvex_app_runs_its_startup_hooks():
    events: list = []
    child = App()
    child.on_startup(lambda: events.append("up"))
    child.on_shutdown(lambda: events.append("down"))
    app = App()
    app.mount("/sub", child)
    with TestClient(app):
        assert events == ["up"]
    assert events == ["up", "down"]


def test_a_failing_sub_app_startup_fails_the_app():
    app = App()
    app.mount("/sub", lifespan_app([], fail=True))
    with pytest.raises(RuntimeError, match="nope"), TestClient(app):
        pass


def test_websocket_to_a_mounted_app():
    chat = App()

    @chat.websocket("/room/{name}")
    async def room(ws: WebSocket, name):
        await ws.accept()
        await ws.send_text(f"{name} {ws.root_path}")

    app = App()
    app.mount("/chat", chat)
    with TestClient(app) as client, client.websocket("/chat/room/lobby") as ws:
        assert ws.receive_text() == "lobby /chat"


def test_websocket_refused_by_a_parent_hook_never_reaches_the_mount():
    reached: list = []

    async def asgi(scope, receive, send):
        reached.append(scope["type"])

    app = App()
    app.before_request(lambda request: Response.text("no", status=403))
    app.mount("/chat", asgi)
    with TestClient(app) as client, pytest.raises(WebSocketDisconnect), client.websocket("/chat/x"):
        pass
    assert "websocket" not in reached


def test_url_for_a_named_mount():
    app = App()
    app.mount("/admin", raw_app([]), name="admin")
    assert app.url_for("admin") == "/admin/"
    assert app.url_for("admin", path="users") == "/admin/users"
    assert app.url_for("admin", path="a b/c") == "/admin/a%20b/c"
    for bad in ({"path": "../x"}, {"path": "a//b"}, {"other": "1"}):
        with pytest.raises(ValueError):
            app.url_for("admin", **bad)


def test_url_for_follows_root_path():
    app = App()
    app.mount("/admin", raw_app([]), name="admin")
    app._root_path = "/api"
    assert app.url_for("admin") == "/api/admin/"


def test_a_name_is_taken_once():
    app = App()
    app.mount("/a", raw_app([]), name="x")
    with pytest.raises(ValueError):
        app.mount("/b", raw_app([]), name="x")


@pytest.mark.parametrize(
    "prefix", ["", "/", "//", "admin", "/a/{x}", "/a/*", "/a?b", "/a#b", "/a%2fb", "/a//b", "/a/../b", "/a b", "/./a"]
)
def test_invalid_prefixes(prefix):
    with pytest.raises(ValueError):
        App().mount(prefix, raw_app([]))


def test_duplicate_prefix_and_non_app():
    app = App()
    app.mount("/a", raw_app([]))
    with pytest.raises(ValueError):
        app.mount("/a/", raw_app([]))
    with pytest.raises(TypeError):
        app.mount("/b", "not an app")  # type: ignore[arg-type]


def test_openapi_leaves_mounts_out():
    app = App()
    app.mount("/sub", raw_app([]), name="sub")

    @app.get("/real")
    def real():
        return Response.text("x")

    paths = app.openapi()["paths"]
    assert list(paths) == ["/real"]
    assert json.dumps(paths).count("sub") == 0


def test_request_url_for_a_named_mount():
    app = App()
    app.mount("/admin", raw_app([]), name="admin")

    @app.get("/link")
    def link(request):
        return Response.text(request.url_for("admin") + " " + request.url_for("admin", path="users"))

    @app.get("/unknown")
    def unknown(request):
        return Response.text(request.url_for("nope"))

    body = TestClient(app).get("/link").text
    first, second = body.split()
    assert first.endswith("/admin/") and first.startswith("http")
    assert second.endswith("/admin/users")
    with pytest.raises(LookupError):
        TestClient(app, raise_server_errors=True).get("/unknown")


def test_the_mount_root_reaches_the_sub_app_without_redirect():
    seen: list = []
    app = App()
    app.mount("/m", raw_app(seen), name="m")
    with TestClient(app) as client:
        replies = [client.get(p) for p in ("/m/", "/m//", "/m/?q=1")]
        link = client.get(app.url_for("m"))
    assert [r.status for r in replies] == [200, 200, 200] and link.status == 200
    assert seen[0][0]["path"] == "/m/"


def test_a_hook_prefix_check_cannot_be_bypassed_by_a_mount():
    app = App()
    app.mount("/m", raw_app([]))
    with TestClient(app) as client:
        assert client.get("/m/x").status == 200
        assert client.get("//m/x").status == 404


def test_websocket_double_slash_does_not_reach_a_mount():
    reached: list = []

    async def sub(scope, receive, send):
        reached.append(scope["type"])

    app = App()
    app.mount("/m", sub)
    with TestClient(app) as client, pytest.raises(WebSocketDisconnect), client.websocket("//m/x"):
        pass
    assert "websocket" not in reached


def test_lifespan_state_reaches_requests_as_a_copy():
    states: list = []

    async def sub(scope, receive, send):
        if scope["type"] == "lifespan":
            await receive()
            scope["state"]["db"] = "DB"
            await send({"type": "lifespan.startup.complete"})
            await receive()
            await send({"type": "lifespan.shutdown.complete"})
            return
        states.append(scope["state"])
        scope["state"]["x"] = 1  # a request's change stays its own
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    app = App()
    app.mount("/m", sub)
    with TestClient(app) as client:
        client.get("/m/a")
        client.get("/m/b")
    assert states[0] == {"db": "DB", "x": 1} and states[0] is not states[1]


def test_a_second_response_start_raises_without_showing_the_mount_match():
    errors: list = []

    async def sub(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        try:
            await send({"type": "http.response.start", "status": 200, "headers": []})
        except RuntimeError as exc:
            errors.append(exc)
        raise ValueError("boom")

    app = App()
    app.mount("/m", sub)
    with TestClient(app) as client:
        try:
            client.get("/m/x")
        except ValueError as exc:
            errors.append(exc)
    assert isinstance(errors[0], RuntimeError) or errors
    assert all(e.__context__ is None or "MountMatch" not in repr(e.__context__) for e in errors)
