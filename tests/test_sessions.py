"""Tests for sessions and flash messages."""

from __future__ import annotations

import pytest

from korvex import App, Response, WebSocket
from korvex.middleware._sessions import current_session, flash, get_flashed_messages, sessions
from korvex.middleware._signing import derive_keys, sign, unsign
from korvex.testing import TestClient

SECRET = "k" * 40


def make_app(**options):
    app = App()
    sessions(app, secret_key=SECRET, **options)

    @app.post("/login")
    def login():
        current_session()["user"] = "ada"
        return Response.text("ok")

    @app.get("/me")
    async def me():
        return Response.text(str(current_session().get("user")))

    @app.get("/peek")
    def peek():
        return Response.text(str(len(current_session())))

    @app.get("/logout")
    def logout():
        current_session().clear()
        return Response.text("bye")

    @app.get("/plain")
    def plain():
        return Response.text("plain")

    @app.post("/nested")
    def nested():
        s = current_session()
        s.setdefault("cart", []).append(1)
        s["cart"].append(2)
        s.modified = True
        return Response.text("ok")

    @app.get("/flash")
    def do_flash():
        flash("hello", "success")
        return Response.text("ok")

    @app.get("/messages")
    def messages():
        return Response.text(repr(get_flashed_messages()))

    @app.get("/big")
    def big():
        current_session()["x"] = "a" * 5000
        return Response.text("ok")

    @app.websocket("/ws")
    async def ws_user(ws: WebSocket):
        await ws.accept()
        await ws.send_text(str(current_session().get("user")))

    return app


def client(app=None, **options):
    return TestClient(app or make_app(**options))


def test_login_sets_cookie_with_flags():
    with client() as c:
        header = c.post("/login").headers["set-cookie"]
    for flag in ("session=", "HttpOnly", "Secure", "SameSite=lax", "Path=/", "Max-Age=1209600"):
        assert flag.lower() in header.lower()


def test_next_request_sees_value():
    with client() as c:
        c.post("/login")
        assert c.get("/me").text == "ada"


def test_secure_cookie_not_sent_over_http():
    with TestClient(make_app()) as c:
        c.post("/login")
        assert c.get("http://example.com/me").text == "None"


def test_no_set_cookie_when_unchanged():
    with client() as c:
        assert "set-cookie" not in c.get("/plain").headers
        assert "set-cookie" not in c.get("/peek").headers
        c.post("/login")
        assert "set-cookie" not in c.get("/me").headers


def forged(value, secret=SECRET, now=None):
    return sign(value, derive_keys(secret, b"session")[0], now)


@pytest.mark.parametrize(
    "cookie",
    [
        forged(b'{"user":"eve"}')[:-2] + "xx",
        forged(b'{"user":"eve"}', now=1000),
        "garbage",
        forged(b"not json"),
        forged(b'["user"]'),
        forged(b'"user"'),
        forged(b'{"user":"eve"}', secret="z" * 40),
    ],
)
def test_bad_cookie_gives_empty_session(cookie):
    with client() as c:
        assert c.get("/me", cookies={"session": cookie}).text == "None"


def test_valid_forged_cookie_is_read():
    with client() as c:
        assert c.get("/me", cookies={"session": forged(b'{"user":"eve"}')}).text == "eve"


def test_key_rotation():
    old = forged(b'{"user":"old"}', secret="o" * 40)
    rotated = App()
    sessions(rotated, secret_key=["n" * 40, "o" * 40])

    @rotated.get("/me")
    def me():
        return Response.text(str(current_session().get("user")))

    @rotated.post("/login")
    def login():
        current_session()["user"] = "x"
        return Response.text("ok")

    with TestClient(rotated) as c:
        assert c.get("/me", cookies={"session": old}).text == "old"
        header = c.post("/login").headers["set-cookie"]
    value = header.split("=", 1)[1].split(";")[0]
    new_key = derive_keys(["n" * 40], b"session")
    assert unsign(value, new_key, None) == b'{"user":"x"}'


def test_clear_deletes_cookie():
    with client() as c:
        c.post("/login")
        header = c.get("/logout").headers["set-cookie"]
        assert "Max-Age=0" in header
        assert c.get("/me").text == "None"


def test_clear_without_cookie_sends_nothing():
    with client() as c:
        assert "set-cookie" not in c.get("/logout").headers


