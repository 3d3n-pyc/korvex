"""The app served by test_server.py through Korvex's built-in server.

Run: python tests/server_app.py PORT
"""

import asyncio
import os
import sys
from pathlib import Path

from korvex import App, HTTPException, Response
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
    return Response.json({"body": body, "query": request.query_params, "cookies": request.cookies})


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
    if ws.query_params.get("token") != ["secret"]:
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


@app.get("/fail")
def fail(request):
    raise RuntimeError("boom")


@app.get("/teapot")
def teapot(request):
    raise HTTPException(418)


if __name__ == "__main__":
    import korvex.serve

    if os.environ.get("STARTUP_WINDOW"):  # lets tests wait less than 5 s
        korvex.serve.STARTUP_WINDOW = float(os.environ["STARTUP_WINDOW"])
    app.run(
        port=int(sys.argv[1]),
        workers=int(os.environ.get("WORKERS", "1")),
        threads=int(os.environ.get("THREADS", "1")),
        graceful_timeout=float(os.environ.get("GRACE", "30")),
        max_body=int(os.environ.get("MAX_BODY", str(1024 * 1024))),
        timeout=float(os.environ.get("TIMEOUT", "30")),
        uds=os.environ.get("UDS"),
        forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW", "127.0.0.1,::1").split(","),
        access_log=bool(os.environ.get("ACCESS_LOG")),
        ws_ping_interval=float(os.environ.get("WS_PING", "20")),
        forwarded_host=bool(os.environ.get("FORWARDED_HOST")),
    )
