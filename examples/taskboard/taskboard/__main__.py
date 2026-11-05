"""`python -m taskboard`: serves the app with Korvex's built-in server,
configured by the TASKBOARD_* environment variables (see config.py)."""

from taskboard.app import create_app
from taskboard.config import Settings
from taskboard.logs import configure_logging


def main() -> None:
    settings = Settings.from_env()
    configure_logging()
    create_app(settings).run(settings.host, settings.port, workers=settings.workers, access_log=True)


if __name__ == "__main__":
    main()
