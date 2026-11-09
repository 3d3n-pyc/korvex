# Templates

Korvex renders HTML pages with its own template engine. No extra package.

```python
app = App()
app.static("/static", "static")
views = app.templates("templates")  # a folder of .html files


@app.get("/posts/{id}")
def show(request, id: int):
    post = load_post(id)
    return views.response("post.html", post=post)
```

`templates/base.html`:

```html
<!doctype html>
<title>{% block title %}Blog{% endblock %}</title>
<link rel="stylesheet" href="{{ static_url('css/app.css') }}">
<main>{% block content %}{% endblock %}</main>
{% include "footer.html" %}
```

`templates/post.html`:

```html
{% extends "base.html" %}
{% block title %}{{ post.title }}{% endblock %}
{% block content %}
  <h1>{{ post.title|upper }}</h1>
  {% if post.draft %}
    <p>Draft</p>
  {% elif post.tags|length > 2 %}
    <p>Popular</p>
  {% endif %}
  <ul>
    {% for tag in post.tags %}
      <li>{{ loop.index }}/{{ loop.length }} {{ tag }}</li>
    {% else %}
      <li>No tags</li>
    {% endfor %}
  </ul>
{% endblock %}
```

`views.response(name, **context)` renders the template and returns an HTML
response. It takes `status=` and `headers=` too. Templates are compiled the
first time they are used, then cached.

## Syntax

- `{{ expression }}` prints a value. `{# comment #}` prints nothing.
- `{% if %}`, `{% elif %}`, `{% else %}`, `{% endif %}`.
- `{% for x in items %}`, with an optional `{% else %}` for an empty list.
  `for k, v in d.items()` unpacks. Inside a loop: `loop.index` (from 1),
  `loop.index0`, `loop.first`, `loop.last`, `loop.length`.
- `{% set total = price * 3 %}`: visible from there to the end of the
  template, also after the `for` or `if` it was set in.
- `{% extends "base.html" %}` (first tag) and `{% block name %}...{% endblock %}`.
  In a child template, anything outside the blocks is ignored.
- `{% include "nav.html" %}`. The name is a string in quotes.
- `{%- ... -%}` and `{{- ... -}}` remove the whitespace before or after.

An expression can use names, `a.b`, `a[k]`, `a[1:3]`, strings, numbers,
`True`, `False`, `None`, lists, dicts, comparisons, `and`, `or`, `not`,
`in`, `+ - * / // %`, `x if cond else y`, calls, and filters
(`value|filter` or `value|filter(arg)`). `a.b` reads the key `b` of a dict,
the attribute `b` of anything else.

Not supported: macros, `is` tests other than `is None`, comprehensions,
lambdas. A syntax error names the file and the line.

## Filters

| Filter | Does |
| --- | --- |
| `safe` | Do not escape: the value is trusted HTML. |
| `escape`, `e` | Escape the value. |
| `upper`, `lower` | Change the case. |
| `default(x)` | `x` when the value is `None` or undefined. |
| `length` | Number of items or characters. |
| `join(sep)` | Join a list, escaping each item. |
| `truncate(n, end="...")` | Cut to `n` characters at a word boundary. |
| `tojson` | The value as JSON, safe in a `<script>` and in a single-quoted attribute (`data-x='{{ x|tojson }}'`), not a double-quoted one. |
| `urlencode` | Percent-encode a text, or a dict as a query string. |

Add your own, or a value every template can read:

```python
views.filters["money"] = lambda cents: f"{cents / 100:.2f} EUR"
views.globals["site_name"] = "Acme"
```

`{{ price|money }}` and `{{ site_name }}` now work. `url_for` and
`static_url` are already there:

```html
<a href="{{ url_for('show', id=post.id) }}">{{ post.title }}</a>
```

## Escaping

- Every printed value is HTML-escaped, unless it is a `Markup` (from
  `korvex`) or goes through `|safe`. `None` prints as an empty string.
- A name, key or attribute that does not exist is an error, with the file and
  line: `post.html, line 5: 'post.draft' is undefined`. Use
  `{{ post.draft|default(False) }}` when it may be missing.
- To give data to JavaScript, use `tojson`:

  ```html
  <script>const items = {{ items|tojson }};</script>
  ```

- Escaping does not make a URL safe: `href="javascript:..."` stays a
  script. Only put URLs you trust in `href` and `src`, and use
  `|urlencode` for a value in a query string:
  `<a href="/search?q={{ q|urlencode }}">`.

## Trust

Templates are code, written by you. Names and attributes that start with an
underscore are refused, but this is not a sandbox. Do not render templates
written by your users.

## Reload

```python
views = app.templates("templates", reload=True)
```

With `reload=True`, a template is compiled again when it, or a template it
extends or includes, changes on disk. Use it in development. It is off by
default: nothing checks the files on each render.

## Static files with a hash

`static_url()` gives the URL of a file served by `app.static()`, with a hash of
its content:

```html
<link rel="stylesheet" href="{{ static_url('css/app.css') }}">
<!-- /static/css/app.3f2a1c9b.css -->
```

- The server answers that URL with `Cache-Control: public, max-age=31536000, immutable`,
  because the URL changes when the file does. Browsers keep it for a year.
- The plain URL (`/static/css/app.css`) still works, without that header.
- With several `app.static()` mounts, pick one with
  `static_url('logo.svg', name="assets")`.
- In a handler, call `app.static_url("css/app.css")`. It raises
  `FileNotFoundError` for a missing file and `LookupError` for an unknown mount.

## Testing

```python
with TestClient(app) as client:
    assert "<h1>HELLO</h1>" in client.get("/posts/1").text

html = views.render("post.html", post=post)  # the page as a str
```

## Speed

4 to 6 times faster than Jinja2 in `benches/bench_templates.py`. See
[Performance](performance.md).
