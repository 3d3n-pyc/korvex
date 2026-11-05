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


@app.get("/slow")
async def slow(request):
    await asyncio.sleep(1)
    return Response.text("slow done")


@app.get("/pid")
def pid(request):
    return Response.text(str(os.getpid()))


@app.get("/crash")
def crash(request):
    os._exit(1)  # a worker dying abruptly, as if it segfaulted


@app.post("/upload", max_body=5_000_000)
async def upload(request):
    return Response.text(str(len(await request.body())))


@app.group("/api").post("/import", max_body=5_000_000)
async def import_data(request):
    return Response.text(str(len(await request.body())))


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
        graceful_timeout=float(os.environ.get("GRACE", "30")),
        max_body=int(os.environ.get("MAX_BODY", str(1024 * 1024))),
        timeout=float(os.environ.get("TIMEOUT", "30")),
    )
