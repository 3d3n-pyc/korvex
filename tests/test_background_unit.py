from __future__ import annotations

import asyncio
import logging
import threading

from korvex._background import BackgroundTasks


def test_order_and_mixed() -> None:
    seen: list[str] = []

    async def a() -> None:
        seen.append("a")

    def b() -> None:
        seen.append("b")

    tasks = BackgroundTasks()
    tasks.add(a)
    tasks.add(b)
    tasks.add(a)
    asyncio.run(tasks._run())
    assert seen == ["a", "b", "a"]


def test_sync_runs_off_loop_thread() -> None:
    ids: list[int] = []
    tasks = BackgroundTasks()
    tasks.add(lambda: ids.append(threading.get_ident()))
    asyncio.run(tasks._run())
    assert ids and ids[0] != threading.get_ident()


def test_failure_logged_and_next_runs(caplog) -> None:
    seen: list[int] = []

    def boom() -> None:
        raise ValueError("x")

    async def aboom() -> None:
        raise ValueError("y")

    tasks = BackgroundTasks()
    tasks.add(boom)
    tasks.add(aboom)
    tasks.add(seen.append, 1)
    with caplog.at_level(logging.ERROR, logger="korvex"):
        asyncio.run(tasks._run())
    assert seen == [1]
    assert "background task boom failed" in caplog.text
    assert "background task aboom failed" in caplog.text


def test_args_and_kwargs() -> None:
    got: list[tuple] = []

    def f(*args, **kwargs) -> None:
        got.append((args, kwargs))

    async def g(*args, **kwargs) -> None:
        got.append((args, kwargs))

    tasks = BackgroundTasks()
    tasks.add(f, 1, 2, k=3)
    tasks.add(g, 4, z=5)
    asyncio.run(tasks._run())
    assert got == [((1, 2), {"k": 3}), ((4,), {"z": 5})]


def test_async_call_partial_and_coroutine_returning_forms() -> None:
    import functools

    seen: list[str] = []

    class Callable_:
        async def __call__(self, tag: str) -> None:
            seen.append(tag)

    async def coro(tag: str) -> None:
        seen.append(tag)

    tasks = BackgroundTasks()
    tasks.add(Callable_(), "object")
    tasks.add(functools.partial(coro, "partial"))
    tasks.add(lambda: coro("lambda"))
    tasks.add(functools.partial(lambda tag: coro(tag), "partial-lambda"))
    asyncio.run(tasks._run())
    assert seen == ["object", "partial", "lambda", "partial-lambda"]


def test_dropped_tasks_do_not_run() -> None:
    seen: list[int] = []
    tasks = BackgroundTasks()
    tasks.add(seen.append, 1)
    tasks._dropped = True
    asyncio.run(tasks._run())
    assert seen == []
