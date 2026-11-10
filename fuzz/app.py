"""The app the black-box fuzzers attack: small routes that read bodies, forms, paths and queries."""

from korvex import App, Request, Response, WebSocket, WebSocketDisconnect

app = App()


@app.get("/")
async def index():
    return Response.text("ok")


@app.post("/echo")
async def echo(request: Request):
    return Response.text(str(len(await request.body())))


@app.post("/form")
async def form(request: Request):
    return Response.text(str(len(await request.form())))


@app.get("/items/{id:int}")
async def item(id: int):
    return Response.text(str(id))


@app.get("/users/{name}")
async def user(name: str, request: Request):
    return Response.text(f"{name}:{len(request.query)}")


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
