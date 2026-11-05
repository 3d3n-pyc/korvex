"""Hands what happens on the board to everyone listening (the open pages).

Each listener gets its own bounded queue: one that stops reading is cut
off instead of holding up the others. This bus lives in one process, so
with several workers each has its own; a shared one (Redis pub/sub,
Postgres LISTEN/NOTIFY) would keep the same three methods.
"""

import asyncio
from typing import Optional

import msgspec

Message = msgspec.Struct  # domain events (domain.py) and anything else a page should see


class EventBus:
    def __init__(self, queue_size: int = 100) -> None:
        self._queue_size = queue_size
        self._listeners: set[asyncio.Queue[Optional[Message]]] = set()

    @property
    def listeners(self) -> int:
        return len(self._listeners)

    def subscribe(self) -> "asyncio.Queue[Optional[Message]]":
        """A queue that gets every message published from now on, and
        `None` if it fell too far behind and was cut off."""
        queue: asyncio.Queue[Optional[Message]] = asyncio.Queue(self._queue_size)
        self._listeners.add(queue)
        return queue

    def unsubscribe(self, queue: "asyncio.Queue[Optional[Message]]") -> None:
        self._listeners.discard(queue)

    def publish(self, message: Message) -> None:
        for queue in list(self._listeners):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                self._listeners.discard(queue)
                queue.get_nowait()  # make room for the signal
                queue.put_nowait(None)
