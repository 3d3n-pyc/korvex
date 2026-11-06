"""Tests for dependencies: Depends(), route and group dependencies,
teardown of those that yield, overrides, and WebSockets."""

import asyncio
import contextlib
import json
from typing import Annotated, Optional

import pytest
from test_app import make_scope, response_from, run_app
from test_websocket import run_ws, ws_scope

from korvex import App, Cookie, Depends, Header, HTTPException, Request, Response, WebSocket


def call(app, path="/", headers=(), query=b"", method="GET", body=b""):
    scope = make_scope(method, path, headers=list(headers), query_string=query)
    status, response_headers, raw = response_from(run_app(app, scope, body=body))
    is_json = ("Content-Type", "application/json") in response_headers
    return status, json.loads(raw) if is_json else raw.decode()


USERS = {"t1": "ada", "t2": "bob"}


def current_user(token: Optional[str] = Cookie(None, alias="session")) -> str:
    if token not in USERS:
        raise HTTPException(401, "sign in first")
    return USERS[token]


CurrentUser = Annotated[str, Depends(current_user)]


def test_a_dependency_gives_its_value_and_can_stop_the_request():
    app = App()

    @app.get("/me")
    def me(request, user: str = Depends(current_user)):
        return Response.json({"user": user})

    @app.get("/me2")
    async def me2(request, user: CurrentUser):
        return Response.json({"user": user})

    assert call(app, "/me", headers=[("Cookie", "session=t1")]) == (200, {"user": "ada"})
    assert call(app, "/me2", headers=[("Cookie", "session=t2")]) == (200, {"user": "bob"})
    assert call(app, "/me") == (401, "sign in first")


def test_dependencies_of_dependencies_sync_and_async_and_the_request():
    app = App()

    async def tenant(x_tenant: str = Header("main")) -> str:
        await asyncio.sleep(0)
        return x_tenant

    def scoped_user(request: Request, user: CurrentUser, tenant: str = Depends(tenant)) -> str:
        return f"{tenant}/{user} via {request.method}"

    def path_of(request) -> str:  # unannotated `request` works for dependencies
        return request.path

    @app.get("/")
    def read(request, who: str = Depends(scoped_user), where: str = Depends(path_of)):
        return Response.json({"who": who, "where": where})

    status, body = call(app, headers=[("Cookie", "session=t1"), ("X-Tenant", "acme")])
    assert body == {"who": "acme/ada via GET", "where": "/"}


def test_a_dependency_runs_once_per_request_unless_told_otherwise():
    app = App()
    calls = []

    def counted() -> int:
        calls.append(1)
        return len(calls)

    def a(n: int = Depends(counted)) -> int:
        return n

    def b(n: int = Depends(counted)) -> int:
        return n

    def fresh(n: int = Depends(counted, cache=False)) -> int:
        return n

    @app.get("/")
    def read(request, x: int = Depends(a), y: int = Depends(b), z: int = Depends(fresh)):
        return Response.json([x, y, z])

    assert call(app) == (200, [1, 1, 2])
    assert call(app) == (200, [3, 3, 4])  # and once per request, not once for all


def test_validation_errors_from_dependencies_and_the_handler_come_together():
    app = App()

    def paging(limit: int = 20, offset: int = 0) -> tuple:
        return limit, offset

    def also_paging(limit: int = 20) -> int:
        return limit

    @app.get("/")
    def read(request, page: tuple = Depends(paging), again: int = Depends(also_paging), q: int = 0):
        return Response.json([*page, again, q])

    assert call(app, query=b"limit=5&offset=10&q=1") == (200, [5, 10, 5, 1])
    status, body = call(app, query=b"limit=x&q=y")
    assert status == 422
    assert [e["loc"] for e in body["detail"]] == [["query", "limit"], ["query", "q"]]  # limit once, though read twice


def test_route_and_group_dependencies_check_without_giving_a_value():
    app = App()
    seen = []

    def signed_in(user: CurrentUser) -> None:
        seen.append(("signed_in", user))

    def audited(request: Request) -> None:
        seen.append(("audited", request.path))

    api = app.group("/api", dependencies=[Depends(signed_in)])
    admin = api.group("/admin", dependencies=[Depends(audited)])

    @api.get("/tasks")
    def tasks(request):
        return Response.text("tasks")

    @admin.post("/purge", dependencies=[Depends(lambda: seen.append(("route", None)))])
    def purge(request):
        return Response.text("purged")

    assert call(app, "/api/tasks") == (401, "sign in first")
    assert call(app, "/api/tasks", headers=[("Cookie", "session=t1")]) == (200, "tasks")
    seen.clear()
    assert call(app, "/api/admin/purge", method="POST", headers=[("Cookie", "session=t2")]) == (200, "purged")
    assert seen == [("signed_in", "bob"), ("audited", "/api/admin/purge"), ("route", None)]  # group first, outer first


