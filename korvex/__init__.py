"""Korvex: a fast router for Python, written in Rust, with an optional ASGI app."""

from importlib.metadata import version

from korvex._core import MethodNotAllowed
from korvex.app import App, AppGroup, LoopLocal, Request, UploadedFile, WebSocket, WebSocketDisconnect
from korvex.errors import Abort, HTTPException, ValidationError
from korvex.params import Cookie, Depends, Form, Header, Query
from korvex.response import Headers, Response
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
