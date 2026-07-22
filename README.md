# Korvex

[![CI](https://github.com/3d3n-pyc/korvex/actions/workflows/ci.yml/badge.svg)](https://github.com/3d3n-pyc/korvex/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A high-performance router for Python, backed by a Rust radix tree — with an
optional, equally lightweight ASGI layer for actually hosting it.

Korvex is deliberately small: the core is a router and nothing else. It
matches a path to an opaque handler name; it never calls your code itself.
Everything above that (an ASGI `App`, `Response`/`Headers` helpers, route
groups) is a thin, optional layer built on top — you can use as much or as
little of it as you want.

## Why

- **Fast routing.** A radix tree in Rust, exposed through PyO3, with an
  allocation-free matching path (no `Vec` of segments, no `String` clones for
  captured params). Benchmarked at 10×–1000× faster route lookups than
  Flask/Django/Starlette's own routers (see [`benches/`](benches/)).
- **Small surface.** `Router` deals only in opaque strings — a path pattern
  in, a handler name + params out. It doesn't know what a `Response` is, and
  never will; that keeps the matching engine simple and independently
  testable (`src/router/engine/`, zero `PyO3` dependency).
- **Host it or don't.** `korvex.App` is a real ASGI application (works with
  uvicorn, Granian, hypercorn, ...) if you want to actually serve requests —
  but it's a couple hundred lines of pure Python on top of the router
  (`korvex/app.py`), not a requirement.

## Install

```sh
pip install korvex
# or, to also pull in a server for the ASGI examples:
pip install "korvex[serve]"
```

Building from source requires [maturin](https://www.maturin.rs/) and a Rust
toolchain — see [CONTRIBUTING.md](CONTRIBUTING.md).

## Quickstart

```python
from korvex import Router

router = Router()
router.get("/users/{id:int}", "get_user")
router.post("/users", "create_user")

router.match_route("/users/42")
# -> ("get_user", {"id": "42"}, [])
```

`Router` never calls your code — `match_route` just tells you which opaque
handler name matched. Dispatching that to real code is up to you, or use
`App` to have Korvex do it:

```python
from korvex import App, Response

app = App()

@app.get("/users/{id:int}")
def get_user(request, id):
    return Response.json({"id": id})

# uvicorn mymodule:app
```

See [`examples/`](examples/) for the router alone, route groups with
middleware, and the full ASGI app — each one runnable as-is.

## Features

- Static, dynamic (`{id}`), constrained (`{id:int}`, `{id:uuid}`), and
  wildcard (`*filepath`) path segments
- Per-route HTTP methods, with `405 Method Not Allowed` (not a bare 404)
  when a path matches but the method doesn't
- Route groups with shared prefixes and middleware (`router.group(...)`)
- Route introspection (`router.routes()`) and removal (`router.remove_route(...)`)
- An optional ASGI `App`: decorator-based handler/middleware registration,
  sync or async, zero extra dependencies
- Fully typed (`korvex/py.typed`, `.pyi` stubs for the compiled `Router`)

## Performance

Routing lookups beat Flask/Django/Starlette by 10×–1000× (`benches/bench_router.py`).
Once wired into a full ASGI app, the framework-level gap narrows — most of
the per-request cost becomes Python-level handler/response work, common to
any framework — but the server you host it with matters far more than the
framework: swapping uvicorn for a Rust-based ASGI server (e.g.
[Granian](https://github.com/emmett-framework/granian)) measured roughly
5× the throughput in this project's own testing, with zero code changes,
since `App` speaks plain ASGI. See `benches/bench_app.py` for the
methodology; treat any specific numbers as a starting point for your own
measurements, not a guarantee — they depend heavily on hardware, Python
version, and workload.

## Development

```sh
git clone https://github.com/3d3n-pyc/korvex
cd korvex
uv run maturin develop --release
uv run pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full checklist.

## License

[MIT](LICENSE)
