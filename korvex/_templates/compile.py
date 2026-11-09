"""Compile parsed templates to one Python function each.

`compile_template(nodes, name, load, filters)` resolves `extends`, `include`
and `block` at compile time, then writes the source of
`render(context, globals) -> str` and runs it through `compile`.

Names: a name comes from the context, then from the globals, else it is
undefined. A name set by `{% set %}` or a `for` target is a plain local: it is
visible from there to the end of the template, and a `set` inside a `for` or
`if` body stays visible after it. Content outside the blocks of a template
that extends another is ignored.
"""

from __future__ import annotations

import ast
import copy
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, Callable, NamedTuple, NoReturn, Optional, cast

from korvex._core import escape_html
from korvex._templates.filters import UNDEFINED, default, escape, safe
from korvex._templates.parse import (
    Block,
    Extends,
    For,
    If,
    Include,
    Node,
    Output,
    Set,
    TemplateError,
    TemplateSyntaxError,
    Text,
)


class UndefinedError(TemplateError):
    """A template used a name, key or attribute that does not exist."""


# ---------------------------------------------------------------- run time


def _undef(name: str) -> Any:
    """Raise the error for an undefined name."""
    raise UndefinedError(f"{name!r} is undefined")


def _attr(obj: Any, key: str) -> Any:
    """Read `obj.key`: an item for a dict subclass, an attribute for anything else."""
    if isinstance(obj, dict):
        return obj[key]
    return getattr(obj, key)


def _look(obj: Any, key: str) -> Any:
    """Like `_attr`, but give UNDEFINED when it is missing."""
    if isinstance(obj, dict):
        return obj.get(key, UNDEFINED)
    return getattr(obj, key, UNDEFINED)


def _sized(items: Any) -> Any:
    """Return the iterable as it is if it has a length, else as a list."""
    return items if hasattr(items, "__len__") else list(items)


_MISSING = re.compile(r"attribute '(\w+)'")


def _missing_key(exc: Exception) -> Optional[str]:
    """Return the key or attribute a KeyError or AttributeError was about."""
    if isinstance(exc, KeyError):
        return exc.args[0] if exc.args and isinstance(exc.args[0], str) else None
    if isinstance(exc, AttributeError):
        found = _MISSING.search(str(exc))
        return found.group(1) if found else None
    return None


def _missing_name(exc: Exception, attrs: tuple) -> Optional[str]:
    """Return the `a.b` expression of the line that a missing key or attribute came from."""
    key = _missing_key(exc)
    for attr, text in attrs:
        if attr == key:
            return text
    return None


def _fail(exc: Exception, lines: list) -> None:
    """Re-raise an error from the render function with the template name and line."""
    tb = exc.__traceback__
    name, line, attrs = lines[tb.tb_lineno - 1] if tb else ("<template>", 0, ())
    where = f"{name}, line {line}: "
    missing = _missing_name(exc, attrs)
    if isinstance(exc, UndefinedError):
        error: TemplateError = UndefinedError(where + str(exc))
    elif missing:
        error = UndefinedError(f"{where}{missing!r} is undefined")
    else:
        error = TemplateError(f"{where}{type(exc).__name__}: {exc}")
    raise error from exc


def _item(obj: Any, key: Any) -> Any:
    """`obj[key]`, or UNDEFINED when the key or index is missing."""
    try:
        return obj[key]
    except (KeyError, IndexError):
        return UNDEFINED


_HELPERS: dict[str, Any] = {
    "_R": escape_html,
    "_esc": escape,
    "_str": str,
    "_item": _item,
    "_int": int,
    "_dict": dict,
    "_len": len,
    "_enum": enumerate,
    "_U": UNDEFINED,
    "_undef": _undef,
    "_attr": _attr,
    "_look": _look,
    "_sized": _sized,
}

# ---------------------------------------------------------------- resolving


@dataclass
class Inline:
    """Nodes that come from template `name`, through an include or a block."""

    name: str
    body: list


class _Scope(NamedTuple):
    """The template being resolved, the blocks that override it, and the chain that led here."""

    name: str
    overrides: dict
    stack: tuple


