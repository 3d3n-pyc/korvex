"""What the app is made of: lists, tasks, what can happen to them, and
what can go wrong. No HTTP and no SQL here; every other layer uses these."""

import uuid
from datetime import date, datetime
from typing import Literal, Optional

import msgspec

Color = Literal["blue", "green", "orange", "red", "purple", "pink", "teal", "gray"]
COLORS: tuple[Color, ...] = ("blue", "green", "orange", "red", "purple", "pink", "teal", "gray")


class TaskList(msgspec.Struct, frozen=True):
    id: uuid.UUID
    name: str
    color: Color
    position: int
    open_tasks: int = 0
    """How many of its tasks aren't done yet."""


class Task(msgspec.Struct, frozen=True):
    id: uuid.UUID
    list_id: uuid.UUID
    title: str
    notes: str
    done: bool
    due: Optional[date]
    position: int
    """The order within its list, smallest first."""
    created_at: datetime
    completed_at: Optional[datetime] = None


# Events, published by the services when something changes. The tag names
# each one in JSON: {"event": "task_created", "task": {...}}.


class TaskCreated(msgspec.Struct, frozen=True, tag_field="event", tag="task_created"):
    task: Task


class TaskUpdated(msgspec.Struct, frozen=True, tag_field="event", tag="task_updated"):
    task: Task


class TaskDeleted(msgspec.Struct, frozen=True, tag_field="event", tag="task_deleted"):
    id: uuid.UUID
    list_id: uuid.UUID


class TasksReordered(msgspec.Struct, frozen=True, tag_field="event", tag="tasks_reordered"):
    list_id: uuid.UUID


class ListCreated(msgspec.Struct, frozen=True, tag_field="event", tag="list_created"):
    list: TaskList


class ListUpdated(msgspec.Struct, frozen=True, tag_field="event", tag="list_updated"):
    list: TaskList


class ListDeleted(msgspec.Struct, frozen=True, tag_field="event", tag="list_deleted"):
    id: uuid.UUID


# Errors. The web layer turns them into HTTP responses (web/errors.py).


class NotFound(LookupError):
    """A list or a task that doesn't exist (any more)."""

    def __init__(self, kind: str, id: uuid.UUID) -> None:
        super().__init__(f"no {kind} {id}")
        self.kind = kind
        self.id = id


class InvalidInput(ValueError):
    """A request that's well formed but breaks a rule of the app."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field
