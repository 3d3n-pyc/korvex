"""The lists API, under /api/lists."""

import uuid

from korvex import App, Depends, Request

from taskboard.domain import TaskList
from taskboard.services.lists import ListService
from taskboard.services.tasks import TaskService
from taskboard.web.auth import CurrentUser
from taskboard.web.schemas import Cleared, ListChanges, NewList, Problem, TaskOrder

NOT_FOUND = {404: ("No such list", Problem)}


def register(app: App, lists: ListService, tasks: TaskService, current_user: CurrentUser) -> None:
    api = app.group(
        "/api", dependencies=[Depends(current_user)], tags=["lists"], responses={401: ("Not signed in", Problem)}
    )

    @api.get("/lists")
    async def list_lists(request: Request) -> list[TaskList]:
        """All lists

        In order, each with how many tasks are still open.
        """
        return await lists.find()

    @api.post("/lists", status=201)
    async def create_list(request: Request, body: NewList) -> TaskList:
        """Create a list"""
        return await lists.create(body.name, color=body.color)

    @api.patch("/lists/{id:uuid}", responses=NOT_FOUND)
    async def update_list(request: Request, id: uuid.UUID, body: ListChanges) -> TaskList:
        """Rename or recolor a list"""
        return await lists.update(id, name=body.name or None, color=body.color or None)

    @api.delete("/lists/{id:uuid}", status=204, responses=NOT_FOUND)
    async def delete_list(request: Request, id: uuid.UUID) -> None:
        """Delete a list

        Its tasks go with it. The last list can't be deleted.
        """
        await lists.delete(id)

    @api.put("/lists/{id:uuid}/order", status=204, responses=NOT_FOUND)
    async def reorder_tasks(request: Request, id: uuid.UUID, body: TaskOrder) -> None:
        """Reorder a list's tasks

        After a drag and drop: the task IDs in their new order.
        """
        await tasks.reorder(id, body.task_ids)

    @api.post("/lists/{id:uuid}/clear-done", responses=NOT_FOUND)
    async def clear_done(request: Request, id: uuid.UUID) -> Cleared:
        """Delete a list's finished tasks"""
        return Cleared(await tasks.clear_done(id))
