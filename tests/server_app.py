"""The app served by test_server.py through Korvex's built-in server.

Run: python tests/server_app.py PORT
"""

import asyncio
import os
import sys
from pathlib import Path

from korvex import App, Depends, HTTPException, Response, ServerSentEvent
from korvex._background import BackgroundTasks
from korvex.middleware import rate_limit, request_id

app = App()
if os.environ.get("REQUEST_ID"):
    request_id(app)
if os.environ.get("RATE_LIMIT"):
    rate_limit(app, requests=int(os.environ["RATE_LIMIT"]), per=60)
if os.environ.get("STATIC_DIR"):
    app.static("/static", Path(os.environ["STATIC_DIR"]))


@app.on_startup
def startup():
    if os.environ.get("FAIL_STARTUP"):
        raise ConnectionError("database unreachable")
    app.local.opened_on = asyncio.get_running_loop()  # like a connection pool, one per loop
    print("STARTED", flush=True)


@app.on_shutdown
def shutdown():
    print("STOPPED", flush=True)


@app.after_request
def powered_by(request, response):
    response.headers.set("X-Powered-By", "korvex")


@app.exception_handler(413)
def too_large(request, exc):
    return Response.json({"error": "too large", "path": request.path}, status=413)


@app.get("/users/{id:int}")
def get_user(request, id):
    return Response.json({"id": id})


@app.get("/wait")
async def wait(request):
    await asyncio.sleep(0.2)
    return Response.text("waited")


@app.get("/stream")
async def stream(request):
    async def chunks():
        for i in range(3):
            await asyncio.sleep(0.01)
            yield f"{i}\n"

    return Response.stream(chunks())


@app.post("/echo")
async def echo(request):
    body = (await request.body()).decode()
    return Response.json(
        {
            "body": body,
            "query": {name: request.query.get_all(name) for name in request.query},
            "cookies": request.cookies,
        }
    )


@app.get("/slow")
async def slow(request):
    await asyncio.sleep(1)
    return Response.text("slow done")


@app.get("/pid")
def pid(request):
    return Response.text(str(os.getpid()))


@app.get("/local")
def local(request):
    return Response.json(app.local.opened_on is asyncio.get_running_loop())


@app.get("/thread")
def thread(request):
    import threading

    return Response.text(threading.current_thread().name)


@app.get("/crash")
def crash(request):
    os._exit(1)  # a worker dying abruptly, as if it segfaulted


app.constraint("slug", r"[a-z0-9]+(?:-[a-z0-9]+)*")


@app.post("/notes/{key:slug}", max_body=5_000_000)  # a body limit on a route with a custom constraint
async def note(request, key):
    return Response.text(f"{key}: {len(await request.body())}")


@app.post("/form")
async def form(request):
    return Response.text(str(len(await request.form())))


@app.post("/upload", max_body=5_000_000)
async def upload(request):
    return Response.text(str(len(await request.body())))


@app.group("/api").post("/import", max_body=5_000_000)
async def import_data(request):
    return Response.text(str(len(await request.body())))


@app.get("/client")
def client(request):
    return Response.json({"client": request.client, "scheme": request.scheme, "host": request.headers.get("host")})


@app.websocket("/ws/{room}")
async def ws_echo(ws, room):
    if ws.query.get("token") != "secret":
        await ws.close(code=1008)  # refuses it: a 403
        return
    await ws.accept(subprotocol="chat" if "chat" in ws.subprotocols else None)
    async for message in ws:
        if isinstance(message, bytes):
            await ws.send_bytes(message[::-1])
        else:
            await ws.send_text(f"{room}: {message}")
    print("WS CLOSED", flush=True)


# Backpressure: how much a stream or a WebSocket produced so far, for a
# client that stopped reading.
PRODUCED = {"stream": 0, "ws": 0}
FIREHOSE = 100 * 1024 * 1024
PIECE = 64 * 1024


