from __future__ import annotations

import asyncio
import functools
from typing import Any, cast

import pytest

from korvex import Request
from korvex._mount import Lifespan, Receive, ResponderSend, child_scope, child_ws_scope


def run(test):
    """Runs an async test in its own event loop."""

    @functools.wraps(test)
    def wrapper():
        asyncio.run(test())

    return wrapper


class FakeResponder:
    id = 7

    def __init__(self, chunk_result=1):
        self.calls = []
        self.chunk_result = chunk_result

    def respond(self, status, headers, body):
        self.calls.append(("respond", status, headers, body))

    def start_stream(self, status, headers):
        self.calls.append(("start", status, headers))

    def send_chunk(self, chunk):
        self.calls.append(("chunk", chunk))
        return self.chunk_result

    def end_stream(self):
        self.calls.append(("end",))

    def abort_stream(self):
        self.calls.append(("abort",))


def make_request(path="/sub/a%20b", root_path="/api"):
    return Request._from_server(
        "POST",
        path,
        b"x=1",
        [(b"Host", b"h"), (b"X-A", b"b")],
        ("1.2.3.4", 5),
        "https",
        b"hi",
        cast(Any, None),
        root_path,
    )


def test_child_scope_fields():
    scope = child_scope(make_request("/sub/a b"), "/sub", raw_path=b"/sub/a%20b")
    assert scope["type"] == "http"
    assert scope["asgi"] == {"version": "3.0", "spec_version": "2.3"}
    assert scope["http_version"] == "1.1"
    assert scope["method"] == "POST"
    assert scope["scheme"] == "https"
    assert scope["path"] == "/api/sub/a b"
    assert scope["raw_path"] == b"/api/sub/a%20b"
    assert scope["root_path"] == "/api/sub"
    assert scope["query_string"] == b"x=1"
    assert (b"host", b"h") in scope["headers"] and (b"x-a", b"b") in scope["headers"]
    assert scope["client"] == ("1.2.3.4", 5)
    assert scope["state"] == {} and "server" not in scope


def test_child_scope_encodes_raw_path_when_unknown():
    assert child_scope(make_request("/sub/a b"), "/sub")["raw_path"] == b"/api/sub/a%20b"


def test_child_ws_scope():
    scope = child_ws_scope(make_request(), "/sub", server=("h", 80))
    assert scope["type"] == "websocket" and scope["server"] == ("h", 80)
    assert scope["subprotocols"] == []


@run
async def test_receive_replays_body_then_disconnects():
    receive = Receive(b"abc")
    assert await receive() == {"type": "http.request", "body": b"abc", "more_body": False}
    waiter = asyncio.ensure_future(receive())
    await asyncio.sleep(0)
    assert not waiter.done()
    receive.close()
    assert await waiter == {"type": "http.disconnect"}


@run
async def test_single_body_uses_respond():
    responder = FakeResponder()
    send = ResponderSend(responder, Receive(b""))
    await send({"type": "http.response.start", "status": 201, "headers": [(b"a", b"b")]})
    assert responder.calls == [] and send.unanswered
    await send({"type": "http.response.body", "body": b"ok"})
    assert responder.calls == [("respond", 201, [(b"a", b"b")], b"ok")]
    assert not send.unanswered


@run
async def test_streaming_maps_to_chunks_and_waits_when_behind():
    responder = FakeResponder(chunk_result=2)
    waited = []

    async def writable(id):
        waited.append(id)

    send = ResponderSend(responder, Receive(b""), writable)
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"a", "more_body": True})
    await send({"type": "http.response.body", "body": b"b", "more_body": False})
    assert responder.calls == [("start", 200, []), ("chunk", b"a"), ("chunk", b"b"), ("end",)]
    assert waited == [7]
    send.finish()
    assert ("abort",) not in responder.calls


@run
async def test_client_gone_marks_receive_disconnected():
    responder = FakeResponder(chunk_result=0)
    receive = Receive(b"")
    send = ResponderSend(responder, receive)
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"a", "more_body": True})
    await receive()
    assert await asyncio.wait_for(receive(), 1) == {"type": "http.disconnect"}
    await send({"type": "http.response.body", "body": b"b", "more_body": True})
    assert responder.calls.count(("chunk", b"a")) == 1 and ("chunk", b"b") not in responder.calls


@run
async def test_unfinished_stream_is_aborted():
    responder = FakeResponder()
    send = ResponderSend(responder, Receive(b""))
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"a", "more_body": True})
    send.finish()
    assert responder.calls[-1] == ("abort",)


@run
async def test_body_before_start_raises_and_no_start_is_unanswered():
    send = ResponderSend(FakeResponder(), Receive(b""))
    assert send.unanswered
    with pytest.raises(RuntimeError):
        await send({"type": "http.response.body", "body": b""})


@run
async def test_lifespan_ok():
    seen = []

    async def app(scope, receive, send):
        assert scope["type"] == "lifespan"
        for event in ("startup", "shutdown"):
            seen.append((await receive())["type"])
            await send({"type": f"lifespan.{event}.complete"})

    lifespan = Lifespan(app)
    await lifespan.startup()
    await lifespan.shutdown()
    assert seen == ["lifespan.startup", "lifespan.shutdown"]


@run
async def test_lifespan_startup_failed():
    async def app(scope, receive, send):
        await receive()
        await send({"type": "lifespan.startup.failed", "message": "boom"})

    with pytest.raises(RuntimeError, match="boom"):
        await Lifespan(app).startup()


@run
async def test_lifespan_shutdown_failed():
    async def app(scope, receive, send):
        await receive()
        await send({"type": "lifespan.startup.complete"})
        await receive()
        await send({"type": "lifespan.shutdown.failed", "message": "bad"})

    lifespan = Lifespan(app)
    await lifespan.startup()
    with pytest.raises(RuntimeError, match="bad"):
        await lifespan.shutdown()


@run
async def test_lifespan_unsupported_is_ignored():
    async def app(scope, receive, send):
        raise RuntimeError("http only")

    lifespan = Lifespan(app)
    await lifespan.startup()
    await lifespan.shutdown()


@run
async def test_a_second_response_start_raises():
    send = ResponderSend(FakeResponder(), Receive(b""))
    await send({"type": "http.response.start", "status": 200, "headers": []})
    with pytest.raises(RuntimeError):
        await send({"type": "http.response.start", "status": 200, "headers": []})
