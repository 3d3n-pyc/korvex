"""The template compiler: tags, filters, inheritance, loops and errors."""

from typing import Any, Optional

import pytest

from korvex._templates.compile import UndefinedError, compile_template
from korvex._templates.filters import FILTERS, Markup
from korvex._templates.parse import TemplateError, TemplateSyntaxError, parse


def render(
    source: str,
    context: Optional[dict] = None,
    templates: Optional[dict] = None,
    globals_: Optional[dict] = None,
    filters: Optional[dict] = None,
) -> str:
    """Compile `source` as post.html and render it."""
    files = {"post.html": source, **(templates or {})}

    def load(name: str) -> list:
        return parse(files[name], name)

    fn, _ = compile_template(load("post.html"), "post.html", load, filters or FILTERS)
    return fn(context or {}, globals_ or {})


def test_text_output_and_escaping() -> None:
    """Text is kept, values are escaped."""
    assert render("<p>{{ x }}</p>", {"x": "<b>&'\""}) == "<p>&lt;b&gt;&amp;&#39;&#34;</p>"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Markup("<b>"), "<b>"),
        ("<b>", "&lt;b&gt;"),
        (3, "3"),
        (1.5, "1.5"),
        (True, "True"),
        (False, "False"),
        (None, ""),
        (["<"], "[&#39;&lt;&#39;]"),
    ],
)
def test_escape_each_type(value: Any, expected: str) -> None:
    """Each kind of value is written the right way."""
    assert render("{{ x }}", {"x": value}) == expected


def test_constants_and_operators() -> None:
    """Constants are escaped at compile time and operators work."""
    assert render("{{ '<' }}{{ 1 + 2 * 3 }}{{ 'a' if x > 1 else 'b' }}", {"x": 2}) == "&lt;7a"


def test_if_elif_else() -> None:
    """Each branch is chosen."""
    src = "{% if x == 1 %}one{% elif x == 2 %}two{% else %}many{% endif %}"
    assert [render(src, {"x": n}) for n in (1, 2, 3)] == ["one", "two", "many"]


def test_for_with_targets_and_else() -> None:
    """A loop unpacks pairs and runs its else for an empty iterable."""
    src = "{% for k, v in d.items() %}{{ k }}={{ v }};{% else %}none{% endfor %}"
    assert render(src, {"d": {"a": 1, "b": 2}}) == "a=1;b=2;"
    assert render(src, {"d": {}}) == "none"


def test_loop_variables() -> None:
    """The loop object has index, index0, first, last and length."""
    src = "{% for x in xs %}{{ loop.index }}{{ loop.index0 }}{{ loop.first }}{{ loop.last }}{{ loop.length }},{% endfor %}"
    assert render(src, {"xs": "ab"}) == "10TrueFalse2,21FalseTrue2,"


def test_loop_last_on_a_generator() -> None:
    """An iterable without a length is turned into a list first."""
    src = "{% for x in xs %}{{ x }}{% if loop.last %}!{% endif %}{% endfor %}"
    assert render(src, {"xs": (n for n in range(3))}) == "012!"


def test_inner_loop_has_its_own_loop() -> None:
    """`loop` is the innermost loop."""
    src = "{% for a in xs %}{% for b in xs %}{{ loop.index }}{% endfor %}{{ loop.index }}|{% endfor %}"
    assert render(src, {"xs": [0, 0]}) == "121|122|"


def test_loop_misuse_is_a_syntax_error() -> None:
    """Only the five loop attributes exist."""
    with pytest.raises(TemplateSyntaxError, match="no attribute 'foo'"):
        render("{% for x in xs %}{{ loop.foo }}{% endfor %}", {"xs": [1]})
    with pytest.raises(TemplateSyntaxError, match="loop.index"):
        render("{% for x in xs %}{{ loop }}{% endfor %}", {"xs": [1]})


def test_set_is_visible_to_the_end() -> None:
    """A set inside a for body stays visible after it."""
    assert render("{% set a = 1 %}{{ a }}{% for x in xs %}{% set b = x %}{% endfor %}{{ b }}", {"xs": [7, 8]}) == "18"


def test_set_in_an_untaken_branch_is_undefined() -> None:
    """A name set in a branch that did not run is not defined."""
    src = "{% if c %}{% set a = 1 %}{% endif %}{{ a }}"
    assert render(src, {"c": True}) == "1"
    with pytest.raises(UndefinedError, match="post.html, line 1: 'a' is undefined"):
        render(src, {"c": False})


def test_context_then_globals() -> None:
    """The context wins over the globals."""
    assert render("{{ a }}{{ b }}", {"a": "ctx"}, globals_={"a": "glb", "b": "glb"}) == "ctxglb"


def test_attribute_item_and_call() -> None:
    """Dicts give items, objects give attributes, calls and subscripts work."""

    class Obj:
        name = "obj"

        def hi(self, who: str) -> str:
            return "hi " + who

    ctx = {"d": {"k": "v", "n": {"m": 1}}, "o": Obj(), "xs": [1, 2, 3]}
    assert (
        render("{{ d.k }}{{ d.n.m }}{{ o.name }}{{ o.hi('x') }}{{ xs[1] }}{{ xs[1:] | length }}", ctx) == "v1objhi x22"
    )


