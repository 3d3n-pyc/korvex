# Korvex

[![PyPI](https://img.shields.io/pypi/v/korvex.svg)](https://pypi.org/project/korvex/)
[![Python versions](https://img.shields.io/pypi/pyversions/korvex.svg)](https://pypi.org/project/korvex/)
[![CI](https://github.com/3d3n-pyc/korvex/actions/workflows/ci.yml/badge.svg)](https://github.com/3d3n-pyc/korvex/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A fast Python web framework, with a router and an HTTP server written in
Rust.

- **Simple**: a function per route; its parameters say what it reads.
- **Typed**: parameters and bodies validated with msgspec or pydantic,
  and described in OpenAPI, with Swagger UI.
- **Fast**: on one CPU core, among the fastest Python frameworks tested;
  its own server serves more than uvicorn, Granian, Robyn and FastrAPI on
  the same cores, with less memory.
- **Complete**: dependencies, WebSockets, a test client, ready-made
  middleware, and its own server, with workers and threads.

Korvex is young (alpha): the API can still change before 1.0, each change
listed in the [changelog](CHANGELOG.md) with how to update.

```sh
pip install korvex
```

## Quickstart

```python
import msgspec                       # or pydantic: pip install korvex msgspec

from korvex import App, HTTPException


class Task(msgspec.Struct):
    id: int
    title: str


app = App()
app.docs(title="Tasks")              # Swagger UI at /docs
TASKS = {1: Task(1, "Write the docs")}


@app.get("/tasks/{id:int}")
def get_task(id: int) -> Task:
    """Get a task"""
    if id not in TASKS:
        raise HTTPException(404, "no such task")
    return TASKS[id]


if __name__ == "__main__":
    app.run()                        # http://127.0.0.1:8000/tasks/1
```

And its test:

```python
from korvex.testing import TestClient


def test_get_task():
    with TestClient(app) as client:
        assert client.get("/tasks/1").json() == {"id": 1, "title": "Write the docs"}
        assert client.get("/tasks/2").status == 404
```

## The guide

Also on the [documentation site](https://3d3n-pyc.github.io/korvex/), with
the full API reference.

Using an AI coding agent? Run `korvex agents` in your project: it installs
a skill that teaches it Korvex ([more](docs/ai.md)).

- [Routes](docs/routes.md): path parameters, groups, hosts, links, static files.
- [Requests and responses](docs/requests-and-responses.md): what a handler gets and returns.
- [Validation](docs/validation.md): typed parameters, JSON bodies, headers, cookies, forms.
- [Dependencies](docs/dependencies.md): signing in, database connections, shared checks.
- [Errors and hooks](docs/errors-and-hooks.md): errors, code around every request, startup and shutdown.
- [WebSockets](docs/websockets.md)
- [OpenAPI and security](docs/openapi.md): statuses, tags, documented errors, Bearer and API keys.
- [Ready-made middleware](docs/middleware.md): CORS, request IDs, rate limiting, compression.
- [Testing](docs/testing.md)
- [The built-in server](docs/server.md) and [deployment](docs/deployment.md): nginx, Caddy, Docker, AWS Lambda.

[`examples/`](examples/) goes from a few lines to a
[complete app](examples/taskboard/) laid out like a real project.

## Performance

The same app on the same 4 CPU cores, each server with its best number of
workers:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/servers-dark.svg">
  <img alt="Requests per second on the same 4 CPU cores, with 200 and 1,000 clients: Korvex built-in server 142.7k and 145.4k, Granian with the Korvex app 123.9k and 134.1k, uvicorn with the Korvex app 117.5k and 110.4k, Robyn 95.7k and 91.7k, FastrAPI 61.4k and 67.1k." src="docs/servers-light.svg">
</picture>

- On one core with uvicorn, Korvex serves 34.4k requests/s; Falcon
  30.5k, Starlette 3.0k to 30.3k, FastAPI 1.6k to 12.6k.
- With validation, a request takes 16.6 µs in Korvex, 73.5 µs in FastAPI.
- On free-threaded Python, one worker with 3 threads serves 57% more than
  one thread, with a third of the memory of 4 workers.

[Performance](docs/performance.md) has every benchmark, and how to run
them.

## Contributing

```sh
uv sync                            # the tools, and Korvex built from source
uv run pytest
```

[CONTRIBUTING.md](CONTRIBUTING.md) has the layout of the code and every
check CI runs; [ROADMAP.md](ROADMAP.md) what's planned. Report a
vulnerability privately, as [SECURITY.md](SECURITY.md) explains. Everyone
taking part follows the [code of conduct](CODE_OF_CONDUCT.md).

## License

[MIT](LICENSE)
