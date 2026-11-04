"""The load-test app, in Sanic, served over ASGI (see korvex_app.py)."""

from sanic import Sanic
from sanic.response import json, text

app = Sanic("load_test")


async def filler(request, id: int):
    return text("filler")


for i in range(1_000):
    app.add_route(filler, f"/r{i % 10}/<id:int>/detail_{i}", name=f"filler_{i}")


@app.get("/users/<id:int>")
async def get_user(request, id: int):
    return json({"id": id, "name": f"user {id}"})
