"""Benchmark: a request that reads one row from PostgreSQL.

Each request does what a real API does at the least: a query through a
connection pool (asyncpg), then JSON. The database takes its own share of
each request, so this shows what's left of a framework's speed when the
database is part of it.

- PostgreSQL runs in a container (podman or docker) on 2 cores of its
  own, with 10,000 users; the server gets 4 cores, oha 8 others.
- Korvex with its built-in server, Korvex on uvicorn, and FastAPI on
  uvicorn, each with 3 workers and the same query.

Needs oha and podman (or docker). Run: uv run python benches/database.py
Results go to benches/database_results.json.
"""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).parent))
import servers  # noqa: E402  (same cores and load, another app)

RESULTS = Path("benches/database_results.json")
CONTAINER = "korvex-bench-postgres"
DB_PORT = 54329
DATABASE_URL = f"postgresql://bench:bench@127.0.0.1:{DB_PORT}/bench"
DB_CPUS = "6-7"
USERS = 10_000
CONNECTIONS = 200
WORKERS = 3
PY = sys.executable
PORT = servers.PORT

SERVERS = {
    "korvex, built-in server": [PY, "-m", "korvex", "run", "korvex_db_app:app", "--app-dir", "benches/db",
                                "--port", str(PORT), "--workers", str(WORKERS)],
    "korvex on uvicorn": [PY, "-m", "uvicorn", "korvex_db_app:app", "--app-dir", "benches/db", "--port", str(PORT),
                          "--log-level", "warning", "--no-access-log", "--loop", "uvloop", "--http", "httptools",
                          "--workers", str(WORKERS)],
    "fastapi on uvicorn": [PY, "-m", "uvicorn", "fastapi_db_app:app", "--app-dir", "benches/db", "--port", str(PORT),
                           "--log-level", "warning", "--no-access-log", "--loop", "uvloop", "--http", "httptools",
                           "--workers", str(WORKERS)],
}


def start_postgres():
    runtime = shutil.which("podman") or shutil.which("docker")
    if runtime is None:
        sys.exit("needs podman or docker to run PostgreSQL")
    subprocess.run([runtime, "rm", "-f", CONTAINER], capture_output=True)
    subprocess.run(
        [runtime, "run", "-d", "--rm", "--name", CONTAINER, "-p", f"127.0.0.1:{DB_PORT}:5432",
         "-e", "POSTGRES_USER=bench", "-e", "POSTGRES_PASSWORD=bench", "-e", "POSTGRES_DB=bench",
         "docker.io/library/postgres:17-alpine", "-c", "max_connections=200"],
        check=True, capture_output=True,
    )
    # Pinned with taskset rather than --cpuset-cpus, which rootless podman
    # often can't apply. The backends PostgreSQL forks for each connection
    # inherit it.
    pid = subprocess.run([runtime, "inspect", "--format", "{{.State.Pid}}", CONTAINER],
                         check=True, capture_output=True, text=True).stdout.strip()
    subprocess.run(["taskset", "-a", "-p", "-c", DB_CPUS, pid], check=True, capture_output=True)
    asyncio.run(seed())
    return runtime


async def seed():
    for _ in range(100):
        try:
            connection = await asyncpg.connect(DATABASE_URL)
            break
        except (OSError, asyncpg.PostgresError):
            await asyncio.sleep(0.3)
    else:
        sys.exit("PostgreSQL did not start")
    await connection.execute("DROP TABLE IF EXISTS users; CREATE TABLE users (id int PRIMARY KEY, name text NOT NULL)")
    await connection.copy_records_to_table("users", records=[(n, f"user {n}") for n in range(1, USERS + 1)])
    await connection.close()


def main():
    runtime = start_postgres()
    os.environ["DATABASE_URL"] = DATABASE_URL
    servers.URLS = f"{servers.BASE}/users/[1-9][0-9]{{0,3}}"  # ids 1 to 9999: always a row
    results = {}
    try:
        for label, command in SERVERS.items():
            print(f"Testing {label}...", flush=True)
            process = servers.start(command)  # it checks /users/42: the database has that row
            try:
                results[label] = servers.load(process.pid, CONNECTIONS)
            finally:
                servers.stop(process)
            RESULTS.write_text(json.dumps(results, indent=2))
            time.sleep(1)
    finally:
        subprocess.run([runtime, "stop", CONTAINER], capture_output=True)

    print(f"\n{'':26}{'req/s':>10}{'p99 ms':>9}{'RAM MiB':>9}")
    for label, run in results.items():
        print(f"{label:26}{run['requests_per_second']:>10,.0f}{run['p99_ms']:>9.1f}{run['peak_rss_mb']:>9.0f}")


if __name__ == "__main__":
    main()