def _bodies(node: Any) -> list:
    """Return the node lists inside a node."""
    if isinstance(node, If):
        return [body for _, body in node.branches] + [node.orelse]
    if isinstance(node, For):
        return [node.body, node.orelse]
    if isinstance(node, (Block, Inline)):
        return [node.body]
    return []


def _blocks(nodes: list, name: str) -> dict:
    """Collect every block of a template as `{block name: (template name, body)}`."""
    found: dict = {}
    for node in nodes:
        if isinstance(node, Block):
            found[node.name] = (name, node.body)
        for body in _bodies(node):
            found.update(_blocks(body, name))
    return found


class _Resolver:
    """Follows `extends` and inlines `include` and `block`."""

    def __init__(self, load: Callable[[str], list]) -> None:
        self.load = load
        self.used: set = set()

    def enter(self, target: str, scope: _Scope, line: int) -> _Scope:
        """Record a template as used and return the scope for it, refusing a loop."""
        if target in scope.stack:
            raise TemplateSyntaxError(f"template {target!r} includes or extends itself", scope.name, line)
        self.used.add(target)
        return _Scope(target, scope.overrides, scope.stack + (target,))

    def template(self, nodes: list, scope: _Scope) -> list:
        """Resolve a whole template: climb to the base template and inline everything."""
        ext = next((n for n in nodes if isinstance(n, Extends)), None)
        if ext is None:
            return self.expand(nodes, scope)
        parent = self.enter(ext.template, scope, ext.line)
        merged = {**_blocks(nodes, scope.name), **scope.overrides}
        return self.template(self.load(ext.template), parent._replace(overrides=merged))

    def expand(self, nodes: list, scope: _Scope) -> list:
        """Resolve the blocks and includes of a node list."""
        return [self.node(node, scope) for node in nodes]

    def node(self, node: Any, scope: _Scope) -> Any:
        """Resolve one node."""
        if isinstance(node, Block):
            name, body = scope.overrides.get(node.name, (scope.name, node.body))
            return Inline(name, self.expand(body, scope._replace(name=name)))
        if isinstance(node, Include):
            inner = self.enter(node.template, scope, node.line)._replace(overrides={})
            return Inline(node.template, self.template(self.load(node.template), inner))
        if isinstance(node, If):
            branches = [(cond, self.expand(body, scope)) for cond, body in node.branches]
            return replace(node, branches=branches, orelse=self.expand(node.orelse, scope))
        if isinstance(node, For):
            return replace(node, body=self.expand(node.body, scope), orelse=self.expand(node.orelse, scope))
        return node


# ---------------------------------------------------------------- expressions

_LOOP = {
    "index": "_i{k}",
    "index0": "(_i{k} - 1)",
    "first": "(_i{k} == 1)",
    "last": "(_i{k} == _n{k})",
    "length": "_n{k}",
}


def _expr(src: str) -> ast.expr:
    """Parse a snippet of generated code into an expression."""
    return ast.parse(src, mode="eval").body


def _is_loop(node: ast.AST) -> bool:
    """Tell whether the node is the name `loop`."""
    return isinstance(node, ast.Name) and node.id == "loop"


