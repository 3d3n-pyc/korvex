"""Wires the layers together: the only place that knows them all."""

import logging
from datetime import timedelta
from typing import Optional

from korvex import App
from taskboard import __version__, web
from taskboard.config import Settings
from taskboard.db import Database
from taskboard.repositories import ListRepository, SessionRepository, TaskRepository
from taskboard.services import Services
from taskboard.services.auth import AuthService
from taskboard.services.events import EventBus
from taskboard.services.lists import ListService
from taskboard.services.tasks import TaskService

log = logging.getLogger(__name__)


def build_services(settings: Settings) -> Services:
    db = Database(settings.database)
    events = EventBus()
    lists = ListRepository(db)
    return Services(
        settings=settings,
        db=db,
        events=events,
        lists=ListService(lists, events),
        tasks=TaskService(TaskRepository(db), lists, events),
        auth=AuthService(SessionRepository(db), settings.admin_password, timedelta(hours=settings.session_hours)),
    )


def create_app(settings: Optional[Settings] = None) -> App:
    """A new, independent app. Nothing is opened until the server starts
    it: each worker process connects to the database on its own."""
    settings = settings or Settings.from_env()
    services = build_services(settings)
    app = App()
    app.on_startup(services.db.connect)  # startup hooks run in order: the database first
    app.on_startup(services.tasks.welcome)
    app.on_shutdown(services.db.close)
    if settings.admin_password == Settings().admin_password:
        app.on_startup(lambda: log.warning("the password is the default one: set TASKBOARD_ADMIN_PASSWORD"))
    web.register(app, services)
    app.docs(
        title="Taskboard",
        version=__version__,
        description="A todo list. Every `/api` route but `/api/me` needs the session cookie set by `POST /login`.",
    )
    return app