def test_oversize_raises():
    with client() as c, pytest.raises(ValueError, match="bytes"):
        c.get("/big")


def test_nested_change_with_modified():
    with client() as c:
        c.post("/nested")
        assert c.get("/me").text == "None"
        assert "set-cookie" not in c.get("/plain").headers


def test_flash_once():
    with client() as c:
        c.get("/flash")
        assert c.get("/messages").text == "[('success', 'hello')]"
        assert c.get("/messages").text == "[]"


def test_runtime_error_without_sessions():
    app = App()

    @app.get("/")
    def index():
        return Response.text(str(current_session()))

    with TestClient(app) as c, pytest.raises(RuntimeError, match="sessions"):
        c.get("/")
    with pytest.raises(RuntimeError):
        current_session()


def test_twice_is_an_error():
    app = App()
    sessions(app, secret_key=SECRET)
    with pytest.raises(RuntimeError):
        sessions(app, secret_key=SECRET)


def test_short_secret():
    with pytest.raises(ValueError):
        sessions(App(), secret_key="short")


def test_websocket_reads_session():
    with client() as c:
        c.post("/login")
        with c.websocket("/ws") as ws:
            assert ws.receive_text() == "ada"


def test_prefixed_cookie_is_deleted_with_same_attributes():
    with client(cookie="__Host-session") as c:
        c.post("/login")
        header = c.get("/logout").headers["set-cookie"]
        assert "Max-Age=0" in header
        for part in ("Secure", "HttpOnly", "SameSite=lax", "Path=/"):
            assert part in header


def test_ior_marks_modified():
    app = App()
    sessions(app, secret_key=SECRET, secure=False)

    @app.post("/ior")
    def ior():
        s = current_session()
        s |= {"user": "ada"}
        return Response.text("ok")

    r = TestClient(app).post("/ior")
    assert "session=" in r.headers["set-cookie"]


def test_cookie_limit_counts_the_name():
    app = App()
    sessions(app, secret_key=SECRET, secure=False, cookie="c" * 200)

    @app.get("/fill/{n}")
    def fill(n: int):
        current_session()["x"] = "a" * n
        return Response.text("ok")

    c = TestClient(app, raise_server_errors=False)
    sizes = {}
    for n in range(2500, 3000, 50):
        r = c.get(f"/fill/{n}")
        sizes[n] = (r.status, len(r.headers["set-cookie"].split(";")[0]) if r.status == 200 else 0)
    assert all(size <= 4096 for _, size in sizes.values())
    assert any(status == 500 for status, _ in sizes.values())


def test_csrf_before_sessions_still_reads_session():
    from korvex.middleware import csrf

    app = App()
    csrf(app)
    sessions(app, secret_key=SECRET, secure=False)

    @app.exception_handler(403)
    def forbidden(request, exc):
        return Response.text(f"msgs={get_flashed_messages()!r}", status=403)

    @app.post("/p")
    def p():
        return Response.text("ok")

    r = TestClient(app, raise_server_errors=False).post("/p", headers={"origin": "https://evil"})
    assert r.status == 403
    assert r.text == "msgs=[]"


def test_refused_request_does_not_reuse_previous_state():
    import asyncio

    app = App()
    sessions(app, secret_key=SECRET, secure=False)

    @app.before_request
    def refuse(request):
        if request.path == "/no":
            return Response.text("no", status=403)

    @app.get("/login")
    def login():
        current_session()["user"] = "ada"
        return Response.text("ok")

    async def call(path):
        out = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            out.append(message)

        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET",
            "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
            "root_path": "", "headers": [(b"host", b"x")], "client": ("1.2.3.4", 1), "server": ("x", 80),
        }  # fmt: skip
        await app(scope, receive, send)
        return out[0]["status"], [v for k, v in out[0]["headers"] if k.lower() == b"set-cookie"]

    async def main():
        first = await call("/login")
        second = await call("/no")
        return first, second

    first, second = asyncio.run(main())
    assert first[1]
    assert second == (403, [])


def test_a_stream_can_read_the_session():
    app = make_app()

    @app.get("/stream")
    def stream():
        def chunks():
            yield str(current_session().get("user"))

        return Response.stream(chunks())

    with TestClient(app) as client:
        client.post("/login")
        assert client.get("/stream").text == "ada"
