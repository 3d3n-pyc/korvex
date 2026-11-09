# Contributing

Everyone taking part follows the [code of conduct](CODE_OF_CONDUCT.md).
Report a vulnerability privately, as [SECURITY.md](SECURITY.md) explains.

## Setup

You need [uv](https://docs.astral.sh/uv/) and a Rust toolchain
([rustup](https://rustup.rs/)).

```sh
git clone https://github.com/3d3n-pyc/korvex
cd korvex
uv sync                                # Python, the tools, and Korvex built from source
uv run maturin develop --release       # after changing Rust code
uv run pre-commit install              # optional: lint and format before each commit
```

`uv sync --group bench` adds the frameworks and servers the benchmarks
compare.

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
korvex/       public: korvex, korvex.router, .testing, .security, .middleware
  __init__.py   what `import korvex` gives
  router.py     the standalone Router (wraps the Rust one) and RouteGroup
  testing.py    TestClient and AsyncTestClient: the app in-process, for tests
  security.py   security schemes: Bearer(), APIKey(), OAuth2Password()
  middleware/   ready-made hooks, one module each: cors(), request_id(),
                rate_limit(), compress(), security_headers()
  _app.py       App: dispatch, hooks, errors, lifespan, App.run()
  _requests.py  Request, WebSocket and UploadedFile (multipart forms)
  _routing.py   registering routes: the get/post/... decorators, groups, hosts
  _response.py  Response and Headers
  _errors.py    HTTPException, ValidationError and Abort
  _params.py    Header(), Cookie(), Query(), Form() and Depends()
  _schema.py    validation from a handler's annotations (msgspec or pydantic),
                and resolving dependencies
  _openapi.py   the OpenAPI document and the Swagger UI page
  _urls.py      reverse URLs: url_for()
  _serve.py     the built-in server's Python side: workers, threads, signals
  _cli.py       the `korvex run` command
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

Modules starting with `_` are internal: what they hold can change in any
version. The public API is what the five public modules export, and what
the docs show.

Only `lib.rs`, `server.rs` and `websocket.rs` use PyO3; keep `tree`, `path`, `method` and
`proxy` plain Rust.

## Before opening a PR

```sh
cargo fmt && cargo clippy --all-targets -- -D warnings && cargo test
uv run ruff check . && uv run ruff format .
uv run pyright
uv run pytest
```

CI runs the same checks, the tests on Python 3.9 to 3.14 and on
free-threaded 3.14t, and a performance check against the base branch
(more than 10% slower on any case fails). New behavior needs a test;
`korvex.testing.TestClient` drives the app, and `tests/support.py` has
helpers for the tests that need raw ASGI messages.

Coverage, the built-in server's subprocesses included:

```sh
uv run coverage run -m pytest && uv run coverage combine && uv run coverage report
```

The docs are Markdown in `docs/`, published at
<https://3d3n-pyc.github.io/korvex/>; the reference pages are built from
the docstrings. To see your changes as the site shows them:

```sh
uv run --group docs mkdocs serve      # http://127.0.0.1:8000, reloads on save
```

CI builds them with `--strict`: a broken link fails the PR.

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
