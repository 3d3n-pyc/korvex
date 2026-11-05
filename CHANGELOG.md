# Changelog

## 0.9.0

The built-in server, ready for production behind a reverse proxy.

- Backpressure: a streamed response, or a WebSocket's outgoing messages,
  waits once 1 MiB is waiting for a slow client, until it catches up. A
  client that stopped reading made the handler produce everything into
  memory before.
- WebSockets get a ping every `ws_ping_interval` seconds (20 by default;
  `korvex run --ws-ping-interval`, 0 for none). A client silent until the
  next ping is closed with 1011.
- A body over the limit (413), too slow (408) or broken (400) is still
  decided by the server, but answered by the app: before hooks, exception
  handlers, after hooks (CORS, request IDs) and the access log see it.
- `Forwarded` (RFC 7239) is read from trusted proxies when they send no
  `X-Forwarded-For` or `-Proto`: nginx, Caddy and Traefik set those, and
  pass a client's own `Forwarded` on untouched.
- `forwarded_host=True` (`korvex run --forwarded-host`): the host a trusted
  proxy forwards (`X-Forwarded-Host`, or `Forwarded`'s `host`) replaces the
  `Host` header the app sees. Off by default, as in Django and Werkzeug:
  nginx passes a client's `X-Forwarded-Host` on as is.
- `X-Forwarded-Proto` believes its rightmost value, like `-Host`: the one
  the nearest proxy set, not one the client could have written.
- CI fails a pull request that makes Korvex more than 10% slower than its
  base branch (`benches/perf_check.py`).

## 0.8.0

- `Depends(func)`: a parameter gets the value `func` returns. Dependencies
  take parameters like handlers (query, headers, cookies, body, path, the
  request, other dependencies), can be sync or async, run once per
  request (`cache=False` otherwise), and can stop the request with
  `HTTPException`. One that `yield`s is torn down after the handler, or
  after the stream for a streamed response, with the handler's error
  raised at the `yield`.
- `dependencies=[Depends(...)]` on routes, groups and WebSockets.
- `app.dependency_overrides`, to replace a dependency in tests.
- `Header()`, `Cookie()`, `Form()` (files too) and `Query(alias=...)`, as
  a parameter's default or in `Annotated[...]`.
- WebSocket handlers get parameters and dependencies like HTTP handlers; a
  dependency raising `HTTPException` refuses the connection.
- OpenAPI describes headers, cookies, forms, and the parameters of
  dependencies.
- `Request.body()` and `form()` can be called more than once.
- Path parameters annotated with a type Korvex can't convert are refused
  at registration, like query parameters in 0.7.
- Each route is registered under its own handler name (`module.func`,
  then `module.func#2`...). A function serving two routes now runs each
  route's own dependencies, and functions of the same name registered by
  a factory in several groups each run their own code: before, the last
  one registered ran for all of them.
- `examples/taskboard/` guards its API and WebSocket with one dependency.

## 0.7.0

- Validation from type annotations, with msgspec or pydantic models (both
  optional): a model parameter is the JSON body, typed parameters come
  from the path or the query string (`str`, `int`, `float`, `bool`,
  `UUID`, `date`, `datetime`), a model return annotation becomes a JSON
  response. Invalid input gets a 422 listing the problems
  (`ValidationError`, customizable with `@app.exception_handler(422)`).
- `app.openapi(title=..., version=...)`: an OpenAPI 3.1 document built
  from the routes, typed parameters, models and docstrings.
- `app.docs()`: serves the document at `/openapi.json` and Swagger UI at
  `/docs`.
- `HTTPException` moves to `korvex.errors`; `from korvex import
  HTTPException` works as before.
- Type checkers accept handlers that return a model, and exception
  handlers whose exception is annotated with a narrower class.
- `examples/taskboard/`: a complete todo list laid out like a real project
  (lists, due dates, drag and drop, live updates; SQLite, layers, settings
  and its own tests).

## 0.6.0

- `@app.before_request` and `@app.after_request`: hooks around every
  request, under ASGI and in the built-in server. Before hooks run ahead
  of routing and can answer right away; after hooks see every response,
  errors included, in reverse order.
- `korvex.middleware.cors(app, ...)`: CORS, preflight requests included.
- `korvex.middleware.request_id(app)`: an ID per request, sent back in
  `X-Request-ID`, available through `current_request_id()`, added to log
  records by `RequestIdFilter` and shown in the built-in server's access
  logs.
- WebSockets: `@app.websocket(path)`, with `accept()`, `receive()`,
  `send_text/bytes/json()`, `close()` and `async for`. They work the same
  under ASGI servers and in the built-in server (messages limited to
  `max_body`, closed with 1001 on shutdown).
- The nginx examples pass WebSocket upgrades on; without that, WebSocket
  connections failed behind nginx.
- `korvex.middleware.rate_limit(app, requests=..., per=..., key=...)`:
  per-key limits (client address by default), 429 with `Retry-After`,
  RateLimit-* headers. In-memory by default, one store per worker; the
  `RateLimitStore` interface allows a shared one.

## 0.5.0

A built-in server, written in Rust, to run an App without an ASGI server
(Linux and macOS).

- `korvex run main:app` or `app.run()`. On the same 4 CPU cores it serves
  126k to 131k requests/s, against 105k/96k for uvicorn, 112k/113k for
  Granian and 90k/82k for Robyn, with about 40% less memory than the first
  two (`benches/servers.py`, chart in the README).
- How it works: a Rust thread per worker reads each request in full, then
  hands it to the asyncio loop, which runs the handlers, waiting ones
  included. Requests go through the same dispatch as under ASGI.
- `--workers N`: processes sharing one listening socket; a crashed worker
  is restarted, one failing at startup stops everything.
- Graceful shutdown on SIGINT and SIGTERM (`--graceful-timeout`).
- Request bodies over `--max-body` (1 MiB by default, per route with
  `max_body=`) get a 413; slow headers or bodies (`--timeout`) a 408.
- `--uds` for a Unix socket; `request.client` and `request.scheme`, from
  `X-Forwarded-For` and `X-Forwarded-Proto` when they come from
  `--forwarded-allow-ips` or the socket; `--access-log`.
- `request.client` and `request.scheme` also work under ASGI servers.
- The deployment guide covers the built-in server behind Caddy and nginx,
  and the Docker example now uses it.

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
  files as `UploadedFile`; other content types get a 415, undecodable text
  a 400), and
  `response.set_cookie()` / `response.delete_cookie()`. No new dependency.
- `Response.stream(chunks)`, `Response.file(path, filename=None)` and
  `app.static(prefix, directory)`. Static paths that leave the directory
  (`..`, symlinks) get a 404.
- `HEAD` requests are answered by the `GET` route without the body. They
  used to get a 405.
- `await request.json()` answers invalid JSON with a 400 instead of a 500.
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
