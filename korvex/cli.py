"""The `korvex` command: `korvex run mymodule:app` serves an App with the
built-in server, with the same options as App.run()."""

from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="korvex", description="Korvex's built-in server.")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="serve an App", description="Serves an App with the built-in server.")
    run.add_argument("target", help="the App to serve, as module:attribute (attribute defaults to app)")
    run.add_argument("--host", default="127.0.0.1", help="address to listen on (default: 127.0.0.1)")
    run.add_argument("--port", type=int, default=8000, help="port to listen on (default: 8000)")
    run.add_argument("--uds", help="listen on this Unix socket path instead of host and port")
    run.add_argument("--workers", type=int, default=1, help="worker processes (default: 1; about 3 per 4 CPU cores)")
    run.add_argument("--graceful-timeout", type=float, default=30.0, help="seconds requests get to finish on stop")
    run.add_argument("--max-body", type=int, default=1024 * 1024, help="largest request body in bytes (default: 1 MiB)")
    run.add_argument("--timeout", type=float, default=30.0, help="seconds to send the headers, then the body")
    run.add_argument(
        "--forwarded-allow-ips",
        default="127.0.0.1,::1",
        help="comma-separated proxy addresses whose X-Forwarded-* (or Forwarded) headers are trusted, or * (default: 127.0.0.1,::1)",
    )
    run.add_argument(
        "--forwarded-host",
        action="store_true",
        help="also believe the proxy's X-Forwarded-Host: only if it sets that header itself",
    )
    run.add_argument("--access-log", action="store_true", help="log each request")
    run.add_argument(
        "--ws-ping-interval", type=float, default=20.0, help="seconds between WebSocket pings; 0 for none (default: 20)"
    )
    run.add_argument("--app-dir", default=".", help="directory to import the module from (default: current directory)")
    args = parser.parse_args(argv)

    app = load(args.target, args.app_dir)
    app.run(
        args.host,
        args.port,
        workers=args.workers,
        graceful_timeout=args.graceful_timeout,
        max_body=args.max_body,
        timeout=args.timeout,
        uds=args.uds,
        forwarded_allow_ips=args.forwarded_allow_ips.split(","),
        access_log=args.access_log,
        ws_ping_interval=args.ws_ping_interval,
        forwarded_host=args.forwarded_host,
    )


def load(target: str, app_dir: str = "."):
    """Imports `module:attribute` (attribute defaults to `app`), looking in
    `app_dir` first."""
    from korvex.app import App

    module_name, _, attribute = target.partition(":")
    sys.path.insert(0, app_dir)
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as error:
        if error.name != module_name:  # the module exists but one of its imports failed
            raise
        sys.exit(f"korvex: can't find module {module_name!r} (looked in {app_dir!r} and the Python path)")
    app = getattr(module, attribute or "app", None)
    if not isinstance(app, App):
        sys.exit(f"korvex: {target!r} isn't a korvex App")
    return app
