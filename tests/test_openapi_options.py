"""Tests for the OpenAPI options of routes and groups (status, tags,
deprecated, responses, include_in_schema), security schemes, and what
`status` and `-> None` do at runtime."""

import uuid

import pytest

from korvex import App, Depends, HTTPException, Response
from korvex.security import APIKey, Bearer, OAuth2Password
from korvex.testing import TestClient

msgspec = pytest.importorskip("msgspec")
validator = pytest.importorskip("openapi_spec_validator")


class Task(msgspec.Struct):
    id: str
    title: str


class NewTask(msgspec.Struct):
    title: str


class Conflict(msgspec.Struct):
    existing: str


def make_app():
    app = App()
    bearer = Bearer(format="JWT")

    def current_user(token: str = Depends(bearer)) -> str:
        if token != "good":
            raise HTTPException(401, "invalid token", headers=[("WWW-Authenticate", "Bearer")])
        return "ada"

    api = app.group("/api", dependencies=[Depends(current_user)], tags=["api"], responses={403: "Not yours"})

    @api.post("/tasks", status=201, tags=["tasks"], responses={409: ("Title taken", Conflict)})
    def create_task(request, body: NewTask) -> Task:
        """Create a task"""
        return Task(id="1", title=body.title)

    @api.delete("/tasks/{id}", status=204, tags=["tasks"], responses={404: "No such task"})
    def delete_task(request, id: str) -> None:
        if id == "missing":
            raise HTTPException(404)

    @app.get("/v1/tasks", deprecated=True)
    def old_tasks(request) -> list[Task]:
        return []

    @app.get("/health", include_in_schema=False)
    def health(request):
        return Response.text("ok")

    hidden = app.group("/internal", include_in_schema=False)

    @hidden.get("/stats")
    def stats(request):
        return Response.text("{}")

    old = app.group("/v0", deprecated=True, tags=["legacy"])

    @old.get("/ping", tags=["legacy", "ping"])
    def ping(request):
        return Response.text("pong")

    return app


@pytest.fixture
def document():
    doc = make_app().openapi()
    validator.validate(doc)
    return doc


def test_status_is_the_documented_success(document):
    create = document["paths"]["/api/tasks"]["post"]
    assert "content" in create["responses"]["201"] and "200" not in create["responses"]
    delete = document["paths"]["/api/tasks/{id}"]["delete"]
    assert delete["responses"]["204"] == {"description": "Successful response"}  # no body


def test_tags_from_groups_and_routes(document):
    assert document["paths"]["/api/tasks"]["post"]["tags"] == ["api", "tasks"]
    assert document["paths"]["/v0/ping"]["get"]["tags"] == ["legacy", "ping"]  # each once
    assert "tags" not in document["paths"]["/v1/tasks"]["get"]


def test_deprecated_and_hidden(document):
    assert document["paths"]["/v1/tasks"]["get"]["deprecated"] is True
    assert document["paths"]["/v0/ping"]["get"]["deprecated"] is True  # from its group
    assert "deprecated" not in document["paths"]["/api/tasks"]["post"]
    assert "/health" not in document["paths"] and "/internal/stats" not in document["paths"]


def test_documented_responses(document):
    responses = document["paths"]["/api/tasks"]["post"]["responses"]
    assert list(responses) == ["201", "401", "403", "409", "422"]  # sorted
    assert responses["403"] == {"description": "Not yours"}  # the group's
    assert responses["409"] == {
        "description": "Title taken",
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Conflict"}}},
    }
    assert "Conflict" in document["components"]["schemas"]
    assert document["paths"]["/api/tasks/{id}"]["delete"]["responses"]["404"] == {"description": "No such task"}


def test_responses_override_and_ranges():
    app = App()

    @app.get("/a", responses={200: "The thing", "4XX": "Client error", "default": Conflict, 500: Conflict})
    def a(request) -> Task: ...

    responses = app.openapi()["paths"]["/a"]["get"]["responses"]
    assert responses["200"]["description"] == "The thing" and "content" in responses["200"]
    assert responses["4XX"] == {"description": "Client error"}
    assert responses["500"]["description"] == "Internal Server Error"
    assert responses["default"]["description"] == "Response"
    validator.validate(app.openapi())


