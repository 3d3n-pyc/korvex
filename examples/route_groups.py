"""Level 2: route groups.

A group registers routes under a shared prefix and middleware. Groups can
be nested; the outer group's middleware comes first.

Run: python examples/route_groups.py
"""

from korvex import Router

router = Router()

api = router.group("/api/v1", middleware=["auth"])
api.get("/users/{id:int}", "get_user")
api.post("/users", "create_user", middleware=["rate_limit"])

admin = api.group("/admin", middleware=["require_admin"])
admin.get("/stats", "get_stats")

if __name__ == "__main__":
    for path, method, handler, middleware in router.routes():
        print(f"{method:6} {path:24} -> {handler:12} {middleware}")
    # GET    /api/v1/admin/stats      -> get_stats    ('auth', 'require_admin')
    # POST   /api/v1/users            -> create_user  ('auth', 'rate_limit')
    # GET    /api/v1/users/{id:int}   -> get_user     ('auth',)

    handler, params, middleware = router.match("/api/v1/users/42")
    print(handler, dict(params), middleware)
    # get_user {'id': 42} ('auth',)
