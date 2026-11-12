"""Dataclasses and TypedDicts as models: body, return, form and OpenAPI,
validated by msgspec or by pydantic."""

import dataclasses
import sys
from typing import List, Optional  # noqa: UP035

import pytest
from support import request

from korvex import App, Form, Response

pydantic = pytest.importorskip("pydantic")
msgspec = pytest.importorskip("msgspec")
validator = pytest.importorskip("openapi_spec_validator")

if sys.version_info >= (3, 12):
    from typing import TypedDict
else:
    from typing_extensions import TypedDict


@dataclasses.dataclass
class Address:
    city: str


@dataclasses.dataclass
class Task:
    title: str
    done: bool = False
    address: Optional[Address] = None


@dataclasses.dataclass
class Team:
    name: str
    members: list[Task] = dataclasses.field(default_factory=list)
    tags: list[str] = dataclasses.field(default_factory=lambda: ["new"])
    lead: Optional[Task] = None


class Point(TypedDict):
    x: int
    y: int


class Options(TypedDict, total=False):
    verbose: bool


@dataclasses.dataclass
class Signup:
    name: str
    age: int = 0


@pytest.fixture(params=["msgspec", "pydantic"])
def library(request, monkeypatch):
    """Runs the test with msgspec, then with it hidden so pydantic is used."""
    if request.param == "pydantic":
        monkeypatch.setitem(sys.modules, "msgspec", None)
    return request.param


@pytest.fixture
def app(library):
    app = App()

    @app.post("/tasks")
    def add(task: Task) -> Task:
        return dataclasses.replace(task)

    @app.post("/batch")
    def batch(tasks: list[Task]) -> list[Task]:
        return tasks

    @app.post("/teams")
    def team(team: Team) -> Team:
        return team

    @app.post("/as-dict")
    def as_dict(task: Task):
        return Response.json({"asdict": dataclasses.asdict(task), "vars": sorted(vars(task))})

    @app.post("/points")
    def points(point: Point) -> list[Point]:
        return [point]

    @app.post("/options")
    def options(options: Options) -> list[Options]:
        return [options]

    @app.post("/signup")
    def signup(form: Signup = Form()) -> Signup:
        return form

    return app


def test_dataclass_body_round_trip(app):
    r = request(app, "POST", "/tasks", json={"title": "a", "address": {"city": "Paris"}})
    assert r.status == 200
    assert r.json() == {"title": "a", "done": False, "address": {"city": "Paris"}}


def test_dataclass_body_invalid(app):
    r = request(app, "POST", "/tasks", json={"done": "x", "address": {}})
    assert r.status == 422
    locs = [e["loc"] for e in r.json()["detail"]]
    assert all(loc[0] == "body" and len(loc) >= 2 for loc in locs)


def test_dataclass_missing_field_location(app):
    r = request(app, "POST", "/tasks", json={})
    assert r.status == 422
    assert r.json()["detail"][0]["loc"] == ["body", "title"]


def test_list_of_dataclasses(app):
    r = request(app, "POST", "/batch", json=[{"title": "a"}, {"title": "b", "done": True}])
    assert [t["title"] for t in r.json()] == ["a", "b"]
    assert request(app, "POST", "/batch", json=[{"title": 1}]).status == 422


def test_typeddict_body(app):
    assert request(app, "POST", "/points", json={"x": 1, "y": 2}).json() == [{"x": 1, "y": 2}]
    assert request(app, "POST", "/points", json={"x": 1}).status == 422


def test_typeddict_total_false(app):
    assert request(app, "POST", "/options", json={}).json() == [{}]
    assert request(app, "POST", "/options", json={"verbose": True}).json() == [{"verbose": True}]


def test_form_dataclass(app):
    r = request(app, "POST", "/signup", form={"name": "Ann", "age": "30"})
    assert r.json() == {"name": "Ann", "age": 30}
    assert request(app, "POST", "/signup", form={"age": "x"}).status == 422


def test_openapi_components(app):
    doc = app.openapi(title="T", version="1")
    validator.validate(doc)
    schemas = doc["components"]["schemas"]
    assert {"Task", "Point", "Signup"} <= set(schemas)
    assert doc["paths"]["/tasks"]["post"]["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/Task"
    }


def test_neither_library_installed(monkeypatch):
    monkeypatch.setitem(sys.modules, "msgspec", None)
    monkeypatch.setitem(sys.modules, "pydantic", None)
    app = App()
    with pytest.raises(TypeError, match="validating dataclass .*Task needs msgspec or pydantic"):

        @app.post("/tasks")
        def add(task: Task):
            return None

    with pytest.raises(TypeError, match="validating TypedDict Point needs msgspec or pydantic"):

        @app.post("/points")
        def points(point: Point):
            return None


def test_omitted_default_is_encoded(app):
    r = request(app, "POST", "/tasks", json={"title": "a"})
    assert r.json() == {"title": "a", "done": False, "address": None}


def test_nested_defaults_and_factories(app):
    body = {"name": "t", "lead": {"title": "x"}}
    r = request(app, "POST", "/teams", json=body)
    assert r.json() == {
        "name": "t",
        "members": [],
        "tags": ["new"],
        "lead": {"title": "x", "done": False, "address": None},
    }
    r = request(app, "POST", "/teams", json={"name": "t", "members": [{"title": "m"}]})
    assert r.json()["members"] == [{"title": "m", "done": False, "address": None}]


def test_list_of_dataclasses_defaults(app):
    r = request(app, "POST", "/batch", json=[{"title": "a"}])
    assert r.json() == [{"title": "a", "done": False, "address": None}]


def test_handler_sees_every_field(app):
    r = request(app, "POST", "/as-dict", json={"title": "a"})
    assert r.json() == {
        "asdict": {"title": "a", "done": False, "address": None},
        "vars": ["address", "done", "title"],
    }


@dataclasses.dataclass
class Node:
    children: List["Node"] = dataclasses.field(default_factory=list)  # noqa: UP006 (3.9 cannot resolve list["X"])
    tags: list[str] = dataclasses.field(default_factory=lambda: ["x"])


def _nested(depth):
    return b'{"children":[' * depth + b"]}" * depth


@pytest.fixture
def tree_app(library):
    app = App()

    @app.post("/tree")
    def tree(node: Node):
        return Response.json({"tags": node.tags})

    @app.get("/ok")
    def ok():
        return Response.json({"ok": True})

    return app


@pytest.mark.parametrize("depth", [1000, 20000])
def test_deeply_nested_dataclass_is_422(tree_app, library, depth):
    r = request(tree_app, "POST", "/tree", body=_nested(depth), headers={"content-type": "application/json"})
    assert r.status == 422
    if library == "msgspec":
        error = r.json()["detail"][0]
        assert (error["loc"], error["msg"]) == (["body"], "nested too deeply")
    assert request(tree_app, "GET", "/ok").status == 200


def test_moderate_nesting_fills_defaults(tree_app):
    r = request(tree_app, "POST", "/tree", body=_nested(50), headers={"content-type": "application/json"})
    assert r.status == 200
    assert r.json() == {"tags": ["x"]}
