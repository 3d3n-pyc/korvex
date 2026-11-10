"""Tests for korvex.middleware: CORS, request IDs, rate limiting."""

import asyncio
import logging
import re
import sys

import pytest
from support import make_scope, request, run_app

from korvex import App, HTTPException, Response
from korvex.middleware import MemoryStore, RequestIdFilter, cors, current_request_id, rate_limit, request_id

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
    """`(status, headers, body)` of one request; `X_Client="a"` sends `X-Client: a`."""
    response = request(app, method, path, headers=[(k.replace("_", "-"), v) for k, v in headers.items()])
    return response.status, dict(response.headers), response.body


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


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def limited_app(clock, **options):
    app = App()

    @app.get("/")
    def home(request):
        return Response.text("ok")

    @app.get("/health")
    def health(request):
        return Response.text("healthy")

    rate_limit(app, **{"requests": 3, "per": 60, "store": MemoryStore(clock), **options})
    return app


def test_requests_beyond_the_limit_get_a_429():
    clock = Clock()
    app = limited_app(clock)
    statuses = [call(app, "GET", "/")[0] for _ in range(4)]
    assert statuses == [200, 200, 200, 429]
    status, headers, body = call(app, "GET", "/")
    assert (status, body, headers["Retry-After"]) == (429, b"Too Many Requests", "20")
    clock.now = 20  # one request's worth of time later
    assert [call(app, "GET", "/")[0] for _ in range(2)] == [200, 429]


def test_rate_limit_headers_tell_clients_where_they_stand():
    app = limited_app(Clock())
    remaining = [call(app, "GET", "/")[1]["RateLimit-Remaining"] for _ in range(3)]
    _, headers, _ = call(app, "GET", "/")
    assert remaining == ["2", "1", "0"]
    assert headers["RateLimit-Limit"] == "3" and headers["RateLimit-Remaining"] == "0"
    assert "RateLimit-Limit" not in call(limited_app(Clock(), headers=False), "GET", "/")[1]


def test_the_key_decides_who_shares_a_limit():
    def by_api_key(request):
        if request.path == "/health":
            return None  # exempt
        return request.headers.get("x-api-key")

    app = limited_app(Clock(), key=by_api_key)
    assert [call(app, "GET", "/", X_API_Key="alice")[0] for _ in range(4)][-1] == 429
    assert call(app, "GET", "/", X_API_Key="bob")[0] == 200  # bob has his own allowance
    assert all(call(app, "GET", "/health")[0] == 200 for _ in range(10))
    assert "RateLimit-Limit" not in call(app, "GET", "/health")[1]


def test_preflight_requests_answered_by_cors_first_arent_counted():
    app = App()

    @app.get("/data")
    def data(request):
        return Response.text("ok")

    cors(app, allow_origins=[SITE])
    rate_limit(app, requests=1, per=60)
    for _ in range(5):
        assert preflight(app)[0] == 204
    assert call(app, "GET", "/data", Origin=SITE)[0] == 200


def test_memory_store_forgets_keys_whose_allowance_is_back():
    clock = Clock()
    store = MemoryStore(clock)
    for n in range(999):
        store.hit(n, 10, 1)
    clock.now = 5  # every allowance is back
    store.hit("new", 10, 1)  # the 1,000th call cleans up
    assert list(store._next_free) == ["new"]


def test_rate_limit_rejects_nonsense_settings():
    import pytest

    with pytest.raises(ValueError):
        rate_limit(App(), requests=0, per=60)


def test_requests_with_an_unknown_address_share_one_limit_instead_of_escaping_it():
    app = limited_app(Clock())  # the test requests carry no client address
    assert [call(app, "GET", "/")[0] for _ in range(4)][-1] == 429


# --- compress() ---


def compress_app(**options):
    from korvex.middleware import compress

    app = App()
    compress(app, **options)

    @app.get("/big")
    def big(request):
        return Response.text("hello " * 200)

    @app.get("/small")
    def small(request):
        return Response.text("hi")

    @app.get("/png")
    def png(request):
        return Response(b"\x89PNG" + b"x" * 2000, headers=[("Content-Type", "image/png")])

    @app.get("/stream")
    def stream(request):
        return Response.stream((f"line {n}\n" for n in range(100)), headers=[("Content-Type", "text/plain")])

    @app.get("/already")
    def already(request):
        return Response(b"x" * 2000, headers=[("Content-Type", "text/plain"), ("Content-Encoding", "gzip")])

    @app.get("/no-transform")
    def no_transform(request):
        return Response.text("hello " * 200, headers=[("Cache-Control", "no-transform")])

    return app


