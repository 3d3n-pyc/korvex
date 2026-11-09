"""Template source to a tree of nodes, each with its line number.

`parse(source, name)` returns a list of nodes. Expressions are checked
against an allow-list and kept as `ast.expr` for the compiler.
"""

from __future__ import annotations

import ast
import keyword
import re
from dataclasses import dataclass
from typing import NamedTuple, Union


class TemplateError(Exception):
    """Base class of every template error."""


class TemplateSyntaxError(TemplateError):
    """The template is invalid; `str()` reads `name, line N: message`."""

    def __init__(self, message: str, name: str, line: int) -> None:
        self.message = message
        self.name = name
        self.line = line
        super().__init__(f"{name}, line {line}: {message}")


# ---------------------------------------------------------------- nodes


@dataclass
class Text:
    """Literal text, output as it is."""

    text: str
    line: int


@dataclass
class Output:
    """`{{ expr }}`: `expr` is the validated expression."""

    expr: ast.expr
    line: int


@dataclass
class If:
    """`if`/`elif`/`else`: `branches` are (condition, body) pairs; `orelse` may be empty."""

    branches: list[tuple[ast.expr, list[Node]]]
    orelse: list[Node]
    line: int


@dataclass
class For:
    """`for targets in iterable`, with an optional `else` body for an empty loop."""

    targets: tuple[str, ...]
    iterable: ast.expr
    body: list[Node]
    orelse: list[Node]
    line: int


@dataclass
class Set:
    """`{% set name = value %}`."""

    name: str
    value: ast.expr
    line: int


@dataclass
class Extends:
    """`{% extends "template" %}`: `template` is the literal name."""

    template: str
    line: int


@dataclass
class Include:
    """`{% include "template" %}`: `template` is the literal name."""

    template: str
    line: int


@dataclass
class Block:
    """`{% block name %}body{% endblock %}`."""

    name: str
    body: list[Node]
    line: int


Node = Union[Text, Output, If, For, Set, Extends, Include, Block]

# ---------------------------------------------------------------- expressions

_FIELDS = {
    ast.Name: (),
    ast.Constant: (),
    ast.Attribute: ("value",),
    ast.Subscript: ("value", "slice"),
    ast.Slice: ("lower", "upper", "step"),
    ast.List: ("elts",),
    ast.Tuple: ("elts",),
    ast.Dict: ("keys", "values"),
    ast.BoolOp: ("values",),
    ast.UnaryOp: ("operand",),
    ast.BinOp: ("left", "right"),
    ast.Compare: ("left", "comparators"),
    ast.IfExp: ("test", "body", "orelse"),
    ast.Call: ("func", "args", "keywords"),
    ast.keyword: ("value",),
}  # fmt: skip
_UNARY = (ast.Not, ast.USub, ast.UAdd)
_BINARY = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod)
_COMPARE = (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn, ast.Is, ast.IsNot)
_CONSTANTS = (str, int, float, bool, type(None))


