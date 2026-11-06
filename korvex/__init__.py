"""Korvex: a fast Python web framework, with a Rust router and HTTP server.

The standalone router is in `korvex.router`; the test client in
`korvex.testing`; security schemes in `korvex.security`; ready-made hooks
in `korvex.middleware`.
"""

from importlib.metadata import version

from korvex._app import App, LoopLocal
from korvex._errors import Abort, HTTPException, ValidationError
from korvex._params import Cookie, Depends, Form, Header, Query
from korvex._requests import Request, UploadedFile, WebSocket, WebSocketDisconnect
from korvex._response import Headers, Response
from korvex._routing import AppGroup

__all__ = [
    "Abort",
    "App",
    "AppGroup",
    "Cookie",
    "Depends",
    "Form",
    "Header",
    "Headers",
    "HTTPException",
    "LoopLocal",
    "Query",
    "Request",
    "Response",
    "UploadedFile",
    "ValidationError",
    "WebSocket",
    "WebSocketDisconnect",
]
__version__ = version("korvex")
