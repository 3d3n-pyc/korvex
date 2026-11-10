"""`Response.sse()`: headers, encoding, pings, errors, compression and client disconnect."""

import asyncio

import pytest
from support import make_scope

from korvex import App, Response
from korvex._sse import PING, ServerSentEvent
from korvex.middleware import compress
from korvex.testing import TestClient


def _app(source):
    app = App()

    @app.get("/s")
    async def handler(request):
        return Response.sse(source(), ping=0.05)

    return app


def test_headers_and_user_headers_win():
    app = App()

    @app.get("/s")
    async def handler(request):
        return Response.sse([], headers={"Cache-Control": "no-store"})

    with TestClient(app) as client, client.stream("GET", "/s") as response:
        assert response.headers["Content-Type"] == "text/event-stream; charset=utf-8"
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["X-Accel-Buffering"] == "no"


def test_events_are_encoded_from_a_sync_generator():
    def events():
        yield {"a": 1}
        yield "one\ntwo"
        yield ServerSentEvent("x", event="e", id="7", retry=1500)

    with TestClient(_app(events)) as client:
        body = client.get("/s").body
    assert body == b'data: {"a": 1}\n\ndata: one\ndata: two\n\nevent: e\nid: 7\nretry: 1500\ndata: x\n\n'


def test_pings_appear_while_an_async_generator_is_slow():
    async def events():
        await asyncio.sleep(0.3)
        yield "late"

    with TestClient(_app(events)) as client:
        body = client.get("/s").body
    assert body.startswith(PING)
    assert body.endswith(b"data: late\n\n")


def test_a_bad_event_stops_the_stream_and_the_error_reaches_the_server():
    async def events():
        yield "ok"
        yield ServerSentEvent("x", id="a\nb")

    with TestClient(_app(events)) as client, pytest.raises(ValueError, match="line break"):
        client.get("/s")


def test_compression_leaves_event_streams_alone():
    app = _app(lambda: iter(["x" * 2000]))
    compress(app, minimum_size=1)
    with TestClient(app) as client, client.stream("GET", "/s", headers={"Accept-Encoding": "gzip"}) as response:
        assert "Content-Encoding" not in response.headers
        assert response.read() == b"data: " + b"x" * 2000 + b"\n\n"


def test_disconnect_closes_the_generator():
    stopped = []

    async def events():
        try:
            while True:
                await asyncio.sleep(10)
                yield "never"
        finally:
            stopped[0].set()

    async def scenario():
        stopped.append(asyncio.Event())  # created in the loop: Python 3.9 binds an Event to one
        app = _app(events)
        left = asyncio.Event()
        sent = []

        async def receive():
            await left.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)

        task = asyncio.ensure_future(app(make_scope(path="/s"), receive, send))
        await asyncio.sleep(0.2)
        left.set()
        await asyncio.wait_for(stopped[0].wait(), 1)
        await asyncio.wait_for(task, 1)

    asyncio.run(scenario())
