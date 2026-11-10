"""Server-Sent Events: the event type, its wire encoding, keep-alive pings and a small parser."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from typing import Any, Optional

PING = b": ping\n\n"

_NEWLINE = re.compile(r"\r\n|\r|\n")


def _check_line(name: str, value: Optional[str], *, nul: bool = False) -> Optional[str]:
    """Return `value` if it is None or a single-line str, else raise ValueError."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a str")
    if "\r" in value or "\n" in value or (nul and "\0" in value):
        raise ValueError(f"{name} must not contain a line break" + (" or NUL" if nul else ""))
    return value


class ServerSentEvent:
    """One event of a Server-Sent Events stream.

    Send it from a streaming handler; the browser's `EventSource` receives it.

    Args:
        data: The payload. A str is sent as is; anything else is sent as JSON. None sends no data.
        event: The event name, a single line. Listeners use it to pick events.
        id: The event id, a single line without NUL. The browser sends the last one back on reconnect.
        retry: How many milliseconds the browser waits before reconnecting, an int of 0 or more.

    Guide: https://3d3n-pyc.github.io/korvex/requests-and-responses/#server-sent-events
    """

    __slots__ = ("data", "event", "id", "retry", "_wire")

    def __init__(
        self,
        data: Any = None,
        *,
        event: Optional[str] = None,
        id: Optional[str] = None,  # noqa: A002
        retry: Optional[int] = None,
    ) -> None:
        """Validate the fields, then build the wire bytes once so errors surface here."""
        if retry is not None and (isinstance(retry, bool) or not isinstance(retry, int) or retry < 0):
            raise ValueError("retry must be an int of 0 or more")
        self.data = data
        self.event = _check_line("event", event)
        self.id = _check_line("id", id, nul=True)
        self.retry = retry
        self._wire = self._build_wire()

    def _data_text(self) -> Optional[str]:
        """The data as text: a str as is, anything else as JSON, None as None."""
        if self.data is None or isinstance(self.data, str):
            return self.data
        if isinstance(self.data, (bytes, bytearray, memoryview)):
            raise TypeError("data must be str or JSON-serialisable, not bytes; decode it first")
        try:
            return json.dumps(self.data, allow_nan=False)
        except ValueError as exc:
            raise ValueError(f"data cannot be sent as JSON: {exc}") from None

    def _build_wire(self) -> bytes:
        """The event as UTF-8 bytes, ending with a blank line."""
        lines = []
        if self.event is not None:
            lines.append(f"event: {self.event}")
        if self.id is not None:
            lines.append(f"id: {self.id}")
        if self.retry is not None:
            lines.append(f"retry: {self.retry}")
        text = self._data_text()
        if text is not None:
            lines.extend(f"data: {part}" for part in _NEWLINE.split(text))
        try:
            return ("\n".join(lines) + "\n\n").encode()
        except UnicodeEncodeError:
            raise ValueError("event text contains a lone surrogate, which UTF-8 cannot encode") from None

    def encode(self) -> bytes:
        """The event as UTF-8 bytes, ending with a blank line."""
        return self._wire

    def _key(self) -> tuple:
        """The fields, for equality."""
        return (self.data, self.event, self.id, self.retry)

    def __eq__(self, other: object) -> bool:
        """Events are equal when all four fields are."""
        return isinstance(other, ServerSentEvent) and self._key() == other._key()

    __hash__ = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        """Show the fields that are set."""
        fields = (("data", self.data), ("event", self.event), ("id", self.id), ("retry", self.retry))
        return "ServerSentEvent(" + ", ".join(f"{k}={v!r}" for k, v in fields if v is not None) + ")"


def encode(item: Any) -> bytes:
    """Encode a ServerSentEvent, or any other value as the data of a new one."""
    if not isinstance(item, ServerSentEvent):
        item = ServerSentEvent(item)
    return item.encode()


async def _next(source: AsyncIterator) -> Any:
    """Await the next item of `source`."""
    return await source.__anext__()


async def _stop(task: asyncio.Future, source: AsyncIterator) -> None:
    """Cancel the pending task, then close the source so its `finally` runs."""
    if not task.done():
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    aclose = getattr(source, "aclose", None)
    if aclose is not None:
        await aclose()


async def with_pings(events: AsyncIterator, interval: float) -> AsyncIterator[bytes]:
    """Yield encoded events, plus PING whenever `interval` seconds pass without one."""
    source = events.__aiter__()
    task = asyncio.ensure_future(_next(source))
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=interval if interval > 0 else None)
            if not done:
                yield PING
                continue
            try:
                item = task.result()
            except StopAsyncIteration:
                return
            task = asyncio.ensure_future(_next(source))
            yield encode(item)
    finally:
        await _stop(task, source)


def _apply(event: dict, field: str, value: str) -> None:
    """Record one parsed field in the event being built."""
    if field == "data":
        event.setdefault("data", []).append(value)
    elif field == "event" or (field == "id" and "\0" not in value):
        event[field] = value
    elif field == "retry" and value.isascii() and value.isdigit():
        event["retry"] = int(value)


def _build(event: dict) -> ServerSentEvent:
    """Turn the collected fields into a ServerSentEvent."""
    data = event.get("data")
    return ServerSentEvent(
        "\n".join(data) if data is not None else None,
        event=event.get("event"),
        id=event.get("id"),
        retry=event.get("retry"),
    )


def parse(text: str) -> list[ServerSentEvent]:
    """Parse an event stream into events; comments are skipped, data stays a str."""
    events = []
    current: dict = {}
    text = text.removeprefix("\ufeff")
    for line in _NEWLINE.split(text):
        if not line:
            if current:
                events.append(_build(current))
                current = {}
        elif not line.startswith(":"):
            field, _, value = line.partition(":")
            _apply(current, field, value[1:] if value.startswith(" ") else value)
    if current:
        events.append(_build(current))
    return events
