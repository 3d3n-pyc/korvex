# Examples

Three levels of the same API, from the bare router up to a running server.
Read them in this order:

1. **[basic_router.py](basic_router.py)** — `Router` alone: register routes,
   match a path, dispatch by hand. The lowest level; everything else is
   built on top of this.
2. **[route_groups.py](route_groups.py)** — `RouteGroup`: shared prefixes and
   middleware, nested groups, and `router.routes()` for introspection.
3. **[asgi_app.py](asgi_app.py)** — `App`: a real, runnable web API served by
   any ASGI server (uvicorn, hypercorn, ...).

## Running

From the repository root, with Korvex installed in the environment
(`uv run maturin develop --release` if you haven't already):

```sh
uv run python examples/basic_router.py
uv run python examples/route_groups.py

uv add uvicorn  # only needed for this one
uv run uvicorn examples.asgi_app:app --reload
```
