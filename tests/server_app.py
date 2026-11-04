"""The app served by test_server.py through Korvex's built-in server.

Run: python tests/server_app.py PORT
"""

import asyncio
import os
import sys
from pathlib import Path

from korvex import App, HTTPException, Response

app = App()
if os.environ.get("STATIC_DIR"):
    app.static("/static", Path(os.environ["STATIC_DIR"]))


@app.on_startup
def startup():
    if os.environ.get("FAIL_STARTUP"):
        raise ConnectionError("database unreachable")
    print("STARTED", flush=True)


@app.on_shutdown
def shutdown():
    print("STOPPED", flush=True)


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


@app.get("/fail")
def fail(request):
    raise RuntimeError("boom")


@app.get("/teapot")
def teapot(request):
    raise HTTPException(418)


if __name__ == "__main__":
    app.run(port=int(sys.argv[1]))
