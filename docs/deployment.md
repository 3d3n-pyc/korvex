# Deploying a Korvex app

An `App` runs on Korvex's built-in server (Linux and macOS) or on any
ASGI server, including serverless platforms. The files below are in
[`examples/deploy/`](https://github.com/3d3n-pyc/korvex/tree/main/examples/deploy), around a small
[`main.py`](https://github.com/3d3n-pyc/korvex/blob/main/examples/deploy/main.py).

| Setup | When | Checked |
|---|---|---|
| [Built-in server behind a reverse proxy](#1-built-in-server-behind-a-reverse-proxy) | Your own server or VM, Linux or macOS | Yes, behind Caddy and nginx over a Unix socket, in containers, WebSockets included |
| [uvicorn behind a reverse proxy](#2-uvicorn-behind-a-reverse-proxy) | The same, or Windows | Yes, behind Caddy and nginx, in containers |
| [Docker](#3-docker) | Fly.io, Railway, Render, Cloud Run, Kubernetes... | Yes, image built and run, stops cleanly on SIGTERM |
| [AWS Lambda](#4-aws-lambda) | Serverless on AWS | Locally: handler called with an API Gateway event, not on AWS |
| [Vercel](#5-vercel) | Serverless on Vercel | Yes, deployed and tested |

In every setup with your own server, put a reverse proxy in front: it
handles HTTPS, HTTP/2 and HTTP/3, then forwards each request to Korvex in
plain HTTP/1.1.

## 1. Built-in server behind a reverse proxy

The fastest setup (see the server comparison in the README). With the
proxy on the same machine, a Unix socket is the simplest link:

```sh
pip install korvex
korvex run main:app --workers 3 --uds /run/korvex/korvex.sock
```

- `--workers`: about 3 per 4 CPU cores. Each worker is a separate
  process, restarted if it crashes.
- `--uds`: only processes on the machine can reach the app. Korvex makes
  the socket file writable by every user, as uvicorn does, so a proxy
  running as another user can connect; restrict access with the
  permissions of its directory (`/run/korvex` here). Keep the path short:
  macOS allows 104 characters.
- Requests coming through the socket are trusted, so `request.client` and
  `request.scheme` are the client's, from the proxy's `X-Forwarded-For`
  and `X-Forwarded-Proto`. The `Host` header is the one nginx sets
  (`proxy_set_header Host $host`) or Caddy passes on. `--forwarded-host`
  would read `X-Forwarded-Host` instead: only for a proxy that sets it,
  which this nginx configuration doesn't (it would pass a client's on).
- WebSockets get a ping every 20 seconds (`--ws-ping-interval`), which also
  keeps them open through nginx's 60-second `proxy_read_timeout`; a client
  that stopped answering is closed.
- On SIGTERM (systemd, Docker, Kubernetes), requests in progress get up
  to 30 seconds to finish (`--graceful-timeout`).
- To listen on TCP instead, use `--host 127.0.0.1 --port 8000` with the
  proxy configurations of section 2.

With [Caddy](https://github.com/3d3n-pyc/korvex/blob/main/examples/deploy/Caddyfile-unix):

```
example.com {
	reverse_proxy unix//run/korvex/korvex.sock
}
```

With [nginx](https://github.com/3d3n-pyc/korvex/blob/main/examples/deploy/nginx-unix.conf):

```nginx
# Passes WebSocket upgrades on, and keeps plain requests as they were.
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}

upstream korvex {
    server unix:/run/korvex/korvex.sock;
}

server {
    listen 80;
    server_name example.com;

    location / {
        proxy_pass http://korvex;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Caddy passes WebSockets on by itself. nginx needs the `map` and the
`Upgrade` / `Connection` headers above; without them, WebSocket
connections reach the app as plain requests and fail.

Keep `korvex run` running with systemd, or use Docker (section 3).

## 2. uvicorn behind a reverse proxy

On Windows, where the built-in server isn't available, or to keep an
ASGI server:

```sh
pip install "korvex[serve]"
uvicorn main:app --host 127.0.0.1 --port 8000 --workers 4 \
    --proxy-headers --forwarded-allow-ips 127.0.0.1
```

- `--workers`: one per CPU core. Each worker is a separate process.
- `--proxy-headers` with `--forwarded-allow-ips`: uvicorn trusts the
  proxy's `X-Forwarded-*` headers, so `request.client` and
  `request.scheme` are the client's, and its logs show the real address.
- Listen on `127.0.0.1`, not `0.0.0.0`, so only the proxy can reach the app.

With [Caddy](https://github.com/3d3n-pyc/korvex/blob/main/examples/deploy/Caddyfile), which gets and renews the
HTTPS certificate by itself:

```
example.com {
	reverse_proxy 127.0.0.1:8000
}
```

With [nginx](https://github.com/3d3n-pyc/korvex/blob/main/examples/deploy/nginx.conf) (add a certificate, for
example with certbot, to serve HTTPS):

```nginx
# Passes WebSocket upgrades on, and keeps plain requests as they were.
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}

server {
    listen 80;
    server_name example.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## 3. Docker

```dockerfile
FROM python:3.14-slim
WORKDIR /app

# Korvex ships prebuilt wheels: no Rust toolchain needed.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

# Run as an unprivileged user, not root.
RUN useradd --no-create-home --shell /usr/sbin/nologin app
USER app

EXPOSE 8000
CMD ["korvex", "run", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

`requirements.txt` contains `korvex`. Wheels exist for Linux x86_64 and
ARM64, so the image builds on both.

- Set `--workers` to about 3/4 of the container's CPUs (1 for a single
  CPU, the default).
- `docker stop` sends SIGTERM: requests in progress finish, then the
  container exits (in 0.1 s when idle, in our test).

Hosting platforms (Fly.io, Railway, Render, Cloud Run...) put their own
proxy in front and handle HTTPS, and add containers when traffic grows.
On your own server, put Caddy or nginx in front as in section 1.

## 4. AWS Lambda

[Mangum](https://pypi.org/project/mangum/) turns Lambda events into ASGI
calls. [`lambda_handler.py`](https://github.com/3d3n-pyc/korvex/blob/main/examples/deploy/lambda_handler.py):

```python
from mangum import Mangum

from main import app

handler = Mangum(app)
```

Package `korvex` and `mangum` with your code, set the Lambda handler to
`lambda_handler.handler`, and put an API Gateway (HTTP API) or a function
URL in front.

**Startup hooks run on every request here.** Mangum replays the startup
and shutdown events around each Lambda call, so `@app.on_startup` and
`@app.on_shutdown` run every time. Don't open expensive resources there
(a database pool, for example): create them once at module level, or on
first use, so they're reused while the Lambda instance stays warm.

## 5. Vercel

Vercel finds the app by itself: put it in `main.py` (or `app.py`) as a
top-level `app` variable, and list `korvex` in `requirements.txt`
(`examples/deploy/main.py` follows this layout). Then deploy:

```sh
npx vercel deploy --prod
```

If Vercel doesn't find the app, point to it in `pyproject.toml`:

```toml
[tool.vercel]
entrypoint = "main:app"
```

Vercel runs the app through its own runtime, as ASGI, so the server
options above don't apply. What was checked on a real deployment:

- Typed path parameters, cookies, forms (urlencoded and multipart, with
  a file), streaming, `HTTPException`, 404 and 405, and `HEAD` all behave
  as they do locally.
- **Startup hooks run once per instance**, not on every request (unlike
  AWS Lambda with Mangum): 20 requests in a row saw a single startup. So
  opening a database pool in `@app.on_startup` is fine here.
- Each new instance starts with an empty lookup cache.

## Running in production

Four things that work in a quick test and break under real traffic.

### Keep-alive timeouts

Korvex closes a connection that sits idle for `--read-timeout` (30 seconds
by default). If the proxy keeps idle connections to Korvex open longer, it
can send a request on one that Korvex just closed, and the client gets a
sporadic 502. The rule: **the proxy must close idle connections before
Korvex does.**

- **nginx.** The configurations above send `Connection: close` for plain
  requests, so nginx opens a new connection each time and this can't
  happen. If you turn on upstream keepalive to save those connections,
  set `keepalive_timeout` below 30 seconds:

  ```nginx
  map $http_upgrade $connection_upgrade {
      default upgrade;
      ''      '';       # not "close": lets nginx reuse the connection
  }

  upstream korvex {
      server 127.0.0.1:8000;
      keepalive 32;
      keepalive_timeout 25s;
  }
  ```

  Keep `proxy_http_version 1.1;` and the `Connection $connection_upgrade`
  header in the `location` block, as above.

- **Caddy.** It keeps idle upstream connections for 2 minutes. Lower it:

  ```
  example.com {
  	reverse_proxy 127.0.0.1:8000 {
  		transport http {
  			keepalive 25s
  		}
  	}
  }
  ```

- **AWS Application Load Balancer.** Its idle timeout is 60 seconds by
  default. Either set it below 30 seconds, or start Korvex with
  `--read-timeout 65` or more. The second choice also gives slow clients
  longer to send their request.

### Kubernetes and containers

On SIGTERM, Korvex stops accepting connections at once, then gives
requests in progress up to `--graceful-timeout` (30 seconds) to finish.
Kubernetes removes a pod from its Service at about the same moment, so
the load balancer may still send it a few requests. A short `preStop`
sleep keeps the pod serving until the balancer has caught up, and the
grace period must cover the sleep and the graceful timeout:

```yaml
spec:
  terminationGracePeriodSeconds: 45   # preStop 5 + graceful timeout 30 + margin
  containers:
    - name: app
      image: example/app:latest
      lifecycle:
        preStop:
          exec:
            command: ["sleep", "5"]
      readinessProbe:
        httpGet:
          path: /health
          port: 8000
```

The `sleep` command must exist in the image (`python:slim` has it).

### A health route

Proxies and orchestrators need a URL that answers 200 when the app is up:

```python
@app.get("/health", include_in_schema=False)
def health():
    return Response.json({"ok": True})
```

### File descriptors

Each open connection, WebSockets included, uses one file descriptor, and
the default limit is often 1024 per process. Past it, new connections fail.
Raise it if you expect many clients at once:

- systemd: `LimitNOFILE=65536` in the `[Service]` section.
- Docker: `docker run --ulimit nofile=65536:65536 ...`.
