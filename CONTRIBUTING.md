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
korvex/
  router.py   Router (wraps the Rust one) and RouteGroup
  app.py      App and Request (ASGI)
  response.py Response and Headers
  serve.py    App.run(): one process, or a supervisor and its workers
  cli.py      the `korvex run` command
tests/        Python tests (Rust tests live next to the code)
benches/      comparisons with other frameworks:
  bench_router.py  route matching alone
  load_test.py     frameworks on the same server under load (apps in benches/load/)
  servers.py       servers on the same CPU cores, built-in one included
  plot.py          draws the charts in docs/
```

Only `lib.rs` and `server.rs` use PyO3; keep `tree`, `path`, `method` and
`proxy` plain Rust.

## Before opening a PR

```sh
cargo fmt
cargo clippy --all-targets -- -D warnings
cargo test
uv run pytest
```

CI runs the same checks. New behavior needs a test.

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
