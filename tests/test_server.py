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
            if "UDS" in env:
                with socket.socket(socket.AF_UNIX) as probe:
                    probe.connect(env["UDS"])
            else:
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


def test_after_request_hooks_apply_to_the_built_in_server(server):
    base, _ = server
    assert fetch(f"{base}/users/1")[1]["x-powered-by"] == "korvex"
    assert fetch(f"{base}/missing")[1]["x-powered-by"] == "korvex"


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


def stop_and_collect(process):
    process.send_signal(signal.SIGTERM)
    output, _ = process.communicate(timeout=10)
    return process.returncode, output


def test_workers_each_start_and_serve():
    port = free_port()
    process = start(port, WORKERS="3")
    base = f"http://127.0.0.1:{port}"
    pids = {fetch(f"{base}/pid")[2] for _ in range(60)}
    code, output = stop_and_collect(process)
    assert code == 0
    assert output.count("STARTED") == 3 and output.count("STOPPED") == 3
    assert len(pids) >= 2, "requests should be spread over the workers"


def test_crashed_worker_is_replaced():
    port = free_port()
    process = start(port, WORKERS="2", STARTUP_WINDOW="0.3")
    base = f"http://127.0.0.1:{port}"
    time.sleep(0.5)  # past the startup window: this crash isn't a startup failure
    with pytest.raises(OSError):
        urllib.request.urlopen(f"{base}/crash", timeout=5)
    time.sleep(0.5)
    assert all(fetch(f"{base}/users/1")[0] == 200 for _ in range(20))
    code, output = stop_and_collect(process)
    assert code == 0
    assert "starting a new one" in output
    assert output.count("STARTED") == 3  # 2 workers, then 1 replacement


def test_worker_failing_to_start_stops_everything():
    port = free_port()
    process = start(port, WORKERS="2", FAIL_STARTUP="1")
    output, _ = process.communicate(timeout=10)
    assert process.returncode == 1
    assert "failed to start" in output


@pytest.mark.parametrize("workers", ["1", "2"])
def test_sigterm_lets_requests_in_progress_finish(workers):
    port = free_port()
    process = start(port, WORKERS=workers)
    base = f"http://127.0.0.1:{port}"
    with ThreadPoolExecutor(1) as pool:
        slow = pool.submit(fetch, f"{base}/slow")
        time.sleep(0.3)  # the request is now being handled
        process.send_signal(signal.SIGTERM)
        time.sleep(0.2)
        with pytest.raises(OSError):  # no new connections once stopping
            socket.create_connection(("127.0.0.1", port), timeout=0.5)
        assert slow.result(timeout=5) == (200, slow.result()[1], b"slow done")
    output, _ = process.communicate(timeout=10)
    assert process.returncode == 0 and "STOPPED" in output


def test_graceful_timeout_gives_up_on_slow_requests():
    port = free_port()
    process = start(port, GRACE="0.2")
    base = f"http://127.0.0.1:{port}"
    with ThreadPoolExecutor(1) as pool:
        slow = pool.submit(fetch, f"{base}/slow")
        time.sleep(0.2)
        stopped_at = time.perf_counter()
        process.send_signal(signal.SIGTERM)
        process.communicate(timeout=10)
        assert time.perf_counter() - stopped_at < 0.8  # didn't wait for the 1 s request
        with pytest.raises(Exception):
            slow.result(timeout=5)
    assert process.returncode == 0


@pytest.fixture(scope="module")
def strict_server():
    """A server with small limits: 1,000-byte bodies, 0.5 s to send them."""
    port = free_port()
    process = start(port, MAX_BODY="1000", TIMEOUT="0.5")
    yield port
    process.kill()


def chunked_post(port, path, chunks):
    import http.client

    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request("POST", path, body=iter(chunks), encode_chunked=True)
    response = connection.getresponse()
    return response.status, response.read()


def test_body_over_the_limit_is_rejected(strict_server):
    base = f"http://127.0.0.1:{strict_server}"
    assert fetch(f"{base}/echo", method="POST", data=b"x" * 1000)[0] == 200
    assert fetch(f"{base}/echo", method="POST", data=b"x" * 1001)[0] == 413  # Content-Length: refused before reading
    assert chunked_post(strict_server, "/echo", [b"x" * 600, b"x" * 600])[0] == 413  # no length announced


def test_a_route_can_raise_its_limit(strict_server):
    base = f"http://127.0.0.1:{strict_server}"
    assert fetch(f"{base}/upload", method="POST", data=b"x" * 50_000)[2] == b"50000"
    assert fetch(f"{base}/api/import", method="POST", data=b"x" * 50_000)[2] == b"50000"  # in a group
    assert chunked_post(strict_server, "/upload", [b"x" * 30_000] * 2) == (200, b"60000")


