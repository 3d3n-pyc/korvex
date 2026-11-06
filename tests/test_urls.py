"""Tests for reverse URLs: `app.url_for()` and `request.url_for()`."""

import datetime
import uuid

import pytest

from korvex import App, Response, WebSocket
from korvex.testing import TestClient


def make_app():
    app = App()
    app.constraint("slug", r"[a-z0-9]+(?:-[a-z0-9]+)*")

    @app.get("/users/{id:int}")
    def get_user(request, id):
        return Response.text("user")

    @app.get("/users/me")
    def me(request):
        return Response.text("me")

    @app.get("/users/{id:int}/files/*path")
    def user_file(request, id, path):
        return Response.text(path)

    @app.get("/people/{name}")
    def person(request, name):
        return Response.text(name)

    @app.get("/people/admin")
    def admin(request):
        return Response.text("admin")

    @app.get("/posts/{post:slug}", name="post")
    def show_post(request, post):
        return Response.text(post)

    @app.get("/tasks/{id:uuid}", name="task")
    def get_task(request, id):
        return Response.text(str(id))

    @app.delete("/tasks/{id:uuid}", name="task")
    def delete_task(request, id):
        return Response.text("deleted")

    @app.get("/days/{day}")
    def on_day(request, day: datetime.date):
        return Response.text(day.isoformat())

    @app.get("/flags/{on}")
    def flag(request, on: bool):
        return Response.text(str(on))

    @app.get("/")
    def home(request):
        return Response.text(request.url_for("get_user", id=1))

    @app.websocket("/live/{room}")
    async def live(ws: WebSocket, room):
        await ws.accept()
        await ws.send_text(ws.url_for("live", room=room))

    return app


def test_paths_with_parameters():
    app = make_app()
    assert app.url_for("get_user", id=42) == "/users/42"
    assert app.url_for("get_user", id="-7") == "/users/-7"
    assert app.url_for("home") == "/"
    assert app.url_for("post", post="hello-world") == "/posts/hello-world"
    some = uuid.UUID(int=5)
    assert app.url_for("task", id=some) == f"/tasks/{some}"
    assert app.url_for("on_day", day=datetime.date(2026, 11, 6)) == "/days/2026-11-06"
    assert app.url_for("flag", on=True) == "/flags/true"
    assert app.url_for("live", room="lobby") == "/live/lobby"


def test_values_are_percent_encoded_and_round_trip():
    app = make_app()
    for name in ["café au lait", "a?b#c", "100%", "x+y&z=1", "Ünïcødé"]:
        url = app.url_for("person", name=name)
        assert "?" not in url and "#" not in url and " " not in url
        with TestClient(app) as client:
            assert client.get(url).text == name
    assert app.url_for("user_file", id=1, path="docs/a b.txt") == "/users/1/files/docs/a%20b.txt"


def test_names_given_or_from_the_function():
    app = make_app()
    with pytest.raises(LookupError, match="no route named 'show_post'"):
        app.url_for("show_post")  # it was given a name: "post"
    with pytest.raises(LookupError, match="no route named 'nothing'"):
        app.url_for("nothing")


def test_one_name_for_several_methods_on_a_path():
    app = make_app()
    assert app.url_for("task", id=uuid.UUID(int=1)) == f"/tasks/{uuid.UUID(int=1)}"


def test_a_given_name_is_unique():
    app = App()

    @app.get("/a", name="page")
    def a(request): ...

    with pytest.raises(ValueError, match="'page' is already taken by /a"):

        @app.get("/b", name="page")
        def b(request): ...

    assert app.url_for("page") == "/a"  # the refused route left no trace
    with pytest.raises(ValueError, match="non-empty string"):
        app.get("/c", name="")


def test_function_names_shared_by_routes_are_ambiguous_only_when_used():
    app = App()

    def factory(prefix):
        group = app.group(prefix)

        @group.get("/items")
        def items(request): ...

    factory("/one")
    factory("/two")
    with pytest.raises(LookupError, match=r"'items' names several routes \(GET /one/items, GET /two/items\)"):
        app.url_for("items")

    @app.get("/x")
    @app.get("/y")
    def stacked(request): ...

    with pytest.raises(LookupError, match="several routes"):
        app.url_for("stacked")


def test_a_given_name_wins_over_function_names():
    app = App()

    @app.get("/old")
    def page(request): ...

    @app.get("/new", name="page")
    def new_page(request): ...

    assert app.url_for("page") == "/new"


