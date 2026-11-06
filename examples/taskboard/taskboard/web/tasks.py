"""The tasks API, under /api/tasks. Handlers translate between HTTP and the
service: read the request, call one method, return its result."""

import uuid
from datetime import date
from typing import Optional

import msgspec

from korvex import App, Depends, Response
from taskboard.domain import Task
from taskboard.services.tasks import TaskService
from taskboard.web.auth import CurrentUser
from taskboard.web.schemas import NewTask, Problem, TaskChanges

NOT_FOUND = {404: ("No such task", Problem)}


def register(app: App, tasks: TaskService, current_user: CurrentUser) -> None:
    api = app.group(
        "/api", dependencies=[Depends(current_user)], tags=["tasks"], responses={401: ("Not signed in", Problem)}
    )

    @api.get("/tasks")
    async def find_tasks(
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

    @api.post("/tasks", status=201, responses={404: ("No such list", Problem)})
    async def create_task(body: NewTask) -> Task:
        """Create a task

        At the top of its list.
        """
        return await tasks.create(body.list_id, body.title, notes=body.notes, due=body.due)

    @api.get("/tasks/{id:uuid}", responses=NOT_FOUND)
    async def get_task(id: uuid.UUID) -> Task:
        """Get a task"""
        return await tasks.get(id)

    @api.patch("/tasks/{id:uuid}", responses=NOT_FOUND)
    async def update_task(id: uuid.UUID, body: TaskChanges) -> Task:
        """Change a task

        Only the fields sent change; `"due": null` removes the due date.
        """
        return await tasks.update(
            id, title=body.title, notes=body.notes, done=body.done, due=body.due, list_id=body.list_id
        )

    @api.delete("/tasks/{id:uuid}", status=204, responses=NOT_FOUND)
    async def delete_task(id: uuid.UUID) -> None:
        """Delete a task"""
        await tasks.delete(id)

    @api.get("/export")
    def export_tasks() -> Response:
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
