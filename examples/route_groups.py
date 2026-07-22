"""Level 2: RouteGroup — shared path prefixes and middleware, nested
groups, and introspecting everything that got registered.

`router.group(prefix, middleware=...)` returns a `RouteGroup`: a small,
chainable, pure-Python builder over the same `Router` — it never touches
the matching engine, it just resolves full paths and concatenated
middleware lists before calling `router.add_route` for you.

Run: python examples/route_groups.py
"""

from korvex import Router

router = Router()

api = router.group("/api/v1", middleware=["auth"])
api.get("/users/{id:int}", "get_user")
api.post("/users", "create_user", middleware=["rate_limit"])  # runs after "auth"

admin = api.group("/admin", middleware=["require_admin"])
admin.get("/stats", "get_stats")

if __name__ == "__main__":
    print("Registered routes:")
    for path, method, handler, middleware in router.routes():
        print(f"  {method:6} {path:30} -> {handler:12} middleware={middleware}")

    print()
    print(router.match_route("/api/v1/users/42"))
    # ("get_user", {"id": "42"}, ["auth"])

    print(router.match_route("/api/v1/admin/stats"))
    # ("get_stats", {}, ["auth", "require_admin"])
