import asyncio
from collections.abc import Awaitable, Iterator
from typing import Callable

import pytest
from client import Client

from taskboard.app import build_services, create_app
from taskboard.config import Settings
from taskboard.services import Services

PASSWORD = "s3cret"


@pytest.fixture
def settings(tmp_path) -> Settings:
    """A fresh database for each test."""
    return Settings(data_dir=str(tmp_path), admin_password=PASSWORD)


@pytest.fixture
def anonymous(settings: Settings) -> Iterator[Client]:
    """The app, started, seen by someone who hasn't signed in."""
    with Client(create_app(settings)) as client:
        yield client


@pytest.fixture
def client(anonymous: Client) -> Client:
    """The app, started, seen by someone signed in."""
    assert anonymous.post("/login", form={"password": PASSWORD}).status == 303
    return anonymous


@pytest.fixture
def inbox(client: Client) -> dict:
    """The list created on first start."""
    return client.get("/api/lists").json()[0]


@pytest.fixture
def with_services(settings: Settings) -> Callable[[Callable[[Services], Awaitable[None]]], None]:
    """Runs a scenario against the services alone, without HTTP:
    `with_services(scenario)`, where `async def scenario(services): ...`."""

    def run(scenario: Callable[[Services], Awaitable[None]]) -> None:
        async def main() -> None:
            services = build_services(settings)
            await services.db.connect()
            try:
                await scenario(services)
            finally:
                await services.db.close()

        asyncio.run(main())

    return run
