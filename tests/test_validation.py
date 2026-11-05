"""Tests for validation from type annotations, with msgspec and pydantic
models alike: every test runs once with each."""

import json
from typing import Optional
from uuid import UUID

import pytest
from test_app import make_scope, response_from, run_app

from korvex import App, Response

msgspec = pytest.importorskip("msgspec")
pydantic = pytest.importorskip("pydantic")


class MsgspecUserIn(msgspec.Struct):
    name: str
    age: int


class MsgspecUserOut(msgspec.Struct):
    id: int
    name: str


class PydanticUserIn(pydantic.BaseModel):
    name: str
    age: int


class PydanticUserOut(pydantic.BaseModel):
    id: int
    name: str


@pytest.fixture(params=["msgspec", "pydantic"])
def models(request):
    if request.param == "msgspec":
        return MsgspecUserIn, MsgspecUserOut, lambda cls, **kw: cls(**kw)
    return PydanticUserIn, PydanticUserOut, lambda cls, **kw: cls(**kw)


def call(app, method, path, body=b"", query=b""):
    scope = make_scope(method, path, headers=[("Content-Type", "application/json")], query_string=query)
    status, headers, raw = response_from(run_app(app, scope, body=body))
    return status, dict(headers), json.loads(raw) if raw else None


def user_app(models):
    UserIn, UserOut, make = models
    app = App()

    @app.post("/users")
    async def create(request, body: UserIn, notify: bool = False) -> UserOut:
        assert isinstance(body, UserIn)
        return make(UserOut, id=1, name=f"{body.name}{'!' if notify else ''}")

    @app.post("/users/batch")
    async def batch(request, body: list[UserIn]) -> list[UserOut]:
        return [make(UserOut, id=i, name=user.name) for i, user in enumerate(body)]

    @app.put("/users/{id}")
    async def replace(request, id: int, body: Optional[UserIn] = None) -> UserOut:
        return make(UserOut, id=id, name=body.name if body else "unchanged")

    return app


def test_body_is_validated_and_the_return_value_encoded(models):
    status, headers, data = call(user_app(models), "POST", "/users", b'{"name": "Ada", "age": 36}', b"notify=yes")
    assert (status, data) == (200, {"id": 1, "name": "Ada!"})
    assert headers["Content-Type"] == "application/json"


def test_invalid_bodies_get_a_422_with_the_details(models):
    app = user_app(models)
    status, _, data = call(app, "POST", "/users", b'{"name": "Ada", "age": "old"}')
    assert status == 422 and data["detail"][0]["loc"] == ["body", "age"]
    status, _, data = call(app, "POST", "/users", b'{"name": "Ada"}')
    assert status == 422 and data["detail"][0]["loc"] == ["body", "age"] and data["detail"][0]["type"] == "missing"
    status, _, data = call(app, "POST", "/users", b"{not json")
    assert status == 422 and data["detail"][0]["loc"] == ["body"]
    status, _, data = call(app, "POST", "/users", b"")  # a required body
    assert status == 422


def test_lists_of_models(models):
    body = json.dumps([{"name": "a", "age": 1}, {"name": "b", "age": 2}]).encode()
    status, _, data = call(user_app(models), "POST", "/users/batch", body)
    assert (status, data) == (200, [{"id": 0, "name": "a"}, {"id": 1, "name": "b"}])
    status, _, data = call(user_app(models), "POST", "/users/batch", b'[{"name": "a", "age": 1}, {"name": "b"}]')
    assert status == 422 and data["detail"][0]["loc"][:2] == ["body", 1]


def test_optional_body_and_converted_path_parameter(models):
    app = user_app(models)
    assert call(app, "PUT", "/users/7")[2] == {"id": 7, "name": "unchanged"}  # no body: the default
    assert call(app, "PUT", "/users/7", b'{"name": "Bo", "age": 3}')[2] == {"id": 7, "name": "Bo"}
    status, _, data = call(app, "PUT", "/users/seven")  # `{id}` has no constraint: the annotation converts
    assert status == 422 and data["detail"][0]["loc"] == ["path", "id"]


