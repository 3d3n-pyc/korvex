"""Performance check of the built-in server: is it slower than the base?

perf_check.py times the App in-process, without a server. The built-in
server's own path (the Rust thread, the hand-off to asyncio, the code
between the server and the App) isn't in it, and a slowdown there went
unnoticed from 0.7 to 0.14. This check serves the load app
(benches/load/korvex_app.py) with each interpreter's Korvex, one worker
pinned to one CPU core, and floods it with oha from other cores: several
rounds, alternating which side goes first, so both see the same machine
noise. It prints the median requests per second of each, and fails
(exit 1) when the head is more than `--threshold` slower than the base.

CI runs it on every pull request (.github/workflows/perf.yml). Locally:

    python benches/server_check.py --base ../main/.venv/bin/python --head .venv/bin/python

Needs oha (`cargo install oha`) and taskset (Linux).
"""

import argparse
import json
import os
import socket
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent / "load"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def serve(python: str, port: int, cpu: str) -> subprocess.Popen:
    """The built-in server with the load app, pinned to `cpu`, once it answers."""
    command = ["taskset", "-c", cpu, python, "-m", "korvex", "run", "korvex_app:app"]
    command += ["--app-dir", str(APP_DIR), "--port", str(port), "--workers", "1"]
    # From the app's folder: `-m` puts the current directory first on the
    # import path, and from the repository root that would be the source
    # tree's korvex, whatever the interpreter has installed.
    process = subprocess.Popen(command, cwd=APP_DIR, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(300):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/users/1", timeout=1):
                return process
        except OSError:
            time.sleep(0.1)
    process.kill()
    sys.exit(f"{python}: the built-in server didn't start")


def flood(port: int, cpus: str, seconds: float, connections: int) -> float:
    """Requests per second oha gets from the server, to random user URLs."""
    url = f"http://127.0.0.1:{port}/users/[1-9][0-9]{{0,4}}"
    command = ["taskset", "-c", cpus, "oha", "--no-tui", "--output-format", "json", "--rand-regex-url"]
    command += ["-z", f"{seconds}s", "-c", str(connections), url]
    report = json.loads(subprocess.run(command, capture_output=True, text=True, check=True).stdout)
    failed = sum(n for code, n in report.get("statusCodeDistribution", {}).items() if code != "200")
    if failed:
        sys.exit(f"{failed} requests failed")
    return report["summary"]["requestsPerSec"]


def measure(python: str, args: argparse.Namespace) -> float:
    port = free_port()
    process = serve(python, port, args.server_cpu)
    try:
        flood(port, args.client_cpus, 1, args.connections)  # warm-up
        return flood(port, args.client_cpus, args.seconds, args.connections)
    finally:
        process.terminate()
        process.wait(timeout=30)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base", required=True, help="the interpreter with the base's Korvex")
    parser.add_argument("--head", required=True, help="the interpreter with the change's Korvex")
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--seconds", type=float, default=5)
    parser.add_argument("--connections", type=int, default=100)
    parser.add_argument("--server-cpu", default="0", help="the core the server runs on")
    parser.add_argument("--client-cpus", default="1-3", help="the cores oha runs on")
    parser.add_argument("--threshold", type=float, default=0.10)
    args = parser.parse_args()
    # Absolute, as the servers start from the app's folder; not resolved, as a
    # venv's python is a link that has to stay in the venv.
    args.base, args.head = os.path.abspath(args.base), os.path.abspath(args.head)

    results: dict[str, list[float]] = {"base": [], "head": []}
    for round_ in range(args.rounds):
        order = ["base", "head"] if round_ % 2 == 0 else ["head", "base"]
        for side in order:
            results[side].append(measure(getattr(args, side), args))
        print(f"round {round_ + 1}: base {results['base'][-1]:,.0f}, head {results['head'][-1]:,.0f} requests/s")

    base, head = statistics.median(results["base"]), statistics.median(results["head"])
    change = head / base - 1
    print(f"\nbuilt-in server, median: base {base:,.0f}, head {head:,.0f} requests/s ({change:+.1%})")
    if change < -args.threshold:
        sys.exit(f"The built-in server is {-change:.0%} slower than the base (threshold {args.threshold:.0%}).")
    print(f"No more than {args.threshold:.0%} slower than the base.")


if __name__ == "__main__":
    main()
