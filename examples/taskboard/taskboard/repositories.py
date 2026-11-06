"""All the SQL. The services ask for lists, tasks and sessions; only this
module knows the tables. Moving to Postgres means rewriting this file and
db.py, nothing else."""

import sqlite3
import uuid
from collections.abc import AsyncIterator, Sequence
from datetime import date, datetime
from typing import Optional

from taskboard.db import Database
from taskboard.domain import Task, TaskList


class ListRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def find(self) -> list[TaskList]:
        """Every list, in order, with how many open tasks each has."""
        return await self.db.run(
            lambda c: [
                _list(row)
                for row in c.execute(
                    """SELECT lists.*, count(tasks.id) AS open_tasks FROM lists
                       LEFT JOIN tasks ON tasks.list_id = lists.id AND NOT tasks.done
                       GROUP BY lists.id ORDER BY lists.position, lists.name"""
                )
            ]
        )

    async def get(self, id: uuid.UUID) -> Optional[TaskList]:
        row = await self.db.fetch_one(
            """SELECT lists.*, (SELECT count(*) FROM tasks WHERE list_id = lists.id AND NOT done) AS open_tasks
               FROM lists WHERE id = ?""",
            (str(id),),
        )
        return _list(row) if row else None

    async def save(self, task_list: TaskList) -> None:
        await self.db.execute(
            """INSERT INTO lists (id, name, color, position) VALUES (?, ?, ?, ?)
               ON CONFLICT (id) DO UPDATE SET name = excluded.name, color = excluded.color, position = excluded.position""",
            (str(task_list.id), task_list.name, task_list.color, task_list.position),
        )

    async def delete(self, id: uuid.UUID) -> bool:
        """Deletes the list and its tasks; False if there was no such list."""
        return await self.db.execute("DELETE FROM lists WHERE id = ?", (str(id),)) > 0

    async def count(self) -> int:
        row = await self.db.fetch_one("SELECT count(*) FROM lists")
        return row[0] if row else 0

    async def next_position(self) -> int:
        row = await self.db.fetch_one("SELECT coalesce(max(position), -1) + 1 FROM lists")
        return row[0] if row else 0

    async def create_first(self, first: TaskList, tasks: Sequence[Task]) -> bool:
        """Saves `first` and `tasks` if there's no list yet, all at once:
        workers starting together create them only once."""

        def work(c: sqlite3.Connection) -> bool:
            if c.execute("SELECT 1 FROM lists LIMIT 1").fetchone():
                return False
            c.execute(
                "INSERT INTO lists (id, name, color, position) VALUES (?, ?, ?, ?)",
                (str(first.id), first.name, first.color, first.position),
            )
            c.executemany(_INSERT_TASK, [_task_row(task) for task in tasks])
            return True

        return await self.db.transaction(work)


class TaskRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def find(
        self,
        *,
        list_id: Optional[uuid.UUID] = None,
        done: Optional[bool] = None,
        due_from: Optional[date] = None,
        due_to: Optional[date] = None,
        search: Optional[str] = None,
        limit: int = 500,
    ) -> list[Task]:
        """Tasks matching every filter given. Within a list: open ones in
        their order, then done ones, latest first. Across lists: by due date."""
        where, params = [], []
        if list_id is not None:
            where.append("list_id = ?")
            params.append(str(list_id))
        if done is not None:
            where.append("done = ?")
            params.append(int(done))
        if due_from is not None:
            where.append("due >= ?")
            params.append(due_from.isoformat())
        if due_to is not None:
            where.append("due <= ?")
            params.append(due_to.isoformat())
        if search:
            where.append(r"(title LIKE ? ESCAPE '\' OR notes LIKE ? ESCAPE '\')")
            pattern = "%" + search.replace("\\", r"\\").replace("%", r"\%").replace("_", r"\_") + "%"
            params += [pattern, pattern]
        order = (
            "done, CASE WHEN done THEN completed_at END DESC, position"
            if list_id is not None
            else "done, due IS NULL, due, position"
        )
        sql = f"SELECT * FROM tasks {'WHERE ' + ' AND '.join(where) if where else ''} ORDER BY {order} LIMIT ?"
        return await self.db.run(lambda c: [_task(row) for row in c.execute(sql, [*params, limit])])

    async def iterate(self, *, batch: int = 200) -> AsyncIterator[Task]:
        """Every task, fetched `batch` at a time."""
        offset = 0
        while True:
            sql = "SELECT * FROM tasks ORDER BY list_id, position LIMIT ? OFFSET ?"
            tasks = await self.db.run(lambda c: [_task(row) for row in c.execute(sql, (batch, offset))])
            for task in tasks:
                yield task
            if len(tasks) < batch:
                return
            offset += batch

    async def get(self, id: uuid.UUID) -> Optional[Task]:
        row = await self.db.fetch_one("SELECT * FROM tasks WHERE id = ?", (str(id),))
        return _task(row) if row else None

    async def save(self, task: Task) -> None:
        """Inserts the task, or updates it if it exists."""
        await self.db.execute(
            _INSERT_TASK
            + """ ON CONFLICT (id) DO UPDATE SET list_id = excluded.list_id, title = excluded.title,
                  notes = excluded.notes, done = excluded.done, due = excluded.due, position = excluded.position,
                  completed_at = excluded.completed_at""",
            _task_row(task),
        )

    async def delete(self, id: uuid.UUID) -> bool:
        return await self.db.execute("DELETE FROM tasks WHERE id = ?", (str(id),)) > 0

    async def delete_done(self, list_id: uuid.UUID) -> list[uuid.UUID]:
        """Deletes the list's finished tasks; returns their IDs."""

        def work(c: sqlite3.Connection) -> list[uuid.UUID]:
            ids = [
                uuid.UUID(row[0])
                for row in c.execute("SELECT id FROM tasks WHERE list_id = ? AND done", (str(list_id),))
            ]
            c.execute("DELETE FROM tasks WHERE list_id = ? AND done", (str(list_id),))
            return ids

        return await self.db.transaction(work)

    async def first_position(self, list_id: uuid.UUID) -> int:
        """A position before every task of the list (new tasks go on top)."""
        row = await self.db.fetch_one(
            "SELECT coalesce(min(position), 1) - 1 FROM tasks WHERE list_id = ?", (str(list_id),)
        )
        return row[0] if row else 0

    async def reorder(self, list_id: uuid.UUID, ids: Sequence[uuid.UUID]) -> None:
        """Puts the list's tasks in the order of `ids`. Tasks of the list not
        in `ids` keep their order, after them; IDs from elsewhere are ignored."""

        def work(c: sqlite3.Connection) -> None:
            current = [
                row[0] for row in c.execute("SELECT id FROM tasks WHERE list_id = ? ORDER BY position", (str(list_id),))
            ]
            known = set(current)
            wanted = [str(id) for id in ids if str(id) in known]
            order = list(dict.fromkeys(wanted + current))
            c.executemany("UPDATE tasks SET position = ? WHERE id = ?", list(enumerate(order)))

        await self.db.transaction(work)


class SessionRepository:
    """Sign-in sessions, by a hash of their token."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def add(self, token_hash: str, user: str, expires_at: datetime) -> None:
        await self.db.execute(
            "INSERT INTO sessions (token_hash, user, expires_at) VALUES (?, ?, ?)",
            (token_hash, user, expires_at.isoformat()),
        )

    async def user(self, token_hash: str, now: datetime) -> Optional[str]:
        row = await self.db.fetch_one(
            "SELECT user FROM sessions WHERE token_hash = ? AND expires_at > ?", (token_hash, now.isoformat())
        )
        return row["user"] if row else None

    async def delete(self, token_hash: str) -> None:
        await self.db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    async def delete_expired(self, now: datetime) -> None:
        await self.db.execute("DELETE FROM sessions WHERE expires_at <= ?", (now.isoformat(),))


_INSERT_TASK = """INSERT INTO tasks (id, list_id, title, notes, done, due, position, created_at, completed_at)
                  VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"""


def _task_row(task: Task) -> tuple:
    return (
        str(task.id),
        str(task.list_id),
        task.title,
        task.notes,
        int(task.done),
        task.due.isoformat() if task.due else None,
        task.position,
        task.created_at.isoformat(),
        task.completed_at.isoformat() if task.completed_at else None,
    )


def _task(row: sqlite3.Row) -> Task:
    return Task(
        id=uuid.UUID(row["id"]),
        list_id=uuid.UUID(row["list_id"]),
        title=row["title"],
        notes=row["notes"],
        done=bool(row["done"]),
        due=date.fromisoformat(row["due"]) if row["due"] else None,
        position=row["position"],
        created_at=datetime.fromisoformat(row["created_at"]),
        completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
    )


def _list(row: sqlite3.Row) -> TaskList:
    return TaskList(
        id=uuid.UUID(row["id"]),
        name=row["name"],
        color=row["color"],
        position=row["position"],
        open_tasks=row["open_tasks"],
    )