def test_security_scheme_from_a_dependency(document):
    assert document["components"]["securitySchemes"] == {
        "bearer": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
    }
    create = document["paths"]["/api/tasks"]["post"]
    assert create["security"] == [{"bearer": []}]
    assert create["responses"]["401"] == {"description": "Not authenticated"}
    assert "security" not in document["paths"]["/v1/tasks"]["get"]


def test_several_schemes_required_or_alternatives():
    app = App()
    bearer, key = Bearer(), APIKey(header="X-API-Key")
    optional_bearer, optional_key = Bearer(required=False), APIKey(query="key", required=False, name="query_key")
    oauth = OAuth2Password("/token", scopes={"tasks:write": "Change tasks"}, description="Sign in")

    def both(token: str = Depends(bearer), api_key: str = Depends(key)) -> None: ...

    def either(token=Depends(optional_bearer), api_key=Depends(optional_key)) -> None: ...

    @app.get("/both", dependencies=[Depends(both)])
    def needs_both(request): ...

    @app.get("/either", dependencies=[Depends(either)])
    def needs_either(request): ...

    @app.get("/oauth", dependencies=[Depends(oauth)])
    def with_oauth(request): ...

    doc = app.openapi()
    validator.validate(doc)
    assert doc["paths"]["/both"]["get"]["security"] == [{"bearer": [], "api_key": []}]
    # Optional or not, a Bearer is defined the same: one name for both.
    assert doc["paths"]["/either"]["get"]["security"] == [{"bearer": []}, {"query_key": []}]
    assert "401" not in doc["paths"]["/either"]["get"]["responses"]
    assert doc["components"]["securitySchemes"] == {
        "api_key": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
        "bearer": {"type": "http", "scheme": "bearer"},
        "oauth2": {
            "type": "oauth2",
            "description": "Sign in",
            "flows": {"password": {"tokenUrl": "/token", "scopes": {"tasks:write": "Change tasks"}}},
        },
        "query_key": {"type": "apiKey", "in": "query", "name": "key"},
    }


def test_schemes_sharing_a_name():
    app = App()
    one, two, jwt = Bearer(), Bearer(), Bearer(format="JWT")  # the first two defined the same

    @app.get("/one", dependencies=[Depends(one)])
    def a(request): ...

    @app.get("/two", dependencies=[Depends(two)])
    def b(request): ...

    @app.get("/three", dependencies=[Depends(jwt)])
    def c(request): ...

    doc = app.openapi()
    assert list(doc["components"]["securitySchemes"]) == ["bearer", "bearer_2"]
    assert doc["paths"]["/two"]["get"]["security"] == [{"bearer": []}]
    assert doc["paths"]["/three"]["get"]["security"] == [{"bearer_2": []}]


def test_schemes_at_runtime():
    app = App()
    bearer, header_key, query_key, cookie_key = (
        Bearer(),
        APIKey(header="X-Key"),
        APIKey(query="key"),
        APIKey(cookie="key"),
    )
    optional = Bearer(required=False)

    @app.get("/bearer")
    def b(request, token: str = Depends(bearer)):
        return Response.text(token)

    @app.get("/keys")
    def k(request, h: str = Depends(header_key)):
        return Response.text(h)

    @app.get("/query")
    def q(request, v: str = Depends(query_key)):
        return Response.text(v)

    @app.get("/cookie")
    def c(request, v: str = Depends(cookie_key)):
        return Response.text(v)

    @app.get("/maybe")
    def m(request, token=Depends(optional)):
        return Response.text(repr(token))

    with TestClient(app) as client:
        assert client.get("/bearer", headers={"Authorization": "bearer abc"}).text == "abc"
        for headers in ({}, {"Authorization": "Basic abc"}, {"Authorization": "Bearer "}):
            refused = client.get("/bearer", headers=headers)
            assert (refused.status, refused.text, refused.headers["www-authenticate"]) == (
                401,
                "Not authenticated",
                "Bearer",
            )
        assert client.get("/keys", headers={"X-Key": "k1"}).text == "k1"
        assert client.get("/keys").headers["www-authenticate"] == "APIKey"
        assert client.get("/query?key=k2").text == "k2"
        assert client.get("/cookie", cookies={"key": "k3"}).text == "k3"
        assert client.get("/maybe").text == "None"
        assert client.get("/maybe", headers={"Authorization": "Bearer t"}).text == "'t'"
        app.dependency_overrides[bearer] = lambda: "test"
        assert client.get("/bearer").text == "test"


