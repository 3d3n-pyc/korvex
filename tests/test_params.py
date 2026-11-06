"""Tests for parameters read from headers, cookies, forms and aliased query
fields: Header(), Cookie(), Form() and Query()."""

from datetime import date
from typing import Annotated, Optional

import pytest
from support import request

from korvex import App, Cookie, Form, Header, Query, Response, UploadedFile


def call(app, path="/", headers=(), query=b"", method="GET", body=b""):
    """`(status, JSON or text)` of one request."""
    response = request(app, method, f"{path}?{query.decode()}" if query else path, headers=list(headers), body=body)
    is_json = response.headers.get("content-type") == "application/json"
    return response.status, response.json() if is_json else response.text


def echo(**values):
    return Response.json({k: v.isoformat() if isinstance(v, date) else v for k, v in values.items()})


def test_headers():
    app = App()

    @app.get("/")
    def read(
        request,
        x_client: str = Header(),
        x_retries: int = Header(0),
        accept: list[str] = Header([]),
        ua: Optional[str] = Header(None, alias="User-Agent"),
    ):
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

    assert call(app, headers=[("Cookie", "session=abc; n=3")]) == (
        200,
        {"session": "abc", "theme": "light", "visits": 3},
    )
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
    def submit(
        request,
        name: str = Form(),
        age: int = Form(),
        note: str = Form(""),
        tags: list[str] = Form([]),
        score: Optional[float] = Form(None),
    ):
        return echo(name=name, age=age, note=note, tags=tags, score=score)

    form = [("Content-Type", "application/x-www-form-urlencoded")]
    status, body = call(app, method="POST", headers=form, body=b"name=Ada&age=36&tags=a&tags=b&score=")
    assert (status, body) == (200, {"name": "Ada", "age": 36, "note": "", "tags": ["a", "b"], "score": None})

    status, body = call(
        app, method="POST", headers=form, body=b"name=&age="
    )  # empty text is text; an empty number is missing
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
    assert call(app, method="POST", headers=headers, body=parts) == (
        200,
        {"title": "Holiday", "name": "a.txt", "size": 5},
    )

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

    status, body = call(
        app, method="POST", headers=[("Content-Type", "application/x-www-form-urlencoded")], body=b"name=Ada"
    )
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


def form_models(library):
    """A sign-up form's model, the same with msgspec or pydantic."""
    if library == "msgspec":
        msgspec = pytest.importorskip("msgspec")

        class Signup(msgspec.Struct):
            name: str
            age: int
            tags: list[str] = []
            newsletter: bool = False
            birthday: Optional[date] = None
            nickname: str = ""

        return Signup
    pydantic = pytest.importorskip("pydantic")

    class Signup(pydantic.BaseModel):
        name: str
        age: int
        tags: list[str] = []
        newsletter: bool = False
        birthday: Optional[date] = None
        nickname: str = ""

    return Signup


@pytest.mark.parametrize("library", ["msgspec", "pydantic"])
def test_a_model_filled_from_a_form(library):
    Signup = form_models(library)
    app = App()

    @app.post("/signup")
    def signup(request, body: Signup = Form()):
        return echo(**{k: getattr(body, k) for k in ("name", "age", "tags", "newsletter", "birthday", "nickname")})

    form = [("Content-Type", "application/x-www-form-urlencoded")]
    sent = b"name=Ada&age=36&tags=a&tags=b&newsletter=on&birthday=1815-12-10&nickname="
    status, body = call(app, "/signup", method="POST", headers=form, body=sent)
    assert (status, body) == (
        200,
        {"name": "Ada", "age": 36, "tags": ["a", "b"], "newsletter": True, "birthday": "1815-12-10", "nickname": ""},
    )

    status, body = call(
        app, "/signup", method="POST", headers=form, body=b"name=Ada&age=&birthday="
    )  # empty: not given
    assert status == 422
    assert [e["loc"] for e in body["detail"]] == [["form", "age"]]

    status, body = call(app, "/signup", method="POST", headers=form, body=b"name=Ada&age=old")
    assert status == 422 and body["detail"][0]["loc"][:2] == ["form", "age"]


def test_a_form_model_next_to_a_file_and_left_out():
    msgspec = pytest.importorskip("msgspec")

    class Photo(msgspec.Struct):
        title: str
        public: bool = False

    app = App()

    @app.post("/photos")
    def upload(request, info: Photo = Form(), file: UploadedFile = Form()):
        return echo(title=info.title, public=info.public, size=len(file.data))

    @app.post("/maybe")
    def maybe(request, info: Optional[Photo] = Form(None)):
        return echo(info=None if info is None else info.title)

    boundary = "XyZ"
    parts = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="title"\r\n\r\nHoliday\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.txt"\r\n\r\nhello\r\n'
        f"--{boundary}--\r\n"
    ).encode()
    headers = [("Content-Type", f"multipart/form-data; boundary={boundary}")]
    assert call(app, "/photos", method="POST", headers=headers, body=parts) == (
        200,
        {"title": "Holiday", "public": False, "size": 5},
    )
    assert call(app, "/maybe", method="POST") == (200, {"info": None})


def test_form_models_in_openapi_and_refused_where_they_cant_be():
    msgspec = pytest.importorskip("msgspec")
    validator = pytest.importorskip("openapi_spec_validator")

    class Login(msgspec.Struct):
        user: str
        password: str

    app = App()

    @app.post("/login")
    def login(request, body: Login = Form()):
        pass

    doc = app.openapi()
    validator.validate(doc)
    content = doc["paths"]["/login"]["post"]["requestBody"]["content"]
    assert content == {"application/x-www-form-urlencoded": {"schema": {"$ref": "#/components/schemas/Login"}}}

    with pytest.raises(TypeError, match="both a model and a form"):

        @app.post("/mixed")
        def mixed(request, a: Login, b: Login = Form()):
            pass
