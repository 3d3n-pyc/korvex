"""Request IDs: one per request, sent back and found in the logs."""

from __future__ import annotations

import logging
import re
import uuid
from contextvars import ContextVar
from typing import TYPE_CHECKING

from korvex.response import Response

if TYPE_CHECKING:
    from korvex.app import App
    from korvex.requests import Request


_request_id: ContextVar[str | None] = ContextVar("korvex_request_id", default=None)
# A request ID from outside is reused only if it's short and plain, so it
# can't inject newlines or huge values into the logs.
_SAFE_ID = re.compile(r"[A-Za-z0-9._:-]{1,200}")


def request_id(app: App, *, header: str = "X-Request-ID", trust_incoming: bool = True) -> None:
    """Gives every request an ID: the incoming `header` (from the client or
    a proxy) when `trust_incoming` and it's safe, otherwise a new random
    one. The ID goes back in the response's `header`, `current_request_id()`
    returns it while the request runs, and `RequestIdFilter` adds it to
    log records. The built-in server's access logs show it too."""

    @app.before_request
    def assign(request: Request) -> None:
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
    """A logging filter that adds `request_id` to every record (`-` outside
    a request), for formats like `"%(request_id)s %(message)s"`:

        handler.addFilter(RequestIdFilter())
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get() or "-"
        return True
