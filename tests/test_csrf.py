import pytest

from korvex import App, Response, WebSocket, WebSocketDisconnect
from korvex.middleware._csrf import csrf
from korvex.testing import TestClient


def make_app(**options) -> TestClient:
    app = App()
    csrf(app, **options)

    @app.get("/")
    def home_get():
        return Response.text("ok")

    @app.post("/")
    def home():
        return Response.text("ok")

    @app.post("/webhooks/stripe")
    def stripe():
        return Response.text("hook")

    @app.post("/webhooksX")
    def other():
        return Response.text("x")

    @app.post("/webhooks")
    def bare():
        return Response.text("bare")

    @app.websocket("/ws")
    async def ws(socket: WebSocket):
        await socket.accept()
        await socket.send_text("hi")

    return TestClient(app)


def post(client, **headers):
    return client.post("/", headers=headers).status


def test_sec_fetch_site():
    client = make_app()
    assert post(client, **{"Sec-Fetch-Site": "same-origin"}) == 200
    assert post(client, **{"Sec-Fetch-Site": "none"}) == 200
    assert post(client, **{"Sec-Fetch-Site": "cross-site"}) == 403
    assert post(client, **{"Sec-Fetch-Site": "same-site"}) == 403
    refused = client.post("/", headers={"Sec-Fetch-Site": "cross-site"})
    assert refused.json() == {"detail": "cross-origin request refused"}


def test_origin_against_host():
    client = make_app()
    assert post(client, Origin="http://testserver") == 200
    assert post(client, Origin="HTTP://TestServer") == 200
    assert post(client, Origin="http://evil.example") == 403
    assert post(client, Origin="http://testserver:81") == 403
    assert post(client, Origin="null") == 403


def test_no_headers_passes():
    assert post(make_app()) == 200


def test_trusted_origins():
    client = make_app(trusted_origins=["https://Admin.example.com"])
    assert post(client, Origin="https://admin.example.com") == 200
    assert post(client, **{"Sec-Fetch-Site": "cross-site", "Origin": "https://admin.example.com"}) == 200
    assert post(client, **{"Sec-Fetch-Site": "cross-site", "Origin": "https://other.example.com"}) == 403
    assert post(client, Origin="http://admin.example.com") == 403


def test_exempt():
    client = make_app(exempt=["/webhooks/"])
    cross = {"Sec-Fetch-Site": "cross-site"}
    assert client.post("/webhooks/stripe", headers=cross).status == 200
    assert client.post("/webhooksX", headers=cross).status == 403
    assert client.post("/webhooks", headers=cross).status == 403
    assert make_app(exempt=["/webhooks"]).post("/webhooks", headers=cross).status == 200


def test_safe_methods_pass():
    client = make_app()
    assert client.get("/", headers={"Sec-Fetch-Site": "cross-site"}).status == 200
    assert client.options("/", headers={"Origin": "http://evil.example"}).status != 403


def test_websocket():
    client = make_app()
    with client.websocket("/ws", headers={"Origin": "http://testserver"}) as ws:
        assert ws.receive_text() == "hi"
    with pytest.raises(WebSocketDisconnect), client.websocket("/ws", headers={"Sec-Fetch-Site": "cross-site"}):
        pass
    with pytest.raises(WebSocketDisconnect), client.websocket("/ws", headers={"Origin": "http://evil.example"}):
        pass


@pytest.mark.parametrize("bad", ["example.com", "ftp://example.com", "https://example.com/path", "https://", ""])
def test_bad_trusted_origin(bad):
    with pytest.raises(ValueError):
        csrf(App(), trusted_origins=[bad])


@pytest.mark.parametrize("bad", ["hook", "", "webhooks/"])
def test_bad_exempt(bad):
    with pytest.raises(ValueError):
        csrf(App(), exempt=[bad])
