# The built-in server

Korvex runs an app without an ASGI server, on Linux and macOS:

```sh
korvex run main:app --workers 3
```

or, from Python, `app.run(workers=3)`. On the same CPU cores, it serves 21 to
32% more requests than uvicorn, with 40% less memory (see
[Performance](performance.md)).

The app stays a plain ASGI app: uvicorn, Granian or a serverless platform
run it too, and Windows needs one of them.

## Options

The same in `korvex run` and `app.run()`. In Python, write them with
underscores, and give lists as lists:

```python
app.run("0.0.0.0", 8000, workers=3, graceful_timeout=10, forwarded_allow_ips=["10.0.0.5"])
```

| Option | Default | Does |
|---|---|---|
| `--host`, `--port` | `127.0.0.1`, `8000` | Address to listen on. |
| `--uds PATH` | | Listen on a Unix socket instead, for a proxy on the same machine. |
| `--workers N` | `1` | Processes sharing the port; about 3 per 4 CPU cores works best. A worker that crashes is restarted. |
| `--threads N` | `1` | Event loops per worker, each in its own thread: see below. |
| `--graceful-timeout S` | `30` | On Ctrl+C or SIGTERM, seconds requests in progress get to finish. |
| `--max-body BYTES` | 1 MiB | Larger bodies get a 413, larger WebSocket messages close the connection. A route can set its own: `@app.post("/upload", max_body=50_000_000)`. |
| `--read-timeout S` | `30` | Seconds a client gets to send the headers, then the body (a 408 after). |
| `--forwarded-allow-ips` | `127.0.0.1,::1` | Proxies whose `X-Forwarded-For` and `-Proto` (or `Forwarded`) give the real client and scheme. `*` trusts any. |
| `--forwarded-host` | off | Also take the host they forward. Only if your proxy sets that header itself: nginx passes a client's `X-Forwarded-Host` on as it is. |
| `--access-log` | off | Log each request. |
| `--ws-ping-interval S` | `20` | Seconds between pings on open WebSockets; a client silent until the next ping is closed. `0` turns them off. |

## Threads

With `--threads 3`, each worker runs 3 event loops, in 3 threads. On
free-threaded Python (3.14t) they run in parallel: one process uses
several cores and shares its memory. With the GIL, they take turns on one
core, so use workers instead.

Each loop runs the startup and shutdown hooks. Keep what they open in
`app.local`, which holds one value per loop: a module global would give
every loop the first loop's connection pool, and asyncio objects only
work on the loop that made them.

## How it works

A Rust thread per loop reads each request in full, without touching
Python, then hands it to the asyncio loop that runs your handlers. A slow
or huge upload never holds Python up.

## Good to know

- In production, run it behind a reverse proxy (nginx, Caddy, Traefik):
  it speaks HTTP/1.1 and WebSocket, and leaves HTTPS, HTTP/2 and HTTP/3 to
  the proxy. The [deployment guide](deployment.md) has ready configurations.
- A body that's too big (413), too slow (408) or broken (400) is still
  answered by the app, so before hooks, exception handlers, after hooks
  and the access log see it.
- A slow client can't make a stream or a WebSocket pile up in memory: once
  1 MiB waits for it, the handler's next send waits.
- With several workers, a worker that crashes after starting is restarted
  after a growing delay (0.1 s, doubling up to 30 s). On SIGTERM or SIGINT
  the workers get `graceful_timeout` to finish, and are killed 5 s later if
  still there; a second signal kills them at once.
- From a proxy's forwarded headers, the rightmost value counts: the one
  the nearest proxy set, not one the client could have written.
