"""Tests for the built-in server (App.run), against a real server process."""

import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the built-in server needs Linux or macOS")

SERVER_APP = Path(__file__).parent / "server_app.py"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start(port, **env):
    process = subprocess.Popen(
        [sys.executable, str(SERVER_APP), str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env={**os.environ, **env},
    )
    for _ in range(100):
        if process.poll() is not None:
            return process
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.1).close()
            return process
        except OSError:
            time.sleep(0.05)
    process.kill()
    raise RuntimeError("the server did not start")


@pytest.fixture(scope="module")
def static_dir(tmp_path_factory):
    directory = tmp_path_factory.mktemp("static")
    (directory / "big.bin").write_bytes(os.urandom(3_000_000))
    return directory


@pytest.fixture(scope="module")
def server(static_dir):
    port = free_port()
    process = start(port, STATIC_DIR=str(static_dir))
    yield f"http://127.0.0.1:{port}", process
    if process.poll() is None:
        process.kill()


def fetch(url, method="GET", data=None, headers=None):
    """Returns (status, headers, body) without raising on HTTP errors."""
    request = urllib.request.Request(url, method=method, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def test_routes_with_typed_params(server):
    base, _ = server
    status, headers, body = fetch(f"{base}/users/42")
    assert (status, json.loads(body)) == (200, {"id": 42})
    assert headers["content-type"] == "application/json"


def test_not_found_and_method_not_allowed(server):
    base, _ = server
    assert fetch(f"{base}/users/abc")[0] == 404
    status, headers, _ = fetch(f"{base}/users/1", method="DELETE")
    assert status == 405 and headers["allow"] == "GET"


def test_waiting_handlers_run_concurrently(server):
    base, _ = server
    started = time.perf_counter()
    with ThreadPoolExecutor(20) as pool:
        results = list(pool.map(lambda _: fetch(f"{base}/wait"), range(20)))
    elapsed = time.perf_counter() - started
    assert all(r[0] == 200 and r[2] == b"waited" for r in results)
    assert elapsed < 1.5, f"20 handlers waiting 0.2 s each took {elapsed:.2f} s: they ran one at a time"


def test_many_concurrent_clients_all_get_answers(server):
    """Regression test: a lost wake-up between the Rust thread and the
    asyncio loop used to freeze the server under concurrent load."""
    base, _ = server

    def client(n):
        return [fetch(f"{base}/users/{n * 100 + i}")[0] for i in range(20)]

    with ThreadPoolExecutor(50) as pool:
        statuses = [status for batch in pool.map(client, range(50)) for status in batch]
    assert statuses == [200] * 1000


def test_streaming(server):
    base, _ = server
    assert fetch(f"{base}/stream")[2] == b"0\n1\n2\n"


def test_large_static_file_arrives_intact(server, static_dir):
    base, _ = server
    status, headers, body = fetch(f"{base}/static/big.bin")
    assert status == 200 and headers["content-length"] == "3000000"
    assert hashlib.sha256(body).digest() == hashlib.sha256((static_dir / "big.bin").read_bytes()).digest()
    assert fetch(f"{base}/static/../server_app.py")[0] == 404


def test_request_body_query_and_cookies(server):
    base, _ = server
    _, _, body = fetch(f"{base}/echo?tag=a&tag=b", method="POST", data=b"hello", headers={"Cookie": "session=abc"})
    assert json.loads(body) == {"body": "hello", "query": {"tag": ["a", "b"]}, "cookies": {"session": "abc"}}


def test_head_sends_headers_only(server):
    base, _ = server
    status, headers, body = fetch(f"{base}/users/42", method="HEAD")
    assert (status, body) == (200, b"")
    assert headers["content-length"] == str(len(b'{"id": 42}'))


def test_errors(server):
    base, _ = server
    assert fetch(f"{base}/fail")[0] == 500
    assert fetch(f"{base}/teapot")[0] == 418


def test_sigterm_runs_shutdown_hooks_and_exits():
    port = free_port()
    process = start(port)
    assert fetch(f"http://127.0.0.1:{port}/users/1")[0] == 200
    process.send_signal(signal.SIGTERM)
    output, _ = process.communicate(timeout=5)
    assert process.returncode == 0
    assert "STARTED" in output and "STOPPED" in output


def test_failing_startup_hook_prevents_serving():
    port = free_port()
    process = start(port, FAIL_STARTUP="1")
    output, _ = process.communicate(timeout=5)
    assert process.returncode != 0
    assert "database unreachable" in output
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=0.5)
