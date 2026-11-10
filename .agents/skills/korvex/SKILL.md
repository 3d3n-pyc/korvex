---
name: korvex
description: Write web apps and APIs with Korvex, the Python web framework with a Rust router and server. Use when code imports korvex, or when asked to build, change, test or deploy a Korvex app.
---

# Korvex

Korvex is an ASGI web framework: handlers are plain functions, sync or
async, whose parameters say what they read. Validation uses msgspec or
pydantic (both optional). Python 3.9 to 3.14, free-threaded 3.14t
included. The API can change before 1.0: check the installed version
(`korvex.__version__`) against the
[changelog](https://github.com/3d3n-pyc/korvex/blob/main/CHANGELOG.md).

The full documentation, as Markdown for LLMs:
<https://3d3n-pyc.github.io/korvex/llms-full.txt>. Read the page you need
from <https://3d3n-pyc.github.io/korvex/llms.txt> before guessing an API.

## A complete app

```python
import os
import secrets
from typing import Optional

import msgspec

from korvex import App, Depends, Header, HTTPException, Response


class NewTask(msgspec.Struct):
    title: str


class Task(msgspec.Struct):
    id: int
    title: str
    done: bool = False


app = App()
app.docs(title="Tasks")  # /openapi.json, and Swagger UI at /docs
TASKS: dict[int, Task] = {}
API_KEY = os.environ.get("API_KEY", "dev-key")  # never hard-code a real one


def api_key(x_api_key: Optional[str] = Header(None)) -> str:
    if not secrets.compare_digest(x_api_key or "", API_KEY):
        raise HTTPException(401, "bad API key", headers={"WWW-Authenticate": "ApiKey"})
    return x_api_key


api = app.group("/api", dependencies=[Depends(api_key)])


@api.get("/tasks")
def list_tasks(done: Optional[bool] = None) -> list[Task]:
    """List tasks"""
    return [t for t in TASKS.values() if done is None or t.done == done]


@api.post("/tasks", status=201)
def create_task(body: NewTask) -> Task:
    task = Task(id=len(TASKS) + 1, title=body.title)
    TASKS[task.id] = task
    return task


@api.get("/tasks/{id:int}")
def get_task(id: int) -> Task:
    if id not in TASKS:
        raise HTTPException(404, "no such task")
    return TASKS[id]


@api.delete("/tasks/{id:int}", status=204)
def delete_task(id: int) -> None:
    TASKS.pop(id, None)


@app.get("/health", include_in_schema=False)
def health():
    return Response.json({"ok": True})


if __name__ == "__main__":
    app.run()  # or: korvex run main:app --workers 3, or: uvicorn main:app
```

And its tests:

```python
from korvex.testing import TestClient


def test_tasks():
    with TestClient(app, headers={"X-API-Key": "dev-key"}) as client:
        assert client.post("/api/tasks", json={"title": "Write"}).status == 201
        assert client.get("/api/tasks/1").json() == {"id": 1, "title": "Write", "done": False}
        assert client.get("/api/tasks/9").status == 404
        assert client.post("/api/tasks", json={"title": 3}).status == 422
        assert client.get("/api/tasks", headers={"X-API-Key": "no"}).status == 401
```

## Rules that differ from FastAPI and Starlette

- **A handler returns a `Response`, or a model with a return annotation.**
  Returning a `dict` (or a model without `-> Model`) raises `TypeError`
  on the request. For plain data, `return Response.json({...})`.
  `-> None` sends an empty body (use it with `status=204`).
- **`status=` is the success status of a returned model** or `None`; a
  returned `Response` keeps its own.
- **Where a parameter comes from** depends on its name and type: a name
  in the path → path; a msgspec/pydantic model (or a list of them) → JSON
  body; `str`, `int`, `float`, `bool`, `UUID`, `date`, `datetime` (or
  `Optional`/`list` of them) → query string; `Header()`, `Cookie()`,
  `Form()`, `Query(alias=...)` → there; `Depends(func)` → what `func`
  returns. Anything else is refused when the route is declared.
- **The request** is the parameter named `request`, or annotated
  `Request`. For a WebSocket: named `ws` or `websocket`, or annotated
  `WebSocket`. A dependency used by both takes a `Connection`.
- **Path types** go in the path: `{id:int}`, `{id:uuid}`, `*path` (rest
  of the path), or `{name:slug}` after `app.constraint("slug", regex)`.
- **Errors are JSON** `{"detail": ...}`: `raise HTTPException(404, "...")`.
  `raise Abort(response)` sends an exact response (a redirect) from a
  handler, a dependency or a before hook. Validation errors are a 422
  (`ValidationError`, list in `exc.errors`).
- **`request.query` and forms are multi-dicts**: `.get(name)` gives the
  first value, `.get_all(name)` every one. `headers.get()` is
  case-insensitive.
- **Hooks, not middleware classes**: `@app.before_request` (gets a
  `Connection`; returns a `Response` to answer early, or `None`) and
  `@app.after_request` (gets `request, response`). Ready-made ones are
  functions taking the app: `cors(app, allow_origins=[...])`,
  `request_id(app)`, `rate_limit(app, requests=100, per=60)`,
  `compress(app)` (call it first), `security_headers(app)`, from
  `korvex.middleware`.
- **Startup resources go in `app.local`**, not module globals:

  ```python
  @app.on_startup
  async def connect():
      app.local.pool = await asyncpg.create_pool(DATABASE_URL)
  ```

  With `threads=`, each event loop runs the startup hooks and gets its
  own `app.local`.
- **Links**: `app.url_for("get_task", id=1)` gives the path,
  `request.url_for(...)` the full URL. A route's name is its function's
  name, or `name=`.
- **Dependencies** run once per request (`Depends(f, cache=False)` to
  repeat), can `yield` for teardown, and can be replaced in tests with
  `app.dependency_overrides[f] = lambda: ...`.
- **Templates** are not Jinja2Templates: no `TemplateResponse(request, ...)`.
  Use `views.response("page.html", **context)`.

## Pages (HTML templates)

- `views = app.templates("templates")` loads the `.html` files of a folder.
  A handler returns `views.response("page.html", post=post)`.
- The syntax is Jinja-like, but it is Korvex's own engine: `{% extends %}`,
  `{% block %}`, `{% include %}`, `{% if %}`, `{% for %}`, filters with `|`.
  No macros.
- A name, key or attribute that does not exist is an error in `{{ }}`,
  unless you write `|default`: `{{ post.draft|default(False) }}`. In
  `{% if %}` it counts as `None` (false). `None` renders as an empty string.
- `{{ static_url('css/app.css') }}` gives the hashed URL of a file under
  `app.static()`. `{{ items|tojson }}` passes data to a `<script>`.
- `app.templates("templates", reload=True)` recompiles changed templates:
  use it in development only.

## Testing

- `TestClient(app)` in a `with` block runs startup and shutdown hooks.
  Methods take `query=`, `headers=`, `cookies=`, and `json=`, `form=`
  (`files=` for multipart) or `body=`. A response has `status` (not
  `status_code`), `headers`, `body`, `text`, `json()`.
- An unhandled error in the app is raised in the test with its
  traceback; `raise_server_errors=False` gives the 500 instead.
- `client.websocket("/path")` as a context manager; `AsyncTestClient`
  for async tests.

## Running and deploying

- `app.run(host, port, workers=..., threads=...)` or
  `korvex run module:app --workers 3`: the built-in Rust server, Linux
  and macOS. About 3 workers per 4 cores. `threads` only helps on
  free-threaded Python.
- Any ASGI server works too (uvicorn, Granian), and is required on
  Windows.
- In production, put it behind a reverse proxy (nginx, Caddy) for HTTPS,
  and set `--forwarded-allow-ips` to the proxy's address.
- The built-in server answers bodies over 1 MiB with a 413: `--max-body`,
  or `max_body=` on a route.

## Before you finish

- Run the tests with `TestClient` and import the app module once: a wrong
  parameter type fails at import, with a message saying why.
- Keep msgspec/pydantic imports where their models are used.
