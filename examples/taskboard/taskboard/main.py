"""The app, for servers that import one:

    korvex run taskboard.main:app --workers 3
    uvicorn taskboard.main:app
"""

from taskboard.app import create_app
from taskboard.logs import configure_logging

configure_logging()
app = create_app()
