# Routes

Use this when you declare which URLs your app answers, and with which handler.

```python
from korvex import App

app = App()


@app.get("/users/{id:int}")
def get_user(id: int):
    ...


@app.post("/users")
def create_user():
    ...
```

`@app.get`, `post`, `put`, `patch`, `delete`, `head` and `options` declare a
[route](glossary.md#route) for one method. A function can be sync or async.

!!! note
    A plain `def` [handler](glossary.md#handler) runs on the
    [event loop](glossary.md#event-loop), so a blocking call in it (a
    synchronous database driver, `requests`, `time.sleep`) holds up every
    other request of the [worker](glossary.md#worker). Prefer `async def`
    with async libraries, or `await asyncio.to_thread(...)` for the
    blocking part.

## Path parameters

A `{name}` segment is a [path parameter](glossary.md#path-parameter): it
captures part of the path, and the handler gets it by
name:

| Segment       | Matches                                | Gives      |
|---------------|----------------------------------------|------------|
| `users`       | exactly `users`                        |            |
| `{id}`        | any one segment                        | `str`      |
| `{id:int}`    | an integer (`42`, `-7`)                | `int`      |
| `{id:uuid}`   | a UUID                                 | `UUID`     |
| `{post:slug}` | what a constraint you defined allows   | `str`      |
| `*path`       | the rest of the path (last segment)    | `str`      |

A path that doesn't fit gets a 404. When several routes could match, the
most precise wins: a literal segment, then constrained parameters, then
`{id}`, then `*path`.

An annotation converts a value further: `def on_day(day: date)` for
`/days/{day}` gets a `date`, and a value that isn't one gets a 422.

### Your own constraints

```python
from korvex import App

app = App()
app.constraint("slug", r"[a-z0-9]+(?:-[a-z0-9]+)*")


@app.get("/posts/{post:slug}")  # /posts/hello-world, not /posts/Hello
def show_post(post: str): ...
```

The whole segment must match the regex. Define a constraint before the
routes that use it. Regexes are matched in Rust, in linear time (no
look-around or backreferences).

## Groups

A group shares a prefix, and [dependencies](dependencies.md), between
routes:

```python
from korvex import App, Depends

app = App()


def signed_in(): ...  # your check; raise HTTPException(401) to refuse


api = app.group("/api", dependencies=[Depends(signed_in)])


@api.get("/tasks")  # GET /api/tasks, after signed_in
def list_tasks(): ...


admin = api.group("/admin")  # /api/admin, signed_in too
```

A group also takes `tags=`, `deprecated=`, `responses=` and
`include_in_schema=` for [OpenAPI](openapi.md); a route's own add to them.

## Hosts

A host group answers only for one host name:

```python
from korvex import App

app = App()
api = app.host("api.example.com")
tenants = app.host("{tenant}.example.com")


@tenants.get("/")  # acme.example.com: tenant="acme"
def home(tenant: str): ...
```

- `{tenant}` takes one whole label of the host name, and reaches the
  handlers and dependencies that ask for it, like a path parameter.
- The first host pattern that fits owns the request: its routes answer,
  then the routes declared without a host. Patterns without a parameter
  are tried first.

## Links to a route

```python
from korvex import App, Request, Response

app = App()


@app.get("/users/{id:int}")
def get_user(id: int): ...


@app.get("/profile")
def profile(request: Request):
    return Response.text(request.url_for("get_user", id=42))  # https://example.com/users/42


app.url_for("get_user", id=42)  # "/users/42"
```

- A route is known by its function's name, or `name=` on its decorator:
  `@app.get("/users/{id:int}", name="user")`.
- Values are encoded and checked: one that doesn't fit the route
  (`id="abc"`) raises an error.
- `request.url_for()` gives the full URL, with the route's own host for a
  host route. It uses the request's `Host` header, which the client
  sends: for a link in an email, build it from your own base URL and
  `app.url_for()`.
- Two routes can share a function's name; `url_for()` then asks you for a
  `name=` to tell them apart.

## Static files

```python
from korvex import App

app = App()
app.static("/static", "public")  # /static/css/app.css: public/css/app.css
app.url_for("static", path="css/app.css")
```

A path that leaves the directory gets a 404. With several directories,
give each a `name=`.

Files are served as conditional requests:

- Every file gets an [`ETag`](glossary.md#etag) and a `Last-Modified`. A request that sends
  `If-None-Match` or `If-Modified-Since` for an unchanged file gets a 304,
  with no body.
- [`Range: bytes=...`](glossary.md#range-request) (one range) gets a 206 with that part of the file,
  for resumable downloads and for seeking in a video. A range past the end
  of the file gets a 416. `If-Range` is honored; several ranges at once
  get the whole file.
- A URL without a hash is sent with `Cache-Control: no-cache`: the browser
  asks each time, and an unchanged file costs a cheap 304. A hashed URL
  (`app.static_url("css/app.css")`) stays `public, max-age=31536000,
  immutable`.

In your own handler, `Response.file(path, request=request)` does the same:
see [Streams and files](requests-and-responses.md#streams-and-files).

## Mounting another app

```python
from korvex import App, Request, Response

admin_app = App()  # or a Starlette or FastAPI app, or Django's ASGI app
app = App()
app.mount("/admin", admin_app, name="admin")

app.url_for("admin")  # "/admin/"
app.url_for("admin", path="users")  # "/admin/users"


@app.get("/link")
def link(request: Request):
    return Response.text(request.url_for("admin", path="users"))  # https://example.com/admin/users
```

`mount(prefix, asgi_app)` hands every request under `prefix` to any
[ASGI](glossary.md#asgi) app: a Korvex `App`, a Starlette or FastAPI app, Django's ASGI app, or a
plain `async def (scope, receive, send)`.

- The app gets the request with [`root_path`](glossary.md#root_path) set to the prefix (plus the
  server's own `root_path`, if any). `path` still holds the whole path:
  ASGI says an app strips its `root_path` itself, as a Korvex `App` does,
  so `/admin/users` is `/users` for `admin_app`.
- `/admin` answers 308 to `/admin/`, keeping the query string. The prefix
  starts with `/`, is not `/` itself and holds no `{}` or `*`.
- A route of the parent app wins: with `@app.get("/admin/health")`, that
  route answers and the rest of `/admin/...` goes to the mount.
- The mounted app's [lifespan](glossary.md#lifespan) runs with the parent's startup and shutdown,
  and the state it sets reaches its requests. If its startup fails, the
  parent's does.
- On the built-in server, a mounted app that waits for the client to leave
  (`http.disconnect`) learns it when its response ends; for long polling,
  stream the response.
- [WebSockets](glossary.md#websocket) under the prefix go to the mounted app too.
- The mount is not in the parent's [OpenAPI](openapi.md) document.
- `name=` is for `url_for()`: with `path=`, a value that is empty, `.` or
  `..` in a part raises an error. Other parameters are refused.

!!! warning
    The parent's before [hooks](glossary.md#hook) run before the mounted app, so
    [`csrf()`](glossary.md#csrf), `rate_limit()` and
    [`sessions()`](glossary.md#session) protect it
    (see [Middleware](middleware.md)).
    Its after hooks and exception handlers do not run: [`cors()`](glossary.md#cors),
    `security_headers()`, `compress()` and the request ID header don't
    apply to the mounted app. Configure them in the mounted app. Only the
    built-in server's own maximum body size applies, not a route's
    `max_body=`.

## HEAD, 404 and 405

- A `HEAD` request is answered by the `GET` route, without the body.
- An unknown path gets a 404; a known path with the wrong method a 405,
  with an `Allow` header.

## Route options

Every decorator also takes:

- `name=`: for [`url_for()`](#links-to-a-route).
- `dependencies=[...]`: see [Dependencies](dependencies.md).
- `status=`, `tags=`, `deprecated=`, `responses=`, `include_in_schema=`:
  see [OpenAPI](openapi.md).
- `max_body=`: the largest body the [built-in server](server.md) accepts
  for this route.

Next: [Validation](validation.md).
