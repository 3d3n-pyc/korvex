"""All the queries. The services ask for lists, tasks and sessions; only this
module knows the tables. Moving to Postgres means rewriting this file and
db.py, nothing else."""

import uuid
from collections.abc import AsyncIterator, Sequence
from datetime import date, datetime
from typing import Optional

import msgspec
from korbase import delete, insert, param, select, upsert

from taskboard.columns import cols
from taskboard.db import Database
from taskboard.domain import TaskList
from taskboard.models import ListRow, SessionRow, Task

_L, _T, _S = cols(ListRow), cols(Task), cols(SessionRow)

_LISTS = select(ListRow).order_by(_L.position.asc(), _L.name.asc())
_LIST = select(ListRow).where(id=param("id"))
_SAVE_LIST = (
    upsert(ListRow).values(id=param("id"), name=param("name"), color=param("color"), position=param("position"))
).on_conflict("id")
_DELETE_LIST = delete(ListRow).where(id=param("id"))
_OPEN_COUNTS = "SELECT list_id, count(*) FROM tasks WHERE NOT done GROUP BY list_id"
_OPEN_COUNT = "SELECT count(*) FROM tasks WHERE list_id = ? AND NOT done"

_PAGE = select(Task).order_by(_T.list_id.asc(), _T.position.asc()).limit(param("limit")).offset(param("offset"))
_TASK = select(Task).where(id=param("id"))
_SAVE_TASK = upsert(Task).values(**{name: param(name) for name in Task.__struct_fields__}).on_conflict("id")
_DELETE_TASK = delete(Task).where(id=param("id"))
_DELETE_DONE = delete(Task).where(list_id=param("list_id"), done=True).returning()
_REORDER = "UPDATE tasks SET position = j.key FROM json_each(?1) j WHERE tasks.id = j.value AND tasks.list_id = ?2"

_ADD_SESSION = insert(SessionRow).values(token_hash=param("token_hash"), user=param("user"), expires_at=param("at"))
_SESSION = select(SessionRow).where(_S.token_hash == param("token_hash"), _S.expires_at > param("now"))
_DELETE_SESSION = delete(SessionRow).where(token_hash=param("token_hash"))
_DELETE_EXPIRED = delete(SessionRow).where(_S.expires_at <= param("now"))


class ListRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def find(self) -> list[TaskList]:
        """Every list, in order, with how many open tasks each has."""
        db = self.db.get()
        rows = await db.run(_LISTS)
        counts = {uuid.UUID(list_id): n for list_id, n in await db.fetch(_OPEN_COUNTS)}
        return [_list(row, counts.get(row.id, 0)) for row in rows]

    async def get(self, id: uuid.UUID) -> Optional[TaskList]:
        db = self.db.get()
        row = await db.first(_LIST, id=id)
        if row is None:
            return None
        count = await db.fetchrow(_OPEN_COUNT, id)
        return _list(row, count[0] if count else 0)

    async def save(self, task_list: TaskList) -> None:
        await self.db.get().run(
            _SAVE_LIST, id=task_list.id, name=task_list.name, color=task_list.color, position=task_list.position
        )

    async def delete(self, id: uuid.UUID) -> bool:
        """Deletes the list and its tasks; False if there was no such list."""
        return await self.db.get().run(_DELETE_LIST, id=id) > 0

    async def count(self) -> int:
        row = await self.db.get().fetchrow("SELECT count(*) FROM lists")
        return row[0] if row else 0

    async def next_position(self) -> int:
        row = await self.db.get().fetchrow("SELECT coalesce(max(position), -1) + 1 FROM lists")
        return row[0] if row else 0

    async def create_first(self, first: TaskList, tasks: Sequence[Task]) -> bool:
        """Saves `first` and `tasks` if there's no list yet, all at once:
        workers starting together create them only once."""
        async with self.db.get().transaction() as tx:
            if await tx.fetchrow("SELECT 1 FROM lists LIMIT 1"):
                return False
            await tx.run(_SAVE_LIST, id=first.id, name=first.name, color=first.color, position=first.position)
            for task in tasks:
                await tx.run(_SAVE_TASK, **msgspec.structs.asdict(task))
            return True


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
        where: list[str] = []
        params: list = []
        if list_id is not None:
            where.append("list_id = ?")
            params.append(list_id)
        if done is not None:
            where.append("done = ?")
            params.append(done)
        if due_from is not None:
            where.append("due >= ?")
            params.append(due_from)
        if due_to is not None:
            where.append("due <= ?")
            params.append(due_to)
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
        return await self.db.get().fetch(sql, *params, limit, model=Task)

    async def iterate(self, *, batch: int = 200) -> AsyncIterator[Task]:
        """Every task, fetched `batch` at a time."""
        offset = 0
        while True:
            tasks = await self.db.get().run(_PAGE, limit=batch, offset=offset)
            for task in tasks:
                yield task
            if len(tasks) < batch:
                return
            offset += batch

    async def get(self, id: uuid.UUID) -> Optional[Task]:
        return await self.db.get().first(_TASK, id=id)

    async def save(self, task: Task) -> None:
        """Inserts the task, or updates it if it exists."""
        await self.db.get().run(_SAVE_TASK, **msgspec.structs.asdict(task))

    async def delete(self, id: uuid.UUID) -> bool:
        return await self.db.get().run(_DELETE_TASK, id=id) > 0

    async def delete_done(self, list_id: uuid.UUID) -> list[uuid.UUID]:
        """Deletes the list's finished tasks; returns their IDs."""
        return [task.id for task in await self.db.get().run(_DELETE_DONE, list_id=list_id)]

    async def first_position(self, list_id: uuid.UUID) -> int:
        """A position before every task of the list (new tasks go on top)."""
        row = await self.db.get().fetchrow(
            "SELECT coalesce(min(position), 1) - 1 FROM tasks WHERE list_id = ?", list_id
        )
        return row[0] if row else 0

    async def reorder(self, list_id: uuid.UUID, ids: Sequence[uuid.UUID]) -> None:
        """Puts the list's tasks in the order of `ids`. Tasks of the list not
        in `ids` keep their order, after them; IDs from elsewhere are ignored."""
        async with self.db.get().transaction() as tx:
            rows = await tx.fetch("SELECT id FROM tasks WHERE list_id = ? ORDER BY position", list_id)
            current = [row[0] for row in rows]
            known = set(current)
            wanted = [str(id) for id in ids if str(id) in known]
            await tx.execute(_REORDER, list(dict.fromkeys(wanted + current)), str(list_id))


class SessionRepository:
    """Sign-in sessions, by a hash of their token."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def add(self, token_hash: str, user: str, expires_at: datetime) -> None:
        await self.db.get().run(_ADD_SESSION, token_hash=token_hash, user=user, at=expires_at.isoformat())

    async def user(self, token_hash: str, now: datetime) -> Optional[str]:
        row = await self.db.get().first(_SESSION, token_hash=token_hash, now=now.isoformat())
        return row.user if row else None

    async def delete(self, token_hash: str) -> None:
        await self.db.get().run(_DELETE_SESSION, token_hash=token_hash)

    async def delete_expired(self, now: datetime) -> None:
        await self.db.get().run(_DELETE_EXPIRED, now=now.isoformat())


def _list(row: ListRow, open_tasks: int) -> TaskList:
    """The list `row` with its open task count."""
    return TaskList(id=row.id, name=row.name, color=row.color, position=row.position, open_tasks=open_tasks)  # type: ignore[arg-type]
