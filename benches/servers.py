"""Benchmark: servers that run a Korvex-style app, on the same CPU cores.

Each server gets the same 4 CPU cores (oha, the load generator, runs on 8
others) and the same app as the load test (1,000 routes, one JSON
endpoint). Worker counts are tuned per server: each is tried with 3 and 4
workers (2 too for Korvex) and its best result is kept, so no server
loses to a default that doesn't suit it.

- uvicorn and Granian (Rust) serve the Korvex ASGI app.
- Robyn serves its own version of the app with its own server (Rust).
- Korvex's built-in server (App.run) serves the Korvex app.

Needs oha (`cargo install oha`).
Run everything (about 4 minutes): uv run --group bench python benches/servers.py
Run some servers only:            uv run --group bench python benches/servers.py robyn korvex
Results are merged into benches/servers_results.json.
"""

import contextlib
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import psutil

PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
URLS = f"{BASE}/users/[1-9][0-9]{{0,6}}"
SERVER_CPUS = "2-5"  # 4 cores
CLIENT_CPUS = "8-15"
CONCURRENCY = [200, 1000]
SECONDS = 8
RESULTS = Path("benches/servers_results.json")
PY = sys.executable

UVICORN = [PY, "-m", "uvicorn", "benches.load.korvex_app:app", "--port", str(PORT), "--log-level", "warning"]
UVICORN += ["--no-access-log", "--loop", "uvloop", "--http", "httptools", "--workers"]
GRANIAN = [PY, "-m", "granian", "--interface", "asgi", "--runtime-threads", "1", "--http", "1", "--loop", "uvloop"]
GRANIAN += ["--port", str(PORT), "--log-level", "warning", "benches.load.korvex_app:app", "--workers"]
# Robyn's own setting: 2 threads per process did best (4 processes x 2: 91k
# req/s, against 66k with 1 thread and 38k or less with 2 processes).
ROBYN = [PY, "benches/load/robyn_app.py", "--workers", "2", "--disable-openapi", "--log-level", "WARN", "--processes"]
KORVEX = [PY, "-m", "korvex", "run", "korvex_app:app", "--app-dir", "benches/load", "--port", str(PORT), "--workers"]

SERVERS = {
    "uvicorn": (UVICORN, [3, 4]),
    "granian": (GRANIAN, [3, 4]),
    "robyn": (ROBYN, [3, 4]),
    "korvex": (KORVEX, [2, 3, 4]),
}


def tree(pid):
    root = psutil.Process(pid)
    return [root, *root.children(recursive=True)]


def start(command):
    process = subprocess.Popen(
        ["taskset", "-c", SERVER_CPUS, *command],
        env={**os.environ, "PORT": str(PORT)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(300):
        try:
            with urllib.request.urlopen(f"{BASE}/users/42", timeout=1) as response:
                if json.loads(response.read()) == {"id": 42, "name": "user 42"}:
                    time.sleep(1)  # let every worker finish starting
                    return process
        except OSError:
            time.sleep(0.1)
    process.kill()
    sys.exit(f"{command} did not start")


def stop(process):
    processes = tree(process.pid)
    for p in processes:
        p.terminate()
    _, alive = psutil.wait_procs(processes, timeout=10)
    for p in alive:
        p.kill()


def load(pid, connections):
    """Runs oha; returns requests/s, p99 latency and the server's peak memory."""
    peak = 0
    done = threading.Event()

    def sample():
        nonlocal peak
        while not done.is_set():
            with contextlib.suppress(psutil.Error):
                peak = max(peak, sum(p.memory_info().rss for p in tree(pid)))
            time.sleep(0.25)

    sampler = threading.Thread(target=sample)
    sampler.start()
    command = ["taskset", "-c", CLIENT_CPUS, "oha", "--no-tui", "--output-format", "json", "--rand-regex-url"]
    report = json.loads(
        subprocess.run(
            [*command, "-z", f"{SECONDS}s", "-c", str(connections), URLS], capture_output=True, text=True, check=True
        ).stdout
    )
    done.set()
    sampler.join()
    statuses = report.get("statusCodeDistribution", {})
    return {
        "connections": connections,
        "requests_per_second": report["summary"]["requestsPerSec"],
        "p99_ms": report["latencyPercentiles"]["p99"] * 1000,
        "failed": sum(n for code, n in statuses.items() if code != "200"),
        "peak_rss_mb": peak / 2**20,
    }


def main():
    names = sys.argv[1:] or list(SERVERS)
    results = json.loads(RESULTS.read_text()) if RESULTS.exists() else {}
    for name in names:
        command, worker_counts = SERVERS[name]
        for stale in [label for label in results if label.startswith(f"{name},")]:
            del results[stale]
        for workers in worker_counts:
            label = f"{name}, {workers} workers"
            print(f"Testing {label}...", flush=True)
            process = start([*command, str(workers)])
            try:
                results[label] = [load(process.pid, c) for c in CONCURRENCY]
            finally:
                stop(process)
            RESULTS.write_text(json.dumps(results, indent=2))

    print(f"\n{'server':22}" + "".join(f"{c:>12} clients" for c in CONCURRENCY) + f"{'p99 @1000':>12}{'RAM':>10}")
    for label, runs in results.items():
        cells = "".join(f"{r['requests_per_second']:>14,.0f} req/s"[:20].rjust(20) for r in runs)
        print(f"{label:22}{cells}{runs[-1]['p99_ms']:>10.1f}ms{max(r['peak_rss_mb'] for r in runs):>8.0f}MB")


if __name__ == "__main__":
    main()
