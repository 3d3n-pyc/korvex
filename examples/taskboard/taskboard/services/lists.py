"""The rules for lists."""

import logging
import uuid
from typing import Optional

import msgspec

from taskboard.domain import (
    COLORS,
    Color,
    InvalidInput,
    ListCreated,
    ListDeleted,
    ListUpdated,
    NotFound,
    TaskList,
)
from taskboard.repositories import ListRepository
from taskboard.services.events import EventBus

log = logging.getLogger(__name__)


class ListService:
    def __init__(self, lists: ListRepository, events: EventBus) -> None:
        self.lists = lists
        self.events = events

    async def find(self) -> list[TaskList]:
        return await self.lists.find()

    async def get(self, id: uuid.UUID) -> TaskList:
        found = await self.lists.get(id)
        if found is None:
            raise NotFound("list", id)
        return found

    async def create(self, name: str, *, color: Optional[Color] = None) -> TaskList:
        position = await self.lists.next_position()
        new = TaskList(uuid.uuid4(), _name(name), color or COLORS[position % len(COLORS)], position)
        await self.lists.save(new)
        log.info("list %s created: %s", new.id, new.name)
        self.events.publish(ListCreated(new))
        return new

    async def update(self, id: uuid.UUID, *, name: Optional[str] = None, color: Optional[Color] = None) -> TaskList:
        changed = await self.get(id)
        if name is not None:
            changed = msgspec.structs.replace(changed, name=_name(name))
        if color is not None:
            changed = msgspec.structs.replace(changed, color=color)
        await self.lists.save(changed)
        self.events.publish(ListUpdated(changed))
        return changed

    async def delete(self, id: uuid.UUID) -> None:
        """Deletes the list and its tasks. The last list stays."""
        await self.get(id)
        if await self.lists.count() == 1:
            raise InvalidInput("id", "the last list can't be deleted")
        await self.lists.delete(id)
        log.info("list %s deleted", id)
        self.events.publish(ListDeleted(id))


def _name(name: str) -> str:
    name = " ".join(name.split())
    if not name:
        raise InvalidInput("name", "a list needs a name")
    return name
