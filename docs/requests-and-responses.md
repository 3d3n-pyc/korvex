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
  `filename`, `content_type` and `data`.

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

`headers=` takes a dict, or `(name, value)` pairs to repeat a name.

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

`samesite` is `"lax"` unless you say otherwise.

### Streams and files

`Response.stream()` sends each chunk as it comes, to a slow client too:
once 1 MiB waits for it, the generator waits. `Response.file()` reads the
file in chunks, without blocking the server, and sets its type and
length.
