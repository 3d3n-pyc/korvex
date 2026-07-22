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

Use plain, descriptive messages. No specific convention enforced yet.
