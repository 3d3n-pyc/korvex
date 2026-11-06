"""An OpenAPI 3.1 description of an App, built from its routes: path
patterns, typed parameters, msgspec or pydantic models, and docstrings.
See App.openapi() and App.docs()."""

from __future__ import annotations

import datetime
import html
import json
import re
import uuid
from typing import TYPE_CHECKING, Any

from http import HTTPStatus

from korvex.app import UploadedFile, _has_body
from korvex.schema import Model, Plan, _unwrap, path_params, scalar_schema
from korvex.security import SecurityScheme

if TYPE_CHECKING:
    from korvex.app import App

_PARAM = re.compile(r"\{(\w+)(?::\w+)?\}|\*(\w+)")

VALIDATION_ERROR = {
    "type": "object",
    "properties": {
        "detail": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "loc": {"type": "array", "items": {"anyOf": [{"type": "string"}, {"type": "integer"}]}},
                    "msg": {"type": "string"},
                    "type": {"type": "string"},
                },
                "required": ["loc", "msg", "type"],
            },
        }
    },
    "required": ["detail"],
}


def openapi_path(pattern: str) -> str:
    """Korvex syntax to OpenAPI's: `/users/{id:int}/*rest` -> `/users/{id}/{rest}`."""
    return _PARAM.sub(lambda m: "{" + (m.group(1) or m.group(2)) + "}", pattern)


def build(
    app: App, *, title: str, version: str, description: str | None = None, host: str | None = None
) -> dict[str, Any]:
    components: dict[str, Any] = {}
    paths: dict[str, dict[str, Any]] = {}
    used_ids: set[str] = set()
    custom = dict(app.router.constraints())  # name -> regex
    schemes = _Schemes()
    for pattern, method, name, _ in app._routes_for(host):
        if name.startswith("korvex.") or name not in app._handlers:
            continue  # static files and the docs themselves; names without a handler can't be described
        docs = app._docs.get(name)
        if docs is not None and not docs.include:
            continue
        func = app._handlers[name]
        plan = app._plan_of(name)
        route_name = app._route_names.get((False, name), (getattr(func, "__name__", "handler"),))[0]
        operation: dict[str, Any] = {"operationId": _unique(route_name, used_ids)}
        if docs is not None and docs.tags:
            operation["tags"] = list(docs.tags)
        if plan.summary:
            operation["summary"] = plan.summary
        if plan.description:
            operation["description"] = plan.description
        if docs is not None and docs.deprecated:
            operation["deprecated"] = True

        everything = plan.everything()  # the handler's parameters and its dependencies'
        annotated = {p.key: p.type for p in everything if p.source == "path"}
        parameters = [
            {
                "name": param,
                "in": "path",
                "required": True,
                "schema": _path_schema(param, given, annotated, pattern, custom),
            }
            for param, given in path_params(pattern).items()
        ]
        for param in everything:
            if param.source not in ("query", "header", "cookie"):
                continue
            entry: dict[str, Any] = {"name": param.key, "in": param.source, "required": param.required}
            entry["schema"] = _with_default(scalar_schema(param.type), param)
            parameters.append(entry)
        if parameters:
            operation["parameters"] = parameters

        body = next((p for p in everything if p.source == "body"), None)
        form = [p for p in everything if p.source == "form"]
        if body is not None:
            operation["requestBody"] = {
                "required": body.required,
                "content": {"application/json": {"schema": Model(body.type).schema(components)}},
            }
        elif form or any(p.source == "form_model" for p in everything):
            models = [p for p in everything if p.source == "form_model"]
            files = any(_unwrap(p.type)[0] is UploadedFile for p in form)
            schemas = [Model(p.type).schema(components) for p in models]
            if form:
                fields: dict[str, Any] = {
                    "type": "object",
                    "properties": {p.key: _with_default(_form_schema(p.type), p) for p in form},
                }
                if [p.key for p in form if p.required]:
                    fields["required"] = [p.key for p in form if p.required]
                schemas.append(fields)
            schema = schemas[0] if len(schemas) == 1 else {"allOf": schemas}
            required = any(p.required for p in (*form, *models))
            kind = "multipart/form-data" if files else "application/x-www-form-urlencoded"
            operation["requestBody"] = {"required": required, "content": {kind: {"schema": schema}}}
        success: dict[str, Any] = {"description": "Successful response"}
        if plan.response is not None and _has_body(plan.status):
            success["content"] = {"application/json": {"schema": plan.response.schema(components)}}
        responses = {str(plan.status): success}
        if plan.validates:
            components["ValidationError"] = VALIDATION_ERROR
            responses["422"] = {
                "description": "Validation error",
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ValidationError"}}},
            }
        security = schemes.requirements(plan)
        if security:
            operation["security"] = security
            if any(s.required for s in _schemes_of(plan)):
                responses["401"] = {"description": "Not authenticated"}
        for status, (text, model) in (docs.responses if docs is not None else {}).items():
            entry = dict(responses.get(status, {}))
            entry["description"] = text or entry.get("description") or _phrase(status)
            if model is not None:
                entry["content"] = {"application/json": {"schema": Model(model).schema(components)}}
            responses[status] = entry
        operation["responses"] = dict(sorted(responses.items()))
        paths.setdefault(openapi_path(pattern), {})[method.lower()] = operation

    info: dict[str, Any] = {"title": title, "version": version}
    if description:
        info["description"] = description
    document: dict[str, Any] = {"openapi": "3.1.0", "info": info, "paths": paths}
    if components or schemes.specs:
        document["components"] = {}
        if components:
            document["components"]["schemas"] = dict(sorted(components.items()))
        if schemes.specs:
            document["components"]["securitySchemes"] = dict(sorted(schemes.specs.items()))
    return document


