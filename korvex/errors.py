"""Exceptions that become HTTP error responses."""

from __future__ import annotations

from collections.abc import Iterable
from http import HTTPStatus


class HTTPException(Exception):
    """Raise it in a handler or middleware to answer with an HTTP error:
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
