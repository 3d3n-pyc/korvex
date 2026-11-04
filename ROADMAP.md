# Roadmap

Where Korvex is going. Versions are a rough order, not promises.

## Where it stands (0.3)

- **Fast routing.** On one CPU core, Korvex serves the most requests per
  second of the frameworks tested, with the least memory (see the README).
- **Small feature set.** A router, route groups, and a minimal ASGI `App`.
  Compared with FastAPI, Litestar or Django, a lot is missing: WebSockets,
  streaming, static files, startup hooks, validation, OpenAPI.
- **Young.** Alpha, one maintainer, no known production use yet.

Two rules hold for everything below:

1. The router stays opaque and independent: names in, names out. Every
   new feature is a layer on top that can be skipped.
2. No performance claim without a benchmark in `benches/` that checks it
   measures the right thing.

## 0.4: what a real app needs first

- **Typed path parameters.** `{id:int}` gives the handler an `int`, not a
  string that only looks like one.
- **Startup and shutdown hooks** (`@app.on_startup`), for database pools
  and the like.
- **Error handling.** Custom 404/405 responses and exception handlers,
  instead of a bare 500.
- **Responses:** streaming, files, and static directories.
- **Request:** cookies and form data.
- **`app.group()`**, so groups and decorators work together without going
  through `app.router` and `@app.handler`.

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
nothing or when only the app knows enough to do it well (see 0.5 and
0.6). That also helps where there's no full proxy in front: local
development, or a platform whose load balancer doesn't do everything.

## 0.5: a built-in server

Today `App` needs an ASGI server such as uvicorn. The goal is a server
inside Korvex, written in Rust like the router, as Robyn does:

```python
app.run(port=8000, workers=4)   # or: korvex run mymodule:app
```

- **HTTP/1.1** with keep-alive, built on hyper and tokio: what the proxy
  sends.
- **Requests go straight from Rust to the handlers**, without the ASGI layer
  in between, where most of the time per request goes today.
- **Workers:** several processes, restarted if one crashes; graceful
  shutdown on SIGTERM (finish in-flight requests, then stop).
- **Talking to the proxy:** listen on a port or a Unix socket; read the
  client's real address from `Forwarded` / `X-Forwarded-*`, trusted only
  from configured proxies.
- **Protecting the Python workers**, which are the scarce resource:
  - Rust reads each request in full before handing it to Python, so a
    slow client never holds a worker;
  - timeouts and limits on header and body size, with safe defaults and
    per-route overrides (an upload route needs more than a JSON one).
- **Access logs**, off by default, for platforms where the load
  balancer's logs aren't available.
- `App` stays a plain ASGI app: uvicorn, Granian or Hypercorn keep working.
- Then compare it with Robyn and with Granian-served apps, which also run
  their own server. Today that comparison would be unfair, so Robyn is
  left out of the load test.

Open questions: how many requests one Python thread can take from Rust;
whether free-threaded Python (3.14t) lets one process use several cores.

## 0.6: more of HTTP

- WebSockets (proxies forward them, but the app has to speak them).
- App-wide middleware and after-response hooks (headers, CORS).
- Middleware for jobs that need to know the app:
  - rate limiting per user or API key (a proxy only sees IP addresses);
  - request IDs (`X-Request-ID`), passed on and written to the logs.

## Later

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
