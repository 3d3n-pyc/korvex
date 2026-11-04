"""Benchmark: route lookup in korvex vs other Python routers.

Every router gets the same routes and the same lookups. Before timing,
each lookup is checked to find the right handler, so no router is timed
on a failure path.

Korvex, Sanic and Litestar keep an LRU cache of past lookups. The
benchmark replays the same 200 paths, so with the cache they would only
be timed on cache hits. Their plain names below bypass the cache to time
the actual matching; the `(cached)` variants keep it.

Run:
    uv run pytest benches/bench_router.py --benchmark-only
    uv run pytest benches/bench_router.py --benchmark-only --benchmark-json=benches/results.json
    uv run python benches/plot.py   # draws docs/benchmark-{light,dark}.svg

To add a router: write `build(routes) -> router` and
`lookup(router, path) -> handler name`, then add them to ROUTERS.
"""

import itertools
import random
import re

import django
import pytest
from django.conf import settings as django_settings

if not django_settings.configured:
    django_settings.configure(DEBUG=False, ALLOWED_HOSTS=["*"])
    django.setup()

from django.urls import path as django_path  # noqa: E402
from django.urls.resolvers import RegexPattern, URLResolver  # noqa: E402
from flask import Flask  # noqa: E402
from litestar import Litestar  # noqa: E402
from litestar import get as litestar_get  # noqa: E402
from sanic import Sanic  # noqa: E402
from starlette.routing import Match  # noqa: E402
from starlette.routing import Route as StarletteRoute  # noqa: E402

from korvex import Router  # noqa: E402
from korvex._core import Router as UncachedRouter  # noqa: E402

ROUTE_COUNTS = [100, 1_000, 10_000]
LOOKUPS = 200
SEED = 42
RESOURCES = ["users", "posts", "comments", "orders", "products", "teams", "projects", "tasks", "invoices", "sessions"]


def generate_routes(n: int) -> list[str]:
    """Half static (`/users/7/static`), half with a parameter (`/users/{id}/detail_7`)."""
    rng = random.Random(SEED)
    routes = []
    for i in range(n):
        resource = rng.choice(RESOURCES)
        routes.append(f"/{resource}/{i}/static" if rng.random() < 0.5 else f"/{resource}/{{id}}/detail_{i}")
    return routes


def handler_name(i: int) -> str:
    return f"handler_{i}"


# --- korvex ---


def build_korvex(routes):
    router = Router()
    for i, path in enumerate(routes):
        router.route(path, handler_name(i))
    return router


def lookup_korvex(router, path):
    return UncachedRouter.match(router, path)[0]


def lookup_korvex_cached(router, path):
    return router.match(path)[0]


# --- starlette (also FastAPI's router): tries each route in turn ---


async def endpoint(request):
    return None


def build_starlette(routes):
    return [StarletteRoute(path, endpoint, name=handler_name(i)) for i, path in enumerate(routes)]


def lookup_starlette(routes, path):
    scope = {"type": "http", "path": path, "method": "GET"}
    for route in routes:
        if route.matches(scope)[0] == Match.FULL:
            return route.name


# --- flask (werkzeug) ---


def build_flask(routes):
    app = Flask(__name__)
    for i, path in enumerate(routes):
        app.add_url_rule(path.replace("{", "<").replace("}", ">"), handler_name(i), endpoint)
    return app.url_map.bind("localhost")


def lookup_flask(adapter, path):
    return adapter.match(path, method="GET")[0]


# --- django ---


def build_django(routes):
    patterns = [
        django_path(re.sub(r"\{(\w+)\}", r"<str:\1>", path).lstrip("/"), endpoint, name=handler_name(i))
        for i, path in enumerate(routes)
    ]
    return URLResolver(RegexPattern(r"^/"), patterns)


def lookup_django(resolver, path):
    return resolver.resolve(path).url_name


# --- sanic ---


def build_sanic(routes):
    Sanic.test_mode = True
    app = Sanic(f"bench_{len(routes)}")
    for i, path in enumerate(routes):
        app.add_route(endpoint, re.sub(r"\{(\w+)\}", r"<\1:str>", path), name=handler_name(i))
    app.router.finalize()
    return app.router


def lookup_sanic(router, path):
    return type(router).get.__wrapped__(router, path, "GET", None)[0].name.rsplit(".", 1)[-1]


def lookup_sanic_cached(router, path):
    return router.get(path, "GET", None)[0].name.rsplit(".", 1)[-1]


# --- litestar ---


async def litestar_endpoint() -> None:
    return None


def build_litestar(routes):
    handlers = [
        litestar_get(path.replace("{id}", "{id:str}"), name=handler_name(i))(litestar_endpoint)
        for i, path in enumerate(routes)
    ]
    return Litestar(handlers).asgi_router


def lookup_litestar(router, path):
    return router._handle_routing(path, "GET")[1].name


def lookup_litestar_cached(router, path):
    return router.handle_routing(path, "GET")[1].name


ROUTERS = {
    "korvex": (build_korvex, lookup_korvex),
    "litestar": (build_litestar, lookup_litestar),
    "sanic": (build_sanic, lookup_sanic),
    "flask": (build_flask, lookup_flask),
    "django": (build_django, lookup_django),
    "starlette": (build_starlette, lookup_starlette),
    "korvex (cached)": (build_korvex, lookup_korvex_cached),
    "sanic (cached)": (build_sanic, lookup_sanic_cached),
    "litestar (cached)": (build_litestar, lookup_litestar_cached),
}

CASES = list(itertools.product(ROUTE_COUNTS, ROUTERS))


@pytest.mark.parametrize(("n_routes", "router_name"), CASES, ids=[f"{name}-{n}" for n, name in CASES])
def test_router_lookup(benchmark, n_routes, router_name):
    build, lookup = ROUTERS[router_name]
    routes = generate_routes(n_routes)
    router = build(routes)

    indexes = random.Random(SEED + 1).sample(range(n_routes), min(LOOKUPS, n_routes))
    cases = [(routes[i].replace("{id}", "123"), handler_name(i)) for i in indexes]
    for path, expected in cases:
        assert lookup(router, path) == expected, f"{router_name} matched {path} wrongly"

    paths = [path for path, _ in cases]
    benchmark.extra_info.update(router=router_name, routes=n_routes, lookups=len(paths))
    benchmark(lambda: [lookup(router, path) for path in paths])
