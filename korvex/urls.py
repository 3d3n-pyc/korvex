"""Reverse URLs: the path (or the full URL) of a route, from its name and
its parameters. See `App.url_for()` and `Request.url_for()`."""

from __future__ import annotations

import datetime
import re
import uuid
from typing import TYPE_CHECKING, Any, NamedTuple
from urllib.parse import quote

from korvex._core import MethodNotAllowed
from korvex._core import Router as _CoreRouter
from korvex.schema import _TYPE_NAMES, _convert

if TYPE_CHECKING:
    from korvex.app import App, Request, _Host
    from korvex.router import Router

_PARAM = re.compile(r"\{(\w+)(?::(\w+))?\}")
_WILDCARD = re.compile(r"\*(\w+)")
_INT = re.compile(r"[+-]?[0-9]+")
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_LABEL = re.compile(r"[A-Za-z0-9_-]+")  # one label of a host name
_HOST = re.compile(r"(?:\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9_.-]+)(?::[0-9]{1,5})?")  # a Host header
_LITERAL_SAFE = "!$&'()*+,;=:@"  # left as is in a pattern's literal segments


class Target(NamedTuple):
    """A route a name can point to."""

    name: str  # the name it's known by
    key: str  # its handler name in the router
    websocket: bool
    method: str
    pattern: str  # its path, as the router lists it
    host: Any  # the app.host() it's on (`_Host`), or None
    router: Router  # where it's registered, to check a path reaches it


class Index(NamedTuple):
    """Every name, with the routes it points to; explicit names (`name=`)
    take precedence over function names."""

    explicit: dict[str, list[Target]]
    default: dict[str, list[Target]]
    by_key: dict[tuple[bool, int, str], Target]  # (websocket, id of its router, key) -> route


def build_index(app: App) -> Index:
    explicit: dict[str, list[Target]] = {}
    default: dict[str, list[Target]] = {}
    by_key: dict[tuple[bool, int, str], Target] = {}
    sources: list[tuple[Router, bool, _Host | None]] = [(app.router, False, None), (app._ws_router, True, None)]
    for host in app._hosts:
        sources += [(host.router, False, host), (host.ws_router, True, host)]
    for router, websocket, host in sources:
        for pattern, method, key, _ in router.routes():
            found = app._route_names.get((websocket, key))
            if found is None:
                if websocket or key.startswith("korvex."):
                    continue  # the docs, and what has no name
                found = (key, False)  # a route added to app.router directly: its handler name
            name, is_explicit = found
            target = Target(name, key, websocket, method, pattern, host, router)
            by_key[(websocket, id(router), key)] = target
            (explicit if is_explicit else default).setdefault(name, []).append(target)
    return Index(explicit, default, by_key)


def lookup(index: Index, name: str) -> Target:
    """The route `name` points to. Raises LookupError for an unknown name,
    or one that points to routes with different paths."""
    targets = index.explicit.get(name) or index.default.get(name)
    if not targets:
        raise LookupError(f"no route named {name!r}")
    places = {(t.websocket, id(t.host), t.pattern) for t in targets}
    if len(places) > 1:
        listed = ", ".join(sorted({_describe(t) for t in targets}))
        raise LookupError(f"{name!r} names several routes ({listed}): give them each a name= to tell them apart")
    return targets[0]


def path_for(app: App, target: Target, params: dict[str, Any], *, host_given: bool = False) -> str:
    """The path of `target` with `params`, each checked and escaped.
    `host_given`: the host's parameters were taken out of `params` by the
    caller (`request.url_for`)."""
    expected = [m.group(1) or m.group(2) for m in re.finditer(r"\{(\w+)(?::\w+)?\}|\*(\w+)", target.pattern)]
    missing = [p for p in expected if p not in params]
    extra = [p for p in params if p not in expected]
    if missing or extra:
        problems = []
        if missing:
            problems.append(f"missing {', '.join(missing)}")
        if extra:
            hint = ""
            if target.host is not None and not host_given and set(extra) & set(target.host.params):
                hint = " (a host parameter: request.url_for() gives the full URL, host included)"
            problems.append(f"unexpected {', '.join(extra)}{hint}")
        raise ValueError(f"url_for({target.name!r}): {'; '.join(problems)}, for {_describe(target)}")

    types = _annotated(app, target)
    encoded, decoded = [], []
    for segment in target.pattern.strip("/").split("/") if target.pattern.strip("/") else []:
        param, wildcard = _PARAM.fullmatch(segment), _WILDCARD.fullmatch(segment)
        if param is None and wildcard is None:
            encoded.append(quote(segment, safe=_LITERAL_SAFE))
            decoded.append(segment)
            continue
        key = param.group(1) if param else wildcard.group(1)  # type: ignore[union-attr]
        text = _text(target, key, params[key])
        parts = text.split("/") if wildcard else [text]
        for part in parts:
            if part in ("", ".", ".."):
                what = "an empty part" if part == "" else repr(part)
                raise ValueError(f"url_for({target.name!r}): {key}={text!r} has {what}, which can't be part of a path")
        if param is not None:
            if "/" in text:
                raise ValueError(f"url_for({target.name!r}): {key}={text!r} has a '/': only a *wildcard can")
            _check_constraint(target, key, text, param.group(2))
        _check_type(target, key, text, types.get(key))
        encoded.append("/".join(quote(part, safe="") for part in parts))
        decoded.append(text)
    path = "/" + "/".join(decoded)
    _check_reached(app, target, path)
    return "/" + "/".join(encoded)


