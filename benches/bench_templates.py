"""Benchmark: template rendering, Korvex vs Jinja2 vs minijinja.

Run with:
    uv run --group bench python benches/bench_templates.py

Each engine renders the same templates with autoescape on, templates
precompiled and cached. Outputs are checked equal to Jinja2's.
"""

import html
import re
import statistics
import timeit

import jinja2
import markupsafe
import minijinja

from korvex._templates.compile import compile_template  # noqa: E402
from korvex._templates.filters import FILTERS  # noqa: E402
from korvex._templates.parse import parse  # noqa: E402

TEMPLATES = {
    "table.html": (
        "<table>{% for r in rows %}"
        '<tr class="{% if loop.index % 2 == 0 %}even{% else %}odd{% endif %}">'
        "<td>{{ loop.index }}</td><td>{{ r.id }}</td><td>{{ r.name }}</td>"
        "<td>{{ r.email }}</td><td>{{ r.note }}</td>"
        "<td>{% if r.active %}yes{% else %}no{% endif %}</td></tr>"
        "{% endfor %}</table>"
    ),
    "header.html": "<header><h1>{{ site }}</h1><span>{{ user.name }}</span></header>",
    "footer.html": "<footer>&copy; {{ site }} {{ year }}</footer>",
    "base.html": (
        "<html><head><title>{% block title %}Site{% endblock %}</title></head><body>"
        '{% include "header.html" %}<nav>{% for n in nav %}'
        '<a href="{{ n.url }}"{% if n.active %} class="on"{% endif %}>{{ n.label }}</a>'
        "{% endfor %}</nav><main>{% block content %}{% endblock %}</main>"
        "<aside>{% block sidebar %}{% endblock %}</aside>"
        '{% include "footer.html" %}</body></html>'
    ),
    "page.html": (
        '{% extends "base.html" %}{% block title %}{{ title }}{% endblock %}'
        "{% block content %}<h2>{{ title }}</h2><p>{{ body }}</p>{% endblock %}"
        "{% block sidebar %}{% if user.admin %}<b>admin</b>{% else %}guest{% endif %}"
        "{% endblock %}"
    ),
    "form.html": (
        '<form>{% for f in fields %}<label for="{{ f.id }}">{{ f.label|upper }}</label>'
        '<input id="{{ f.id }}" name="{{ f.name }}" value="{{ f.value }}" '
        'placeholder="{{ f.hint }}"><small>{{ f.help|default("-") }}</small>'
        "{% if f.icon %}{{ f.icon|safe }}{% endif %}{% endfor %}"
        "<textarea>{{ text }}</textarea></form>"
    ),
}
NASTY = "<b>\"Tom\" & 'Jerry'</b>"
CONTEXTS = {
    "table.html": {
        "rows": [
            {
                "id": i,
                "name": f"User {i} <{i}>",
                "email": f"u{i}@example.com",
                "note": NASTY if i % 3 == 0 else "plain note",
                "active": i % 2 == 0,
            }
            for i in range(100)
        ]
    },
    "page.html": {
        "site": "Acme & Co",
        "year": 2026,
        "user": {"name": "Ann <admin>", "admin": True},
        "title": "Dashboard",
        "body": "Welcome back",
        "nav": [{"url": f"/p/{i}?a=1&b=2", "label": f"Item {i}", "active": i == 3} for i in range(20)],
    },
    "form.html": {
        "fields": [
            {
                "id": f"f{i}",
                "name": f"field_{i}",
                "label": f"Name <{i}>",
                "value": NASTY,
                "hint": 'say "hi" & <wave>',
                **({"help": "it's <help>"} if i % 2 else {}),
                "icon": "<i class='x'></i>" if i % 5 == 0 else "",
            }
            for i in range(30)
        ],
        "text": NASTY * 5,
    },
}
PAGES = [("table", "table.html"), ("layout", "page.html"), ("form", "form.html")]


def normalize(s):
    """Collapse whitespace and unify the entities engines differ on."""
    return " ".join(s.replace("&#x2f;", "/").replace("&#34;", "&quot;").replace("&#39;", "&#x27;").split())


def load(name):
    """Parse one of the benchmark templates."""
    return parse(TEMPLATES[name], name)


def korvex_render(name):
    """Compile one of the benchmark templates with Korvex."""
    return compile_template(load(name), name, load, FILTERS)[0]


def esc_str(s):
    """Escape with five `replace` calls, the pure-Python way."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("'", "&#39;").replace('"', "&#34;")


def renderers(name):
    """Return {engine: zero-argument render callable} for one page."""
    ctx = CONTEXTS[name]
    env = jinja2.Environment(loader=jinja2.DictLoader(TEMPLATES), autoescape=True)
    mini = minijinja.Environment(templates=TEMPLATES)
    korvex = korvex_render(name)
    jt = env.get_template(name)
    return {
        "korvex": lambda: korvex(ctx, {}),
        "jinja2": lambda: jt.render(ctx),
        "minijinja": lambda: mini.render_template(name, **ctx),
    }


def median_us(fn, repeat=9):
    """Median microseconds per call over several timeit repeats."""
    number, _ = timeit.Timer(fn).autorange()
    runs = timeit.Timer(fn).repeat(repeat=repeat, number=number)
    return statistics.median(runs) / number * 1e6


def bench_pages():
    """Check outputs against Jinja2, then print the timing table."""
    print(f"{'page':8}{'korvex':>10}{'jinja2':>10}{'minijinja':>11}{'korvex x':>10}{'mini x':>8}")
    for label, name in PAGES:
        fns = renderers(name)
        want = normalize(fns["jinja2"]())
        for engine, fn in fns.items():
            assert normalize(fn()) == want, f"{engine} output differs on {label}"
        t = {engine: median_us(fn) for engine, fn in fns.items()}
        print(
            f"{label:8}{t['korvex']:10.1f}{t['jinja2']:10.1f}{t['minijinja']:11.1f}"
            f"{t['jinja2'] / t['korvex']:9.2f}x{t['jinja2'] / t['minijinja']:7.2f}x"
        )


def bench_escape():
    """Time escaping alone, in ns per call, on typical strings."""
    esc_re = re.compile(r"[&<>'\"]")
    samples = {
        "clean 12": "hello world!",
        "clean 80": "The quick brown fox jumps over the lazy dog, again and again and again.",
        "dirty 25": NASTY,
        "dirty 80": (NASTY + " x ") * 3,
    }
    engines = {
        "html.escape": html.escape,
        "markupsafe": lambda s: str(markupsafe.escape(s)),
        "markupsafe*": markupsafe.escape,
        "replace x5": esc_str,
        "re-guard": lambda s: esc_str(s) if esc_re.search(s) else s,
    }
    print(f"\nescape ns/call: {'':12}" + "".join(f"{k:>12}" for k in samples))
    for engine, fn in engines.items():
        row = [median_us(lambda s=s, fn=fn: fn(s)) * 1000 for s in samples.values()]
        print(f"{'':16}{engine:12}" + "".join(f"{v:12.0f}" for v in row))
    print("(* = returns Markup, no str() copy)")


if __name__ == "__main__":
    bench_pages()
    bench_escape()
