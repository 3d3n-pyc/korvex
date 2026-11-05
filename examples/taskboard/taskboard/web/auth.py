"""Signing in and out with a regular HTML form and a session cookie, and
the "session" middleware that guards the API."""

from korvex import App, HTTPException, Request, Response

from taskboard.config import Settings
from taskboard.services.auth import AuthService

COOKIE = "session"


def register(app: App, auth: AuthService, settings: Settings) -> None:
    @app.middleware("session")
    async def require_session(request: Request) -> None:
        if await auth.user(request.cookies.get(COOKIE)) is None:
            raise HTTPException(401, "sign in first")

    @app.post("/login")
    async def login(request: Request) -> Response:
        """Sign in

        A form with a `password` field. Sets the session cookie and goes back
        to the app (303), with `?login=failed` if the password is wrong.
        """
        password = (await request.form()).get("password", [""])[0]
        token = await auth.login(password) if isinstance(password, str) else None
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
    async def logout(request: Request) -> Response:
        """Sign out

        Ends the session on the server too, not just in the browser.
        """
        await auth.logout(request.cookies.get(COOKIE))
        response = Response.redirect("/", status=303)
        response.delete_cookie(COOKIE)
        return response

    @app.get("/api/me")
    async def me(request: Request) -> Response:
        """Who is signed in

        `{"user": "admin"}`, or `{"user": null}`. Open to everyone: the page
        asks it first, to show the app or the sign-in screen.
        """
        return Response.json({"user": await auth.user(request.cookies.get(COOKIE))})
