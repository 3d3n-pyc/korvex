# Changelog

## 1.0.0rc2

Fixes found by fuzzing and an external report; no API change.

- **Security: templates refuse `format` and `format_map`.** A format string
  such as `"{0.__globals__}".format(obj)` could read attributes starting with
  `_` at runtime, past the underscore rule. The attributes are now refused at
  compile time. This needs control of a template's source; reported externally.
  Migration: an object's own `.format()` method can't be called from a
  template; register a filter instead.
- **Fixed: malformed multipart part headers return 400, not 500.** A part
  with an empty field name, a header line without a colon, or bare LF line
  endings caused a 500, on Python 3.14 only (older versions ignored the
  part). Found by fuzzing.
- **Fixed: an unknown method returns 501, not 500.** A request such as
  `POSE / HTTP/1.1` made the router raise, on every server, with a
  traceback in the log. RFC 9110 asks for 501 Not Implemented. Found by
  fuzzing.
- **New: fuzzing for contributors.** `fuzz/` has HTTP and WebSocket fuzzers,
  a cargo-fuzz target for the Rust router and proxy, and Hypothesis property
  tests for the Python parsers. Nightly CI runs them. See CONTRIBUTING.md.

## 1.0.0rc1

The API is frozen. From 1.0, no breaking change without a 2.0. The breaking
changes below are the last before then; each has a migration line.

- **New: ASGI `root_path`.** An app served under a prefix
  (`uvicorn --root-path /api`) routes correctly. `request.path` stays the
  route path, `request.root_path` holds the prefix, and `url_for` (on the
  request, the app and in templates) and `static_url` include it. The
  built-in server takes it too, for a proxy that strips the prefix:
  `korvex run --root-path /api` or `app.run(root_path="/api")`. An app is
  served under one root_path. Guide: <https://3d3n-pyc.github.io/korvex/server/>.
- **New exports** from `korvex`: `StreamingResponse`, `MultiDict`,
  `UndefinedError` and `TemplateSyntaxError`.
- **Breaking: `ValidationError` is now `RequestValidationError`.** Migration:
  rename it in imports and in `exception_handler`. The OpenAPI schema name
  stays `ValidationError`.
- **Breaking: template context is a dict.** `Templates.render(name, context)`
  and `response(name, context)` take the variables as a dict, not as
  keywords. Migration: `views.response("p.html", post=post)` becomes
  `views.response("p.html", {"post": post})`. A variable can now be called
  `status` or `headers`.
- **Breaking: rate limit `Decision` is a frozen dataclass.** Migration: read
  `d.allowed`, `d.remaining` and so on, instead of unpacking it as a tuple.
- **Breaking: more keyword-only arguments.** `sessions(app, secret_key=...)`,
  `MemoryStore(clock=...)`, `WebSocket.accept(subprotocol, headers=...)` and
  `App.static_url(path, name=...)`. Migration: pass `secret_key`, `clock`,
  `headers` and `name` by keyword.

## 0.17.0

Signed cookie sessions, flash messages and CSRF protection, ready-made.

- **Sessions.** `sessions(app, secret_key=...)` keeps a small dict per
  visitor in a signed cookie, and `current_session()` returns it. The
  signature is HMAC-SHA256: the data is signed, not encrypted, so keep
  secrets out of it. Pass a list of keys to rotate them. The cookie is
  sent again only when the session changes, and holds up to 4 KB. Guide:
  <https://3d3n-pyc.github.io/korvex/sessions/>.
- **Flash messages.** `flash(message, category="info")` and
  `get_flashed_messages()`, also in templates.
- **CSRF protection.** `csrf(app, trusted_origins=(), exempt=())` refuses
  cross-origin unsafe requests and WebSocket handshakes with a 403. It
  reads the `Sec-Fetch-Site` and `Origin` headers, so it needs no token.
- **New exports** in `korvex.middleware`: `sessions`, `current_session`,
  `flash`, `get_flashed_messages` and `csrf`.
