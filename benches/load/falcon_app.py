"""The load-test app, in Falcon, served over ASGI (see korvex_app.py)."""

import os

import falcon
import falcon.asgi

ROUTE_FIRST = os.environ.get("ROUTE_FIRST") == "1"  # see starlette_app.py


class Filler:
    async def on_get(self, req, resp, id):
        resp.content_type = falcon.MEDIA_TEXT
        resp.text = "filler"


class User:
    async def on_get(self, req, resp, id):
        resp.media = {"id": id, "name": f"user {id}"}


app = falcon.asgi.App()
if ROUTE_FIRST:
    app.add_route("/users/{id:int}", User())
filler = Filler()
for i in range(1_000):
    app.add_route(f"/r{i % 10}/{{id:int}}/detail_{i}", filler)
if not ROUTE_FIRST:
    app.add_route("/users/{id:int}", User())
