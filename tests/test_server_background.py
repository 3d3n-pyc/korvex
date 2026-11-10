"""Background tasks through the built-in server, against a real server process."""

import http.client
import signal
import sys
import time

import pytest
from test_server import free_port, start

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the built-in server needs Linux or macOS")


def get(connection, path="/background"):
    connection.request("GET", path)
    response = connection.getresponse()
    return response.status, response.read()


def test_response_and_next_request_do_not_wait_for_the_task(tmp_path):
    marker = tmp_path / "marker"
    port = free_port()
    process = start(port, BG_MARKER=str(marker), BG_SECONDS="1")
    try:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        begin = time.monotonic()
        assert get(connection) == (200, b'{"queued": true}')
        assert time.monotonic() - begin < 0.5
        assert get(connection, "/users/1")[0] == 200  # same keep-alive connection
        assert time.monotonic() - begin < 0.5
        assert not marker.exists()
        time.sleep(1.3)
        assert marker.read_text() == "done"
    finally:
        process.kill()
        process.wait()


def test_shutdown_waits_for_a_running_task(tmp_path):
    marker = tmp_path / "marker"
    port = free_port()
    process = start(port, BG_MARKER=str(marker), BG_SECONDS="1", GRACE="10")
    try:
        assert get(http.client.HTTPConnection("127.0.0.1", port, timeout=5))[0] == 200
        process.send_signal(signal.SIGTERM)
        output, _ = process.communicate(timeout=10)
    finally:
        process.kill()
    assert marker.read_text() == "done"
    assert "cut off" not in output


def test_shutdown_cuts_off_a_task_past_the_graceful_timeout(tmp_path):
    marker = tmp_path / "marker"
    port = free_port()
    process = start(port, BG_MARKER=str(marker), BG_SECONDS="3", GRACE="0.5")
    try:
        assert get(http.client.HTTPConnection("127.0.0.1", port, timeout=5))[0] == 200
        process.send_signal(signal.SIGTERM)
        output, _ = process.communicate(timeout=10)
    finally:
        process.kill()
    assert not marker.exists()
    assert "cut off 1 background task" in output


STUCK_APP = """
import asyncio, os, sys, time
from korvex import App, BackgroundTasks, Response

app = App()
shutdown_marker = os.environ["SHUTDOWN_MARKER"]


async def stubborn():
    while True:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass


def blocking():
    time.sleep(1000)


@app.get("/async")
async def stuck_async(request, tasks: BackgroundTasks):
    tasks.add(stubborn)
    return Response.json({})


@app.get("/sync")
def stuck_sync(request, tasks: BackgroundTasks):
    tasks.add(blocking)
    return Response.json({})


@app.get("/stream")
async def stream(request, tasks: BackgroundTasks):
    async def chunks():
        while True:
            yield b"x"
            await asyncio.sleep(0.2)

    tasks.add(stubborn)
    return Response.stream(chunks())


@app.on_shutdown
def done():
    open(shutdown_marker, "w").write("hook")


app.run(port=int(sys.argv[1]), graceful_timeout=0.5, threads=int(os.environ.get("THREADS", "1")))
"""


def start_stuck(tmp_path, **env):
    import subprocess

    script = tmp_path / "stuck_app.py"
    script.write_text(STUCK_APP)
    port = free_port()
    process = subprocess.Popen(
        [sys.executable, str(script), str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env={**__import__("os").environ, "SHUTDOWN_MARKER": str(tmp_path / "hook"), **env},
    )
    for _ in range(100):
        try:
            __import__("socket").create_connection(("127.0.0.1", port), timeout=0.1).close()
            return process, port
        except OSError:
            time.sleep(0.05)
    process.kill()
    raise RuntimeError("the server did not start")


@pytest.mark.parametrize("path", ["/async", "/sync"])
@pytest.mark.parametrize("threads", ["1", "2"])
def test_shutdown_is_bounded_with_a_stuck_task(tmp_path, path, threads):
    process, port = start_stuck(tmp_path, THREADS=threads)
    try:
        assert get(http.client.HTTPConnection("127.0.0.1", port, timeout=5), path)[0] == 200
        begin = time.monotonic()
        process.send_signal(signal.SIGTERM)
        output, _ = process.communicate(timeout=10)
    finally:
        process.kill()
    assert time.monotonic() - begin < 4
    assert "cut off" in output
    assert (tmp_path / "hook").read_text() == "hook"


def test_second_signal_exits_at_once(tmp_path):
    process, port = start_stuck(tmp_path)
    try:
        assert get(http.client.HTTPConnection("127.0.0.1", port, timeout=5), "/async")[0] == 200
        process.send_signal(signal.SIGTERM)
        time.sleep(0.2)
        process.send_signal(signal.SIGINT)
        process.communicate(timeout=1)
    finally:
        process.kill()


def test_tasks_of_a_stream_open_at_shutdown_are_dropped(tmp_path):
    process, port = start_stuck(tmp_path)
    try:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request("GET", "/stream")
        connection.getresponse().read(1)
        process.send_signal(signal.SIGTERM)
        output, _ = process.communicate(timeout=10)
    finally:
        process.kill()
    assert "Task was destroyed" not in output
    assert "dropped 1 background task" in output or "cut off" in output