class _Schemes:
    """The security schemes the routes use, each under a name of its own."""

    def __init__(self) -> None:
        self.specs: dict[str, dict[str, Any]] = {}  # name -> Security Scheme Object
        self._names: dict[int, str] = {}  # id of a scheme -> its name

    def name_of(self, scheme: SecurityScheme) -> str:
        found = self._names.get(id(scheme))
        if found is not None:
            return found
        spec, name, n = scheme.openapi(), scheme.name, 2
        while name in self.specs and self.specs[name] != spec:  # another scheme took it: `name_2`...
            name, n = f"{scheme.name}_{n}", n + 1
        self.specs[name] = spec
        self._names[id(scheme)] = name
        return name

    def requirements(self, plan: Plan) -> list[dict[str, list[str]]]:
        """The operation's `security`: the required schemes all together,
        or else each optional one as an alternative."""
        found = _schemes_of(plan)
        required = [s for s in found if s.required]
        if required:
            return [{self.name_of(s): [] for s in required}]
        return [{self.name_of(s): []} for s in found]


def _schemes_of(plan: Plan) -> list[SecurityScheme]:
    """The security schemes among a plan's dependencies, theirs included,
    each once, in order."""
    found: dict[int, SecurityScheme] = {}

    def walk(p: Plan) -> None:
        for dep in p.dependencies:
            if isinstance(dep.func, SecurityScheme):
                found.setdefault(id(dep.func), dep.func)
            walk(dep.plan)

    walk(plan)
    return list(found.values())


def _phrase(status: str) -> str:
    """The standard description of a status: `"404"` -> `"Not Found"`."""
    try:
        return HTTPStatus(int(status)).phrase
    except ValueError:  # "4XX", "default", or a status Python doesn't name
        return "Response"


_CONSTRAINED = re.compile(r"\{(\w+):(\w+)\}")


def _path_schema(param: str, given: Any, annotated: dict, pattern: str, custom: dict) -> dict:
    """A path parameter's schema: its annotation's type, or the type its
    constraint gives; a custom constraint adds its regex."""
    tp = annotated.get(param, given)
    schema = scalar_schema(tp)
    constraint = dict(_CONSTRAINED.findall(pattern)).get(param)
    if constraint in custom and schema == {"type": "string"}:
        schema = {"type": "string", "pattern": f"^(?:{custom[constraint]})$"}
    return schema


def _with_default(schema: dict, param: Any) -> dict:
    if not param.required and param.default is not None and param.default != []:
        return {**schema, "default": _jsonable(param.default)}
    return schema


def _form_schema(tp: Any) -> dict:
    inner, is_list, _ = _unwrap(tp)
    if inner is UploadedFile:
        file = {"type": "string", "format": "binary"}
        return {"type": "array", "items": file} if is_list else file
    return scalar_schema(tp)


def _unique(name: str, used: set[str]) -> str:
    candidate, n = name, 2
    while candidate in used:
        candidate, n = f"{name}_{n}", n + 1
    used.add(candidate)
    return candidate


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    return str(value) if isinstance(value, uuid.UUID) else value


# Swagger UI from a CDN, major version pinned; App.docs(docs_path=None) skips it.
_SWAGGER_UI = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css">
</head>
<body>
<div id="swagger-ui"></div>
<script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
<script>SwaggerUIBundle({{url: {url}, dom_id: "#swagger-ui"}});</script>
</body>
</html>
"""


def swagger_ui(title: str, openapi_url: str) -> str:
    """The docs page. The title is HTML-escaped and the URL JSON-encoded
    (with `</` broken up), so neither can inject markup or script."""
    url = json.dumps(openapi_url).replace("</", "<\\/")
    return _SWAGGER_UI.format(title=html.escape(title), url=url)
