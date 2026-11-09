# WebSockets

```python
from korvex import WebSocket


@app.websocket("/chat/{room}")
async def chat(ws: WebSocket, room: str, user: CurrentUser):
    await ws.accept()
    async for message in ws:  # text or bytes, until the client leaves
        await ws.send_text(f"{user.name} in {room}: {message}")
```

- The handler gets the `WebSocket` by naming a parameter `ws` or
  `websocket`, or annotating it.
- Before `accept()`, the handler can look at the handshake: `headers`,
  `cookies`, `query`, `path_params`, `client`. `close()` before accepting
  refuses the connection (a 403).
- `receive()` gives the next message, `receive_text()`, `receive_bytes()`
  and `receive_json()` one of that kind; they raise `WebSocketDisconnect`
  once the client is gone. `send_text()`, `send_bytes()`, `send_json()`
  and `close(code)` go the other way.
- Parameters and [dependencies](dependencies.md) work as for HTTP, without
  a body. A dependency raising `HTTPException` refuses the connection. A
  dependency both kinds of route share takes a `Connection`, not a
  `Request`.
- Groups and hosts declare WebSockets too: `api.websocket(...)`.
- [Before hooks](errors-and-hooks.md#hooks-around-every-request) run on
  the handshake: check its `Origin` there, which browsers don't check for
  WebSockets. After hooks don't run: there's no response.

They work the same under any ASGI server and in the
[built-in server](server.md), which also pings idle connections and makes a
slow client wait instead of piling messages in memory.