- **Changed: test clients send Secure cookies over http to local hosts.**
  `TestClient` and `AsyncTestClient` now send them back over http to
  `testserver`, `localhost`, `127.0.0.1` and `[::1]`, as browsers do for
  localhost. They used to send them over https only. Migration: a test
  that relied on Secure cookies not being sent over `http://testserver`
  must use another host.

## 0.16.0

Server-side HTML templates, and static files with hashed URLs.

- **Templates**, Korvex's own engine, with Jinja-like syntax (`if`, `for`,
  `set`, `extends`, `block`, `include`, filters). Create them with
  `app.templates("templates")`, then `views.response("page.html", **context)`.
  HTML is always escaped, and an undefined variable is an error that names
  the file and line. Each template is compiled once to a Python function.
  `reload=True` reloads them while developing. Guide:
  <https://3d3n-pyc.github.io/korvex/templates/>. No migration needed.
- **Hashed static URLs.** `static_url('css/app.css')` in a template, or
  `app.static_url()`, gives `/static/css/app.3f2a1c9b.css`. The file is
  served with a one-year immutable cache, so a changed file gets a new URL.
- **New exports**: `Templates`, `Markup` and `TemplateError`.
- **Fixes**: `rate_limit` could refuse a request within the allowance when
  requests arrived at the same clock tick (seen on Windows).
