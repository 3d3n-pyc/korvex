"""Shared harness of the black-box fuzzers: start the built-in server, send raw bytes, judge the result.

    python fuzz/harness.py --selftest
    python fuzz/harness.py --replay target/fuzz/crashes/<file>.bin

Linux only (reads /proc).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import random
import re
import socket
import subprocess
import sys
import threading
import time
from collections import Counter, deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CRASHES = ROOT / "target" / "fuzz" / "crashes"
HOST = "127.0.0.1"
MAX_GROWTH_KIB = 64 * 1024
EVERY = 25
VALID = b"GET / HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n"


def rng(seed: int) -> random.Random:
    """A seeded random generator, so a run can be repeated."""
    return random.Random(seed)


def free_port() -> int:
    """A TCP port nobody listens on."""
    with socket.socket() as sock:
        sock.bind((HOST, 0))
        return sock.getsockname()[1]


def rss_kib(pid: int) -> int:
    """VmRSS of a process in KiB."""
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1])
    raise OSError("no VmRSS")


def children(pid: int) -> set[int]:
    """PIDs whose parent is `pid`, read from /proc."""
    found = set()
    for entry in os.listdir("/proc"):
        try:
            stat = Path(f"/proc/{entry}/stat").read_text()
        except OSError:
            continue
        if entry.isdigit() and int(stat.rsplit(")", 1)[1].split()[1]) == pid:
            found.add(int(entry))
    return found


def status_of(response: bytes) -> int:
    """The status code of a raw HTTP response, 0 when there is none."""
    match = re.match(rb"HTTP/1\.[01] (\d{3})", response)
    return int(match.group(1)) if match else 0


class Target:
    """The built-in server running fuzz/app.py, one worker."""

    def __init__(self) -> None:
        """Remember the port; nothing runs until start()."""
        self.port = free_port()
        self.process: subprocess.Popen | None = None
        self.worker = 0
        self.base_kib = 0

    def __enter__(self) -> Target:
        """Start the server."""
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        """Stop the server, also after Ctrl+C."""
        self.stop()

    def start(self) -> None:
        """Start `korvex run`, wait until it answers, record the worker PID and its RSS."""
        if not sys.platform.startswith("linux"):
            sys.exit("the fuzzers need Linux (/proc)")
        command = [sys.executable, "-m", "korvex", "run", "app:app", "--app-dir", str(HERE)]
        command += ["--port", str(self.port), "--workers", "1", "--read-timeout", "2"]
        self.process = subprocess.Popen(command, cwd=HERE)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if status_of(self.send(VALID, read_timeout=1.0)) == 200:
                break
            time.sleep(0.1)
        else:
            self.stop()
            sys.exit("the server didn't start")
        workers = children(self.process.pid)
        self.worker = min(workers) if workers else self.process.pid
        self.base_kib = rss_kib(self.worker)

    def stop(self) -> None:
        """SIGTERM, wait, then SIGKILL, by PID."""
        process, self.process = self.process, None
        if process is None:
            return
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    def send(self, raw: bytes, *, read_timeout: float = 3.0, half_close: bool = False) -> bytes:
        """Send raw bytes on a new connection; read until close or timeout.

        With `half_close`, the write side is shut down after the bytes, so the server sees the
        end of the input and answers or closes at once instead of waiting for more.
        """
        data = b""
        try:
            with socket.create_connection((HOST, self.port), timeout=max(read_timeout, 3.0)) as sock:
                sock.sendall(raw)
                if half_close:
                    sock.shutdown(socket.SHUT_WR)
                sock.settimeout(read_timeout)
                while chunk := sock.recv(65536):
                    data += chunk
        except OSError:
            pass
        return data

    def health(self) -> list[str]:
        """The server's state: worker gone or changed, RSS growth, a valid GET that hangs."""
        problems = []
        workers = children(self.process.pid) if self.process else set()
        if self.worker not in workers and Path(f"/proc/{self.worker}").exists() is False:
            problems.append(f"worker {self.worker} is gone")
        elif self.worker in workers and rss_kib(self.worker) > self.base_kib + MAX_GROWTH_KIB:
            problems.append("RSS grew by more than 64 MiB")
        started = time.monotonic()
        if status_of(self.send(VALID, read_timeout=2.0)) != 200 or time.monotonic() - started > 2:
            problems.append("a valid GET / no longer answers 200 within 2 s (hang)")
        return problems

    def check(self, case: bytes, response: bytes) -> list[str]:
        """The problems this case caused: 5xx plus everything `health` finds."""
        return quick_check(response) + self.health()

    def restart_if_dead(self, problems: list[str]) -> None:
        """Start a fresh server when the old one died or lost its worker."""
        if (self.process and self.process.poll() is not None) or "gone" in " ".join(problems):
            self.stop()
            self.start()


def quick_check(response: bytes) -> list[str]:
    """The cheap per-case oracle: a 5xx status."""
    status = status_of(response)
    return [f"server error {status}"] if 500 <= status < 600 and status != 501 else []


