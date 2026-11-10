"""Property tests: hostile input to the request parsers answers 2xx or 4xx.

The oracle is the raw ASGI path: no exception, never a 500, and fast.
`HYPOTHESIS_PROFILE=fuzz` runs 5000 examples per property.
"""

import os
import time

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from support import make_scope, response_from, run_app

from korvex import App, Query, Response

settings.register_profile("default", max_examples=50, deadline=None, suppress_health_check=list(HealthCheck))
settings.register_profile("fuzz", max_examples=5000, deadline=None, suppress_health_check=list(HealthCheck))
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))


def build_app() -> App:
    """An app that reads cookies, query, a typed param and the form."""
    app = App()

    @app.post("/any")
    async def any_route(request):
        _ = request.cookies
        dict(request.query)
        await request.form()
        return Response.text("ok")

    @app.get("/typed")
    def typed(request, n: int = Query(0)):
        return Response.text(str(n))

    return app


APP = build_app()


def call(method, path, headers=(), query=b"", body=b""):
    """Runs one raw ASGI request; returns the status and the seconds it took."""
    scope = make_scope(method, path, query_string=query)
    scope["headers"] = [(k, v) for k, v in headers]
    start = time.perf_counter()
    status, _, _ = response_from(run_app(APP, scope, body=body))
    return status, time.perf_counter() - start


def ok(status):
    """True for a 2xx or 4xx answer."""
    return 200 <= status < 300 or 400 <= status < 500


@given(st.binary(max_size=300))
def test_cookie_header_never_fails(raw):
    status, _ = call("POST", "/any", [(b"cookie", raw), (b"content-type", b"application/x-www-form-urlencoded")])
    assert ok(status)


@given(st.binary(max_size=300))
def test_query_string_never_fails(raw):
    assert ok(call("GET", "/typed", query=raw)[0])
    assert ok(call("POST", "/any", query=raw, headers=[(b"content-type", b"application/x-www-form-urlencoded")])[0])


CHARSETS = st.one_of(st.sampled_from(["utf-8", "latin-1", "ascii", "utf-16", "nope", "", '"x"']), st.text(max_size=10))


@given(st.binary(max_size=300), CHARSETS)
def test_urlencoded_form_never_fails(body, charset):
    ctype = f"application/x-www-form-urlencoded; charset={charset}".encode("utf-8", "replace")
    assert ok(call("POST", "/any", [(b"content-type", ctype)], body=body)[0])


def part(draw_name, filename, extra, data, crlf):
    """One multipart part, with optional breakage."""
    head = b"Content-Disposition: form-data; name=" + draw_name
    if filename is not None:
        head += b'; filename="' + filename + b'"'
    return head + extra + (b"\r\n\r\n" if crlf else b"\n\n") + data


@st.composite
def multipart(draw):
    """A (boundary, content type, body) with random, maybe broken, structure."""
    boundary = draw(
        st.text(alphabet=st.characters(codec="latin-1", exclude_characters="\r\n"), min_size=1, max_size=70).filter(
            lambda b: b == b.strip()
        )
    )
    quote = draw(st.sampled_from(['"', "", "'"]))
    ctype = f"multipart/form-data; boundary={quote}{boundary}{quote}".encode("latin-1")
    pieces = []
    for _ in range(draw(st.integers(0, 8))):
        name = draw(st.sampled_from([b'"a"', b"a", b'"a"b"', b'""', b'"\xff"']))
        filename = draw(
            st.none()
            | st.text(max_size=20).map(lambda s: s.encode())
            | st.sampled_from([b"../x", b"a\\b", b'q"q', b""])
        )
        extra = draw(
            st.sampled_from(
                [b"", b"\r\nContent-Type: text/plain; charset=nope", b"\r\nContent-Type: a/b;charset=utf-16"]
            )
        )
        pieces.append(part(name, filename, extra, draw(st.binary(max_size=40)), True))
    sep = b"\r\n--" + boundary.encode("latin-1") + b"\r\n"
    body = sep + sep.join(pieces)
    if draw(st.booleans()):
        body += b"\r\n--" + boundary.encode("latin-1") + b"--\r\n"
    cut = draw(st.integers(0, len(body)))
    return ctype, (body[:cut] if draw(st.booleans()) else body)


@given(multipart())
def test_multipart_never_fails(case):
    ctype, body = case
    assert ok(call("POST", "/any", [(b"content-type", ctype)], body=body)[0])


@given(st.binary(max_size=300))
def test_multipart_garbage_body_never_fails(body):
    ctype = b"multipart/form-data; boundary=xyz"
    assert ok(call("POST", "/any", [(b"content-type", ctype)], body=body)[0])


def test_multipart_many_parts_is_fast():
    body = (
        b"".join(b'--b\r\nContent-Disposition: form-data; name="f%d"\r\n\r\nv\r\n' % i for i in range(2000)) + b"--b--"
    )
    status, took = call("POST", "/any", [(b"content-type", b"multipart/form-data; boundary=b")], body=body)
    assert ok(status) and took < 1


def test_multipart_long_boundary_is_fast():
    boundary = "a" * 70
    chunk = b"--" + boundary.encode() + b'\r\nContent-Disposition: form-data; name="x"\r\n\r\n' + b"a" * 60 + b"\r\n"
    ctype = f"multipart/form-data; boundary={boundary}".encode()
    status, took = call("POST", "/any", [(b"content-type", ctype)], body=chunk * 2000)
    assert ok(status) and took < 1


@given(st.lists(st.tuples(st.binary(max_size=30), st.binary(max_size=60)), max_size=8))
def test_arbitrary_headers_never_fail(headers):
    status, _ = call("POST", "/any", headers)
    assert ok(status)


def test_multipart_part_header_without_a_name():
    body = b'--b\r\nContent-Disposition: form-data; name="a"\r\nbad header\r\n\r\nv\r\n--b--'
    status, _ = call("POST", "/any", [(b"content-type", b"multipart/form-data; boundary=b")], body=body)
    assert ok(status)


def test_multipart_part_with_bare_lf():
    body = b'--b\r\nContent-Disposition: form-data; name="a"\n\nv\r\n--b--'
    status, _ = call("POST", "/any", [(b"content-type", b"multipart/form-data; boundary=b")], body=body)
    assert ok(status)
