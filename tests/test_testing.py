"""Tests for `korvex.testing`: the test client, sync and async."""

import asyncio
import time

import pytest

from korvex import App, Depends, Form, Header, HTTPException, Response, UploadedFile, WebSocket, WebSocketDisconnect
from korvex.testing import AsyncTestClient, TestClient


def make_app():
    app = App()
    events = []
    app.events = events  # type: ignore[attr-defined]

    @app.on_startup
    async def start():
        events.append("startup")
        app.local.greeting = "hello"

    @app.on_shutdown
    def stop():
        events.append("shutdown")

    @app.get("/hello")
    def hello(request):
        return Response.text(app.local.greeting)

    @app.get("/echo")
    async def echo(request):
        return Response.json(
            {
                "query": request.query_params,
                "headers": {k: v for k, v in request.headers if k in ("x-one", "x-two", "host", "user-agent")},
                "cookies": request.cookies,
                "scheme": request.scheme,
                "path": request.path,
            }
        )

    @app.post("/body")
    async def body(request):
        return Response(
            await request.body(),
            headers=[
                ("Content-Type", request.headers.get("content-type") or "none"),
                ("X-Length", request.headers.get("content-length") or "none"),
            ],
        )

    @app.post("/form")
    def form(request, title: str = Form(), tags: list[str] = Form([]), doc: UploadedFile = Form(None)):
        out = {"title": title, "tags": tags}
        if doc is not None:
            out["doc"] = [doc.filename, doc.content_type, doc.data.decode()]
        return Response.json(out)

    @app.post("/login")
    def login(request):
        response = Response.redirect("/hello", status=303)
        response.set_cookie("session", "abc")
        response.set_cookie("secret", "s", secure=True)
        return response

    @app.post("/logout")
    def logout(request):
        response = Response.text("bye")
        response.delete_cookie("session")
        return response

    @app.get("/boom")
    def boom(request):
        raise ValueError("kaboom")

    @app.get("/teapot")
    def teapot(request):
        raise HTTPException(418)

    return app


def test_lifespan_runs_with_the_context_manager_only():
    app = make_app()
    with TestClient(app) as client:
        assert app.events == ["startup"]
        assert client.get("/hello").text == "hello"  # app.local, from the startup hook
    assert app.events == ["startup", "shutdown"]

    client = TestClient(make_app())
    assert client.get("/echo").status == 200  # works without the hooks
    client.close()


def test_response_fields():
    with TestClient(make_app()) as client:
        response = client.get("/hello")
    assert response.status == 200
    assert response.headers["content-type"] == "text/plain; charset=utf-8"  # case-insensitive
    assert response.body == b"hello" and response.text == "hello"
    assert response.url == "http://testserver/hello"
    assert repr(response) == "TestResponse(200, 5 bytes)"


def test_query_headers_and_cookies():
    with TestClient(make_app(), headers={"X-One": "1"}, cookies={"theme": "dark"}) as client:
        data = client.get("/echo?a=1", query={"b": [2, 3]}, headers=[("X-Two", "2")], cookies={"lang": "fr"}).json()
        assert data["query"] == {"a": ["1"], "b": ["2", "3"]}
        assert data["headers"] == {"host": "testserver", "user-agent": "testclient", "x-one": "1", "x-two": "2"}
        assert data["cookies"] == {"theme": "dark", "lang": "fr"}
        assert client.get("/echo").json()["cookies"] == {"theme": "dark"}  # per-request cookies don't stay


def test_full_url_sets_scheme_and_host():
    with TestClient(make_app()) as client:
        data = client.get("https://api.example.com:8443/echo").json()
    assert data["headers"]["host"] == "api.example.com:8443"
    assert data["scheme"] == "https"


def test_path_is_decoded_and_raw_path_kept():
    app = App()

    @app.get("/files/{name}")
    def show(request, name):
        return Response.text(name)

    with TestClient(app) as client:
        assert client.get("/files/caf%C3%A9%20au%20lait").text == "café au lait"
        assert client.get("/files/café").text == "café"


