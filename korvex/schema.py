"""Request validation from a handler's type annotations.

A handler's annotations say where its parameters come from, read once when
the route is registered:

- a msgspec `Struct` or pydantic model (or a list of them): the JSON body;
- the name of a path parameter: the path, converted to the annotated type;
- `str`, `int`, `float`, `bool`, `UUID`, `date`, `datetime`, or an
  `Optional` or `list` of those: the query string (dates in ISO 8601).

A model return annotation turns the returned object into a JSON response.
Invalid input gets a 422 listing every problem. msgspec and pydantic are
optional: Korvex depends on neither, and uses whichever a model comes from.
"""

from __future__ import annotations

import datetime
import inspect
import re
import sys
import types
import typing
import uuid
from collections.abc import Callable, Mapping
from typing import Any, NamedTuple, Union

from korvex.errors import ValidationError
from korvex.response import Response

SCALARS = (str, int, float, bool, uuid.UUID, datetime.date, datetime.datetime)
_TRUE = {"true", "1", "yes", "on"}
_FALSE = {"false", "0", "no", "off"}
_PATH_PARAM = re.compile(r"\{(\w+)(?::(\w+))?\}|\*(\w+)")
_CONSTRAINT_TYPES = {"int": int, "uuid": uuid.UUID}


def _error(loc: list, msg: str, kind: str) -> dict:
    return {"loc": loc, "msg": msg, "type": kind}


# --- models (msgspec or pydantic) ---


def _library(tp: Any) -> str | None:
    """"msgspec" or "pydantic" if `tp` is a model of that library, or a
    list of such models; None otherwise."""
    if typing.get_origin(tp) is list and len(typing.get_args(tp)) == 1:
        tp = typing.get_args(tp)[0]
    if not inspect.isclass(tp):
        return None
    for module, base in (("msgspec", "Struct"), ("pydantic", "BaseModel")):
        library = sys.modules.get(module)  # a model of it means the app imported it
        if library is not None and issubclass(tp, getattr(library, base)):
            return module
    return None


class Model:
    """Decodes, encodes and describes one model type (or list of models)
    with the library it comes from."""

    def __init__(self, tp: Any) -> None:
        self.type = tp
        self.library = _library(tp)
        if self.library == "pydantic":
            import pydantic

            self._adapter = pydantic.TypeAdapter(tp)

    def decode(self, data: bytes) -> Any:
        if self.library == "msgspec":
            import msgspec

            try:
                return msgspec.json.decode(data, type=self.type)
            except (msgspec.ValidationError, msgspec.DecodeError) as error:
                raise ValidationError([_msgspec_error(error)]) from None
        import pydantic

        try:
            return self._adapter.validate_json(data)
        except pydantic.ValidationError as error:
            raise ValidationError(
                [_error(["body", *e["loc"]], e["msg"], e["type"]) for e in error.errors()]
            ) from None

    def encode(self, value: Any) -> bytes:
        if self.library == "msgspec":
            import msgspec

            return msgspec.json.encode(value)
        return self._adapter.dump_json(value)

    def schema(self, components: dict) -> dict:
        """The JSON schema of this type, its models added to `components`
        (OpenAPI's `components/schemas`) and referenced from it."""
        if self.library == "msgspec":
            import msgspec

            (schema,), defs = msgspec.json.schema_components([self.type], ref_template="#/components/schemas/{name}")
            components.update(defs)
            return schema
        schema = self._adapter.json_schema(ref_template="#/components/schemas/{model}")
        components.update(schema.pop("$defs", {}))
        if "properties" in schema and "title" in schema:  # a model itself: reference it like nested ones
            components[schema["title"]] = schema
            return {"$ref": f"#/components/schemas/{schema['title']}"}
        return schema


