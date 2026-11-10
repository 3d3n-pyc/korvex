from __future__ import annotations

import ast

import pytest

from korvex._templates.parse import (
    Block,
    Extends,
    For,
    If,
    Include,
    Output,
    Set,
    TemplateError,
    TemplateSyntaxError,
    Text,
    parse,
)


def error(src: str) -> TemplateSyntaxError:
    with pytest.raises(TemplateSyntaxError) as info:
        parse(src, "t.html")
    return info.value


def test_text_and_output() -> None:
    nodes = parse("Hi {{ user.name }}!")
    assert [type(n) for n in nodes] == [Text, Output, Text]
    assert nodes[0].text == "Hi "
    assert ast.unparse(nodes[1].expr) == "user.name"


def test_comment_is_dropped() -> None:
    assert parse("a{# note\nover lines #}b") == [Text("a", 1), Text("b", 2)]


def test_if_elif_else() -> None:
    node = parse("{% if a %}A{% elif b %}B{% else %}C{% endif %}")[0]
    assert isinstance(node, If)
    assert [ast.unparse(c) for c, _ in node.branches] == ["a", "b"]
    assert node.branches[1][1] == [Text("B", 1)]
    assert node.orelse == [Text("C", 1)]


def test_if_without_else() -> None:
    node = parse("{% if a %}A{% endif %}")[0]
    assert isinstance(node, If) and node.orelse == []


def test_for_single_and_pair() -> None:
    one, two = parse("{% for x in xs %}{% endfor %}{% for k, v in d.items() %}{% endfor %}")
    assert isinstance(one, For) and one.targets == ("x",)
    assert isinstance(two, For) and two.targets == ("k", "v")
    assert ast.unparse(two.iterable) == "d.items()"


def test_for_else() -> None:
    node = parse("{% for x in xs %}{{ x }}{% else %}none{% endfor %}")[0]
    assert isinstance(node, For)
    assert isinstance(node.body[0], Output)
    assert node.orelse == [Text("none", 1)]


def test_set() -> None:
    node = parse("{% set total = a + 1 %}")[0]
    assert isinstance(node, Set) and node.name == "total"
    assert ast.unparse(node.value) == "a + 1"


def test_extends_block_include() -> None:
    nodes = parse('{% extends "base.html" %}{% block body %}{% include "nav.html" %}{% endblock body %}')
    assert isinstance(nodes[0], Extends) and nodes[0].template == "base.html"
    block = nodes[1]
    assert isinstance(block, Block) and block.name == "body"
    assert block.body == [Include("nav.html", 1)]


def test_extends_after_whitespace_and_comment() -> None:
    assert isinstance(parse('\n{# c #}\n{% extends "b.html" %}')[-1], Extends)


def test_nesting() -> None:
    src = "{% for x in xs %}{% if x %}{% for y in x %}{{ y }}{% endfor %}{% endif %}{% endfor %}"
    outer = parse(src)[0]
    assert isinstance(outer, For)
    inner_if = outer.body[0]
    assert isinstance(inner_if, If) and isinstance(inner_if.branches[0][1][0], For)


def test_line_numbers_after_multiline_text() -> None:
    nodes = parse("one\ntwo\n{{ a }}\n\n{% if b %}\nx\n{% endif %}")
    lines = [(type(n).__name__, n.line) for n in nodes]
    assert lines == [("Text", 1), ("Output", 3), ("Text", 3), ("If", 5)]
    assert nodes[3].branches[0][1][0].line == 5


def test_whitespace_control_strips_both_sides() -> None:
    nodes = parse("a  \n {%- if x -%}  \n b \n {%- endif -%} \n c")
    assert nodes[0] == Text("a", 1)
    assert nodes[1].branches[0][1] == [Text("b", 3)]
    assert nodes[2] == Text("c", 5)


def test_whitespace_control_on_output() -> None:
    nodes = parse("a \n{{- x -}}\n b")
    assert [type(n) for n in nodes] == [Text, Output, Text]
    assert nodes[0].text == "a" and nodes[2].text == "b"


def test_whitespace_kept_without_marks() -> None:
    assert parse(" {% if x %} y {% endif %} ")[0] == Text(" ", 1)


def test_filter_pipe_and_calls() -> None:
    node = parse("{{ title|truncate(20, end='...')|upper }}")[0]
    assert isinstance(node, Output)
    assert ast.unparse(node.expr) == "title | truncate(20, end='...') | upper"


def test_allowed_expression_forms() -> None:
    for src in [
        "a[1:2]",
        "a[b][-1]",
        "[1, 'x', None]",
        "(1, 2)",
        "{'a': 1}",
        "a and not b or c",
        "-a + b * 2 // 3 % 4 - 1 / 2",
        "a == 1 != 2 < 3 <= 4 > 5 >= 6",
        "a in b and c not in d",
        "a is None or b is not None",
        "a if b else c",
        "f(1, k=2)",
    ]:
        assert isinstance(parse("{{ " + src + " }}")[0], Output), src


