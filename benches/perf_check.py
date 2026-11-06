"""Performance check: is a change slower than the code it replaces?

Two commands:

- `measure` times a few in-process cases with whatever Korvex the
  interpreter imports, and prints them as JSON (µs per call).
- `compare --base PYTHON --head PYTHON` runs `measure` with two
  interpreters, each with its own Korvex installed, several rounds
  alternating between them so that both see the same machine noise. It
  prints the median of each case and fails (exit 1) when the head is more
  than `--threshold` slower than the base on any of them.

CI runs `compare` on every pull request against its base branch (see
.github/workflows/perf.yml). Locally:

    python benches/perf_check.py compare --base ../main/.venv/bin/python --head .venv/bin/python

The cases only use what Korvex has had for a while; one a version
doesn't support is skipped for both.
"""

import argparse
import asyncio
import json
import statistics
import subprocess
import sys
import timeit
from pathlib import Path

# --- measure ---


# No `from __future__ import annotations` here: the handlers below are
# defined in a function, and Korvex reads their annotations.


def _cases() -> dict:
    import korvex
    from korvex import App, Response
    from korvex.router import Router

    loop = asyncio.new_event_loop()
    cases = {}

    router = Router()
    for n in range(50):
        router.get(f"/section{n}/{{id:int}}", f"handler{n}")
    paths = [f"/section{n % 50}/{n}" for n in range(4096)]  # more than the lookup cache holds
    position = iter(range(1 << 62))

    def match():
        router.match(paths[next(position) % 4096])

    cases["router: match"] = match

    def asgi(app, scope, body=b""):
        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message):
            pass

        def call():
            loop.run_until_complete(app(dict(scope), receive, send))

        return call

    get = {"type": "http", "method": "GET", "path": "/users/42", "headers": [], "query_string": b""}

    app = App()

    @app.get("/users/{id:int}")
    def user(request, id):
        return Response.json({"id": id})

    cases["app: GET with a JSON response"] = asgi(app, get)

    app = App()

    @app.get("/users/{id:int}")
    def stream(request, id):
        return Response.stream(str(n) for n in range(10))

    cases["app: streamed response"] = asgi(app, get)

    try:
        import msgspec
    except ImportError:
        msgspec = None
    if msgspec is not None:

        class UserIn(msgspec.Struct):
            name: str
            age: int

        class UserOut(msgspec.Struct):
            id: int
            name: str

        app = App()

        @app.post("/users")
        async def create(request, body: UserIn) -> UserOut:
            return UserOut(id=1, name=body.name)

        post = {**get, "method": "POST", "path": "/users", "headers": [(b"content-type", b"application/json")]}
        cases["app: validated POST (msgspec)"] = asgi(app, post, b'{"name": "Ada", "age": 36}')

    if hasattr(korvex, "Depends"):
        from korvex import Depends, Header

        def current_user(x_token: str = Header()) -> str:
            return x_token

        def session():
            yield "s"

        app = App()

        @app.get("/me")
        async def me(request, user: str = Depends(current_user), s: str = Depends(session)):
            return Response.json({"user": user})

        me_scope = {**get, "path": "/me", "headers": [(b"x-token", b"t1")]}
        cases["app: two dependencies"] = asgi(app, me_scope)
    return cases


def measure(repeat: int = 5) -> dict[str, float]:
    """The fastest of `repeat` runs of each case, in µs per call."""
    results = {}
    for name, case in _cases().items():
        case()  # warm up
        timer = timeit.Timer(case)
        number, _ = timer.autorange()  # enough calls for about 0.2 s
        results[name] = min(timer.repeat(repeat=repeat, number=number)) / number * 1e6
    return results


# --- compare ---


def run(python: str) -> dict[str, float]:
    # -I: the Korvex installed for this interpreter, never a source tree nearby.
    output = subprocess.run(
        [python, "-I", str(Path(__file__).resolve()), "measure"], capture_output=True, text=True, check=True
    ).stdout
    return json.loads(output)


def compare(base: str, head: str, rounds: int, threshold: float) -> int:
    samples: dict[str, dict[str, list[float]]] = {"base": {}, "head": {}}
    for round_ in range(rounds):
        # Alternate which goes first, so neither always runs on a warmer machine.
        order = [("base", base), ("head", head)] if round_ % 2 == 0 else [("head", head), ("base", base)]
        for side, python in order:
            for name, value in run(python).items():
                samples[side].setdefault(name, []).append(value)

    width = max(map(len, samples["head"]), default=10)
    print(f"{'case':<{width}}  {'base':>9}  {'head':>9}  change")
    slower = []
    for name in samples["head"]:
        if name not in samples["base"]:
            print(f"{name:<{width}}  {'-':>9}  {statistics.median(samples['head'][name]):>7.2f}µs  new")
            continue
        before = statistics.median(samples["base"][name])
        after = statistics.median(samples["head"][name])
        ratio = after / before
        flag = "  <- slower" if ratio > threshold else ""
        print(f"{name:<{width}}  {before:>7.2f}µs  {after:>7.2f}µs  {ratio - 1:+.1%}{flag}")
        if ratio > threshold:
            slower.append(name)
    if slower:
        print(f"\n{len(slower)} case(s) more than {threshold - 1:.0%} slower than the base: {', '.join(slower)}")
        return 1
    print(f"\nNo case more than {threshold - 1:.0%} slower than the base.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("measure", help="time the cases with this interpreter's Korvex, as JSON")
    versus = commands.add_parser("compare", help="compare two interpreters' Korvex")
    versus.add_argument("--base", required=True, help="Python with the Korvex to compare against")
    versus.add_argument("--head", required=True, help="Python with the Korvex being checked")
    versus.add_argument("--rounds", type=int, default=5)
    versus.add_argument("--threshold", type=float, default=1.10, help="slowest allowed, as a ratio (default: 1.10)")
    args = parser.parse_args()
    if args.command == "measure":
        print(json.dumps(measure()))
        return 0
    return compare(args.base, args.head, args.rounds, args.threshold)


if __name__ == "__main__":
    sys.exit(main())
