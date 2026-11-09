# Ready-made middleware

`korvex.middleware` has ready-made hooks. Each is a function that
registers one on the app:

```python
from korvex.middleware import compress, cors, rate_limit, request_id, security_headers

compress(app)  # first: it compresses what the others produced
cors(app, allow_origins=["https://example.com"])
request_id(app)
rate_limit(app, requests=100, per=60)
security_headers(app)
```

Behind a reverse proxy, let the proxy compress and add headers.

## CORS: `cors(app, allow_origins=...)`

Lets browsers call the app from pages on other sites (origins).

| Option | Default | What it does |
|---|---|---|
| `allow_origins` | (required) | The sites allowed, like `["https://example.com"]`, or `"*"` for any. |
| `allow_methods` | all of them | The methods those pages may use. |
| `allow_headers` | none | Request headers they may send beyond the basic ones, or `"*"`. |
| `allow_credentials` | `False` | Whether they may send cookies. |
| `expose_headers` | none | Response headers their code may read. |
| `max_age` | `600` | Seconds the browser may remember a preflight answer. |

A browser's preflight request (`OPTIONS` with
`Access-Control-Request-Method`) is answered before routing: 204 if
allowed, 400 if not. Every other response to an allowed origin gets the
CORS headers, errors included.

## Request IDs: `request_id(app)`

Gives each request an ID, sends it back in the response, and puts it in
your logs.

| Option | Default | What it does |
|---|---|---|
| `header` | `"X-Request-ID"` | The header the ID is read from and sent back in. |
| `trust_incoming` | `True` | Keep an ID the client or a proxy sent, when it's safe (letters, digits, `._:-`, up to 200). `False` always makes a new one. |

- `current_request_id()` returns it anywhere in your code, while the
  request runs.
- `RequestIdFilter` adds it to log records:

```python
import logging
from korvex.middleware import RequestIdFilter

handler = logging.StreamHandler()
handler.addFilter(RequestIdFilter())
handler.setFormatter(logging.Formatter("%(request_id)s %(message)s"))
```

## Rate limiting: `rate_limit(app, requests=100, per=60)`

Allows each client `requests` requests every `per` seconds; beyond, a 429
with `Retry-After`.

| Option | Default | What it does |
|---|---|---|
| `requests`, `per` | (required) | The allowance, like 100 per 60 seconds. |
| `key` | the client's address | Who the limit applies to: a function of the request, returning an API key, a user ID... or `None` to exempt the request (a health check). |
| `store` | a `MemoryStore` | Where the counts live: see below. |
| `headers` | `True` | Send `RateLimit-Limit`, `RateLimit-Remaining` and `RateLimit-Reset`, so clients can slow down. |

It counts WebSocket handshakes too. Requests can come in bursts of up to
`requests`, then one every `per / requests` seconds.

### Where the counts live

- `MemoryStore()`, the default: in each worker's memory. With 3 workers,
  a client gets up to 3 times the limit.
- `RedisStore("redis://localhost:6379/0")`: in Redis, shared by every
  worker and machine (`pip install korvex[redis]`). It takes a URL or a
  `redis.asyncio.Redis` you made yourself; `prefix=` sets what its keys
  start with. If Redis can't be reached, requests go through and the
  error is logged; `fail_open=False` refuses them instead.

### Your own store

A store (a `RateLimitStore`) is any object with a `hit()` method, sync
or async, that decides for one request:

```python
from korvex.middleware import Decision


class MyStore:
    async def hit(self, key, requests: int, per: float) -> Decision:
        ...
        return Decision(allowed=True, remaining=99, retry_after=0, reset=0.6)
```

`Decision` has `allowed`, `remaining` (requests left), `retry_after`
(seconds until the next one is allowed, 0 if allowed) and `reset`
(seconds until the whole allowance is back). The store decides rather
than just storing numbers, so a shared one can do it atomically.

## Compression: `compress(app)`

Compresses text-like responses for clients that accept it.

| Option | Default | What it does |
|---|---|---|
| `encodings` | `("zstd", "br", "gzip")` | In order of preference, among those the client accepts. `zstd` needs Python 3.14, `br` the `brotli` package (`pip install korvex[brotli]`); those not available are skipped. On free-threaded Python, `br` is left out of the default: the `brotli` package turns the GIL back on. |
| `minimum_size` | `500` | Smaller bodies are sent as they are. |
| `levels` | gzip 6, brotli 4, zstd 3 | Per encoding: `{"gzip": 9}`. |
| `content_types` | text, JSON, JavaScript, XML, SVG... | What's worth compressing: prefixes like `"text/"`, or suffixes like `"+json"`. |

- Streams are compressed piece by piece, each piece sent at once, so a
  live stream isn't held back.
- A response that already has a `Content-Encoding`, says
  `Cache-Control: no-transform`, or has no body is left alone.
- Don't compress a page that holds a secret (a CSRF token) next to
  something the client sent: an attacker could guess the secret (the
  BREACH attack).

## Security headers: `security_headers(app)`

Adds the headers that tell browsers to protect your pages. A response
that sets one itself keeps its own.

| Option | Default | What it does |
|---|---|---|
| `hsts` | one year | `Strict-Transport-Security`: browsers stick to HTTPS. Sent over HTTPS only. |
| `hsts_subdomains`, `hsts_preload` | `False` | Add those directives; only once every subdomain serves HTTPS. |
| `content_security_policy` | none | A CSP, like `"default-src 'self'"`. Off by default: the right one depends on your pages, and `/docs` loads Swagger UI from a CDN. |
| `frame_options` | `"DENY"` | `X-Frame-Options`: other sites can't show your pages in a frame. `"SAMEORIGIN"` allows your own. |
| `content_type_options` | `True` | `X-Content-Type-Options: nosniff`: browsers trust `Content-Type`. |
| `referrer_policy` | `"strict-origin-when-cross-origin"` | `Referrer-Policy`. |
| `cross_origin_opener_policy` | `"same-origin"` | `Cross-Origin-Opener-Policy`. |
| `permissions_policy` | none | `Permissions-Policy`, like `"camera=(), microphone=()"`. |

Each header option takes `None` to leave the header out.

All the details are in the [reference](reference/middleware.md).
