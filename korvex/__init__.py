from importlib.metadata import version as _version

from korvex._core import MethodNotAllowed
from korvex.app import App, Request
from korvex.group import RouteGroup
from korvex.response import Headers, Response
from korvex.router import Router

__all__ = ["App", "Headers", "MethodNotAllowed", "Request", "Response", "RouteGroup", "Router"]
# Read from the installed package's own metadata rather than hand-written
# here, so it can never drift from what `pip show korvex`/PyPI report -
# the same "one source of truth" principle as Cargo.toml driving the
# package version itself (see .github/scripts/set_version_from_tag.py).
__version__ = _version("korvex")
