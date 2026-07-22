"""Tests for korvex.App / korvex.Request — a minimal ASGI application.

Exercised directly against the ASGI calling convention (scope/receive/send),
with no server or extra dependency involved: `asyncio.run` drives the
coroutine, and plain dicts/closures stand in for what a real ASGI server
would send/collect.
"""

import asyncio
import json

import pytest

from korvex import App, Response


def make_scope(method="GET", path="/", headers=None, query_string=b""):
    return {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in (headers or [])],
        "query_string": query_string,
    }


def run_app(app, scope, body=b""):
    sent = []
    body_sent = False

    async def receive():
        nonlocal body_sent
        if not body_sent:
            body_sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    return sent


def response_from(sent):
    start = next(m for m in sent if m["type"] == "http.response.start")
    body_msg = next(m for m in sent if m["type"] == "http.response.body")
    headers = [(name.decode("latin-1"), value.decode("latin-1")) for name, value in start["headers"]]
    return start["status"], headers, body_msg["body"]


def test_get_decorator_registers_route_and_handler():
    app = App()

    @app.get("/health")
    def health(request):
        return Response.text("ok")

    status, _, body = response_from(run_app(app, make_scope("GET", "/health")))
    assert status == 200
    assert body == b"ok"


def test_async_handler_is_supported():
    app = App()

    @app.get("/health")
    async def health(request):
        return Response.text("ok")

    status, _, body = response_from(run_app(app, make_scope("GET", "/health")))
    assert status == 200
    assert body == b"ok"


def test_route_decorator_returns_the_original_function():
    app = App()

    @app.get("/health")
    def health(request):
        return Response.text("ok")

    assert health.__name__ == "health"
    assert callable(health)


def test_path_params_are_passed_as_keyword_arguments():
    app = App()

    @app.get("/users/{id:int}")
    def get_user(request, id):
        return Response.json({"id": id})

    status, _, body = response_from(run_app(app, make_scope("GET", "/users/42")))
    assert status == 200
    assert json.loads(body) == {"id": "42"}


def test_returns_404_for_unknown_route():
    app = App()

    status, _, _ = response_from(run_app(app, make_scope("GET", "/unknown")))

    assert status == 404


def test_returns_405_with_allow_header_for_wrong_method():
    app = App()

    @app.get("/users")
    def list_users(request):
        return Response.text("ok")

    status, headers, _ = response_from(run_app(app, make_scope("POST", "/users")))

    assert status == 405
    assert ("Allow", "GET") in headers


def test_handler_decorator_attaches_to_an_existing_route_name():
    app = App()
    app.router.add_route("/health", "health_check")

    @app.handler("health_check")
    def health(request):
        return Response.text("ok")

    status, _, body = response_from(run_app(app, make_scope("GET", "/health")))
    assert status == 200
    assert body == b"ok"


def test_missing_handler_raises_lookup_error():
    app = App()
    app.router.add_route("/health", "health_check")

    with pytest.raises(LookupError):
        run_app(app, make_scope("GET", "/health"))


def test_middleware_short_circuits_before_the_handler_runs():
    app = App()
    handler_called = False

    @app.get("/secret", middleware=["auth"])
    def secret(request):
        nonlocal handler_called
        handler_called = True
        return Response.text("secret")

    @app.middleware("auth")
    def auth(request):
        return Response.text("Unauthorized", status=401)

    status, _, _ = response_from(run_app(app, make_scope("GET", "/secret")))

    assert status == 401
    assert handler_called is False


def test_middleware_returning_none_passes_through_to_handler():
    app = App()

    @app.get("/secret", middleware=["auth"])
    def secret(request):
        return Response.text("secret")

    @app.middleware("auth")
    def auth(request):
        return None

    status, _, body = response_from(run_app(app, make_scope("GET", "/secret")))

    assert status == 200
    assert body == b"secret"


def test_async_middleware_is_supported():
    app = App()

    @app.get("/secret", middleware=["auth"])
    def secret(request):
        return Response.text("secret")

    @app.middleware("auth")
    async def auth(request):
        return None

    status, _, body = response_from(run_app(app, make_scope("GET", "/secret")))
    assert status == 200
    assert body == b"secret"


def test_missing_middleware_raises_lookup_error():
    app = App()

    @app.get("/secret", middleware=["auth"])
    def secret(request):
        return Response.text("secret")

    with pytest.raises(LookupError):
        run_app(app, make_scope("GET", "/secret"))


def test_request_headers_are_available_case_insensitively():
    app = App()
    captured = {}

    @app.get("/echo")
    def echo(request):
        captured["value"] = request.headers.get("x-custom")
        return Response.text("ok")

    run_app(app, make_scope("GET", "/echo", headers=[("X-Custom", "hello")]))

    assert captured["value"] == "hello"


def test_request_query_params_are_parsed():
    app = App()
    captured = {}

    @app.get("/search")
    def search(request):
        captured["value"] = request.query_params
        return Response.text("ok")

    run_app(app, make_scope("GET", "/search", query_string=b"q=hello&page=2"))

    assert captured["value"] == {"q": ["hello"], "page": ["2"]}


def test_request_body_and_json():
    app = App()
    captured = {}

    @app.post("/echo")
    async def echo(request):
        captured["json"] = await request.json()
        return Response.text("ok")

    run_app(app, make_scope("POST", "/echo"), body=json.dumps({"a": 1}).encode())

    assert captured["json"] == {"a": 1}


def test_unsupported_scope_type_raises():
    app = App()

    with pytest.raises(NotImplementedError):
        run_app(app, {"type": "websocket"})


def test_lifespan_startup_and_shutdown_are_acknowledged():
    events = iter([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])
    sent = []

    async def receive():
        return next(events)

    async def send(message):
        sent.append(message)

    asyncio.run(App()({"type": "lifespan"}, receive, send))

    assert sent == [{"type": "lifespan.startup.complete"}, {"type": "lifespan.shutdown.complete"}]


def test_app_repr_reports_handler_and_middleware_counts():
    app = App()

    @app.get("/health")
    def health(request):
        return Response.text("ok")

    @app.middleware("auth")
    def auth(request):
        return None

    assert "handlers=1" in repr(app)
    assert "middleware=1" in repr(app)


def test_app_composes_with_route_groups():
    app = App()
    api = app.router.group("/api", middleware=["auth"])
    api.get("/users", "list_users")

    @app.handler("list_users")
    def list_users(request):
        return Response.text("users")

    @app.middleware("auth")
    def auth(request):
        return None

    status, _, body = response_from(run_app(app, make_scope("GET", "/api/users")))

    assert status == 200
    assert body == b"users"
