# Your first API

You will build a small to-do API: list, read, add and filter tasks. You need
basic Python and nothing else. Linked words, like
[handler](glossary.md#handler), are explained in the [glossary](glossary.md).

## 1. Install

A virtual environment keeps this project's packages apart from the others. Create one, then install Korvex, msgspec (it checks your data) and pytest:

```sh
mkdir todo && cd todo
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install --pre korvex msgspec pytest
```

`--pre` is needed while Korvex 1.0 is a release candidate. The built-in
server runs on Linux and macOS; on Windows, `pip install uvicorn` and run
`uvicorn main:app` (any [ASGI](glossary.md#asgi) server works).

## 2. A first route

A [route](glossary.md#route) links a method and a path (`GET /tasks`) to a
[handler](glossary.md#handler): a plain function Korvex calls for that
request. It returns a `Response`; `Response.json` sends JSON. Create `main.py`:

```python
from korvex import App, Response

app = App()

TASKS = {
    1: {"id": 1, "title": "Install Korvex", "done": True},
    2: {"id": 2, "title": "Build a to-do API", "done": False},
}


@app.get("/tasks")
def list_tasks():
    return Response.json(list(TASKS.values()))


if __name__ == "__main__":
    app.run()
```

Run it with `korvex run main:app` (`main` is the file, `app` the variable)
or `python main.py`, then open <http://127.0.0.1:8000/tasks>: you see your
two tasks. Stop with Ctrl+C.

## 3. One task, and a 404

`{id:int}` in a path is a [path parameter](glossary.md#path-parameter): the
part of the URL that changes. Naming the handler's parameter `id` with type
`int` makes Korvex pass the number, and refuse `/tasks/abc`. For an unknown
task, `raise HTTPException(404, ...)` sends a 404 with a JSON message.

Import `HTTPException` too, and add this below `list_tasks`:

```python
@app.get("/tasks/{id:int}")
def get_task(id: int):
    if id not in TASKS:
        raise HTTPException(404, "no such task")
    return Response.json(TASKS[id])
```

Restart the server: `/tasks/9` now shows `{"detail": "no such task"}`.

## 4. A model for the request body

So far we sent dicts and trusted them. A [model](glossary.md#model) says
what a task looks like: names and types. Korvex uses it two ways. A
handler parameter typed with a model is read from the JSON
[request body](glossary.md#request-body) and checked
([validation](glossary.md#validation)). A return annotation (`-> Task`)
means "send this model as JSON". Here, a new task needs only a `title`;
Korvex fills `id` and `done`. Make `TASKS` a `dict[int, Task]`, and have `list_tasks` and `get_task` return them directly, with
`-> list[Task]` and `-> Task` and no `Response.json`: the complete file is
below. Add the models and a create route:

```python
import msgspec

from korvex import App, HTTPException


class NewTask(msgspec.Struct):
    title: str


class Task(msgspec.Struct):
    id: int
    title: str
    done: bool = False


@app.post("/tasks", status=201)
def create_task(body: NewTask) -> Task:
    task = Task(id=len(TASKS) + 1, title=body.title)
    TASKS[task.id] = task
    return task
```

`status=201` means "created". Try `curl -X POST localhost:8000/tasks -d
'{"title": "Write tests"}'`: it returns the new task. With `{"title": 3}`
(a title must be text), Korvex answers `422` before your code runs, and
says where and why:

```json
{"detail": [{"loc": ["body", "title"], "msg": "Expected `str`, got `int`", "type": "validation"}]}
```

## 5. A query parameter

The [query string](glossary.md#query-string) is the part after `?` in
`/tasks?done=true`. A parameter that is not in the path and has a simple
type (`str`, `int`, `bool`...) is read from it. `Optional[bool] = None`
means "optional; absent means no filter". Replace `list_tasks`, and add
`from typing import Optional` at the top:

```python
@app.get("/tasks")
def list_tasks(done: Optional[bool] = None) -> list[Task]:
    return [t for t in TASKS.values() if done is None or t.done == done]
```

`/tasks?done=true` lists finished tasks; `?done=maybe` is a 422.

## 6. A test

A test calls your app without starting a server, so it runs in milliseconds.
`TestClient` is a fake browser; a response has `status` and `json()`.
Save `test_main.py` below, then run `pytest`.

## 7. The complete files

**main.py**

```python
from typing import Optional

import msgspec

from korvex import App, HTTPException


class NewTask(msgspec.Struct):
    title: str


class Task(msgspec.Struct):
    id: int
    title: str
    done: bool = False


app = App()

TASKS: dict[int, Task] = {
    1: Task(id=1, title="Install Korvex", done=True),
    2: Task(id=2, title="Build a to-do API"),
}


@app.get("/tasks")
def list_tasks(done: Optional[bool] = None) -> list[Task]:
    return [t for t in TASKS.values() if done is None or t.done == done]


@app.get("/tasks/{id:int}")
def get_task(id: int) -> Task:
    if id not in TASKS:
        raise HTTPException(404, "no such task")
    return TASKS[id]


@app.post("/tasks", status=201)
def create_task(body: NewTask) -> Task:
    task = Task(id=len(TASKS) + 1, title=body.title)
    TASKS[task.id] = task
    return task


if __name__ == "__main__":
    app.run()
```

**test_main.py**

```python
from korvex.testing import TestClient

from main import app


def test_tasks():
    with TestClient(app) as client:
        done = client.get("/tasks", query={"done": "true"}).json()
        assert done and all(task["done"] for task in done)
        created = client.post("/tasks", json={"title": "Write tests"})
        assert created.status == 201
        assert client.get(f"/tasks/{created.json()['id']}").json()["title"] == "Write tests"


def test_errors():
    with TestClient(app) as client:
        assert client.get("/tasks/999").status == 404
        assert client.post("/tasks", json={"title": 3}).status == 422
        assert client.get("/tasks", query={"done": "maybe"}).status == 422
```

## Where to go next

- [Routes](routes.md): groups, hosts, links between routes, static files.
- [Validation](validation.md): headers, cookies, forms, your own 422 reply.
- [Testing](testing.md): async tests, WebSockets, replacing dependencies.
- [Deployment](deployment.md): workers, nginx, Docker.
- [OpenAPI](openapi.md): `app.docs()` adds a page at `/docs` to try each route.
- [Glossary](glossary.md): every term used in this guide.
