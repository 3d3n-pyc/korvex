"""Request IDs: one per request, sent back and found in the logs."""

from __future__ import annotations

import logging
import re
import uuid
from contextvars import ContextVar
from typing import TYPE_CHECKING

from korvex._response import Response

if TYPE_CHECKING:
    from korvex._app import App
    from korvex._requests import Connection, Request


_request_id: ContextVar[str | None] = ContextVar("korvex_request_id", default=None)
# A request ID from outside is reused only if it's short and plain, so it
# can't inject newlines or huge values into the logs.
_SAFE_ID = re.compile(r"[A-Za-z0-9._:-]{1,200}")


def request_id(app: App, *, header: str = "X-Request-ID", trust_incoming: bool = True) -> None:
    """Gives every request an ID, sent back in the response and put in the logs.

    - `header`: the header the ID is read from and sent back in.
    - `trust_incoming`: reuse a safe incoming ID, else make a new one.

    The built-in server's access logs show it too.
    Guide: https://3d3n-pyc.github.io/korvex/middleware/#request-ids-request_idapp
    """

    @app.before_request
    def assign(request: Connection) -> None:
        incoming = request.headers.get(header) if trust_incoming else None
        _request_id.set(incoming if incoming and _SAFE_ID.fullmatch(incoming) else uuid.uuid4().hex)

    @app.after_request
    def send_back(request: Request, response: Response) -> None:
        value = _request_id.get()
        if value is not None:
            response.headers.set(header, value)


def current_request_id() -> str | None:
    """The ID of the request being handled, if `request_id()` is set up."""
    return _request_id.get()


class RequestIdFilter(logging.Filter):
    """Logging filter that adds `request_id` to records (`-` outside a request).

    Guide: https://3d3n-pyc.github.io/korvex/middleware/#request-ids-request_idapp
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get() or "-"
        return True