def _msgspec_error(error: Exception) -> dict:
    """msgspec reports one error as text ("Expected `int`, got `str` - at
    `$.age`"): turn it into the common format."""
    message = str(error)
    if type(error).__name__ == "DecodeError":
        return _error(["body"], message, "json_invalid")
    loc: list = ["body"]
    where = re.search(r" - at `\$(.*)`$", message)
    if where:
        message = message[: where.start()]
        loc += [int(part) if part.isdigit() else part for part in re.findall(r"\.(\w+)|\[(\d+)\]", where.group(1)) for part in part if part]
    missing = re.match(r"Object missing required field `(\w+)`", message)
    if missing:
        loc.append(missing.group(1))
    return _error(loc, message, "missing" if missing else "validation")


# --- scalars (path and query parameters) ---


def _unwrap(tp: Any) -> tuple[Any, bool, bool]:
    """(inner type, is a list, accepts None) for `X`, `Optional[X]`,
    `list[X]` or `X | None`."""
    optional = False
    origin = typing.get_origin(tp)
    if origin is Union or (hasattr(types, "UnionType") and origin is getattr(types, "UnionType")):
        args = [a for a in typing.get_args(tp) if a is not type(None)]
        optional = len(args) < len(typing.get_args(tp))
        tp = args[0] if len(args) == 1 else tp
    if typing.get_origin(tp) is list and len(typing.get_args(tp)) == 1:
        return typing.get_args(tp)[0], True, optional
    return tp, False, optional


def _convert(value: str, tp: Any) -> Any:
    """Converts one text value to `tp`; raises ValueError if it can't."""
    if tp is bool:
        lowered = value.lower()
        if lowered in _TRUE:
            return True
        if lowered in _FALSE:
            return False
        raise ValueError(f"not a boolean: {value!r}")
    if tp is datetime.datetime:  # before date: a datetime is a date too
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))  # "Z": Python 3.11+ only otherwise
    if tp is datetime.date:
        return datetime.date.fromisoformat(value)
    return tp(value)


_TYPE_NAMES = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    uuid.UUID: "UUID",
    datetime.date: "date (YYYY-MM-DD)",
    datetime.datetime: "datetime (ISO 8601)",
}


def scalar_schema(tp: Any) -> dict:
    inner, is_list, _ = _unwrap(tp)
    schema: dict = {
        str: {"type": "string"},
        int: {"type": "integer"},
        float: {"type": "number"},
        bool: {"type": "boolean"},
        uuid.UUID: {"type": "string", "format": "uuid"},
        datetime.date: {"type": "string", "format": "date"},
        datetime.datetime: {"type": "string", "format": "date-time"},
    }.get(inner, {"type": "string"})
    return {"type": "array", "items": schema} if is_list else schema


# --- a handler's plan ---


class Param(NamedTuple):
    name: str
    source: str  # "path", "query" or "body"
    type: Any
    required: bool
    default: Any = None


def path_params(pattern: str) -> dict[str, Any]:
    """The parameters of a route pattern and the type Rust already gives
    them: `/users/{id:int}/*path` -> `{"id": int, "path": str}`."""
    return {
        (name or rest): _CONSTRAINT_TYPES.get(constraint or "", str)
        for name, constraint, rest in _PATH_PARAM.findall(pattern)
    }