class _Checker:
    """Walks an expression and refuses anything outside the allow-list."""

    def __init__(self, name: str, line: int) -> None:
        self.name = name
        self.line = line

    def fail(self, message: str) -> None:
        """Raise a syntax error at the checker's line."""
        raise TemplateSyntaxError(message, self.name, self.line)

    def check(self, node: ast.AST) -> None:
        """Check a node, then its children."""
        kind = type(node)
        if kind not in _FIELDS:
            self.fail(f"{kind.__name__} is not allowed in expressions")
        rule = getattr(self, f"_rule_{kind.__name__}", None)
        if rule:
            rule(node)
        for field in _FIELDS[kind]:
            value = getattr(node, field)
            for child in value if isinstance(value, list) else [value]:
                if child is not None:
                    self.check(child)

    def _rule_Name(self, node: ast.Name) -> None:
        """Refuse names starting with an underscore."""
        if node.id.startswith("_"):
            self.fail(f"name {node.id!r} is not allowed: it starts with an underscore")

    def _rule_Attribute(self, node: ast.Attribute) -> None:
        """Refuse attributes starting with an underscore."""
        if node.attr.startswith("_"):
            self.fail(f"attribute {node.attr!r} is not allowed: it starts with an underscore")

    def _rule_Constant(self, node: ast.Constant) -> None:
        """Allow only strings, numbers, booleans and None."""
        if type(node.value) not in _CONSTANTS:
            self.fail("this kind of constant is not allowed")

    def _rule_UnaryOp(self, node: ast.UnaryOp) -> None:
        """Allow `not`, `-` and `+`."""
        if not isinstance(node.op, _UNARY):
            self.fail(f"operator {type(node.op).__name__} is not allowed")

    def _rule_BinOp(self, node: ast.BinOp) -> None:
        """Allow arithmetic; `|` is the filter pipe and needs a name or a call on a name."""
        if not isinstance(node.op, ast.BitOr):
            if not isinstance(node.op, _BINARY):
                self.fail(f"operator {type(node.op).__name__} is not allowed")
            return
        right = node.right
        if isinstance(right, ast.Call):
            right = right.func
        if not isinstance(right, ast.Name):
            self.fail("a filter must be a name or a call like name(args)")

    def _rule_Compare(self, node: ast.Compare) -> None:
        """Allow comparisons; `is` only against None."""
        for op, other in zip(node.ops, node.comparators):
            if not isinstance(op, _COMPARE):
                self.fail(f"operator {type(op).__name__} is not allowed")
            if isinstance(op, (ast.Is, ast.IsNot)) and not _is_none(other):
                self.fail("`is` only works with None")

    def _rule_Dict(self, node: ast.Dict) -> None:
        """Refuse `**` unpacking in a dict."""
        if None in node.keys:
            self.fail("`**` is not allowed in expressions")

    def _rule_Call(self, node: ast.Call) -> None:
        """Refuse `**kwargs` in a call (`*args` is a Starred, refused on its own)."""
        if any(k.arg is None for k in node.keywords):
            self.fail("`**` is not allowed in expressions")


def _is_none(node: ast.expr) -> bool:
    """Tell whether the node is the constant None."""
    return isinstance(node, ast.Constant) and node.value is None


def parse_expression(src: str, name: str, line: int) -> ast.expr:
    """Parse and validate one expression."""
    try:
        tree = ast.parse(src.strip(), mode="eval")
    except SyntaxError as exc:
        raise TemplateSyntaxError(f"bad expression {src.strip()!r}: {exc.msg}", name, line) from None
    except (ValueError, RecursionError):
        raise TemplateSyntaxError("bad expression: cannot be parsed", name, line) from None
    try:
        _Checker(name, line).check(tree.body)
    except RecursionError:
        raise TemplateSyntaxError("expression is nested too deeply", name, line) from None
    return tree.body


# ---------------------------------------------------------------- tokens

_OPEN = re.compile(r"\{[{%]|\{#.*?#\}", re.S)
_CLOSERS = {"{{": "}}", "{%": "%}"}
_OPENER = re.compile(r"\{[{%#]")
_KINDS = {"{%": "tag", "{{": "expr", "{#": "comment"}


@dataclass
class _Token:
    """One piece of the source: text, tag, expr or comment."""

    kind: str
    text: str
    line: int
    strip_left: bool = False
    strip_right: bool = False


def _markup_token(raw: str, line: int) -> _Token:
    """Build a token from `{% ... %}`, `{{ ... }}` or `{# ... #}`, reading the `-` marks."""
    inner = raw[2:-2]
    left = inner.startswith("-")
    inner = inner[1:] if left else inner
    right = inner.endswith("-")
    inner = inner[:-1] if right else inner
    return _Token(_KINDS[raw[:2]], inner.strip(), line, left, right)


def _add_text(tokens: list[_Token], src: str, start: int, end: int, name: str) -> None:
    """Add the text between two markers, refusing an opener that never closes."""
    text = src[start:end]
    if not text:
        return
    bad = _OPENER.search(text)
    if bad:
        line = src.count("\n", 0, start + bad.start()) + 1
        raise TemplateSyntaxError(f"unclosed {bad.group()!r}", name, line)
    tokens.append(_Token("text", text, src.count("\n", 0, start) + 1))


def _lstrip(token: _Token) -> None:
    """Strip leading whitespace of a text token, keeping its line right."""
    stripped = token.text.lstrip()
    token.line += token.text[: len(token.text) - len(stripped)].count("\n")
    token.text = stripped


