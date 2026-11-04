"""The load-test app, in BlackSheep (see korvex_app.py)."""

import os

from blacksheep import Application, json, text

ROUTE_FIRST = os.environ.get("ROUTE_FIRST") == "1"  # see starlette_app.py

app = Application()


async def filler(id: int):
    return text("filler")


async def get_user(id: int):
    return json({"id": id, "name": f"user {id}"})


if ROUTE_FIRST:
    app.router.add_get("/users/{int:id}", get_user)
for i in range(1_000):
    app.router.add_get(f"/r{i % 10}/{{int:id}}/detail_{i}", filler)
if not ROUTE_FIRST:
    app.router.add_get("/users/{int:id}", get_user)
