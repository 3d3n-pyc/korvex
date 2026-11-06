"""Level 1: the router on its own.

`Router` maps a path to a handler *name*. Calling the matching code is up
to you; here, a plain dict does it.

Run: python examples/basic_router.py
"""

from korvex.router import MethodNotAllowed, Router

router = Router()
router.get("/health", "health")
router.get("/users/{id:int}", "get_user")
router.post("/users", "create_user")
router.get("/static/*path", "static")

HANDLERS = {
    "health": lambda: "OK",
    "get_user": lambda id: f"user #{id}",
    "create_user": lambda: "created",
    "static": lambda path: f"serving {path}",
}


def handle(path: str, method: str = "GET") -> str:
    try:
        found = router.match(path, method)
    except MethodNotAllowed as exc:
        return f"405 Method Not Allowed (allowed: {', '.join(exc.allowed)})"
    if found is None:
        return "404 Not Found"
    handler, params, _middleware = found
    return HANDLERS[handler](**params)


if __name__ == "__main__":
    print(handle("/health"))  # OK
    print(handle("/users/42"))  # user #42
    print(handle("/users", "POST"))  # created
    print(handle("/static/css/app.css"))  # serving css/app.css
    print(handle("/unknown"))  # 404 Not Found
    print(handle("/users/42", "DELETE"))  # 405 Method Not Allowed (allowed: GET)
