# Changelog

## 0.14.2

A documentation site, Korvex taught to AI coding agents, and the built-in
server's speed checked in CI.

- **A documentation site**, at <https://3d3n-pyc.github.io/korvex/>: the
  guide, a homepage with a first app, and a reference of every public
  class, function and option, built from the docstrings. CI builds it on
  each pull request (a broken link fails) and publishes it from main.
- The guide covers every option: tables for each ready-made middleware,
  your own rate-limit store or security scheme, `Headers` methods,
  `ValidationError`, the test client's `base_url` and WebSocket
  subprotocols, and `app.run()`'s Python names.
- CI checks the built-in server's speed on every pull request
  (`benches/server_check.py`): the base's and the change's servers under
  the same load, one worker pinned to one core, alternating, failing
  past 10% slower. The existing check only timed the App in-process,
  which is how 0.7 to 0.14 lost up to 8% unnoticed.
- **For AI assistants**: the site publishes
  [`llms.txt`](https://3d3n-pyc.github.io/korvex/llms.txt) and
  [`llms-full.txt`](https://3d3n-pyc.github.io/korvex/llms-full.txt),
  rebuilt with the docs; an agent skill
  teaches coding agents to write Korvex apps (its examples run in the
  tests): `korvex agents` installs it in a project, for the installed
  version, or `npx skills add 3d3n-pyc/korvex`; the `korvex` module's
  docstring points agents to both; and `AGENTS.md` gives agents working on Korvex its checks and
  rules.
- `TestClient` and `AsyncTestClient`: a request's `headers=` replace the
  client's headers of the same name, rather than being sent next to them
  (where the app read the client's).

## 0.14.1

The built-in server 47% faster, and the comparisons measured again.

- **The built-in server serves 47% more than 0.14.0**: 39.7k requests/s
  against 26.9k, one worker on one core, measured side by side. Three
  changes, found with perf:
  - From 0.7 on, each request checked its handler's plan (the validation
    0.7 added) before calling it: 0.2 µs alone, but on a core that also
    runs the server's Rust thread and the kernel's network stack, with a
    full instruction cache, it cost 3 to 8% (the same instructions, 8%
    more cycles). A route now works out once how to call a handler with
    nothing to read from the request.
  - Since 0.14, a request's headers were checked one by one, as for a
    `headers=` dict, though the server builds them; and Content-Type was
    looked up before being set. Both are gone (a regression of 0.14).
  - The built-in server built an ASGI scope for each request, then read
    it back into a Request; it now builds the Request directly.
  Under ASGI servers too, a GET answered with JSON is 15% faster
  in-process, and the other cases 4 to 10%.
- The comparisons are measured again, after these changes: the servers
  (`benches/servers.py`) and the frameworks under load
  (`benches/load_test.py`), which dated from 0.6, and validation and
  dependencies in-process. FastrAPI joins the servers, run from one
  process with sync handlers (its only working setup in 0.2.11). Korvex's
  built-in server serves 145k requests/s at 1,000 clients on 4 cores,
  Granian 134k, FastrAPI 67k; on one core under uvicorn, Korvex 34.4k,
  Falcon 30.5k, Starlette 30.3k at best.

## 0.14.0

The public API reviewed before 1.0: simpler to learn, with one way to do
each thing. Breaking changes are listed with how to update.

### Breaking changes

- **A handler takes the request only when it needs it.** It was always
  its first parameter, even unused (`def health(request)`). It's now a
  parameter like the others: named `request`, or annotated `Request`
  (`ws` or `websocket`, or annotated `WebSocket`, for a WebSocket
  handler), anywhere in the list, or not at all:
  `def get_task(id: int) -> Task`. To update: nothing for handlers whose
  first parameter is named `request` (or `ws`); rename or annotate one
  named otherwise (`def h(req)` becomes `def h(req: Request)`): it's
  refused at registration, with a message saying so.
- **Errors are answered as JSON**: `{"detail": "..."}`. An
  `HTTPException`, a 404 or a 405 answered `text/plain` while a
  validation error's 422 was JSON, so an API client got two formats. A
  500 answers `{"detail": "Internal Server Error"}`. To update: clients
  that read the text read `detail`; an `@app.exception_handler(...)`
  can answer anything else, a page for a browser say.
- **`request.query` replaces `request.query_params`, and reads like the
  headers**: `request.query.get("page")` gives the first value,
  `.get_all("tag")` every one; `await request.form()` works the same. It
  was a dict of lists (`query_params["page"][0]`). Typed parameters now
  take the *first* value of a repeated field wherever it comes from: a
  query or form field took the last one (`?page=1&page=2` gave 2, now
  1), a header the first. To update: `request.query_params["x"][0]`
  becomes `request.query["x"]`, `form["x"]` gives the first value
  (`form.get_all("x")` the list).
- **Internal modules are private.** `korvex.app`, `korvex.schema`,
  `korvex.openapi`, `korvex.serve`, `korvex.urls`, `korvex.routing`,
  `korvex.requests`, `korvex.params`, `korvex.errors`, `korvex.response`
  and `korvex.cli` became `korvex._app`... The public modules are
  `korvex`, `korvex.router`, `korvex.testing`, `korvex.security` and
  `korvex.middleware`; what they export is what 1.0 will keep stable. To
  update: import from `korvex` (`from korvex.app import App` becomes
  `from korvex import App`).
- **The standalone router moves to `korvex.router`.** `Router`,
  `RouteGroup` and `MethodNotAllowed` sat in `korvex` next to `App`,
  though an app never uses them. To update:
  `from korvex.router import Router`.
- **One way to declare a route: the decorators.** `App(router=...)`,
  `app.router` and `@app.handler("name")` (to attach a function to a
  route added to the router by name) are gone, and with them two errors
  only that path had: a 500 for router middleware names, and "no handler
  named". To update: declare the route with `@app.get(...)` and the
  others (`app.get(path)(func)` registers a function you already have).
- **`AppGroup` is now `Group`**: what `app.group()` and `app.host()`
  return. With `RouteGroup` gone from `korvex`, there's one kind of group
  left. To update: rename it in type annotations.
- **`Connection`, what a `Request` and a `WebSocket` share**: path,
  headers, cookies, query, path parameters, client, scheme, `app` and
  `url_for()`. `WebSocket` was a `Request` whose `body()`, `json()` and
  `form()` always raised; it's now a `Connection` without them. Before
  hooks get a `Connection`. A dependency annotated `Request` in a
  WebSocket route is refused at registration, with a message: annotate
  it `Connection` to serve both. To update: `isinstance(x, Request)` is
  false for a WebSocket now (`isinstance(x, WebSocket)` works as
  before); hooks and shared dependencies take a `Connection`.
- **`timeout` is now `read_timeout`** in `App.run()`, and `--timeout` becomes
  `--read-timeout` in `korvex run`: the seconds a client gets to send the
  headers, then the body. The old name didn't say what it timed. To
  update: rename it.
- `korvex.middleware` no longer exports `ALL_METHODS` and
  `COMPRESSIBLE`, two defaults of `cors()` and `compress()`.

### New

- **`headers=` takes a dict**, in `Response(...)` and its shortcuts,
  `HTTPException` and `WebSocket.accept()`: `headers={"X-Total": "3"}`.
  Pairs still work, to repeat a name. A dict failed before with "too
  many values to unpack".

### Documentation

- The README is short: what Korvex is, a quickstart and its test, the
  guide, and performance in brief. It was 793 lines.
- The guide is in [`docs/`](docs/index.md), a page per topic, each
  starting with an example: routes, requests and responses, validation,
  dependencies, errors and hooks, WebSockets, OpenAPI and security,
  middleware, testing, the built-in server, deployment, performance and
  the standalone router.

### Inside

- The code is reorganized: the App's module split in three, the
  middleware in a module per hook, one record per route; the query string is
  parsed only when read. Requests are 1 to 5% faster.
- Ruff, pyright and coverage are configured in `pyproject.toml`, and CI
  runs them, with the tests on Python 3.9 to 3.14 and 3.14t.
- New: SECURITY.md (private reports through GitHub), a code of conduct,
  issue and pull request templates, Dependabot, pre-commit hooks.

## 0.13.0

What everyone expects from a framework: a test client, reverse URLs and
a richer OpenAPI.

- `korvex.testing`: `TestClient(app)` runs the app in-process, with its
  lifespan (`with TestClient(app) as client:` runs the startup and
  shutdown hooks), cookies, headers, query strings, JSON, forms and
  multipart files, streamed responses (`client.stream()` to read one as
  it comes) and WebSockets (`client.websocket()`). An error the app
  answers with a 500 is raised in the test with its traceback, and a
  request that gets no answer within `timeout` fails instead of hanging.
  `AsyncTestClient` does the same in `async` tests. No dependency: it
  speaks ASGI directly, to any ASGI app. The taskboard example uses it,
  in place of the client it wrote for itself.
- Reverse URLs: `app.url_for("get_user", id=42)` gives `/users/42`,
  `request.url_for(...)` the full URL (the request's scheme, `ws`/`wss`
  for a WebSocket route, and the route's own host for an `app.host()`
  route, its parameters given too). A route is known by its function's
  name, or by `name=` on its decorator (`get`, `post`... and `websocket`);
  `app.static()` directories by `"static"`, or their `name=`. A given
  name points to one path (several methods on it are fine); function
  names shared by routes on different paths are refused by `url_for()`
  only, with a request for a `name=`. Values are percent-encoded, and
  checked: against `int`, `uuid` and custom constraints, against the
  handler's annotations, not empty, `.` or `..`, a `/` only in a
  `*wildcard`, and the path must reach that route rather than another
  answering it first. Starlette and Litestar escape none of them.
- `request.app`: the `App` handling the request. `operationId` in
  OpenAPI is the route's name.
- Route options for OpenAPI, on the decorators: `status`, `tags`,
  `deprecated`, `responses` (`{404: "No such task"}`, a model, or
  `(description, model)`; `"4XX"` and `"default"` too) and
  `include_in_schema`. Groups and hosts take all but `status`, for their
  routes: tags add up, responses merge. Bad values fail at registration.
- `status` applies at runtime: to the response made from a returned
  model, and to the empty response of a handler annotated `-> None`
  (`status=204`). A returned `Response` keeps its own. A 204 or 304
  route can't have a return model.
- `korvex.security`: `Bearer()`, `APIKey(header=/query=/cookie=)` and
  `OAuth2Password(token_url, scopes=...)` are dependencies that give the
  credential and answer 401 (with `WWW-Authenticate`) without one, or
  `None` with `required=False`. OpenAPI lists them in
  `securitySchemes`, and on every route whose dependencies use one,
  directly or not, so Swagger UI's "Authorize" sends it; a required one
  also documents the 401. The taskboard's session cookie is one.
- Fixed: a handler returning something other than a `Response` (`None`,
  a `dict`) made an `AttributeError` escape the App, so the server
  answered a 500 past the exception handlers and after hooks. It's now
  a `TypeError` that says what to do, answered like any error.

Nothing breaks: handlers that relied on the bug above crashed already.

## 0.12.0

More than one core per worker.

- `threads=N` (`korvex run --threads N`): N event loops per worker, each
  in its own thread with its own server on the socket, each running the
  startup and shutdown hooks. On free-threaded Python (3.14t) they run in
  parallel: with 4 cores, 3 threads serve 57% more than one, within 4% of
  4 workers, with a third of the memory. With the GIL they take turns on
  one core, and a warning says so.
- Wheels for free-threaded Python 3.14t (cp314t), built and tested by the
  Release workflow; CI runs the tests on 3.14t and fails if anything turns
  the GIL back on.
- `app.local` (`LoopLocal`): attributes kept per event loop, for what
  startup hooks open (a connection pool). With threads, each loop runs
  the hooks, and a module-level global would hold one loop's pool for
  all of them. Found by the security review of this version: not a
  security issue, but requests failing.
- `MemoryStore` is safe with several threads; a `RedisStore` made from a
  URL opens one client per event loop.
- `compress()` leaves brotli out of its default encodings on
  free-threaded Python: the `brotli` package turns the GIL back on.
- Benchmarks: `benches/threads.py` (threads and workers, with and without
  the GIL) and `benches/database.py` (each request reads PostgreSQL).

## 0.11.0

Ready-made middleware, for deployments without a proxy.

- `compress(app)`: gzip, brotli (`korvex[brotli]`) or zstd (Python 3.14),
  the best the client accepts. Text-like content over 500 bytes, and
  streamed responses piece by piece, each flushed.
- `security_headers(app)`: `X-Frame-Options`, `X-Content-Type-Options`,
  `Referrer-Policy`, `Cross-Origin-Opener-Policy`, HSTS on HTTPS only, and
  an opt-in `Content-Security-Policy`. A response's own headers win.
- `RedisStore`: a rate limit shared by every worker and machine
  (`korvex[redis]`), atomic in Redis, failing open by default. A store's
  `hit()` can now be a coroutine function.
- `Headers.remove(name)`.

## 0.10.0

Breaking changes are listed with how to update.

- **Removed: App middleware**, `@app.middleware(name)` and `middleware=[...]`
  on `App` routes and groups. Dependencies do the same job, and also work
  for WebSockets, take typed parameters, show in OpenAPI and can be
  replaced in tests. To update: `middleware=["auth"]` becomes
  `dependencies=[Depends(auth)]`; where the middleware returned a
  response, raise `HTTPException` (an error) or `Abort(response)` (that
  exact response). The standalone `Router` keeps its middleware names; a
  route given some through `app.router` answers a 500 in an `App`, rather
  than run without what may be its guard.
- Before hooks run on WebSocket handshakes too, with the `WebSocket` as
  the request; one answering, or raising `HTTPException` or `Abort`,
  refuses the connection. `request_id()` gives WebSocket handlers an ID,
  and **`rate_limit()` now counts handshakes**. A before hook that only
  makes sense for HTTP can skip them with `isinstance(request, WebSocket)`.
- Custom route constraints: `app.constraint("slug", r"[a-z0-9-]+")` (or
  `router.constraint()`), then `{post:slug}`. Regexes, compiled and
  matched in Rust; OpenAPI shows them; the built-in server's per-route
  body limits accept them.
- **Constrained parameters are tried in the order their routes were
  registered** (before plain ones, as before). They were tried most
  recent first, which only mattered for `int` and `uuid` together, which
  never match the same value.
- Host-based routing: `app.host("api.example.com")` and
  `app.host("{tenant}.example.com")` give groups whose routes answer for
  that host; a host parameter reaches the dependencies and handlers that
  take it, like a path parameter. Routes declared without a host answer on every host.
  `app.openapi(host=...)`, and `/docs` documents the host it's served
  from.
- A model filled from a form: `body: Signup = Form()`, with msgspec or
  pydantic, each model field from the form field of the same name. Its
  errors are at `["form", ...]`, and OpenAPI shows it as the form's
  schema.
- `Abort(response)`: raised in a dependency, a handler or a before hook,
  sends that response as is, past the exception handlers. In a
  WebSocket's dependencies, it refuses the connection.

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
  pass a client's own `Forwarded` on untouched. A `Forwarded` line with a
  quote left open is ignored: it could hide the element a proxy appends.
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
