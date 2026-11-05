# Korvex

[![CI](https://github.com/3d3n-pyc/korvex/actions/workflows/ci.yml/badge.svg)](https://github.com/3d3n-pyc/korvex/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A fast router for Python, written in Rust, with an ASGI app on top and a
built-in server to run it.

Korvex is young (alpha). It's fast (see [Performance](#performance)), but it
lacks some of what full frameworks offer: WebSockets, validation, OpenAPI. The [roadmap](ROADMAP.md) lists what's coming.

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

router.match("/users/42")         # ("get_user", {"id": 42}, ())
router.match("/users", "POST")    # ("create_user", {}, ("auth",))
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

`{id:int}` gives an `int` and `{id:uuid}` a `uuid.UUID`; every other
captured value is a string. When several routes could match,
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

`match` keeps the last 1,024 distinct lookups in a cache, and adding or
removing a route clears it. Its results are shared between calls, so they
are immutable: `params` is a read-only mapping and `middleware` a tuple.

### Route groups

```python
api = router.group("/api", middleware=["auth"])
api.get("/users", "list_users")             # GET /api/users, middleware ("auth",)

admin = api.group("/admin", middleware=["admin"])
admin.get("/stats", "stats")                # GET /api/admin/stats, middleware ("auth", "admin")
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
  `path_params`, `cookies`, `client` (`(host, port)`), `scheme`, and
  `await request.body()`,
  `await request.json()`, `await request.form()`. A form maps each field
  to its values; files from a multipart form are `UploadedFile`s
  (`filename`, `content_type`, `data`). A body that can't be parsed is
  answered with a 400.
- `Response(body, status=, headers=)`, `Response.text(...)`,
  `Response.json(...)`, `Response.redirect(...)`, and
  `response.set_cookie(...)` / `response.delete_cookie(...)`.
- `Response.stream(chunks)` sends a body piece by piece, from a sync or
  async generator. `Response.file(path, filename=None)` sends a file in
  chunks (with `filename`, as a download). `app.static("/static",
  "public")` serves a directory; paths that leave it get a 404.
- `HEAD` requests are answered by the `GET` route, without the body.
- Unknown paths get a 404; known paths with the wrong method get a 405
  with an `Allow` header.
- `raise HTTPException(404, "user not found")` in a handler or middleware
  answers with that error. `@app.exception_handler(404)` (a status) or
  `@app.exception_handler(ValueError)` (an exception class) customizes the
  response. Any other exception gives a 500 and is logged to the `korvex`
  logger.
- `app.group(prefix, middleware=[...])` works like `router.group()`, with
  decorators: `api = app.group("/api")`, then `@api.get("/users")`.
  Groups nest.
- Routes registered on `app.router` directly are linked to a function
  with `@app.handler("name")`.
- `@app.on_startup` and `@app.on_shutdown` run a function (sync or async)
  when the server starts and stops, for example to open and close a
  database pool. Shutdown hooks run in reverse order. If a startup hook
  raises, the server doesn't start. Both `@app.on_startup` and
  `@app.on_startup()` work.

More in [`examples/`](examples/). To put it in production (behind nginx or
Caddy, in Docker, on AWS Lambda or Vercel), see the
[deployment guide](docs/deployment.md).

## The built-in server

Korvex can run an `App` without an ASGI server, on Linux and macOS. On the
same CPU cores, it serves 20 to 36% more requests than uvicorn with about
40% less memory (see [Performance](#performance)).

```sh
korvex run main:app --workers 3
```

or, from Python, `app.run(workers=3)`. The same options exist in both:

| Option | Default | Does |
|---|---|---|
| `--host`, `--port` | `127.0.0.1`, `8000` | Address to listen on. |
| `--uds PATH` | | Listen on a Unix socket instead, for a proxy on the same machine. |
| `--workers N` | `1` | Processes sharing the port. About 3 per 4 CPU cores works best. A worker that crashes is restarted. |
| `--graceful-timeout S` | `30` | On Ctrl+C or SIGTERM, seconds requests in progress get to finish. |
| `--max-body BYTES` | 1 MiB | Larger request bodies get a 413. A route can set its own: `@app.post("/upload", max_body=50_000_000)`. |
| `--timeout S` | `30` | Seconds a client gets to send the headers, then the body (408 after that). |
| `--forwarded-allow-ips` | `127.0.0.1,::1` | Proxies whose `X-Forwarded-For` and `X-Forwarded-Proto` set `request.client` and `request.scheme`. `*` trusts any. |
| `--access-log` | off | Log each request. |

How it works: a Rust thread per worker reads each request in full, without
touching Python, then hands it to the asyncio loop, which runs your
handlers. A slow or huge upload never holds a Python worker.

Things to know:

- Run it behind a reverse proxy (nginx, Caddy, Traefik) in production: it
  speaks HTTP/1.1 only, and HTTPS, HTTP/2 and HTTP/3 are the proxy's job.
- The 413 and 408 errors are answered by the server itself, before your
  app sees the request, so `@app.exception_handler` doesn't apply to them.
- The `App` is still a plain ASGI app: uvicorn, Granian or a serverless
  platform keep working, and Windows needs one of them.

## Performance

### Servers

The same app, served by each server on the same 4 CPU cores, each with its
best number of workers:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/servers-dark.svg">
  <img alt="Requests per second on the same 4 CPU cores, with 200 and 1,000 clients: Korvex built-in server 126.4k and 130.5k, Granian with the Korvex app 111.6k and 113.4k, uvicorn with the Korvex app 105.0k and 96.3k, Robyn 90.3k and 81.9k." src="docs/servers-light.svg">
</picture>

| Server | Requests/s, 200 clients | 1,000 clients | RAM |
|---|---|---|---|
| **Korvex built-in** (3 workers) | **126.4k** | **130.5k** | **113 MB** |
| Granian + Korvex app (3 workers) | 111.6k | 113.4k | 196 MB |
| uvicorn + Korvex app (4 workers) | 105.0k | 96.3k | 196 MB |
| Robyn (4 processes, 2 threads each) | 90.3k | 81.9k | 228 MB |

- Granian is a Rust ASGI server too: it does better than uvicorn here, but
  the built-in server still leads, because it skips the ASGI layer.
- Robyn runs its own version of the app with its own Rust server.
- No request failed.

Reproduce with `uv run python benches/servers.py` (needs `cargo install oha`,
takes about 4 minutes).

### Under load

The same small app (1,000 routes, one JSON endpoint) in each framework,
served the same way (uvicorn, one worker, one CPU core) and flooded with
requests to random URLs by [oha](https://github.com/hatoo/oha):

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/load-dark.svg">
  <img alt="Requests per second on one CPU core with 200 clients: Korvex 33.7k, Starlette 3.0k to 30.6k, Falcon 30.5k, Litestar 18.2k, Sanic 15.8k, FastAPI 1.6k to 12.4k, BlackSheep 6.7k, Django 1.1k to 2.2k." src="docs/load-light.svg">
</picture>

| Framework | Requests/s, 200 clients | p99 latency, 1,000 clients | RAM at rest, peak |
|---|---|---|---|
| **Korvex** | **33.7k** | **26 ms** | **35 MB, 46 MB** |
| Starlette | 3.0k to 30.6k | 35 to 189 ms | 39 MB, 49 MB |
| Falcon | 30.5k | 31 ms | 60 MB, 63 MB |
| Litestar | 18.2k | 79 ms | 78 MB, 89 MB |
| Sanic | 15.8k | 94 ms | 60 MB, 105 MB |
| FastAPI | 1.6k to 12.4k | 57 to 351 ms | 66 MB, 77 MB |
| BlackSheep | 6.7k | 70 ms | 51 MB, 59 MB |
| Django | 1.1k to 2.2k | 1.3 to 1.9 s | 48 MB, 145 MB |

- Every framework uses a full CPU core under load, so requests per second
  is also how much work each one gets out of that core.
- Falcon, and Starlette in its best case, come within about 10% of Korvex.
- Starlette, FastAPI and Django try routes one by one: the ranges go from
  the tested route declared after the 1,000 others to declared first.
  BlackSheep tries them one by one too, but sorts them itself, deepest
  first, so here the tested route always comes last.
- No request failed. Throughput holds steady as clients are added; latency
  grows because requests queue, not because each one gets slower. After
  the flood, memory stays flat and speed is back to normal.

Robyn isn't in this table, since it can't run on uvicorn: it's compared
with Korvex's built-in server above.

Reproduce with `uv run python benches/load_test.py` (needs
`cargo install oha`, takes about 11 minutes).

### Router alone

Route matching only, without any server:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/benchmark-dark.svg">
  <img alt="Time per route lookup at 10,000 routes. Without cache: Korvex 347 ns, Litestar 732 ns, Sanic 3.5 µs, Flask 3.5 µs, Django 1.3 ms, Starlette 1.5 ms. With each router's cache and repeated URLs: Korvex 67 ns, Litestar 92 ns, Sanic 211 ns." src="docs/benchmark-light.svg">
</picture>

Median time per lookup, 10,000 routes:

| Router | First time a URL is seen | Same URL again (cached) |
|---|---|---|
| **Korvex** | **347 ns** | **67 ns** |
| Litestar | 732 ns | 92 ns |
| Sanic | 3.5 µs | 211 ns |
| Flask | 3.5 µs | (no cache) |
| Django | 1.3 ms | (no cache) |
| Starlette / FastAPI | 1.5 ms | (no cache) |

- Korvex is the fastest in both cases, and its time stays flat as routes
  are added. Django and Starlette try routes one by one, so they slow
  down linearly.
- Korvex, Litestar and Sanic cache the last 1,024 lookups. The cache helps
  most with static URLs; URLs with parameters (`/users/1`, `/users/2`, ...)
  are often different, so they hit it less.
- With few routes, the server and the handler cost more than the router.
  With many routes, a router that tries them one by one becomes the main
  cost (see Under load).

Reproduce with `uv run pytest benches/bench_router.py --benchmark-only
--benchmark-json=benches/results.json`, then `uv run python benches/plot.py`
to redraw both charts. Results depend on the machine: measure your own
workload before relying on any number.

## Development

```sh
uv run maturin develop --release   # build the Rust extension
uv run pytest                      # Python tests
cargo test                         # Rust tests
```

See [CONTRIBUTING.md](CONTRIBUTING.md), and [ROADMAP.md](ROADMAP.md) for
what's planned.

## License

[MIT](LICENSE)
