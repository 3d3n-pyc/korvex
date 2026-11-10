"""Tests for app.mount() under the built-in server, against a real server process."""

import socket
import sys
import time

import pytest

from tests.test_server import WebSocketClient, fetch, free_port, start

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the built-in server needs Linux or macOS")


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    port = free_port()
    marker = tmp_path_factory.mktemp("mount") / "disconnect"
    process = start(port, DISCONNECT_FILE=str(marker))
    yield f"http://127.0.0.1:{port}", port, marker
    if process.poll() is None:
        process.kill()


def raw_get(port, target, method="GET"):
    """Sends one request, reads the whole reply (the server closes it)."""
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(f"{method} {target} HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n".encode())
        data = b""
        while chunk := sock.recv(65536):
            data += chunk
    return data


def test_a_simple_body_arrives_intact(server):
    status, headers, body = fetch(f"{server[0]}/raw/hello")
    assert (status, body) == (200, b"hello from the mount")
    assert headers["content-type"] == "text/plain"
    assert raw_get(server[1], "/raw/hello").lower().count(b"content-length") == 1


def test_a_streamed_body_arrives_chunked_and_intact(server):
    head = raw_get(server[1], "/raw/stream").partition(b"\r\n\r\n")[0]
    assert b"transfer-encoding: chunked" in head.lower()
    assert fetch(f"{server[0]}/raw/stream")[2] == b"0;1;2;3;4;end"


def test_post_body_status_and_headers_reach_through(server):
    status, headers, body = fetch(f"{server[0]}/raw/echo", method="POST", data=b"payload")
    assert status == 201 and headers["x-sub"] == "1"
    assert body == b"POST /raw/echo /raw /raw/echo payload"


def test_an_encoded_slash_stays_encoded_in_raw_path(server):
    _, _, body = fetch(f"{server[0]}/raw/echo%2Fx", method="POST", data=b"")
    assert body == b"POST /raw/echo/x /raw /raw/echo%2Fx "


def test_the_client_leaving_mid_stream_reaches_the_app(server):
    with socket.create_connection(("127.0.0.1", server[1]), timeout=5) as sock:
        sock.sendall(b"GET /raw/disconnect HTTP/1.1\r\nHost: x\r\n\r\n")
        sock.recv(100)
    for _ in range(100):
        if server[2].exists():
            break
        time.sleep(0.05)
    assert server[2].read_text() == "disconnected"


def test_a_failing_app_gives_500_and_the_worker_keeps_serving(server):
    assert fetch(f"{server[0]}/raw/boom")[0] == 500
    assert fetch(f"{server[0]}/raw/hello")[0] == 200


def test_an_app_that_returns_without_answering_gives_500(server):
    assert fetch(f"{server[0]}/raw/silent")[0] == 500


def test_a_mounted_korvex_app(server):
    status, _, body = fetch(f"{server[0]}/inner/hi")
    assert (status, body) == (200, b"inner /inner")


def test_the_bare_prefix_redirects(server):
    reply = raw_get(server[1], "/raw")
    assert reply.startswith(b"HTTP/1.1 308") and b"location: /raw/" in reply.lower()


def test_head_has_no_body(server):
    reply = raw_get(server[1], "/raw/hello", method="HEAD")
    assert reply.startswith(b"HTTP/1.1 200") and reply.endswith(b"\r\n\r\n")


def test_websocket_echo_through_a_mount(server):
    client = WebSocketClient(server[1], "/raw/chat")
    assert client.status == 101
    client.send_text("hi")
    assert client.recv() == (1, b"mounted: hi")
    client.close()


def test_mount_root_reaches_the_sub_app(server):
    for target in ("/raw/", "/raw//", "/raw/?q=1"):
        status, _, body = fetch(f"{server[0]}{target}")
        assert (status, body) == (200, b"hello from the mount")


def test_double_slash_does_not_reach_the_mount(server):
    assert raw_get(server[1], "//raw/x").startswith(b"HTTP/1.1 404")
    assert WebSocketClient(server[1], "//raw/chat").status != 101
