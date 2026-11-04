"""The load-test app, in Starlette (see korvex_app.py)."""

import os

from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

# Starlette, FastAPI and Django try routes one by one, so the order matters:
# ROUTE_FIRST=1 declares /users/{id} before the 1,000 others (best case)
# instead of after them (worst case).
ROUTE_FIRST = os.environ.get("ROUTE_FIRST") == "1"


async def filler(request):
    return PlainTextResponse("filler")


async def get_user(request):
    id = request.path_params["id"]
    return JSONResponse({"id": id, "name": f"user {id}"})


fillers = [Route(f"/r{i % 10}/{{id:int}}/detail_{i}", filler) for i in range(1_000)]
target = Route("/users/{id:int}", get_user)
app = Starlette(routes=[target, *fillers] if ROUTE_FIRST else [*fillers, target])
