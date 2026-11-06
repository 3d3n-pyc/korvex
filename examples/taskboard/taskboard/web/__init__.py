"""The HTTP layer. Each module registers its routes on the app, given the
services it needs (no globals: tests build as many apps as they like)."""

from korvex import App
from taskboard.services import Services
from taskboard.web import auth, errors, hooks, lists, live, pages, tasks


def register(app: App, services: Services) -> None:
    hooks.install(app, services.settings)
    errors.install(app)
    current_user = auth.register(app, services.auth, services.settings)  # the guard for the others
    lists.register(app, services.lists, services.tasks, current_user)
    tasks.register(app, services.tasks, current_user)
    live.register(app, services.events, current_user)
    pages.register(app, services.db)
