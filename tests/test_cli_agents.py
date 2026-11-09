"""`korvex agents` installs the packaged agent skill into a project."""

from pathlib import Path

from korvex._cli import main

PACKAGED = Path(__file__).parent.parent / "korvex" / "_skill" / "SKILL.md"
PLACES = (".agents/skills/korvex/SKILL.md", ".claude/skills/korvex/SKILL.md")


def test_installs_both_copies_and_reports(tmp_path, capsys):
    main(["agents", "--dir", str(tmp_path)])
    lines = capsys.readouterr().out.splitlines()
    for place in PLACES:
        assert (tmp_path / place).read_bytes() == PACKAGED.read_bytes()
    assert lines[:2] == [str(tmp_path / place) for place in PLACES]
    assert lines[2] == "Coding agents now read how to write Korvex apps. Rerun after upgrading Korvex."


def test_rerun_overwrites_a_modified_copy(tmp_path):
    main(["agents", "--dir", str(tmp_path)])
    (tmp_path / PLACES[0]).write_text("old")
    main(["agents", "--dir", str(tmp_path)])
    assert (tmp_path / PLACES[0]).read_bytes() == PACKAGED.read_bytes()
