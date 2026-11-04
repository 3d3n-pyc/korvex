# Korvex

[![CI](https://github.com/3d3n-pyc/korvex/actions/workflows/ci.yml/badge.svg)](https://github.com/3d3n-pyc/korvex/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A fast router for Python, written in Rust, with an optional ASGI app on top.

```sh
pip install korvex
```

## The router

`Router` maps a path and an HTTP method to a handler **name**. It never
calls your code: what you do with the name is up to you.

```python
from korvex import Router

router = Router()
router.get("/users/{id:int}", "get_user")
router.post("/users", "create_user", middleware=["auth"])

router.match("/users/42")         # ("get_user", {"id": "42"}, [])
router.match("/users", "POST")    # ("create_user", {}, ["auth"])
router.match("/nope")             # None
router.match("/users", "DELETE")  # raises MethodNotAllowed, .allowed == ["POST"]
```

### Path syntax

| Segment     | Matches                              | Example            |
|-------------|--------------------------------------|--------------------|
| `users`     | exactly `users`                      | `/users`           |
| `{id}`      | any segment                          | `/users/{id}`      |
| `{id:int}`  | an integer (`42`, `-7`)              | `/users/{id:int}`  |
| `{id:uuid}` | a UUID                               | `/items/{id:uuid}` |
| `*path`     | the rest of the path (last segment)  | `/static/*path`    |

Captured values are always strings. When several routes could match,
the most specific one wins: literal, then `{id:int}`/`{id:uuid}`, then
`{id}`, then `*path`.

### API

| Method                                           | Does                                                     |
|--------------------------------------------------|----------------------------------------------------------|
| `route(path, handler, *, method="GET", middleware=None)` | Registers a route (replaces an existing one).    |
| `get` / `post` / `put` / `patch` / `delete` / `head` / `options(path, handler, *, middleware=None)` | Shortcuts for `route`. |
| `match(path, method="GET")`                      | `(handler, params, middleware)`, `None`, or raises `MethodNotAllowed`. |
| `remove(path, method="GET")`                     | Removes a route by its pattern. Returns whether it existed. |
| `routes()`                                       | Every route as `(path, method, handler, middleware)`.    |
| `group(prefix, *, middleware=None)`              | A `RouteGroup` (see below).                              |

Invalid paths or methods raise `ValueError`.

### Route groups

```python
api = router.group("/api", middleware=["auth"])
api.get("/users", "list_users")             # GET /api/users, middleware ["auth"]

admin = api.group("/admin", middleware=["admin"])
admin.get("/stats", "stats")                # GET /api/admin/stats, middleware ["auth", "admin"]
```

## The ASGI app

`App` runs the matched handler for you. Serve it with any ASGI server
(uvicorn, hypercorn, granian...).

```python
from korvex import App, Response

app = App()

@app.get("/users/{id:int}", middleware=["auth"])
async def get_user(request, id):
    return Response.json({"id": id})

@app.middleware("auth")
def auth(request):
    if request.headers.get("Authorization") is None:
        return Response.text("Unauthorized", status=401)  # stops here
    return None                                          # carries on
```

```sh
pip install "korvex[serve]"   # installs uvicorn
uvicorn mymodule:app
```

- Handlers receive the `Request` plus path params as keyword arguments,
  and return a `Response`. Middleware returns a `Response` to answer
  early, or `None` to continue. Both can be sync or async.
- `Request` has `method`, `path`, `headers`, `query_params`,
  `path_params`, and `await request.body()` / `await request.json()`.
- `Response(body, status=, headers=)`, `Response.text(...)`,
  `Response.json(...)`, `Response.redirect(...)`.
- Unknown paths get a 404; known paths with the wrong method get a 405
  with an `Allow` header.
- Routes registered on `app.router` directly (for example through a
  group) are linked to a function with `@app.handler("name")`.

More in [`examples/`](examples/).

## Performance

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/benchmark-dark.svg">
  <img alt="Time per route lookup: Korvex 372 ns, Litestar 711 ns, Sanic 3.4 µs, Flask 3.5 µs, Django 1.3 ms, Starlette 1.4 ms at 10,000 routes. With their LRU cache and repeated URLs, Litestar takes 88 ns and Sanic 210 ns." src="docs/benchmark-light.svg">
</picture>

Median time per lookup, 10,000 routes:

| Router | Without cache | With the router's cache, same URLs repeated |
|---|---|---|
| **Korvex** | **372 ns** | (no cache) |
| Litestar | 711 ns | 88 ns |
| Sanic | 3.4 µs | 210 ns |
| Flask | 3.5 µs | |
| Django | 1.3 ms | |
| Starlette / FastAPI | 1.4 ms | |

- Korvex does the actual matching fastest, and its time stays flat as
  routes are added. Django and Starlette try routes one by one, so they
  slow down linearly.
- Litestar and Sanic cache the last 1,024 lookups. When the same URLs come
  back, that cache beats Korvex. It helps less with parameters
  (`/users/1`, `/users/2`, ... are all different URLs).
- This measures routing only. In a full app, the handler and the ASGI
  server cost more than the router.

Reproduce with `uv run pytest benches/bench_router.py --benchmark-only
--benchmark-json=benches/results.json`, then `uv run python benches/plot.py`
to redraw the chart. Results depend on the machine: measure your own
workload before relying on any number.

## Development

```sh
uv run maturin develop --release   # build the Rust extension
uv run pytest                      # Python tests
cargo test                         # Rust tests
```

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)
