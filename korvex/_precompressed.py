"""Choosing a precompressed sibling (`app.js.zst`, `.br`, `.gz`) of a static file."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

CANDIDATES = (("zstd", ".zst"), ("br", ".br"), ("gzip", ".gz"))  # our order of preference


def accepted(header: str) -> dict[str, float]:
    """`Accept-Encoding: gzip, br;q=0.8, *;q=0` -> `{"gzip": 1.0, "br": 0.8, "*": 0.0}`."""
    accepted: dict[str, float] = {}
    for item in header.split(","):
        name, _, params = item.strip().partition(";")
        if not name:
            continue
        q = 1.0
        key, _, value = params.strip().partition("=")
        if key.strip().lower() == "q":
            try:
                q = float(value)
            except ValueError:
                q = 0.0
        accepted[name.strip().lower()] = 0.0 if q != q else min(max(q, 0.0), 1.0)  # NaN -> 0
    return accepted


def _ranked(accept_encoding: str) -> list[tuple[str, str]]:
    """The candidates the client accepts, best first (client q, then our order)."""
    wanted = accepted(accept_encoding)
    star = wanted.get("*", 0.0)
    scored = [(wanted.get(name, star if star > 0 else 0.0), name, suffix) for name, suffix in CANDIDATES]
    scored.sort(key=lambda item: -item[0])  # stable: ties keep our order
    return [(name, suffix) for q, name, suffix in scored if q > 0]


def _usable(path: Path, root: Path, mtime: float) -> bool:
    """Whether `path` is a regular file inside `root` at least as new as `mtime`."""
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        return False
    stat = resolved.stat()
    return resolved.is_file() and stat.st_mtime >= mtime


def variant(target: Path, root: Path, accept_encoding: str) -> Optional[tuple[Path, str]]:
    """The precompressed sibling of `target` the client prefers, as `(path, encoding)`.

    Looks for `target` plus `.zst`, `.br` or `.gz`. A sibling is used only if it
    stays inside `root`, is a regular file and isn't older than `target`.
    Returns `None` when there is none.
    """
    if not accept_encoding:
        return None
    try:
        mtime = target.stat().st_mtime
    except (OSError, ValueError):
        return None
    for name, suffix in _ranked(accept_encoding):
        candidate = Path(str(target) + suffix)
        try:
            if _usable(candidate, root, mtime):
                return candidate.resolve(), name
        except (OSError, ValueError):
            continue
    return None
