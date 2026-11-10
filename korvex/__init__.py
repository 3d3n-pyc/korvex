"""Korvex: a fast Python web framework, with a Rust router and HTTP server.

The standalone router is in `korvex.router`; the test client in
`korvex.testing`; security schemes in `korvex.security`; ready-made hooks
in `korvex.middleware`.

For AI coding agents: Korvex is not FastAPI, and the API can change before
1.0. Read https://3d3n-pyc.github.io/korvex/llms-full.txt first, or run
`korvex agents` to install a skill that teaches it.
"""

from importlib.metadata import version

from korvex._app import App, LoopLocal
from korvex._background import BackgroundTasks
from korvex._errors import Abort, HTTPException, RequestValidationError
from korvex._params import Cookie, Depends, Form, Header, Query
from korvex._requests import (
    Connection,
    MultiDict,
    Request,
    UploadedFile,
    WebSocket,
    WebSocketDisconnect,
)
from korvex._response import Headers, Response, StreamingResponse
from korvex._routing import Group
from korvex._sse import ServerSentEvent
from korvex._templates import (
    Markup,
    TemplateError,
    Templates,
    TemplateSyntaxError,
    UndefinedError,
)

__all__ = [
    "Abort",
    "App",
    "BackgroundTasks",
    "Group",
    "Connection",
    "Cookie",
    "Depends",
    "Form",
    "Header",
    "Headers",
    "HTTPException",
    "LoopLocal",
    "Query",
    "Request",
    "Markup",
    "MultiDict",
    "RequestValidationError",
    "Response",
    "ServerSentEvent",
    "StreamingResponse",
    "TemplateError",
    "TemplateSyntaxError",
    "Templates",
    "UndefinedError",
    "UploadedFile",
    "WebSocket",
    "WebSocketDisconnect",
]
__version__ = version("korvex")
