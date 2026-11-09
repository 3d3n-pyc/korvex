"""The app a soak test hammers: plain, JSON, streamed, file and WebSocket routes."""

import atexit
import os
import tempfile

import msgspec

from korvex import App, Response, WebSocket, WebSocketDisconnect

CHUNK_SIZE = 1024
CHUNK_COUNT = 64
FILE_SIZE = 256 * 1024
LEAK_ENABLED = os.environ.get("SOAK_LEAK") == "1"
leak: list[bytes] = []

app = App()


class Item(msgspec.Struct):
    name: str
    n: int


def make_file() -> str:
    """Creates the test file, a new one in the temp folder for each worker."""
    fd, path = tempfile.mkstemp(prefix="korvex-soak-")
    with os.fdopen(fd, "wb") as file:
        file.write(b"k" * FILE_SIZE)
    atexit.register(os.remove, path)
    return path


FILE_PATH = make_file()


def chunks():
    """Yields CHUNK_COUNT blocks of CHUNK_SIZE bytes."""
    block = b"s" * CHUNK_SIZE
    for _ in range(CHUNK_COUNT):
        yield block


@app.get("/plain")
async def plain():
    if LEAK_ENABLED:
        leak.append(bytes(10 * 1024))
    return Response.text("ok")


@app.post("/json")
async def echo_json(body: Item) -> Item:
    return body


@app.get("/stream")
async def stream():
    return Response.stream(chunks())


@app.get("/file")
async def file():
    return Response.file(FILE_PATH)


@app.websocket("/ws")
async def ws_echo(ws: WebSocket):
    await ws.accept()
    while True:
        try:
            message = await ws.receive()
        except WebSocketDisconnect:
            return
        if isinstance(message, str):
            await ws.send_text(message)
        else:
            await ws.send_bytes(message)
