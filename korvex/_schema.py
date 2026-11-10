"""Request validation from a handler's type annotations.

A handler's annotations say where its parameters come from, read once at
route registration:

- a msgspec `Struct` or pydantic model (or a list of them): the JSON body;
- the name of a path parameter: the path, converted to the annotated type;
- `str`, `int`, `float`, `bool`, `UUID`, `date`, `datetime`, or an
  `Optional` or `list` of those: the query string (dates in ISO 8601);
- `Header()`, `Cookie()`, `Query()` or `Form()` (korvex._params): there;
- `Depends(func)`: what `func` returns; its own parameters are read the same way.

A model return annotation turns the returned object into a JSON response.
Invalid input gets a 422 listing every problem. msgspec and pydantic are
optional: Korvex uses whichever a model comes from.
"""

from __future__ import annotations

import datetime
import inspect
import re
import sys
import types
import typing
import uuid
from collections.abc import Callable, Mapping, Sequence
from typing import Any, NamedTuple, Union

from korvex._errors import RequestValidationError
from korvex._params import REQUIRED, Dependency, Source
from korvex._requests import Connection, MultiDict, Request, UploadedFile, WebSocket
from korvex._response import Response

SCALARS = (str, int, float, bool, uuid.UUID, datetime.date, datetime.datetime)
_TRUE = {"true", "1", "yes", "on"}
_FALSE = {"false", "0", "no", "off"}
_PATH_PARAM = re.compile(r"\{(\w+)(?::(\w+))?\}|\*(\w+)")
_CONSTRAINT_TYPES = {"int": int, "uuid": uuid.UUID}


def _error(loc: list, msg: str, kind: str) -> dict:
    return {"loc": loc, "msg": msg, "type": kind}


# --- models (msgspec or pydantic) ---


def _library(tp: Any) -> str | None:
    """ "msgspec" or "pydantic" if `tp` is a model (or list of models) of that library, else None."""
    if typing.get_origin(tp) is list and len(typing.get_args(tp)) == 1:
        tp = typing.get_args(tp)[0]
    if not inspect.isclass(tp):
        return None
    for module, base in (("msgspec", "Struct"), ("pydantic", "BaseModel")):
        library = sys.modules.get(module)  # a model of it means it's imported
        if library is not None and issubclass(tp, getattr(library, base)):
            return module
    return None


class Model:
    """Decodes, encodes and describes a model type (or list of models)
    with its library."""

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
                raise RequestValidationError([_msgspec_error(error)]) from None
        import pydantic

        try:
            return self._adapter.validate_json(data)
        except pydantic.ValidationError as error:
            raise RequestValidationError(
                [_error(["body", *e["loc"]], e["msg"], e["type"]) for e in error.errors()]
            ) from None

    def from_form(self, form: MultiDict) -> Any:
        """The model, from a form's fields: each model field from the form
        field of the same name (its alias or rename); all values for a list,
        else the first. An empty value is missing, except for text; a
        checkbox's `on` is true. Raises RequestValidationError with `["form", ...]`
        locations."""
        data: dict[str, Any] = {}
        for key, tp in self._fields():
            inner, many, _ = _unwrap(tp)
            values = form.get_all(key)
            if inner is not str:
                values = [v for v in values if v != ""]
            if inner is bool:
                values = [_lenient_bool(v) for v in values]
            if values:
                data[key] = values if many else values[0]
        if self.library == "msgspec":
            import msgspec

            try:
                return msgspec.convert(data, type=self.type, strict=False)  # text to numbers, dates...
            except msgspec.ValidationError as error:
                raise RequestValidationError([_msgspec_error(error, "form")]) from None
        import pydantic

        try:
            return self._adapter.validate_python(data)
        except pydantic.ValidationError as error:
            raise RequestValidationError(
                [_error(["form", *e["loc"]], e["msg"], e["type"]) for e in error.errors()]
            ) from None

    def _fields(self) -> list[tuple[str, Any]]:
        """`(name in the request, annotation)` of each field of the model."""
        if self.library == "msgspec":
            import msgspec

            return [(field.encode_name, field.type) for field in msgspec.structs.fields(self.type)]
        return [(info.alias or name, info.annotation) for name, info in self.type.model_fields.items()]

    def encode(self, value: Any) -> bytes:
        if self.library == "msgspec":
            import msgspec

            return msgspec.json.encode(value)
        return self._adapter.dump_json(value)

    def schema(self, components: dict) -> dict:
        """The JSON schema of this type; its models go to `components`
        (OpenAPI's `components/schemas`) and are referenced."""
        if self.library == "msgspec":
            import msgspec

            (schema,), defs = msgspec.json.schema_components([self.type], ref_template="#/components/schemas/{name}")
            components.update(defs)
            return schema
        schema = self._adapter.json_schema(ref_template="#/components/schemas/{model}")
        components.update(schema.pop("$defs", {}))
        if "properties" in schema and "title" in schema:  # a model: reference it like nested ones
            components[schema["title"]] = schema
            return {"$ref": f"#/components/schemas/{schema['title']}"}
        return schema