def test_dict_subclass_uses_items() -> None:
    """A dict subclass is read by key as well."""
    from collections import OrderedDict

    assert render("{{ a.b }}{{ a.c|default('-') }}", {"a": OrderedDict(b=1)}) == "1-"


def test_filters() -> None:
    """The built-in filters run, in a chain, with arguments."""
    ctx = {"x": "Hello <b>"}
    assert render("{{ x|upper }}|{{ x|lower|length }}|{{ x|safe }}|{{ x|truncate(5) }}", ctx) == (
        "HELLO &lt;B&gt;|9|Hello <b>|He..."
    )
    assert render("{{ xs|join(', ') }}", {"xs": ["a", "<"]}) == "a, &lt;"


def test_default_filter() -> None:
    """`default` falls back for a missing name, key, attribute, or None."""
    ctx = {"d": {"a": None, "z": ""}}
    src = "{{ nope|default('n') }}{{ d.a|default('a') }}{{ d.b|default('b') }}{{ d.z|default('z') }}{{ nope.x.y|default('c') }}"
    assert render(src, ctx) == "nabc"
    assert render("{{ nope|default }}|{{ x|default(y) }}", {"x": None, "y": 1}) == "|1"


def test_default_with_keywords_uses_the_filter() -> None:
    """`default` with a keyword goes through the filter function."""
    assert render("{{ x|default(fallback='f') }}", {}) == "f"


def test_custom_filter_and_unknown_filter() -> None:
    """App filters work; an unknown filter fails at compile time."""
    filters = {**FILTERS, "twice": lambda v, n=2: str(v) * n}
    assert render("{{ x|twice }}{{ x|twice(3) }}", {"x": "a"}, filters=filters) == "aaaaa"
    with pytest.raises(TemplateSyntaxError, match="post.html, line 2: unknown filter 'nope'"):
        render("a\n{{ x|nope }}", {"x": 1})


def test_extends_two_levels_and_include() -> None:
    """Child blocks override their parents; includes are inlined."""
    templates = {
        "root.html": "<{% block a %}ra{% endblock %}|{% block b %}rb{% endblock %}|{% include 'part.html' %}>",
        "mid.html": "{% extends 'root.html' %}{% block a %}ma[{% block inner %}mi{% endblock %}]{% endblock %}"
        "{% block b %}mb{% endblock %}",
        "part.html": "part {{ x }}",
    }
    src = "{% extends 'mid.html' %}{% block inner %}{{ x }}{% endblock %}{% block b %}cb{% endblock %}"
    assert render(src, {"x": "<1>"}, templates) == "<ma[&lt;1&gt;]|cb|part &lt;1&gt;>"


def test_used_lists_dependencies() -> None:
    """`used` has every template pulled in."""
    files = {"p": "{% extends 'b' %}", "b": "{% include 'i' %}", "i": "x"}

    def load(name: str) -> list:
        return parse(files[name], name)

    assert compile_template(load("p"), "p", load, FILTERS)[1] == {"b", "i"}


def test_circular_include() -> None:
    """A template that includes itself is refused."""
    files = {"a": "{% include 'b' %}", "b": "{% include 'a' %}"}

    def load(name: str) -> list:
        return parse(files[name], name)

    with pytest.raises(TemplateSyntaxError, match="itself"):
        compile_template(load("a"), "a", load, FILTERS)


def test_error_in_included_template_names_it() -> None:
    """A runtime error in an include names that file and its line."""
    with pytest.raises(UndefinedError, match=r"part.html, line 2: 'who' is undefined"):
        render("{% include 'part.html' %}", templates={"part.html": "a\n{{ who }}"})


def test_undefined_name_everywhere() -> None:
    """An undefined name raises with the line, however it is used."""
    for src in [
        "{{ user }}",
        "{{ user.name }}",
        "{{ user() }}",
        "{% for x in user %}{% endfor %}",
    ]:
        with pytest.raises(UndefinedError) as info:
            render("a\n" + src)
        assert str(info.value) == "post.html, line 2: 'user' is undefined"


def test_missing_key_and_attribute() -> None:
    """A missing key or attribute names the whole expression."""
    with pytest.raises(UndefinedError, match=r"post.html, line 1: 'u.name' is undefined"):
        render("{{ u.name }}", {"u": {}})
    with pytest.raises(UndefinedError, match=r"'o.name' is undefined"):
        render("{{ o.name }}", {"o": object()})
    with pytest.raises(UndefinedError, match=r"'u.a.b' is undefined"):
        render("{{ u.a.b }}", {"u": {"a": {}}})


