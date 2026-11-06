"""What clients send. Korvex validates request bodies against these before
a handler runs (422 otherwise), and they appear in the OpenAPI document.
Responses use the domain's TaskList and Task directly.

In the *Changes schemas, a field left out stays as it is; `UNSET` tells
"left out" apart from `null`, so `{"due": null}` removes a due date."""

import uuid
from datetime import date
from typing import Annotated, Optional, Union

import msgspec
from msgspec import UNSET, UnsetType

from taskboard.domain import Color

Name = Annotated[str, msgspec.Meta(min_length=1, max_length=60)]
Title = Annotated[str, msgspec.Meta(min_length=1, max_length=200)]
Notes = Annotated[str, msgspec.Meta(max_length=5000)]


class Cleared(msgspec.Struct):
    deleted: int
    """How many tasks were deleted."""


class Problem(msgspec.Struct):
    """What the API answers when something goes wrong (in OpenAPI)."""

    error: str
    request_id: Optional[str] = None
    """The request's ID, to find its log lines."""


class NewList(msgspec.Struct, forbid_unknown_fields=True):
    name: Name
    color: Optional[Color] = None
    """Picked for you if left out."""


class ListChanges(msgspec.Struct, forbid_unknown_fields=True):
    name: Union[Name, UnsetType] = UNSET
    color: Union[Color, UnsetType] = UNSET


class NewTask(msgspec.Struct, forbid_unknown_fields=True):
    list_id: uuid.UUID
    title: Title
    notes: Notes = ""
    due: Optional[date] = None


class TaskChanges(msgspec.Struct, forbid_unknown_fields=True):
    title: Union[Title, UnsetType] = UNSET
    notes: Union[Notes, UnsetType] = UNSET
    done: Union[bool, UnsetType] = UNSET
    due: Union[date, None, UnsetType] = UNSET
    list_id: Union[uuid.UUID, UnsetType] = UNSET
    """Moves the task to the top of that list."""


class TaskOrder(msgspec.Struct, forbid_unknown_fields=True):
    task_ids: Annotated[list[uuid.UUID], msgspec.Meta(max_length=1000)]
    """The list's tasks in their new order; the ones left out go after."""
