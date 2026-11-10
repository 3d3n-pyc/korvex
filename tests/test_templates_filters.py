"""The template escaping and the built-in filters."""

import json

from korvex._templates.filters import (
    FILTERS,
    UNDEFINED,
    Markup,
    escape,
)


def test_escape_each_type() -> None:
    """Each kind of value is escaped the right way."""
    assert escape("<a href='x'>&\"") == "&lt;a href=&#39;x&#39;&gt;&amp;&#34;"
    assert escape(None) == ""
    assert escape(True) == "True"
    assert escape(False) == "False"
    assert escape(3) == "3"
    assert escape(1.5) == "1.5"
    assert escape(["<"]) == "[&#39;&lt;&#39;]"


def test_markup_passthrough() -> None:
    """Markup is not escaped, and str operations give plain str."""
    m = Markup("<b>")
    assert escape(m) is m
    assert repr(m) == "Markup('<b>')"
    assert type(m + "x") is str


def test_safe_and_escape_filters() -> None:
    """`safe` and `escape` return Markup."""
    assert escape(FILTERS["safe"]("<b>")) == "<b>"
    out = FILTERS["e"]("<b>")
    assert isinstance(out, Markup) and out == "&lt;b&gt;"
    assert FILTERS["escape"] is FILTERS["e"]


def test_join_mixes_markup_and_str() -> None:
    """join escapes plain str items but not Markup."""
    out = FILTERS["join"](["<a>", Markup("<b>"), 1], ", ")
    assert isinstance(out, Markup)
    assert out == "&lt;a&gt;, <b>, 1"


def test_default() -> None:
    """default replaces None and the undefined marker only."""
    d = FILTERS["default"]
    assert d(None, "x") == "x"
    assert d(UNDEFINED, "x") == "x"
    assert d(0, "x") == 0
    assert d("", "x") == ""


def test_simple_filters() -> None:
    """upper, lower and length."""
    assert FILTERS["upper"]("ab") == "AB"
    assert FILTERS["lower"]("AB") == "ab"
    assert FILTERS["length"]([1, 2]) == 2


def test_truncate() -> None:
    """truncate cuts at a word boundary."""
    t = FILTERS["truncate"]
    assert t("short", 10) == "short"
    assert t("hello big world", 12) == "hello..."
    assert t("abcdefghij", 6) == "abc..."


def test_tojson_script_safe() -> None:
    """tojson is safe in a script tag and in single-quoted attributes."""
    out = FILTERS["tojson"]({"a": "</script>&'"})
    assert isinstance(out, Markup)
    assert not any(c in out for c in "<>&'")
    assert json.loads(out) == {"a": "</script>&'"}
    assert FILTERS["tojson"]([1, 2]) == "[1,2]"


def test_urlencode() -> None:
    """urlencode handles text, dicts and pairs."""
    u = FILTERS["urlencode"]
    assert u("a b/c&d") == "a%20b/c%26d"
    assert u({"q": "a b", "n": 1}) == "q=a+b&n=1"
    assert u([("a", "1"), ("b", "2")]) == "a=1&b=2"


def test_escape_int_subclass_and_html_method() -> None:
    """An int subclass is escaped; an object with `__html__` is trusted."""

    class Evil(int):
        def __str__(self) -> str:
            return "<b>"

    class Widget:
        def __html__(self) -> str:
            return "<i>ok</i>"

    assert escape(Evil(1)) == "&lt;b&gt;"
    assert escape(Widget()) == "<i>ok</i>"
    assert escape(True) == "True"


def test_tojson_docstring_scope() -> None:
    """The docs say single-quoted attributes, as double quotes are not safe."""
    assert "single-quoted" in (FILTERS["tojson"].__doc__ or "")