class Plan:
    """What a handler's annotations ask for, worked out at registration."""

    def __init__(self, params: list[Param], response: Model | None, summary: str, description: str) -> None:
        self.params = params
        self.path = [p for p in params if p.source == "path"]
        self.query = [p for p in params if p.source == "query"]
        self.body = next((p for p in params if p.source == "body"), None)
        self.body_model = Model(self.body.type) if self.body else None
        self.response = response
        self.summary = summary
        self.description = description

    @property
    def validates(self) -> bool:
        """Whether a request can fail validation (and get a 422)."""
        return bool(self.query or self.body or self.path)

    async def bind(self, request: Any, path_values: Mapping[str, Any]) -> dict[str, Any]:
        """The handler's keyword arguments for `request`; raises
        ValidationError listing every problem."""
        kwargs: dict[str, Any] = {}
        errors: list[dict] = []
        for param in self.path:
            value = path_values[param.name]
            inner, _, _ = _unwrap(param.type)
            try:
                kwargs[param.name] = value if isinstance(value, inner) else _convert(str(value), inner)
            except (ValueError, TypeError):
                errors.append(_error(["path", param.name], f"Input should be a valid {_TYPE_NAMES.get(inner, inner)}", "type_error"))
        for param in self.query:
            inner, is_list, _ = _unwrap(param.type)
            values = request.query_params.get(param.name)
            if not values:
                if param.required:
                    errors.append(_error(["query", param.name], "Field required", "missing"))
                else:
                    kwargs[param.name] = param.default
                continue
            try:
                kwargs[param.name] = [_convert(v, inner) for v in values] if is_list else _convert(values[-1], inner)
            except (ValueError, TypeError):
                errors.append(_error(["query", param.name], f"Input should be a valid {_TYPE_NAMES.get(inner, inner)}", "type_error"))
        if self.body is not None and self.body_model is not None:
            raw = await request.body()
            if not raw.strip() and not self.body.required:
                kwargs[self.body.name] = self.body.default
            else:
                try:
                    kwargs[self.body.name] = self.body_model.decode(raw)
                except ValidationError as error:
                    errors.extend(error.errors)
        if errors:
            raise ValidationError(errors)
        return kwargs

    def respond(self, result: Any) -> Response:
        """A Response as is; anything else encoded with the return model."""
        if isinstance(result, Response) or self.response is None:
            return result
        return Response(self.response.encode(result), headers=[("Content-Type", "application/json")])


def plan_for(func: Callable, pattern: str) -> Plan:
    """Reads `func`'s annotations for the route `pattern`. Raises TypeError
    for a parameter whose origin can't be told."""
    try:
        hints = typing.get_type_hints(func)
    except Exception as error:  # an annotation naming something that isn't importable here
        raise TypeError(f"can't read the annotations of {func.__qualname__}: {error}") from None
    route_params = path_params(pattern)
    params: list[Param] = []
    signature = inspect.signature(func)
    for index, (name, parameter) in enumerate(signature.parameters.items()):
        if index == 0 or parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
            continue  # the request, and catch-alls
        tp = hints.get(name)
        has_default = parameter.default is not parameter.empty
        default = parameter.default if has_default else None
        inner, _, optional = _unwrap(tp) if tp is not None else (None, False, False)
        if name in route_params:
            # Converted only when the pattern's constraint doesn't already give that type.
            if tp is not None and inner in SCALARS and inner is not route_params[name] and inner is not str:
                params.append(Param(name, "path", tp, True))
        elif tp is not None and _library(tp) is not None or (optional and _library(inner)):
            body_type = tp if _library(tp) else inner
            params.append(Param(name, "body", body_type, not (has_default or optional), default))
        elif tp is not None and inner in SCALARS:
            params.append(Param(name, "query", tp, not (has_default or optional), default))
        elif tp is not None or not has_default:
            # An annotated parameter is refused even with a default: silently
            # ignored, it would always get that default.
            raise TypeError(
                f"{func.__qualname__}: can't tell where parameter {name!r} comes from: it isn't in the path "
                f"{pattern!r}, and its annotation isn't a model or a str, int, float, bool, UUID, date or datetime"
            )
    if sum(p.source == "body" for p in params) > 1:
        raise TypeError(f"{func.__qualname__}: only one parameter can be the request body")
    returned = hints.get("return")
    response = Model(returned) if returned is not None and _library(returned) else None
    doc = inspect.getdoc(func) or ""
    summary, _, description = doc.partition("\n")
    return Plan(params, response, summary.strip(), description.strip())


def needs_plan(plan: Plan) -> bool:
    """Whether calling the handler needs the plan at all: a handler with no
    body, query, converted path or return model is called as before."""
    return bool(plan.params or plan.response)

