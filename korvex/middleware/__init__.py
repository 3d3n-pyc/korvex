"""Ready-made hooks (CORS, request IDs, rate limiting, compression, security headers, sessions, CSRF).

Guide: https://3d3n-pyc.github.io/korvex/middleware/
"""

from korvex.middleware._compression import compress
from korvex.middleware._cors import cors
from korvex.middleware._csrf import csrf
from korvex.middleware._rate_limit import Decision, MemoryStore, RateLimitStore, RedisStore, rate_limit
from korvex.middleware._request_id import RequestIdFilter, current_request_id, request_id
from korvex.middleware._security_headers import security_headers
from korvex.middleware._sessions import Session, current_session, flash, get_flashed_messages, sessions

__all__ = [
    "Decision",
    "MemoryStore",
    "RateLimitStore",
    "RedisStore",
    "RequestIdFilter",
    "Session",
    "compress",
    "cors",
    "csrf",
    "current_request_id",
    "current_session",
    "flash",
    "get_flashed_messages",
    "rate_limit",
    "request_id",
    "security_headers",
    "sessions",
]
