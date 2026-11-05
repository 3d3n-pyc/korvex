"""Taskboard: a shared task board, built on Korvex.

Layers, each importing only the ones below it:

    web/          HTTP: routes, input schemas, hooks, errors -> responses
    services/     the board's rules; knows nothing about HTTP
    repositories  SQL, and nothing else
    db            the SQLite connection and its migrations
    domain        entities, events and errors, used by every layer

`app.create_app()` wires them together.
"""

__version__ = "1.0.0"
