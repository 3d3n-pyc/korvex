"""Tests for parameters read from headers, cookies, forms and aliased query
fields: Header(), Cookie(), Form() and Query()."""

import json
from datetime import date
from typing import Annotated, Optional

import pytest
from test_app import make_scope, response_from, run_app

from korvex import App, Cookie, Form, Header, Query, Response, UploadedFile


def call(app, path="/", headers=(), query=b"", method="GET", body=b""):
    scope = make_scope(method, path, headers=list(headers), query_string=query)
    status, response_headers, raw = response_from(run_app(app, scope, body=body))
    is_json = ("Content-Type", "application/json") in response_headers
    return status, json.loads(raw) if is_json else raw.decode()


def echo(**values):
    return Response.json({k: v.isoformat() if isinstance(v, date) else v for k, v in values.items()})


def test_headers():
    app = App()

    @app.get("/")
    def read(request, x_client: str = Header(), x_retries: int = Header(0), accept: list[str] = Header([]), ua: Optional[str] = Header(None, alias="User-Agent")):
        return echo(client=x_client, retries=x_retries, accept=accept, ua=ua)

    status, body = call(app, headers=[("X-Client", "web"), ("Accept", "a"), ("Accept", "b"), ("User-Agent", "curl")])
    assert (status, body) == (200, {"client": "web", "retries": 0, "accept": ["a", "b"], "ua": "curl"})

    status, body = call(app, headers=[("x-retries", "many")])
    assert status == 422
    assert body["detail"] == [
        {"loc": ["header", "x-client"], "msg": "Field required", "type": "missing"},
        {"loc": ["header", "x-retries"], "msg": "Input should be a valid integer", "type": "type_error"},
    ]


def test_cookies():
    app = App()

    @app.get("/")
    def read(request, session: str = Cookie(), theme: str = Cookie("light"), visits: int = Cookie(0, alias="n")):
        return echo(session=session, theme=theme, visits=visits)

    assert call(app, headers=[("Cookie", "session=abc; n=3")]) == (200, {"session": "abc", "theme": "light", "visits": 3})
    status, body = call(app)
    assert status == 422 and body["detail"][0]["loc"] == ["cookie", "session"]


def test_aliased_query_parameters():
    app = App()

    @app.get("/")
    def read(request, page_size: int = Query(20, alias="page-size"), since: Optional[date] = Query(None)):
        return echo(page_size=page_size, since=since)

    assert call(app, query=b"page-size=5&since=2026-11-05") == (200, {"page_size": 5, "since": "2026-11-05"})
    assert call(app) == (200, {"page_size": 20, "since": None})
    status, body = call(app, query=b"page-size=x")
    assert status == 422 and body["detail"][0]["loc"] == ["query", "page-size"]


def test_annotated_works_like_a_default():
    app = App()
    Client = Annotated[str, Header(alias="X-Client")]

    @app.get("/")
    def read(request, client: Client, theme: Annotated[str, Cookie()] = "light"):
        return echo(client=client, theme=theme)

    assert call(app, headers=[("X-Client", "cli")]) == (200, {"client": "cli", "theme": "light"})


def test_urlencoded_forms():
    app = App()

    @app.post("/")
    def submit(request, name: str = Form(), age: int = Form(), note: str = Form(""), tags: list[str] = Form([]), score: Optional[float] = Form(None)):
        return echo(name=name, age=age, note=note, tags=tags, score=score)

    form = [("Content-Type", "application/x-www-form-urlencoded")]
    status, body = call(app, method="POST", headers=form, body=b"name=Ada&age=36&tags=a&tags=b&score=")
    assert (status, body) == (200, {"name": "Ada", "age": 36, "note": "", "tags": ["a", "b"], "score": None})

    status, body = call(app, method="POST", headers=form, body=b"name=&age=")  # empty text is text; an empty number is missing
    assert status == 422 and body["detail"] == [{"loc": ["form", "age"], "msg": "Field required", "type": "missing"}]

    status, _ = call(app, method="POST", headers=[("Content-Type", "application/json")], body=b"{}")
    assert status == 415


def test_files_in_multipart_forms():
    app = App()

    @app.post("/")
    def upload(request, title: str = Form(), file: UploadedFile = Form()):
        return echo(title=title, name=file.filename, size=len(file.data))

    boundary = "XyZ"
    parts = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="title"\r\n\r\nHoliday\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.txt"\r\nContent-Type: text/plain\r\n\r\nhello\r\n'
        f"--{boundary}--\r\n"
    ).encode()
    headers = [("Content-Type", f"multipart/form-data; boundary={boundary}")]
    assert call(app, method="POST", headers=headers, body=parts) == (200, {"title": "Holiday", "name": "a.txt", "size": 5})

    swapped = (  # a file where text is expected, and text where a file is
        f'--{boundary}\r\nContent-Disposition: form-data; name="title"; filename="t.txt"\r\n\r\nx\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"\r\n\r\ntext\r\n'
        f"--{boundary}--\r\n"
    ).encode()
    status, body = call(app, method="POST", headers=headers, body=swapped)
    assert status == 422
    assert {tuple(e["loc"]): e["msg"] for e in body["detail"]} == {
        ("form", "title"): "Input should be a valid string",
        ("form", "file"): "Input should be a valid file",
    }


def test_optional_form_fields_with_no_body_at_all():
    app = App()

    @app.post("/")
    def submit(request, name: str = Form("anonymous")):
        return echo(name=name)

    assert call(app, method="POST") == (200, {"name": "anonymous"})


def test_the_body_can_be_read_by_the_handler_too():
    app = App()

    @app.post("/")
    async def submit(request, name: str = Form()):
        return echo(name=name, raw=(await request.body()).decode(), form=(await request.form())["name"])

    status, body = call(app, method="POST", headers=[("Content-Type", "application/x-www-form-urlencoded")], body=b"name=Ada")
    assert body == {"name": "Ada", "raw": "name=Ada", "form": ["Ada"]}


def test_bad_declarations_are_refused_at_registration():
    app = App()
    with pytest.raises(TypeError, match="can't be"):

        @app.get("/a")
        def a(request, x: dict = Header()):
            pass

    with pytest.raises(TypeError, match="a cookie has one value"):

        @app.get("/b")
        def b(request, x: list[str] = Cookie()):
            pass

    with pytest.raises(TypeError, match="two defaults"):

        @app.get("/c")
        def c(request, x: Annotated[str, Header("a")] = "b"):
            pass

    with pytest.raises(TypeError, match="twice"):

        @app.get("/d")
        def d(request, x: Annotated[str, Header()] = Cookie()):
            pass

    with pytest.raises(TypeError, match="UploadedFile"):  # files only come from forms

        @app.get("/e")
        def e(request, x: UploadedFile = Header()):
            pass


def test_parameters_without_annotations_are_text():
    app = App()

    @app.get("/")
    def read(request, x_client=Header("none")):
        return echo(client=x_client)

    assert call(app, headers=[("X-Client", "42")]) == (200, {"client": "42"})
