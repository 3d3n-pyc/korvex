"""Live updates: each open page keeps a WebSocket on /live and gets every
change as it happens (from any window or device), plus how many pages are
open. Signed-in users only: the same `current_user` dependency as the
API refuses anyone else before the connection opens (a 403)."""

import asyncio
import logging
from typing import Optional

import msgspec
from korvex import App, Depends, WebSocket

from taskboard.services.events import EventBus, Message
from taskboard.web.auth import CurrentUser

log = logging.getLogger(__name__)


class Presence(msgspec.Struct, frozen=True, tag_field="event", tag="presence"):
    """How many pages are open; sent when one opens or closes."""

    open: int


def register(app: App, events: EventBus, current_user: CurrentUser) -> None:
    @app.websocket("/live", dependencies=[Depends(current_user)])
    async def live(ws: WebSocket) -> None:
        await ws.accept()
        queue = events.subscribe()
        events.publish(Presence(events.listeners))
        sending = asyncio.create_task(_send_all(queue, ws))
        try:
            async for _ in ws:  # pages send nothing: this waits until the page goes away
                pass
        finally:
            sending.cancel()
            events.unsubscribe(queue)
            events.publish(Presence(events.listeners))


async def _send_all(queue: "asyncio.Queue[Optional[Message]]", ws: WebSocket) -> None:
    """Forwards the page's queue to its socket. A page that fell behind
    is closed with 1013 ("try again later"); it reconnects and reloads."""
    try:
        while (message := await queue.get()) is not None:
            await ws.send_text(msgspec.json.encode(message).decode())
        await ws.close(1013)
    except asyncio.CancelledError:
        raise
    except Exception:  # the page left mid-send; what a server raises for that varies
        log.debug("live page gone while sending", exc_info=True)
