# Ready-made middleware

`korvex.middleware` has hooks for common jobs. Each is a function that
registers them on the app:

```python
from korvex.middleware import compress, cors, rate_limit, request_id, security_headers

compress(app)  # first: it compresses what the others produced
cors(app, allow_origins=["https://example.com"])
request_id(app)
rate_limit(app, requests=100, per=60)
security_headers(app)
```

Behind a reverse proxy, let the proxy compress and add headers: it does
both well, and the app does less.

## `cors(app, allow_origins=...)`

Lets browsers call the app from other origins. Options:
`allow_methods`, `allow_headers`, `allow_credentials=False`,
`expose_headers`, `max_age=600`. Preflight requests are answered before
routing, and every response to an allowed origin gets the headers,
errors included.

## `request_id(app)`

Gives each request an ID (the incoming `X-Request-ID` if it's safe, a new
one otherwise), and sends it back. `current_request_id()` returns it
anywhere in your code, and `RequestIdFilter` adds it to log records as
`%(request_id)s`.

## `rate_limit(app, requests=100, per=60)`

Allows each client that many requests per period; beyond, a 429 with
`Retry-After`. Responses carry `RateLimit-Limit`, `RateLimit-Remaining`
and `RateLimit-Reset`.

- `key=` counts by something else than the client's address: an API key,
  a user ID.
- Counters live in each worker's memory, so with 3 workers a client gets
  up to 3 times the limit. `store=RedisStore("redis://...")` shares them
  between workers and machines (`pip install korvex[redis]`). If Redis
  can't be reached, requests go through and the error is logged;
  `fail_open=False` refuses them instead.
- It counts WebSocket handshakes too.

## `compress(app)`

Compresses text-like responses over 500 bytes with zstd (Python 3.14),
brotli (`pip install korvex[brotli]`) or gzip, whichever the client
prefers. Streams are compressed piece by piece, each piece sent at once.
Don't compress pages that hold a secret next to something the client
sent (the BREACH attack).

## `security_headers(app)`

Adds `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`,
`Referrer-Policy`, `Cross-Origin-Opener-Policy`, and
`Strict-Transport-Security` on HTTPS. `content_security_policy=` adds a
CSP, off by default: the right one depends on your pages. A response that
sets one of these headers keeps its own.
