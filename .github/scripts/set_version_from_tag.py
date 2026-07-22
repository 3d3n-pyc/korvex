"""Patches Cargo.toml's package version to match the git tag that
triggered this workflow run (e.g. tag "v0.1.0" -> version = "0.1.0").

Cargo.toml is the single source of truth for the version — pyproject.toml
declares `dynamic = ["version"]` so maturin reads it from there. This
script exists so the checked-in Cargo.toml can stay at a fixed dev
placeholder; the real version only ever comes from the tag that triggers
a release build, never from a manually-edited file.
"""

import os
import re
import sys
from pathlib import Path

ref_name = os.environ["GITHUB_REF_NAME"]
version = ref_name.removeprefix("v")

cargo_toml = Path("Cargo.toml")
content = cargo_toml.read_text()
# Matches the [package] table's own `version = "..."` specifically (the
# shortest span from `[package]` to the next `version = ` line) rather
# than a bare `^version = ".*"$` anywhere in the file, which would also
# match [dependencies.pyo3]'s own `version = "0.29.0"` below it.
pattern = re.compile(r'(\[package\].*?\nversion = )".*?"', re.DOTALL)
patched, count = pattern.subn(rf'\1"{version}"', content, count=1)
if count != 1:
    sys.exit("could not find [package]'s version field in Cargo.toml")

cargo_toml.write_text(patched)
print(f"Cargo.toml version set to {version} (from tag {ref_name})")
