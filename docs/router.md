# The standalone router

`korvex.router` is Korvex's Rust router on its own. It maps a path and a
method to a handler **name**, and never calls your code: for building
something of your own on top. An app doesn't need it; `App` uses it
inside.

```python
from korvex.router import Router

router = Router()
router.get("/users/{id:int}", "get_user")
router.post("/users", "create_user", middleware=["auth"])

router.match("/users/42")  # ("get_user", {"id": 42}, ())
router.match("/users", "POST")  # ("create_user", {}, ("auth",))
router.match("/nope")  # None
router.match("/users", "DELETE")  # raises MethodNotAllowed, .allowed == ["POST"]
```

The path syntax is the App's: see [Routes](routes.md#path-parameters).

| Method | Does |
|---|---|
| `route(path, handler, *, method="GET", middleware=None)` | Registers a route, or replaces it. |
| `get`, `post`, `put`, `patch`, `delete`, `head`, `options` | Shortcuts for `route`. |
| `match(path, method="GET")` | `(handler, params, middleware)`, `None`, or raises `MethodNotAllowed`. |
| `remove(path, method="GET")` | Removes a route; returns whether it was there. |
| `routes()` | Every route, as `(path, method, handler, middleware)`. |
| `group(prefix, *, middleware=None)` | A `RouteGroup`, below. |
| `constraint(name, regex)`, `constraints()` | Your own parameter types: `{post:slug}`. |

```python
api = router.group("/api", middleware=["auth"])
api.get("/users", "list_users")  # GET /api/users, middleware ("auth",)
```

- `middleware` names are given back by `match()`, for your code to run.
- `match()` keeps the last 1,024 lookups in a cache; adding or removing a
  route clears it. Its results are read-only, since they're shared.
- An invalid path or method raises `ValueError`.
