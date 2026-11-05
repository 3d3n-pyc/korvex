"""The tasks API, under /api/tasks. Handlers translate between HTTP and the
service: read the request, call one method, return its result."""

import uuid
from datetime import date
from typing import Optional

import msgspec
from korvex import App, Request, Response

from taskboard.domain import Task
from taskboard.services.tasks import TaskService
from taskboard.web.schemas import NewTask, TaskChanges


def register(app: App, tasks: TaskService) -> None:
    api = app.group("/api", middleware=["session"])

    @api.get("/tasks")
    async def find_tasks(
        request: Request,
        list_id: Optional[uuid.UUID] = None,
        done: Optional[bool] = None,
        due_from: Optional[date] = None,
        due_to: Optional[date] = None,
        q: Optional[str] = None,
        limit: int = 500,
    ) -> list[Task]:
        """Find tasks

        Every filter is optional and they combine. In one list (`list_id`):
        open tasks in their order, then done ones, latest first. Otherwise,
        by due date. `q` searches titles and notes. Dates are the client's
        own: "today" for `due_to` is whatever day it is where the user is.
        """
        return await tasks.find(list_id=list_id, done=done, due_from=due_from, due_to=due_to, search=q, limit=limit)

    @api.post("/tasks")
    async def create_task(request: Request, body: NewTask) -> Task:
        """Create a task

        At the top of its list.
        """
        return await tasks.create(body.list_id, body.title, notes=body.notes, due=body.due)

    @api.get("/tasks/{id:uuid}")
    async def get_task(request: Request, id: uuid.UUID) -> Task:
        """Get a task"""
        return await tasks.get(id)

    @api.patch("/tasks/{id:uuid}")
    async def update_task(request: Request, id: uuid.UUID, body: TaskChanges) -> Task:
        """Change a task

        Only the fields sent change; `"due": null` removes the due date.
        """
        return await tasks.update(
            id, title=body.title, notes=body.notes, done=body.done, due=body.due, list_id=body.list_id
        )

    @api.delete("/tasks/{id:uuid}")
    async def delete_task(request: Request, id: uuid.UUID) -> Response:
        """Delete a task"""
        await tasks.delete(id)
        return Response(status=204)

    @api.get("/export")
    def export_tasks(request: Request) -> Response:
        """Export every task

        Newline-delimited JSON, streamed: read from the database and sent a
        batch at a time, however many tasks there are.
        """

        async def lines():
            async for task in tasks.iterate():
                yield msgspec.json.encode(task) + b"\n"

        return Response.stream(
            lines(),
            headers=[
                ("Content-Type", "application/x-ndjson"),
                ("Content-Disposition", 'attachment; filename="tasks.ndjson"'),
            ],
        )
