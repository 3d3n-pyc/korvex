"""The load-test app, in Korvex. Every file in this folder is the same app:
1,000 filler routes plus GET /users/{id:int} returning a small JSON."""

import asyncio
import os

from korvex import App, Response

# AWAIT=1 makes the handler really wait once, like a database call would.
AWAIT = os.environ.get("AWAIT") == "1"

app = App()


async def filler(request, id):
    return Response.text("filler")


for i in range(1_000):
    app.get(f"/r{i % 10}/{{id:int}}/detail_{i}")(filler)


@app.get("/users/{id:int}")
async def get_user(request, id):
    if AWAIT:
        await asyncio.sleep(0)
    return Response.json({"id": id, "name": f"user {id}"})
