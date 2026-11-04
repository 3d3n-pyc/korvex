# Deploying a Korvex app

`App` is a standard ASGI app, so anything that runs ASGI apps runs it.
The files below are in [`examples/deploy/`](../examples/deploy/), around a
small [`main.py`](../examples/deploy/main.py).

| Setup | When | Checked |
|---|---|---|
| [uvicorn behind a reverse proxy](#1-uvicorn-behind-a-reverse-proxy) | Your own server or VM | Yes, behind Caddy and nginx, in containers |
| [Docker](#2-docker) | Fly.io, Railway, Render, Cloud Run, Kubernetes... | Yes, image built and run |
| [AWS Lambda](#3-aws-lambda) | Serverless on AWS | Locally: handler called with an API Gateway event, not on AWS |
| [Vercel](#4-vercel) | Serverless on Vercel | No, not deployed yet |

## 1. uvicorn behind a reverse proxy

The recommended setup on your own server. The proxy handles HTTPS,
HTTP/2 and HTTP/3, slow clients and size limits, then forwards each
request to uvicorn in plain HTTP/1.1.

```sh
pip install "korvex[serve]"
uvicorn main:app --host 127.0.0.1 --port 8000 --workers 4 \
    --proxy-headers --forwarded-allow-ips 127.0.0.1
```

- `--workers`: one per CPU core. Each worker is a separate process.
- `--proxy-headers` with `--forwarded-allow-ips`: uvicorn trusts the
  proxy's `X-Forwarded-*` headers, so its logs show the client's real
  address. The headers also reach your handlers, in `request.headers`.
- Listen on `127.0.0.1`, not `0.0.0.0`, so only the proxy can reach the app.

With [Caddy](../examples/deploy/Caddyfile), which gets and renews the
HTTPS certificate by itself:

```
example.com {
	reverse_proxy 127.0.0.1:8000
}
```

With [nginx](../examples/deploy/nginx.conf) (add a certificate, for
example with certbot, to serve HTTPS):

```nginx
server {
    listen 80;
    server_name example.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Keep uvicorn running with systemd, or use Docker (next section).

## 2. Docker

```dockerfile
FROM python:3.14-slim
WORKDIR /app

# Korvex ships prebuilt wheels: no Rust toolchain needed.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

`requirements.txt` contains `korvex[serve]`. Wheels exist for Linux
x86_64 and ARM64, so the image builds on both.

Hosting platforms (Fly.io, Railway, Render, Cloud Run...) put their own
proxy in front and handle HTTPS. There, run one worker per container and
let the platform add containers when traffic grows. On your own server,
put Caddy or nginx in front as in section 1.

## 3. AWS Lambda

[Mangum](https://pypi.org/project/mangum/) turns Lambda events into ASGI
calls. [`lambda_handler.py`](../examples/deploy/lambda_handler.py):

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

## 4. Vercel

Not deployed yet, so these steps follow Vercel's documentation for
Python ASGI apps and haven't been checked:

- Put the app in `main.py` (or `app.py`) as a top-level `app` variable,
  and list `korvex` in `requirements.txt`.
- If Vercel doesn't find it, point to it in `pyproject.toml`:

  ```toml
  [tool.vercel]
  entrypoint = "main:app"
  ```

Vercel runs the app through its own runtime, as ASGI, so the server
options above don't apply. Expect the lookup cache to start empty on
each new instance. If you deploy it, please report how it went.
