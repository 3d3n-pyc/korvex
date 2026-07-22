"""Benchmark: korvex.App's full request-handling cost (match_route +
middleware + handler + Response + ASGI send) vs an equivalent minimal
Starlette app, calling each directly via the ASGI protocol — no real
socket or server involved, so this isolates in-process framework
overhead the same way bench_router.py isolates routing alone.

Run with:
    uv run pytest benches/bench_app.py --benchmark-only -v
"""

import asyncio

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from korvex import App, Response

SCOPE = {
    "type": "http",
    "method": "GET",
    "path": "/users/42",
    "headers": [],
    "query_string": b"",
}


_loop = asyncio.new_event_loop()
"""One event loop, reused for every benchmarked call — `asyncio.run()`
creates and tears down a fresh loop each time, which a real ASGI server
never does (uvicorn keeps a single loop alive for its whole lifetime and
just schedules a coroutine per request). Benchmarking with a fresh loop
per call would measure loop setup/teardown cost, not request-handling
cost, drowning out the very difference this file exists to measure."""


def _run_asgi(app) -> None:
    """Drives one ASGI request through `app` on the shared loop,
    discarding the response — mirrors what a real server does
    per-request, minus the socket."""
    sent = []
    body_sent = False

    async def receive():
        nonlocal body_sent
        if not body_sent:
            body_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    _loop.run_until_complete(app(SCOPE, receive, send))


@pytest.fixture(scope="module")
def korvex_app() -> App:
    app = App()

    @app.get("/users/{id:int}")
    def get_user(request, id):
        return Response.json({"id": id})

    return app


@pytest.fixture(scope="module")
def korvex_app_with_middleware() -> App:
    app = App()

    @app.get("/users/{id:int}", middleware=["auth"])
    def get_user(request, id):
        return Response.json({"id": id})

    @app.middleware("auth")
    def auth(request):
        return None

    return app


@pytest.fixture(scope="module")
def starlette_app() -> Starlette:
    async def get_user(request):
        return JSONResponse({"id": request.path_params["id"]})

    return Starlette(routes=[Route("/users/{id:int}", get_user)])


def test_korvex_app(benchmark, korvex_app: App) -> None:
    benchmark(lambda: _run_asgi(korvex_app))


def test_korvex_app_with_middleware(benchmark, korvex_app_with_middleware: App) -> None:
    benchmark(lambda: _run_asgi(korvex_app_with_middleware))


def test_starlette_app(benchmark, starlette_app: Starlette) -> None:
    benchmark(lambda: _run_asgi(starlette_app))