class _Expr(ast.NodeTransformer):
    """Rewrites one template expression into plain Python."""

    def __init__(self, gen: _Gen, line: int) -> None:
        self.gen = gen
        self.line = line
        self.attrs: list = []

    def fail(self, message: str) -> NoReturn:
        """Raise a syntax error at this expression."""
        raise TemplateSyntaxError(message, self.gen.name, self.line)

    def in_loop(self, node: ast.AST) -> bool:
        """Tell whether the node is `loop` while inside a `for` body."""
        return _is_loop(node) and bool(self.gen.loops)

    def visit_Name(self, node: ast.Name) -> ast.expr:  # noqa: N802
        """A name read from the context, checked for being defined."""
        if self.in_loop(node):
            self.fail("use loop.index, loop.index0, loop.first, loop.last or loop.length")
        return _expr(self.gen.ref(node.id, True))

    def loop_attr(self, node: ast.Attribute) -> ast.expr:
        """`loop.index` and the like become plain locals."""
        template = _LOOP.get(node.attr)
        if template is None:
            self.fail(f"loop has no attribute {node.attr!r}")
        return _expr(template.format(k=self.gen.loops[-1]))

    def visit_Attribute(self, node: ast.Attribute) -> ast.expr:  # noqa: N802
        """`a.b`: an item if `a` is a dict, else an attribute."""
        if self.in_loop(node.value):
            return self.loop_attr(node)
        self.attrs.append((node.attr, ast.unparse(node)))
        a, base = repr(node.attr), node.value
        if isinstance(base, ast.Name) and base.id in self.gen.known:
            return _expr(f"({base.id}[{a}] if {base.id}.__class__ is _dict else _attr({base.id}, {a}))")
        value = ast.unparse(self.visit(base))
        return _expr(f"(_b[{a}] if (_b := {value}).__class__ is _dict else _attr(_b, {a}))")

    def visit_Call(self, node: ast.Call) -> ast.expr:  # noqa: N802
        """A call; `a.b(...)` reads the method as an attribute, even on a dict."""
        func = node.func
        if not isinstance(func, ast.Attribute):
            return cast(ast.expr, self.generic_visit(node))
        self.attrs.append((func.attr, ast.unparse(func)))
        func.value = self.visit(func.value)
        node.args = [self.visit(arg) for arg in node.args]
        node.keywords = [self.visit(kw) for kw in node.keywords]
        return node

    def lenient(self, node: ast.expr) -> ast.expr:
        """Compile so that a missing name or attribute gives UNDEFINED, not an error."""
        if isinstance(node, ast.Name) and not self.in_loop(node):
            return _expr(self.gen.ref(node.id, False))
        if isinstance(node, ast.Subscript):
            return _expr(f"_item({ast.unparse(self.visit(node.value))}, {ast.unparse(self.visit(node.slice))})")
        if not isinstance(node, ast.Attribute) or self.in_loop(node.value):
            return self.visit(node)
        a, base = repr(node.attr), self.lenient(node.value)
        if isinstance(base, ast.Name):
            n = base.id
            return _expr(f"({n}.get({a}, _U) if {n}.__class__ is _dict else _look({n}, {a}))")
        return _expr(f"(_b.get({a}, _U) if (_b := {ast.unparse(base)}).__class__ is _dict else _look(_b, {a}))")

    def visit_BinOp(self, node: ast.BinOp) -> ast.expr:  # noqa: N802
        """`|` is the filter pipe; any other operator is left alone."""
        if not isinstance(node.op, ast.BitOr):
            return cast(ast.expr, self.generic_visit(node))
        call = node.right if isinstance(node.right, ast.Call) else None
        name = (call.func if call else node.right).id  # type: ignore[union-attr]
        if name not in self.gen.filters:
            self.fail(f"unknown filter {name!r}")
        args = [self.visit(arg) for arg in call.args] if call else []
        keywords = [self.visit(kw) for kw in call.keywords] if call else []
        value = self.lenient(node.left) if name == "default" else self.visit(node.left)
        if name == "default" and len(args) == 1 and not keywords and self.gen.filters[name] is default:
            fallback = ast.unparse(args[0])
            return _expr(f"(_d if (_d := {ast.unparse(value)}) is not _U and _d is not None else {fallback})")
        self.gen.used_filters.add(name)
        return ast.Call(ast.Name(f"_f_{name}", ast.Load()), [value, *args], keywords)


# ---------------------------------------------------------------- code generation


class _Line(NamedTuple):
    """One line of generated code and the template line it comes from."""

    text: str
    name: str
    line: int
    attrs: tuple


@dataclass
class _Piece:
    """One part of the output: literal text or the code of a value."""

    name: str
    line: int
    attrs: tuple = ()
    text: Optional[str] = None
    expr: str = ""

    def code(self) -> str:
        """Return the code that gives this piece."""
        return repr(self.text) if self.text is not None else self.expr