def url_for(request: Request, target: Target, params: dict[str, Any]) -> str:
    """The full URL of `target`: the request's scheme (`ws`/`wss` for a
    WebSocket route), the route's host (or the request's), the path."""
    app = request.app
    assert app is not None
    secure = request.scheme in ("https", "wss")
    scheme = ("wss" if secure else "ws") if target.websocket else ("https" if secure else "http")
    header = request.headers.get("host") or ""
    if not _HOST.fullmatch(header):
        raise ValueError(f"url_for({target.name!r}): the request's Host header {header!r} isn't a host name")
    port = header.rsplit(":", 1)[1] if ":" in header and not header.endswith("]") else ""
    params = dict(params)
    if target.host is None:
        netloc = header
    else:
        labels = []
        types = _annotated(app, target)
        missing = [p for p in target.host.params if p not in params]
        if missing:
            raise ValueError(
                f"url_for({target.name!r}): missing {', '.join(missing)}, for the host {target.host.pattern!r}"
            )
        for is_param, label in target.host._labels:
            if not is_param:
                labels.append(label)
                continue
            text = _text(target, label, params.pop(label))
            if not _LABEL.fullmatch(text):
                raise ValueError(f"url_for({target.name!r}): {label}={text!r} isn't one label of a host name")
            _check_type(target, label, text, types.get(label))
            labels.append(text)
        netloc = ".".join(labels) + (f":{port}" if port else "")
    return f"{scheme}://{netloc}{path_for(app, target, params, host_given=True)}"


def _text(target: Target, key: str, value: Any) -> str:
    """A parameter's value as text, the way the route reads it back."""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):  # before int: a bool is an int too
        return "true" if value else "false"
    if isinstance(value, (int, float, uuid.UUID)):
        return str(value)
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    raise TypeError(
        f"url_for({target.name!r}): {key} can't be of type {type(value).__name__}; "
        "give a str, int, float, bool, UUID, date or datetime"
    )


def _check_constraint(target: Target, key: str, text: str, constraint: str | None) -> None:
    """The built-in constraints, checked here for a clear message; a custom
    one is checked when the path is matched."""
    if constraint == "int" and not (_INT.fullmatch(text) and -(2**63) <= int(text) < 2**63):
        raise ValueError(f"url_for({target.name!r}): {key}={text!r} isn't an integer, for {{{key}:int}}")
    if constraint == "uuid" and not _UUID.fullmatch(text):
        raise ValueError(f"url_for({target.name!r}): {key}={text!r} isn't a UUID, for {{{key}:uuid}}")


def _check_type(target: Target, key: str, text: str, tp: Any) -> None:
    """The value converts back to the type the handler (or a dependency)
    annotated it with: the link wouldn't get a 422."""
    if tp is None or tp is str:
        return
    try:
        _convert(text, tp)
    except (ValueError, TypeError):
        raise ValueError(f"url_for({target.name!r}): {key}={text!r} isn't a valid {_TYPE_NAMES.get(tp, tp)}") from None


def _annotated(app: App, target: Target) -> dict[str, Any]:
    """The types the route's handler and dependencies give its path (and
    host) parameters."""
    plans = app._ws_plans if target.websocket else app._plans
    plan = plans.get(target.key)
    if plan is None:
        return {}
    return {p.key: p.inner for p in plan.everything() if p.source == "path"}


def _check_reached(app: App, target: Target, path: str) -> None:
    """Matches `path` the way a request would be: it must reach `target`,
    not fail a constraint or reach another route that answers it first."""
    try:
        found = _CoreRouter.match(target.router, path, target.method)  # not the cache: links don't pollute it
    except MethodNotAllowed:
        found = None
    if found is None:
        raise ValueError(
            f"url_for({target.name!r}): {path!r} doesn't fit {_describe(target)}: a value fails its constraint"
        )
    if found[0] != target.key:
        other = app._url_index().by_key.get((target.websocket, id(target.router), found[0]))
        which = f"{other.name!r} ({_describe(other)})" if other else repr(found[0])
        raise ValueError(f"url_for({target.name!r}): {path!r} would reach route {which} instead")


def _describe(target: Target) -> str:
    host = f"{target.host.pattern} " if target.host is not None else ""
    kind = "WebSocket" if target.websocket else target.method
    return f"{kind} {host}{target.pattern}"
