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

from korvex.app import UploadedFile
from korvex.schema import Model, _unwrap, path_params, scalar_schema

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


def build(app: App, *, title: str, version: str, description: str | None = None, host: str | None = None) -> dict[str, Any]:
    components: dict[str, Any] = {}
    paths: dict[str, dict[str, Any]] = {}
    used_ids: set[str] = set()
    custom = dict(app.router.constraints())  # name -> regex
    for pattern, method, name, _ in app._routes_for(host):
        if name.startswith("korvex.") or name not in app._handlers:
            continue  # static files and the docs themselves; names without a handler can't be described
        func = app._handlers[name]
        plan = app._plan_of(name)
        operation: dict[str, Any] = {"operationId": _unique(getattr(func, "__name__", "handler"), used_ids)}
        if plan.summary:
            operation["summary"] = plan.summary
        if plan.description:
            operation["description"] = plan.description

        everything = plan.everything()  # the handler's parameters and its dependencies'
        annotated = {p.key: p.type for p in everything if p.source == "path"}
        parameters = [
            {"name": param, "in": "path", "required": True, "schema": _path_schema(param, given, annotated, pattern, custom)}
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
        elif form:
            files = any(_unwrap(p.type)[0] is UploadedFile for p in form)
            properties = {p.key: _with_default(_form_schema(p.type), p) for p in form}
            schema: dict[str, Any] = {"type": "object", "properties": properties}
            required = [p.key for p in form if p.required]
            if required:
                schema["required"] = required
            kind = "multipart/form-data" if files else "application/x-www-form-urlencoded"
            operation["requestBody"] = {"required": bool(required), "content": {kind: {"schema": schema}}}
        success: dict[str, Any] = {"description": "Successful response"}
        if plan.response is not None:
            success["content"] = {"application/json": {"schema": plan.response.schema(components)}}
        operation["responses"] = {"200": success}
        if plan.validates:
            components["ValidationError"] = VALIDATION_ERROR
            operation["responses"]["422"] = {
                "description": "Validation error",
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ValidationError"}}},
            }
        paths.setdefault(openapi_path(pattern), {})[method.lower()] = operation

    info: dict[str, Any] = {"title": title, "version": version}
    if description:
        info["description"] = description
    document: dict[str, Any] = {"openapi": "3.1.0", "info": info, "paths": paths}
    if components:
        document["components"] = {"schemas": dict(sorted(components.items()))}
    return document


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
