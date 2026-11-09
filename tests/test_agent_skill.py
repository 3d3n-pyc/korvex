"""The agent skill (.agents/skills/korvex/SKILL.md) stays true as Korvex
changes: its complete app and its tests run, every other snippet parses,
and every name it imports from korvex exists."""

import ast
import importlib
import re
import textwrap
from pathlib import Path

import pytest

SKILL = Path(__file__).parent.parent / ".agents" / "skills" / "korvex" / "SKILL.md"
BLOCK = re.compile(r"^( *)```python\n(.*?)^\1```", re.S | re.M)


def snippets() -> list[str]:
    """Every Python block of the skill, dedented."""
    return [textwrap.dedent(code) for _, code in BLOCK.findall(SKILL.read_text())]


def test_snippets_parse_and_import_what_exists():
    for code in snippets():
        for node in ast.walk(ast.parse(code)):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("korvex"):
                module = importlib.import_module(node.module)
                for alias in node.names:
                    assert hasattr(module, alias.name), f"{node.module}.{alias.name}"


def test_complete_app_and_its_tests_run():
    pytest.importorskip("msgspec")
    app_code, test_code = snippets()[:2]
    namespace: dict = {"__name__": "skill_app"}
    exec(compile(app_code, "SKILL.md app", "exec"), namespace)
    exec(compile(test_code, "SKILL.md tests", "exec"), namespace)
    namespace["test_tasks"]()


def test_packaged_copy_is_identical():
    packaged = SKILL.parent.parent.parent.parent / "korvex" / "_skill" / "SKILL.md"
    assert packaged.read_bytes() == SKILL.read_bytes(), "copy .agents/skills/korvex/SKILL.md to korvex/_skill/SKILL.md"
