"""A client flooding WebSocket messages can't grow the server's memory."""

import base64
import os
import socket
import struct
import sys
import threading
import time
from pathlib import Path

import pytest
from test_server import free_port, start

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="measures the server's memory through /proc"
)

PIECE = 64 * 1024
TOTAL = 50 * 1024 * 1024
COUNT = TOTAL // PIECE

APP = """
import asyncio
import sys

from korvex import App

app = App()


@app.websocket("/slow")
async def slow(ws):
    await ws.accept()
    await asyncio.sleep(4)  # the client floods meanwhile
    expected = 0
    async for message in ws:
        if isinstance(message, str):
            break
        assert int.from_bytes(message[:4], "big") == expected, "out of order"
        assert message[4:] == bytes([expected % 251]) * (len(message) - 4), "corrupted"
        expected += 1
    await ws.send_text(f"received {expected}")


if __name__ == "__main__":
    app.run(port=int(sys.argv[1]), access_log=False)
"""


def rss_kib(pid: int) -> int:
    """Resident memory of a process and its children, in KiB."""
    total = 0
    for task in Path(f"/proc/{pid}/task").iterdir():
        for child in (task / "children").read_text().split():
            total += rss_kib(int(child))
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            total += int(line.split()[1])
    return total


def frame(opcode: int, payload: bytes) -> bytes:
    """A masked client frame (the mask is zero: the payload goes as is)."""
    head = bytes([0x80 | opcode])
    size = len(payload)
    head += bytes([0x80 | size]) if size < 126 else b"\xff" + struct.pack(">Q", size)
    return head + b"\0\0\0\0" + payload


def connect(port: int) -> socket.socket:
    """Opens a WebSocket to /slow with a raw handshake."""
    sock = socket.create_connection(("127.0.0.1", port))
    key = base64.b64encode(os.urandom(16)).decode()
    sock.sendall(
        (
            f"GET /slow HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        ).encode()
    )
    reply = b""
    while b"\r\n\r\n" not in reply:
        reply += sock.recv(1024)
    assert reply.startswith(b"HTTP/1.1 101")
    return sock


def flood(sock: socket.socket) -> None:
    """Sends COUNT numbered binary messages, then a text one to end."""
    for index in range(COUNT):
        body = index.to_bytes(4, "big") + bytes([index % 251]) * (PIECE - 4)
        sock.sendall(frame(2, body))
    sock.sendall(frame(1, b"done"))


def test_a_flood_waits_for_the_handler_and_loses_nothing(tmp_path):
    script = tmp_path / "app.py"
    script.write_text(APP)
    port = free_port()
    process = start_script(script, port)
    try:
        sock = connect(port)
        before = rss_kib(process.pid)
        sender = threading.Thread(target=flood, args=(sock,), daemon=True)
        sender.start()
        time.sleep(2.5)  # the handler is still asleep
        grown = (rss_kib(process.pid) - before) / 1024
        print(f"grown {grown:.1f} MiB")
        assert sender.is_alive(), "the whole flood fit in the server: nothing held it back"
        assert grown < 20, f"the server grew by {grown:.1f} MiB"
        sender.join(timeout=30)
        assert not sender.is_alive()
        sock.settimeout(10)
        data = b""
        while b"received" not in data:
            data += sock.recv(4096)
        assert f"received {COUNT}".encode() in data
        sock.close()
    finally:
        process.kill()


def start_script(script: Path, port: int):
    """Starts the app like test_server.start() does, from another script."""
    import test_server

    original = test_server.SERVER_APP
    test_server.SERVER_APP = script
    try:
        return start(port)
    finally:
        test_server.SERVER_APP = original
