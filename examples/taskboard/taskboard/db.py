"""The SQLite database, on korbase.

Each worker process opens one connection (`size=1`), so calls never overlap;
WAL mode lets workers read while one writes. The schema comes from the SQL
files in `taskboard/migrations`, applied at startup (safe with several workers).

TEMPORARY: `run`, `transaction`, `fetch_one` and `execute` below let
repositories.py keep its old queries. They are removed when repositories.py
uses korbase queries.
"""

import asyncio
from collections.abc import Callable, Sequence
from importlib import resources
from pathlib import Path
from typing import Any, Optional, TypeVar

import korbase
from korbase import migrate

T = TypeVar("T")


class Row:
    """TEMPORARY: a row readable by position or by column name, like sqlite3.Row."""

    def __init__(self, names: Sequence[str], values: Sequence[Any]) -> None:
        self._names = list(names)
        self._values = tuple(values)

    def __getitem__(self, key: Any) -> Any:
        """The value at a position or under a column name."""
        return self._values[self._names.index(key) if isinstance(key, str) else key]


class _Cursor:
    """TEMPORARY: the result of `_Connection.execute`."""

    def __init__(self, rows: list[Row]) -> None:
        self._rows = rows

    def fetchone(self) -> Optional[Row]:
        """The first row, or None."""
        return self._rows[0] if self._rows else None

    def __iter__(self) -> Any:
        """Iterates over the rows."""
        return iter(self._rows)


class _Connection:
    """TEMPORARY: what `Database.run` hands to its callback, which runs in a
    thread: each call is sent to the event loop and waited for."""

    def __init__(self, runner: Any, loop: asyncio.AbstractEventLoop) -> None:
        self._runner = runner
        self._loop = loop

    def execute(self, sql: str, params: Sequence[Any] = ()) -> _Cursor:
        """Runs one statement and returns its rows."""
        future = asyncio.run_coroutine_threadsafe(_query(self._runner, sql, params), self._loop)
        return _Cursor(future.result())

    def executemany(self, sql: str, rows: Sequence[Sequence[Any]]) -> None:
        """Runs one statement once per row of parameters."""
        for params in rows:
            self.execute(sql, params)


async def _query(runner: Any, sql: str, params: Sequence[Any]) -> list[Row]:
    """TEMPORARY: runs `sql` on a korbase Database or Transaction; rows keep their column names."""
    async with runner._conn() as conn:  # korbase has no public call that returns column names
        names, rows = await conn.fetch_columns(sql, params)
    return [Row(names, row) for row in rows]


class Database:
    """Owns the korbase database; `.db` is it once connected."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.db: Optional[korbase.Database] = None

    async def connect(self) -> None:
        """Opens the database and brings the schema up to date."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = await korbase.Database.connect(f"sqlite:///{self.path}", size=1)
        with resources.as_file(resources.files("taskboard") / "migrations") as directory:
            await migrate.apply(self.db, directory)

    async def close(self) -> None:
        """Closes the database."""
        if self.db is not None:
            await self.db.close()
            self.db = None

    def _connected(self) -> korbase.Database:
        """The open database; RuntimeError before connect()."""
        if self.db is None:
            raise RuntimeError("the database isn't connected: did the app start?")
        return self.db

    async def run(self, work: Callable[[Any], T]) -> T:
        """TEMPORARY: runs `work(connection)` in a thread."""
        connection = _Connection(self._connected(), asyncio.get_running_loop())
        return await asyncio.to_thread(work, connection)

    async def transaction(self, work: Callable[[Any], T]) -> T:
        """TEMPORARY: like run(), all or nothing."""
        async with self._connected().transaction() as tx:
            connection = _Connection(tx, asyncio.get_running_loop())
            return await asyncio.to_thread(work, connection)

    async def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> Optional[Row]:
        """TEMPORARY: the first row of `sql`, or None."""
        rows = await _query(self._connected(), sql, params)
        return rows[0] if rows else None

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        """TEMPORARY: runs one statement; returns how many rows it changed."""
        tag = await self._connected().execute(sql, *params)
        return int(tag.rsplit(" ", 1)[-1])
