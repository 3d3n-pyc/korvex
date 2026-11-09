"""Security schemes: dependencies that read a credential and show in OpenAPI.

Guide: https://3d3n-pyc.github.io/korvex/openapi/
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Optional

from korvex._errors import HTTPException
from korvex._requests import Connection

__all__ = ["APIKey", "Bearer", "OAuth2Password", "SecurityScheme"]


class SecurityScheme:
    """What the schemes share: `Depends(scheme)` gives the credential, a `str`.

    - `name`: its key in OpenAPI's `securitySchemes` (a duplicate gets `name_2`...)
    - `description`: shown in Swagger UI
    - `required`: without a credential, answer 401 with `WWW-Authenticate` (False gives None)

    Guide: https://3d3n-pyc.github.io/korvex/openapi/#signing-in-from-swagger-ui
    """

    __slots__ = ("name", "description", "required")
    challenge = "Bearer"  # the WWW-Authenticate of a 401

    def __init__(self, *, name: str, description: str | None, required: bool) -> None:
        self.name = name
        self.description = description
        self.required = required

    def __call__(self, request: Connection) -> Optional[str]:
        credential = self.credential(request)
        if credential:
            return credential
        if self.required:
            raise HTTPException(401, "Not authenticated", headers=[("WWW-Authenticate", self.challenge)])
        return None

    def credential(self, request: Connection) -> str | None:
        """The credential the request carries, if any."""
        raise NotImplementedError

    def openapi(self) -> dict[str, Any]:
        """Its Security Scheme Object."""
        raise NotImplementedError

    def _described(self, spec: dict[str, Any]) -> dict[str, Any]:
        return {**spec, "description": self.description} if self.description else spec

    def __repr__(self) -> str:
        return f"{type(self).__name__}(name={self.name!r})"


class Bearer(SecurityScheme):
    """A token in `Authorization: Bearer <token>`; `format` documents it (`"JWT"`)."""

    __slots__ = ("format",)

    def __init__(
        self, *, format: str | None = None, name: str = "bearer", description: str | None = None, required: bool = True
    ) -> None:
        super().__init__(name=name, description=description, required=required)
        self.format = format

    def credential(self, request: Connection) -> str | None:
        return _bearer_token(request)

    def openapi(self) -> dict[str, Any]:
        spec = {"type": "http", "scheme": "bearer"}
        if self.format:
            spec["bearerFormat"] = self.format
        return self._described(spec)


class APIKey(SecurityScheme):
    """A key in a header, query parameter or cookie: give exactly one (`TypeError` otherwise).

    A query key ends up in access logs and browser history: prefer a header.
    """

    __slots__ = ("location", "key")
    challenge = "APIKey"

    def __init__(
        self,
        *,
        header: str | None = None,
        query: str | None = None,
        cookie: str | None = None,
        name: str = "api_key",
        description: str | None = None,
        required: bool = True,
    ) -> None:
        given = [(where, key) for where, key in (("header", header), ("query", query), ("cookie", cookie)) if key]
        if len(given) != 1:
            raise TypeError("APIKey takes one of header=, query= or cookie=")
        super().__init__(name=name, description=description, required=required)
        self.location, self.key = given[0]

    def credential(self, request: Connection) -> str | None:
        if self.location == "header":
            return request.headers.get(self.key)
        if self.location == "query":
            return request.query.get(self.key)
        return request.cookies.get(self.key)

    def openapi(self) -> dict[str, Any]:
        return self._described({"type": "apiKey", "in": self.location, "name": self.key})


class OAuth2Password(SecurityScheme):
    """OAuth2 password flow: Swagger UI posts the form to `token_url`, then sends the bearer token.

    `scopes` maps scope to description; `refresh_url` is optional.
    """

    __slots__ = ("token_url", "scopes", "refresh_url")

    def __init__(
        self,
        token_url: str,
        *,
        scopes: Mapping[str, str] | None = None,
        refresh_url: str | None = None,
        name: str = "oauth2",
        description: str | None = None,
        required: bool = True,
    ) -> None:
        super().__init__(name=name, description=description, required=required)
        self.token_url = token_url
        self.scopes = dict(scopes or {})
        self.refresh_url = refresh_url

    def credential(self, request: Connection) -> str | None:
        return _bearer_token(request)

    def openapi(self) -> dict[str, Any]:
        flow: dict[str, Any] = {"tokenUrl": self.token_url, "scopes": self.scopes}
        if self.refresh_url:
            flow["refreshUrl"] = self.refresh_url
        return self._described({"type": "oauth2", "flows": {"password": flow}})


def _bearer_token(request: Connection) -> str | None:
    """The token of `Authorization: Bearer <token>` (the scheme's case
    doesn't matter), or None."""
    scheme, _, token = (request.headers.get("authorization") or "").partition(" ")
    if scheme.lower() != "bearer":
        return None
    return token.strip() or None
