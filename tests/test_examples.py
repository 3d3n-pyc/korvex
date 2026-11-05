"""The example projects keep working as Korvex changes: each one's own
test suite runs here."""

import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).parent.parent / "examples"


@pytest.mark.parametrize("project", ["taskboard"])
def test_example_project(project):
    pytest.importorskip("msgspec")
    # From the project's folder, so its own pytest settings apply and the
    # installed Korvex is used, not the source tree.
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=EXAMPLES / project,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
