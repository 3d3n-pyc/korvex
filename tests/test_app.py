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
    body = b"".join(m["body"] for m in sent if m["type"] == "http.response.body")
    headers = [(name.decode("latin-1"), value.decode("latin-1")) for name, value in start["headers"]]
    return start["status"], headers, body


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
        run_app(app, {"type": "carrier-pigeon"})


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


def form_app():
    """An app whose POST /form echoes what request.form() returned."""
    app = App()

    @app.post("/form")
    async def form(request):
        fields = await request.form()
        echo = {
            name: [v if isinstance(v, str) else [v.filename, v.content_type, v.data.hex()] for v in values]
            for name, values in fields.items()
        }
        return Response.json(echo)

    return app


def test_request_cookies():
    app = App()

    @app.get("/")
    def home(request):
        return Response.json(request.cookies)

    scope = make_scope("GET", "/", headers=[("Cookie", 'session=abc; theme="dark"'), ("Cookie", "lang=fr")])
    _, _, body = response_from(run_app(app, scope))
    assert json.loads(body) == {"session": "abc", "theme": "dark", "lang": "fr"}


def test_urlencoded_form():
    scope = make_scope("POST", "/form", headers=[("Content-Type", "application/x-www-form-urlencoded")])
    _, _, body = response_from(run_app(form_app(), scope, body=b"tag=a&tag=b&name=Ada+L&empty="))
    assert json.loads(body) == {"tag": ["a", "b"], "name": ["Ada L"], "empty": [""]}


def test_multipart_form_with_a_binary_file():
    data = b"\x00\x01\r\n\r\nbinary\r\n"
    body = (
        b"--XyZ\r\n"
        b'Content-Disposition: form-data; name="title"\r\n\r\n'
        b"Holiday \xc3\xa9t\xc3\xa9\r\n"
        b"--XyZ\r\n"
        b'Content-Disposition: form-data; name="photo"; filename="beach.png"\r\n'
        b"Content-Type: image/png\r\n\r\n" + data + b"\r\n"
        b"--XyZ--\r\n"
    )
    scope = make_scope("POST", "/form", headers=[("Content-Type", "multipart/form-data; boundary=XyZ")])
    _, _, response = response_from(run_app(form_app(), scope, body=body))
    assert json.loads(response) == {"title": ["Holiday été"], "photo": [["beach.png", "image/png", data.hex()]]}


def test_form_rejects_other_content_types():
    scope = make_scope("POST", "/form", headers=[("Content-Type", "application/json")])
    status, _, _ = response_from(run_app(form_app(), scope, body=b"{}"))
    assert status == 415


def test_set_and_delete_cookie():
    response = Response.text("ok")
    response.set_cookie("session", "abc", max_age=3600, secure=True, httponly=True)
    response.delete_cookie("theme")

    assert response.headers.get_all("Set-Cookie") == [
        "session=abc; HttpOnly; Max-Age=3600; Path=/; SameSite=lax; Secure",
        'theme=""; Max-Age=0; Path=/; SameSite=lax',
    ]


def test_streaming_response_sends_chunks_as_they_come():
    app = App()

    async def numbers():
        for n in range(3):
            yield f"{n}\n"

    @app.get("/async")
    def stream_async(request):
        return Response.stream(numbers(), headers=[("Content-Type", "text/plain")])

    @app.get("/sync")
    def stream_sync(request):
        return Response.stream([b"a", "b", b"c"])

    sent = run_app(app, make_scope("GET", "/async"))
    bodies = [m for m in sent if m["type"] == "http.response.body"]
    assert [m["body"] for m in bodies] == [b"0\n", b"1\n", b"2\n", b""]
    assert [m.get("more_body", False) for m in bodies] == [True, True, True, False]

    assert response_from(run_app(app, make_scope("GET", "/sync")))[2] == b"abc"


