# Changelog

## 0.4.0

- Breaking: path parameters are converted by their constraint. `{id:int}`
  gives an `int` and `{id:uuid}` a `uuid.UUID`, in `Router.match()` and as
  handler arguments; other values stay strings. The conversion happens in
  Rust, which already checked the format.
- `@app.on_startup` and `@app.on_shutdown` hooks, sync or async, with or
  without parentheses. Shutdown hooks run in reverse order; a failing
  startup hook stops the server from starting.
- Error handling: `raise HTTPException(status, detail)` from a handler or
  middleware, and `@app.exception_handler(...)` keyed by status (404 and
  405 included) or exception class. Unhandled exceptions, including a
  missing handler or middleware, now give a 500 logged to the `korvex`
  logger instead of propagating to the server.
- `app.group(prefix, middleware=...)` returns an `AppGroup`: decorators
  (`@api.get(...)`) under a shared prefix and middleware, nestable.
- `request.cookies`, `await request.form()` (urlencoded and multipart,
  files as `UploadedFile`; other content types get a 415), and
  `response.set_cookie()` / `response.delete_cookie()`. No new dependency.
- `Response.stream(chunks)`, `Response.file(path, filename=None)` and
  `app.static(prefix, directory)`. Static paths that leave the directory
  (`..`, symlinks) get a 404.
- `HEAD` requests are answered by the `GET` route without the body. They
  used to get a 405.
- [Deployment guide](docs/deployment.md) with ready-to-copy files in
  `examples/deploy/`: uvicorn behind Caddy or nginx, Docker, AWS Lambda
  (Mangum) and Vercel, the last one checked on a real deployment.
- Load test: Falcon and BlackSheep added. Falcon comes within about 10% of
  Korvex; BlackSheep tries routes one by one, which costs it a lot with
  1,000 routes.
- New [ROADMAP.md](ROADMAP.md).

## 0.3.0

- `Router.match` caches the last 1,024 distinct lookups: 67 ns instead of
  347 ns for a repeated URL in the benchmark, ahead of Litestar (92 ns)
  and Sanic (211 ns) with their own caches. Adding or removing a route
  clears the cache.
- Breaking: since results are now shared, they are immutable. `params` is
  a read-only mapping (it still compares equal to a dict and works with
  `**params`) and `middleware` is a tuple, in `match()` and `routes()`.
  `Request.path_params` is read-only too.
- New load test (`benches/load_test.py`): the same app in Korvex,
  Litestar, Starlette, Sanic, FastAPI and Django, flooded on one CPU core.
  Korvex serves the most requests per second with the least memory; see
  the README.

## 0.2.0

Breaking: the API was simplified and made consistent.

| 0.1                                         | 0.2                                       |
|---------------------------------------------|-------------------------------------------|
| `router.add_route(path, handler_name, ...)` | `router.route(path, handler, ...)`        |
| `router.match_route(path, method)`          | `router.match(path, method)`              |
| `router.remove_route(path, method)`         | `router.remove(path, method)`             |
| `exc.args[0]` on `MethodNotAllowed`         | `exc.allowed`                             |
| `method`/`middleware` positional            | keyword-only: `route(p, h, method="POST")` |
| `RouteGroup` methods return the group       | they return `None`                        |
| `Headers.setdefault`, `del headers[name]`, `Headers.items()` | removed (`list(headers)` replaces `items()`) |

Internals were rewritten for readability; matching speed is unchanged.

The benchmark now checks that every router finds the right route, adds
Litestar, and separates cached from uncached lookups (see the README).

## 0.1.1

- First release with tested wheels for every platform.
