"""The lists API, under /api/lists."""

import uuid

from korvex import App, Request, Response

from taskboard.domain import TaskList
from taskboard.services.lists import ListService
from taskboard.services.tasks import TaskService
from taskboard.web.schemas import ListChanges, NewList, TaskOrder


def register(app: App, lists: ListService, tasks: TaskService) -> None:
    api = app.group("/api", middleware=["session"])

    @api.get("/lists")
    async def list_lists(request: Request) -> list[TaskList]:
        """All lists

        In order, each with how many tasks are still open.
        """
        return await lists.find()

    @api.post("/lists")
    async def create_list(request: Request, body: NewList) -> TaskList:
        """Create a list"""
        return await lists.create(body.name, color=body.color)

    @api.patch("/lists/{id:uuid}")
    async def update_list(request: Request, id: uuid.UUID, body: ListChanges) -> TaskList:
        """Rename or recolor a list"""
        return await lists.update(id, name=body.name or None, color=body.color or None)

    @api.delete("/lists/{id:uuid}")
    async def delete_list(request: Request, id: uuid.UUID) -> Response:
        """Delete a list

        Its tasks go with it. The last list can't be deleted.
        """
        await lists.delete(id)
        return Response(status=204)

    @api.put("/lists/{id:uuid}/order")
    async def reorder_tasks(request: Request, id: uuid.UUID, body: TaskOrder) -> Response:
        """Reorder a list's tasks

        After a drag and drop: the task IDs in their new order.
        """
        await tasks.reorder(id, body.task_ids)
        return Response(status=204)

    @api.post("/lists/{id:uuid}/clear-done")
    async def clear_done(request: Request, id: uuid.UUID) -> Response:
        """Delete a list's finished tasks"""
        return Response.json({"deleted": await tasks.clear_done(id)})
