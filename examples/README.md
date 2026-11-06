# Examples

1. [asgi_app.py](asgi_app.py): a small web API in one file: routes, a
   JSON body, a link with `url_for()`, a route guarded by a Bearer token,
   and Swagger UI.
2. [taskboard/](taskboard/): a complete todo list, laid out like a real
   project. Several lists, due dates, drag and drop, search, keyboard
   shortcuts and live updates, over a validated JSON API with Swagger UI,
   sign-in, SQLite with migrations, and its own tests. Its README explains
   the layers and the choices behind them.

The [standalone router](../docs/router.md), on its own, without the App:

- [basic_router.py](basic_router.py): matching paths to names, and calling
  handlers by hand.
- [route_groups.py](route_groups.py): shared prefixes and middleware names.

[deploy/](deploy/) holds ready-to-copy deployment files (Dockerfile,
Caddy and nginx configurations over TCP or a Unix socket, AWS Lambda
handler), explained in the
[deployment guide](../docs/deployment.md).

From the repository root, after `uv sync`:

```sh
uv run korvex run examples.asgi_app:app          # the built-in server (Linux, macOS)
uv run --with uvicorn uvicorn examples.asgi_app:app --reload
uv run python examples/basic_router.py
uv run python examples/route_groups.py
```

The taskboard is a project of its own: see [its README](taskboard/README.md)
to run it and its tests.