def test_query_parameters():
    app = App()

    @app.get("/search")
    def search(request, q: str, limit: int = 10, tags: list[str] = [], exact: Optional[bool] = None, after: Optional[UUID] = None):
        return Response.json({"q": q, "limit": limit, "tags": tags, "exact": exact, "after": str(after) if after else None})

    assert call(app, "GET", "/search", query=b"q=rust")[2] == {"q": "rust", "limit": 10, "tags": [], "exact": None, "after": None}
    uid = "123e4567-e89b-12d3-a456-426614174000"
    data = call(app, "GET", "/search", query=f"q=a&limit=3&tags=x&tags=y&exact=false&after={uid}".encode())[2]
    assert data == {"q": "a", "limit": 3, "tags": ["x", "y"], "exact": False, "after": uid}
    status, _, data = call(app, "GET", "/search", query=b"limit=abc&exact=maybe")
    assert status == 422
    assert [e["loc"] for e in data["detail"]] == [["query", "q"], ["query", "limit"], ["query", "exact"]]  # all at once


def test_handlers_without_annotations_are_called_as_before():
    app = App()

    @app.get("/users/{id:int}")
    def user(request, id):
        return Response.json({"id": id})

    @app.get("/typed/{id:int}")
    def typed(request, id: int) -> Response:  # the pattern already gives an int: nothing to do
        return Response.json({"id": id})

    assert not app._plans["test_validation.test_handlers_without_annotations_are_called_as_before.<locals>.user"].params
    assert not app._plans["test_validation.test_handlers_without_annotations_are_called_as_before.<locals>.typed"].params
    assert call(app, "GET", "/typed/5")[2] == {"id": 5}


def test_returning_a_response_still_works_with_a_return_model(models):
    UserIn, UserOut, _ = models
    app = App()

    @app.get("/maybe")
    def maybe(request) -> UserOut:
        return Response.text("not today", status=503)

    assert response_from(run_app(app, make_scope("GET", "/maybe")))[:3:2] == (503, b"not today")


def test_unclear_parameters_are_refused_at_registration(models):
    UserIn, _, _ = models
    app = App()
    with pytest.raises(TypeError, match="can't tell where parameter 'user'"):

        @app.post("/a")
        def unclear(request, user):
            pass

    with pytest.raises(TypeError, match="only one parameter can be the request body"):

        @app.post("/b")
        def two_bodies(request, a: UserIn, b: UserIn):
            pass


def test_the_422_can_be_customized(models):
    app = user_app(models)

    @app.exception_handler(422)
    def invalid(request, exc):
        return Response.json({"first": exc.errors[0]["loc"]}, status=400)

    status, _, data = call(app, "POST", "/users", b"{}")
    assert status == 400 and data["first"][0] == "body"


def test_how_many_body_errors_depends_on_the_library(models):
    """pydantic reports every problem in a body; msgspec stops at the first,
    which is part of why it's faster."""
    UserIn = models[0]
    status, _, data = call(user_app(models), "POST", "/users", b"{}")  # both fields missing
    assert status == 422
    assert len(data["detail"]) == (1 if UserIn is MsgspecUserIn else 2)


def test_handlers_attached_by_name_get_their_plan_on_first_use(models):
    UserIn, _, _ = models
    app = App()
    app.router.route("/things/{id}", "update_thing", method="POST")

    @app.handler("update_thing")
    def update(request, id: int, body: UserIn):
        return Response.json({"id": id, "name": body.name})

    assert call(app, "POST", "/things/4", b'{"name": "x", "age": 1}')[2] == {"id": 4, "name": "x"}
    assert call(app, "POST", "/things/four", b'{"name": "x", "age": 1}')[0] == 422
