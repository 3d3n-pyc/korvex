"""Signing in: one admin account, its password from the settings.

Sessions live in the database, so they survive restarts and every worker
sees them. Only a hash of each token is stored: someone who reads the
database can't sign in with what they find there.
"""

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from taskboard.repositories import SessionRepository

log = logging.getLogger(__name__)

ADMIN = "admin"


class AuthService:
    def __init__(self, sessions: SessionRepository, password: str, lifetime: timedelta) -> None:
        self.sessions = sessions
        self._password = password.encode()
        self.lifetime = lifetime

    async def login(self, password: str) -> Optional[str]:
        """A new session token if the password is right, else None."""
        if not secrets.compare_digest(password.encode(), self._password):  # same time whatever matches
            log.warning("failed sign-in")
            return None
        now = datetime.now(timezone.utc)
        await self.sessions.delete_expired(now)
        token = secrets.token_urlsafe(32)
        await self.sessions.add(_hash(token), ADMIN, now + self.lifetime)
        log.info("%s signed in", ADMIN)
        return token

    async def user(self, token: Optional[str]) -> Optional[str]:
        """Who the token belongs to, if it's a live session."""
        if not token:
            return None
        return await self.sessions.user(_hash(token), datetime.now(timezone.utc))

    async def logout(self, token: Optional[str]) -> None:
        if token:
            await self.sessions.delete(_hash(token))


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
