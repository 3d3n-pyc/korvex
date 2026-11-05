"""Load test: Korvex vs other frameworks, on the same server and the same app.

Each framework serves the app in benches/load/ with uvicorn (one worker,
pinned to one CPU core), and oha (on other cores) sends it requests to
random URLs (/users/<id>) while the server's CPU and memory are sampled.
For each framework:

1. Check that it answers correctly.
2. Measure memory at rest.
3. Raise the load from 1 to 1,000 simultaneous clients.
4. Flood it for 20 s at 1,000 clients and compare memory before and after.

Starlette, FastAPI and Django try routes one by one, and the tested route
is declared after the 1,000 others: their worst case. They are run a
second time with it declared first, their best case. BlackSheep also tries
routes one by one, but sorts them itself (deepest first), so the tested
route always comes last.

Needs oha: `cargo install oha`.
Run everything (about 11 minutes): uv run python benches/load_test.py
Run some only:                     uv run python benches/load_test.py korvex "django, route first"
Results are merged into benches/load_results.json.
"""

import json
import os
import resource
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import psutil

FRAMEWORKS = ["korvex", "litestar", "starlette", "sanic", "fastapi", "django", "blacksheep", "falcon"]
LINEAR = ["starlette", "fastapi", "django"]
RUNS = FRAMEWORKS + [f"{name}, route first" for name in LINEAR]
RESULTS = Path("benches/load_results.json")
PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
URLS = f"{BASE}/users/[1-9][0-9]{{0,6}}"  # up to 10 million distinct URLs
SERVER_CPU = "2"
CLIENT_CPUS = "8-15"
CONCURRENCY = [1, 10, 50, 200, 1000]
STAGE_SECONDS = 6
FLOOD_SECONDS = 20


def raise_fd_limit():
    """1,000 connections need more than the usual 1,024 file descriptors."""
    _, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (hard, hard))


def start_server(run):
    framework = run.split(",")[0]
    env = {**os.environ, "ROUTE_FIRST": "1" if run.endswith("route first") else "0", "PORT": str(PORT)}
    command = ["taskset", "-c", SERVER_CPU, sys.executable, "-m", "uvicorn", f"benches.load.{framework}_app:app"]
    command += ["--port", str(PORT), "--log-level", "warning", "--no-access-log", "--loop", "uvloop", "--http", "httptools"]
    server = subprocess.Popen(command, preexec_fn=raise_fd_limit, env=env)
    for _ in range(300):
        try:
            with urllib.request.urlopen(f"{BASE}/users/42", timeout=1) as response:
                body = json.loads(response.read())
            if body != {"id": 42, "name": "user 42"}:
                sys.exit(f"{framework} answered {body}")
            return server
        except OSError:
            time.sleep(0.1)
    server.kill()
    sys.exit(f"{framework} did not start")


class Sampler(threading.Thread):
    """Samples the server's CPU (100% = one full core) and memory."""

    def __init__(self, pid):
        super().__init__(daemon=True)
        self.process = psutil.Process(pid)
        self.cpu, self.rss = [], []
        self.running = True

    def run(self):
        self.process.cpu_percent()
        while self.running:
            time.sleep(0.25)
            self.cpu.append(self.process.cpu_percent())
            self.rss.append(self.process.memory_info().rss)

    def stop(self):
        self.running = False
        self.join()
        busy = self.cpu[2:] or self.cpu  # skip the ramp-up
        return {"cpu_percent": sum(busy) / len(busy), "rss_mb": max(self.rss) / 2**20}


def rss_mb(pid):
    return psutil.Process(pid).memory_info().rss / 2**20


def load(pid, connections, seconds):
    """Runs oha and returns throughput, latency, failures and server usage."""
    command = ["taskset", "-c", CLIENT_CPUS, "oha", "--no-tui", "--output-format", "json", "--rand-regex-url"]
    command += ["-z", f"{seconds}s", "-c", str(connections), URLS]
    sampler = Sampler(pid)
    sampler.start()
    report = json.loads(subprocess.run(command, capture_output=True, text=True, check=True).stdout)
    usage = sampler.stop()
    statuses = report.get("statusCodeDistribution", {})
    # Requests still in flight when the time is up aren't failures.
    errors = {e: n for e, n in report.get("errorDistribution", {}).items() if e != "aborted due to deadline"}
    return {
        "connections": connections,
        "requests_per_second": report["summary"]["requestsPerSec"],
        "p50_ms": report["latencyPercentiles"]["p50"] * 1000,
        "p99_ms": report["latencyPercentiles"]["p99"] * 1000,
        "failed": sum(n for code, n in statuses.items() if code != "200") + sum(errors.values()),
        **usage,
    }


def test(run):
    server = start_server(run)
    try:
        time.sleep(2)
        result = {"idle_rss_mb": rss_mb(server.pid)}
        result["ramp"] = [load(server.pid, c, STAGE_SECONDS) for c in CONCURRENCY]
        result["flood"] = load(server.pid, 1000, FLOOD_SECONDS)
        result["after_flood_rss_mb"] = rss_mb(server.pid)
        return result
    finally:
        server.terminate()
        server.wait()


def table(title, results, cell):
    print(f"\n{title}")
    print(f"{'clients':<24}" + "".join(f"{c:>9}" for c in CONCURRENCY))
    for run, result in results.items():
        print(f"{run:<24}" + "".join(f"{cell(stage):>9}" for stage in result["ramp"]))


def main():
    runs = sys.argv[1:] or RUNS
    results = json.loads(RESULTS.read_text()) if RESULTS.exists() else {}
    for run in runs:
        print(f"Testing {run}...", flush=True)
        results[run] = test(run)
        RESULTS.write_text(json.dumps(results, indent=2))
    results = {run: results[run] for run in RUNS if run in results}

    table("Requests per second", results, lambda r: f"{r['requests_per_second']:,.0f}")
    table("Median latency (ms)", results, lambda r: f"{r['p50_ms']:.2f}")
    table("p99 latency (ms)", results, lambda r: f"{r['p99_ms']:.1f}")
    table("Server CPU (%)", results, lambda r: f"{r['cpu_percent']:.0f}")
    table("Failed requests", results, lambda r: str(r["failed"]))

    print(f"\n{'Memory (MB)':<24}{'at rest':>9}{'peak':>9}{'after':>9}   flood req/s")
    for run, r in results.items():
        peak = max(stage["rss_mb"] for stage in [*r["ramp"], r["flood"]])
        print(
            f"{run:<24}{r['idle_rss_mb']:>9.1f}{peak:>9.1f}{r['after_flood_rss_mb']:>9.1f}"
            f"   {r['flood']['requests_per_second']:,.0f}"
        )


if __name__ == "__main__":
    main()
