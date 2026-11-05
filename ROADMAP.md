# Roadmap

Where Korvex is going. Versions are a rough order, not promises.

## Where it stands (0.9)

- **Fast.** On one CPU core, Korvex serves the most requests per second of
  the frameworks tested, with the least memory. Its built-in server serves
  more than uvicorn, Granian and Robyn on the same cores (see the README).
- **The basics of a web app.** A router, route groups, and an ASGI `App`
  with typed path parameters, startup and shutdown hooks, error handling,
  cookies and forms, streaming, files and static directories, WebSockets,
  app-wide hooks, CORS, request IDs and rate limiting; validation from
  type annotations with msgspec or pydantic, headers, cookies and forms
  as parameters, and dependencies; an OpenAPI document and Swagger UI; a
  built-in server with workers, graceful shutdown, size limits, timeouts,
  backpressure and WebSocket pings; and a deployment guide.
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

Everything left is done before 1.0, while no one runs Korvex in
production: an API can still change for free now, and won't after. Until
then, a version may break things; each break is listed in the changelog
with how to update.

The versions go from what shapes the API to what only adds to it.

### 0.10: the API's last open questions

- **Dependencies in middleware.** Route middleware (`@app.middleware`,
  `middleware=[...]`) and route dependencies (`dependencies=[...]`) now do
  the same job. Settle it: one way to guard a group of routes, the other
  kept for what only it can do, or deprecated.
- **App hooks see the WebSocket handshake**, as dependencies already do.
- **Custom route constraints**, beyond `int` and `uuid` (`{slug:slug}`, a
  regex, a function), without making the router any less opaque: the
  router still only gets a method and a path, and gives back a name.
- **Host-based routing**: routes for `api.example.com` apart from
  `example.com`, as a layer on top of the router.
- **A model filled from form fields**, like a JSON body.

### 0.11: ready-made middleware

- **Response compression** (gzip, and brotli or zstd when installed), for
  deployments without a proxy to do it.
- **Security headers**: `Strict-Transport-Security`, `Content-Security-Policy`,
  `X-Content-Type-Options` and the like, with safe defaults.
- **A shared rate limit store on Redis**, so a limit holds across workers
  and machines (`redis` as an optional dependency).

### 0.12: more than one core per worker

- **Free-threaded Python (3.14t)**: wheels built for it (the `abi3` wheels
  don't cover it), and the Rust and Python sides checked for real
  parallel threads.
- **Benchmarks with several workers and with a real database**
  (PostgreSQL), in `benches/` like the others.

## 1.0

- An API that stays stable, with deprecations announced in advance: a
  deprecated feature warns for at least two minor versions before it goes.
- A documentation site, built from the README, the deployment guide and
  the examples.
- A built-in server that's solid in production behind a reverse proxy.
- At least a few projects running it in production.
