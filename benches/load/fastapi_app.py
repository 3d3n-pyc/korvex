"""The load-test app, in FastAPI (see korvex_app.py)."""

import os

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

# Starlette, FastAPI and Django try routes one by one, so the order matters:
# ROUTE_FIRST=1 declares /users/{id} before the 1,000 others (best case)
# instead of after them (worst case).
ROUTE_FIRST = os.environ.get("ROUTE_FIRST") == "1"

app = FastAPI()


async def filler(id: int):
    return PlainTextResponse("filler")


async def get_user(id: int):
    return {"id": id, "name": f"user {id}"}


if ROUTE_FIRST:
    app.get("/users/{id}")(get_user)
for i in range(1_000):
    app.get(f"/r{i % 10}/{{id}}/detail_{i}")(filler)
if not ROUTE_FIRST:
    app.get("/users/{id}")(get_user)