@pytest.mark.parametrize(
    "src",
    [
        "lambda: 1",
        "[x for x in y]",
        "(a := 1)",
        "f(*a)",
        "f(**a)",
        "{**a}",
        "[*a]",
        "f'{a}'",
        "a ** 2",
        "a & b",
        "a is b",
        "b'x'",
        "x |",
        "(yield)",
        "a if",
        "",
    ],
)
def test_forbidden_expressions(src: str) -> None:
    err = error("ok\n{{ " + src + " }}")
    assert (err.name, err.line) == ("t.html", 2)


def test_filter_must_be_name_or_call_on_name() -> None:
    assert "filter" in error("{{ a|b.c }}").message
    assert "filter" in error("{{ a|1 }}").message


@pytest.mark.parametrize("src", ["_x", "a._b", "a.b.__class__", "f(_x)", "a|_f", "a[_b]"])
def test_underscore_names_refused(src: str) -> None:
    assert "underscore" in error("{{ " + src + " }}").message


@pytest.mark.parametrize("src", ['"{0.__globals__}".format(f)', "s.format(x)", "s.format_map(d)", "'a'.format()"])
def test_format_attributes_refused(src: str) -> None:
    err = error("{{ " + src + " }}")
    assert "'format" in err.message
    assert err.line == 1
    assert err.name == "t.html"


def test_normal_method_call_still_works() -> None:
    assert ast.unparse(parse("{{ s.upper() }}")[0].expr) == "s.upper()"


def test_underscore_in_set_and_for() -> None:
    error("{% set _x = 1 %}")
    error("{% for _x in y %}{% endfor %}")


def test_error_str_format() -> None:
    err = error("a\nb\n{% for x in y %}")
    assert str(err) == "t.html, line 3: unclosed {% for %}"
    assert (err.message, err.name, err.line) == ("unclosed {% for %}", "t.html", 3)
    assert isinstance(err, TemplateError)


@pytest.mark.parametrize(
    ("src", "text"),
    [
        ("{% if a %}", "unclosed {% if %}"),
        ("{% if a %}{% else %}x", "unclosed {% if %}"),
        ("{% block b %}", "unclosed {% block %}"),
        ("{% endif %}", "unexpected {% endif %}"),
        ("{% if a %}{% endfor %}", "unexpected {% endfor %}"),
        ("{% else %}", "unexpected {% else %}"),
        ("{% if a %}{% else %}{% elif b %}{% endif %}", "unexpected {% elif %}"),
        ("{% while a %}", "unknown tag 'while'"),
        ("{% %}", "empty tag"),
        ("{{ a", "unclosed '{{'"),
        ("{% if a", "unclosed '{%'"),
        ("{# a", "unclosed '{#'"),
        ("{% if a b %}{% endif %}", "bad expression"),
        ("{% for x %}{% endfor %}", "bad {% for %}"),
        ("{% set x %}", "bad {% set %}"),
        ("{% include a %}", "{% include %} takes a string"),
        ("{% extends 3 %}", "{% extends %} takes a string"),
        ("{% if a %}{% endif x %}", "takes no arguments"),
    ],
)
def test_structure_errors(src: str, text: str) -> None:
    assert text in error(src).message


def test_extends_must_be_first() -> None:
    assert "first tag" in error('x{% extends "b.html" %}').message
    assert "first tag" in error('{{ a }}{% extends "b.html" %}').message
    assert "first tag" in error('{% block a %}{% extends "b.html" %}{% endblock %}').message


def test_duplicate_block_and_mismatched_endblock() -> None:
    err = error("{% block a %}{% endblock %}\n{% block a %}{% endblock %}")
    assert "defined twice" in err.message and err.line == 2
    assert "does not match" in error("{% block a %}{% endblock b %}").message


def test_unclosed_reports_opening_line() -> None:
    assert error("x\n{% if a %}\n{% for x in y %}\n{% endfor %}").line == 2


@pytest.mark.parametrize("src", ["{% set if = 1 %}", "{% for class in x %}x{% endfor %}", "{% set ½ = 1 %}"])
def test_keyword_or_non_identifier_variable_is_a_syntax_error(src: str) -> None:
    assert error(src).line == 1


def test_closer_inside_string_or_braces() -> None:
    assert isinstance(parse('{{ "}}" }}', "t")[0], Output)
    assert isinstance(parse('{{ {"a": {"b": 1}} }}', "t")[0], Output)
    assert isinstance(parse('{% set x = "%}" %}', "t")[0], Set)


def test_unclosed_tag_after_quote_still_fails() -> None:
    assert "unclosed" in str(error('{{ "a }}'))
