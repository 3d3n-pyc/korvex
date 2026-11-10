from __future__ import annotations

import logging

import pytest

from korvex import App, Depends, Response, StreamingResponse
from korvex._background import BackgroundTasks
from korvex.testing import TestClient


def test_task_runs_after_the_response() -> None:
    app = App()
    seen: list[str] = []

    @app.get("/")
    async def index(tasks: BackgroundTasks) -> Response:
        tasks.add(seen.append, "task")
        seen.append("handler")
        return Response.json({"ok": True})

    response = TestClient(app).get("/")
    assert response.json() == {"ok": True}
    assert seen == ["handler", "task"]


def test_order_sync_and_async() -> None:
    app = App()
    seen: list[str] = []

    async def a() -> None:
        seen.append("a")

    @app.get("/")
    async def index(tasks: BackgroundTasks) -> Response:
        tasks.add(a)
        tasks.add(seen.append, "b")
        tasks.add(a)
        return Response.text("x")

    TestClient(app).get("/")
    assert seen == ["a", "b", "a"]


def test_handler_and_dependency_share_one_object() -> None:
    app = App()
    seen: list[object] = []

    def dep(tasks: BackgroundTasks) -> BackgroundTasks:
        tasks.add(seen.append, "dep")
        return tasks

    @app.get("/")
    async def index(tasks: BackgroundTasks, other: BackgroundTasks = Depends(dep)) -> Response:
        assert tasks is other
        tasks.add(seen.append, "handler")
        return Response.text("x")

    TestClient(app).get("/")
    assert seen == ["dep", "handler"]


def test_failing_task_is_logged_and_response_unchanged(caplog: pytest.LogCaptureFixture) -> None:
    app = App()
    seen: list[str] = []

    def boom() -> None:
        raise ValueError("nope")

    @app.get("/")
    async def index(tasks: BackgroundTasks) -> Response:
        tasks.add(boom)
        tasks.add(seen.append, "next")
        return Response.text("fine")

    with caplog.at_level(logging.ERROR, logger="korvex"):
        response = TestClient(app, raise_server_errors=False).get("/")
    assert response.status == 200 and response.text == "fine"
    assert seen == ["next"]
    assert "background task boom failed" in caplog.text


def test_tasks_after_a_streamed_response() -> None:
    app = App()
    seen: list[str] = []

    @app.get("/")
    async def index(tasks: BackgroundTasks) -> StreamingResponse:
        async def chunks():
            seen.append("chunk")
            yield b"x"

        tasks.add(seen.append, "task")
        return StreamingResponse(chunks())

    TestClient(app).get("/")
    assert seen == ["chunk", "task"]


def test_tasks_after_dependency_teardown() -> None:
    app = App()
    seen: list[str] = []

    def dep():
        seen.append("setup")
        yield 1
        seen.append("teardown")

    @app.get("/")
    async def index(tasks: BackgroundTasks, value: int = Depends(dep)) -> Response:
        tasks.add(seen.append, "task")
        return Response.text("x")

    TestClient(app).get("/")
    assert seen == ["setup", "teardown", "task"]


def test_websocket_route_refuses_it() -> None:
    app = App()
    with pytest.raises(TypeError, match="BackgroundTasks"):

        @app.websocket("/ws")
        async def ws(websocket, tasks: BackgroundTasks) -> None: ...

    def dep(tasks: BackgroundTasks) -> int:
        return 1

    with pytest.raises(TypeError, match="BackgroundTasks"):

        @app.websocket("/ws2")
        async def ws2(websocket, value: int = Depends(dep)) -> None: ...


def test_failing_task_raises_in_the_test_client_by_default() -> None:
    app = App()

    @app.get("/")
    async def index(tasks: BackgroundTasks) -> Response:
        tasks.add(lambda: 1 / 0)
        return Response.text("fine")

    with pytest.raises(ZeroDivisionError):
        TestClient(app).get("/")


def test_tasks_dropped_when_the_handler_raises() -> None:
    from korvex import HTTPException

    app = App()
    seen: list[str] = []

    @app.get("/http")
    async def http(tasks: BackgroundTasks) -> Response:
        tasks.add(seen.append, "http")
        raise HTTPException(400)

    @app.get("/boom")
    async def boom(tasks: BackgroundTasks) -> Response:
        tasks.add(seen.append, "boom")
        raise RuntimeError("x")

    @app.get("/returned")
    async def returned(tasks: BackgroundTasks) -> Response:
        tasks.add(seen.append, "returned")
        return Response.json({}, status=404)

    client = TestClient(app, raise_server_errors=False)
    assert client.get("/http").status == 400
    assert client.get("/boom").status == 500
    assert client.get("/returned").status == 404
    assert seen == ["returned"]
