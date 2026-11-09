"""Ready-made hooks (CORS, request IDs, rate limiting, compression, security headers).

Guide: https://3d3n-pyc.github.io/korvex/middleware/
"""

from korvex.middleware._compression import compress
from korvex.middleware._cors import cors
from korvex.middleware._rate_limit import Decision, MemoryStore, RateLimitStore, RedisStore, rate_limit
from korvex.middleware._request_id import RequestIdFilter, current_request_id, request_id
from korvex.middleware._security_headers import security_headers

__all__ = [
    "Decision",
    "MemoryStore",
    "RateLimitStore",
    "RedisStore",
    "RequestIdFilter",
    "compress",
    "cors",
    "current_request_id",
    "rate_limit",
    "request_id",
    "security_headers",
]
