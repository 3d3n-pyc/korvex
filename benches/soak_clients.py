"""Soak-test clients that run next to oha: WebSocket echo, slow and aborting clients.

    python benches/soak_clients.py HOST PORT SECONDS

Standard library only. Needs a server with GET /plain, GET /stream and a
WebSocket echo on /ws.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import os
import random
import struct
import sys
import time
from dataclasses import dataclass, field

GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
SIZES = (10, 125, 126, 1000, 65535, 65536)
MESSAGES_PER_CONNECTION = 200


@dataclass
class Counts:
    """What the clients saw."""

    ws_messages: int = 0
    ws_errors: int = 0
    slow_clients: int = 0
    aborted_streams: int = 0
    errors: list[str] = field(default_factory=list)

    def error(self, text: str) -> None:
        """Keep the first 20 error texts."""
        if len(self.errors) < 20:
            self.errors.append(text)


def frame(opcode: int, payload: bytes) -> bytes:
    """Build a masked client frame."""
    size = len(payload)
    if size < 126:
        head = struct.pack("!BB", 0x80 | opcode, 0x80 | size)
    elif size < 65536:
        head = struct.pack("!BBH", 0x80 | opcode, 0x80 | 126, size)
    else:
        head = struct.pack("!BBQ", 0x80 | opcode, 0x80 | 127, size)
    mask = os.urandom(4)
    body = bytes(b ^ mask[i % 4] for i, b in enumerate(payload)) if size < 4096 else _mask_big(payload, mask)
    return head + mask + body


def _mask_big(payload: bytes, mask: bytes) -> bytes:
    """XOR a large payload with the 4-byte mask, fast."""
    key = (mask * (len(payload) // 4 + 1))[: len(payload)]
    xored = int.from_bytes(payload, "big") ^ int.from_bytes(key, "big")
    return xored.to_bytes(len(payload), "big")


async def read_frame(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    """Read one unmasked server frame, return (opcode, payload)."""
    b1, b2 = await reader.readexactly(2)
    size = b2 & 0x7F
    if size == 126:
        (size,) = struct.unpack("!H", await reader.readexactly(2))
    elif size == 127:
        (size,) = struct.unpack("!Q", await reader.readexactly(8))
    return b1 & 0x0F, await reader.readexactly(size)


async def handshake(host: str, port: int):
    """Open a socket and upgrade it to WebSocket on /ws."""
    reader, writer = await asyncio.open_connection(host, port)
    key = base64.b64encode(os.urandom(16))
    writer.write(
        b"GET /ws HTTP/1.1\r\nHost: %b:%d\r\nUpgrade: websocket\r\n"
        b"Connection: Upgrade\r\nSec-WebSocket-Version: 13\r\n"
        b"Sec-WebSocket-Key: %b\r\n\r\n" % (host.encode(), port, key)
    )
    head = await reader.readuntil(b"\r\n\r\n")
    accept = base64.b64encode(hashlib.sha1(key + GUID).digest())
    if b" 101 " not in head.split(b"\r\n")[0] or accept not in head:
        raise ValueError(f"bad handshake: {head[:80]!r}")
    return reader, writer


async def ws_session(host: str, port: int, counts: Counts, end: float) -> None:
    """One connection: echo messages, then close properly."""
    reader, writer = await handshake(host, port)
    try:
        for _ in range(MESSAGES_PER_CONNECTION):
            if time.monotonic() >= end:
                break
            await echo_once(reader, writer, counts)
        writer.write(frame(0x8, struct.pack("!H", 1000)))
        await writer.drain()
        await read_close(reader)
    finally:
        writer.close()


async def read_close(reader: asyncio.StreamReader) -> None:
    """Wait for the server's close frame; a plain TCP close is accepted too."""
    with contextlib.suppress(asyncio.IncompleteReadError):
        await read_frame(reader)


async def echo_once(reader, writer, counts: Counts) -> None:
    """Send one text or binary message and compare the echo."""
    size = random.choice(SIZES)
    text = random.random() < 0.5
    payload = os.urandom(size)
    if text:
        payload = base64.b64encode(payload)[:size].ljust(size, b"a")
    writer.write(frame(0x1 if text else 0x2, payload))
    await writer.drain()
    opcode, got = await read_frame(reader)
    if opcode != (0x1 if text else 0x2) or got != payload:
        counts.ws_errors += 1
        counts.error(f"ws echo mismatch: opcode {opcode}, {len(got)} of {size} bytes")
    else:
        counts.ws_messages += 1


async def ws_loop(host: str, port: int, counts: Counts, end: float) -> None:
    """Keep one WebSocket client reconnecting until the end."""
    while time.monotonic() < end:
        try:
            await ws_session(host, port, counts, end)
        except Exception as exc:
            counts.ws_errors += 1
            counts.error(f"ws: {type(exc).__name__}: {exc}")
            await asyncio.sleep(0.1)


async def slow_once(host: str, port: int, counts: Counts) -> None:
    """Send GET /plain a few bytes at a time, then read the answer."""
    reader, writer = await asyncio.open_connection(host, port)
    try:
        request = b"GET /plain HTTP/1.1\r\nHost: soak\r\nConnection: close\r\n\r\n"
        for i in range(0, len(request), 4):
            writer.write(request[i : i + 4])
            await writer.drain()
            await asyncio.sleep(0.05)
        await reader.read()
        counts.slow_clients += 1
    except (ConnectionResetError, BrokenPipeError):
        counts.slow_clients += 1  # the server's timeouts may close it
    finally:
        writer.close()


async def slow_loop(host: str, port: int, counts: Counts, end: float) -> None:
    """Run slow clients one after another until the end."""
    while time.monotonic() < end:
        try:
            await slow_once(host, port, counts)
        except OSError as exc:
            counts.error(f"slow: {type(exc).__name__}: {exc}")
            await asyncio.sleep(0.1)


async def abort_once(host: str, port: int, counts: Counts) -> None:
    """Start GET /stream, read a few KiB, then drop the socket."""
    reader, writer = await asyncio.open_connection(host, port)
    try:
        writer.write(b"GET /stream HTTP/1.1\r\nHost: soak\r\n\r\n")
        await writer.drain()
        await reader.readuntil(b"\r\n")
        await reader.readexactly(4096)
        writer.transport.abort()
        counts.aborted_streams += 1
    finally:
        writer.close()


async def abort_loop(host: str, port: int, counts: Counts, end: float) -> None:
    """Run aborting clients one after another until the end."""
    while time.monotonic() < end:
        try:
            await abort_once(host, port, counts)
        except (OSError, asyncio.IncompleteReadError) as exc:
            counts.error(f"abort: {type(exc).__name__}: {exc}")
            await asyncio.sleep(0.1)


async def run(host: str, port: int, duration: float) -> Counts:
    """Run all the clients for `duration` seconds and return the counts."""
    counts = Counts()
    end = time.monotonic() + duration
    loops = [(ws_loop, 4), (slow_loop, 2), (abort_loop, 2)]
    await asyncio.gather(*(fn(host, port, counts, end) for fn, n in loops for _ in range(n)))
    return counts


if __name__ == "__main__":
    print(asyncio.run(run(sys.argv[1], int(sys.argv[2]), float(sys.argv[3]))))