def test_yield_dependencies_are_torn_down_after_the_handler_last_first():
    app = App()
    events = []

    def connection():
        events.append("open db")
        try:
            yield "db"
        finally:  # the handler's error is raised at the yield
            events.append("close db")

    async def transaction(db: str = Depends(connection)):
        events.append("begin")
        try:
            yield f"tx on {db}"
        except Exception as error:
            events.append(f"rollback after {error!r}")
            raise
        else:
            events.append("commit")

    @app.get("/ok")
    def ok(request, tx: str = Depends(transaction)):
        events.append(f"handler with {tx}")
        return Response.text("ok")

    @app.get("/fails")
    def fails(request, tx: str = Depends(transaction)):
        raise HTTPException(409, "conflict")

    assert call(app, "/ok") == (200, "ok")
    assert events == ["open db", "begin", "handler with tx on db", "commit", "close db"]
    events.clear()
    assert call(app, "/fails") == (409, "conflict")  # the error still gets its response
    assert events == ["open db", "begin", "rollback after HTTPException(409, 'conflict')", "close db"]


def test_a_teardown_that_catches_the_error_does_not_swallow_it():
    app = App()

    def lenient():
        with contextlib.suppress(Exception):  # caught, not re-raised
            yield

    @app.get("/")
    def read(request, _: None = Depends(lenient)):
        raise HTTPException(418)

    assert call(app)[0] == 418


def test_an_error_in_a_teardown_is_a_500(caplog):
    app = App()

    def broken():
        yield
        raise RuntimeError("teardown failed")

    @app.get("/")
    def read(request, _: None = Depends(broken)):
        return Response.text("ok")

    assert call(app)[0] == 500


def test_a_streamed_response_keeps_its_dependencies_until_sent():
    app = App()
    events = []

    def resource():
        events.append("open")
        yield "r"
        events.append("close")

    @app.get("/")
    def stream(request, r: str = Depends(resource)):
        def chunks():
            for n in range(3):
                events.append(f"chunk {n} with {r}")
                yield str(n)

        return Response.stream(chunks())

    assert call(app) == (200, "012")
    assert events == ["open", "chunk 0 with r", "chunk 1 with r", "chunk 2 with r", "close"]
    events.clear()
    status, _, body = response_from(run_app(app, make_scope("HEAD", "/")))
    assert events == ["open", "close"]  # not sent, still torn down


def test_overrides_replace_a_dependency_everywhere():
    app = App()

    def signed_in(user: CurrentUser) -> str:
        return user

    @app.get("/", dependencies=[Depends(signed_in)])
    def read(request, user: CurrentUser):
        return Response.json({"user": user})

    app.dependency_overrides[current_user] = lambda: "tester"
    assert call(app) == (200, {"user": "tester"})

    def from_header(x_user: str = Header()) -> str:  # an override with parameters of its own
        return x_user

    app.dependency_overrides[current_user] = from_header
    assert call(app, headers=[("X-User", "carol")]) == (200, {"user": "carol"})
    assert call(app)[0] == 422

    app.dependency_overrides.clear()
    assert call(app)[0] == 401


def test_dependencies_can_read_path_parameters_and_the_body():
    msgspec = pytest.importorskip("msgspec")

    class Note(msgspec.Struct):
        text: str

    app = App()

    def note_owner(id: int, note: Note) -> str:  # `{id:int}` from the path, Note from the body
        return f"{id}:{note.text}"

    @app.put("/notes/{id:int}")
    def update(request, id: int, note: Note, owner: str = Depends(note_owner)):
        return Response.json({"id": id, "text": note.text, "owner": owner})

    status, body = call(
        app, "/notes/7", method="PUT", headers=[("Content-Type", "application/json")], body=b'{"text": "hi"}'
    )
    assert body == {"id": 7, "text": "hi", "owner": "7:hi"}


def test_mistakes_are_caught_at_registration():
    app = App()
    with pytest.raises(TypeError, match="dependencies takes Depends"):

        @app.get("/1", dependencies=[current_user])
        def one(request):
            pass

    def first(x=None):
        pass

    def second(x=Depends(first)):
        pass

    first.__defaults__ = (Depends(second),)  # first -> second -> first
    with pytest.raises(TypeError, match="dependency cycle"):

        @app.get("/2")
        def two(request, x=Depends(first)):
            pass

    def needs_ws(ws: WebSocket) -> None:
        pass

    with pytest.raises(TypeError, match="WebSocket in an HTTP route"):

        @app.get("/3")
        def three(request, _=Depends(needs_ws)):
            pass

    def unclear(thing) -> None:
        pass

    with pytest.raises(TypeError, match="can't tell where parameter 'thing'"):

        @app.get("/4")
        def four(request, _=Depends(unclear)):
            pass


