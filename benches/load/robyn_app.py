"""The load-test app, in Robyn (see korvex_app.py).

Robyn isn't ASGI: it runs its own server, written in Rust, so it's
compared with Korvex's built-in server (benches/servers.py), not in the
uvicorn load test. Run: python benches/load/robyn_app.py
"""

import os

from robyn import Robyn

app = Robyn(__file__)


async def filler(request):
    return "filler"


for i in range(1_000):
    app.get(f"/r{i % 10}/:id/detail_{i}")(filler)


@app.get("/users/:id")
async def get_user(request):
    id = int(request.path_params["id"])
    return {"id": id, "name": f"user {id}"}


if __name__ == "__main__":
    app.start(host="127.0.0.1", port=int(os.environ.get("PORT", "8080")))
