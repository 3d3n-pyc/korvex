"""Exceptions that become HTTP error responses."""

from __future__ import annotations

from collections.abc import Iterable
from http import HTTPStatus
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from korvex._response import Response


class HTTPException(Exception):
    """Raise it in a handler, a dependency or a before hook to answer with
    an HTTP error:
    `raise HTTPException(404, "user not found")`. Without a `detail`, the
    standard reason (`Not Found`) is used."""

    def __init__(self, status: int, detail: str | None = None, *, headers: Iterable[tuple[str, str]] = ()) -> None:
        self.status = status
        self.detail = detail if detail is not None else HTTPStatus(status).phrase
        self.headers = list(headers)
        super().__init__(status, self.detail)


class ValidationError(HTTPException):
    """Raised when a request doesn't match a handler's annotations; answered
    with a 422 and `{"detail": errors}`, or by `@app.exception_handler(422)`.
    Each error has `loc` (where: `["body", "age"]`, `["query", "limit"]`...),
    `msg` and `type`."""

    def __init__(self, errors: list[dict]) -> None:
        super().__init__(422)
        self.errors = errors


class Abort(Exception):
    """Raise it to stop a request with this exact response: from a
    dependency, a handler or a before hook.

        def signed_in(session: Optional[str] = Cookie(None)) -> None:
            if session is None:
                raise Abort(Response.redirect("/login"))

    The response is sent as is, without going through exception handlers;
    after hooks still apply. For an error status, `HTTPException` is
    usually what you want. In a WebSocket's dependencies, it refuses the
    connection (403): there's no other response to send before accepting."""

    def __init__(self, response: Response) -> None:
        super().__init__(response)
        self.response = response
