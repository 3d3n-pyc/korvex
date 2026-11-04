"""Level 3: a web API served over ASGI.

`App` does what `basic_router.py` does by hand: match the request, run
the middleware, call the handler, send the response.

Run:
    pip install uvicorn
    uvicorn examples.asgi_app:app --reload

Then:
    curl http://127.0.0.1:8000/health
    curl http://127.0.0.1:8000/users/42
    curl -X POST http://127.0.0.1:8000/users -d '{"name": "Ada"}'
    curl http://127.0.0.1:8000/secret                                   # 401
    curl -H "Authorization: Bearer token" http://127.0.0.1:8000/secret  # 200
"""

from __future__ import annotations

from korvex import App, Request, Response

app = App()


@app.get("/health")
def health(request: Request) -> Response:
    return Response.text("ok")


@app.get("/users/{id:int}")
def get_user(request: Request, id: str) -> Response:
    return Response.json({"id": id})


@app.post("/users")
async def create_user(request: Request) -> Response:
    data = await request.json()
    return Response.json({"created": data}, status=201)


@app.get("/secret", middleware=["auth"])
def secret(request: Request) -> Response:
    return Response.text("top secret")


@app.middleware("auth")
def auth(request: Request) -> Response | None:
    if request.headers.get("Authorization") != "Bearer token":
        return Response.text("Unauthorized", status=401)
    return None  # carry on to the handler