def test_file_response(tmp_path):
    data = bytes(range(256)) * 600  # 150 KB: several chunks
    (tmp_path / "report.pdf").write_bytes(data)
    app = App()

    @app.get("/download")
    def download(request):
        return Response.file(tmp_path / "report.pdf", filename="rapport été.pdf")

    @app.get("/view")
    def view(request):
        return Response.file(tmp_path / "report.pdf")

    status, headers, body = response_from(run_app(app, make_scope("GET", "/download")))
    assert (status, body) == (200, data)
    assert ("Content-Type", "application/pdf") in headers
    assert ("Content-Length", str(len(data))) in headers
    assert ("Content-Disposition", "attachment; filename*=UTF-8''rapport%20%C3%A9t%C3%A9.pdf") in headers

    _, headers, _ = response_from(run_app(app, make_scope("GET", "/view")))
    assert not any(name == "Content-Disposition" for name, _ in headers)


def test_file_response_for_a_missing_file_raises_right_away(tmp_path):
    with pytest.raises(FileNotFoundError):
        Response.file(tmp_path / "missing.txt")


def test_static_files(tmp_path):
    public = tmp_path / "public"
    (public / "css").mkdir(parents=True)
    (public / "css" / "app.css").write_text("body {}")
    (tmp_path / "secret.txt").write_text("password")
    (public / "link").symlink_to(tmp_path / "secret.txt")

    app = App()
    app.static("/static", public)

    status, headers, body = response_from(run_app(app, make_scope("GET", "/static/css/app.css")))
    assert (status, body) == (200, b"body {}")
    assert ("Content-Type", "text/css") in headers

    for path in [
        "/static/missing.css",
        "/static/css",  # a directory
        "/static/../secret.txt",
        "/static/css/../../secret.txt",
        "/static//etc/passwd",
        "/static/link",  # a symlink out of the directory
        "/static/a\x00b",
    ]:
        status, _, _ = response_from(run_app(app, make_scope("GET", path)))
        assert status == 404, path


def test_static_needs_an_existing_directory(tmp_path):
    with pytest.raises(NotADirectoryError):
        App().static("/static", tmp_path / "nope")


def test_two_static_directories_dont_overwrite_each_other(tmp_path):
    for name in ["a", "b"]:
        (tmp_path / name).mkdir()
        (tmp_path / name / "file.txt").write_text(name)
    app = App()
    app.static("/a", tmp_path / "a")
    app.static("/b", tmp_path / "b")

    assert response_from(run_app(app, make_scope("GET", "/a/file.txt")))[2] == b"a"
    assert response_from(run_app(app, make_scope("GET", "/b/file.txt")))[2] == b"b"


def test_head_uses_the_get_route_without_the_body(tmp_path):
    (tmp_path / "f.txt").write_text("hello")
    app = App()
    app.static("/static", tmp_path)

    @app.get("/page")
    def page(request):
        return Response.text("a page")

    @app.post("/only-post")
    def only_post(request):
        return Response.text("posted")

    sent = run_app(app, make_scope("HEAD", "/page"))
    status, headers, body = response_from(sent)
    assert (status, body) == (200, b"")
    assert ("Content-Length", "6") in headers

    status, headers, body = response_from(run_app(app, make_scope("HEAD", "/static/f.txt")))
    assert (status, body) == (200, b"")
    assert ("Content-Length", "5") in headers and ("Content-Type", "text/plain") in headers

    status, headers, _ = response_from(run_app(app, make_scope("HEAD", "/only-post")))
    assert status == 405 and ("Allow", "POST") in headers


def test_an_explicit_head_route_wins():
    app = App()

    @app.get("/x")
    def get_x(request):
        return Response.text("get")

    @app.head("/x")
    def head_x(request):
        return Response.text("", headers=[("X-From", "head")])

    _, headers, _ = response_from(run_app(app, make_scope("HEAD", "/x")))
    assert ("X-From", "head") in headers


def test_hooks_work_with_and_without_parentheses():
    app = App()
    calls = []

    @app.on_startup
    def bare():
        calls.append("startup bare")

    @app.on_startup()
    async def called():
        calls.append("startup called")

    @app.on_shutdown()
    def shutdown_called():
        calls.append("shutdown called")

    @app.on_shutdown
    def shutdown_bare():
        calls.append("shutdown bare")

    assert bare.__name__ == "bare" and called.__name__ == "called"  # decorators return the function
    run_lifespan(app)
    assert calls == ["startup bare", "startup called", "shutdown bare", "shutdown called"]


