# Roadmap

Where Korvex is going. Versions are a rough order, not promises.

## Where it stands (0.12)

- **Fast.** On one CPU core, Korvex serves the most requests per second of
  the frameworks tested, with the least memory. Its built-in server serves
  more than uvicorn, Granian and Robyn on the same cores (see the README).
- **The basics of a web app.** A router, route groups, and an ASGI `App`
  with typed path parameters, startup and shutdown hooks, error handling,
  cookies and forms, streaming, files and static directories, WebSockets,
  app-wide hooks, CORS, request IDs, rate limiting (shared through Redis
  if needed), compression and security headers; validation from
  type annotations with msgspec or pydantic, headers, cookies and forms
  (models too) as parameters, and dependencies, which guard routes,
  groups and WebSockets; custom route constraints and host-based
  routing; an OpenAPI document and Swagger UI; a built-in server with
  workers, threads (in parallel on free-threaded Python), graceful
  shutdown, size limits, timeouts, backpressure and WebSocket pings; and a
  deployment guide.
- **Young.** Alpha, one maintainer, no known production use yet.

Two rules hold for everything below:

1. The router stays opaque and independent: names in, names out. Every
   new feature is a layer on top that can be skipped.
2. No performance claim without a benchmark in `benches/` that checks it
   measures the right thing.

## Behind a reverse proxy

Korvex is meant to run behind a reverse proxy: nginx, Caddy, Traefik or a
cloud load balancer. They already do these jobs well:

- HTTPS and certificates (Caddy even gets them automatically)
- HTTP/2 and HTTP/3 towards clients
- protection against slow clients, size limits, connection caps
- compression and access logs

The proxy then forwards each request to Korvex in plain HTTP/1.1. The
router never sees any of this anyway: it only gets a method and a path.

Korvex leaves HTTPS, certificates, HTTP/2 and HTTP/3 entirely to the
proxy. For the rest, it does a light version when that costs almost
nothing or when only the app knows enough to do it well: the built-in
server reads requests in full in Rust, limits body sizes and enforces
timeouts; the app adds rate limiting per user or API key and request
IDs. That also helps where there's no full proxy in front: local
development, or a platform whose load balancer doesn't do everything.

## Until 1.0

The API's open questions (0.10), ready-made middleware (0.11) and more
than one core per worker (0.12) are done. Compared with FastAPI,
Starlette and Litestar, three things are still missing that everyone
expects and that shape the API, so they come before it's frozen. Until
1.0, a version may still break things, each break listed in the
changelog with how to update: no one runs Korvex in production yet, and
an API changes for free now, not after.

### 0.13: what everyone expects

- **A test client**: `TestClient(app)` with `get()`, `post()`... and
  WebSockets, running the app in-process with its lifespan and cookies,
  like Starlette's and Litestar's. Today tests drive raw ASGI messages
  (the taskboard example wrote its own client).
- **Reverse URLs**: `app.url_for(name, **params)` (and on the request),
  to build a link to a route without writing its path again. Routes
  already have names.
- **Richer OpenAPI**, whose options go on the route decorators: a status
  code per route, `tags`, `deprecated`, documented error responses, and
  security schemes (Bearer, API key, OAuth2) so Swagger UI can sign in.

Then **a review of the whole public API** (names, arguments, what's
exported, errors), the last chance to change it freely.

## 1.0

- An API that stays stable, with deprecations announced in advance: a
  deprecated feature warns for at least two minor versions before it goes.
- A documentation site, built from the README, the deployment guide and
  the examples.
- A built-in server that's solid in production behind a reverse proxy.
- The comparisons with other frameworks and servers measured again
  (`benches/servers.py`, `benches/load_test.py`): the published ones date
  from 0.6, and everyone's versions have moved since.
- At least a few projects running it in production.

## After 1.0

What the other frameworks have that Korvex doesn't yet, roughly by how
often it's needed. Each one adds to the API without changing it, so it
can come in a minor version:

- **Background tasks**: work that runs after the response is sent (an
  email, a webhook), without holding it up.
- **Sessions and CSRF protection**, ready-made, as Starlette and Litestar
  have them.
- **Server-Sent Events**: a helper on top of `Response.stream`.
- **Mounting another ASGI app** under a prefix (an admin, an existing
  app), like Starlette's `Mount`.
- **Templates** (Jinja2), for server-rendered pages.
- **Conditional static files**: `ETag`, `If-Modified-Since` and `Range`,
  for caching and resumable downloads.
- **More kinds of models** for validation: dataclasses, `TypedDict`,
  attrs, as Litestar takes them.