def get_with(app, path, accept=None):
    response = request(app, "GET", path, headers=[("Accept-Encoding", accept)] if accept is not None else [])
    return response.status, {k.lower(): v for k, v in response.headers}, response.body


def test_compress_picks_the_best_encoding_the_client_takes():
    import gzip
    import zlib

    app = compress_app()
    _, headers, body = get_with(app, "/big", "gzip, deflate")
    assert headers["content-encoding"] == "gzip" and headers["vary"] == "Accept-Encoding"
    assert gzip.decompress(body) == b"hello " * 200 and len(body) < 100
    if not getattr(sys, "_is_gil_enabled", lambda: True)():
        pytest.skip("brotli isn't in the defaults on free-threaded Python")
    brotli = pytest.importorskip("brotli")
    _, headers, body = get_with(app, "/big", "gzip;q=0.5, br")
    assert headers["content-encoding"] == "br" and brotli.decompress(body) == b"hello " * 200
    _, headers, _ = get_with(app, "/big", "br;q=0, gzip")  # refused explicitly
    assert headers["content-encoding"] == "gzip"
    _, headers, body = get_with(app, "/big", "*")
    assert headers["content-encoding"] in ("zstd", "br")
    assert zlib  # gzip itself is checked above


def test_compress_leaves_some_responses_alone():
    app = compress_app()
    assert "content-encoding" not in get_with(app, "/big")[1]  # no Accept-Encoding
    assert "content-encoding" not in get_with(app, "/big", "identity")[1]
    assert get_with(app, "/big", "gzip;q=0")[2] == b"hello " * 200
    assert "content-encoding" not in get_with(app, "/small", "gzip")[1]  # too small
    assert "content-encoding" not in get_with(app, "/png", "gzip")[1]  # compressed already
    assert get_with(app, "/already", "br")[1]["content-encoding"] == "gzip"  # left as the handler set it
    assert "content-encoding" not in get_with(app, "/no-transform", "gzip")[1]
    assert "vary" not in get_with(app, "/small", "gzip")[1]  # never compressed: doesn't depend on it
    assert get_with(app, "/big")[1]["vary"] == "Accept-Encoding"  # not compressed for this client, still depends on it


def test_compress_streams_piece_by_piece():
    import zlib

    app = compress_app()
    sent = run_app(app, make_scope("GET", "/stream", headers=[("Accept-Encoding", "gzip")]))
    pieces = [m["body"] for m in sent if m["type"] == "http.response.body" and m["body"]]
    assert len(pieces) > 50  # each line flushed on its own, not held back
    decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    first = decompressor.decompress(pieces[0])
    assert first == b"line 0\n"  # readable as soon as it arrives
    rest = b"".join(decompressor.decompress(p) for p in pieces[1:]) + decompressor.flush()
    assert first + rest == b"".join(f"line {n}\n".encode() for n in range(100))


def test_compress_zstd_on_python_3_14():
    zstd = pytest.importorskip("compression.zstd")
    app = compress_app(encodings=["zstd"])
    _, headers, body = get_with(app, "/big", "zstd")
    assert headers["content-encoding"] == "zstd" and zstd.decompress(body) == b"hello " * 200


def test_compress_leaves_brotli_out_on_free_threaded_python(monkeypatch):
    from korvex.middleware import _compression

    monkeypatch.setattr(_compression, "_gil_disabled", lambda: True)
    app = compress_app()
    assert get_with(app, "/big", "br, gzip")[1]["content-encoding"] == "gzip"  # br isn't offered


def test_compress_rejects_unknown_encodings():
    from korvex.middleware import compress

    with pytest.raises(ValueError, match="unknown encodings"):
        compress(App(), encodings=["deflate"])


# --- security_headers() ---


def test_security_headers_defaults_and_overrides():
    from korvex.middleware import security_headers

    app = App()
    security_headers(app, content_security_policy="default-src 'self'")

    @app.get("/")
    def home(request):
        return Response.text("home")

    @app.get("/embeddable")
    def embeddable(request):
        return Response.text("widget", headers=[("X-Frame-Options", "SAMEORIGIN")])

    def headers_of(path, scheme="http"):
        return {k.lower(): v for k, v in request(app, "GET", f"{scheme}://testserver{path}").headers}

    plain = headers_of("/")
    assert plain["x-frame-options"] == "DENY" and plain["x-content-type-options"] == "nosniff"
    assert plain["referrer-policy"] == "strict-origin-when-cross-origin"
    assert plain["cross-origin-opener-policy"] == "same-origin"
    assert plain["content-security-policy"] == "default-src 'self'"
    assert "strict-transport-security" not in plain  # not over HTTP: browsers ignore it there
    assert headers_of("/", "https")["strict-transport-security"] == "max-age=31536000"
    assert headers_of("/embeddable")["x-frame-options"] == "SAMEORIGIN"  # the response's own wins
    assert headers_of("/missing")["x-frame-options"] == "DENY"  # errors too