def test_json_raw_body_and_content_length():
    with TestClient(make_app()) as client:
        response = client.post("/body", json={"a": [1, None]})
        assert response.json() == {"a": [1, None]}
        assert response.headers["content-type"] == "application/json"
        assert response.headers["x-length"] == str(len(response.body))
        assert client.post("/body", json=None).body == b"null"
        raw = client.post("/body", body=b"\x00raw", headers={"Content-Type": "application/octet-stream"})
        assert raw.body == b"\x00raw" and raw.headers["content-type"] == "application/octet-stream"
        assert client.post("/body").headers["x-length"] == "none"
        with pytest.raises(TypeError, match="one way only"):
            client.post("/body", json={}, body=b"x")


def test_forms_and_files():
    with TestClient(make_app()) as client:
        assert client.post("/form", form={"title": "a & b", "tags": ["x", "y"]}).json() == {
            "title": "a & b",
            "tags": ["x", "y"],
        }
        response = client.post(
            "/form",
            form={"title": "report"},
            files={"doc": ('my "q".txt', b"data", "text/plain")},
        )
        assert response.json() == {"title": "report", "tags": [], "doc": ["my %22q%22.txt", "text/plain", "data"]}
        guessed = client.post("/form", form={"title": "t"}, files=[("doc", ("notes.txt", "hi"))]).json()
        assert guessed["doc"] == ["notes.txt", "text/plain", "hi"]


def test_cookies_kept_deleted_and_secure_only_over_https():
    with TestClient(make_app()) as client:
        response = client.post("/login")
        assert response.status == 303 and response.headers["location"] == "/hello"
        assert client.cookies == {"session": "abc", "secret": "s"}
        assert client.get("/echo").json()["cookies"] == {"session": "abc"}  # not the Secure one over http
        assert client.get("https://testserver/echo").json()["cookies"] == {"session": "abc", "secret": "s"}
        client.post("/logout")
        assert client.cookies == {"secret": "s"}


def test_redirects_followed_on_demand():
    with TestClient(make_app()) as client:
        response = client.post("/login", follow_redirects=True)
        assert (response.status, response.text, response.url) == (200, "hello", "http://testserver/hello")


def test_redirect_loop_stops():
    app = App()

    @app.get("/loop")
    def loop(request):
        return Response.redirect("/loop")

    with TestClient(app) as client, pytest.raises(RuntimeError, match="more than 20 redirects"):
        client.get("/loop", follow_redirects=True)


def test_server_errors_are_raised_in_the_test():
    with TestClient(make_app()) as client:
        with pytest.raises(ValueError, match="kaboom"):
            client.get("/boom")
        assert client.get("/teapot").status == 418  # an HTTP error is an answer, not a bug
    with TestClient(make_app(), raise_server_errors=False) as client:
        response = client.get("/boom")
    assert (response.status, response.text) == (500, "Internal Server Error")


def test_teardown_errors_are_raised_too():
    app = App()

    def resource():
        yield 1
        raise RuntimeError("teardown broke")

    @app.get("/r")
    def handler(request, value: int = Depends(resource)):
        return Response.text(str(value))

    with TestClient(app) as client, pytest.raises(RuntimeError, match="teardown broke"):
        client.get("/r")


def test_streamed_responses_read_whole_or_as_they_come():
    app = App()
    stopped = []

    @app.get("/count")
    def count(request):
        return Response.stream(str(i) for i in range(3))

    @app.get("/forever")
    async def forever(request):
        async def events():
            try:
                n = 0
                while True:
                    yield f"event {n}\n"
                    n += 1
                    await asyncio.sleep(0)
            finally:
                stopped.append(True)

        return Response.stream(events())

    with TestClient(app) as client:
        assert client.get("/count").text == "012"
        with client.stream("GET", "/count") as response:
            assert response.status == 200
            assert response.read() == b"012"
        with client.stream("GET", "/forever") as response:
            chunks = response.iter_text()
            assert [next(chunks), next(chunks)] == ["event 0\n", "event 1\n"]
        assert stopped == [True]  # leaving the block stopped the stream


def test_stream_teardown_of_dependencies_runs_when_the_client_leaves():
    app = App()
    closed = []

    async def resource():
        yield "r"
        closed.append(True)

    @app.get("/s")
    async def handler(request, value: str = Depends(resource)):
        async def chunks():
            while True:
                yield value
                await asyncio.sleep(0)

        return Response.stream(chunks())

    with TestClient(app) as client:
        with client.stream("GET", "/s") as response:
            assert next(response.iter_bytes()) == b"r"
        assert closed == [True]


