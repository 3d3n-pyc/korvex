"""Soak test: does the built-in server leak memory or fail under steady mixed load?

Serves benches/soak_app.py with 2 workers, drives it with rate-limited oha
runs and the soak_clients clients, samples each worker's RSS every 5 s, and
fails (exit 1) on RSS growth, a worker change, or request errors. Linux only.

    python benches/soak.py --duration 600 --out target/soak

Needs oha on the PATH.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import csv
import json
import os
import socket
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import soak_clients  # noqa: E402

HOST = "127.0.0.1"
WORKERS = 2
INTERVAL = 5
MIB = 1024
JSON_ARGS = ["-m", "POST", "-T", "application/json", "-d", '{"name":"x","n":1}']
RUNS = {
    "keep-alive": ("/plain", ["-c", "8", "-q", "1000"]),
    "no-keep-alive": ("/plain", ["--disable-keepalive", "-c", "2", "-q", "100"]),
    "json": ("/json", JSON_ARGS + ["-c", "4", "-q", "300"]),
    "stream": ("/stream", ["-c", "2", "-q", "50"]),
    "file": ("/file", ["-c", "2", "-q", "50"]),
}


def free_port() -> int:
    """A TCP port nobody listens on."""
    with socket.socket() as sock:
        sock.bind((HOST, 0))
        return sock.getsockname()[1]


def start_server(port: int) -> subprocess.Popen:
    """Start the server from benches/ (not the source tree) and wait until it answers."""
    command = [sys.executable, "-m", "korvex", "run", "soak_app:app", "--app-dir", str(HERE)]
    process = subprocess.Popen(command + ["--port", str(port), "--workers", str(WORKERS)], cwd=HERE)
    for _ in range(300):
        try:
            with urllib.request.urlopen(f"http://{HOST}:{port}/plain", timeout=1):
                return process
        except OSError:
            time.sleep(0.1)
    stop_server(process)
    sys.exit("the server didn't start")


def stop_server(process: subprocess.Popen) -> None:
    """SIGTERM, wait, then SIGKILL."""
    process.terminate()
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def children(pid: int) -> set[int]:
    """PIDs whose parent is `pid`, read from /proc."""
    found = set()
    for entry in os.listdir("/proc"):
        if entry.isdigit():
            try:
                stat = Path(f"/proc/{entry}/stat").read_text()
            except OSError:
                continue
            if int(stat.rsplit(")", 1)[1].split()[1]) == pid:
                found.add(int(entry))
    return found


def rss_kib(pid: int) -> int:
    """VmRSS of a process in KiB."""
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1])
    raise OSError("no VmRSS")


async def sample(server_pid: int, seconds: float, problems: list[str]) -> list[tuple[float, int, int]]:
    """Every INTERVAL s, the RSS of each worker; a changed worker set is a problem."""
    start, samples, first = time.monotonic(), [], None
    while True:
        pids = children(server_pid)
        first = first or pids
        if pids != first and "the worker processes changed (crash or restart)" not in problems:
            problems.append("the worker processes changed (crash or restart)")
        t = time.monotonic() - start
        for pid in sorted(pids):
            with contextlib.suppress(OSError):
                samples.append((round(t, 1), pid, rss_kib(pid)))
        if t + INTERVAL > seconds:
            return samples
        await asyncio.sleep(INTERVAL)


async def run_oha(name: str, port: int, seconds: int, problems: list[str]) -> int:
    """One rate-limited oha run; returns its request count and records failures."""
    path, extra = RUNS[name]
    command = ["oha", "-z", f"{seconds}s", "--no-tui", "--output-format", "json", *extra, f"http://{HOST}:{port}{path}"]
    proc = await asyncio.create_subprocess_exec(*command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = await proc.communicate()
    try:
        report = json.loads(out)
    except ValueError:
        problems.append(f"oha {name}: no report ({err.decode()[:200]})")
        return 0
    codes = report.get("statusCodeDistribution", {})
    bad = sum(n for code, n in codes.items() if not code.startswith("2"))
    errors = {k: v for k, v in report.get("errorDistribution", {}).items() if "deadline" not in k.lower()}
    if bad:
        problems.append(f"oha {name}: {bad} non-2xx responses")
    if errors:
        problems.append(f"oha {name}: errors {errors}")
    return sum(codes.values())


def verdicts(samples: list[tuple[float, int, int]], duration: float) -> list[dict]:
    """Per worker: baseline and end median RSS, growth, and pass or fail."""
    warm, window = max(15, 0.2 * duration), min(60, duration / 6)
    result = []
    for pid in sorted({s[1] for s in samples}):
        mine = [(t, kib) for t, p, kib in samples if p == pid]
        base = statistics.median([k for t, k in mine if warm <= t < warm + window] or [0])
        end = statistics.median([k for t, k in mine if t > mine[-1][0] - window] or [0])
        growth = (end / base - 1) if base else 0.0
        failed = growth > 0.10 and end - base > 4 * MIB
        result.append(
            {"pid": pid, "baseline_mib": base / MIB, "end_mib": end / MIB, "growth": growth, "ok": not failed}
        )
    return result


def render(rows: list[dict], runs: dict[str, int], counts: soak_clients.Counts, problems: list[str]) -> str:
    """The report as Markdown tables (readable as plain text too)."""
    lines = ["| worker | baseline MiB | end MiB | growth | verdict |", "|---|---|---|---|---|"]
    for r in rows:
        verdict = "ok" if r["ok"] else "FAIL"
        lines.append(f"| {r['pid']} | {r['baseline_mib']:.1f} | {r['end_mib']:.1f} | {r['growth']:+.1%} | {verdict} |")
    lines += ["", "| requests | count |", "|---|---|"] + [f"| oha {k} | {v:,} |" for k, v in runs.items()]
    lines += [f"| ws messages | {counts.ws_messages:,} |", f"| slow clients | {counts.slow_clients:,} |"]
    lines += [f"| aborted streams | {counts.aborted_streams:,} |", ""]
    return "\n".join(lines + [f"FAILED: {p}" for p in problems])


async def drive(port: int, duration: int, server_pid: int, problems: list[str]):
    """Run all the load and the sampler together."""
    sampler = asyncio.ensure_future(sample(server_pid, duration, problems))
    clients = asyncio.ensure_future(soak_clients.run(HOST, port, duration))
    counts = await asyncio.gather(*(run_oha(name, port, duration, problems) for name in RUNS))
    return await sampler, await clients, dict(zip(RUNS, counts))


def main() -> None:
    """Parse the arguments, run the soak, write the reports, set the exit code."""
    if not sys.platform.startswith("linux"):
        sys.exit("the soak test needs Linux (/proc)")
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--duration", type=int, default=600, help="seconds of load")
    parser.add_argument("--port", type=int, default=0, help="default: a free port")
    parser.add_argument("--out", default="target/soak", help="folder for samples.csv and verdict.json")
    args = parser.parse_args()
    problems: list[str] = []
    port = args.port or free_port()
    server = start_server(port)
    try:
        samples, counts, runs = asyncio.run(drive(port, args.duration, server.pid, problems))
    finally:
        stop_server(server)
    rows = verdicts(samples, args.duration)
    problems += [f"worker {r['pid']}: RSS grew {r['growth']:+.1%}" for r in rows if not r["ok"]]
    if counts.ws_errors or counts.errors:
        problems.append(f"clients: {counts.ws_errors} ws errors, {counts.errors[:3]}")
    if len(rows) != WORKERS:
        problems.append(f"expected {WORKERS} workers, saw {len(rows)}")
    report = render(rows, runs, counts, problems)
    print(report)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
            summary.write(report + "\n")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "samples.csv", "w", newline="") as file:
        csv.writer(file).writerows([("t", "pid", "rss_kib"), *samples])
    (out / "verdict.json").write_text(json.dumps({"ok": not problems, "workers": rows, "problems": problems}, indent=2))
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