@app.get("/firehose")
async def firehose(request):
    async def chunks():
        piece = b"x" * PIECE
        for _ in range(FIREHOSE // PIECE):
            PRODUCED["stream"] += PIECE
            yield piece

    return Response.stream(chunks())


@app.get("/produced")
def produced(request):
    return Response.json(PRODUCED)


@app.websocket("/ws-firehose")
async def ws_firehose(ws):
    await ws.accept()
    message = b"x" * (8 * PIECE)
    for _ in range(FIREHOSE // len(message)):
        PRODUCED["ws"] += len(message)
        await ws.send_bytes(message)


@app.get("/link", name="link")
def link(request):
    return Response.json({"url": app.url_for("link"), "root_path": request.root_path})


@app.get("/api/status")
def api_status():
    return Response.text("status")


@app.get("/admin")
def admin():
    return Response.text("admin")


@app.get("/fail")
def fail(request):
    raise RuntimeError("boom")


@app.get("/teapot")
def teapot(request):
    raise HTTPException(418)


async def slow_marker(path: str, seconds: float) -> None:
    await asyncio.sleep(seconds)
    Path(path).write_text("done")


@app.get("/background")
async def background(request, tasks: BackgroundTasks):
    tasks.add(slow_marker, os.environ["BG_MARKER"], float(os.environ.get("BG_SECONDS", "1")))
    return Response.json({"queued": True})


def note_event(text: str) -> None:
    """Appends a line to the SSE_MARKER file, to observe what ran."""
    with open(os.environ["SSE_MARKER"], "a") as marker:
        marker.write(text + "\n")


def sse_dependency():
    yield "dep"
    note_event("dependency closed")


async def sse_note_task() -> None:
    note_event("background ran")


@app.get("/sse")
async def sse(request, tasks: BackgroundTasks, _dep: str = Depends(sse_dependency)):
    async def events():
        try:
            yield ServerSentEvent("hello", id="1")
            await asyncio.sleep(3600)  # idle: nothing more to say
        finally:
            note_event("generator closed")

    tasks.add(sse_note_task)
    return Response.sse(events(), ping=0.2)


@app.get("/sse-sync")
def sse_sync(request):
    return Response.sse(ServerSentEvent(n) for n in range(3))


@app.get("/sse-bad")
async def sse_bad(request):
    async def events():
        yield ServerSentEvent("ok")
        yield ServerSentEvent("x", id="a\nb")

    return Response.sse(events(), ping=0)


async def raw_sub_app(scope, receive, send):
    """A raw ASGI app mounted at /raw: its behaviour depends on the path."""
    if scope["type"] == "websocket":
        await receive()
        await send({"type": "websocket.accept"})
        while (message := await receive())["type"] == "websocket.receive":
            await send({"type": "websocket.send", "text": "mounted: " + message["text"]})
        return
    path = scope["path"][len(scope["root_path"]) :]
    if path == "/stream":
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
        for i in range(5):
            await send({"type": "http.response.body", "body": f"{i};".encode(), "more_body": True})
            await asyncio.sleep(0.01)
        await send({"type": "http.response.body", "body": b"end"})
    elif path.startswith("/echo"):
        message = await receive()
        info = f"{scope['method']} {scope['path']} {scope['root_path']} {scope['raw_path'].decode()} "
        body = info.encode() + message["body"]
        await send({"type": "http.response.start", "status": 201, "headers": [(b"x-sub", b"1")]})
        await send({"type": "http.response.body", "body": body})
    elif path == "/disconnect":
        await receive()
        waiting = asyncio.ensure_future(receive())  # resolves at http.disconnect
        await send({"type": "http.response.start", "status": 200, "headers": []})
        while not waiting.done():  # a send to a client that left is what reveals it
            await send({"type": "http.response.body", "body": b"x" * 1024, "more_body": True})
            await asyncio.sleep(0.02)
        Path(os.environ["DISCONNECT_FILE"]).write_text("disconnected")
    elif path == "/boom":
        raise RuntimeError("sub-app failed")
    elif path == "/silent":
        return
    else:
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": b"hello from the mount"})


app.mount("/raw", raw_sub_app)

inner = App()


@inner.get("/hi")
def inner_hi(request):
    return Response.text("inner " + request.root_path)


app.mount("/inner", inner)


if __name__ == "__main__":
    import korvex._serve

    if os.environ.get("STARTUP_WINDOW"):  # lets tests wait less than 5 s
        korvex._serve.STARTUP_WINDOW = float(os.environ["STARTUP_WINDOW"])
    app.run(
        port=int(sys.argv[1]),
        workers=int(os.environ.get("WORKERS", "1")),
        threads=int(os.environ.get("THREADS", "1")),
        graceful_timeout=float(os.environ.get("GRACE", "30")),
        max_body=int(os.environ.get("MAX_BODY", str(1024 * 1024))),
        read_timeout=float(os.environ.get("TIMEOUT", "30")),
        uds=os.environ.get("UDS"),
        forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW", "127.0.0.1,::1").split(","),
        access_log=bool(os.environ.get("ACCESS_LOG")),
        ws_ping_interval=float(os.environ.get("WS_PING", "20")),
        forwarded_host=bool(os.environ.get("FORWARDED_HOST")),
        max_connections=int(os.environ.get("MAX_CONNECTIONS", "0")),
        max_requests=int(os.environ.get("MAX_REQUESTS", "0")),
        **({"root_path": os.environ["ROOT_PATH"]} if "ROOT_PATH" in os.environ else {}),
    )