def _apply_strips(tokens: list[_Token]) -> None:
    """Apply the `-` whitespace control to the text tokens around each marker."""
    for i, token in enumerate(tokens):
        if token.kind == "text":
            continue
        if token.strip_left and i > 0 and tokens[i - 1].kind == "text":
            tokens[i - 1].text = tokens[i - 1].text.rstrip()
        if token.strip_right and i + 1 < len(tokens) and tokens[i + 1].kind == "text":
            _lstrip(tokens[i + 1])


def _find_close(src: str, start: int, closer: str) -> int:
    """Index just past the `closer` that ends a tag, skipping quotes and nested braces; -1 if none."""
    depth, quote, i = 0, "", start
    while i < len(src):
        ch = src[i]
        if quote:
            i += 2 if ch == "\\" else 1
            quote = "" if ch == quote else quote
            continue
        if ch in "\"'":
            quote = ch
        elif ch == "{":
            depth += 1
        elif depth == 0 and src.startswith(closer, i):
            return i + 2
        elif depth and ch == "}":
            depth -= 1
        elif ch == "-" and depth == 0 and src.startswith(closer, i + 1):
            return i + 3
        i += 1
    return -1


def _next_markup(src: str, pos: int) -> tuple[int, int] | None:
    """Span of the next complete tag, expression or comment at or after `pos`."""
    for opener in _OPEN.finditer(src, pos):
        if opener.group().startswith("{#"):
            return opener.start(), opener.end()
        end = _find_close(src, opener.end(), _CLOSERS[opener.group()])
        if end != -1:
            return opener.start(), end
        return None
    return None


def _tokenize(src: str, name: str) -> list[_Token]:
    """Split the source into tokens; comments and empty text are dropped."""
    tokens: list[_Token] = []
    pos = 0
    while (span := _next_markup(src, pos)) is not None:
        start, end = span
        _add_text(tokens, src, pos, start, name)
        tokens.append(_markup_token(src[start:end], src.count("\n", 0, start) + 1))
        pos = end
    _add_text(tokens, src, pos, len(src), name)
    _apply_strips(tokens)
    return [t for t in tokens if t.kind != "comment" and (t.kind != "text" or t.text)]


# ---------------------------------------------------------------- parser

_FOR = re.compile(r"(.+?)\s+in\s+(.+)", re.S)
_SET = re.compile(r"(\S+?)\s*=(?!=)(.*)", re.S)
_IDENT = re.compile(r"[A-Za-z_]\w*")
_END_WORDS = ("elif", "else", "endif", "endfor", "endblock")


class _End(NamedTuple):
    """The tag that closed a body: its word, its rest and its line."""

    word: str
    rest: str
    line: int


