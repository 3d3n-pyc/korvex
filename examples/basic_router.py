"""Level 1: just the router — register routes, match a path by hand.

This is Korvex at its lowest level: `Router` only ever deals in opaque
strings (a path pattern in, a handler name + params out). It never calls
anything itself — dispatching `handler_name` to real code is entirely up
to you, as shown in `handle()` below. See `route_groups.py` for shared
prefixes/middleware, and `asgi_app.py` for a version of this same
dispatch loop that Korvex does for you, wired up to a real server.

Run: python examples/basic_router.py
"""

from korvex import MethodNotAllowed, Router

router = Router()
router.get("/health", "health_check")
router.get("/users/{id:int}", "get_user")
router.post("/users", "create_user")
router.get("/static/*filepath", "serve_static")

# The registry mapping each opaque handler name to real code — this is
# the part Korvex intentionally has no opinion about.
HANDLERS = {
    "health_check": lambda: "OK",
    "get_user": lambda id: f"user #{id}",
    "create_user": lambda: "created",
    "serve_static": lambda filepath: f"serving {filepath}",
}


def handle(path: str, method: str = "GET") -> str:
    try:
        result = router.match_route(path, method=method)
    except MethodNotAllowed as exc:
        return f"405 Method Not Allowed (allowed: {', '.join(exc.args[0])})"
    if result is None:
        return "404 Not Found"
    handler_name, params, _middleware = result
    return HANDLERS[handler_name](**params)


if __name__ == "__main__":
    print(handle("/health"))
    print(handle("/users/42"))
    print(handle("/users", method="POST"))
    print(handle("/static/css/app.css"))
    print(handle("/unknown"))
    print(handle("/users/42", method="DELETE"))
