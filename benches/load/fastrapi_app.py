"""The load-test app, in FastrAPI (see korvex_app.py).

FastrAPI copies FastAPI's API, but isn't ASGI: it runs its own server,
written in Rust (Axum), so it's compared with Korvex's built-in server
(benches/servers.py), not in the uvicorn load test.
Run: python benches/load/fastrapi_app.py

Its handlers are sync: in FastrAPI 0.2.11, an async handler answers a 500
("no running event loop"), on Python 3.13 and 3.14 alike. It serves from
one process: it has no workers option, and a second process can't share
its port.
"""

import os

from fastrapi import FastrAPI

app = FastrAPI()


def filler(id: int):
    return "filler"


def get_user(id: int):
    return {"id": id, "name": f"user {id}"}


for i in range(1_000):
    app.get(f"/r{i % 10}/{{id}}/detail_{i}")(filler)
app.get("/users/{id}")(get_user)


if __name__ == "__main__":
    app.serve("127.0.0.1", int(os.environ.get("PORT", "8080")))
