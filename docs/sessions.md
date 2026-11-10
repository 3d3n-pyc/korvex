# Sessions and CSRF

`sessions(app, ...)` keeps a small dict per browser in a signed cookie.
`csrf(app)` refuses cross-site requests that change things. No store, no
extra package, no tokens in your forms.

```python
import os

from korvex import App, Depends, Form, HTTPException, Response
from korvex.middleware import csrf, current_session, flash, sessions

app = App()
sessions(app, secret_key=os.environ["SECRET_KEY"])
csrf(app)


@app.post("/login")
def login(username: str = Form(), password: str = Form()):
    user = check_password(username, password)  # yours: returns the user or None
    if user is None:
        flash("Wrong name or password", "error")
        return Response.redirect("/login", status=303)
    session = current_session()
    session.clear()  # never keep what an anonymous visitor had (session fixation)
    session["user_id"] = user.id
    flash(f"Welcome, {user.name}", "success")
    return Response.redirect("/", status=303)


def current_user() -> int:
    user_id = current_session().get("user_id")
    if user_id is None:
        raise HTTPException(401, "log in first")
    return user_id


@app.get("/account")
def account(user_id: int = Depends(current_user)):
    return {"user_id": user_id}


@app.post("/logout")
def logout():
    current_session().clear()
    return Response.redirect("/", status=303)
```

Make a key once and keep it out of the code:

```sh
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

A key must be at least 32 bytes, or `sessions()` raises `ValueError`. To
rotate, pass a list with the new key first: it signs, and every key in the
list is still accepted when checking. Drop the old key once its sessions
have expired.

```python
sessions(app, secret_key=[os.environ["NEW_KEY"], os.environ["OLD_KEY"]])
```

## What a session is

`current_session()` returns a `Session`: a dict stored in a signed cookie.

- The browser can read it, as it is not encrypted. Put no secret in it.
  It cannot change it: a cookie that was edited, or signed with another
  key, gives an empty session.
- Values must be JSON: strings, numbers, booleans, `None`, lists, dicts.
- The signed cookie may be 4096 bytes. A bigger session raises `ValueError`.
  Keep ids in it, not data.
- The cookie is sent only when the session was read and changed. Setting,
  deleting or clearing a key counts. A change inside a nested value does
  not, so set `session.modified = True` after it.
- `max_age` counts from the last change, not from the last visit. A
  session that is only read is not renewed.
- Clearing a session that came with a cookie deletes the cookie.

```python
cart = session.setdefault("cart", [])
cart.append(3)
session.modified = True
```

| Option | Default | What it does |
|---|---|---|
| `secret_key` | (required) | A key, or a list of keys (new first). 32 bytes at least. |
| `max_age` | 14 days | Seconds a session stays valid. |
| `cookie` | `"session"` | The cookie's name. |
| `secure` | `True` | Send the cookie over HTTPS only. |
| `samesite` | `"lax"` | The cookie's `SameSite` attribute. |
| `path` | `"/"` | The cookie's path. |
| `domain` | none | The cookie's domain. Unset: the exact host only. |

The cookie is always `HttpOnly`: scripts on the page cannot read it. With
`secure=True`, browsers do not send it over plain `http://`, except to
`localhost`. Use `secure=False` only for a local setup that is not
`localhost`.

`sessions()` can be called once per app. `current_session()` raises
`RuntimeError` outside a request, or when `sessions()` was not called.

## Limits to know

- **A session cannot be revoked.** The cookie is the whole session. A copy
  of it stays valid until it expires, even after the user logged out.
  To be able to cancel sessions, keep something on the server and check it:
  for example a version number per user, stored in the session at login
  and compared in `current_user`. Raise the number to log that user out
  everywhere.
- **In a WebSocket handler the session is read-only.** `current_session()`
  reads the cookie of the handshake. A change is lost, as the cookie can
  not be sent after the handshake.
- **Register order does not matter.** `sessions()` always runs first among
  the before hooks, so a `csrf()` or `rate_limit()` refusal handler can
  read the session too.
- **A streamed response cannot change the session.** Its headers are sent
  before the body: a stream can read the session, but a change made while
  it is being sent is not saved.

## Flash messages

`flash(message, category="info")` keeps a message in the session. The next
page shows it once.

```python
flash("Saved", "success")
```

In a template made with `app.templates()`, `get_flashed_messages()` is
available. It returns `(category, message)` pairs and forgets them:

```html
{% for category, message in get_flashed_messages() %}
  <p class="flash {{ category }}">{{ message }}</p>
{% endfor %}
```

In Python, import `get_flashed_messages` from `korvex.middleware`. Both
`flash()` and `get_flashed_messages()` change the session, so they need
`sessions(app, ...)`.

## CSRF: `csrf(app)`

A cross-site request forgery is a page on another site that makes the
visitor's browser send a `POST` to yours, with your cookies. `csrf(app)`
refuses it with a 403. There is nothing to add to your forms or `fetch`
calls.

It uses what the browser sends, in this order:

1. `Sec-Fetch-Site` is present: `same-origin` and `none` (the user typed
   the address) pass. Anything else, `same-site` included, is refused,
   unless the `Origin` is in `trusted_origins`.
2. Else `Origin` is present: it passes if it equals the request's `Host`
   (scheme aside), or is in `trusted_origins`.
3. Neither header: the client is not a browser (curl, a script, a mobile
   app), so it passes. A browser always sends one of them on a request
   that changes something.

`GET`, `HEAD` and `OPTIONS` always pass. WebSocket handshakes are checked
too, as a page on another site can open one with the visitor's cookies.

There is no token because every current browser sends these headers. A
very old browser sends neither, and is let through.

| Option | What it does |
|---|---|
| `trusted_origins` | Other origins allowed, like `["https://app.example.com"]` for a separate front-end. Each is `scheme://host[:port]`, no path. |
| `exempt` | Paths not checked. An exact path (`"/hook"`) or, ending in `/`, a prefix (`"/webhooks/"`). |

```python
csrf(
    app,
    trusted_origins=["https://app.example.com"],
    exempt=["/webhooks/"],  # these check their own signature
)
```

A bad entry raises `ValueError` when `csrf()` is called.

Behind a reverse proxy, `Origin` is compared with the `Host` header as the
app receives it. If the proxy replaces `Host` (with `127.0.0.1:8000`, for
example), every form post is refused. Keep the public `Host` (nginx:
`proxy_set_header Host $host;`; Caddy does by default), or list the public
origin in `trusted_origins`.

`csrf()` and `cors()` are separate. A cross-origin front-end allowed by
`cors()` must also be listed in `csrf(trusted_origins=...)`. Without a
`Sec-Fetch-Site` header, the `Origin` check compares hosts, not schemes
(as Go does).

## Testing

`TestClient` keeps cookies, so a session lasts across the requests of one
client. It also sends `Secure` cookies back to `http://testserver` and to
`localhost`, as browsers do, so the default `secure=True` works in tests.

```python
from korvex.testing import TestClient

client = TestClient(app)
client.post("/login", form={"username": "ada", "password": "pw"})
assert client.get("/account").status == 200
```

To test CSRF, send the header a browser would send from another site:

```python
r = client.post("/logout", headers={"Sec-Fetch-Site": "cross-site"})
assert r.status == 403
```
