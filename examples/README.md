# Examples

Read them in order, each builds on the previous one:

1. [basic_router.py](basic_router.py): the router alone, calling handlers by hand.
2. [route_groups.py](route_groups.py): shared prefixes and middleware.
3. [asgi_app.py](asgi_app.py): a real web API served with uvicorn.

[deploy/](deploy/) holds ready-to-copy deployment files (Dockerfile,
Caddy and nginx configurations over TCP or a Unix socket, AWS Lambda
handler), explained in the
[deployment guide](../docs/deployment.md).

From the repository root, after `uv run maturin develop --release`:

```sh
uv run python examples/basic_router.py
uv run python examples/route_groups.py
uv run --with uvicorn uvicorn examples.asgi_app:app --reload
```
