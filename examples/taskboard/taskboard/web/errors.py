"""Turns errors into responses. Under /api, always JSON with the request ID
(to find the matching log lines); elsewhere, plain text.

The domain's errors map to HTTP statuses here, so the services never have
to know about HTTP."""

from collections.abc import Iterable

from korvex import App, HTTPException, Request, Response, ValidationError
from korvex.middleware import current_request_id

from taskboard.domain import InvalidInput, NotFound


def install(app: App) -> None:
    @app.exception_handler(NotFound)
    def not_found(request: Request, exc: NotFound) -> Response:
        return _error(request, 404, str(exc))

    @app.exception_handler(InvalidInput)
    def invalid(request: Request, exc: InvalidInput) -> Response:
        # Same shape as Korvex's own 422s, so clients handle both alike.
        problem = {"loc": ["body", exc.field], "msg": str(exc), "type": "value_error"}
        return Response.json({"detail": [problem], "request_id": current_request_id()}, status=422)

    @app.exception_handler(HTTPException)  # ValidationError (422) included: it's an HTTPException
    def http_error(request: Request, exc: HTTPException) -> Response:
        if isinstance(exc, ValidationError) and _is_api(request):
            return Response.json({"detail": exc.errors, "request_id": current_request_id()}, status=422)
        return _error(request, exc.status, exc.detail, exc.headers)


def _error(request: Request, status: int, message: str, headers: Iterable[tuple[str, str]] = ()) -> Response:
    if _is_api(request):
        return Response.json({"error": message, "request_id": current_request_id()}, status=status, headers=headers)
    return Response.text(message, status=status, headers=headers)


def _is_api(request: Request) -> bool:
    return request.path.startswith("/api/")
