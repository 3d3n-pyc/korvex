"""The database tables, as korbase models.

`migrations/0001_initial.sql` creates the same schema; `korbase.migrate`
checks that the two still agree.
"""

import uuid
from datetime import date, datetime
from typing import Annotated, Optional

import msgspec
from korbase import FK, PK, Default, Index, table


@table("lists")
class ListRow(msgspec.Struct, frozen=True):
    id: Annotated[uuid.UUID, PK()]
    name: str
    color: str
    position: int


@table(
    "tasks",
    indexes=(
        Index("list_id", "done", "position", name="tasks_by_list"),
        Index("due", name="tasks_by_due", where="due IS NOT NULL"),
    ),
)
class Task(msgspec.Struct, frozen=True):
    id: Annotated[uuid.UUID, PK()]
    list_id: Annotated[uuid.UUID, FK("lists.id", on_delete="cascade")]
    title: str
    notes: Annotated[str, Default("")]
    done: Annotated[bool, Default(False)]
    due: Optional[date]
    position: int
    """The order within its list, smallest first."""
    created_at: datetime
    completed_at: Optional[datetime] = None


@table("sessions")
class SessionRow(msgspec.Struct, frozen=True):
    token_hash: Annotated[str, PK()]
    user: str
    expires_at: str
