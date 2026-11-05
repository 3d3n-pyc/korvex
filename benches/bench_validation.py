"""Benchmark: what validation from annotations costs per request.

Each case receives the same JSON body and returns the same object,
called in-process through ASGI (no server), like bench_app.py:

- korvex, no annotations: reads the JSON itself (the baseline);
- korvex with a msgspec model in and out;
- korvex with a pydantic model in and out;
- FastAPI with the same pydantic models, for reference.

Run: uv run pytest benches/bench_validation.py --benchmark-only
"""

import asyncio

import msgspec
import pydantic
import pytest
from fastapi import FastAPI

from korvex import App, Response

BODY = b'{"name": "Ada", "age": 36}'
SCOPE = {
    "type": "http",
    "method": "POST",
    "path": "/users",
    "headers": [(b"content-type", b"application/json")],
    "query_string": b"",
}
_loop = asyncio.new_event_loop()  # one loop for every call, like a server


class MsgspecIn(msgspec.Struct):
    name: str
    age: int


class MsgspecOut(msgspec.Struct):
    id: int
    name: str


class PydanticIn(pydantic.BaseModel):
    name: str
    age: int


class PydanticOut(pydantic.BaseModel):
    id: int
    name: str


def korvex_plain():
    app = App()

    @app.post("/users")
    async def create(request):
        data = await request.json()
        return Response.json({"id": 1, "name": data["name"]})

    return app


def korvex_msgspec():
    app = App()

    @app.post("/users")
    async def create(request, body: MsgspecIn) -> MsgspecOut:
        return MsgspecOut(id=1, name=body.name)

    return app


def korvex_pydantic():
    app = App()

    @app.post("/users")
    async def create(request, body: PydanticIn) -> PydanticOut:
        return PydanticOut(id=1, name=body.name)

    return app


def fastapi_pydantic():
    app = FastAPI()

    @app.post("/users")
    async def create(body: PydanticIn) -> PydanticOut:
        return PydanticOut(id=1, name=body.name)

    return app


APPS = {
    "korvex, no annotations": korvex_plain,
    "korvex + msgspec": korvex_msgspec,
    "korvex + pydantic": korvex_pydantic,
    "fastapi + pydantic": fastapi_pydantic,
}


def call(app):
    sent = []
    done = False

    async def receive():
        nonlocal done
        if done:
            return {"type": "http.disconnect"}
        done = True
        return {"type": "http.request", "body": BODY, "more_body": False}

    async def send(message):
        sent.append(message)

    _loop.run_until_complete(app(dict(SCOPE), receive, send))
    return sent


@pytest.mark.parametrize("name", APPS)
def test_validation(benchmark, name):
    app = APPS[name]()
    sent = call(app)
    assert sent[0]["status"] == 200 and b'"name":"Ada"' in sent[-1]["body"].replace(b" ", b"")
    benchmark.extra_info["case"] = name
    benchmark(call, app)
