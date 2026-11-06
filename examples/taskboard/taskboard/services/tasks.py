"""The rules for tasks: where they go, what's normalized, what gets
announced. Called by the web layer with plain values; knows nothing
about HTTP."""

import logging
import uuid
from collections.abc import AsyncIterator, Sequence
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Union

import msgspec
from msgspec import UNSET, UnsetType

from taskboard.domain import (
    InvalidInput,
    NotFound,
    Task,
    TaskCreated,
    TaskDeleted,
    TaskList,
    TasksReordered,
    TaskUpdated,
)
from taskboard.repositories import ListRepository, TaskRepository
from taskboard.services.events import EventBus

log = logging.getLogger(__name__)

MAX_RESULTS = 500


class TaskService:
    def __init__(self, tasks: TaskRepository, lists: ListRepository, events: EventBus) -> None:
        self.tasks = tasks
        self.lists = lists
        self.events = events

    async def find(
        self,
        *,
        list_id: Optional[uuid.UUID] = None,
        done: Optional[bool] = None,
        due_from: Optional[date] = None,
        due_to: Optional[date] = None,
        search: Optional[str] = None,
        limit: int = MAX_RESULTS,
    ) -> list[Task]:
        search = " ".join(search.split()) if search else None
        limit = max(1, min(limit, MAX_RESULTS))
        return await self.tasks.find(
            list_id=list_id, done=done, due_from=due_from, due_to=due_to, search=search, limit=limit
        )

    def iterate(self) -> AsyncIterator[Task]:
        return self.tasks.iterate()

    async def get(self, id: uuid.UUID) -> Task:
        task = await self.tasks.get(id)
        if task is None:
            raise NotFound("task", id)
        return task

    async def create(self, list_id: uuid.UUID, title: str, *, notes: str = "", due: Optional[date] = None) -> Task:
        """Adds a task at the top of its list."""
        await self._list(list_id)
        task = Task(
            id=uuid.uuid4(),
            list_id=list_id,
            title=_title(title),
            notes=notes.strip(),
            done=False,
            due=due,
            position=await self.tasks.first_position(list_id),
            created_at=_now(),
        )
        await self.tasks.save(task)
        log.info("task %s created: %s", task.id, task.title)
        self.events.publish(TaskCreated(task))
        return task

    async def update(
        self,
        id: uuid.UUID,
        *,
        title: Union[str, UnsetType] = UNSET,
        notes: Union[str, UnsetType] = UNSET,
        done: Union[bool, UnsetType] = UNSET,
        due: Union[date, None, UnsetType] = UNSET,
        list_id: Union[uuid.UUID, UnsetType] = UNSET,
    ) -> Task:
        """Changes the fields given. `due=None` removes the due date; moving
        a task to another list puts it at the top of that list."""
        task = await self.get(id)
        changes: dict[str, object] = {}
        if not isinstance(title, UnsetType):
            changes["title"] = _title(title)
        if not isinstance(notes, UnsetType):
            changes["notes"] = notes.strip()
        if not isinstance(due, UnsetType):
            changes["due"] = due
        if not isinstance(done, UnsetType) and done != task.done:
            changes["done"] = done
            changes["completed_at"] = _now() if done else None
        if not isinstance(list_id, UnsetType) and list_id != task.list_id:
            await self._list(list_id)
            changes["list_id"] = list_id
            changes["position"] = await self.tasks.first_position(list_id)
        if changes:
            task = msgspec.structs.replace(task, **changes)
            await self.tasks.save(task)
            self.events.publish(TaskUpdated(task))
        return task

    async def delete(self, id: uuid.UUID) -> None:
        task = await self.get(id)
        await self.tasks.delete(id)
        log.info("task %s deleted", id)
        self.events.publish(TaskDeleted(id, task.list_id))

    async def reorder(self, list_id: uuid.UUID, ids: Sequence[uuid.UUID]) -> None:
        """Puts the list's tasks in this order (the ones left out after)."""
        await self._list(list_id)
        await self.tasks.reorder(list_id, ids)
        self.events.publish(TasksReordered(list_id))

    async def clear_done(self, list_id: uuid.UUID) -> int:
        """Deletes the list's finished tasks; returns how many."""
        await self._list(list_id)
        ids = await self.tasks.delete_done(list_id)
        for id in ids:
            self.events.publish(TaskDeleted(id, list_id))
        return len(ids)

    async def welcome(self) -> None:
        """On the very first start, a list with a few tasks that show around."""
        today = datetime.now(timezone.utc).date()
        inbox = TaskList(uuid.uuid4(), "Inbox", "blue", 0)
        tips = [
            ("Press N to add a task, / to search, ? for every shortcut", "", None),
            ("Drag tasks to reorder them, or onto another list to move them", "", None),
            (
                "Give a task a due date: it shows up in Today and Planned",
                "Click a task to open it, then pick a date.",
                today,
            ),
            ("Open this page in a second window", "Changes appear in both right away.", today + timedelta(days=1)),
        ]
        tasks = [
            Task(uuid.uuid4(), inbox.id, title, notes, False, due, position, _now())
            for position, (title, notes, due) in enumerate(tips)
        ]
        if await self.lists.create_first(inbox, tasks):
            log.info("first start: welcome list created")

    async def _list(self, id: uuid.UUID) -> TaskList:
        found = await self.lists.get(id)
        if found is None:
            raise NotFound("list", id)
        return found


def _title(title: str) -> str:
    title = " ".join(title.split())  # no leading, trailing or repeated spaces
    if not title:
        raise InvalidInput("title", "a title can't be blank")
    return title


def _now() -> datetime:
    return datetime.now(timezone.utc)
