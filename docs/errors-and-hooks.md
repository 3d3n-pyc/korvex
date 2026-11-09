# Errors and hooks

## Errors

```python
from korvex import HTTPException

raise HTTPException(404, "no such task")  # {"detail": "no such task"}, a 404
```

- Raise it in a handler, a dependency or a before hook. Without a
  message, the standard one is used (`"Not Found"`). `headers=` adds
  headers: `{"WWW-Authenticate": "Bearer"}`.
- Every error Korvex answers is JSON with a `detail`: these, routing's 404
  and 405, validation's 422.
- Any other exception gives a 500, `{"detail": "Internal Server Error"}`.
  The error itself is logged to the `korvex` logger, never sent.

`Abort` sends an exact response instead, past the exception handlers:

```python
from korvex import Abort, Response


def signed_in(session: Optional[str] = Cookie(None)) -> None:
    if session is None:
        raise Abort(Response.redirect("/login"))
```

After hooks still see that response. In a WebSocket's dependency, `Abort`
refuses the connection (a 403).

### Answering errors your way

```python
@app.exception_handler(404)  # a status
def not_found(request, exc):
    return Response.text("Nothing here", status=404)


@app.exception_handler(ValueError)  # an exception class, and its subclasses
def bad_value(request, exc: ValueError):
    return Response.json({"detail": str(exc)}, status=400)
```

## Hooks around every request

```python
@app.before_request
def block_bots(connection):
    if "bot" in (connection.headers.get("user-agent") or ""):
        return Response.text("no bots", status=403)  # answers right away
    # returning None carries on


@app.after_request
def add_header(request, response):
    response.headers.set("X-Served-By", "korvex")  # change it, or return another
```

- Before hooks run before routing, so for unknown paths too (and with
  `path_params` still empty), in the order declared. They also run for WebSocket handshakes: they get a
  `Connection`, and `isinstance(connection, WebSocket)` tells a
  handshake apart. Answering refuses the connection.
- After hooks get every response, errors included, and run in reverse
  order, like layers.
- Both can be sync or async. Ready-made ones are in
  [korvex.middleware](middleware.md).

## Startup and shutdown

```python
@app.on_startup
async def connect():
    app.local.pool = await asyncpg.create_pool(DATABASE_URL)


@app.on_shutdown
async def disconnect():
    await app.local.pool.close()
```

- Startup hooks run before the first request; if one raises, the server
  doesn't start. Shutdown hooks run when it stops, last declared first.
- `app.local` keeps attributes per event loop: with the built-in server's
  threads, each loop gets its own pool, as asyncio needs. Use it instead
  of module globals for what a startup hook opens, and read it from
  handlers and dependencies:

```python
async def db():  # a dependency
    async with app.local.pool.acquire() as connection:
        yield connection
```
