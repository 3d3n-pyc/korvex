# Roadmap

Where Korvex is going. Versions are a rough order, not promises.

## Where it stands (1.0.0rc1)

- **Fast.** On one CPU core, Korvex serves the most requests of the
  frameworks tested (34.4k requests/s, Falcon 30.5k, Starlette's best
  case 30.3k). Its built-in server serves more than uvicorn, Granian,
  Robyn and FastrAPI on the same cores, with the least memory (see
  [Performance](docs/performance.md)).
- **The basics of a web app.** A router, route groups, and an ASGI `App`
  with typed path parameters, startup and shutdown hooks, error handling,
  cookies and forms, streaming, files and static directories, WebSockets,
  app-wide hooks, CORS, request IDs, rate limiting (shared through Redis
  if needed), compression and security headers; validation from type
  annotations with msgspec or pydantic, with headers, cookies and forms
  (models too) as parameters; dependencies, which guard routes, groups
  and WebSockets; custom route constraints and host-based routing;
  reverse URLs (`url_for`); an OpenAPI document with statuses, tags,
  documented errors and security schemes, and Swagger UI; a test client
  that runs the app in-process; and a deployment guide.
- **Pages, not just APIs.** Its own template engine (4 to 6 times faster
  than Jinja2), static files with hashed URLs, signed cookie sessions,
  flash messages and CSRF protection; apps served under a path prefix
  (ASGI `root_path`).
- **A built-in server.** Workers, threads (in parallel on free-threaded
  Python), a supervisor, graceful shutdown, size limits, timeouts,
  `max-connections`, `max-requests`, backpressure on requests and on
  WebSockets, and WebSocket pings.
- **Young.** A release candidate: the API is frozen, but there is one
  maintainer and no known production use yet.

Two rules hold for everything below:

1. The router stays opaque and independent: names in, names out. Every
   new feature is a layer on top that can be skipped.
2. No performance claim without a benchmark in `benches/` that checks it
   measures the right thing.

## Behind a reverse proxy

Korvex is meant to run behind a reverse proxy: nginx, Caddy, Traefik or a
cloud load balancer, which already do these jobs well:

- HTTPS and certificates (Caddy even gets them automatically)
- HTTP/2 and HTTP/3 towards clients
- protection against slow clients, size limits, connection caps
- compression and access logs

The proxy forwards each request to Korvex in plain HTTP/1.1. The router
never sees any of this: it only gets a method and a path.

Korvex leaves HTTPS, certificates, HTTP/2 and HTTP/3 to the proxy. For
the rest, it does a light version when that costs almost nothing or only
the app knows enough to do it well: the built-in server reads requests in
full in Rust, limits body sizes and enforces timeouts; the app adds rate
limiting per user or API key, and request IDs. That also helps without a
full proxy in front: local development, or a platform whose load balancer
does less.

## Until 1.0

Done: the API's open questions (0.10), ready-made middleware (0.11), more
than one core per worker (0.12), what everyone expects from a framework
(0.13: a test client, reverse URLs, a richer OpenAPI), a review of the
whole public API (0.14), a documentation site (0.14.2), a more robust
built-in server (0.15), a soak test in CI (0.15.1), server-side
templates with hashed static URLs (0.16), sessions with CSRF
protection (0.17), and an ASGI `root_path` with the last breaking API
changes (1.0.0rc1). The public API is what `korvex`, `korvex.router`,
`korvex.testing`, `korvex.security` and `korvex.middleware` export. Until
1.0 it can change, each change listed in the changelog with how to
update; after, it stays. Next: maybe rc5, then 1.0.0.

## Before 1.0.0

Korvex stays in release candidates until the official launch, 1.0.0. The
features planned for after 1.0 come first, in release candidates, so each
one is tested before the launch. The rule during the rc phase: what rc1
froze doesn't break; a feature added in an rc can still change until 1.0.0.

- **1.0.0rc3, background tasks**: work that runs after the response is sent
  (an email, a webhook), without holding it up.
- **1.0.0rc3, Server-Sent Events**: a helper on top of `Response.stream`.
- **1.0.0rc4, mounting another ASGI app** under a prefix (an admin, an
  existing app), like Starlette's `Mount`. Done.
- **1.0.0rc4, conditional static files**: `ETag`, `If-Modified-Since` and
  `Range`, for caching and resumable downloads. Done.
- **Maybe 1.0.0rc5, more kinds of models** for validation: dataclasses,
  `TypedDict`, as Litestar takes them.

1.0.0 comes when all of these hold:

- The features above have shipped.
- A week of nightly fuzzing and soak runs has passed without a failure.
- Korvex runs a real app that we deploy and keep running ourselves, for
  example the taskboard example behind nginx or Caddy. This replaces "a few
  projects running it in production", which can't happen before 1.0.

## After 1.0

From 1.0.0, no breaking change without a 2.0. Deprecations are announced in
advance: a deprecated feature warns for at least two minor versions before
it goes.