def _msgspec_error(error: Exception, root: str = "body") -> dict:
    """Turns msgspec's text error ("Expected `int`, got `str` - at `$.age`")
    into the common format."""
    message = str(error)
    if type(error).__name__ == "DecodeError":
        return _error([root], message, "json_invalid")
    loc: list = [root]
    where = re.search(r" - at `\$(.*)`$", message)
    if where:
        message = message[: where.start()]
        loc += [
            int(part) if part.isdigit() else part
            for part in re.findall(r"\.(\w+)|\[(\d+)\]", where.group(1))
            for part in part
            if part
        ]
    missing = re.match(r"Object missing required field `(\w+)`", message)
    if missing:
        loc.append(missing.group(1))
    return _error(loc, message, "missing" if missing else "validation")


def _lenient_bool(value: Any) -> Any:
    """`on`, `yes`, `1`... as True, their opposites as False, anything else
    unchanged (the model refuses it)."""
    if isinstance(value, str):
        lowered = value.lower()
        if lowered in _TRUE:
            return True
        if lowered in _FALSE:
            return False
    return value


# --- scalars (path and query parameters) ---


def _unwrap(tp: Any) -> tuple[Any, bool, bool]:
    """(inner type, is a list, accepts None) for `X`, `Optional[X]`,
    `list[X]` or `X | None`."""
    optional = False
    origin = typing.get_origin(tp)
    if origin is Union or (hasattr(types, "UnionType") and origin is types.UnionType):
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
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))  # "Z" needs Python 3.11+
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
    name: str  # the parameter's name in the function
    source: str  # "path", "query", "header", "cookie", "form", "body" or "request"
    type: Any
    required: bool
    default: Any = None
    key: str = ""  # its name in the request (path or query parameter, header, cookie, form field)
    inner: Any = None  # the type of each value: `int` for `Optional[int]` or `list[int]`
    many: bool = False  # a list


def _param(name: str, source: str, tp: Any, required: bool, default: Any = None, key: str = "") -> Param:
    inner, many, _ = _unwrap(tp) if tp is not None else (None, False, False)
    return Param(name, source, tp, required, default, key, inner, many)


class Dep(NamedTuple):
    name: str | None  # the parameter it fills; None for a route's `dependencies`
    func: Callable
    cache: bool
    plan: Plan


def path_params(pattern: str) -> dict[str, Any]:
    """A route pattern's parameters and the type Rust already gives them:
    `/users/{id:int}/*path` -> `{"id": int, "path": str}`."""
    return {
        (name or rest): _CONSTRAINT_TYPES.get(constraint or "", str)
        for name, constraint, rest in _PATH_PARAM.findall(pattern)
    }


