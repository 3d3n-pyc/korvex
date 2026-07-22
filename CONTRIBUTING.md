# Contributing to Korvex

Thanks for considering a contribution! Here's how to get set up.

## Setup

```bash
git clone https://github.com/3d3n-pyc/korvex
cd korvex
uv run maturin develop --release
```

## Before opening a PR

```bash
cargo fmt
cargo clippy --all-targets -- -D warnings
cargo test
uv run pytest
```

All four must pass — CI enforces the same checks.

## Code style

- Every public Rust item needs a `///` doc comment explaining *why*,
  not just *what*.
- Prefer `Option`/`Result` over panics; this is a library, callers
  should never see an unexpected panic from routing logic.
- New behavior needs a test in the same file (`#[cfg(test)] mod tests`
  for Rust, `tests/test_*.py` for Python) before a PR is reviewed.
- Keep `src/router/engine/` free of any `PyO3` dependency — it's the
  part that's tested and reasoned about independently of Python. The
  `PyO3` boundary lives in `src/router/bindings.rs` only.
- Python code targets 3.9+; use `collections.abc`/builtin generics
  (`list[str]`, not `typing.List[str]`) under `from __future__ import
  annotations`, and `typing.Union`/`Optional` (not bare `X | Y`) in any
  plain runtime assignment, which that future import doesn't defer.

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org/): `feat:`,
`fix:`, `test:`, `docs:`, `chore:`, `ci:`, etc.

## Releasing

Versioning is tag-driven — `Cargo.toml`'s checked-in version is just a
dev placeholder; `pyproject.toml` declares `dynamic = ["version"]`, so
maturin reads the real version from `Cargo.toml`, which the release
workflow overwrites to match the tag before building. Nothing in the
repo is manually bumped.

```bash
git tag v0.1.0
git push origin v0.1.0
```

pushing a `v*` tag runs [`.github/workflows/release.yml`](.github/workflows/release.yml):
builds wheels for Linux/macOS/Windows plus an sdist, publishes to PyPI,
and attaches the wheels to a GitHub Release. Two one-time manual steps
this needs, neither doable from CI itself:

1. A GitHub *environment* named `pypi` on this repo (Settings →
   Environments), optionally with required reviewers for extra safety
   before a publish runs.
2. A [Trusted Publisher](https://docs.pypi.org/trusted-publishers/) on
   the `korvex` PyPI project pointing at this repo, workflow file
   (`release.yml`), and environment (`pypi`) — no stored API token
   needed.

`workflow_dispatch` (the "Run workflow" button in the Actions tab) builds
the full matrix without publishing — useful to check it still builds
before cutting a real release.
