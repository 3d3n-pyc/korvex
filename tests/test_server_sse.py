"""Server-Sent Events through the built-in server, against a real server process."""

import socket
import sys
import time

import pytest
from test_server import free_port, start

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the built-in server needs Linux or macOS")


def open_stream(port, path):
    """Sends a GET and returns the socket."""
    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    sock.sendall(f"GET {path} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
    return sock


def read_until(sock, needle, count=1):
    """Reads until `needle` appeared `count` times; returns everything read."""
    data = b""
    while data.count(needle) < count:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    return data


def wait_for(path, text, seconds=3):
    """Waits until the marker file contains `text`."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if path.exists() and text in path.read_text():
            return True
        time.sleep(0.05)
    return False


def test_headers_events_and_pings_then_cleanup_after_disconnect(tmp_path):
    marker = tmp_path / "marker"
    port = free_port()
    process = start(port, SSE_MARKER=str(marker))
    try:
        sock = open_stream(port, "/sse")
        data = read_until(sock, b": ping", 2)
        head = data.lower()
        assert b"200" in data.split(b"\r\n")[0]
        assert b"content-type: text/event-stream" in head
        assert b"cache-control: no-cache" in head
        assert b"id: 1\ndata: hello\n\n" in data
        assert not marker.exists() or "generator closed" not in marker.read_text()
        sock.close()  # the generator is idle: the next ping fails
        assert wait_for(marker, "generator closed", 2)
        assert wait_for(marker, "dependency closed", 1)
        assert wait_for(marker, "background ran", 1)
    finally:
        process.kill()
        process.wait()


def test_a_sync_generator_streams():
    port = free_port()
    process = start(port)
    try:
        sock = open_stream(port, "/sse-sync")
        data = read_until(sock, b"data: ", 3)
        assert b"data: 0\n\n" in data and b"data: 2\n\n" in data
        sock.close()
    finally:
        process.kill()
        process.wait()


def test_an_encoding_error_cuts_the_stream_without_a_clean_end():
    port = free_port()
    process = start(port)
    try:
        sock = open_stream(port, "/sse-bad")
        data = read_until(sock, b"0\r\n\r\n")
        assert not data.endswith(b"0\r\n\r\n")  # cut, not ended: the bad event is never sent
    finally:
        process.kill()
        process.wait()
