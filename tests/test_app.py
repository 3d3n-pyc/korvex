"""Tests for `App` and `Request`, called directly through ASGI (no server)."""

import asyncio
import json

import pytest

from korvex import App, HTTPException, Response


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
    assert json.loads(body) == {"id": 42}


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
    app.router.route("/health", "health_check")

    @app.handler("health_check")
    def health(request):
        return Response.text("ok")

    status, _, body = response_from(run_app(app, make_scope("GET", "/health")))
    assert status == 200
    assert body == b"ok"


def test_missing_handler_is_a_logged_500(caplog):
    app = App()
    app.router.route("/health", "health_check")

    status, _, _ = response_from(run_app(app, make_scope("GET", "/health")))

    assert status == 500
    assert "no handler named 'health_check'" in caplog.text


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


def test_missing_middleware_is_a_logged_500(caplog):
    app = App()

    @app.get("/secret", middleware=["auth"])
    def secret(request):
        return Response.text("secret")

    status, _, _ = response_from(run_app(app, make_scope("GET", "/secret")))

    assert status == 500
    assert "no middleware named 'auth'" in caplog.text


def test_http_exception_becomes_its_response():
    app = App()

    @app.get("/users/{id:int}")
    def get_user(request, id):
        raise HTTPException(404, f"no user {id}", headers=[("X-Reason", "missing")])

    status, headers, body = response_from(run_app(app, make_scope("GET", "/users/7")))

    assert (status, body) == (404, b"no user 7")
    assert ("X-Reason", "missing") in headers


def test_http_exception_defaults_to_the_standard_reason():
    assert HTTPException(403).detail == "Forbidden"


def test_http_exception_works_in_middleware():
    app = App()

    @app.get("/secret", middleware=["auth"])
    def secret(request):
        return Response.text("secret")

    @app.middleware("auth")
    def auth(request):
        raise HTTPException(401)

    status, _, body = response_from(run_app(app, make_scope("GET", "/secret")))
    assert (status, body) == (401, b"Unauthorized")


def test_custom_404_and_405_handlers():
    app = App()

    @app.get("/users")
    def users(request):
        return Response.text("users")

    @app.exception_handler(404)
    def not_found(request, exc):
        return Response.json({"error": f"nothing at {request.path}"}, status=404)

    @app.exception_handler(405)
    def not_allowed(request, exc):
        return Response.json({"error": "wrong method"}, status=405, headers=exc.headers)

    status, _, body = response_from(run_app(app, make_scope("GET", "/nope")))
    assert (status, json.loads(body)) == (404, {"error": "nothing at /nope"})

    status, headers, body = response_from(run_app(app, make_scope("DELETE", "/users")))
    assert (status, json.loads(body)) == (405, {"error": "wrong method"})
    assert ("Allow", "GET") in headers


def test_exception_handler_by_class_catches_subclasses():
    app = App()

    class InvalidInput(ValueError):
        pass

    @app.get("/")
    def home(request):
        raise InvalidInput("bad")

    @app.exception_handler(ValueError)
    async def bad_value(request, exc):
        return Response.text(f"invalid: {exc}", status=400)

    status, _, body = response_from(run_app(app, make_scope("GET", "/")))
    assert (status, body) == (400, b"invalid: bad")


def test_unhandled_exception_is_a_logged_500(caplog):
    app = App()

    @app.get("/")
    def home(request):
        raise RuntimeError("boom")

    status, _, body = response_from(run_app(app, make_scope("GET", "/")))

    assert (status, body) == (500, b"Internal Server Error")
    assert "unhandled error in GET /" in caplog.text
    assert "RuntimeError: boom" in caplog.text


def test_failing_exception_handler_is_a_logged_500(caplog):
    app = App()

    @app.exception_handler(404)
    def broken(request, exc):
        raise KeyError("oops")

    status, _, _ = response_from(run_app(app, make_scope("GET", "/nope")))

    assert status == 500
    assert "exception handler failed" in caplog.text


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


def run_lifespan(app):
    """Sends the startup then shutdown events, as a server would."""
    events = iter([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])
    sent = []

    async def receive():
        return next(events)

    async def send(message):
        sent.append(message)

    asyncio.run(app({"type": "lifespan"}, receive, send))
    return sent


def test_lifespan_startup_and_shutdown_are_acknowledged():
    assert run_lifespan(App()) == [{"type": "lifespan.startup.complete"}, {"type": "lifespan.shutdown.complete"}]


def test_startup_and_shutdown_hooks_run_in_order():
    app = App()
    calls = []

    @app.on_startup
    def open_db():
        calls.append("open db")

    @app.on_startup
    async def open_cache():
        calls.append("open cache")

    @app.on_shutdown
    async def close_db():
        calls.append("close db")

    @app.on_shutdown
    def close_cache():
        calls.append("close cache")

    assert run_lifespan(app) == [{"type": "lifespan.startup.complete"}, {"type": "lifespan.shutdown.complete"}]
    assert calls == ["open db", "open cache", "close cache", "close db"]


def test_failing_startup_hook_stops_the_server_from_starting():
    app = App()
    calls = []

    @app.on_startup
    def broken():
        raise ConnectionError("database unreachable")

    @app.on_startup
    def never_runs():
        calls.append("second hook")

    @app.on_shutdown
    def not_after_a_failed_startup():
        calls.append("shutdown")

    assert run_lifespan(app) == [
        {"type": "lifespan.startup.failed", "message": "ConnectionError: database unreachable"}
    ]
    assert calls == []


def test_failing_shutdown_hook_is_reported():
    app = App()

    @app.on_shutdown
    def broken():
        raise RuntimeError("could not flush")

    assert run_lifespan(app)[-1] == {"type": "lifespan.shutdown.failed", "message": "RuntimeError: could not flush"}


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