def test_malformed_bodies_are_client_errors(caplog):
    app = form_app()

    @app.post("/json")
    async def read_json(request):
        return Response.json(await request.json())

    def status_for(path, content_type, body):
        scope = make_scope("POST", path, headers=[("Content-Type", content_type)])
        return response_from(run_app(app, scope, body=body))[0]

    bad_charset_part = (
        b'--B\r\nContent-Disposition: form-data; name="f"\r\n'
        b"Content-Type: text/plain; charset=not-a-charset\r\n\r\nx\r\n--B--\r\n"
    )
    bad_bytes_part = b'--B\r\nContent-Disposition: form-data; name="f"\r\n\r\n\xff\xfe\r\n--B--\r\n'

    assert status_for("/json", "application/json", b"{not json") == 400
    assert status_for("/json", "application/json", b"\xff\xfe\xfa") == 400
    assert status_for("/form", "application/x-www-form-urlencoded", b"a=\xff") == 400
    assert status_for("/form", "multipart/form-data; boundary=B", bad_charset_part) == 400
    assert status_for("/form", "multipart/form-data; boundary=B", bad_bytes_part) == 400
    assert "unhandled error" not in caplog.text  # client mistakes don't fill the logs


def test_request_client_and_scheme_come_from_the_asgi_server():
    app = App()

    @app.get("/")
    def home(request):
        return Response.json({"client": request.client, "scheme": request.scheme})

    scope = make_scope("GET", "/")
    _, _, body = response_from(run_app(app, scope))
    assert json.loads(body) == {"client": None, "scheme": "http"}

    scope.update(client=["203.0.113.9", 4242], scheme="https")  # as uvicorn passes them
    _, _, body = response_from(run_app(app, scope))
    assert json.loads(body) == {"client": ["203.0.113.9", 4242], "scheme": "https"}


def test_before_request_can_answer_right_away_even_for_unknown_paths():
    app = App()
    calls = []

    @app.get("/users")
    def users(request):
        calls.append("handler")
        return Response.text("users")

    @app.before_request
    def first(request):
        calls.append(f"first {request.path}")

    @app.before_request()
    async def maintenance(request):
        calls.append("maintenance")
        if request.headers.get("X-Maintenance"):
            return Response.text("down for maintenance", status=503)

    assert response_from(run_app(app, make_scope("GET", "/users")))[0] == 200
    assert calls == ["first /users", "maintenance", "handler"]

    calls.clear()
    scope = make_scope("GET", "/no-such-path", headers=[("X-Maintenance", "1")])
    assert response_from(run_app(app, scope))[:3:2] == (503, b"down for maintenance")
    assert calls == ["first /no-such-path", "maintenance"]  # ran before routing, handler skipped


def test_after_request_sees_every_response_in_reverse_order():
    app = App()

    @app.get("/ok")
    def ok(request):
        return Response.text("ok")

    @app.get("/boom")
    def boom(request):
        raise RuntimeError("boom")

    @app.after_request
    def outer(request, response):
        response.headers.add("X-Order", "outer")  # declared first: runs last

    @app.after_request()
    async def inner(request, response):
        response.headers.add("X-Order", "inner")
        return response

    for path, status in [("/ok", 200), ("/missing", 404), ("/boom", 500)]:
        got_status, headers, _ = response_from(run_app(app, make_scope("GET", path)))
        assert got_status == status
        assert [v for k, v in headers if k == "X-Order"] == ["inner", "outer"], path


def test_after_request_can_replace_the_response():
    app = App()

    @app.get("/")
    def home(request):
        return Response.text("original")

    @app.after_request
    def replace(request, response):
        return Response.text(f"replaced {response.status}")

    assert response_from(run_app(app, make_scope("GET", "/")))[2] == b"replaced 200"


