"""Exceptions that become HTTP error responses."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

from korvex._response import HeaderItems, header_pairs

if TYPE_CHECKING:
    from korvex._response import Response


class HTTPException(Exception):
    """Raise it to answer with an HTTP error: `{"detail": ...}` as JSON.

    - `detail`: the message; the standard reason if omitted
    - `headers`: extra response headers, a dict or pairs

    Guide: https://3d3n-pyc.github.io/korvex/errors-and-hooks/
    """

    def __init__(self, status: int, detail: str | None = None, *, headers: HeaderItems = ()) -> None:
        self.status = status
        self.detail = detail if detail is not None else HTTPStatus(status).phrase
        self.headers = header_pairs(headers)
        super().__init__(status, self.detail)


class ValidationError(HTTPException):
    """Raised when a request doesn't match a handler's annotations: a 422 with `errors`.

    Guide: https://3d3n-pyc.github.io/korvex/validation/#errors
    """

    def __init__(self, errors: list[dict]) -> None:
        super().__init__(422)
        self.errors = errors


class Abort(Exception):
    """Raise it to stop the request with this exact response, past exception handlers.

    After hooks still apply. In a WebSocket's dependencies, it refuses the connection (403).
    Guide: https://3d3n-pyc.github.io/korvex/errors-and-hooks/
    """

    def __init__(self, response: Response) -> None:
        super().__init__(response)
        self.response = response
