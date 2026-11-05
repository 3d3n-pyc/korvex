"""The HTTP layer. Each module registers its routes on the app, given the
services it needs (no globals: tests build as many apps as they like)."""

from korvex import App

from taskboard.services import Services
from taskboard.web import auth, errors, hooks, lists, live, pages, tasks


def register(app: App, services: Services) -> None:
    hooks.install(app, services.settings)
    errors.install(app)
    auth.register(app, services.auth, services.settings)  # first: it defines the "session" middleware
    lists.register(app, services.lists, services.tasks)
    tasks.register(app, services.tasks)
    live.register(app, services.events, services.auth)
    pages.register(app, services.db)
