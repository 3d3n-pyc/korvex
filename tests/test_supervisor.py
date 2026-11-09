"""The multi-worker supervisor: escalation to SIGKILL and restart backoff."""

import re
import signal
import socket
import subprocess
import sys
import time

import pytest

from korvex._serve import BACKOFF_MAX, BACKOFF_START, restart_delay

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the supervisor forks: POSIX only")

APP = """
import sys, time
import korvex._serve
from korvex import App

app = App()
MODE = sys.argv[2]
if MODE == "crash":
    korvex._serve.STARTUP_WINDOW = 0  # every crash is a restart, not a failed start


@app.on_startup
def startup():
    print("STARTED", flush=True)
    if MODE == "crash":
        raise SystemExit(1)


@app.on_shutdown
def shutdown():
    if MODE == "stuck":
        time.sleep(1000)


app.run(port=int(sys.argv[1]), workers=2, graceful_timeout=1)
"""


def launch(tmp_path, mode):
    """Starts the app in `mode` and waits until its two workers are up."""
    script = tmp_path / "app.py"
    script.write_text(APP)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    process = subprocess.Popen(
        [sys.executable, str(script), str(port), mode],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if mode == "stuck":
        assert process.stdout is not None
        for _ in range(2):
            assert process.stdout.readline().strip() == "STARTED"
    return process


def test_stuck_workers_are_killed_after_the_graceful_timeout_plus_5_seconds(tmp_path):
    process = launch(tmp_path, "stuck")
    started = time.monotonic()
    process.send_signal(signal.SIGTERM)
    process.communicate(timeout=15)
    elapsed = time.monotonic() - started
    assert 5.5 < elapsed < 9  # graceful_timeout 1 + 5 s, then SIGKILL


def test_a_second_signal_kills_the_workers_at_once(tmp_path):
    process = launch(tmp_path, "stuck")
    process.send_signal(signal.SIGINT)
    time.sleep(0.5)
    started = time.monotonic()
    process.send_signal(signal.SIGINT)
    process.communicate(timeout=15)
    assert time.monotonic() - started < 3


def test_a_crash_looping_worker_is_restarted_after_growing_delays(tmp_path):
    process = launch(tmp_path, "crash")
    time.sleep(2)
    process.send_signal(signal.SIGTERM)
    output, _ = process.communicate(timeout=15)
    delays = [float(d) for d in re.findall(r"starting a new one in ([\d.]+)s", output)]
    assert delays[:3] == [0.1, 0.2, 0.4]
    assert delays == sorted(delays)


def test_restart_delay_doubles_to_a_cap_and_resets_after_a_long_life():
    delay = 0.0
    seen = []
    for _ in range(12):
        delay = restart_delay(delay, 1)
        seen.append(delay)
    assert seen[:4] == [BACKOFF_START, 0.2, 0.4, 0.8]
    assert seen[-1] == BACKOFF_MAX
    assert restart_delay(BACKOFF_MAX, 30) == BACKOFF_START


def test_a_clean_exit_is_replaced_at_once_without_backoff():
    from korvex._serve import _Supervisor

    supervisor = _Supervisor(None, None, 1.0, None)  # type: ignore[arg-type]
    supervisor.children[42] = time.monotonic() - 60
    supervisor.on_exit(42, 0)  # exit code 0
    assert supervisor.delay == 0.0
    assert len(supervisor.restarts) == 1
    assert supervisor.restarts[0] <= time.monotonic()
