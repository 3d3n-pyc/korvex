"""Background tasks: work that runs after the response is sent."""

from __future__ import annotations

import asyncio
import functools
import inspect
import logging
from typing import Any, Callable

logger = logging.getLogger("korvex")


def _is_async(func: Any) -> bool:
    """True for an async function, a partial of one, or an object with an async `__call__`."""
    while isinstance(func, functools.partial):
        func = func.func
    return inspect.iscoroutinefunction(func) or inspect.iscoroutinefunction(type(func).__call__)


class BackgroundTasks:
    """Work to do after the response is sent, in the order it was added.

    Async functions are awaited; plain functions run in a thread. Errors are
    logged, and the client never sees them.

    Guide: https://3d3n-pyc.github.io/korvex/requests-and-responses/#background-tasks
    """

    __slots__ = ("_items", "_dropped")

    def __init__(self) -> None:
        self._items: list[tuple[Callable[..., Any], tuple, dict]] = []
        self._dropped = False  # set when the handler raised: nothing runs

    def add(self, func: Callable[..., Any], /, *args: Any, **kwargs: Any) -> None:
        """Queues `func(*args, **kwargs)` to run after the response."""
        self._items.append((func, args, kwargs))

    async def _run(self) -> None:
        """Runs every task in order; a failure is logged and the next one still runs."""
        if self._dropped:
            logger.debug("background tasks dropped: the handler raised")
            return
        for func, args, kwargs in self._items:
            try:
                if _is_async(func):
                    await func(*args, **kwargs)
                else:
                    result = await asyncio.to_thread(func, *args, **kwargs)
                    if inspect.isawaitable(result):
                        await result
            except Exception:
                logger.exception("background task %s failed", getattr(func, "__name__", repr(func)))
