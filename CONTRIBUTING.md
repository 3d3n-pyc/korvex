# Contributing

## Setup

```sh
git clone https://github.com/3d3n-pyc/korvex
cd korvex
uv run maturin develop --release
```

Rebuild with the same command after changing Rust code.

## Layout

```
src/
  lib.rs      Python bindings (the korvex._core module)
  tree.rs     the radix tree: insert, match, remove, list routes
  path.rs     path splitting and pattern syntax ({id:int}, *path)
  method.rs   HTTP methods
  server.rs   the built-in server (Unix only): HTTP in a Rust thread, handed to asyncio
  proxy.rs    trusted X-Forwarded-* headers
  websocket.rs WebSockets in the built-in server
korvex/
  router.py   Router (wraps the Rust one) and RouteGroup
  app.py      App and Request (ASGI)
  response.py Response and Headers
  errors.py   HTTPException and ValidationError
  params.py   Header(), Cookie(), Query(), Form() and Depends()
  schema.py   validation from a handler's annotations (msgspec or pydantic),
              and resolving dependencies
  openapi.py  the OpenAPI document and the Swagger UI page
  middleware.py ready-made hooks: cors(), request_id(), rate_limit()
  serve.py    App.run(): one process, or a supervisor and its workers
  cli.py      the `korvex run` command
tests/        Python tests (Rust tests live next to the code)
benches/      comparisons with other frameworks:
  bench_router.py  route matching alone
  bench_app.py     the App's own cost per request, against Starlette
  bench_validation.py  validation from annotations, against FastAPI
  bench_dependencies.py  dependencies, against FastAPI
  perf_check.py    the check CI runs on pull requests: slower than the base?
  threads.py       the built-in server with more threads or workers, 3.14t and GIL
  database.py      a request that reads PostgreSQL, Korvex and FastAPI (apps in benches/db/)
  load_test.py     frameworks on the same server under load (apps in benches/load/)
  servers.py       servers on the same CPU cores, built-in one included
  plot.py          draws the charts in docs/
```

Only `lib.rs`, `server.rs` and `websocket.rs` use PyO3; keep `tree`, `path`, `method` and
`proxy` plain Rust.

## Before opening a PR

```sh
cargo fmt
cargo clippy --all-targets -- -D warnings
cargo test
uv run pytest
```

CI runs the same checks. New behavior needs a test.

msgspec and pydantic stay optional: import them only where a model of
theirs is being used, never at the top of a module.

Python code must run on 3.9: write `X | Y` only in annotations (thanks to
`from __future__ import annotations`), and `Union[X, Y]` elsewhere.

Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/)
(`feat:`, `fix:`, `docs:`, ...).

## Releasing

Push a tag:

```sh
git tag v0.2.0
git push origin v0.2.0
```

The release workflow sets the version from the tag (the version in
`Cargo.toml` is a placeholder), builds and tests wheels for Linux, macOS
and Windows, publishes them to PyPI and creates a GitHub release.
Running the workflow by hand from the Actions tab builds and tests
without publishing.

Publishing relies on two settings made once: a `pypi` environment in the
GitHub repo, and a [trusted publisher](https://docs.pypi.org/trusted-publishers/)
on PyPI pointing at `release.yml` and that environment.
