"""Signing in and out with a regular HTML form and a session cookie, and
`current_user`, the dependency that guards the API and the WebSocket."""

from collections.abc import Awaitable, Callable
from typing import Optional

from korvex import App, Cookie, Form, HTTPException, Request, Response

from taskboard.config import Settings
from taskboard.services.auth import AuthService

COOKIE = "session"


CurrentUser = Callable[..., Awaitable[str]]


def register(app: App, auth: AuthService, settings: Settings) -> CurrentUser:
    """Registers the routes, and returns the `current_user` dependency for
    the other route modules: `dependencies=[Depends(current_user)]`."""

    async def current_user(token: Optional[str] = Cookie(None, alias=COOKIE)) -> str:
        """Who is signed in; a 401 for anyone else. HTTP routes and the
        WebSocket depend on it alike."""
        user = await auth.user(token)
        if user is None:
            raise HTTPException(401, "sign in first")
        return user

    @app.post("/login")
    async def login(request: Request, password: str = Form("")) -> Response:
        """Sign in

        A form with a `password` field. Sets the session cookie and goes back
        to the app (303), with `?login=failed` if the password is wrong.
        """
        token = await auth.login(password)
        if token is None:
            return Response.redirect("/?login=failed", status=303)
        response = Response.redirect("/", status=303)
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

    @app.post("/logout")
    async def logout(request: Request, token: Optional[str] = Cookie(None, alias=COOKIE)) -> Response:
        """Sign out

        Ends the session on the server too, not just in the browser.
        """
        await auth.logout(token)
        response = Response.redirect("/", status=303)
        response.delete_cookie(COOKIE)
        return response

    @app.get("/api/me")
    async def me(request: Request, token: Optional[str] = Cookie(None, alias=COOKIE)) -> Response:
        """Who is signed in

        `{"user": "admin"}`, or `{"user": null}`. Open to everyone: the page
        asks it first, to show the app or the sign-in screen.
        """
        return Response.json({"user": await auth.user(token)})

    return current_user
