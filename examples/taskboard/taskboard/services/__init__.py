"""The app's rules, between the web layer and the repositories."""

from dataclasses import dataclass

from taskboard.config import Settings
from taskboard.db import Database
from taskboard.services.auth import AuthService
from taskboard.services.events import EventBus
from taskboard.services.lists import ListService
from taskboard.services.tasks import TaskService


@dataclass(frozen=True)
class Services:
    """Everything the web layer is given, built once by app.create_app()."""

    settings: Settings
    db: Database
    events: EventBus
    lists: ListService
    tasks: TaskService
    auth: AuthService