def test_timeout_fails_a_stuck_handler():
    app = App()

    @app.get("/stuck")
    async def stuck(request):
        await asyncio.Event().wait()

    with TestClient(app, timeout=0.05) as client:
        started = time.monotonic()
        with pytest.raises(TimeoutError, match="GET /stuck"):
            client.get("/stuck")
        assert time.monotonic() - started < 2


def ws_app():
    app = App()

    @app.websocket("/echo/{room}")
    async def echo(ws: WebSocket, room):
        await ws.accept(subprotocol="chat" if "chat" in ws.subprotocols else None, headers=[("X-Room", room)])
        async for message in ws:
            if isinstance(message, bytes):
                await ws.send_bytes(message[::-1])
            elif message == "bye":
                await ws.close(4000)
                return
            else:
                await ws.send_text(f"{room}: {message} {ws.query_params.get('n', [''])[0]}".strip())

    def token(x_token: str = Header("")):
        if x_token != "ok":
            raise HTTPException(403)

    @app.websocket("/guarded", dependencies=[Depends(token)])
    async def guarded(ws: WebSocket):
        await ws.accept()
        await ws.send_json({"ok": True})

    @app.websocket("/broken")
    async def broken(ws: WebSocket):
        await ws.accept()
        raise ValueError("ws kaboom")

    return app


def test_websocket_messages_both_ways():
    with TestClient(ws_app()) as client:
        with client.websocket("/echo/lobby", subprotocols=["chat"], query={"n": 1}) as ws:
            assert ws.subprotocol == "chat" and ws.headers["x-room"] == "lobby"
            ws.send_text("hi")
            assert ws.receive_text() == "lobby: hi 1"
            ws.send_bytes(b"abc")
            assert ws.receive_bytes() == b"cba"
            ws.send_json("x")
            assert ws.receive() == 'lobby: "x" 1'
            ws.send_text("bye")
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive()
            assert closed.value.code == 4000


def test_websocket_refused_and_headers():
    with TestClient(ws_app()) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket("/guarded"):
                pass
        with client.websocket("/guarded", headers={"X-Token": "ok"}) as ws:
            assert ws.receive_json() == {"ok": True}
        with pytest.raises(WebSocketDisconnect):
            with client.websocket("/nowhere"):
                pass


def test_websocket_server_error_raised():
    with TestClient(ws_app()) as client:
        with pytest.raises(ValueError, match="ws kaboom"):
            with client.websocket("/broken") as ws:
                ws.receive()


def test_websocket_receive_timeout():
    with TestClient(ws_app()) as client, client.websocket("/echo/a") as ws:
        with pytest.raises(TimeoutError):
            ws.receive(timeout=0.01)


def test_sync_client_refuses_a_running_loop():
    async def main():
        client = TestClient(make_app())
        with pytest.raises(RuntimeError, match="AsyncTestClient"):
            client.get("/hello")

    asyncio.run(main())


def test_async_client():
    app = make_app()

    async def main():
        async with AsyncTestClient(app) as client:
            assert (await client.get("/hello")).text == "hello"
            await client.post("/login")
            assert client.cookies["session"] == "abc"
            async with client.stream("GET", "/hello") as response:
                assert [chunk async for chunk in response.aiter_bytes()] == [b"hello"]
            async with AsyncTestClient(ws_app()).websocket("/echo/x") as ws:
                await ws.send_text("hi")
                assert await ws.receive_text() == "x: hi"

    asyncio.run(main())
    assert app.events == ["startup", "shutdown"]


def test_startup_failure_is_raised():
    app = App()

    @app.on_startup
    def start():
        raise OSError("no database")

    with pytest.raises(RuntimeError, match="no database"):
        with TestClient(app):
            pass


def test_any_asgi_app_even_without_lifespan():
    async def plain(scope, receive, send):
        if scope["type"] != "http":
            raise NotImplementedError
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    with TestClient(plain) as client:
        assert client.delete("/x").status == 204


def test_an_app_that_raises_or_never_answers():
    async def raising(scope, receive, send):
        raise KeyError("oops")

    async def silent(scope, receive, send):
        return None

    with pytest.raises(KeyError):
        TestClient(raising).get("/")
    with pytest.raises(RuntimeError, match="without completing"):
        TestClient(silent).get("/")


def test_pytest_doesnt_collect_the_client_classes():
    from korvex import testing

    assert all(getattr(testing, name).__test__ is False for name in testing.__all__)
