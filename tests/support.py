"""What the tests share.

- `request()`: one request through `TestClient`, as most tests need it.
- `make_scope()`, `run_app()`, `response_from()`, `run_lifespan()`,
  `ws_scope()` and `run_ws()`: raw ASGI, for the tests of the protocol
  itself (the messages the App sends and expects).
"""

import asyncio

from korvex.testing import TestClient, TestResponse


def request(app, method="GET", path="/", **kwargs) -> TestResponse:
    """One request: no lifespan, and an error the app answers with a 500
    gives the 500 (the tests check it) rather than being raised."""
    client = TestClient(app, raise_server_errors=False)
    try:
        return client.request(method, path, **kwargs)
    finally:
        client.close()


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


def ws_scope(path, subprotocols=(), headers=()):
    return {
        "type": "websocket",
        "path": path,
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers],
        "subprotocols": list(subprotocols),
    }


def run_ws(app, scope, incoming=()):
    """Plays the server: sends connect, then `incoming` messages (str or
    bytes), then a disconnect; returns what the app sent."""
    messages = [{"type": "websocket.connect"}]
    for message in incoming:
        key = "text" if isinstance(message, str) else "bytes"
        messages.append({"type": "websocket.receive", key: message})
    messages.append({"type": "websocket.disconnect", "code": 1001})
    queue = iter(messages)
    sent = []

    async def receive():
        return next(queue)

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    return sent
