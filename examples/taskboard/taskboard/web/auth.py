"""Signing in and out with a regular HTML form and a session cookie, and
`current_user`, the dependency that guards the API and the WebSocket."""

from collections.abc import Awaitable, Callable
from typing import Optional

from korvex import App, Cookie, Depends, Form, HTTPException, Response
from korvex.security import APIKey
from taskboard.config import Settings
from taskboard.services.auth import AuthService

COOKIE = "session"

# The session cookie as a security scheme: OpenAPI lists it on the routes
# it guards. Not required here: current_user answers the 401 itself.
SESSION = APIKey(cookie=COOKIE, name="session", description="Set by `POST /login`.", required=False)


CurrentUser = Callable[..., Awaitable[str]]


def register(app: App, auth: AuthService, settings: Settings) -> CurrentUser:
    """Registers the routes, and returns the `current_user` dependency for
    the other route modules: `dependencies=[Depends(current_user)]`."""

    async def current_user(token: Optional[str] = Depends(SESSION)) -> str:
        """Who is signed in; a 401 for anyone else. HTTP routes and the
        WebSocket depend on it alike."""
        user = await auth.user(token)
        if user is None:
            raise HTTPException(401, "sign in first")
        return user

    @app.post("/login", status=303, tags=["auth"])
    async def login(password: str = Form("")) -> Response:
        """Sign in

        A form with a `password` field. Sets the session cookie and goes back
        to the app (303), with `?login=failed` if the password is wrong.
        """
        token = await auth.login(password)
        if token is None:
            return Response.redirect(app.url_for("board") + "?login=failed", status=303)
        response = Response.redirect(app.url_for("board"), status=303)
        # HttpOnly: page scripts can't read it. SameSite=Lax: other sites
        # can't send it with a POST, which stops cross-site request forgery.
        response.set_cookie(
            COOKIE,
            token,
            max_age=int(auth.lifetime.total_seconds()),
            httponly=True,
            samesite="lax",
            secure=settings.secure_cookies,
        )
        return response

    @app.post("/logout", status=303, tags=["auth"])
    async def logout(token: Optional[str] = Cookie(None, alias=COOKIE)) -> Response:
        """Sign out

        Ends the session on the server too, not just in the browser.
        """
        await auth.logout(token)
        response = Response.redirect(app.url_for("board"), status=303)
        response.delete_cookie(COOKIE)
        return response

    @app.get("/api/me", tags=["auth"])
    async def me(token: Optional[str] = Cookie(None, alias=COOKIE)) -> Response:
        """Who is signed in

        `{"user": "admin"}`, or `{"user": null}`. Open to everyone: the page
        asks it first, to show the app or the sign-in screen.
        """
        return Response.json({"user": await auth.user(token)})

    return current_user
