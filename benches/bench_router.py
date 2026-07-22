"""Benchmark: korvex.Router vs other Python routers, at increasing scale.

Run with:
    uv run pytest benches/bench_router.py --benchmark-only -v

To add a new competitor, add one entry to ROUTERS below with a
`build(routes)` function and a `lookup(router, path)` function.
"""

import pytest
import itertools
import random
import re

import django
from django.conf import settings as django_settings
from flask import Flask
from sanic import Sanic
from starlette.requests import Request
from starlette.routing import Match, Route as StarletteRoute
from starlette.routing import Router as StarletteRouter

from korvex import Router as KorvexRouter

ROUTE_COUNTS = [100, 1_000, 10_000]
SAMPLE_SIZE = 200
SEED = 42

RESOURCES = [
    "users", "posts", "comments", "orders", "products",
    "teams", "projects", "tasks", "invoices", "sessions",
]


def _generate_routes(n: int) -> list[str]:
    """Generates a reproducible mix of static and dynamic routes,
    using Korvex's `{param}` syntax as the canonical format.
    """
    rng = random.Random(SEED)
    routes = []
    for i in range(n):
        resource = rng.choice(RESOURCES)
        if rng.random() < 0.5:
            routes.append(f"/{resource}/{i}/static")
        else:
            routes.append(f"/{resource}/{{id}}/detail_{i}")
    return routes


def _lookup_paths(routes: list[str], sample_size: int) -> list[str]:
    rng = random.Random(SEED + 1)
    sample = rng.sample(routes, min(sample_size, len(routes)))
    return [path.replace("{id}", "123") for path in sample]


# --- korvex --------------------------------------------------------------


def _build_korvex(routes: list[str]) -> KorvexRouter:
    router = KorvexRouter()
    for i, path in enumerate(routes):
        router.add_route(path, f"handler_{i}")
    return router


def _lookup_korvex(router: KorvexRouter, path: str) -> None:
    router.match_route(path)


# --- starlette -------------------------------------------------------------


async def _starlette_endpoint(request: Request) -> None:
    return None


def _build_starlette(routes: list[str]) -> StarletteRouter:
    router = StarletteRouter()
    for path in routes:
        router.routes.append(StarletteRoute(path, _starlette_endpoint))
    return router


def _lookup_starlette(router: StarletteRouter, path: str) -> None:
    scope = {"type": "http", "path": path, "method": "GET"}
    for route in router.routes:
        match, _ = route.matches(scope)
        if match == Match.FULL:
            break


# --- flask -----------------------------------------------------------------


def _flask_endpoint():
    return ""


def _to_flask_path(path: str) -> str:
    """Converts `{param}` (korvex syntax) to `<param>` (Flask syntax)."""
    return path.replace("{", "<").replace("}", ">")


def _build_flask(routes: list[str]) -> Flask:
    app = Flask(__name__)
    for i, path in enumerate(routes):
        app.add_url_rule(_to_flask_path(path), f"handler_{i}", _flask_endpoint)
    return app


def _lookup_flask(app: Flask, path: str) -> None:
    adapter = app.url_map.bind("localhost")
    try:
        adapter.match(path, method="GET")
    except Exception:
        pass


# --- django ------------------------------------------------------------

if not django_settings.configured:
    django_settings.configure(DEBUG=True, ALLOWED_HOSTS=["*"])
    django.setup()

from django.urls import path as django_path  # noqa: E402
from django.urls.resolvers import RegexPattern, URLResolver  # noqa: E402


def _django_endpoint(request, **kwargs):
    return None


def _to_django_path(path: str) -> str:
    """Converts `{param}` (korvex syntax) to `<str:param>` (Django syntax)."""
    converted = re.sub(r"\{(\w+)\}", r"<str:\1>", path)
    return converted.lstrip("/")


def _build_django(routes: list[str]) -> URLResolver:
    patterns = [django_path(_to_django_path(p), _django_endpoint) for p in routes]
    return URLResolver(RegexPattern(r"^/", name="root"), patterns)


def _lookup_django(resolver: URLResolver, path: str) -> None:
    try:
        resolver.resolve(path)
    except Exception:
        pass


# --- sanic -------------------------------------------------------------


def _sanic_endpoint(request):
    return None


def _to_sanic_path(path: str) -> str:
    """Converts `{param}` (korvex syntax) to `<param:str>` (Sanic syntax)."""
    return re.sub(r"\{(\w+)\}", r"<\1:str>", path)


def _build_sanic(routes: list[str]) -> Sanic:
    Sanic.test_mode = True
    app = Sanic(f"bench_{id(routes)}")
    for i, path in enumerate(routes):
        app.add_route(_sanic_endpoint, _to_sanic_path(path), name=f"handler_{i}")
    app.router.finalize()
    return app


def _lookup_sanic(app: Sanic, path: str) -> None:
    try:
        app.router.get(path, "GET", "localhost")
    except Exception:
        pass


# --- Registry ------------------------------------------------------------
#
# `build(routes)` -> opaque router object
# `lookup(router, path)` -> performs one route match (return value unused)

ROUTERS = {
    "korvex": (_build_korvex, _lookup_korvex),
    "starlette": (_build_starlette, _lookup_starlette),
    "flask": (_build_flask, _lookup_flask),
    "django": (_build_django, _lookup_django),
    "sanic": (_build_sanic, _lookup_sanic),
}

PARAMS = list(itertools.product(ROUTE_COUNTS, ROUTERS.keys()))


@pytest.fixture(
    params=PARAMS,
    ids=[f"{name}-{n}routes" for n, name in PARAMS],
)
def bench_case(request):
    n_routes, router_name = request.param
    routes = _generate_routes(n_routes)
    lookups = _lookup_paths(routes, SAMPLE_SIZE)

    build, lookup = ROUTERS[router_name]
    router = build(routes)

    return router, lookup, lookups


def test_router_lookup(benchmark, bench_case) -> None:
    router, lookup, lookups = bench_case

    def run() -> None:
        for path in lookups:
            lookup(router, path)

    benchmark(run)
