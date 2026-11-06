"""Korvex: a fast router for Python, written in Rust, with an optional ASGI app."""

from importlib.metadata import version

from korvex._app import App, LoopLocal
from korvex._core import MethodNotAllowed
from korvex._errors import Abort, HTTPException, ValidationError
from korvex._params import Cookie, Depends, Form, Header, Query
from korvex._requests import Request, UploadedFile, WebSocket, WebSocketDisconnect
from korvex._response import Headers, Response
from korvex._routing import AppGroup
from korvex.router import RouteGroup, Router

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
    "MethodNotAllowed",
    "Query",
    "Request",
    "Response",
    "RouteGroup",
    "Router",
    "UploadedFile",
    "ValidationError",
    "WebSocket",
    "WebSocketDisconnect",
]
__version__ = version("korvex")
