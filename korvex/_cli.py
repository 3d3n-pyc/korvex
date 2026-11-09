"""The `korvex` command: `korvex run mymodule:app` serves an App with the
built-in server, with the same options as App.run()."""

from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Sequence
from pathlib import Path


def _non_negative(text: str) -> int:
    """An argparse type: an integer of 0 or more."""
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError("must be 0 or more")
    return value


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="korvex", description="Korvex: serve an app, or set up coding agents.")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="serve an App", description="Serves an App with the built-in server.")
    run.add_argument("target", help="the App to serve, as module:attribute (attribute defaults to app)")
    run.add_argument("--host", default="127.0.0.1", help="address to listen on (default: 127.0.0.1)")
    run.add_argument("--port", type=int, default=8000, help="port to listen on (default: 8000)")
    run.add_argument("--uds", help="listen on this Unix socket path instead of host and port")
    run.add_argument("--workers", type=int, default=1, help="worker processes (default: 1; about 3 per 4 CPU cores)")
    run.add_argument(
        "--threads",
        type=int,
        default=1,
        help="event loops per worker, in parallel on free-threaded Python (default: 1)",
    )
    run.add_argument("--graceful-timeout", type=float, default=30.0, help="seconds requests get to finish on stop")
    run.add_argument(
        "--max-connections",
        type=_non_negative,
        default=0,
        help="most open connections per worker and thread; 0 for no limit (default: 0)",
    )
    run.add_argument(
        "--max-requests",
        type=_non_negative,
        default=0,
        help="HTTP requests a worker serves before it restarts, give or take 10%%; 0 for never (default: 0)",
    )
    run.add_argument("--max-body", type=int, default=1024 * 1024, help="largest request body in bytes (default: 1 MiB)")
    run.add_argument(
        "--read-timeout", type=float, default=30.0, help="seconds a client gets to send the headers, then the body"
    )
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
    agents = commands.add_parser(
        "agents",
        help="teach coding agents Korvex",
        description="Installs Korvex's agent skill in a project: .agents/skills/korvex and .claude/skills/korvex.",
    )
    agents.add_argument("--dir", default=".", help="the project directory (default: current directory)")
    args = parser.parse_args(argv)

    if args.command == "agents":
        install_skill(Path(args.dir))
        return
    app = load(args.target, args.app_dir)
    app.run(
        args.host,
        args.port,
        workers=args.workers,
        threads=args.threads,
        graceful_timeout=args.graceful_timeout,
        max_body=args.max_body,
        read_timeout=args.read_timeout,
        uds=args.uds,
        forwarded_allow_ips=args.forwarded_allow_ips.split(","),
        access_log=args.access_log,
        ws_ping_interval=args.ws_ping_interval,
        forwarded_host=args.forwarded_host,
        max_connections=args.max_connections,
        max_requests=args.max_requests,
    )


def install_skill(project: Path) -> None:
    """Writes the packaged agent skill into `project`, for the coding agents
    that read .agents/skills and for Claude Code, replacing any older copy."""
    skill = (Path(__file__).parent / "_skill" / "SKILL.md").read_bytes()
    for folder in (".agents", ".claude"):
        target = project / folder / "skills" / "korvex" / "SKILL.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(skill)
        print(target)
    print("Coding agents now read how to write Korvex apps. Rerun after upgrading Korvex.")


def load(target: str, app_dir: str = "."):
    """Imports `module:attribute` (attribute defaults to `app`), looking in
    `app_dir` first."""
    from korvex._app import App

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
