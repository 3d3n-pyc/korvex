# Testing

`korvex.testing` runs the app in your tests, without a server:

```python
from korvex.testing import TestClient


def test_tasks():
    with TestClient(app) as client:
        assert client.post("/login", form={"password": "s3cret"}).status == 303
        response = client.get("/api/tasks", query={"done": "false"})
        assert response.json() == []  # the session cookie was sent back

        with client.websocket("/live") as ws:
            assert ws.receive_json()["event"] == "presence"
```

## Requests

- `get()`, `post()`, `put()`, `patch()`, `delete()`, `head()` and
  `options()` take `query=`, `headers=`, `cookies=`, and a body:
  `json=`, `form=` (with `files={"doc": ("a.pdf", data, "application/pdf")}`
  for a multipart form), or raw `body=`.
- A response has `status`, `headers`, `body`, `text`, `json()`, and the
  `url` it came from.
- Cookies the app sets are kept in `client.cookies` and sent back.
- `follow_redirects=True` follows a redirect.
- A full URL sets the scheme and the host: `client.get("https://acme.example.com/")`.
  Otherwise requests go to `base_url`, `http://testserver` by default.
- `headers=` and `cookies=` given to the client are sent with every request:
  `TestClient(app, headers={"Authorization": "Bearer t0ken"})`.

## What the client does for you

- In a `with` block, the app's startup hooks run first and its shutdown
  hooks last. Every request runs on the same event loop.
- An error the app answers with a 500 is raised in the test, with its
  traceback. `raise_server_errors=False` gives you the 500 instead.
- A request or a WebSocket message that doesn't come within `timeout`
  (5 seconds) raises `TimeoutError`, rather than hang the test suite.

## Streams and WebSockets

```python
with client.stream("GET", "/events") as response:
    first = next(response.iter_text())  # leaving the block stops the stream
```

`client.websocket(path)` opens one: `send_text()`, `send_json()`...,
`receive_text()`, `receive_json()`... A refused connection raises
`WebSocketDisconnect`. It takes `query=`, `headers=`, `cookies=` and
`subprotocols=["chat"]`; `ws.subprotocol` and `ws.headers` are those the
app accepted with.

## Replacing a dependency

```python
app.dependency_overrides[current_user] = lambda: User(id=1, name="test")
```

## Async tests

`AsyncTestClient` does the same, awaited, for `async` tests (with
pytest-asyncio or anyio):

```python
async with AsyncTestClient(app) as client:
    response = await client.get("/")
    async with client.websocket("/live") as ws:
        await ws.send_text("hi")
```