class _Parser:
    """Turns tokens into nodes."""

    def __init__(self, tokens: list[_Token], name: str) -> None:
        self.tokens = tokens
        self.name = name
        self.pos = 0
        self.depth = 0
        self.blocks: set = set()
        self.has_content = False

    def fail(self, message: str, line: int) -> TemplateSyntaxError:
        """Build a syntax error for this template."""
        return TemplateSyntaxError(message, self.name, line)

    def expr(self, src: str, line: int) -> ast.expr:
        """Parse and validate an expression."""
        return parse_expression(src, self.name, line)

    def body(self, ends: tuple[str, ...], opener: str, line: int) -> tuple[list[Node], _End | None]:
        """Parse nodes until a tag in `ends`; an unclosed `opener` is an error."""
        nodes: list[Node] = []
        while self.pos < len(self.tokens):
            token = self.tokens[self.pos]
            self.pos += 1
            node = self.node(token, ends)
            if isinstance(node, _End):
                return nodes, node
            nodes.append(node)
            self.has_content = self.has_content or not (isinstance(node, Text) and not node.text.strip())
        if ends:
            raise self.fail(f"unclosed {{% {opener} %}}", line)
        return nodes, None

    def node(self, token: _Token, ends: tuple[str, ...]) -> Node | _End:
        """Turn one token into a node, or into the `_End` that closes the body."""
        if token.kind == "text":
            return Text(token.text, token.line)
        if token.kind == "expr":
            return Output(self.expr(token.text, token.line), token.line)
        word, _, rest = token.text.partition(" ")
        word, rest = word.strip(), rest.strip()
        if word in ends:
            return _End(word, rest, token.line)
        return self.tag(word, rest, token.line)

    def tag(self, word: str, rest: str, line: int) -> Node:
        """Parse an opening tag and its body."""
        method = getattr(self, f"tag_{word}", None) if _IDENT.fullmatch(word) else None
        if method is None:
            if word in _END_WORDS:
                raise self.fail(f"unexpected {{% {word} %}}", line)
            raise self.fail(f"unknown tag {word!r}" if word else "empty tag", line)
        self.depth += 1
        node = method(rest, line)
        self.depth -= 1
        return node

    def no_args(self, end: _End) -> None:
        """Refuse extra words after a closing tag like `endif`."""
        if end.rest:
            raise self.fail(f"{{% {end.word} %}} takes no arguments", end.line)

    def tag_if(self, rest: str, line: int) -> If:
        """Parse `if` with its `elif` and `else` parts."""
        branches: list[tuple[ast.expr, list[Node]]] = []
        cond, orelse = self.expr(rest, line), []
        while True:
            body, end = self.body(("elif", "else", "endif"), "if", line)
            branches.append((cond, body))
            if end is not None and end.word == "elif":
                cond = self.expr(end.rest, end.line)
                continue
            if end is not None and end.word == "else":
                self.no_args(end)
                orelse, end = self.body(("endif",), "if", line)
            assert end is not None
            self.no_args(end)
            return If(branches, orelse, line)

    def tag_for(self, rest: str, line: int) -> For:
        """Parse `for`, with its optional `else`."""
        match = _FOR.fullmatch(rest)
        if not match:
            raise self.fail("bad {% for %}: expected `for name in expression`", line)
        targets = self.targets(match.group(1), line)
        iterable = self.expr(match.group(2), line)
        body, end = self.body(("else", "endfor"), "for", line)
        orelse: list[Node] = []
        if end is not None and end.word == "else":
            self.no_args(end)
            orelse, end = self.body(("endfor",), "for", line)
        assert end is not None
        self.no_args(end)
        return For(targets, iterable, body, orelse, line)

    @staticmethod
    def valid_name(name: str) -> bool:
        """Whether `name` may be a template variable: an identifier, not a keyword, not starting with `_`."""
        return bool(_IDENT.fullmatch(name)) and not name.startswith("_") and not keyword.iskeyword(name)

    def targets(self, src: str, line: int) -> tuple[str, ...]:
        """Read the loop variables: `x` or `k, v`."""
        names = tuple(part.strip() for part in src.split(","))
        for name in names:
            if not self.valid_name(name):
                raise self.fail(f"bad loop variable {name!r}", line)
        return names

    def tag_set(self, rest: str, line: int) -> Set:
        """Parse `set name = expression`."""
        match = _SET.fullmatch(rest)
        if not match or not self.valid_name(match.group(1)):
            raise self.fail("bad {% set %}: expected `set name = expression`", line)
        return Set(match.group(1), self.expr(match.group(2), line), line)

    def tag_block(self, rest: str, line: int) -> Block:
        """Parse `block name` up to its `endblock`."""
        if not _IDENT.fullmatch(rest):
            raise self.fail("bad {% block %}: expected a name", line)
        if rest in self.blocks:
            raise self.fail(f"block {rest!r} is defined twice", line)
        self.blocks.add(rest)
        body, end = self.body(("endblock",), "block", line)
        assert end is not None
        if end.rest and end.rest != rest:
            raise self.fail(f"{{% endblock {end.rest} %}} does not match block {rest!r}", end.line)
        return Block(rest, body, line)

    def literal(self, tag: str, rest: str, line: int) -> str:
        """Read the literal string argument of `extends` or `include`."""
        try:
            node = ast.parse(rest, mode="eval").body
        except (SyntaxError, ValueError):
            node = None
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            raise self.fail(f"{{% {tag} %}} takes a string in quotes", line)
        return node.value

    def tag_include(self, rest: str, line: int) -> Include:
        """Parse `include "file"`."""
        return Include(self.literal("include", rest, line), line)

    def tag_extends(self, rest: str, line: int) -> Extends:
        """Parse `extends "file"`, which must be the first tag."""
        if self.has_content or self.depth != 1:
            raise self.fail("{% extends %} must be the first tag", line)
        return Extends(self.literal("extends", rest, line), line)


def parse(source: str, name: str = "<template>") -> list[Node]:
    """Parse template source into nodes; raise `TemplateSyntaxError` if it is invalid."""
    parser = _Parser(_tokenize(source, name), name)
    nodes, _ = parser.body((), "", 1)
    return nodes