def _exprs(node: Any) -> list:
    """Return the expressions of a node, not those of its body."""
    if isinstance(node, Output):
        return [node.expr]
    if isinstance(node, Set):
        return [node.value]
    if isinstance(node, If):
        return [cond for cond, _ in node.branches]
    return [node.iterable] if isinstance(node, For) else []


def _loop_uses(nodes: list) -> set:
    """Return the `loop.x` attributes a `for` body uses, leaving out its inner loops' bodies."""
    found: set = set()
    for node in nodes:
        for expr in _exprs(node):
            found.update(n.attr for n in ast.walk(expr) if isinstance(n, ast.Attribute) and _is_loop(n.value))
        found.update(*(_loop_uses(b) for b in ([node.orelse] if isinstance(node, For) else _bodies(node))))
    return found


class _Gen:
    """Writes the source of one render function."""

    def __init__(self, name: str, filters: Mapping[str, Callable[..., Any]]) -> None:
        self.name = name
        self.filters = filters
        self.used_filters: set = set()
        self.lines: list = []
        self.queue: list = []
        self.free: dict = {}
        self.known: set = set()
        self.loops: list = []
        self.depth = 2
        self.count = 0

    def ref(self, name: str, strict: bool) -> str:
        """Return the code that reads a name; a context name is checked unless `strict` is False."""
        if name in self.known:
            return name
        self.free[name] = None
        return f"({name} if {name} is not _U else _undef({name!r}))" if strict else name

    def expr(self, expr: ast.expr, line: int) -> tuple:
        """Compile an expression to code and the `a.b` names it reads."""
        rewriter = _Expr(self, line)
        tree = rewriter.visit(copy.deepcopy(expr))
        return ast.unparse(tree), tuple(rewriter.attrs)

    def add(self, text: str, name: str, line: int, attrs: tuple = ()) -> None:
        """Add one line of code at the current depth."""
        self.lines.append(_Line("    " * self.depth + text, name, line, attrs))

    def line(self, text: str, line: int, attrs: tuple = ()) -> None:
        """Add a statement, after the output pieces queued before it."""
        self.flush()
        self.add(text, self.name, line, attrs)

    def flush(self) -> None:
        """Write the queued pieces as one `append` or one grouped `extend`."""
        pieces, self.queue = self.queue, []
        if not pieces:
            return
        if len(pieces) == 1:
            self.add(f"_append({pieces[0].code()})", pieces[0].name, pieces[0].line, pieces[0].attrs)
            return
        first = pieces[0]
        self.add("_extend((", first.name, first.line)
        self.depth += 1
        for piece in pieces:
            self.add(piece.code() + ",", piece.name, piece.line, piece.attrs)
        self.depth -= 1
        self.add("))", first.name, first.line)

    def queue_text(self, text: str, line: int) -> None:
        """Queue literal text, joined to the text before it."""
        if self.queue and self.queue[-1].text is not None:
            self.queue[-1].text += text
        else:
            self.queue.append(_Piece(self.name, line, text=text))

    def block(self, nodes: list, known: set, lead: Optional[tuple] = None) -> None:
        """Write an indented body with the given known names; `lead` is a first statement."""
        outer, self.known = self.known, known
        self.depth += 1
        start = len(self.lines)
        if lead:
            self.line(*lead)
        self.nodes(nodes)
        self.flush()
        if len(self.lines) == start:
            self.add("pass", self.name, 0)
        self.depth -= 1
        self.known = outer

    def nodes(self, nodes: list) -> None:
        """Write a list of nodes."""
        for node in nodes:
            getattr(self, "gen_" + type(node).__name__.lower())(node)

    def gen_text(self, node: Text) -> None:
        """Literal text."""
        self.queue_text(node.text, node.line)

    def gen_inline(self, node: Inline) -> None:
        """Nodes of another template, written under that template's name."""
        outer, self.name = self.name, node.name
        self.nodes(node.body)
        self.name = outer

    def gen_output(self, node: Output) -> None:
        """`{{ expr }}`, escaped; `|safe` last, a constant, and loop numbers skip the escaping."""
        expr = node.expr
        if isinstance(expr, ast.Constant):
            return self.queue_text(escape(expr.value), node.line)
        plain = isinstance(expr, ast.Attribute) and self.loops and _is_loop(expr.value)
        if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.BitOr):
            right = expr.right
            if isinstance(right, ast.Name) and right.id == "safe" and self.filters.get("safe") is safe:
                expr, plain = expr.left, True
        src, attrs = self.expr(expr, node.line)
        if plain:
            code = f"_str({src})"
        else:
            code = f"_R(_v) if (_c := (_v := {src}).__class__) is _str else _str(_v) if _c is _int else _esc(_v)"
        self.queue.append(_Piece(self.name, node.line, attrs, expr=code))

    def gen_set(self, node: Set) -> None:
        """`{% set name = value %}`."""
        src, attrs = self.expr(node.value, node.line)
        self.line(f"{node.name} = {src}", node.line, attrs)
        self.known.add(node.name)

    def gen_if(self, node: If) -> None:
        """`if`, `elif` and `else`."""
        for i, (cond, body) in enumerate(node.branches):
            src, attrs = self.expr(cond, node.line)
            self.line(("if " if i == 0 else "elif ") + src + ":", node.line, attrs)
            self.block(body, set(self.known))
        if node.orelse:
            self.line("else:", node.line)
            self.block(node.orelse, set(self.known))

    def gen_for(self, node: For) -> None:
        """`for` with an optional `else`; `loop` is plain locals, and only when the body uses it."""
        self.count += 1
        k, uses = self.count, _loop_uses(node.body)
        counted = bool(uses & {"last", "length"})
        src, attrs = self.expr(node.iterable, node.line)
        self.line(f"_it{k} = " + (f"_sized({src})" if counted else src), node.line, attrs)
        if counted:
            self.line(f"_n{k} = _len(_it{k})", node.line)
        if node.orelse:
            self.line(f"_e{k} = True", node.line)
        names = ", ".join(node.targets)
        head = f"for _i{k}, ({names}) in _enum(_it{k}, 1):" if uses else f"for {names} in _it{k}:"
        self.line(head, node.line)
        self.loops.append(k)
        self.block(node.body, self.known | set(node.targets), (f"_e{k} = False", node.line) if node.orelse else None)
        self.loops.pop()
        if node.orelse:
            self.line(f"if _e{k}:", node.line)
            self.block(node.orelse, set(self.known))

    def source(self, root: str) -> tuple:
        """Return the full source of `render` and its generated-line to template-line map."""
        hot = [*_HELPERS, *(f"_f_{n}" for n in sorted(self.used_filters))]
        head = [
            f"def render(_ctx, _glb, {', '.join(f'{n}={n}' for n in hot)}):",
            "    try:",
            "        _parts = []; _append = _parts.append; _extend = _parts.extend",
        ]
        for name in self.free:
            head += [
                f"        {name} = _ctx.get({name!r}, _U)",
                f"        if {name} is _U: {name} = _glb.get({name!r}, _U)",
            ]
        tail = ['        return "".join(_parts)', "    except Exception as _e:", "        _fail(_e, _L)"]
        lines = [_Line(text, root, 1, ()) for text in head] + self.lines + [_Line(t, root, 1, ()) for t in tail]
        return "\n".join(line.text for line in lines), [(ln.name, ln.line, ln.attrs) for ln in lines]


def compile_template(
    nodes: list[Node],
    name: str,
    load: Callable[[str], list[Node]],
    filters: Mapping[str, Callable[..., Any]],
) -> tuple[Callable[[dict, dict], str], set]:
    """Compile a template to `render(context, globals) -> str`; also return the names of the templates it uses."""
    resolver = _Resolver(load)
    gen = _Gen(name, filters)
    gen.nodes(resolver.template(nodes, _Scope(name, {}, (name,))))
    gen.flush()
    source, lines = gen.source(name)
    namespace: dict = {**_HELPERS, "_fail": _fail, "_L": lines}
    namespace.update({f"_f_{n}": filters[n] for n in gen.used_filters})
    exec(compile(source, f"<template {name}>", "exec"), namespace)
    return namespace["render"], resolver.used