class Plan:
    """What a handler's (or dependency's) annotations ask for, worked out at registration."""

    def __init__(
        self,
        func: Callable,
        pattern: str,
        websocket: bool,
        params: list[Param],
        dependencies: list[Dep],
        response: Model | None,
        summary: str,
        description: str,
    ) -> None:
        self.func = func
        self.pattern = pattern
        self.websocket = websocket
        self.params = params
        self.dependencies = dependencies
        self.path = [p for p in params if p.source == "path"]
        self.query = [p for p in params if p.source == "query"]
        self.headers = [p for p in params if p.source == "header"]
        self.cookies = [p for p in params if p.source == "cookie"]
        self.form = [p for p in params if p.source == "form"]
        self.form_models = [(p, Model(p.type)) for p in params if p.source == "form_model"]
        self.request = [p.name for p in params if p.source == "request"]
        self.request_name = self.request[0] if len(self.request) == 1 else None  # the usual case
        self.binds = any(p.source != "request" for p in params)  # something to read from the request
        self.body = next((p for p in params if p.source == "body"), None)
        self.body_model = Model(self.body.type) if self.body else None
        self.response = response
        self.empty = False  # annotated `-> None`: an empty response
        self.status = 200  # the route's, for a response built from the return value
        self.summary = summary
        self.description = description
        # A dependency's value is returned (awaited for a coroutine) or yielded.
        self.generator: str | None = None
        if _is_kind(func, inspect.isasyncgenfunction):
            self.generator = "async"
        elif _is_kind(func, inspect.isgeneratorfunction):
            self.generator = "sync"
        self.coroutine = _is_kind(func, inspect.iscoroutinefunction)

    @property
    def validates(self) -> bool:
        """Whether a request can fail validation (get a 422)."""
        given = path_params(self.pattern)  # what Rust already checked

        def can_fail(p: Param) -> bool:
            if p.source == "path":
                return p.inner not in (str, given.get(p.key))
            return p.source != "request"

        return any(map(can_fail, self.params)) or any(dep.plan.validates for dep in self.dependencies)

    def everything(self) -> list[Param]:
        """The parameters of this plan and its dependencies, each request
        field once: what the request has to provide."""
        found: dict[tuple[str, str], Param] = {}

        def walk(plan: Plan) -> None:
            for dep in plan.dependencies:
                walk(dep.plan)
            for param in plan.params:
                found.setdefault((param.source, param.key), param)

        walk(self)
        return list(found.values())

    async def bind(self, request: Any, path_values: Mapping[str, Any], run: Run | None) -> dict[str, Any]:
        """The function's keyword arguments for `request`, dependencies
        resolved through `run`. Raises RequestValidationError listing every problem."""
        kwargs: dict[str, Any] = {}
        errors: list[dict] = []
        # Each source is checked before looping: this runs on every request
        # and most plans use one or two.
        if self.dependencies:
            assert run is not None  # always given with dependencies
            for dep in self.dependencies:
                try:
                    value = await run.resolve(dep, request, path_values)
                except RequestValidationError as error:  # its parameters didn't validate
                    errors.extend(error.errors)
                    continue
                if dep.name is not None:
                    kwargs[dep.name] = value
        if self.path:
            for param in self.path:
                value = path_values[param.name]
                try:
                    kwargs[param.name] = value if isinstance(value, param.inner) else _convert(str(value), param.inner)
                except (ValueError, TypeError):
                    errors.append(_invalid(param))
        if self.query:
            for param in self.query:
                _take(param, request.query.get_all(param.key), kwargs, errors)
        if self.headers:
            for param in self.headers:
                _take(param, request.headers.get_all(param.key), kwargs, errors)
        if self.cookies:
            for param in self.cookies:
                cookie = request.cookies.get(param.key)
                _take(param, None if cookie is None else [cookie], kwargs, errors)
        if self.form or self.form_models:
            form = await _read_form(request)
            for param, model in self.form_models:
                if not param.required and not any(form.get_all(key) for key, _ in model._fields()):
                    kwargs[param.name] = param.default  # none of its fields sent
                    continue
                try:
                    kwargs[param.name] = model.from_form(form)
                except RequestValidationError as error:
                    errors.extend(error.errors)
            for param in self.form:
                values = form.get_all(param.key)
                if param.inner is not str and param.inner is not UploadedFile:
                    values = [v for v in values if v != ""]  # an empty field: not given
                _take(param, values, kwargs, errors)
        if self.body is not None and self.body_model is not None:
            raw = await request.body()
            if not raw.strip() and not self.body.required:
                kwargs[self.body.name] = self.body.default
            else:
                try:
                    kwargs[self.body.name] = self.body_model.decode(raw)
                except RequestValidationError as error:
                    errors.extend(error.errors)
        if self.request:
            for name in self.request:
                kwargs[name] = request
        if errors:
            raise RequestValidationError(_unique(errors))
        return kwargs

    def respond(self, result: Any) -> Any:
        """A Response as is; else the result encoded with the return model, or
        `None` as an empty response, with the route's status."""
        if isinstance(result, Response):
            return result
        if self.response is not None:
            return Response(
                self.response.encode(result), status=self.status, headers=[("Content-Type", "application/json")]
            )
        if result is None and self.empty:
            return Response(status=self.status)
        return result  # not a Response: the App answers a 500