def test_scheme_on_a_websocket():
    from korvex import WebSocket, WebSocketDisconnect

    app = App()
    key = APIKey(cookie="session")

    @app.websocket("/live")
    async def live(ws: WebSocket, session: str = Depends(key)):
        await ws.accept()
        await ws.send_text(session)

    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket("/live"):
                pass
        with client.websocket("/live", cookies={"session": "s"}) as ws:
            assert ws.receive_text() == "s"


def test_apikey_takes_one_place():
    with pytest.raises(TypeError, match="one of"):
        APIKey()
    with pytest.raises(TypeError, match="one of"):
        APIKey(header="a", query="b")


def test_status_at_runtime():
    with TestClient(make_app()) as client:
        headers = {"Authorization": "Bearer good"}
        created = client.post("/api/tasks", json={"title": "x"}, headers=headers)
        assert (created.status, created.json()) == (201, {"id": "1", "title": "x"})
        deleted = client.delete("/api/tasks/1", headers=headers)
        assert (deleted.status, deleted.body) == (204, b"")
        assert client.delete("/api/tasks/missing", headers=headers).status == 404
        assert client.post("/api/tasks", json={"title": "x"}, headers={"Authorization": "Bearer bad"}).status == 401


def test_a_returned_response_keeps_its_status():
    app = App()

    @app.post("/x", status=201)
    def x(request) -> None:
        return Response.text("mine", status=202)

    with TestClient(app) as client:
        assert client.post("/x").status == 202


def test_none_without_annotation_is_a_clear_500():
    app = App()

    @app.get("/forgot")
    def forgot(request):
        pass  # forgot to return

    @app.get("/dict")
    def gives_dict(request):
        return {"a": 1}

    with TestClient(app) as client:
        with pytest.raises(TypeError, match=r"GET /forgot: the handler returned NoneType, not a Response"):
            client.get("/forgot")
        with pytest.raises(TypeError, match="returned dict"):
            client.get("/dict")
    with TestClient(app, raise_server_errors=False) as client:
        assert client.get("/forgot").status == 500


def test_empty_response_with_the_default_status():
    app = App()

    @app.post("/ping")
    async def ping(request) -> None:
        pass

    with TestClient(app) as client:
        response = client.post("/ping")
    assert (response.status, response.body) == (200, b"")


def test_options_are_checked_at_registration():
    app = App()
    for status in (99, 600, "201", True):
        with pytest.raises(ValueError, match="HTTP status"):
            app.get("/s", status=status)
    with pytest.raises(TypeError, match="a 204 response has no body"):

        @app.get("/t", status=204)
        def t(request) -> Task: ...

    with pytest.raises(TypeError, match="list of names"):
        app.get("/u", tags="users")
    with pytest.raises(ValueError, match="isn't a status"):
        app.get("/v", responses={"404": "x"})
    with pytest.raises(ValueError, match="isn't a status"):
        app.get("/v", responses={"6XX": "x"})
    with pytest.raises(TypeError, match="expected a description"):
        app.get("/w", responses={404: dict})
    with pytest.raises(TypeError, match="expected a description"):
        app.group("/x", responses={404: 1})


def test_host_groups_take_the_options():
    app = App()
    api = app.host("api.example.com", tags=["api"], deprecated=True)

    @api.get("/status")
    def status(request): ...

    operation = app.openapi(host="api.example.com")["paths"]["/status"]["get"]
    assert operation["tags"] == ["api"] and operation["deprecated"] is True


def test_uuid_still_works_with_status():
    app = App()

    @app.post("/things/{id:uuid}", status=202)
    def accept(request, id) -> Task:
        return Task(id=str(id), title="t")

    with TestClient(app) as client:
        some = uuid.UUID(int=3)
        response = client.post(app.url_for("accept", id=some))
    assert (response.status, response.json()["id"]) == (202, str(some))
