# Dependencies

A dependency is a function whose result a handler receives. Put code
shared by routes there: who is signed in, a database connection, a
permission check.

```python
from typing import Annotated, Optional

from korvex import Cookie, Depends, HTTPException


async def current_user(session: Optional[str] = Cookie(None)) -> User:
    user = await users.by_session(session)
    if user is None:
        raise HTTPException(401, "sign in first")  # the request stops here
    return user


@app.get("/me")
def me(user: User = Depends(current_user)) -> User:
    return user
```

- A dependency takes parameters like a handler: query, headers, cookies,
  body, path, the request, and other dependencies. It can be sync or
  async.
- It runs once per request, however many parameters ask for it.
  `Depends(func, cache=False)` runs it each time.
- `Annotated` names it once for all uses:
  `CurrentUser = Annotated[User, Depends(current_user)]`, then
  `def me(user: CurrentUser)`.

## Guarding routes

To check without using the result, list it in `dependencies=`, on a
route, a group or a WebSocket:

```python
api = app.group("/api", dependencies=[Depends(current_user)])  # every route of /api
```

A group's dependencies run before its routes' own.

## Setup and teardown

A dependency that `yield`s is torn down after the handler:

```python
async def database():
    connection = await pool.acquire()
    try:
        yield connection  # the handler runs here
    finally:
        await pool.release(connection)


@app.get("/tasks")
async def tasks(user: CurrentUser, db=Depends(database)) -> list[Task]:
    return await db.tasks_of(user.id)
```

- For a streamed response, the teardown waits until the stream is sent.
- An error from the handler is raised at the `yield`, so `except` and
  `finally` work there. The error still gets its usual response.

## In tests

```python
app.dependency_overrides[current_user] = lambda: User(id=1, name="test")
```

replaces a dependency everywhere. See [Testing](testing.md).

## Errors

Validation errors from dependencies and from the handler come together
in one 422. A dependency can also `raise Abort(response)` to send that
exact response: a redirect to the sign-in page, say. See
[Errors and hooks](errors-and-hooks.md).
