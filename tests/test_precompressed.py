"""Choosing a precompressed sibling of a static file."""

from __future__ import annotations

import os
from pathlib import Path

from korvex._precompressed import variant


def _site(tmp_path: Path, *suffixes: str) -> tuple[Path, Path]:
    root = (tmp_path / "root").resolve()
    root.mkdir()
    target = root / "app.js"
    target.write_text("x")
    for suffix in suffixes:
        (root / f"app.js{suffix}").write_text("y")
    return root, target


def _encoding(root: Path, target: Path, header: str) -> str | None:
    found = variant(target, root, header)
    return found[1] if found else None


def test_our_order_breaks_ties(tmp_path: Path) -> None:
    root, target = _site(tmp_path, ".zst", ".br", ".gz")
    assert _encoding(root, target, "gzip, br, zstd") == "zstd"
    found = variant(target, root, "br")
    assert found == (root / "app.js.br", "br")


def test_client_q_wins(tmp_path: Path) -> None:
    root, target = _site(tmp_path, ".zst", ".br", ".gz")
    assert _encoding(root, target, "zstd;q=0.5, gzip;q=0.9") == "gzip"


def test_q_zero_excludes(tmp_path: Path) -> None:
    root, target = _site(tmp_path, ".br", ".gz")
    assert _encoding(root, target, "br;q=0, gzip") == "gzip"
    assert _encoding(root, target, "br;q=0") is None


def test_star_covers_unnamed(tmp_path: Path) -> None:
    root, target = _site(tmp_path, ".br", ".gz")
    assert _encoding(root, target, "*") == "br"
    assert _encoding(root, target, "br;q=0, *") == "gzip"
    assert _encoding(root, target, "*;q=0, gzip") == "gzip"
    assert _encoding(root, target, "*;q=0") is None


def test_stale_variant_ignored(tmp_path: Path) -> None:
    root, target = _site(tmp_path, ".br", ".gz")
    stat = target.stat()
    os.utime(root / "app.js.br", (stat.st_atime, stat.st_mtime - 10))
    assert _encoding(root, target, "br, gzip") == "gzip"


def test_symlink_out_of_root(tmp_path: Path) -> None:
    root, target = _site(tmp_path)
    outside = tmp_path / "secret.br"
    outside.write_text("z")
    (root / "app.js.br").symlink_to(outside)
    assert variant(target, root, "br") is None


def test_missing_file_and_empty_header(tmp_path: Path) -> None:
    root, target = _site(tmp_path)
    assert variant(target, root, "br, gzip") is None
    assert variant(target, root, "") is None


def test_nan_q_is_refused(tmp_path: Path) -> None:
    root, target = _site(tmp_path, ".zst", ".gz")
    assert _encoding(root, target, "zstd;q=0.5, br;q=nan, gzip;q=1") == "gzip"
