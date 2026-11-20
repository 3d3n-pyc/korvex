"""The schema: models, generated columns and migrations agree."""

import asyncio
import sqlite3
import subprocess
import sys
from pathlib import Path

from korbase import Database
from korbase.migrate import apply, collect_models, make

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = ROOT / "taskboard" / "migrations"

# The schema as it was before korbase: PRAGMA user_version counted it.
OLD_SCHEMA = """
CREATE TABLE lists (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    color TEXT NOT NULL,
    position INTEGER NOT NULL
);
CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    list_id TEXT NOT NULL REFERENCES lists (id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    done INTEGER NOT NULL DEFAULT 0,
    due TEXT,
    position INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX tasks_by_list ON tasks (list_id, done, position);
CREATE INDEX tasks_by_due ON tasks (due) WHERE due IS NOT NULL;
CREATE TABLE sessions (
    token_hash TEXT PRIMARY KEY,
    user TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
"""


def dsn(path: Path) -> str:
    """The korbase DSN of a SQLite file."""
    return f"sqlite:///{path}"


async def drift(path: Path) -> object:
    """Applies the migrations to `path`, then asks `make` what differs."""
    db = await Database.connect(dsn(path))
    try:
        await apply(db, MIGRATIONS)
        return await make(db, collect_models("taskboard.models"), MIGRATIONS, "check")
    finally:
        await db.close()


def test_columns_are_up_to_date() -> None:
    """taskboard/columns.py matches the models."""
    command = [sys.executable, "-m", "korbase.stubs", "taskboard.models", "-o", "taskboard/columns.py", "--check"]
    assert subprocess.run(command, cwd=ROOT, env={"PYTHONPATH": str(ROOT)}).returncode == 0


def test_fresh_database_has_no_drift(tmp_path: Path) -> None:
    """The migrations build exactly the schema of the models."""
    assert asyncio.run(drift(tmp_path / "fresh.db")) is None


def test_legacy_database_is_adopted(tmp_path: Path) -> None:
    """A database made by the old migrations keeps its rows."""
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(OLD_SCHEMA)
        connection.execute("PRAGMA user_version = 1")
        connection.execute("INSERT INTO lists VALUES ('l1', 'Home', 'blue', 0)")
        connection.execute(
            "INSERT INTO tasks (id, list_id, title, position, created_at) VALUES ('t1', 'l1', 'Dishes', 0, 'now')"
        )
        connection.execute("INSERT INTO sessions VALUES ('h', 'admin', 'later')")
    connection.close()
    assert asyncio.run(drift(path)) is None
    with sqlite3.connect(path) as connection:
        counts = [connection.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("lists", "tasks", "sessions")]
    connection.close()
    assert counts == [1, 1, 1]


def test_concurrent_applies_record_once(tmp_path: Path) -> None:
    """Two workers starting together apply the migration once."""
    path = tmp_path / "race.db"

    async def race() -> None:
        first, second = await Database.connect(dsn(path)), await Database.connect(dsn(path))
        try:
            await asyncio.gather(apply(first, MIGRATIONS), apply(second, MIGRATIONS))
        finally:
            await first.close()
            await second.close()

    asyncio.run(race())
    with sqlite3.connect(path) as connection:
        rows = connection.execute("SELECT * FROM korbase_migrations").fetchall()
    connection.close()
    assert len(rows) == 1