def test_websockets_get_parameters_and_dependencies():
    app = App()
    events = []

    def session():
        events.append("open")
        yield "s"
        events.append("close")

    @app.websocket("/live/{room}", dependencies=[Depends(lambda: events.append("checked"))])
    async def live(ws: WebSocket, room: str, user: CurrentUser, s: str = Depends(session), x_tab: str = Header("1")):
        await ws.accept()
        await ws.send_text(f"{user} in {room} on tab {x_tab} with {s}")

    scope = ws_scope("/live/kitchen", headers=[("Cookie", "session=t1"), ("X-Tab", "2")])
    sent = run_ws(app, scope)
    assert sent[1] == {"type": "websocket.send", "text": "ada in kitchen on tab 2 with s"}
    assert events == ["checked", "open", "close"]

    events.clear()
    sent = run_ws(app, ws_scope("/live/kitchen"))  # no session: refused before accept
    assert sent == [{"type": "websocket.close", "code": 1000, "reason": ""}]
    assert events == ["checked"]


def test_websocket_teardown_after_the_client_leaves():
    app = App()
    events = []

    def session():
        try:
            yield
        finally:
            events.append("closed")

    @app.websocket("/echo")
    async def echo(ws: WebSocket, _: None = Depends(session)):
        await ws.accept()
        async for message in ws:
            await ws.send_text(message)

    run_ws(app, ws_scope("/echo"), incoming=["a"])
    assert events == ["closed"]


def test_a_websocket_has_no_body():
    msgspec = pytest.importorskip("msgspec")

    class Body(msgspec.Struct):
        x: int

    app = App()
    with pytest.raises(TypeError, match="no request body"):

        @app.websocket("/ws")
        async def ws(ws: WebSocket, body: Body):
            pass


def test_a_dependency_must_yield_exactly_once(caplog):
    app = App()

    def twice():
        yield 1
        yield 2

    async def never():
        if False:
            yield

    @app.get("/twice")
    def a(request, _: int = Depends(twice)):
        return Response.text("ok")

    @app.get("/never")
    def b(request, _: None = Depends(never)):
        return Response.text("ok")

    assert call(app, "/twice")[0] == 500
    assert "yielded more than once" in caplog.text
    assert call(app, "/never")[0] == 500
    assert "didn't yield" in caplog.text


def guarded_and_public(stack_public_on_top):
    """One function on a guarded route and on a public one, stacked in the
    given order."""
    app = App()
    admin = app.group("/admin", dependencies=[Depends(current_user)])

    def items(request):
        return Response.text("items")

    if stack_public_on_top:
        app.get("/public/items")(admin.get("/items")(items))
    else:
        admin.get("/items")(app.get("/public/items")(items))
    return app


@pytest.mark.parametrize("public_on_top", [True, False])
def test_each_route_keeps_its_own_dependencies_when_a_function_serves_two(public_on_top):
    app = guarded_and_public(public_on_top)
    assert call(app, "/admin/items") == (401, "sign in first")
    assert call(app, "/admin/items", headers=[("Cookie", "session=t1")]) == (200, "items")
    assert call(app, "/public/items") == (200, "items")


def test_functions_of_the_same_name_from_a_factory_stay_apart():
    app = App()

    def crud(group, tag):
        @group.get("/stats")
        def stats(request):  # the same qualname each time
            return Response.text(tag)

    crud(app.group("/admin", dependencies=[Depends(current_user)]), "admin")
    crud(app.group("/public"), "public")
    assert call(app, "/admin/stats") == (401, "sign in first")
    assert call(app, "/admin/stats", headers=[("Cookie", "session=t1")]) == (200, "admin")
    assert call(app, "/public/stats") == (200, "public")


def test_a_websocket_function_on_two_paths_keeps_each_guard():
    app = App()
    admin = app.group("/admin", dependencies=[Depends(current_user)])

    async def feed(ws: WebSocket):
        await ws.accept()
        await ws.send_text("data")

    app.websocket("/public/ws")(admin.websocket("/ws")(feed))
    assert run_ws(app, ws_scope("/admin/ws")) == [{"type": "websocket.close", "code": 1000, "reason": ""}]
    assert run_ws(app, ws_scope("/admin/ws", headers=[("Cookie", "session=t1")]))[1]["text"] == "data"
    assert run_ws(app, ws_scope("/public/ws"))[1]["text"] == "data"
