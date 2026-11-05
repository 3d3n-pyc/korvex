# Taskboard

A todo list built on Korvex: several lists, due dates with Today and
Planned views, drag and drop, instant search, keyboard shortcuts, and
changes that appear live in every open window. Light and dark, works on
a phone.

It's also laid out like a real project, to show how a Korvex app is put
together once it outgrows one file: layers, a database with migrations,
settings, and its own tests.

## Run it

From this folder, with the repository's environment (after
`uv run maturin develop --release` at the root):

```sh
uv run --project ../.. python -m taskboard     # built-in server (Linux, macOS)
uv run --project ../.. pytest                  # the tests
```

Then open http://127.0.0.1:8000 and sign in with the password `korvex`.
The API is described at http://127.0.0.1:8000/docs. Press `?` in the app
for the keyboard shortcuts.

On its own, as a project of its own:

```sh
pip install -e ".[test]"
python -m taskboard                    # or: korvex run taskboard.main:app --workers 3
uvicorn taskboard.main:app             # any ASGI server works, on Windows too
pytest
```

The data goes in `data/` (delete it to start over).

## Layout

```
taskboard/
  domain.py          lists, tasks, events and errors. No HTTP, no SQL.
  db.py              the SQLite connection, run off the event loop; migrations
  repositories.py    every SQL query
  services/
    lists.py         the rules for lists (the last one stays...)
    tasks.py         the rules for tasks: tidying, ordering, moving, announcing
    auth.py          signing in, sessions stored as hashes
    events.py        hands changes to the open pages
    __init__.py      Services: what the web layer gets
  web/
    schemas.py       what clients send, validated by Korvex (422 otherwise)
    lists.py         /api/lists routes
    tasks.py         /api/tasks routes, and the export
    auth.py          /login, /logout, /api/me, and current_user, the guard
    live.py          the /live WebSocket
    pages.py         the page, its static files, /health
    hooks.py         timing, request IDs, CORS, rate limiting
    errors.py        domain errors and HTTP errors -> responses
  app.py             create_app(): builds the services, wires the layers
  config.py          Settings, from TASKBOARD_* environment variables
  logs.py            log lines with the request ID
  main.py            `app`, for `korvex run` and ASGI servers
  __main__.py        `python -m taskboard`
  static/            the page: HTML, CSS, JavaScript modules, no build step
    js/api.js        the JSON API
    js/store.js      the page's state and the actions that change it
    js/ui/           the sidebar, the task list, the details panel, overlays
    js/dragdrop.js, shortcuts.js, live.js, dates.js, dom.js
tests/
  client.py          a test client: the app in-process, with lifespan,
                     cookies and WebSockets
  test_services.py   the rules, on the services alone
  test_api.py        the API through the whole app
  test_auth.py       signing in, and what it guards
  test_live.py       live updates
```

## How it fits together

Each layer imports only the ones below it:

```
web  ->  services  ->  repositories  ->  db
  \__________\______________\____________> domain
```

- **The services don't know HTTP.** They take and return plain values
  and domain objects, and raise domain errors (`NotFound`,
  `InvalidInput`); `web/errors.py` turns those into 404s and 422s. The
  same services could serve a CLI or a job queue.
- **The SQL stays in `repositories.py`.** Moving to Postgres means
  rewriting it and `db.py`; the services don't change.
- **No globals.** `create_app(settings)` builds everything and hands each
  route module the services it needs. Every test gets its own app, with
  its own database in a temporary folder.
- **Nothing opens at import.** The database connects in a startup hook,
  so each worker process opens its own connection after the fork.

The page follows the same idea on a smaller scale: `api.js` talks to the
server, `store.js` holds the state and the actions, and the `ui/` modules
only draw it.

Two kinds of validation, on purpose:

- `web/schemas.py` checks the **shape** of a request (types, lengths,
  dates, colors, unknown fields): Korvex rejects a bad request before the
  handler runs.
- The services check the **rules** (a title can't be blank once trimmed,
  the last list can't be deleted, a task must go to a list that exists).
  They hold whatever calls them.

Both answer with the same 422 format, so the page shows both alike.

## Choices worth knowing

- **"Today" is the user's today.** Due dates are calendar days, and the
  server doesn't know the user's time zone: the page sends its own date
  (`?due_to=2026-11-05`). Korvex validates `date` query parameters like
  any other.
- **Absent is not null.** In a PATCH, a field left out stays as it is,
  and `"due": null` removes the due date: msgspec's `UNSET` tells them
  apart.
- **Order is stored.** New tasks go on top; a drag and drop sends the
  list's new order in one request (`PUT /api/lists/{id}/order`), applied
  in one transaction.
- **Changes show at once.** The page updates before the server answers,
  and puts things back if it refuses. A delete waits five seconds, so it
  can be undone.
- **SQLite, without blocking the loop.** Queries run in one thread per
  worker, which also serializes them; WAL mode lets workers read while
  one writes. Migrations are versioned with `PRAGMA user_version` and
  applied under a lock, so workers starting together don't collide.
- **Sessions in the database**, so they survive restarts and every worker
  sees them. Only a SHA-256 of each token is stored. Signing out ends the
  session on the server, not just in the browser. The cookie is HttpOnly
  and SameSite=Lax; set `TASKBOARD_SECURE_COOKIES=true` behind HTTPS.
  One dependency, `current_user` in `web/auth.py`, guards the `/api`
  groups and the WebSocket alike (`dependencies=[Depends(current_user)]`).
- **Live updates through an event bus.** Services publish events; each
  open page has its own bounded queue, and one that stops reading is cut
  off (closed with 1013) instead of slowing the others. It reconnects and
  reloads.
- **Errors under `/api` are JSON with the request ID**, the same ID that's
  in `X-Request-ID` and in the log lines: from a bug report to its logs in
  one search.

## Settings

All optional, as `TASKBOARD_*` environment variables (see `config.py`):

| Variable | Default | |
|---|---|---|
| `TASKBOARD_DATA_DIR` | `data` | where the database goes |
| `TASKBOARD_ADMIN_PASSWORD` | `korvex` | change it: the app warns at startup otherwise |
| `TASKBOARD_SESSION_HOURS` | `8` | |
| `TASKBOARD_SECURE_COOKIES` | `false` | `true` behind HTTPS |
| `TASKBOARD_CORS_ORIGINS` | | comma-separated sites allowed to call the API |
| `TASKBOARD_RATE_LIMIT` | `600` | requests per minute and client address |
| `TASKBOARD_HOST`, `_PORT`, `_WORKERS` | `127.0.0.1`, `8000`, `1` | for `python -m taskboard` |

A bad value stops the app at startup with a message saying which.

## With several workers

`TASKBOARD_WORKERS=3` works: the data and the sessions are shared through
the database. Two things stay per worker, as they would in any framework:

- live updates reach the pages connected to the same worker;
- rate limits count per worker.

Both have one place to change: `services/events.py` for a shared bus
(Redis pub/sub, Postgres LISTEN/NOTIFY), and the `store=` of
`rate_limit()` in `web/hooks.py` for shared counters.
