"""The tutorial (docs/tutorial.md) stays true as Korvex changes: its final
main.py and test_main.py, each preceded by a line "**main.py**" or
"**test_main.py**", are written to a folder and tested with pytest."""

import re
import subprocess
import sys
from pathlib import Path

import pytest

TUTORIAL = Path(__file__).parent.parent / "docs" / "tutorial.md"


def block(name: str) -> str:
    """The Python block that follows the line ``**name**``."""
    pattern = rf"^\*\*{re.escape(name)}\*\*\n\n```python\n(.*?)^```"
    match = re.search(pattern, TUTORIAL.read_text(), re.S | re.M)
    assert match, f"no **{name}** block in docs/tutorial.md"
    return match.group(1)


def test_final_files_pass_their_tests(tmp_path):
    pytest.importorskip("msgspec")
    (tmp_path / "main.py").write_text(block("main.py"))
    (tmp_path / "test_main.py").write_text(block("test_main.py"))
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(tmp_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_step_snippets_parse():
    import ast

    code_blocks = re.findall(r"^```python\n(.*?)^```", TUTORIAL.read_text(), re.S | re.M)
    assert len(code_blocks) >= 5
    for code in code_blocks:
        ast.parse(code)
