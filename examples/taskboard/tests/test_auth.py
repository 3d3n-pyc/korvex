"""Signing in, and what it guards."""

import pytest
from conftest import PASSWORD
from korvex import WebSocketDisconnect
from korvex.testing import TestClient

from taskboard.app import create_app
from taskboard.config import Settings


def test_the_api_needs_a_session(anonymous):
    for method, path in [("GET", "/api/lists"), ("GET", "/api/tasks"), ("POST", "/api/lists"), ("GET", "/api/export")]:
        response = anonymous.request(method, path)
        assert response.status == 401 and response.json()["error"] == "sign in first"
    assert anonymous.get("/api/me").json() == {"user": None}  # open: the page asks it first


def test_live_updates_need_a_session(anonymous):
    with pytest.raises(WebSocketDisconnect):  # refused: closed before being accepted
        with anonymous.websocket("/live"):
            pass


def test_wrong_password(anonymous):
    response = anonymous.post("/login", form={"password": "guess"})
    assert (response.status, response.headers["location"]) == (303, "/?login=failed")
    assert "session" not in anonymous.cookies


def test_sign_in_and_out(anonymous):
    response = anonymous.post("/login", form={"password": PASSWORD})
    assert response.status == 303 and response.headers["location"] == "/"
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Secure" not in cookie
    assert anonymous.get("/api/me").json() == {"user": "admin"}
    assert anonymous.get("/api/lists").status == 200

    token = anonymous.cookies["session"]
    anonymous.post("/logout")
    assert anonymous.get("/api/me").json() == {"user": None}
    anonymous.cookies["session"] = token  # an old cookie, replayed
    assert anonymous.get("/api/lists").status == 401  # the session ended on the server too


def test_sessions_survive_a_restart(settings):
    with TestClient(create_app(settings)) as first:
        first.post("/login", form={"password": PASSWORD})
        token = first.cookies["session"]
    with TestClient(create_app(settings)) as second:
        second.cookies["session"] = token
        assert second.get("/api/lists").status == 200


def test_secure_cookies_behind_https(tmp_path):
    settings = Settings(data_dir=str(tmp_path), admin_password=PASSWORD, secure_cookies=True)
    with TestClient(create_app(settings)) as client:
        assert "Secure" in client.post("/login", form={"password": PASSWORD}).headers["set-cookie"]