def test_missing_extra_and_mistyped_parameters():
    app = make_app()
    with pytest.raises(ValueError, match="missing id"):
        app.url_for("get_user")
    with pytest.raises(ValueError, match="unexpected page"):
        app.url_for("get_user", id=1, page=2)
    with pytest.raises(TypeError, match="can't be of type list"):
        app.url_for("get_user", id=[1])


def test_constraints_are_checked():
    app = make_app()
    with pytest.raises(ValueError, match=r"'abc' isn't an integer, for \{id:int\}"):
        app.url_for("get_user", id="abc")
    with pytest.raises(ValueError, match="isn't an integer"):
        app.url_for("get_user", id=2**63)  # beyond what the router takes
    with pytest.raises(ValueError, match="isn't a UUID"):
        app.url_for("task", id="not-a-uuid")
    with pytest.raises(ValueError, match="fails its constraint"):
        app.url_for("post", post="Not A Slug")


def test_annotations_are_checked():
    app = make_app()
    with pytest.raises(ValueError, match=r"day='tomorrow' isn't a valid date"):
        app.url_for("on_day", day="tomorrow")
    with pytest.raises(ValueError, match="isn't a valid boolean"):
        app.url_for("flag", on="maybe")


def test_values_that_cant_be_in_a_path():
    app = make_app()
    for value, problem in [("", "an empty part"), (".", "'.'"), ("..", "'..'"), ("a/b", "has a '/'")]:
        with pytest.raises(ValueError, match=problem):
            app.url_for("person", name=value)
    for value in ["../etc/passwd", "a//b", "a/./b", ""]:
        with pytest.raises(ValueError):
            app.url_for("user_file", id=1, path=value)


def test_a_path_another_route_answers_first():
    app = make_app()
    with pytest.raises(ValueError, match=r"'/people/admin' would reach route 'admin' \(GET /people/admin\) instead"):
        app.url_for("person", name="admin")
    assert app.url_for("person", name="ada") == "/people/ada"


def test_static_files():
    app = App()
    app.static("/static", ".")
    app.static("/media", ".", name="media")
    assert app.url_for("static", path="css/app.css") == "/static/css/app.css"
    assert app.url_for("media", path="a b.png") == "/media/a%20b.png"


def test_routes_added_later_are_found():
    app = App()
    with pytest.raises(LookupError):
        app.url_for("late")

    @app.get("/late")
    def late(request): ...

    assert app.url_for("late") == "/late"
    app.router.get("/raw/{id}", "raw_handler")  # a route added to the router directly
    assert app.url_for("raw_handler", id="1") == "/raw/1"


def test_request_url_for_is_absolute():
    app = make_app()
    with TestClient(app) as client:
        assert client.get("/").text == "http://testserver/users/1"
        assert client.get("https://example.com:8443/").text == "https://example.com:8443/users/1"
        with client.websocket("wss://example.com/live/lobby") as ws:
            assert ws.receive_text() == "wss://example.com/live/lobby"


def test_request_url_for_another_host():
    app = App()
    tenants = app.host("{tenant}.example.com")
    api = app.host("api.example.com")

    @tenants.get("/projects/{id:int}")
    def project(request, tenant, id): ...

    @api.get("/status")
    def status(request): ...

    @app.get("/link")
    def link(request):
        return Response.text(" ".join([request.url_for("project", tenant="acme", id=3), request.url_for("status")]))

    assert app.url_for("project", id=3) == "/projects/3"  # the path, for a link on that host
    with pytest.raises(ValueError, match="a host parameter"):
        app.url_for("project", tenant="acme", id=3)
    with TestClient(app) as client:
        assert client.get("http://www.example.com:8000/link").text == (
            "http://acme.example.com:8000/projects/3 http://api.example.com:8000/status"
        )


def test_request_url_for_checks_host_parameters_and_header():
    app = App()
    tenants = app.host("{tenant}.example.com")

    @tenants.get("/")
    def home(request, tenant): ...

    @app.get("/link/{to}")
    def link(request, to):
        return Response.text(request.url_for("home", tenant=to))

    @app.get("/missing")
    def missing(request):
        return Response.text(request.url_for("home"))

    with TestClient(app) as client:
        assert client.get("/link/acme").text == "http://acme.example.com/"
        with pytest.raises(ValueError, match="isn't one label of a host name"):
            client.get("/link/evil.com%2F")
        with pytest.raises(ValueError, match="missing tenant"):
            client.get("/missing")
        with pytest.raises(ValueError, match="isn't a host name"):
            client.get("/link/acme", headers={"Host": "a b"})


def test_request_url_for_needs_an_app():
    from korvex import Request

    request = Request({"type": "http", "path": "/", "headers": []}, None, {})  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="doesn't come from an App"):
        request.url_for("x")
