"""The load-test app, in Django, served over ASGI (see korvex_app.py)."""

import os

import django
from django.conf import settings

# Starlette, FastAPI and Django try routes one by one, so the order matters:
# ROUTE_FIRST=1 declares /users/{id} before the 1,000 others (best case)
# instead of after them (worst case).
ROUTE_FIRST = os.environ.get("ROUTE_FIRST") == "1"

settings.configure(
    DEBUG=False,
    ALLOWED_HOSTS=["*"],
    ROOT_URLCONF=__name__,
    MIDDLEWARE=[],
    INSTALLED_APPS=[],
)
django.setup()

from django.core.asgi import get_asgi_application  # noqa: E402
from django.http import HttpResponse, JsonResponse  # noqa: E402
from django.urls import path  # noqa: E402


async def filler(request, id):
    return HttpResponse("filler")


async def get_user(request, id):
    return JsonResponse({"id": id, "name": f"user {id}"})


urlpatterns = [path(f"r{i % 10}/<int:id>/detail_{i}", filler) for i in range(1_000)]
urlpatterns.insert(0 if ROUTE_FIRST else len(urlpatterns), path("users/<int:id>", get_user))

app = get_asgi_application()
