"""Level 3: a web API served over ASGI.

`App` does what `basic_router.py` does by hand: match the request, run
the route's checks, call the handler, send the response.

Run:
    pip install uvicorn
    uvicorn examples.asgi_app:app --reload

Then:
    curl http://127.0.0.1:8000/health
    curl http://127.0.0.1:8000/users/42
    curl -i -X POST http://127.0.0.1:8000/users -d '{"name": "Ada"}'    # Location: /users/43
    curl http://127.0.0.1:8000/secret                                   # 401
    curl -H "Authorization: Bearer token" http://127.0.0.1:8000/secret  # 200

and http://127.0.0.1:8000/docs, where "Authorize" takes the token.
"""

from __future__ import annotations

from korvex import App, Depends, HTTPException, Request, Response
from korvex.security import Bearer

app = App()
app.docs(title="Example API")
bearer = Bearer()  # reads `Authorization: Bearer <token>`, and shows in /docs


@app.get("/health")
def health(request: Request) -> Response:
    return Response.text("ok")


@app.get("/users/{id:int}")
def get_user(request: Request, id: int) -> Response:
    return Response.json({"id": id})


@app.post("/users")
async def create_user(request: Request) -> Response:
    data = await request.json()
    # The new user's URL, built from the route's name rather than written again.
    location = app.url_for("get_user", id=43)
    return Response.json({"id": 43, **data}, status=201, headers=[("Location", location)])


def auth(token: str = Depends(bearer)) -> None:
    """A dependency: runs before the handler, and stops the request if
    the token is wrong. Without a token at all, `bearer` answers 401."""
    if token != "token":
        raise HTTPException(401, "Unauthorized", headers=[("WWW-Authenticate", "Bearer")])


@app.get("/secret", dependencies=[Depends(auth)], responses={401: "Wrong or missing token"})
def secret(request: Request) -> Response:
    return Response.text("top secret")
