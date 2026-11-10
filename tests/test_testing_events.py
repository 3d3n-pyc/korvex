"""Tests for `iter_events` and `aiter_events` of the test client streams."""

import asyncio

from korvex import App, Response
from korvex._sse import ServerSentEvent
from korvex.testing import AsyncTestClient, TestClient


def make_app(chunks, stopped=None):
    app = App()

    @app.get("/events")
    async def events(request):
        async def body():
            try:
                for chunk in chunks:
                    yield chunk
                    await asyncio.sleep(0)
                if stopped is not None:
                    await asyncio.sleep(3600)
            finally:
                if stopped is not None:
                    stopped.append(True)

        return Response.stream(body())

    return app


def sync_events(chunks):
    with TestClient(make_app(chunks)) as client, client.stream("GET", "/events") as response:
        return list(response.iter_events())


def test_event_split_inside_a_field_and_the_blank_line():
    chunks = [b"data: he", b"llo\n", b"\nda", b"ta: two\n", b"\n"]
    assert sync_events(chunks) == [ServerSentEvent("hello"), ServerSentEvent("two")]


def test_several_events_in_one_chunk_and_crlf():
    assert sync_events([b"data: a\n\ndata: b\r\n\r\n"]) == [ServerSentEvent("a"), ServerSentEvent("b")]
    assert sync_events([b"data: a\r", b"\n\r", b"\ndata: b\r\n\r\n"]) == [ServerSentEvent("a"), ServerSentEvent("b")]


def test_comments_are_skipped_and_fields_are_read():
    chunks = [b": ping\n\n", b"event: tick\nid: 7\nretry: 500\ndata: l1\ndata: l2\n\n", b": ping\n\n"]
    assert sync_events(chunks) == [ServerSentEvent("l1\nl2", event="tick", id="7", retry=500)]


def test_multibyte_character_split_between_chunks():
    raw = "data: é\n\n".encode()
    assert sync_events([raw[:7], raw[7:]]) == [ServerSentEvent("é")]


def test_leaving_the_block_stops_the_stream():
    stopped = []
    app = make_app([b"data: 1\n\n", b"data: 2\n\n"], stopped)
    with TestClient(app) as client:
        with client.stream("GET", "/events") as response:
            assert next(response.iter_events()) == ServerSentEvent("1")
        assert stopped == [True]


def test_async_events():
    async def main():
        app = make_app([b": ping\n\nda", b"ta: x\n", b"\n"])
        async with AsyncTestClient(app) as client, client.stream("GET", "/events") as response:
            return [event async for event in response.aiter_events()]

    assert asyncio.run(main()) == [ServerSentEvent("x")]


def test_async_leaving_the_block_stops_the_stream():
    stopped = []

    async def main():
        app = make_app([b"data: 1\n\n"], stopped)
        async with AsyncTestClient(app) as client:
            async with client.stream("GET", "/events") as response:
                events = response.aiter_events()
                assert await events.__anext__() == ServerSentEvent("1")
            assert stopped == [True]

    asyncio.run(main())
