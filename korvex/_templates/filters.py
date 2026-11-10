"""Escaping and the built-in template filters."""

from __future__ import annotations

import json
from typing import Any, Callable
from urllib.parse import quote
from urllib.parse import urlencode as _urlencode

from korvex._core import escape_html

UNDEFINED: Any = object()
"""Marker the compiler uses for a name the template context does not have."""


class Markup(str):
    """Text known to be safe HTML, so escaping leaves it as is; any str operation on it gives a plain str."""

    __slots__ = ()

    def __repr__(self) -> str:
        """Show the value as `Markup('...')`."""
        return f"Markup({str.__repr__(self)})"


def escape(value: Any) -> str:
    """Return the value as text that is safe in HTML."""
    if type(value) is str:
        return escape_html(value)
    if isinstance(value, Markup):
        return value
    if value is None:
        return ""
    if value is True or value is False:
        return "True" if value else "False"
    if type(value) is int or type(value) is float:
        return str(value)
    html = getattr(value, "__html__", None)
    if html is not None:
        return str(html())
    return escape_html(str(value))


def safe(value: Any) -> Markup:
    """Mark the value as safe HTML, so it is not escaped."""
    return value if isinstance(value, Markup) else Markup(value)


def escape_filter(value: Any) -> Markup:
    """Escape the value for HTML."""
    return Markup(escape(value))


def upper(value: Any) -> str:
    """Convert the text to upper case."""
    return str(value).upper()


def lower(value: Any) -> str:
    """Convert the text to lower case."""
    return str(value).lower()


def default(value: Any, fallback: Any = "") -> Any:
    """Use the fallback when the value is missing or None."""
    return fallback if value is None or value is UNDEFINED else value


def length(value: Any) -> int:
    """Return the number of items, or characters in a text."""
    return len(value)


def join(items: Any, sep: Any = "") -> Markup:
    """Join the items with a separator, escaping each unless it is safe."""
    return Markup(escape(sep).join([escape(item) for item in items]))


def truncate(text: Any, length: int = 255, end: str = "...") -> str:
    """Cut the text to at most `length` characters at a word boundary, then add `end`."""
    text = str(text)
    if len(text) <= length:
        return text
    cut = text[: max(length - len(end), 0)]
    return cut.rsplit(" ", 1)[0] + end if " " in cut else cut + end


_JSON_SAFE = {"<": "\\u003c", ">": "\\u003e", "&": "\\u0026", "'": "\\u0027"}


def tojson(value: Any) -> Markup:
    """Write the value as JSON that is safe in a script tag or a single-quoted attribute (`data-x='{{ x|tojson }}'`), not a double-quoted one."""
    text = json.dumps(value, separators=(",", ":"))
    for char, code in _JSON_SAFE.items():
        text = text.replace(char, code)
    return Markup(text)


def urlencode(value: Any) -> str:
    """Percent-encode a text, or a dict or list of pairs as a query string."""
    if isinstance(value, (dict, list, tuple)):
        return _urlencode(value)
    return quote(str(value), safe="/")


FILTERS: dict[str, Callable[..., Any]] = {
    "safe": safe,
    "escape": escape_filter,
    "e": escape_filter,
    "upper": upper,
    "lower": lower,
    "default": default,
    "length": length,
    "join": join,
    "truncate": truncate,
    "tojson": tojson,
    "urlencode": urlencode,
}
