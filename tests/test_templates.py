"""Tests for the `Templates` folder loader."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from korvex._templates import TemplateError, Templates, env


def make(tmp_path: Path, files: dict, **kw) -> Templates:
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    return Templates(tmp_path, **kw)


def bump(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))


def test_render_context(tmp_path):
    t = make(tmp_path, {"a.html": "Hi {{ name }}"})
    assert t.render("a.html", name="Ada") == "Hi Ada"


def test_cache_compiles_once(tmp_path, monkeypatch):
    t = make(tmp_path, {"a.html": "x"})
    calls = []
    real = env.compile_template
    monkeypatch.setattr(env, "compile_template", lambda *a: calls.append(1) or real(*a))
    t.render("a.html")
    t.render("a.html")
    assert len(calls) == 1


def test_reload_picks_up_include(tmp_path):
    t = make(tmp_path, {"a.html": "{% include 'b.html' %}", "b.html": "old"}, reload=True)
    assert t.render("a.html") == "old"
    bump(tmp_path / "b.html", "new")
    assert t.render("a.html") == "new"


def test_no_reload_by_default(tmp_path):
    t = make(tmp_path, {"a.html": "{% include 'b.html' %}", "b.html": "old"})
    t.render("a.html")
    bump(tmp_path / "b.html", "new")
    assert t.render("a.html") == "old"


@pytest.mark.parametrize("name", ["../x.html", "a/../../x.html", "/etc/passwd", "a\\b.html", ""])
def test_bad_names(tmp_path, name):
    t = make(tmp_path, {})
    with pytest.raises(TemplateError):
        t.render(name)


def test_missing_template(tmp_path):
    t = make(tmp_path, {})
    with pytest.raises(TemplateError, match="no template 'x.html' in"):
        t.render("x.html")


def test_globals_and_filter(tmp_path):
    t = make(tmp_path, {"a.html": "{{ site }} {{ v|shout }}"})
    t.globals["site"] = "S"
    t.filters["shout"] = lambda s: str(s).upper() + "!"
    assert t.render("a.html", v="hey") == "S HEY!"


def test_name_as_context_variable(tmp_path):
    t = make(tmp_path, {"a.html": "{{ name }}"})
    assert t.render("a.html", name="n") == "n"


def test_threads(tmp_path):
    t = make(tmp_path, {"a.html": "{{ n }}"})
    with ThreadPoolExecutor(8) as pool:
        out = list(pool.map(lambda n: t.render("a.html", n=n), range(100)))
    assert out == [str(n) for n in range(100)]
