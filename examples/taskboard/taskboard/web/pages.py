"""The board's page and its files, and the health check."""

import sqlite3
from pathlib import Path

from korvex import App, Request, Response

from taskboard.db import Database

STATIC = Path(__file__).parent.parent / "static"


def register(app: App, db: Database) -> None:
    @app.get("/")
    def board(request: Request) -> Response:
        """The board's page"""
        return Response.file(STATIC / "index.html")

    @app.get("/health")
    async def health(request: Request) -> Response:
        """Health check

        200 when the database answers, 503 otherwise: a load balancer stops
        sending traffic to a worker that can't serve it.
        """
        try:
            await db.fetch_one("SELECT 1")
        except (sqlite3.Error, RuntimeError):
            return Response.text("database unavailable", status=503)
        return Response.text("ok")

    app.static("/static", STATIC)
