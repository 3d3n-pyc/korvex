# Working on Korvex

Instructions for AI coding agents working on this repository. To build an
app *with* Korvex instead, read `.agents/skills/korvex/SKILL.md`.

Korvex is a Python web framework (ASGI) with a Rust router and HTTP server,
built with PyO3 and maturin. [CONTRIBUTING.md](CONTRIBUTING.md) has the
layout of the code, file by file: read it before changing anything.

## Setup and checks

```sh
uv sync                              # Python, the tools, and Korvex built from source
uv run maturin develop --release     # after changing Rust code, before running Python
```

Before saying a change is done, run what CI runs:

```sh
cargo fmt && cargo clippy --all-targets -- -D warnings && cargo test
uv run ruff check . && uv run ruff format .
uv run pyright
uv run pytest
uv run --group docs mkdocs build --strict -d target/site   # if docs or docstrings changed
```

## Rules

- **Python 3.9 to 3.14, and free-threaded 3.14t.** Write `X | Y` only in
  annotations, with `from __future__ import annotations` at the top of
  the module; `Union[X, Y]` and `Optional[X]` elsewhere. CI also runs
  the tests on 3.14t with `-W error::RuntimeWarning`.
- **msgspec and pydantic are optional.** Import them only where one of
  their models is being used, never at the top of a module.
- **The public API** is what `korvex`, `korvex.router`, `korvex.testing`,
  `korvex.security` and `korvex.middleware` export. Modules starting with
  `_` are internal.
- **Before 1.0, break the API directly**: no deprecation shims. Every
  change goes in `CHANGELOG.md` under `## Unreleased`, with how to update.
- **No performance loss.** CI fails a pull request more than 10% slower
  than its base, in-process (`benches/perf_check.py`) and through the
  built-in server (`benches/server_check.py`). The request path in
  `_app.py`, `_requests.py`, `_response.py` and `_serve.py` is hot: no
  extra allocation, lookup or call per request without measuring it.
- **Rust**: only `lib.rs`, `server.rs` and `websocket.rs` use PyO3;
  `tree.rs`, `path.rs`, `method.rs` and `proxy.rs` stay plain Rust.
- **New behavior needs a test.** `korvex.testing.TestClient` drives the
  app; `tests/support.py` helps with raw ASGI messages.
- **Docstrings are documentation**: the reference pages of the site are
  built from them. Write them for users, in plain English, and keep them
  true.
- **Keep the docs in step**: the guide in `docs/`, the README when a
  headline changes, and `.agents/skills/korvex/SKILL.md` when something
  it shows changes (`tests/test_agent_skill.py` runs its examples).
- Code, comments, docs and commit messages are in English. Commits
  follow Conventional Commits (`feat:`, `fix:`, `docs:`, `perf:`...).