- Rendering is 4 to 6 times faster than Jinja2, on par with minijinja
  (see [Performance](docs/performance.md#templates)).

## 0.15.1

A fix for WebSocket clients in the built-in server.

- **A WebSocket client that closes gets a Close frame back**, as RFC 6455
  asks. The server used to just end the connection, and strict clients
  saw an abnormal closure (1006).
- **A soak test** (`benches/soak.py`) runs the server for 10 minutes under
  mixed load each night, and fails on memory growth, errors or a worker
  restart.

## 0.15.0

The built-in server holds up in production: bounded memory, workers that
always stop, and options to cap connections and recycle workers.

- **Backpressure on incoming WebSocket messages.** Past 1 MiB not yet
  received by the handler, the server stops reading the socket until it
  catches up. A 50 MB flood now grows the worker by 0.8 MiB. A client held
  back this way isn't closed by the ping timeout.
- **The supervisor always stops, and doesn't restart in a hot loop.**
  Workers still running 5 s after `graceful_timeout` get SIGKILL; a second
  Ctrl+C or SIGTERM kills them at once. A worker that keeps crashing is
  restarted after a growing delay (0.1 s, doubling to 30 s).
- **A SIGTERM during startup stops a worker gracefully**, with its
  shutdown hooks, instead of killing it outright.
- **A graceful stop serves a request already on its way.** A just-accepted
  connection whose request wasn't read yet got a reset; it now gets up to
  1 s to deliver the request, which is served.
- **`--max-connections N`** (`app.run(max_connections=N)`): at N open
  connections, per worker and per thread, the server stops accepting until
  one closes; new clients wait in the kernel's backlog. Bounds memory to
  about N times `max_body`. Off by default.
- **`--max-requests N`** (`app.run(max_requests=N)`): a worker that has
  served N requests (plus a random 0 to 10%) stops gracefully and is
  replaced at once, which caps slow memory leaks. Off by default.

## 0.14.3

The built-in server made safer in production.

- **No more 100% CPU when the server runs out of file descriptors**: after
  an error accepting a connection, it waits 50 ms before trying again.
- **Tests of malformed and ambiguous HTTP** against a real server (request
  smuggling, bad headers, bad chunk sizes, pipelining, keep-alive, idle
  timeout). All were already handled; the tests keep it that way.
- **"Running in production" in the deployment guide**: the proxy must close
  idle connections before Korvex does (`--read-timeout`, 30 s), or it can
  get a sporadic 502; settings for nginx, Caddy and AWS ALB. Also
  Kubernetes (`preStop`, grace period), a health route, file descriptor
  limits.

## 0.14.2

A documentation site, Korvex taught to AI coding agents, and the built-in
server's speed checked in CI.

- **A documentation site**, at <https://3d3n-pyc.github.io/korvex/>: the
  guide, a homepage with a first app, and a reference of every public
  class, function and option, built from the docstrings. CI builds it on
  each pull request and publishes it from main.
- The guide covers every option: tables for each ready-made middleware,
  your own rate-limit store or security scheme, `Headers` methods,
  `ValidationError`, the test client's `base_url` and WebSocket
  subprotocols, and `app.run()`'s Python names.
- CI checks the built-in server's speed on every pull request
  (`benches/server_check.py`) and fails past 10% slower. The earlier check
  only timed the App in-process, so 0.7 to 0.14 lost up to 8% unnoticed.
- **For AI assistants**: the site publishes
  [`llms.txt`](https://3d3n-pyc.github.io/korvex/llms.txt) and
  [`llms-full.txt`](https://3d3n-pyc.github.io/korvex/llms-full.txt). An
  agent skill teaches coding agents to write Korvex apps: `korvex agents`
  installs it in a project, or `npx skills add 3d3n-pyc/korvex`. The
  `korvex` module's docstring points agents to both, and `AGENTS.md` gives
  agents working on Korvex its checks and rules.
- `TestClient` and `AsyncTestClient`: a request's `headers=` replace the
  client's headers of the same name, instead of being sent next to them.

## 0.14.1

The built-in server 47% faster, and the comparisons measured again.

- **The built-in server serves 47% more than 0.14.0**: 39.7k requests/s
  against 26.9k, one worker on one core. Handler plans are resolved once
  per route, a 0.14 regression in header checks is gone, and the server
  builds the Request directly instead of through an ASGI scope. Under ASGI
  servers too, a GET answered with JSON is 15% faster in-process, and the
  other cases 4 to 10%.
- The server and framework comparisons (`benches/servers.py`,
  `benches/load_test.py`) are measured again, and FastrAPI joins the
  servers. Korvex's built-in server serves 145k requests/s at 1,000
  clients on 4 cores, Granian 134k, FastrAPI 67k; on one core under
  uvicorn, Korvex 34.4k, Falcon 30.5k, Starlette 30.3k at best.

## 0.14.0

The public API reviewed before 1.0: simpler to learn, with one way to do
each thing. Breaking changes are listed with how to update.

### Breaking changes

- **A handler takes the request only when it needs it.** It was always the
  first parameter, even unused (`def health(request)`). It's now a
  parameter like the others: named `request`, or annotated `Request`
  (`ws` or `websocket`, or annotated `WebSocket`, for a WebSocket
  handler), anywhere in the list, or not at all:
  `def get_task(id: int) -> Task`. To update: nothing for handlers whose
  first parameter is named `request` (or `ws`); rename or annotate one
  named otherwise (`def h(req)` becomes `def h(req: Request)`), or it's
  refused at registration with a message saying so.
- **Errors are answered as JSON**: `{"detail": "..."}`. An
  `HTTPException`, a 404 or a 405 answered `text/plain`, a 422 JSON. A 500
  answers `{"detail": "Internal Server Error"}`. To update: clients that
  read the text read `detail`; an `@app.exception_handler(...)` can answer
  anything else, a page for a browser say.
- **`request.query` replaces `request.query_params`, and reads like the
  headers**: `request.query.get("page")` gives the first value,
  `.get_all("tag")` every one; `await request.form()` works the same.
  Typed parameters now take the *first* value of a repeated field wherever
  it comes from (a query or form field took the last one: `?page=1&page=2`
  gave 2, now 1). To update: `request.query_params["x"][0]` becomes
  `request.query["x"]`, `form["x"]` gives the first value
  (`form.get_all("x")` the list).
- **Internal modules are private.** `korvex.app`, `korvex.schema`,
  `korvex.openapi`, `korvex.serve`, `korvex.urls`, `korvex.routing`,
  `korvex.requests`, `korvex.params`, `korvex.errors`, `korvex.response`
  and `korvex.cli` became `korvex._app`... The public modules are
  `korvex`, `korvex.router`, `korvex.testing`, `korvex.security` and
  `korvex.middleware`; what they export is what 1.0 will keep stable. To
  update: import from `korvex` (`from korvex.app import App` becomes
  `from korvex import App`).
- **The standalone router moves to `korvex.router`**: `Router`,
  `RouteGroup` and `MethodNotAllowed`. To update:
  `from korvex.router import Router`.
- **One way to declare a route: the decorators.** `App(router=...)`,
  `app.router` and `@app.handler("name")` are gone, with the two errors
  only that path had (a 500 for router middleware names, "no handler
  named"). To update: declare the route with `@app.get(...)` and the
  others (`app.get(path)(func)` registers a function you already have).
- **`AppGroup` is now `Group`**: what `app.group()` and `app.host()`
  return. To update: rename it in type annotations.
- **`Connection`, what a `Request` and a `WebSocket` share**: path,
  headers, cookies, query, path parameters, client, scheme, `app` and
  `url_for()`. A `WebSocket` is no longer a `Request` (its `body()`,
  `json()` and `form()` always raised). Before hooks get a `Connection`. A
  dependency annotated `Request` in a WebSocket route is refused at
  registration: annotate it `Connection` to serve both. To update:
  `isinstance(x, Request)` is false for a WebSocket now
  (`isinstance(x, WebSocket)` works as before); hooks and shared
  dependencies take a `Connection`.
- **`timeout` is now `read_timeout`** in `App.run()`, and `--timeout` becomes
  `--read-timeout` in `korvex run`: the seconds a client gets to send the
  headers, then the body. To update: rename it.
- `korvex.middleware` no longer exports `ALL_METHODS` and `COMPRESSIBLE`.

### New

- **`headers=` takes a dict**, in `Response(...)` and its shortcuts,
  `HTTPException` and `WebSocket.accept()`: `headers={"X-Total": "3"}`.
  Pairs still work, to repeat a name.

### Documentation

- The README is short (it was 793 lines): what Korvex is, a quickstart and
  its test, the guide, and performance in brief.
- The guide is in [`docs/`](docs/index.md), a page per topic, each
  starting with an example.

### Inside

- The code is reorganized (the App's module split in three, the middleware
  in a module per hook, one record per route), and requests are 1 to 5%
  faster.
- Ruff, pyright and coverage are configured in `pyproject.toml` and run in
  CI, with the tests on Python 3.9 to 3.14 and 3.14t.
- New: SECURITY.md (private reports through GitHub), a code of conduct,
  issue and pull request templates, Dependabot, pre-commit hooks.

## 0.13.0

What everyone expects from a framework: a test client, reverse URLs and
a richer OpenAPI.

- `korvex.testing`: `TestClient(app)` runs the app in-process, with its
  lifespan (`with TestClient(app) as client:` runs the startup and
  shutdown hooks), cookies, headers, query strings, JSON, forms and
  multipart files, streamed responses (`client.stream()`) and WebSockets
  (`client.websocket()`). An error the app answers with a 500 is raised in
  the test with its traceback, and a request with no answer within
  `timeout` fails instead of hanging. `AsyncTestClient` does the same in
  `async` tests. No dependency: it speaks ASGI directly, to any ASGI app.
- Reverse URLs: `app.url_for("get_user", id=42)` gives `/users/42`,
  `request.url_for(...)` the full URL (the request's scheme, `ws`/`wss`
  for a WebSocket route, the route's own host for an `app.host()` route).
  A route is known by its function's name, or by `name=` on its decorator
  (`get`, `post`... and `websocket`); `app.static()` directories by
  `"static"`, or their `name=`. A name points to one path (several methods
  on it are fine); function names shared by routes on different paths are
  refused by `url_for()` only. Values are percent-encoded and checked
  (against `int`, `uuid` and custom constraints, the handler's
  annotations, not empty, `.` or `..`, a `/` only in a `*wildcard`), and
  the path must reach that route rather than another answering it first.
- `request.app`: the `App` handling the request. `operationId` in OpenAPI
  is the route's name.
- Route options for OpenAPI, on the decorators: `status`, `tags`,
  `deprecated`, `responses` (`{404: "No such task"}`, a model, or
  `(description, model)`; `"4XX"` and `"default"` too) and
  `include_in_schema`. Groups and hosts take all but `status`, for their
  routes: tags add up, responses merge. Bad values fail at registration.
- `status` applies at runtime: to the response made from a returned model,
  and to the empty response of a handler annotated `-> None`
  (`status=204`). A returned `Response` keeps its own. A 204 or 304 route
  can't have a return model.
- `korvex.security`: `Bearer()`, `APIKey(header=/query=/cookie=)` and
  `OAuth2Password(token_url, scopes=...)` are dependencies that give the
  credential and answer 401 (with `WWW-Authenticate`) without one, or
  `None` with `required=False`. OpenAPI lists them in `securitySchemes`,
  and on every route whose dependencies use one, so Swagger UI's
  "Authorize" sends it; a required one also documents the 401.
- Fixed: a handler returning something other than a `Response` (`None`, a
  `dict`) let an `AttributeError` escape the App, past the exception
  handlers and after hooks. It's now a `TypeError` that says what to do,
  answered like any error.

Nothing breaks: handlers that relied on the bug above crashed already.

## 0.12.0

More than one core per worker.

- `threads=N` (`korvex run --threads N`): N event loops per worker, each in
  its own thread with its own server on the socket, each running the
  startup and shutdown hooks. On free-threaded Python (3.14t) they run in
  parallel: with 4 cores, 3 threads serve 57% more than one, within 4% of
  4 workers, with a third of the memory. With the GIL they take turns on
  one core, and a warning says so.
- Wheels for free-threaded Python 3.14t (cp314t); CI runs the tests on
  3.14t and fails if anything turns the GIL back on.
- `app.local` (`LoopLocal`): attributes kept per event loop, for what
  startup hooks open (a connection pool). With threads, each loop runs the
  hooks, so a module-level global would hold one loop's pool for all of
  them.
- `MemoryStore` is safe with several threads; a `RedisStore` made from a
  URL opens one client per event loop.
- `compress()` leaves brotli out of its default encodings on free-threaded
  Python: the `brotli` package turns the GIL back on.
- Benchmarks: `benches/threads.py` and `benches/database.py`.

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
  route given some through `app.router` answers a 500 in an `App`.
- Before hooks run on WebSocket handshakes too, with the `WebSocket` as
  the request; one answering, or raising `HTTPException` or `Abort`,
  refuses the connection. `request_id()` gives WebSocket handlers an ID,
  and **`rate_limit()` now counts handshakes**. A before hook that only
  makes sense for HTTP can skip them with `isinstance(request, WebSocket)`.
- Custom route constraints: `app.constraint("slug", r"[a-z0-9-]+")` (or
  `router.constraint()`), then `{post:slug}`. OpenAPI shows them; the
  built-in server's per-route body limits accept them.
- **Constrained parameters are tried in the order their routes were
  registered** (before plain ones, as before), not most recent first. This
  only mattered for `int` and `uuid` together, which never match the same
  value.
- Host-based routing: `app.host("api.example.com")` and
  `app.host("{tenant}.example.com")` give groups whose routes answer for
  that host; a host parameter reaches the dependencies and handlers that
  take it, like a path parameter. Routes declared without a host answer on
  every host. `app.openapi(host=...)`, and `/docs` documents the host it's
  served from.
- A model filled from a form: `body: Signup = Form()`, with msgspec or
  pydantic, each model field from the form field of the same name. Its
  errors are at `["form", ...]`, and OpenAPI shows it as the form's schema.
- `Abort(response)`: raised in a dependency, a handler or a before hook,
  sends that response as is, past the exception handlers. In a WebSocket's
  dependencies, it refuses the connection.

## 0.9.0

The built-in server, ready for production behind a reverse proxy.

- Backpressure: a streamed response, or a WebSocket's outgoing messages,
  waits once 1 MiB is waiting for a slow client, until it catches up.
- WebSockets get a ping every `ws_ping_interval` seconds (20 by default;
  `korvex run --ws-ping-interval`, 0 for none). A client silent until the
  next ping is closed with 1011.
- A body over the limit (413), too slow (408) or broken (400) is still
  decided by the server, but answered by the app: before hooks, exception
  handlers, after hooks (CORS, request IDs) and the access log see it.
- `Forwarded` (RFC 7239) is read from trusted proxies when they send no
  `X-Forwarded-For` or `-Proto`. A `Forwarded` line with a quote left open
  is ignored.
- `forwarded_host=True` (`korvex run --forwarded-host`): the host a trusted
  proxy forwards (`X-Forwarded-Host`, or `Forwarded`'s `host`) replaces the
  `Host` header the app sees. Off by default, as in Django and Werkzeug:
  nginx passes a client's `X-Forwarded-Host` on as is.
- `X-Forwarded-Proto` believes its rightmost value, like `-Host`: the one
  the nearest proxy set.
- CI fails a pull request that makes Korvex more than 10% slower than its
  base branch (`benches/perf_check.py`).

## 0.8.0

- `Depends(func)`: a parameter gets the value `func` returns. Dependencies
  take parameters like handlers (query, headers, cookies, body, path, the
  request, other dependencies), can be sync or async, run once per request
  (`cache=False` otherwise), and can stop the request with
  `HTTPException`. One that `yield`s is torn down after the handler, or
  after the stream for a streamed response, with the handler's error
  raised at the `yield`.
- `dependencies=[Depends(...)]` on routes, groups and WebSockets.
- `app.dependency_overrides`, to replace a dependency in tests.
- `Header()`, `Cookie()`, `Form()` (files too) and `Query(alias=...)`, as a
  parameter's default or in `Annotated[...]`.
- WebSocket handlers get parameters and dependencies like HTTP handlers; a
  dependency raising `HTTPException` refuses the connection.
- OpenAPI describes headers, cookies, forms, and the parameters of
  dependencies.
- `Request.body()` and `form()` can be called more than once.
- Path parameters annotated with a type Korvex can't convert are refused
  at registration, like query parameters in 0.7.
- Fixed: a function serving two routes now runs each route's own
  dependencies, and same-named functions registered by a factory in
  several groups each run their own code (the last one registered ran for
  all of them).
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
  (SQLite, layers, settings and its own tests).

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
- Fixed: the nginx examples now pass WebSocket upgrades on; without that,
  WebSocket connections failed behind nginx.
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
- A Rust thread per worker reads each request in full, then hands it to
  the asyncio loop, which runs the handlers. Requests go through the same
  dispatch as under ASGI.
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
  handler arguments; other values stay strings.
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
  a 400), and `response.set_cookie()` / `response.delete_cookie()`. No new
  dependency.
- `Response.stream(chunks)`, `Response.file(path, filename=None)` and
  `app.static(prefix, directory)`. Static paths that leave the directory
  (`..`, symlinks) get a 404.
- `HEAD` requests are answered by the `GET` route without the body (they
  got a 405).
- `await request.json()` answers invalid JSON with a 400 instead of a 500.
- [Deployment guide](docs/deployment.md) with ready-to-copy files in
  `examples/deploy/`: uvicorn behind Caddy or nginx, Docker, AWS Lambda
  (Mangum) and Vercel.
- Load test: Falcon and BlackSheep added.
- New [ROADMAP.md](ROADMAP.md).

## 0.3.0

- `Router.match` caches the last 1,024 distinct lookups: 67 ns instead of
  347 ns for a repeated URL. Adding or removing a route clears the cache.
- Breaking: since results are now shared, they are immutable. `params` is
  a read-only mapping (it still compares equal to a dict and works with
  `**params`) and `middleware` is a tuple, in `match()` and `routes()`.
  `Request.path_params` is read-only too.
- New load test (`benches/load_test.py`): the same app in Korvex,
  Litestar, Starlette, Sanic, FastAPI and Django on one CPU core. Korvex
  serves the most requests per second with the least memory; see the
  README.

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

The benchmark now checks that every router finds the right route, adds
Litestar, and separates cached from uncached lookups (see the README).

## 0.1.1

- First release with tested wheels for every platform.
