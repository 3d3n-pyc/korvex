"""Exceptions that become HTTP error responses."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

from korvex._response import HeaderItems, header_pairs

if TYPE_CHECKING:
    from korvex._response import Response


class HTTPException(Exception):
    """Raise it in a handler, a dependency or a before hook to answer with
    an HTTP error: `raise HTTPException(404, "user not found")` answers
    `{"detail": "user not found"}`, as JSON, like a validation error's 422.
    Without a `detail`, the standard reason (`Not Found`) is used.
    `headers` is a dict or pairs. `@app.exception_handler(404)` answers
    differently."""

    def __init__(self, status: int, detail: str | None = None, *, headers: HeaderItems = ()) -> None:
        self.status = status
        self.detail = detail if detail is not None else HTTPStatus(status).phrase
        self.headers = header_pairs(headers)
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
    """Raise it from a dependency, a handler or a before hook to stop the
    request with this exact response.

        def signed_in(session: Optional[str] = Cookie(None)) -> None:
            if session is None:
                raise Abort(Response.redirect("/login"))

    The response is sent as is, without exception handlers; after hooks
    still apply. For an error status, prefer `HTTPException`. In a
    WebSocket's dependencies, it refuses the connection (403)."""

    def __init__(self, response: Response) -> None:
        super().__init__(response)
        self.response = response
