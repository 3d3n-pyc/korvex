# Roadmap

Where Korvex is going. Versions are a rough order, not promises.

## Where it stands (0.14)

- **Fast.** On one CPU core, Korvex is among the fastest of the frameworks
  tested (31.9k requests/s, Falcon 31.5k, Starlette's best case 31.1k).
  Its built-in server serves more than uvicorn, Granian, Robyn and
  FastrAPI on the same cores, with the least memory (see
  [Performance](docs/performance.md)).
- **The basics of a web app.** A router, route groups, and an ASGI `App`
  with typed path parameters, startup and shutdown hooks, error handling,
  cookies and forms, streaming, files and static directories, WebSockets,
  app-wide hooks, CORS, request IDs, rate limiting (shared through Redis
  if needed), compression and security headers; validation from
  type annotations with msgspec or pydantic, headers, cookies and forms
  (models too) as parameters, and dependencies, which guard routes,
  groups and WebSockets; custom route constraints and host-based
  routing; reverse URLs (`url_for`); an OpenAPI document with statuses,
  tags, documented errors and security schemes, and Swagger UI; a test
  client that runs the app in-process; a built-in server with
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

The API's open questions (0.10), ready-made middleware (0.11), more
than one core per worker (0.12), what everyone expects from a framework
(0.13: a test client, reverse URLs, a richer OpenAPI) and a review of the
whole public API (0.14) are done. The public API is now what `korvex`,
`korvex.router`, `korvex.testing`, `korvex.security` and
`korvex.middleware` export. Until 1.0 it can still change, each change
listed in the changelog with how to update; after, it stays.

## 1.0

- An API that stays stable, with deprecations announced in advance: a
  deprecated feature warns for at least two minor versions before it goes.
- A documentation site, built from `docs/` and the examples.
- A built-in server that's solid in production behind a reverse proxy.
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