class Run:
    """One request's dependencies: their values (called once each unless
    `cache=False`) and the teardown of those that yield."""

    __slots__ = ("overrides", "plans", "values", "teardowns")

    def __init__(self, overrides: Mapping[Callable, Callable], plans: Callable[[Callable, str, bool], Plan]) -> None:
        self.overrides = overrides
        self.plans = plans  # an override's plan, worked out on first use
        self.values: dict[Callable, Any] = {}
        self.teardowns: list[tuple[Any, bool]] = []  # (generator, is async), in start order

    async def resolve(self, dep: Dep, request: Any, path_values: Mapping[str, Any]) -> Any:
        func = self.overrides.get(dep.func, dep.func) if self.overrides else dep.func
        if dep.cache and func in self.values:
            return self.values[func]
        plan = dep.plan if func is dep.func else self.plans(func, dep.plan.pattern, dep.plan.websocket)
        kwargs = await plan.bind(request, path_values, self) if plan.params or plan.dependencies else {}
        if plan.generator is None:
            value = await func(**kwargs) if plan.coroutine else func(**kwargs)
        else:
            generator = func(**kwargs)
            is_async = plan.generator == "async"
            try:
                value = await generator.__anext__() if is_async else next(generator)
            except (StopIteration, StopAsyncIteration):
                raise RuntimeError(f"dependency {getattr(func, '__qualname__', func)!r} didn't yield") from None
            self.teardowns.append((generator, is_async))
        if dep.cache:
            self.values[func] = value
        return value

    @property
    def open(self) -> bool:
        """Whether some dependencies wait for their teardown."""
        return bool(self.teardowns)

    async def close(self, error: BaseException | None = None) -> None:
        """Tears down the dependencies that yielded, last first. `error`, if
        given, is raised at their `yield` and isn't swallowed even if they
        catch it: the caller raises it on. A teardown's own error is raised
        from here, after the other teardowns ran."""
        teardowns, self.teardowns = self.teardowns, []
        raised = error
        for generator, is_async in reversed(teardowns):
            try:
                if raised is None:
                    await generator.__anext__() if is_async else next(generator)
                else:
                    await generator.athrow(raised) if is_async else generator.throw(raised)
            except (StopIteration, StopAsyncIteration):
                continue
            except BaseException as new:
                if new is not raised:
                    if new.__context__ is None and raised is not None:
                        new.__context__ = raised
                    raised = new
                continue
            name = getattr(generator, "__qualname__", generator)
            await generator.aclose() if is_async else generator.close()
            raised = RuntimeError(f"dependency {name!r} yielded more than once")
        if raised is not None and raised is not error:
            raise raised


def _take(param: Param, values: list | None, kwargs: dict, errors: list) -> None:
    """Converts a parameter's raw values, or uses its default."""
    if not values:
        if param.required:
            errors.append(_error([param.source, param.key], "Field required", "missing"))
        else:
            kwargs[param.name] = param.default
        return
    try:
        if param.many:
            kwargs[param.name] = [_convert_field(v, param.inner) for v in values]
        else:
            kwargs[param.name] = _convert_field(values[0], param.inner)  # the first, wherever it's from
    except (ValueError, TypeError):
        errors.append(_invalid(param))


def _convert_field(value: Any, tp: Any) -> Any:
    """`_convert`, for a value that may be a form's file."""
    if tp is UploadedFile:
        if isinstance(value, str):
            raise ValueError("expected a file")
        return value
    if not isinstance(value, str):
        raise ValueError("expected text, got a file")
    return _convert(value, tp)


def _invalid(param: Param) -> dict:
    name = "file" if param.inner is UploadedFile else _TYPE_NAMES.get(param.inner, param.inner)
    return _error([param.source, param.key], f"Input should be a valid {name}", "type_error")


async def _read_form(request: Any) -> MultiDict:
    """The request's form; an empty body without a content type is an empty
    form, so optional fields can be left out."""
    if not request.headers.get("content-type") and not (await request.body()).strip():
        return MultiDict()
    return await request.form()


def _unique(errors: list[dict]) -> list[dict]:
    """Errors without repeats (two dependencies can read the same field)."""
    seen: set = set()
    unique = []
    for error in errors:
        key = (tuple(error["loc"]), error["type"])
        if key not in seen:
            seen.add(key)
            unique.append(error)
    return unique