def test_failing_hooks_give_a_logged_500(caplog):
    app = App()

    @app.get("/")
    def home(request):
        return Response.text("ok")

    @app.before_request
    def broken_before(request):
        if request.headers.get("X-Break") == "before":
            raise RuntimeError("before broke")

    @app.after_request
    def broken_after(request, response):
        if request.headers.get("X-Break") == "after":
            raise RuntimeError("after broke")

    assert response_from(run_app(app, make_scope("GET", "/", headers=[("X-Break", "before")])))[0] == 500
    assert response_from(run_app(app, make_scope("GET", "/", headers=[("X-Break", "after")])))[0] == 500
    assert "before broke" in caplog.text and "after_request hook failed" in caplog.text


def test_path_params_are_filled_after_routing():
    app = App()
    seen = []

    @app.get("/users/{id:int}")
    def user(request, id):
        return Response.text("user")

    @app.before_request
    def before(request):
        seen.append(dict(request.path_params))

    @app.after_request
    def after(request, response):
        seen.append(dict(request.path_params))

    run_app(app, make_scope("GET", "/users/7"))
    assert seen == [{}, {"id": 7}]



def test_app_groups_register_under_a_prefix_with_their_dependencies():
    from korvex import Depends

    app = App()
    calls = []
    api = app.group("/api", dependencies=[Depends(lambda: calls.append("auth"))])
    admin = api.group("/admin", dependencies=[Depends(lambda: calls.append("admin"))])

    @api.get("/users/{id:int}")
    def get_user(request, id):
        return Response.json({"id": id})

    @admin.post("/stats", dependencies=[Depends(lambda: calls.append("audit"))])
    def stats(request):
        return Response.text("stats")

    status, _, body = response_from(run_app(app, make_scope("GET", "/api/users/7")))
    assert (status, json.loads(body), calls) == (200, {"id": 7}, ["auth"])
    calls.clear()
    status, _, body = response_from(run_app(app, make_scope("POST", "/api/admin/stats")))
    assert (status, body, calls) == (200, b"stats", ["auth", "admin", "audit"])  # outer group first
    assert repr(admin).startswith("AppGroup(prefix='/api/admin', dependencies=[")


def test_routes_on_the_router_itself_work_with_app_handler():
    app = App()
    app.router.group("/api").get("/users", "list_users")

    @app.handler("list_users")
    def list_users(request):
        return Response.text("users")

    assert response_from(run_app(app, make_scope("GET", "/api/users")))[::2] == (200, b"users")


def test_router_middleware_names_are_refused_not_ignored(caplog):
    # The App runs dependencies, not middleware: a route given a middleware
    # name through app.router would otherwise lose what may be its guard.
    app = App()
    app.router.get("/admin", "admin", middleware=["auth"])

    @app.handler("admin")
    def admin(request):
        return Response.text("secret")

    status, _, body = response_from(run_app(app, make_scope("GET", "/admin")))
    assert (status, body) == (500, b"Internal Server Error")
    assert "use dependencies=" in caplog.text


def test_abort_sends_its_response_from_a_dependency_handler_or_hook():
    from typing import Optional

    from korvex import Abort, Cookie, Depends

    app = App()

    def signed_in(session: Optional[str] = Cookie(None)) -> None:
        if session is None:
            raise Abort(Response.redirect("/login"))

    @app.get("/account", dependencies=[Depends(signed_in)])
    def account(request):
        return Response.text("account")

    @app.get("/teapot")
    def teapot(request):
        raise Abort(Response.text("short and stout", status=418))

    @app.before_request
    def closed(request):
        if request.path == "/closed":
            raise Abort(Response.text("closed for the day", status=503))

    @app.after_request
    def stamp(request, response):
        response.headers.set("X-Stamp", "yes")

    @app.exception_handler(Exception)
    def everything(request, exc):  # an Abort isn't an error for exception handlers
        return Response.text("handled", status=500)

    def get(path, headers=()):
        status, headers, body = response_from(run_app(app, make_scope("GET", path, headers=list(headers))))
        return status, {k.lower(): v for k, v in headers}, body

    status, headers, _ = get("/account")
    assert (status, headers["location"], headers["x-stamp"]) == (302, "/login", "yes")  # after hooks still apply
    assert get("/account", [("Cookie", "session=x")])[2] == b"account"
    assert get("/teapot")[::2] == (418, b"short and stout")
    assert get("/closed")[::2] == (503, b"closed for the day")


