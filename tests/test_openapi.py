"""Tests for App.openapi(): the document is valid OpenAPI 3.1 and says
what the routes really do."""

from typing import Optional

import pytest

from korvex import App, Response

msgspec = pytest.importorskip("msgspec")
pydantic = pytest.importorskip("pydantic")
validator = pytest.importorskip("openapi_spec_validator")


class Address(pydantic.BaseModel):
    city: str


class UserIn(pydantic.BaseModel):  # pydantic, with a nested model
    name: str
    address: Address


class UserOut(msgspec.Struct):  # msgspec, in the same app
    id: int
    name: str


def sample_app(tmp_path):
    app = App()

    @app.post("/users")
    async def create_user(request, body: UserIn, notify: bool = False) -> UserOut:
        """Create a user.

        Sends a welcome email when notify is set."""

    @app.get("/users/{id:int}")
    def get_user(request, id) -> UserOut:
        """Get one user."""

    @app.get("/items/{key:uuid}/{name}")
    def get_item(request, key, name, page: int = 1, tags: list[str] = [], q: Optional[str] = None):
        return Response.text("item")

    @app.put("/users/{id}")
    def replace_user(request, id: int, body: Optional[UserIn] = None):
        return Response.text("ok")

    @app.get("/files/*path")
    def get_file(request, path):
        return Response.text("file")

    @app.get("/health")
    def health(request):
        return Response.text("ok")

    api = app.group("/v2")

    @api.get("/health")
    def health(request):  # noqa: F811 -- same name on purpose: operationIds must stay unique
        return Response.text("ok")

    @app.websocket("/ws")
    async def ws(socket):
        pass

    (tmp_path / "a.txt").write_text("a")
    app.static("/static", tmp_path)
    return app


@pytest.fixture
def document(tmp_path):
    doc = sample_app(tmp_path).openapi(title="Demo", version="1.0", description="A demo API")
    validator.validate(doc)  # raises if it isn't valid OpenAPI 3.1
    return doc


def test_info_and_paths(document):
    assert document["openapi"] == "3.1.0"
    assert document["info"] == {"title": "Demo", "version": "1.0", "description": "A demo API"}
    assert sorted(document["paths"]) == [
        "/files/{path}", "/health", "/items/{key}/{name}", "/users", "/users/{id}", "/v2/health",
    ]  # no static files, no WebSocket
    assert sorted(document["paths"]["/users/{id}"]) == ["get", "put"]


def test_parameters_are_typed(document):
    def params(path, method="get"):
        return {p["name"]: p for p in document["paths"][path][method]["parameters"]}

    assert params("/users/{id}")["id"]["schema"] == {"type": "integer"}  # from {id:int}
    assert params("/users/{id}", "put")["id"]["schema"] == {"type": "integer"}  # from the annotation
    item = params("/items/{key}/{name}")
    assert item["key"]["schema"] == {"type": "string", "format": "uuid"}
    assert item["name"]["schema"] == {"type": "string"}
    assert item["page"] == {"name": "page", "in": "query", "required": False, "schema": {"type": "integer", "default": 1}}
    assert item["tags"]["schema"] == {"type": "array", "items": {"type": "string"}}
    assert item["q"]["required"] is False
    assert params("/files/{path}")["path"]["schema"] == {"type": "string"}


def test_bodies_responses_and_models(document):
    create = document["paths"]["/users"]["post"]
    assert create["requestBody"] == {
        "required": True,
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/UserIn"}}},
    }
    assert create["responses"]["200"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/UserOut"}
    assert document["paths"]["/users/{id}"]["put"]["requestBody"]["required"] is False
    schemas = document["components"]["schemas"]
    assert {"UserIn", "UserOut", "Address", "ValidationError"} <= set(schemas)  # nested models included


def test_422_only_where_validation_can_fail(document):
    assert "422" in document["paths"]["/users"]["post"]["responses"]
    assert "422" in document["paths"]["/items/{key}/{name}"]["get"]["responses"]  # query params
    assert "422" not in document["paths"]["/health"]["get"]["responses"]
    assert "422" not in document["paths"]["/users/{id}"]["get"]["responses"]  # Rust already checks {id:int}


def test_docstrings_and_operation_ids(document):
    create = document["paths"]["/users"]["post"]
    assert create["summary"] == "Create a user."
    assert create["description"] == "Sends a welcome email when notify is set."
    ids = [op["operationId"] for path in document["paths"].values() for op in path.values()]
    assert len(ids) == len(set(ids)) and {"health", "health_2"} <= set(ids)


def test_an_empty_app_is_still_valid():
    doc = App().openapi()
    validator.validate(doc)
    assert doc["paths"] == {} and "components" not in doc