class Campaign:
    """Send generated cases from several threads; health-check every N cases; find the culprit."""

    def __init__(self, target: Target, kind: str, seed: int, make_case: Callable[[random.Random, int], bytes]) -> None:
        """Keep the target, the case generator and the counters."""
        self.target, self.kind, self.seed, self.make_case = target, kind, seed, make_case
        self.rng = rng(seed)
        self.lock = threading.Lock()
        self.history: deque[tuple[bytes, bool]] = deque(maxlen=EVERY)
        self.statuses: Counter[int] = Counter()
        self.count = 0
        self.found: set[Path] = set()
        self.read_timeout = 0.5

    def next_case(self) -> tuple[bytes, bool]:
        """The next case, and whether to half-close (about 9 cases in 10)."""
        with self.lock:
            case = self.make_case(self.rng, self.count)
            self.count += 1
            return case, self.rng.random() < 0.9

    def report(self, case: bytes, problems: list[str]) -> None:
        """Save the case and print it, once per distinct case."""
        path = save(self.kind, case, problems)
        if path not in self.found:
            self.found.add(path)
            print(f"problem: {path} {problems}", flush=True)

    def record(self, case: bytes, half: bool, response: bytes) -> None:
        """Count the case, run the cheap oracle, and the full check every EVERY cases."""
        with self.lock:
            self.statuses[status_of(response)] += 1
            self.history.append((case, half))
            if problems := quick_check(response):
                self.report(case, problems)
            if self.count % EVERY == 0:
                self.periodic()

    def periodic(self) -> None:
        """The full health check; when it fails, find which recent case did it."""
        if problems := self.target.health():
            self.bisect(problems)

    def bisect(self, first: list[str]) -> None:
        """Replay the recent cases one by one with full checks; save the culprit."""
        self.target.restart_if_dead(first)
        before = len(self.found)
        for case, half in list(self.history):
            response = self.target.send(case, read_timeout=self.read_timeout, half_close=half)
            if problems := self.target.check(case, response):
                self.report(case, problems)
                self.target.restart_if_dead(problems)
        if len(self.found) == before:
            print(f"problem: health check failed, no single case reproduces it: {first}", flush=True)
            self.found.add(Path(f"unattributed-{len(self.found)}"))

    def work(self, end: float) -> None:
        """One worker thread: send cases until the time is up."""
        while time.monotonic() < end:
            case, half = self.next_case()
            response = self.target.send(case, read_timeout=self.read_timeout, half_close=half)
            self.record(case, half, response)

    def run(self, duration: float, workers: int) -> int:
        """Run the threads, the final health check, print the summary; return the exit code."""
        started = time.monotonic()
        with ThreadPoolExecutor(workers) as pool:
            for future in [pool.submit(self.work, started + duration) for _ in range(workers)]:
                future.result()
        self.periodic()
        elapsed = time.monotonic() - started
        print(f"cases: {self.count}, {self.count / elapsed:.0f}/s, seed {self.seed}")
        print("statuses:", dict(sorted(self.statuses.items())))
        print(f"problems: {len(self.found)}")
        return 1 if self.found else 0


def fuzz_main(kind: str, doc: str, make_case: Callable[[random.Random, int], bytes]) -> None:
    """The shared command line of the fuzzers: --duration, --seed, --workers."""
    parser = argparse.ArgumentParser(description=doc.split("\n")[0])
    parser.add_argument("--duration", type=float, default=60)
    parser.add_argument("--seed", type=int, default=random.SystemRandom().randrange(2**32))
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    try:
        with Target() as target:
            sys.exit(Campaign(target, kind, args.seed, make_case).run(args.duration, max(1, args.workers)))
    except KeyboardInterrupt:
        sys.exit(130)


def save(kind: str, case: bytes, problems: list[str]) -> Path:
    """Write the case and its problems to target/fuzz/crashes/<kind>-<sha1>.bin and .txt."""
    CRASHES.mkdir(parents=True, exist_ok=True)
    path = CRASHES / f"{kind}-{hashlib.sha1(case).hexdigest()[:12]}.bin"
    path.write_bytes(case)
    path.with_suffix(".txt").write_text("\n".join(problems) + "\n")
    return path


def replay(path: str | Path) -> None:
    """Resend one saved case to a fresh server; print the response and the problems."""
    case = Path(path).read_bytes()
    with Target() as target:
        response = target.send(case)
        print(response[:2000])
        print("problems:", target.check(case, response) or "none")


def selftest() -> None:
    """A valid request gives no problem; a bad one gives a 4xx."""
    with Target() as target:
        good = target.send(VALID)
        assert status_of(good) == 200 and not target.check(VALID, good), good[:200]
        bad = b"GET / HTTP/1.1\r\nHost: x\r\nContent-Length: 1\r\nContent-Length: 2\r\n\r\n"
        response = target.send(bad)
        assert 400 <= status_of(response) < 500, response[:200]
        assert not target.check(bad, response)
    print("OK")


def main() -> None:
    """The command line: --selftest or --replay."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--replay", metavar="FILE")
    args = parser.parse_args()
    if args.replay:
        replay(args.replay)
    elif args.selftest:
        selftest()
    else:
        parser.print_help()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
