# OpenAPI and security

```python
app.docs(title="Tasks", version="1.0")  # /openapi.json and Swagger UI at /docs
```

The document is built from the routes: paths, typed parameters, bodies,
returned models, and each handler's docstring (its first line is the
summary). `app.openapi()` gives it as a dict. Swagger UI comes from a CDN;
`docs_path=None` leaves it out.

## What else to say about a route

```python
api = app.group("/api", tags=["tasks"], responses={401: "Not signed in"})


@api.post("/tasks", status=201, responses={409: ("Title taken", Conflict)})
def create_task(body: NewTask) -> Task: ...


@api.delete("/tasks/{id:uuid}", status=204, responses={404: "No such task"})
def delete_task(id: UUID) -> None: ...


@app.get("/v1/tasks", deprecated=True)
def old_tasks() -> list[Task]: ...


@app.get("/health", include_in_schema=False)
def health(): ...
```

- `status`: the success, and the status of what the handler returns (a
  model, or `None` for an empty response). A returned `Response` keeps
  its own.
- `responses`: other answers, by status: a description, a model, or both
  (`(description, model)`). `"4XX"` and `"default"` work too.
- `tags`, `deprecated`, `include_in_schema`: as they say.
- On a group or a host, they apply to its routes: tags add up, and a
  route's responses replace the group's for the same status.

## Signing in from Swagger UI

`korvex.security` reads a credential from the request, refuses the
request without one (401), and tells OpenAPI, so Swagger UI's
"Authorize" button sends it:

```python
from korvex.security import Bearer

bearer = Bearer()  # Authorization: Bearer <token>


async def current_user(token: str = Depends(bearer)) -> User:
    user = await users.by_token(token)
    if user is None:
        raise HTTPException(401, "invalid token", headers={"WWW-Authenticate": "Bearer"})
    return user
```

- `Bearer(format="JWT")`, `APIKey(header="X-API-Key")` (or `query=`,
  `cookie=`) and `OAuth2Password(token_url="/token", scopes={...})`.
- A scheme only reads the credential; checking it is the job of the
  dependency that takes it, like `current_user` above.
- `required=False` gives `None` instead of a 401.
- Every route that depends on a scheme, directly, through another
  dependency or through its group, lists it.
