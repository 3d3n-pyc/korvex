"""Tests for WebSockets in the App, driven through ASGI messages."""

import asyncio
import json

from korvex import App, Response, WebSocket


def ws_scope(path, subprotocols=(), headers=()):
    return {
        "type": "websocket",
        "path": path,
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers],
        "subprotocols": list(subprotocols),
    }


def run_ws(app, scope, incoming=()):
    """Plays the server: sends connect, then `incoming` messages (str or
    bytes), then a disconnect; returns what the app sent."""
    messages = [{"type": "websocket.connect"}]
    for message in incoming:
        key = "text" if isinstance(message, str) else "bytes"
        messages.append({"type": "websocket.receive", key: message})
    messages.append({"type": "websocket.disconnect", "code": 1001})
    queue = iter(messages)
    sent = []

    async def receive():
        return next(queue)

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    return sent


def echo_app():
    app = App()

    @app.websocket("/echo/{room}")
    async def echo(ws: WebSocket, room):
        await ws.accept(subprotocol="chat" if "chat" in ws.subprotocols else None)
        async for message in ws:
            if isinstance(message, bytes):
                await ws.send_bytes(message[::-1])
            else:
                await ws.send_text(f"{room}: {message}")

    return app


def test_echo_text_and_bytes_until_the_client_leaves():
    sent = run_ws(echo_app(), ws_scope("/echo/lobby"), ["hi", b"abc"])
    assert sent == [
        {"type": "websocket.accept", "subprotocol": None, "headers": []},
        {"type": "websocket.send", "text": "lobby: hi"},
        {"type": "websocket.send", "bytes": b"cba"},
    ]  # the client disconnected: nothing left to close


def test_subprotocols():
    sent = run_ws(echo_app(), ws_scope("/echo/x", subprotocols=["chat", "v2"]))
    assert sent[0]["subprotocol"] == "chat"


def test_unknown_path_is_refused():
    assert run_ws(echo_app(), ws_scope("/nope")) == [{"type": "websocket.close", "code": 1000, "reason": ""}]


def test_handler_can_refuse_before_accepting():
    app = App()

    @app.websocket("/private")
    async def private(ws):
        if ws.headers.get("authorization") != "Bearer token":
            await ws.close(code=1008)  # before accept: a 403 for the client
            return
        await ws.accept()

    assert run_ws(app, ws_scope("/private")) == [{"type": "websocket.close", "code": 1008, "reason": ""}]
    sent = run_ws(app, ws_scope("/private", headers=[("Authorization", "Bearer token")]))
    assert [m["type"] for m in sent] == ["websocket.accept", "websocket.close"]


def test_json_messages():
    app = App()

    @app.websocket("/json")
    async def handler(ws):
        await ws.accept()
        data = await ws.receive_json()
        await ws.send_json({"got": data})
        await ws.close()

    sent = run_ws(app, ws_scope("/json"), [json.dumps({"n": 1})])
    assert json.loads(sent[1]["text"]) == {"got": {"n": 1}}
    assert sent[2] == {"type": "websocket.close", "code": 1000, "reason": ""}


def test_handler_error_closes_with_1011(caplog):
    app = App()

    @app.websocket("/boom")
    async def boom(ws):
        await ws.accept()
        raise RuntimeError("boom")

    sent = run_ws(app, ws_scope("/boom"))
    assert sent[-1] == {"type": "websocket.close", "code": 1011, "reason": ""}
    assert "unhandled error in WebSocket /boom" in caplog.text


def test_websocket_in_a_group_and_next_to_an_http_route():
    app = App()
    api = app.group("/api")

    @api.websocket("/live")
    async def live(ws):
        await ws.accept()
        await ws.send_text("live")

    @api.get("/live")
    def live_http(request):
        return Response.text("http")

    sent = run_ws(app, ws_scope("/api/live"))
    assert sent[1] == {"type": "websocket.send", "text": "live"}

    from test_app import make_scope, response_from, run_app

    assert response_from(run_app(app, make_scope("GET", "/api/live")))[2] == b"http"


def test_before_hooks_see_the_handshake_and_can_refuse_it():
    from korvex import Abort, HTTPException

    app = App()
    seen = []

    @app.before_request
    def check_origin(request):
        if isinstance(request, WebSocket):
            seen.append(request.path)
            if request.headers.get("origin") not in (None, "https://example.com"):
                return Response.text("wrong origin", status=403)
        return None

    @app.before_request
    def closed(request):
        if request.path == "/ws/closed":
            raise HTTPException(503)
        if request.path == "/ws/moved":
            raise Abort(Response.redirect("/elsewhere"))

    @app.websocket("/ws/{name}")
    async def echo(ws, name):
        await ws.accept()
        await ws.send_text(name)

    assert run_ws(app, ws_scope("/ws/a", headers=[("Origin", "https://example.com")]))[1] == {"type": "websocket.send", "text": "a"}
    refused = [{"type": "websocket.close", "code": 1000, "reason": ""}]
    assert run_ws(app, ws_scope("/ws/a", headers=[("Origin", "https://evil.example")])) == refused
    assert run_ws(app, ws_scope("/ws/closed")) == refused
    assert run_ws(app, ws_scope("/ws/moved")) == refused
    assert run_ws(app, ws_scope("/nowhere")) == refused  # hooks run ahead of routing, as for HTTP
    assert seen == ["/ws/a", "/ws/a", "/ws/closed", "/ws/moved", "/nowhere"]


def test_ready_made_hooks_apply_to_websockets():
    from korvex.middleware import current_request_id, rate_limit, request_id

    app = App()
    request_id(app)
    rate_limit(app, requests=2, per=60)

    @app.websocket("/ws")
    async def ws(socket):
        await socket.accept()
        await socket.send_text(current_request_id() or "none")

    first = run_ws(app, ws_scope("/ws", headers=[("X-Request-ID", "abc-123")]))
    assert first[1] == {"type": "websocket.send", "text": "abc-123"}
    assert run_ws(app, ws_scope("/ws"))[0]["type"] == "websocket.accept"
    assert run_ws(app, ws_scope("/ws")) == [{"type": "websocket.close", "code": 1000, "reason": ""}]  # the third: limited
