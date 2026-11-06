# Routes

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
route for one method. A function can be sync or async.

## Path parameters

A `{name}` segment captures part of the path, and the handler gets it by
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
api = app.group("/api", dependencies=[Depends(signed_in)])


@api.get("/tasks")  # GET /api/tasks, after signed_in
def list_tasks(): ...


admin = api.group("/admin")  # /api/admin, signed_in too
```

## Hosts

A host group answers only for one host name:

```python
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
@app.get("/users/{id:int}")
def get_user(id: int): ...


app.url_for("get_user", id=42)  # "/users/42"
request.url_for("get_user", id=42)  # "https://example.com/users/42"
```

- A route is known by its function's name, or `name=` on its decorator:
  `@app.get("/users/{id:int}", name="user")`.
- Values are encoded, and checked: a value that doesn't fit the route
  (`id="abc"`) raises an error rather than give a link to a 404.
- `request.url_for()` gives the full URL, with the route's own host for a
  host route. It uses the request's `Host` header, which the client
  sends: for a link in an email, build it from your own base URL and
  `app.url_for()`.
- Two routes can share a function's name; `url_for()` then asks you for a
  `name=` to tell them apart.

## Static files

```python
app.static("/static", "public")  # /static/css/app.css: public/css/app.css
app.url_for("static", path="css/app.css")
```

A path that leaves the directory gets a 404. With several directories,
give each a `name=`.

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