def _is_kind(func: Callable, test: Callable[[Any], bool]) -> bool:
    # Tests the method itself, not whether there is one.
    return test(func) or (not inspect.isfunction(func) and test(getattr(func, "__call__", None)))  # noqa: B004


def _split(hint: Any) -> tuple[Any, Source | Dependency | None]:
    """`Annotated[X, Header()]` -> `(X, Header())`. Other metadata is ignored."""
    if typing.get_origin(hint) is not typing.Annotated:
        return hint, None
    tp, *metadata = typing.get_args(hint)
    marker = next((m for m in metadata if isinstance(m, (Source, Dependency))), None)
    return tp, marker


def _hints(func: Callable) -> dict[str, Any]:
    target = func.__init__ if inspect.isclass(func) else func
    if not (inspect.isfunction(target) or inspect.ismethod(target)):
        target = getattr(target, "__call__", target)  # noqa: B004 (a callable object: its method's hints)
    return typing.get_type_hints(target, include_extras=True)


# Unannotated, a parameter with one of these names is the request (or WebSocket).
_REQUEST_NAMES = ("request",)
_WEBSOCKET_NAMES = ("request", "websocket", "ws")


def plan_for(
    func: Callable,
    pattern: str,
    *,
    dependencies: Sequence[Any] = (),
    websocket: bool = False,
    handler: bool = True,
    cache: dict | None = None,
    _chain: tuple = (),
) -> Plan:
    """Reads `func`'s annotations for the route `pattern`: where each
    parameter comes from. The request (or WebSocket) is a parameter like
    the others, annotated or named so. `dependencies` are the route's own.
    Raises TypeError for a parameter of unknown origin."""
    where = getattr(func, "__qualname__", repr(func))
    if func in _chain:
        cycle = " -> ".join(getattr(f, "__qualname__", repr(f)) for f in (*_chain, func))
        raise TypeError(f"dependency cycle: {cycle}")
    try:
        hints = _hints(func)
    except Exception as error:  # an annotation names something not importable here
        raise TypeError(f"can't read the annotations of {where}: {error}") from None
    chain = (*_chain, func)

    def plan_dependency(marker: Dependency) -> Plan:
        key = (marker.func, pattern, websocket)
        try:
            found = cache.get(key) if cache is not None else None
        except TypeError:  # unhashable callable: not cached
            found = None
        if found is None:
            found = plan_for(marker.func, pattern, websocket=websocket, handler=False, cache=cache, _chain=chain)
            try:
                if cache is not None:
                    cache[key] = found
            except TypeError:
                pass
        return found

    route_params = path_params(pattern)
    params: list[Param] = []
    deps: list[Dep] = []
    for marker in dependencies:
        if not isinstance(marker, Dependency):
            raise TypeError(f"{where}: dependencies takes Depends(...) items, not {marker!r}")
        deps.append(Dep(None, marker.func, marker.cache, plan_dependency(marker)))

    for name, parameter in inspect.signature(func).parameters.items():
        if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
            continue  # catch-alls
        tp, marker = _split(hints.get(name))
        if isinstance(parameter.default, (Source, Dependency)):
            if marker is not None:
                raise TypeError(f"{where}: parameter {name!r} says where it comes from twice")
            marker, has_default, default = parameter.default, False, None
        else:
            has_default = parameter.default is not parameter.empty
            default = parameter.default if has_default else None
        if isinstance(marker, Source) and marker.default is not REQUIRED:
            if has_default:
                raise TypeError(f"{where}: parameter {name!r} has two defaults")
            has_default, default = True, marker.default
        if isinstance(marker, Dependency):
            deps.append(Dep(name, marker.func, marker.cache, plan_dependency(marker)))
            continue

        inner, is_list, optional = _unwrap(tp) if tp is not None else (None, False, False)
        required = not (has_default or optional)
        if isinstance(marker, Source) and marker.kind == "form" and tp is not None and not is_list and _library(inner):
            # A model filled from form fields: `body: Signup = Form()`.
            if websocket:
                raise TypeError(f"{where}: parameter {name!r}: a WebSocket has no form")
            params.append(_param(name, "form_model", inner, required, default, key=name))
        elif isinstance(marker, Source):
            if tp is None:
                tp, inner = str, str
            allowed = SCALARS + ((UploadedFile,) if marker.kind == "form" else ())
            if inner not in allowed:
                raise TypeError(f"{where}: {marker!r} parameter {name!r} can't be {tp!r}; use one of {_names(allowed)}")
            if marker.kind == "cookie" and is_list:
                raise TypeError(f"{where}: cookie parameter {name!r} can't be a list: a cookie has one value")
            if marker.kind == "form" and websocket:
                raise TypeError(f"{where}: parameter {name!r}: a WebSocket has no form")
            key = marker.alias or (name.replace("_", "-") if marker.kind == "header" else name)
            params.append(_param(name, marker.kind, tp, required, default, key))
        elif inspect.isclass(inner) and issubclass(inner, Connection):
            if issubclass(inner, WebSocket) and not websocket:
                raise TypeError(f"{where}: parameter {name!r} asks for a WebSocket in an HTTP route")
            if issubclass(inner, Request) and websocket:
                raise TypeError(
                    f"{where}: parameter {name!r} asks for a Request in a WebSocket route; "
                    "annotate it Connection to serve both, or WebSocket"
                )
            params.append(_param(name, "request", tp, True, key=name))
        elif tp is None and not has_default and name in (_WEBSOCKET_NAMES if websocket else _REQUEST_NAMES):
            params.append(_param(name, "request", None, True, key=name))
        elif name in route_params:
            if tp is not None and inner not in SCALARS:
                raise TypeError(f"{where}: path parameter {name!r} can't be {tp!r}; use one of {_names(SCALARS)}")
            given = route_params[name]
            # A handler gets the path values anyway: only those to convert are planned.
            if not handler or (tp is not None and inner is not given and inner is not str):
                params.append(_param(name, "path", tp if tp is not None else given, True, key=name))
        elif tp is not None and (_library(tp) is not None or (optional and _library(inner))):
            if websocket:
                raise TypeError(f"{where}: parameter {name!r}: a WebSocket has no request body")
            body_type = tp if _library(tp) else inner
            params.append(_param(name, "body", body_type, required, default, key="body"))
        elif tp is not None and inner in SCALARS:
            params.append(_param(name, "query", tp, required, default, key=name))
        elif tp is not None or not has_default:
            # Refused even with a default: ignored, it would always get that default.
            raise TypeError(
                f"{where}: can't tell where parameter {name!r} comes from: it isn't in the path {pattern!r}, "
                f"its annotation isn't a model or a {_names(SCALARS)}, and it isn't given with Depends(), "
                "Header(), Cookie(), Query() or Form(); for the request itself, name it `request` or annotate it "
                f"{'WebSocket' if websocket else 'Request'}"
            )

    if sum(p.source == "body" for p in params) > 1:
        raise TypeError(f"{where}: only one parameter can be the request body")
    returned = hints.get("return")
    response = Model(returned) if handler and not websocket and returned is not None and _library(returned) else None
    doc = inspect.getdoc(func) or ""
    summary, _, description = doc.partition("\n")
    plan = Plan(func, pattern, websocket, params, deps, response, summary.strip(), description.strip())
    plan.empty = handler and not websocket and "return" in hints and returned is type(None)
    if handler:
        _check_body(plan, where)
    return plan


def _check_body(plan: Plan, where: str) -> None:
    """One body per request: no two different models, nor a model and a form,
    across the handler and its dependencies."""
    bodies: set = set()
    form = False

    def walk(p: Plan) -> None:
        nonlocal form
        for dep in p.dependencies:
            walk(dep.plan)
        bodies.update(repr(param.type) for param in p.params if param.source == "body")
        form = form or bool(p.form) or bool(p.form_models)

    walk(plan)
    if len(bodies) > 1:
        raise TypeError(f"{where}: only one model can be the request body, here: {', '.join(sorted(bodies))}")
    if bodies and form:
        raise TypeError(f"{where}: the request body can't be both a model and a form")


def _names(types_: Sequence[Any]) -> str:
    names = [getattr(t, "__name__", str(t)) for t in types_]
    return ", ".join(names[:-1]) + " or " + names[-1]


def needs_plan(plan: Plan) -> bool:
    """Whether calling the handler needs the plan: without anything to read
    from the request (it may take the request itself), dependencies or a
    return model, it gets the path parameters as they are."""
    return bool(plan.binds or plan.response or plan.dependencies or plan.empty)