def test_security_headers_can_be_left_out():
    from korvex.middleware import security_headers

    app = App()
    security_headers(app, hsts=600, hsts_subdomains=True, hsts_preload=True, frame_options=None, referrer_policy=None)

    @app.get("/")
    def home(request):
        return Response.text("home")

    headers = request(app, "GET", "https://testserver/").headers
    assert headers["strict-transport-security"] == "max-age=600; includeSubDomains; preload"
    assert "x-frame-options" not in headers and "referrer-policy" not in headers
    with pytest.raises(ValueError):
        security_headers(App(), hsts=-1)


# --- RedisStore (needs a Redis server: REDIS_URL, or redis://localhost:6379) ---


def redis_url():
    import os
    import socket
    from urllib.parse import urlparse

    pytest.importorskip("redis")
    url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    where = urlparse(url)
    try:
        socket.create_connection((where.hostname, where.port or 6379), timeout=0.5).close()
    except OSError:
        pytest.skip(f"no Redis at {url}")
    return url


def run_requests(app, scopes):
    """Several requests on one event loop, as a server runs them (a Redis
    client's connections belong to the loop that opened them)."""
    statuses = []

    async def one(scope):
        sent = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        await app(scope, receive, send)
        return sent[0]["status"]

    async def all_of_them():
        for scope in scopes:
            statuses.append(await one(scope))

    asyncio.run(all_of_them())
    return statuses


def test_redis_store_limits_like_the_memory_store():
    import uuid

    from korvex.middleware import RedisStore, rate_limit

    app = App()
    rate_limit(app, requests=3, per=60, store=RedisStore(redis_url(), prefix=f"test:{uuid.uuid4().hex}:"))

    @app.get("/")
    def home(request):
        return Response.text("ok")

    statuses = run_requests(app, [make_scope("GET", "/")] * 5)
    assert statuses == [200, 200, 200, 429, 429]


def test_redis_store_is_shared_and_atomic_across_clients():
    import uuid

    url, prefix = redis_url(), f"test:{uuid.uuid4().hex}:"  # skips without redis or a server
    import redis.asyncio

    from korvex.middleware import RedisStore

    async def race():
        # Two "workers", each with its own client, 50 requests at once.
        stores = [RedisStore(redis.asyncio.Redis.from_url(url), prefix=prefix) for _ in range(2)]
        decisions = await asyncio.gather(*(stores[n % 2].hit("client", 20, 60) for n in range(50)))
        for store in stores:
            await store.client.aclose()
        return decisions

    decisions = asyncio.run(race())
    assert sum(d.allowed for d in decisions) == 20  # exactly the limit, not 20 per worker
    refused = [d for d in decisions if not d.allowed]
    assert all(0 < d.retry_after <= 3 for d in refused)  # one request every 3 s


def test_redis_store_fails_open_or_closed(caplog):
    pytest.importorskip("redis")
    import redis.asyncio

    from korvex.middleware import RedisStore

    nowhere = "redis://127.0.0.1:1/0"  # nothing listens there

    async def ask(fail_open):
        store = RedisStore(redis.asyncio.Redis.from_url(nowhere, socket_connect_timeout=0.2), fail_open=fail_open)
        return await store.hit("x", 5, 60)

    assert asyncio.run(ask(True)).allowed is True
    assert asyncio.run(ask(False)).allowed is False
    assert "rate limit store unreachable" in caplog.text


@pytest.mark.parametrize("now", [123456.789, 1e6 + 0.1, 0.1, 86400.3])
@pytest.mark.parametrize(("requests", "per"), [(2, 60), (3, 1), (3, 60), (7, 0.7)])
def test_memory_store_burst_at_one_instant(now, requests, per):
    store = MemoryStore(lambda: now)  # a frozen clock, like two hits in one Windows tick
    assert [store.hit("k", requests, per).allowed for _ in range(requests + 1)] == [True] * requests + [False]
