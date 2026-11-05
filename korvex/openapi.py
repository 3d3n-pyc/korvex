"""An OpenAPI 3.1 description of an App, built from its routes: path
patterns, typed parameters, msgspec or pydantic models, and docstrings.
See App.openapi() and App.docs()."""

from __future__ import annotations

import re
import uuid
from typing import TYPE_CHECKING, Any

from korvex.schema import Model, path_params, scalar_schema

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


def build(app: App, *, title: str, version: str, description: str | None = None) -> dict[str, Any]:
    components: dict[str, Any] = {}
    paths: dict[str, dict[str, Any]] = {}
    used_ids: set[str] = set()
    for pattern, method, name, _ in app.router.routes():
        if name.startswith("korvex.") or name not in app._handlers:
            continue  # static files and the docs themselves; names without a handler can't be described
        func = app._handlers[name]
        plan = app._plan_of(name)
        operation: dict[str, Any] = {"operationId": _unique(getattr(func, "__name__", "handler"), used_ids)}
        if plan.summary:
            operation["summary"] = plan.summary
        if plan.description:
            operation["description"] = plan.description

        annotated = {p.name: p.type for p in plan.path}
        parameters = [
            {"name": param, "in": "path", "required": True, "schema": scalar_schema(annotated.get(param, given))}
            for param, given in path_params(pattern).items()
        ]
        for param in plan.query:
            entry: dict[str, Any] = {"name": param.name, "in": "query", "required": param.required}
            entry["schema"] = scalar_schema(param.type)
            if not param.required and param.default is not None and param.default != []:
                entry["schema"] = {**entry["schema"], "default": _jsonable(param.default)}
            parameters.append(entry)
        if parameters:
            operation["parameters"] = parameters

        if plan.body is not None:
            operation["requestBody"] = {
                "required": plan.body.required,
                "content": {"application/json": {"schema": Model(plan.body.type).schema(components)}},
            }
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


def _unique(name: str, used: set[str]) -> str:
    candidate, n = name, 2
    while candidate in used:
        candidate, n = f"{name}_{n}", n + 1
    used.add(candidate)
    return candidate


def _jsonable(value: Any) -> Any:
    return str(value) if isinstance(value, uuid.UUID) else value
