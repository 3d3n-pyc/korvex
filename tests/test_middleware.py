"""Tests for korvex.middleware: CORS, request IDs, rate limiting."""

import asyncio
import logging
import re

from test_app import make_scope, response_from, run_app

from korvex import App, HTTPException, Response
from korvex.middleware import RequestIdFilter, cors, current_request_id, request_id

SITE = "https://site.example"


def cors_app(**options):
    app = App()

    @app.get("/data")
    def data(request):
        return Response.json({"ok": True}, headers=[("X-Total", "3")])

    @app.get("/fail")
    def fail(request):
        raise HTTPException(403)

    cors(app, **{"allow_origins": [SITE], **options})
    return app


def call(app, method, path, **headers):
    scope = make_scope(method, path, headers=[(k.replace("_", "-"), v) for k, v in headers.items()])
    status, response_headers, body = response_from(run_app(app, scope))
    return status, dict(response_headers), body


def preflight(app, origin=SITE, method="POST", request_headers=None):
    headers = {"Origin": origin, "Access_Control_Request_Method": method}
    if request_headers:
        headers["Access_Control_Request_Headers"] = request_headers
    return call(app, "OPTIONS", "/data", **headers)


def test_preflight_from_an_allowed_origin():
    status, headers, _ = preflight(cors_app(allow_headers=["Content-Type"], max_age=60), request_headers="content-type")
    assert status == 204
    assert headers["Access-Control-Allow-Origin"] == SITE
    assert headers["Vary"] == "Origin"
    assert "POST" in headers["Access-Control-Allow-Methods"]
    assert headers["Access-Control-Allow-Headers"] == "content-type"
    assert headers["Access-Control-Max-Age"] == "60"


def test_preflight_is_refused_for_what_isnt_allowed():
    app = cors_app(allow_methods=["GET"], allow_headers=["Content-Type"])
    assert preflight(app, origin="https://evil.example")[:3:2] == (400, b"Disallowed CORS origin")
    assert preflight(app, method="DELETE")[:3:2] == (400, b"Disallowed CORS method")
    assert preflight(app, method="GET", request_headers="X-Secret")[:3:2] == (400, b"Disallowed CORS headers")


def test_any_header_can_be_allowed():
    status, headers, _ = preflight(cors_app(allow_headers="*"), request_headers="X-Anything, X-Else")
    assert status == 204 and headers["Access-Control-Allow-Headers"] == "x-anything, x-else"


def test_actual_requests_get_the_headers_errors_included():
    app = cors_app(expose_headers=["X-Total"])
    status, headers, _ = call(app, "GET", "/data", Origin=SITE)
    assert status == 200
    assert headers["Access-Control-Allow-Origin"] == SITE
    assert headers["Access-Control-Expose-Headers"] == "X-Total"
    status, headers, _ = call(app, "GET", "/fail", Origin=SITE)
    assert status == 403 and headers["Access-Control-Allow-Origin"] == SITE


def test_other_origins_and_same_origin_requests_get_no_cors_headers():
    app = cors_app()
    assert "Access-Control-Allow-Origin" not in call(app, "GET", "/data", Origin="https://evil.example")[1]
    assert "Access-Control-Allow-Origin" not in call(app, "GET", "/data")[1]


def test_any_origin_with_and_without_credentials():
    _, headers, _ = call(cors_app(allow_origins="*"), "GET", "/data", Origin=SITE)
    assert headers["Access-Control-Allow-Origin"] == "*" and "Vary" not in headers
    _, headers, _ = call(cors_app(allow_origins="*", allow_credentials=True), "GET", "/data", Origin=SITE)
    assert headers["Access-Control-Allow-Origin"] == SITE  # browsers refuse * with credentials
    assert headers["Access-Control-Allow-Credentials"] == "true"


def test_plain_options_requests_still_reach_the_routes():
    app = cors_app()

    @app.options("/data")
    def options(request):
        return Response.text("custom options")

    assert call(app, "OPTIONS", "/data", Origin=SITE)[2] == b"custom options"  # no Access-Control-Request-Method


def id_app(**options):
    app = App()

    @app.get("/")
    async def home(request):
        return Response.text(current_request_id() or "none")

    request_id(app, **options)
    return app


def test_request_id_is_generated_and_sent_back():
    status, headers, body = call(id_app(), "GET", "/")
    assert status == 200
    assert re.fullmatch(r"[0-9a-f]{32}", headers["X-Request-ID"])
    assert body.decode() == headers["X-Request-ID"]  # what the handler saw


def test_incoming_request_id_is_reused_when_safe():
    assert call(id_app(), "GET", "/", X_Request_ID="abc-123")[1]["X-Request-ID"] == "abc-123"
    for unsafe in ["evil\\nINJECTED", "x" * 201, "a b"]:
        assert call(id_app(), "GET", "/", X_Request_ID=unsafe)[1]["X-Request-ID"] != unsafe
    assert call(id_app(trust_incoming=False), "GET", "/", X_Request_ID="abc-123")[1]["X-Request-ID"] != "abc-123"


def test_request_ids_dont_leak_between_concurrent_requests():
    app = App()

    @app.get("/")
    async def slow(request):
        before = current_request_id()
        await asyncio.sleep(0.01)  # the other request runs meanwhile
        return Response.text(f"{before} {current_request_id()}")

    request_id(app)

    async def one(rid):
        sent = []

        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(message):
            sent.append(message)

        await app(make_scope("GET", "/", headers=[("X-Request-ID", rid)]), receive, send)
        return sent[1]["body"].decode()

    async def both():
        return await asyncio.gather(one("first"), one("second"))

    assert asyncio.run(both()) == ["first first", "second second"]


def test_request_id_filter_adds_the_id_to_log_records(caplog):
    app = App()
    log = logging.getLogger("myapp")

    @app.get("/")
    def home(request):
        log.warning("inside")
        return Response.text("ok")

    request_id(app)
    caplog.handler.addFilter(RequestIdFilter())
    _, headers, _ = call(app, "GET", "/", X_Request_ID="trace-42")
    log.warning("outside")
    assert [(r.getMessage(), r.request_id) for r in caplog.records] == [("inside", "trace-42"), ("outside", "-")]
