"""Benchmark: Korvex's built-in server with more threads or more workers.

The same app and load as servers.py (1,000 routes, one JSON endpoint),
the server on the same 4 cores, oha on 8 others. It compares two ways to
use the cores:

- workers: processes, each with its own memory, with the GIL or without;
- threads (`threads=`): event loops in one process, which share its
  memory, and run in parallel only on free-threaded Python (3.14t).

Needs oha (`cargo install oha`), and for the free-threaded runs a Python
3.14t with Korvex installed, given as KORVEX_FT_PYTHON:

    uv python install 3.14t
    uv venv --python 3.14t /tmp/ft && uv pip install --python /tmp/ft/bin/python .
    KORVEX_FT_PYTHON=/tmp/ft/bin/python uv run python benches/threads.py

Results go to benches/threads_results.json.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from servers import PORT, load, start, stop  # noqa: E402  (same app, cores and load)

RESULTS = Path("benches/threads_results.json")
CONNECTIONS = 200


def command(python, workers, threads):
    return [
        python,
        "-m",
        "korvex",
        "run",
        "korvex_app:app",
        "--app-dir",
        "benches/load",
        "--port",
        str(PORT),
        "--workers",
        str(workers),
        "--threads",
        str(threads),
    ]


def main():
    runs = []
    free_threaded = os.environ.get("KORVEX_FT_PYTHON")
    if free_threaded:
        runs += [("3.14t", free_threaded, 1, threads) for threads in (1, 2, 3, 4)]
        runs += [("3.14t", free_threaded, workers, 1) for workers in (2, 4)]
    else:
        print("KORVEX_FT_PYTHON isn't set: only the runs with the GIL", file=sys.stderr)
    runs += [("GIL", sys.executable, 1, 1), ("GIL", sys.executable, 1, 2), ("GIL", sys.executable, 4, 1)]

    results = {}
    for python_name, python, workers, threads in runs:
        label = f"{python_name}, {workers} worker{'s' * (workers > 1)}, {threads} thread{'s' * (threads > 1)}"
        print(f"Testing {label}...", flush=True)
        process = start(command(python, workers, threads))
        try:
            results[label] = load(process.pid, CONNECTIONS)
        finally:
            stop(process)
        RESULTS.write_text(json.dumps(results, indent=2))

    print(f"\n{'':32}{'req/s':>10}{'p99 ms':>9}{'RAM MiB':>9}")
    for label, run in results.items():
        print(f"{label:32}{run['requests_per_second']:>10,.0f}{run['p99_ms']:>9.1f}{run['peak_rss_mb']:>9.0f}")


if __name__ == "__main__":
    main()
