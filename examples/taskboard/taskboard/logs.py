"""Log lines with the ID of the request they belong to, the same ID the
client gets in X-Request-ID: from an error report to its logs in one
search."""

import logging

from korvex.middleware import RequestIdFilter


def configure_logging(level: int = logging.INFO) -> None:
    # Korvex's own lines (the access log) already end with the request ID.
    logging.basicConfig(level=level, format="%(asctime)s %(message)s")
    app_logs = logging.getLogger("taskboard")
    if app_logs.handlers:
        return  # already done
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s"))
    handler.addFilter(RequestIdFilter())  # fills %(request_id)s, "-" outside a request
    app_logs.addHandler(handler)
    app_logs.propagate = False
