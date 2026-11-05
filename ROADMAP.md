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

What was planned before 1.0 is done: the API's open questions (0.10),
ready-made middleware (0.11), and more than one core per worker (0.12).
What's left is what 1.0 itself asks for, below. Until then, a version may
still break things, each break listed in the changelog with how to
update: no one runs Korvex in production yet, and an API changes for
free now, not after.

## 1.0

- An API that stays stable, with deprecations announced in advance: a
  deprecated feature warns for at least two minor versions before it goes.
- A documentation site, built from the README, the deployment guide and
  the examples.
- A built-in server that's solid in production behind a reverse proxy.
- At least a few projects running it in production.
