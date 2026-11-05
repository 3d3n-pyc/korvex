"""The SQLite database, without blocking the event loop.

Every call runs in one thread dedicated to the connection: the loop keeps
serving other requests meanwhile, and calls never overlap. Each worker
process opens its own connection on startup; WAL mode lets them read
while one writes.
"""

import asyncio
import sqlite3
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar

T = TypeVar("T")

# Applied in order, each once; PRAGMA user_version counts those done. To
# change the schema, append a migration: never edit one already released.
MIGRATIONS = [
    """
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
    """,
]


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._executor: Optional[ThreadPoolExecutor] = None
        self._connection: Optional[sqlite3.Connection] = None

    async def connect(self) -> None:
        """Opens the connection and brings the schema up to date."""
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sqlite")
        self._connection = await asyncio.get_running_loop().run_in_executor(self._executor, self._open)

    async def close(self) -> None:
        if self._connection is not None:
            await self.run(sqlite3.Connection.close)
            self._connection = None
        if self._executor is not None:
            self._executor.shutdown()
            self._executor = None

    async def run(self, work: Callable[[sqlite3.Connection], T]) -> T:
        """Runs `work(connection)` in the database thread."""
        if self._connection is None or self._executor is None:
            raise RuntimeError("the database isn't connected: did the app start?")
        return await asyncio.get_running_loop().run_in_executor(self._executor, work, self._connection)

    async def transaction(self, work: Callable[[sqlite3.Connection], T]) -> T:
        """Like run(), all or nothing: an exception rolls everything back."""

        def atomically(connection: sqlite3.Connection) -> T:
            connection.execute("BEGIN IMMEDIATE")  # takes the write lock now, not halfway through
            try:
                result = work(connection)
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")
            return result

        return await self.run(atomically)

    async def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> Optional[sqlite3.Row]:
        return await self.run(lambda connection: connection.execute(sql, params).fetchone())

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Runs one statement; returns how many rows it changed."""
        return await self.run(lambda connection: connection.execute(sql, params).rowcount)

    def _open(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Autocommit: each statement commits on its own, transaction() groups them.
        connection = sqlite3.connect(self.path, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")  # another worker is writing: wait, don't fail
        connection.execute("PRAGMA foreign_keys = ON")
        _migrate(connection)
        return connection


def _migrate(connection: sqlite3.Connection) -> None:
    # The write lock first: workers starting together migrate one at a
    # time, and the next one finds the work done.
    connection.execute("BEGIN IMMEDIATE")
    try:
        done = connection.execute("PRAGMA user_version").fetchone()[0]
        for number, migration in enumerate(MIGRATIONS[done:], start=done + 1):
            for statement in migration.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(f"PRAGMA user_version = {number}")
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    connection.execute("COMMIT")
