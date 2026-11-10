"""A stream that fails mid-way through the built-in server: the connection is cut, not ended cleanly."""

import socket
import subprocess
import sys
import time

import pytest
from test_server import free_port

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the built-in server needs Linux or macOS")

APP = """
import asyncio
import sys
import time
from korvex import App, Response, ServerSentEvent

app = App()


def broken():
    yield b"one"
    yield b"two"
    time.sleep(0.3)  # let the client receive the pieces first
    raise RuntimeError("boom")


async def abroken():
    yield b"one"
    yield b"two"
    await asyncio.sleep(0.3)
    raise RuntimeError("boom")


async def slow_events():
    yield ServerSentEvent(data="ok")
    await asyncio.sleep(0.3)
    yield object()


@app.get("/async")
async def chunked_async():
    return Response.stream(abroken())


@app.get("/sync")
async def chunked_sync():
    return Response.stream(broken())


@app.get("/length")
async def with_length():
    return Response.stream(broken(), headers=[("content-length", "100")])


@app.get("/sse")
async def events():
    return Response.sse(slow_events(), ping=60)


@app.get("/ok")
async def ok():
    return Response.text("ok")


app.run(port=int(sys.argv[1]), workers=1, graceful_timeout=2)
"""


@pytest.fixture
def server(tmp_path):
    """Starts a server with failing streams; yields (port, process)."""
    script = tmp_path / "app.py"
    script.write_text(APP)
    port = free_port()
    process = subprocess.Popen(
        [sys.executable, str(script), str(port)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.1).close()
            break
        except OSError:
            time.sleep(0.05)
    yield port, process
    process.kill()
    process.wait()
    process.stdout.close()


def get(port, path):
    """Sends a GET on a new connection; returns everything read until it closes."""
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(f"GET {path} HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n".encode())
        data = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                return data
            data += chunk


def output(process, needle):
    """Kills the server and returns its output, which must contain `needle`."""
    process.kill()
    out = process.communicate()[0]
    assert needle in out
    assert "never retrieved" not in out
    return out


@pytest.mark.parametrize("path", ["/async", "/sync"])
def test_a_chunked_stream_that_fails_is_cut(server, path):
    port, process = server
    data = get(port, path)
    assert b"one" in data and b"two" in data
    assert not data.endswith(b"0\r\n\r\n")
    assert get(port, "/ok").startswith(b"HTTP/1.1 200")
    out = output(process, "error while streaming GET " + path)
    assert "RuntimeError: boom" in out


def test_a_stream_with_a_length_ends_short(server):
    port, process = server
    data = get(port, "/length")
    body = data.split(b"\r\n\r\n", 1)[1]
    assert 0 < len(body) < 100
    assert get(port, "/ok").startswith(b"HTTP/1.1 200")
    output(process, "error while streaming GET /length")


def test_a_bad_sse_event_cuts_the_stream(server):
    port, process = server
    data = get(port, "/sse")
    assert b"data: ok" in data
    assert not data.endswith(b"0\r\n\r\n")
    assert get(port, "/ok").startswith(b"HTTP/1.1 200")
    output(process, "error while streaming GET /sse")
