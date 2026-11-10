# Testing

`korvex.testing` runs the app in your tests, without a server:

Use this when you want to check your app with automated tests (pytest, for
example). Here is a tiny app and its test:

```python
from korvex import App, Form, HTTPException, Response, WebSocket
from korvex.middleware import current_session, sessions
from korvex.testing import TestClient

app = App()
sessions(app, secret_key="x" * 32)  # a throwaway key, for the example


@app.post("/login")
def login(password: str = Form()):
    if password != "s3cret":
        raise HTTPException(401, "wrong password")
    current_session()["user"] = "ada"
    return Response.redirect("/", status=303)


@app.get("/api/tasks")
def tasks(done: bool = False):
    if "user" not in current_session():
        raise HTTPException(401, "log in first")
    return Response.json([])


@app.websocket("/live")
async def live(ws: WebSocket):
    await ws.accept()
    await ws.send_json({"event": "presence"})


def test_tasks():
    with TestClient(app) as client:
        assert client.post("/login", form={"password": "s3cret"}).status == 303
        response = client.get("/api/tasks", query={"done": "false"})
        assert response.json() == []  # the session cookie was sent back

        with client.websocket("/live") as ws:
            assert ws.receive_json()["event"] == "presence"
```

## With pytest

Put the client in a fixture, in `conftest.py`. The `with` block runs the
app's startup and shutdown hooks around each test:

```python
import pytest

from korvex.testing import TestClient

from myapp import app  # your code: where your App lives


@pytest.fixture
def client():
    with TestClient(app) as client:
        yield client
```

```python
def test_tasks_need_login(client):
    assert client.get("/api/tasks").status == 401
```

## Requests

- `get()`, `post()`, `put()`, `patch()`, `delete()`, `head()` and
  `options()` take `query=`, `headers=`, `cookies=`, and a body:
  `json=`, `form=` (with `files={"doc": ("a.pdf", data, "application/pdf")}`
  for a multipart form), or raw `body=`.
- A response has `status`, `headers`, `body`, `text`, `json()`, and the
  `url` it came from.
- Cookies the app sets are kept in `client.cookies` and sent back.
- `follow_redirects=True` follows redirects, up to 20 (after a 303, or a
  301/302 answering a POST, as a GET).
- A full URL sets the scheme and host: `client.get("https://acme.example.com/")`.
  Otherwise requests go to `base_url`, `http://testserver` by default.
- `headers=` and `cookies=` given to the client go with every request:
  `TestClient(app, headers={"Authorization": "Bearer t0ken"})`.

## What the client does for you

- In a `with` block, the app's startup hooks run first and its shutdown
  hooks last; without one, no hook runs. Every request runs on the same event loop.
- An error the app answers with a 500 is raised in the test, with its
  traceback. `raise_server_errors=False` gives you the 500 instead.
- A request or WebSocket message not received within `timeout` (5
  seconds) raises `TimeoutError`; `timeout=None` waits forever.

## Streams and WebSockets

```python
# `client` is a TestClient, and /events an endpoint of yours
with client.stream("GET", "/events") as response:
    first = next(response.iter_text())  # leaving the block stops the stream
```

To read a Server-Sent Events stream, `iter_events()` (`aiter_events()` on
`AsyncTestClient`) yields `ServerSentEvent`s:

```python
# /feed is an endpoint of yours that sends events
with client.stream("GET", "/feed") as response:
    event = next(response.iter_events())  # event.event, event.data, event.id
```

`client.websocket(path)` opens one: `send_text()`, `send_json()`...,
`receive_text()`, `receive_json()`... A refused connection raises
`WebSocketDisconnect`. It takes `query=`, `headers=`, `cookies=` and
`subprotocols=["chat"]`; `ws.subprotocol` and `ws.headers` are those the
app accepted with.

## Replacing a dependency

```python
from korvex import App, Depends, Response
from korvex.testing import TestClient

app = App()


class User:  # your code
    def __init__(self, id: int, name: str):
        self.id, self.name = id, name


def current_user() -> User:  # your code: reads the session, a token...
    raise NotImplementedError


@app.get("/me")
def me(user: User = Depends(current_user)):
    return Response.json({"name": user.name})


app.dependency_overrides[current_user] = lambda: User(id=1, name="test")
```

## Async tests

`AsyncTestClient` does the same, awaited, for `async` tests (pytest-asyncio
or anyio):

```python
from korvex.testing import AsyncTestClient


async def test_live():  # app: the one above
    async with AsyncTestClient(app) as client:
        response = await client.get("/api/tasks")
        assert response.status == 401
        async with client.websocket("/live") as ws:
            assert (await ws.receive_json())["event"] == "presence"
```

Next: [The built-in server](server.md).