def test_runtime_error_line() -> None:
    """Another error becomes a TemplateError with the right line, even in a grouped output."""
    src = "<p>{{ a }}</p>\n<p>{{ a }}</p>\n<p>{{ 1 / a }}</p>\n<p>{{ a }}</p>"
    with pytest.raises(TemplateError, match=r"post.html, line 3: ZeroDivisionError: division by zero") as info:
        render(src, {"a": 0})
    assert isinstance(info.value.__cause__, ZeroDivisionError)
    assert type(info.value) is TemplateError


def test_runtime_error_inside_if_and_for() -> None:
    """Statements report their own line."""
    with pytest.raises(TemplateError, match="line 3: TypeError"):
        render("{% for x in xs %}\n{{ x }}\n{% if x < 'a' %}y{% endif %}{% endfor %}", {"xs": [1]})


def test_underscore_names_are_refused() -> None:
    """Names and attributes starting with an underscore never compile."""
    with pytest.raises(TemplateSyntaxError):
        render("{{ _x }}")
    with pytest.raises(TemplateSyntaxError):
        render("{{ a.__class__ }}", {"a": 1})


def test_empty_bodies_and_whitespace_control() -> None:
    """Empty bodies still compile, and `-` trims."""
    assert render("{% if x %}{% endif %}{% for a in x %}{% endfor %}a {%- if 1 %} b{% endif %}", {"x": []}) == "a b"


def test_render_is_reusable() -> None:
    """One compiled function renders many contexts."""
    files = {"post.html": "{% for x in xs %}{{ x }}{% endfor %}"}

    def load(name: str) -> list:
        return parse(files[name], name)

    fn, _ = compile_template(load("post.html"), "post.html", load, FILTERS)
    assert fn({"xs": [1, 2]}, {}) == "12"
    assert fn({"xs": [3]}, {}) == "3"


def test_default_on_missing_item() -> None:
    """`x[k]|default(y)` falls back on a missing key or index."""
    assert render("{{ x['a']|default('d') }}", {"x": {}}) == "d"
    assert render("{{ x[3]|default('d') }}", {"x": [1]}) == "d"
    assert render("{{ x['a']|default('d') }}", {"x": {"a": 1}}) == "1"


def test_int_subclass_escaped_in_output() -> None:
    """An int subclass with a custom `__str__` is escaped."""

    class Evil(int):
        def __str__(self) -> str:
            return "<b>"

    assert render("{{ v }}", {"v": Evil(1)}) == "&lt;b&gt;"


@pytest.mark.parametrize(
    "cond",
    [
        "nope",
        "a.b",
        "a.b.c",
        "d.k",
        "d.k.z",
        "d['k']",
        "l[5]",
        "nope['k']",
        "nope.b['k'].c",
        "nope.x and d.y",
        "d.k > 3",
        "d.k <= 3",
        "d.k == 3",
        "d.k != None and d.k",
    ],
)
def test_missing_in_a_condition_is_false(cond: str) -> None:
    ctx = {"a": {}, "d": {}, "l": [1]}
    assert render("{% if " + cond + " %}Y{% else %}N{% endif %}", ctx) == "N"


def test_missing_in_elif_and_boolean_logic() -> None:
    ctx = {"user": {"admin": True}}
    assert render("{% if x.y %}A{% elif user.admin %}B{% endif %}", ctx) == "B"
    assert render("{% if user.admin and post.draft %}Y{% else %}N{% endif %}", ctx) == "N"
    assert render("{% if post.draft or user.admin %}Y{% endif %}", ctx) == "Y"
    assert render("{% if not x.y %}Y{% endif %}") == "Y"
    assert render("{% if x.n == None %}Y{% endif %}") == "Y"
    assert render("{% if x.n != 3 %}Y{% endif %}") == "Y"
    assert render("{% if 3 in x.n %}Y{% else %}N{% endif %}", {"x": {"n": [3]}}) == "Y"


def test_present_values_in_a_condition_are_unchanged() -> None:
    ctx = {"x": {"n": 5}, "zero": 0}
    assert render("{% if x.n > 3 %}Y{% endif %}", ctx) == "Y"
    assert render("{% if zero %}Y{% else %}N{% endif %}", ctx) == "N"


def test_missing_stays_strict_outside_conditions() -> None:
    for source in ["{{ a.b }}", "{% for i in a.b %}{% endfor %}", "{% set v = a.b %}", "{{ a.b + 1 }}"]:
        with pytest.raises(UndefinedError):
            render(source, {"a": {}})
    with pytest.raises(UndefinedError):
        render("{% if x %}{{ nope }}{% endif %}", {"x": 1})
    with pytest.raises(UndefinedError):
        render("{% if f(nope) %}{% endif %}", {"f": bool})


def test_real_errors_in_a_condition_are_not_swallowed() -> None:
    with pytest.raises(TemplateError, match="ZeroDivisionError"):
        render("{% if 1 / z %}{% endif %}", {"z": 0})
    with pytest.raises(TemplateError, match="TypeError"):
        render("{% if f(1) %}{% endif %}", {"f": len})
    with pytest.raises(TemplateError, match="TypeError"):
        render("{% if n > 3 %}{% endif %}", {"n": None})