def test_abort_in_a_websocket_dependency_refuses_the_connection():
    from test_websocket import run_ws, ws_scope

    from korvex import Abort, Depends

    app = App()

    def refuse():
        raise Abort(Response.redirect("/login"))

    @app.websocket("/ws", dependencies=[Depends(refuse)])
    async def ws(socket):
        await socket.accept()

    assert run_ws(app, ws_scope("/ws")) == [{"type": "websocket.close", "code": 1000, "reason": ""}]


def test_custom_route_constraints_and_annotations():
    from datetime import date

    app = App()
    app.constraint("isodate", r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

    @app.get("/days/{day:isodate}")
    def day(request, day: date):  # the constraint picks the route, the annotation converts
        return Response.text(day.strftime("%A"))

    assert response_from(run_app(app, make_scope("GET", "/days/2026-11-05")))[::2] == (200, b"Thursday")
    assert response_from(run_app(app, make_scope("GET", "/days/tomorrow")))[0] == 404  # doesn't fit
    assert response_from(run_app(app, make_scope("GET", "/days/2026-13-45")))[0] == 422  # fits, isn't a date


def host_app():
    from korvex import Depends

    app = App()
    calls = []
    api = app.host("api.example.com", dependencies=[Depends(lambda: calls.append("api"))])
    tenants = app.host("{tenant}.example.com")

    @api.get("/users")
    def api_users(request):
        return Response.text("api users")

    @tenants.get("/")
    def tenant_home(request, tenant: str):
        return Response.text(f"home of {tenant}")

    @tenants.group("/admin").post("/purge")
    def purge(request, tenant):
        return Response.text(f"purged {tenant}")

    @app.get("/health")
    def health(request):
        return Response.text("ok")

    @app.get("/")
    def main_home(request):
        return Response.text("main home")

    return app, calls


def on(app, host, path="/", method="GET"):
    status, _, body = response_from(run_app(app, make_scope(method, path, headers=[("Host", host)])))
    return status, body


def test_host_routes_answer_for_their_host_only():
    app, calls = host_app()
    assert on(app, "api.example.com", "/users") == (200, b"api users")
    assert calls == ["api"]  # the host group's dependencies ran
    assert on(app, "API.Example.com:8443", "/users") == (200, b"api users")  # case and port don't matter
    assert on(app, "example.com", "/users")[0] == 404  # not on another host
    assert on(app, "acme.example.com") == (200, b"home of acme")
    assert on(app, "acme.example.com", "/admin/purge", "POST") == (200, b"purged acme")
    assert on(app, "acme.example.com", "/admin/purge")[0] == 405  # the host has the path, not for GET
    assert on(app, "a.b.example.com")[1] == b"main home"  # {tenant} takes one label: no match there
    assert on(app, ".example.com")[1] == b"main home"  # an empty label isn't a tenant
    assert on(app, "api.example.com", "/admin/purge", "POST")[0] == 404  # api isn't a tenant: its host owns it


def test_routes_without_a_host_answer_on_every_host():
    app, _ = host_app()
    assert on(app, "api.example.com", "/health") == (200, b"ok")
    assert on(app, "acme.example.com", "/health") == (200, b"ok")
    assert on(app, "example.com") == (200, b"main home")
    assert on(app, "acme.example.com") == (200, b"home of acme")  # the host's own route comes first


def test_literal_hosts_come_before_patterns():
    app = App()
    any_tenant = app.host("{tenant}.example.com")
    www = app.host("www.example.com")  # declared after, tried first

    @any_tenant.get("/")
    def tenant(request, tenant):
        return Response.text(tenant)

    @www.get("/")
    def site(request):
        return Response.text("site")

    assert on(app, "www.example.com") == (200, b"site")
    assert on(app, "acme.example.com") == (200, b"acme")
    assert app.host("WWW.example.com.") is not None  # the same host, normalized: no duplicate
    assert len(app._hosts) == 2


def test_bad_hosts_and_clashing_names_are_refused():
    app = App()
    with pytest.raises(ValueError, match="not a host pattern"):
        app.host("api.example.com/x")
    tenants = app.host("{tenant}.example.com")
    with pytest.raises(ValueError, match="both in the host"):

        @tenants.get("/{tenant}")
        def clash(request, tenant):
            pass


def test_websockets_per_host():
    from test_websocket import run_ws, ws_scope

    app = App()
    chat = app.host("{room}.chat.example.com")

    @chat.websocket("/ws")
    async def room(ws, room):
        await ws.accept()
        await ws.send_text(room)

    sent = run_ws(app, ws_scope("/ws", headers=[("Host", "lobby.chat.example.com")]))
    assert sent[1] == {"type": "websocket.send", "text": "lobby"}
    assert run_ws(app, ws_scope("/ws", headers=[("Host", "example.com")]))[0]["type"] == "websocket.close"


def test_each_host_gets_its_own_api_document():
    app, _ = host_app()
    assert sorted(app.openapi()["paths"]) == ["/", "/health"]
    assert sorted(app.openapi(host="api.example.com")["paths"]) == ["/", "/health", "/users"]  # not the tenants' routes
    acme = app.openapi(host="acme.example.com")["paths"]
    assert sorted(acme) == ["/", "/admin/purge", "/health"] and acme["/"]["get"]["operationId"] == "tenant_home"
    app.docs()
    status, body = on(app, "api.example.com", "/openapi.json")
    assert status == 200 and "/users" in json.loads(body)["paths"]


def test_dependencies_read_host_parameters():
    from korvex import Depends, HTTPException

    TENANTS = {"acme": "Acme Corp"}

    def current_tenant(tenant: str) -> str:  # {tenant} from the host, like a path parameter
        if tenant not in TENANTS:
            raise HTTPException(404, "no such tenant")
        return TENANTS[tenant]

    app = App()
    tenants = app.host("{tenant}.example.com")

    @tenants.get("/")
    def home(request, name: str = Depends(current_tenant)):
        return Response.text(name)

    assert on(app, "acme.example.com") == (200, b"Acme Corp")
    assert on(app, "nobody.example.com") == (404, b"no such tenant")


def test_app_local_is_kept_per_event_loop():
    import threading

    app = App()
    seen = {}

    def on_its_own_loop(name):
        async def run():
            app.local.pool = f"pool of {name}"
            await asyncio.sleep(0.01)  # the other loop sets its own meanwhile
            seen[name] = app.local.pool

        asyncio.run(run())

    threads = [threading.Thread(target=on_its_own_loop, args=(name,)) for name in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert seen == {"a": "pool of a", "b": "pool of b"}

    async def fresh_loop():
        with pytest.raises(AttributeError, match="startup hook"):
            app.local.pool

    asyncio.run(fresh_loop())


def test_app_local_from_startup_hooks_to_handlers():
    app = App()

    @app.on_startup
    async def connect():
        app.local.greeting = "hello"

    @app.get("/")
    def home(request):
        return Response.text(app.local.greeting)

    async def serve():
        messages = iter([{"type": "lifespan.startup"}])
        sent = []

        async def receive():
            return next(messages, None) or await asyncio.sleep(3600)

        async def send(message):
            sent.append(message)

        lifespan = asyncio.ensure_future(app({"type": "lifespan"}, receive, send))
        await asyncio.sleep(0.01)
        out = []

        async def http_receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def http_send(message):
            out.append(message)

        await app(make_scope("GET", "/"), http_receive, http_send)
        lifespan.cancel()
        return out

    sent = asyncio.run(serve())
    assert response_from(sent)[2] == b"hello"
