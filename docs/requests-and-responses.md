# Requests and responses

## What a handler gets

A handler's parameters say what it needs. Most handlers never touch the
request itself:

```python
@app.get("/tasks/{id:int}")
def get_task(id: int, verbose: bool = False) -> Task: ...  # path, then query string
```

When it does need it, it asks for it: a parameter named `request`, or
annotated `Request`.

```python
from korvex import Request


@app.post("/echo")
async def echo(request: Request):
    return Response(await request.body())
```

[Validation](validation.md) lists everything a parameter can come from.

## The request

| Attribute | |
|---|---|
| `method`, `path`, `scheme` | `"GET"`, `"/tasks/7"`, `"https"` |
| `headers` | `headers.get("accept")` the first value, `headers.get_all("accept")` every one; names in any case |
| `query` | the query string: `query.get("page")`, `query.get_all("tag")` |
| `cookies` | a dict |
| `path_params` | a dict |
| `client` | `(host, port)` of the client (the real one behind a trusted proxy), or `None` |
| `app` | the `App` |

And three methods to read the body:

- `await request.body()`: the bytes.
- `await request.json()`: parsed JSON (a 400 if it isn't).
- `await request.form()`: a form, urlencoded or multipart, read like the
  query (`form.get("title")`). Files are `UploadedFile`s, with
  `filename`, `content_type` and `data`. A body that isn't a form gets a
  415; text that can't be decoded, a 400.

A `WebSocket` has the same attributes but no body: both are a
`Connection`.

## Responses

```python
from korvex import Response

Response("raw bytes or text", status=200, headers={"X-Total": "3"})
Response.text("hello")
Response.json({"id": 1})
Response.redirect("/login", status=303)
Response.stream(chunks)  # a generator, sync or async, of bytes or str
Response.file("report.pdf", filename="report.pdf")  # read in chunks; `filename` makes it a download
```

A handler can also return a model (`-> Task`, sent as JSON) or `None`: see
[Validation](validation.md#what-the-handler-returns).

`headers=` takes a dict, or `(name, value)` pairs to repeat a name. A
`Content-Type` given there wins over the one `text()` or `json()` sets.

`response.headers` is a `Headers`, the same as `request.headers`: names
in any case, and a name can repeat (several `Set-Cookie`).

| | |
|---|---|
| `get(name)`, `headers[name]` | the first value (`[]` raises `KeyError` if missing) |
| `get_all(name)` | every value |
| `set(name, value)`, `headers[name] = value` | replaces every value |
| `add(name, value)` | adds one more |
| `remove(name)` | removes them all |
| `name in headers`, `for name, value in headers` | test, and go through the pairs |

### Cookies

```python
response = Response.redirect("/")
response.set_cookie("session", token, max_age=3600, httponly=True, secure=True)
response.delete_cookie("theme")
```

`samesite` is `"lax"` unless you say otherwise. Without `max_age`, the
cookie lasts until the browser closes.

### Streams and files

`Response.stream()` sends each chunk as it comes, to a slow client too:
once 1 MiB waits for it, the generator waits. `Response.file()` reads the
file in chunks, without blocking the server, and sets its type and
length.

### Server-Sent Events

```python
@app.get("/feed")
async def feed(last_event_id: int = Header(0)):
    async def events():
        try:
            for n in range(last_event_id + 1, 1000):
                yield ServerSentEvent({"n": n}, event="tick", id=str(n))
                await asyncio.sleep(1)
        finally:
            ...  # runs when the client leaves
    return Response.sse(events(), ping=15.0)
```

A `str` is sent as is, anything else as JSON. In an async generator, a
`: ping` comment goes out after `ping` seconds of silence (15 by default,
`ping=0` turns it off); a sync generator gets none. The response sets
`Content-Type: text/event-stream`, `Cache-Control: no-cache` and
`X-Accel-Buffering: no` (for nginx), and `compress()` leaves it alone.
When the client leaves, the generator is closed and its `finally` runs.
A browser that reconnects sends the last `id` in `Last-Event-ID`; read it
with `Header()`. A newline in `event` or `id` raises `ValueError`.

The event is encoded when you create it, so a bad value fails there:
`bytes` data raises `TypeError`; a lone surrogate or `NaN` raises
`ValueError`.

On the built-in server, a client that left is noticed at the next write.
Keep `ping` on (15 s by default): the generator is closed within `ping`
seconds. With `ping=0` or a sync generator, only when the next event is
sent. Under ASGI servers it is noticed right away.

## Background tasks

Ask for `BackgroundTasks` to run work after the response is sent:

```python
from korvex import BackgroundTasks


@app.post("/signup", status=201)
def signup(body: NewUser, tasks: BackgroundTasks) -> User:
    user = create_user(body)
    tasks.add(send_welcome_email, user.email, name=user.name)  # blocking smtplib
    return user
```

A handler and its dependencies share one object. Tasks run in the order
added, after the response, the after-hooks, a stream's end and dependency
teardown; also if the client left.

- A coroutine function is awaited; a plain function runs in a thread.
- An error is logged on the `"korvex"` logger, the next task still runs, the
  client never sees it. `TestClient` raises it (`raise_server_errors=False`
  gives just the response) and waits for the tasks, so a test can check their
  effect right away.
- Tasks run only if the handler returned, not after an `HTTPException`
  or a 500.
- There is no limit on how many run at once: for heavy or many tasks, use
  a job queue.
- A task should not block forever: shutdown cuts it off.
- A WebSocket route cannot ask for it: `TypeError` when declared.
- On shutdown the built-in server waits for running tasks up to
  `graceful_timeout`, then cuts them off with a warning.

Tasks run in the worker's process: for work that must survive a restart or
needs retries, use a real queue.
