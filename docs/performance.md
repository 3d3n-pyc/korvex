# Performance

All numbers come from the scripts in [`benches/`](https://github.com/3d3n-pyc/korvex/tree/main/benches), on one
machine. Results depend on the machine: measure your own workload.

## Servers

The same app on the same 4 CPU cores, each server with its best number of
workers. Measured with Korvex's main branch after 0.14.0 and FastrAPI 0.2.11:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="servers-dark.svg">
  <img alt="Requests per second on the same 4 CPU cores, with 200 and 1,000 clients: Korvex built-in server 142.7k and 145.4k, Granian + Korvex app 123.9k and 134.1k, uvicorn + Korvex app 117.5k and 110.4k, Robyn 95.7k and 91.7k, FastrAPI 61.4k and 67.1k." src="servers-light.svg">
</picture>

| Server | Requests/s, 200 clients | 1,000 clients | RAM |
|---|---|---|---|
| **Korvex built-in** (3 workers) | **142.7k** | **145.4k** | **125 MB** |
| Granian + Korvex app (3 workers) | 123.9k | 134.1k | 198 MB |
| uvicorn + Korvex app (4 workers) | 117.5k | 110.4k | 207 MB |
| Robyn (4 processes, 2 threads each) | 95.7k | 91.7k | 228 MB |
| FastrAPI (1 process) | 61.4k | 67.1k | 113 MB |

- Granian is a Rust ASGI server too, and comes second: the built-in
  server serves 8 to 15% more, because it skips the ASGI layer, with 40%
  less memory.
- Robyn and FastrAPI run their own version of the app on their own Rust
  server. FastrAPI runs one process (no workers option, and a second process
  can't share the port): its Rust side uses the 4 cores, but one Python
  process, under the GIL, runs every handler. Its handlers are sync: in
  0.2.11, async ones answer a 500.
- No request failed.
- Per-request Python work between the server and the handler was cut
  (handler plan checked once per route, headers no longer checked one by
  one, no lookup before each Content-Type, no ASGI scope built and read
  back). One worker on one core serves 39.7k requests/s, 47% more than
  0.14.0 (26.9k), measured side by side. On 4 cores the server's Rust
  threads and the kernel take a bigger share, so the gain is smaller.

Reproduce with `uv run --group bench python benches/servers.py` (needs `cargo install oha`,
takes about 5 minutes).

## Under load

The same small app (1,000 routes, one JSON endpoint) in each framework, on
uvicorn with one worker and one CPU core, flooded with requests to random
URLs by [oha](https://github.com/hatoo/oha). Measured with Korvex's main
branch after 0.14.0:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="load-dark.svg">
  <img alt="Requests per second on one CPU core with 200 clients: Korvex 34.4k, Falcon 30.5k, Starlette 3.0k to 30.3k, Litestar 17.7k, Sanic 14.1k, FastAPI 1.6k to 12.6k, BlackSheep 7.0k, Django 1.1k to 2.2k." src="load-light.svg">
</picture>

| Framework | Requests/s, 200 clients | p99 latency, 1,000 clients | RAM at rest, peak |
|---|---|---|---|
| **Korvex** | **34.4k** | **24 ms** | **39 MB, 50 MB** |
| Falcon | 30.5k | 31 ms | 60 MB, 63 MB |
| Starlette | 3.0k to 30.3k | 27 to 129 ms | 39 MB, 49 MB |
| Litestar | 17.7k | 89 ms | 79 MB, 90 MB |
| Sanic | 14.1k | 99 ms | 61 MB, 105 MB |
| FastAPI | 1.6k to 12.6k | 58 to 214 ms | 66 MB, 77 MB |
| BlackSheep | 7.0k | 68 ms | 51 MB, 59 MB |
| Django | 1.1k to 2.2k | 1.2 s to 1.7 s | 49 MB, 141 MB |

- Every framework uses a full CPU core, so requests per second is the work
  each gets out of that core.
- Falcon, and Starlette in its best case, serve 11 to 12% less than
  Korvex.
- Starlette, FastAPI and Django try routes one by one: the ranges go from
  the tested route declared after the 1,000 others to declared first.
  BlackSheep also tries them one by one, but sorts them deepest first, so
  the tested route always comes last.
- No request failed. Throughput holds as clients are added; latency grows
  because requests queue. After the flood, memory stays flat and speed is
  back to normal.

Robyn and FastrAPI can't run on uvicorn, so they're compared with Korvex's
built-in server above.

Reproduce with `uv run --group bench python benches/load_test.py` (needs
`cargo install oha`, takes about 11 minutes).

## Router alone

Route matching only, without any server:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="benchmark-dark.svg">
  <img alt="Time per route lookup at 10,000 routes. Without cache: Korvex 347 ns, Litestar 732 ns, Sanic 3.5 µs, Flask 3.5 µs, Django 1.3 ms, Starlette 1.5 ms. With each router's cache and repeated URLs: Korvex 67 ns, Litestar 92 ns, Sanic 211 ns." src="benchmark-light.svg">
</picture>

Median time per lookup, 10,000 routes:

| Router | First time a URL is seen | Same URL again (cached) |
|---|---|---|
| **Korvex** | **347 ns** | **67 ns** |
| Litestar | 732 ns | 92 ns |
| Sanic | 3.5 µs | 211 ns |
| Flask | 3.5 µs | (no cache) |
| Django | 1.3 ms | (no cache) |
| Starlette / FastAPI | 1.5 ms | (no cache) |

- Korvex is the fastest in both cases, and its time stays flat as routes
  are added. Django and Starlette try routes one by one, so they slow
  down linearly.
- Korvex, Litestar and Sanic cache the last 1,024 lookups. It helps most
  with static URLs; URLs with parameters (`/users/1`, `/users/2`, ...)
  differ often, so they hit it less.
- With few routes, the server and handler cost more than the router. With
  many, a router that tries routes one by one becomes the main cost (see
  Under load).

Reproduce with `uv run --group bench pytest benches/bench_router.py --benchmark-only
--benchmark-json=benches/results.json`, then `uv run --group bench python benches/plot.py`
to redraw both charts.

## Validation

One POST with a small JSON body, answered with JSON, run through the app
in-process (no server). Median time per request, Korvex's main branch after 0.14.0:

| Handler | Time |
|---|---|
| **Korvex, msgspec models in and out** | **16.6 µs** |
| Korvex, no annotations (`request.json()`, `Response.json()`) | 19.8 µs |
| Korvex, pydantic models in and out | 20.4 µs |
| FastAPI, the same pydantic models | 73.5 µs |

- With msgspec, validating is faster than not validating: it decodes and
  encodes JSON in compiled code, while `request.json()` and
  `Response.json()` use Python's `json` module. pydantic costs about the
  same as not validating.

Reproduce with `uv run --group bench pytest benches/bench_validation.py --benchmark-only`.

## Dependencies

One GET whose handler reads the user from a header and opens then closes a
session, run in-process. Median time per request, Korvex's main branch
after 0.14.0:

| Handler | Time |
|---|---|
| Korvex, the handler does it itself | 16.2 µs |
| **Korvex, two dependencies (one with `yield`) and `Header()`** | **23.7 µs** |
| FastAPI, the same dependencies, async | 93.8 µs |
| FastAPI, the same dependencies, sync | 405 µs |

- FastAPI runs sync dependencies in a thread pool: a blocking one can't
  stop the event loop, but it costs a lot. Korvex calls them on the loop,
  like sync handlers: keep blocking work out of them, or make them async.
- Dependencies are resolved when the route is registered; a handler
  without them pays nothing.

Reproduce with `uv run --group bench pytest benches/bench_dependencies.py --benchmark-only`.

## Threads on free-threaded Python

The built-in server on the same 4 cores, app and load (200 connections) as
above, with more threads or workers. One run:

| Python, workers, threads | Requests/s | p99 | Memory |
|---|---|---|---|
| 3.14t, 1 worker, 1 thread | 71,096 | 4.6 ms | 39 MiB |
| 3.14t, 1 worker, 2 threads | 98,839 | 3.8 ms | 43 MiB |
| **3.14t, 1 worker, 3 threads** | **111,317** | 4.6 ms | **46 MiB** |
| 3.14t, 1 worker, 4 threads | 103,378 | 6.0 ms | 49 MiB |
| 3.14t, 2 workers, 1 thread | 119,674 | 3.1 ms | 98 MiB |
| 3.14t, 4 workers, 1 thread | 116,183 | 4.1 ms | 159 MiB |
| 3.14 (GIL), 1 worker, 1 thread | 59,301 | 4.9 ms | 32 MiB |
| 3.14 (GIL), 1 worker, 2 threads | 58,653 | 2.5 ms | 33 MiB |
| 3.14 (GIL), 4 workers, 1 thread | 98,304 | 4.6 ms | 124 MiB |

- On 3.14t, 3 threads serve 57% more than one, within 4% of 4 workers,
  with a third of the memory. Past 3 there is no gain: each loop has its
  own Rust thread, so 4 loops already keep 8 threads busy on 4 cores.
- With the GIL, threads don't help: use workers.
- Workers still serve the most; threads are for when memory counts (one
  copy of the app, caches and connection pools per process).
- `compress()` leaves brotli out on 3.14t: the `brotli` package turns the
  GIL back on for the whole process. CI checks that nothing else does.

Reproduce with `KORVEX_FT_PYTHON=/path/to/3.14t/python uv run --group bench python
benches/threads.py` (see the file for the setup).

## With a database

Each request reads one row from PostgreSQL through an asyncpg pool and
answers with JSON. PostgreSQL gets 2 cores, the server 4, with 3 workers
each. Two runs:

| Server | Requests/s | p99 | Memory |
|---|---|---|---|
| Korvex, built-in server | 21,660 / 21,380 | 37.7 / 32.4 ms | 133 MiB |
| Korvex on uvicorn | 22,035 / 23,524 | 30.9 / 23.5 ms | 170 MiB |
| FastAPI on uvicorn | 13,407 / 15,356 | 41.7 / 41.6 ms | 229 MiB |

- The database sets the pace: Korvex's two servers serve about the same
  (uvicorn a little more in the second run), and the built-in server's
  lead from the tests above is gone. It still uses the least memory.
- FastAPI serves 35 to 40% less: its own cost per request adds to the
  database's.

Reproduce with `uv run --group bench python benches/database.py` (needs podman or
docker for PostgreSQL, and oha).

## Memory under steady load

`benches/soak.py` runs the built-in server with 2 workers for 10 minutes of
mixed load: keep-alive and new connections, JSON POST, streams, files,
WebSocket echo, slow clients and clients that hang up mid-stream. It samples
each worker's memory every 5 seconds. It fails if a worker grows over 10%
and 4 MiB after warm-up, on any error, or if a worker restarts. A nightly CI
run uses it. Over 10 minutes (900,000 HTTP requests, 3.8 million
WebSocket messages, 870,000 streams cut off), the workers grew +0.7% and
+0.1%, at 27 MiB each. With a deliberate leak (`SOAK_LEAK=1`), they grew
+150% and the test failed.

Run it with `uv run python benches/soak.py --duration 60` (Linux, needs `oha`).

## Templates

Rendering a template with autoescape on, precompiled and cached, against
Jinja2 and minijinja (a Rust engine). Median time per render, in µs, on one
machine:

| Template | Korvex | Jinja2 | minijinja |
|---|---|---|---|
| A table of 100 rows | **88.5** | 444 | 86 |
| A layout with `extends` and `include` | **9.7** | 59 | 11.7 |
| An escape-heavy form | **43** | 190 | 40 |

- Korvex is 4 to 6 times faster than Jinja2, and on par with minijinja.
- The outputs are checked equal to Jinja2's before timing.
- The time is spent in the template: the app, the server and the request
  are not part of it.

Reproduce with `uv run --group bench python benches/bench_templates.py`.
