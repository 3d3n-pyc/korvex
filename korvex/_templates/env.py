"""`Templates`: a folder of templates, compiled on first use and cached."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable, Union

from korvex._response import HeaderItems, Response
from korvex._templates.compile import compile_template
from korvex._templates.filters import FILTERS
from korvex._templates.parse import Node, TemplateError, parse

_Entry = tuple  # (render, used, {path: mtime_ns})


class Templates:
    """Render HTML templates from a folder.

    Templates are compiled the first time they are used, then cached.

        templates = Templates("templates")
        html = templates.render("index.html", user="Ada")

    Set `reload=True` to pick up edited files. The guide:
    https://3d3n-pyc.github.io/korvex/templates/
    """

    def __init__(self, directory: Union[str, Path], *, reload: bool = False) -> None:
        """Serve templates from `directory`; `reload` recompiles a template when its files change."""
        self.directory = Path(directory)
        self.reload = reload
        self.globals: dict[str, Any] = {}
        self.filters: dict[str, Callable[..., Any]] = dict(FILTERS)
        self._cache: dict[str, _Entry] = {}
        self._lock = threading.Lock()

    def render(self, name: str, /, **context: Any) -> str:
        """Render the template `name` with `context` and return the text."""
        entry = self._cache.get(name)
        if entry is None or (self.reload and self._stale(entry)):
            entry = self._compile(name)
        return entry[0](context, self.globals)

    def response(self, name: str, /, *, status: int = 200, headers: HeaderItems = (), **context: Any) -> Response:
        """Render `name` with `context` now and return it as an HTML response.

        `status` and `headers` set the response's; a Content-Type in `headers` wins.
        Guide: https://3d3n-pyc.github.io/korvex/templates/
        """
        response = Response(self.render(name, **context), status=status, headers=headers)
        if "Content-Type" not in response.headers:
            response.headers.set("Content-Type", "text/html; charset=utf-8")
        return response

    def _compile(self, name: str) -> _Entry:
        """Compile `name` under the lock and cache it."""
        with self._lock:
            used: set = set()

            def load(target: str) -> list[Node]:
                used.add(target)
                return parse(self._read(target), target)

            render, _ = compile_template(load(name), name, load, self.filters)
            stamps = {n: self._mtime(n) for n in used} if self.reload else {}
            entry = (render, used, stamps)
            self._cache[name] = entry
            return entry

    def _stale(self, entry: _Entry) -> bool:
        """Tell whether any file the template uses changed on disk."""
        try:
            return any(self._mtime(n) != m for n, m in entry[2].items())
        except TemplateError:
            return True

    def _mtime(self, name: str) -> int:
        """Return the modification time of a template file."""
        try:
            return self._path(name).stat().st_mtime_ns
        except OSError:
            raise self._missing(name) from None

    def _read(self, name: str) -> str:
        """Read a template file as UTF-8."""
        try:
            return self._path(name).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            raise self._missing(name) from None

    def _missing(self, name: str) -> TemplateError:
        """Build the error for a template that cannot be read."""
        return TemplateError(f"no template {name!r} in {self.directory}/")

    def _path(self, name: str) -> Path:
        """Turn a template name into a path that stays inside the folder."""
        parts = name.split("/")
        if not name or "\\" in name or "\0" in name or name.startswith("/") or ".." in parts:
            raise TemplateError(f"invalid template name {name!r}")
        root = self.directory.resolve()
        path = (root / name).resolve()
        if root not in path.parents:
            raise TemplateError(f"invalid template name {name!r}")
        return path
