"""The names users need are exported from the top-level package."""

import korvex
from korvex._background import BackgroundTasks
from korvex._requests import MultiDict
from korvex._response import StreamingResponse
from korvex._sse import ServerSentEvent
from korvex._templates.compile import UndefinedError
from korvex._templates.parse import TemplateSyntaxError

EXPORTED = {
    "BackgroundTasks": BackgroundTasks,
    "MultiDict": MultiDict,
    "ServerSentEvent": ServerSentEvent,
    "StreamingResponse": StreamingResponse,
    "TemplateSyntaxError": TemplateSyntaxError,
    "UndefinedError": UndefinedError,
}


def test_names_are_the_internal_objects() -> None:
    """Each name imported from korvex is the same object as the internal one."""
    for name, obj in EXPORTED.items():
        assert getattr(korvex, name) is obj


def test_names_are_listed_in_all() -> None:
    """Each new name is listed in korvex.__all__."""
    assert set(EXPORTED) <= set(korvex.__all__)


def test_every_all_name_exists() -> None:
    """Every name in korvex.__all__ resolves to an attribute of korvex."""
    for name in korvex.__all__:
        assert hasattr(korvex, name), name
