"""Benchmark: what dependencies cost per request.

Each case answers the same GET: it finds the user from a header and opens
then closes a "session", and returns both as JSON. Called in-process
through ASGI (no server), like bench_validation.py:

- korvex, no dependencies: the handler does it all itself (the baseline);
- korvex with two dependencies, one of them torn down after the handler
  (`yield`), and the header read with `Header()`;
- FastAPI with the same dependencies, for reference. FastAPI runs sync
  dependencies in a thread pool, which costs a lot; Korvex calls them on
  the event loop, like sync handlers. The async variant is FastAPI's
  fastest way to write them.

Run: uv run pytest benches/bench_dependencies.py --benchmark-only
"""

import asyncio

import pytest
from fastapi import Depends as FastAPIDepends
from fastapi import FastAPI
from fastapi import Header as FastAPIHeader

from korvex import App, Depends, Header, Response

SCOPE = {
    "type": "http",
    "method": "GET",
    "path": "/me",
    "headers": [(b"x-token", b"t1")],
    "query_string": b"",
}
USERS = {"t1": "ada"}
_loop = asyncio.new_event_loop()  # one loop for every call, like a server


class Session:
    def close(self) -> None:
        pass


def korvex_plain():
    app = App()

    @app.get("/me")
    async def me(request):
        user = USERS[request.headers.get("x-token")]
        session = Session()
        try:
            return Response.json({"user": user, "session": True})
        finally:
            session.close()

    return app


def korvex_dependencies():
    app = App()

    def current_user(x_token: str = Header()) -> str:
        return USERS[x_token]

    def session():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    @app.get("/me")
    async def me(request, user: str = Depends(current_user), s: Session = Depends(session)):
        return Response.json({"user": user, "session": True})

    return app


def fastapi_dependencies():
    app = FastAPI()

    def current_user(x_token: str = FastAPIHeader()) -> str:
        return USERS[x_token]

    def session():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    @app.get("/me")
    async def me(user: str = FastAPIDepends(current_user), s: Session = FastAPIDepends(session)):
        return {"user": user, "session": True}

    return app


def fastapi_async_dependencies():
    app = FastAPI()

    async def current_user(x_token: str = FastAPIHeader()) -> str:
        return USERS[x_token]

    async def session():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    @app.get("/me")
    async def me(user: str = FastAPIDepends(current_user), s: Session = FastAPIDepends(session)):
        return {"user": user, "session": True}

    return app


APPS = {
    "korvex, no dependencies": korvex_plain,
    "korvex + dependencies": korvex_dependencies,
    "fastapi + dependencies": fastapi_dependencies,
    "fastapi + async dependencies": fastapi_async_dependencies,
}


def call(app):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    _loop.run_until_complete(app(dict(SCOPE), receive, send))
    return sent


@pytest.mark.parametrize("name", APPS)
def test_dependencies(benchmark, name):
    app = APPS[name]()
    sent = call(app)
    assert sent[0]["status"] == 200 and b'"user":"ada"' in sent[-1]["body"].replace(b" ", b"")
    benchmark.extra_info["case"] = name
    benchmark(call, app)
