"""The SQLite database, on korbase.

Each worker process opens one connection (`size=1`), so calls never overlap;
WAL mode lets workers read while one writer works. The schema comes from the SQL
files in `taskboard/migrations`, applied at startup (safe with several workers).
"""

from importlib import resources
from pathlib import Path
from typing import Any, Optional

import korbase
from korbase import migrate


class Database:
    """Owns the korbase database; `get()` is it once connected."""

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

    def get(self) -> korbase.Database:
        """The open database; RuntimeError before connect()."""
        if self.db is None:
            raise RuntimeError("the database isn't connected: did the app start?")
        return self.db

    async def fetch_one(self, sql: str, *params: Any) -> Optional[tuple]:
        """The first row of `sql`, or None (the health check uses it)."""
        return await self.get().fetchrow(sql, *params)
