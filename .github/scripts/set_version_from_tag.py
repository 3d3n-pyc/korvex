"""Sets the version in Cargo.toml from the git tag (v0.2.0 -> 0.2.0).

A pre-release tag in PEP 440 form becomes SemVer for Cargo (v1.0.0rc1 ->
1.0.0-rc.1); maturin turns it back into 1.0.0rc1 for the wheel."""

import os
import re
import sys
from pathlib import Path

ref_name = os.environ["GITHUB_REF_NAME"]
version = re.sub(r"^(\d+\.\d+\.\d+)(a|b|rc)(\d+)$", r"\1-\2.\3", ref_name.removeprefix("v"))

cargo_toml = Path("Cargo.toml")
content = cargo_toml.read_text()
# The first `version = ` after [package], not the dependencies' ones.
pattern = re.compile(r'(\[package\].*?\nversion = )".*?"', re.DOTALL)
patched, count = pattern.subn(rf'\1"{version}"', content, count=1)
if count != 1:
    sys.exit("could not find [package]'s version field in Cargo.toml")

cargo_toml.write_text(patched)
print(f"Cargo.toml version set to {version} (from tag {ref_name})")
