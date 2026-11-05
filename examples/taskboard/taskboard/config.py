"""Settings, read once from TASKBOARD_* environment variables.

Every setting has a default that works on a laptop; production sets at
least TASKBOARD_ADMIN_PASSWORD and TASKBOARD_SECURE_COOKIES.
"""

import os
from collections.abc import Mapping
from pathlib import Path

import msgspec

PREFIX = "TASKBOARD_"


class Settings(msgspec.Struct, frozen=True, kw_only=True):
    data_dir: str = "data"
    """Where the database goes."""
    admin_password: str = "korvex"
    session_hours: int = 8
    secure_cookies: bool = False
    """Send the session cookie over HTTPS only. Turn on behind HTTPS."""
    cors_origins: list[str] = []
    """Other sites allowed to call the API from a browser."""
    rate_limit: int = 600
    """Requests per minute and client address."""
    host: str = "127.0.0.1"
    port: int = 8000
    workers: int = 1

    @property
    def database(self) -> Path:
        return Path(self.data_dir) / "taskboard.db"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] = os.environ) -> "Settings":
        """TASKBOARD_PORT=9000 sets `port`, and so on. Values are checked:
        a bad one stops the app at startup with a clear message."""
        values: dict[str, object] = {}
        for field in cls.__struct_fields__:
            raw = environ.get(PREFIX + field.upper())
            if raw is None:
                continue
            values[field] = [item.strip() for item in raw.split(",") if item.strip()] if field == "cors_origins" else raw
        try:
            return msgspec.convert(values, cls, strict=False)  # strict=False: "9000" -> 9000, "true" -> True
        except msgspec.ValidationError as error:
            raise SystemExit(f"invalid setting: {error} (from {PREFIX}* environment variables)") from None