def test_slow_headers_get_the_connection_closed(strict_server):
    with socket.create_connection(("127.0.0.1", strict_server), timeout=5) as client:
        client.sendall(b"GET /users/1 HTTP/1.1\r\nHost: x\r\n")  # headers never finished
        started = time.perf_counter()
        while client.recv(1024):  # read until the server closes the connection
            pass
        assert time.perf_counter() - started < 2


def test_slow_body_gets_a_408(strict_server):
    with socket.create_connection(("127.0.0.1", strict_server), timeout=5) as client:
        client.sendall(b"POST /echo HTTP/1.1\r\nHost: x\r\nContent-Length: 100\r\n\r\n" + b"x" * 10)
        response = client.recv(1024)
    assert response.startswith(b"HTTP/1.1 408")


def test_trusted_proxy_headers_set_client_and_scheme(server):
    base, _ = server
    headers = {"X-Forwarded-For": "6.6.6.6, 203.0.113.9", "X-Forwarded-Proto": "https"}
    _, _, body = fetch(f"{base}/client", headers=headers)
    # 127.0.0.1 is trusted by default; 6.6.6.6 was written by the client itself.
    assert json.loads(body) == {"client": ["203.0.113.9", 0], "scheme": "https"}
    _, _, body = fetch(f"{base}/client")
    assert json.loads(body)["client"][0] == "127.0.0.1"


def test_proxy_headers_from_untrusted_addresses_are_ignored():
    port = free_port()
    process = start(port, FORWARDED_ALLOW="10.9.9.9")
    try:
        headers = {"X-Forwarded-For": "203.0.113.9", "X-Forwarded-Proto": "https"}
        _, _, body = fetch(f"http://127.0.0.1:{port}/client", headers=headers)
        result = json.loads(body)
        assert result["client"][0] == "127.0.0.1" and result["scheme"] == "http"
    finally:
        process.kill()


def test_unix_socket():
    import http.client
    import tempfile

    # macOS limits socket paths to 104 characters: pytest's tmp_path is too long there.
    path = os.path.join(tempfile.mkdtemp(prefix="kx", dir="/tmp"), "korvex.sock")

    class UnixConnection(http.client.HTTPConnection):
        def connect(self):
            self.sock = socket.socket(socket.AF_UNIX)
            self.sock.connect(path)

    process = start(0, UDS=path)
    assert os.stat(path).st_mode & 0o777 == 0o666  # a proxy running as another user can connect
    connection = UnixConnection("localhost", timeout=5)
    connection.request("GET", "/client", headers={"X-Forwarded-For": "203.0.113.9"})
    response = connection.getresponse()
    # Over a Unix socket, the proxy is trusted: only a local process can connect.
    assert (response.status, json.loads(response.read())) == (200, {"client": ["203.0.113.9", 0], "scheme": "http"})
    connection.close()
    code, _ = stop_and_collect(process)
    assert code == 0
    assert not os.path.exists(path)  # removed on stop


def test_access_log():
    port = free_port()
    process = start(port, ACCESS_LOG="1")
    fetch(f"http://127.0.0.1:{port}/users/1?verbose=1")
    _, output = stop_and_collect(process)
    assert '127.0.0.1 "GET /users/1?verbose=1" 200' in output


def test_command_line_serves_an_app():
    port = free_port()
    command = [sys.executable, "-m", "korvex", "run", "server_app:app", "--app-dir", str(SERVER_APP.parent)]
    process = subprocess.Popen(
        [*command, "--port", str(port), "--workers", "2", "--access-log"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=SERVER_APP.parent,  # from the repo root, `-m korvex` would import the source folder
    )
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.1).close()
            break
        except OSError:
            time.sleep(0.05)
    status, _, body = fetch(f"http://127.0.0.1:{port}/users/7")
    code, output = stop_and_collect(process)
    assert (status, json.loads(body), code) == (200, {"id": 7}, 0)
    assert output.count("STARTED") == 2 and '"GET /users/7" 200' in output


@pytest.mark.parametrize(
    ("target", "message"),
    [("no_such_module:app", "can't find module"), ("server_app:startup", "isn't a korvex App")],
)
def test_command_line_explains_a_wrong_target(target, message):
    command = [sys.executable, "-m", "korvex", "run", target, "--app-dir", str(SERVER_APP.parent)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=10, cwd=SERVER_APP.parent)
    assert result.returncode != 0 and message in result.stderr
