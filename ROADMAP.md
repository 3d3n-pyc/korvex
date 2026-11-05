# Roadmap

Where Korvex is going. Versions are a rough order, not promises.

## Where it stands (0.5)

- **Fast.** On one CPU core, Korvex serves the most requests per second of
  the frameworks tested, with the least memory. Its built-in server serves
  more than uvicorn, Granian and Robyn on the same cores (see the README).
- **The basics of a web app.** A router, route groups, and an ASGI `App`
  with typed path parameters, startup and shutdown hooks, error handling,
  cookies and forms, streaming, files and static directories; a built-in
  server with workers, graceful shutdown, size limits and timeouts; and a
  deployment guide. Compared with FastAPI, Litestar or Django, still
  missing: WebSockets, validation, OpenAPI.
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
timeouts, and 0.6 adds rate limiting and request IDs. That also helps where there's no full proxy in front: local
development, or a platform whose load balancer doesn't do everything.

## 0.6: more of HTTP

- WebSockets (proxies forward them, but the app has to speak them).
- App-wide middleware and after-response hooks (headers, CORS).
- Middleware for jobs that need to know the app:
  - rate limiting per user or API key (a proxy only sees IP addresses);
  - request IDs (`X-Request-ID`), passed on and written to the logs.

## Later

- Built-in server, known limits to lift:
  - streamed responses have no backpressure: a slow client lets a stream
    pile up in memory;
  - the 413 and 408 it answers itself skip exception handlers and access
    logs;
  - `X-Forwarded-Host` and the standard `Forwarded` header aren't read.
- Free-threaded Python (3.14t), to let one worker use several cores.
- Optional middleware for security headers and response compression,
  for deployments without a proxy.
- OpenAPI generated from the registered routes.
- Optional request validation through msgspec or pydantic, never required.
- More route syntax: custom constraints, host-based routing.
- Benchmarks with several workers and with a real database.
- A performance check in CI that fails when a change makes Korvex slower.

## 1.0

- An API that stays stable, with deprecations announced in advance.
- A documentation site.
- A built-in server that's solid in production behind a reverse proxy.
- At least a few projects running it in production.
