# Glossary

Short meanings for the words the guide uses, each with where Korvex covers it.

## ASGI

The standard way a Python web server talks to a web app: the server calls the app with a request and the app sends back a response. Because of it, one app can run on many servers.

In Korvex: an `App` is an ASGI app, so the built-in server, uvicorn, Granian or a serverless platform can run it. See [The built-in server](server.md).

## Handler

The function that answers a request: it gets what it asks for as parameters and returns the response. It can be sync (`def`) or async (`async def`).

In Korvex: `@app.get("/users")` above a function. See [Routes](routes.md) and [Requests and responses](requests-and-responses.md#what-a-handler-gets).

## Route

A rule that links a method (`GET`, `POST`...) and a URL path to a handler. Korvex picks the route that matches each request.

In Korvex: `@app.get`, `@app.post` and the like. See [Routes](routes.md).

## Path parameter

A changing part of the URL that the handler receives, like the `7` in `/users/7`.

In Korvex: `"/users/{id:int}"`, then `def get_user(id: int)`. See [Path parameters](routes.md#path-parameters).

## Query string

What follows the `?` in a URL, like `?page=2&tag=a`. It carries optional settings of the request.

In Korvex: a parameter of a simple type (`page: int = 1`) comes from it, and `request.query` reads it. See [Where a parameter comes from](validation.md#where-a-parameter-comes-from).

## Request body

The data a client sends after the headers, usually with `POST`, `PUT` or `PATCH`: JSON, a form or a file. It is read from the connection, so a very large one is limited.

In Korvex: a model parameter reads the JSON body, and `await request.body()`, `.json()` and `.form()` read it by hand. See [The request](requests-and-responses.md#the-request).

## Model

A class that describes the shape of some data (`name: str`, `age: int`). Korvex uses one to check an incoming body and to describe the API. It can be a msgspec struct, a pydantic model, a dataclass or a `TypedDict`. To choose: msgspec is the fastest, pydantic is the most common. Install one (`pip install msgspec` or `pip install pydantic`). Korvex needs neither, unless you validate bodies (a dataclass or `TypedDict` is checked by whichever is installed).

In Korvex: a `msgspec.Struct` or a pydantic `BaseModel` as a parameter or a return annotation. See [Validation](validation.md).

## Validation

Checking that incoming data is what you expect (a number where a number is due, a required field present) before your code runs. Without it, every handler must check by hand.

In Korvex: type annotations do it, and a wrong value gets a 422 error with no code of yours. See [Validation](validation.md) and its [Errors](validation.md#errors).

## Dependency

A function whose result a handler receives, for code that several routes share: who is signed in, a database connection.

In Korvex: `Depends(func)`. See [Dependencies](dependencies.md).

## Hook

A function Korvex calls at a set moment, so you can act on every request without editing each handler.

In Korvex: `@app.before_request`, `@app.after_request`, `@app.on_startup`, `@app.on_shutdown`. See [Errors and hooks](errors-and-hooks.md#hooks-around-every-request).

## Middleware

Code that sits between the server and your handlers and sees every request and response: logging, compression, rate limiting.

In Korvex: ready-made ones are functions like `cors(app, ...)` that register a hook on the app. See [Ready-made middleware](middleware.md).

## Lifespan

The life of the app from start to stop. Things you open once, like a database pool, are created at its start and closed at its end, not per request.

In Korvex: `@app.on_startup` and `@app.on_shutdown`; the state they keep goes in `app.local`. See [Startup and shutdown](errors-and-hooks.md#startup-and-shutdown).

## Event loop

The loop that runs async code: it switches between tasks whenever one waits (for the network, a database). A sync handler runs on the event loop too, so blocking code in it (a slow sync database call, `time.sleep`) holds up every other request in that worker. Use async libraries, or move the blocking call to a thread with `await asyncio.to_thread(...)`.

In Korvex: the built-in server runs one loop per worker, or per thread with `--threads`. See [Threads](server.md#threads).

## Worker

A process that runs your app and answers requests. Several workers share one port, so the app uses several CPU cores, and one that crashes is restarted.

In Korvex: `korvex run main:app --workers 3` or `app.run(workers=3)`. See [Options](server.md#options).

## Reverse proxy

A server (nginx, Caddy, Traefik) in front of your app. It takes the traffic from the internet, handles HTTPS, and forwards requests to the app.

In Korvex: run the built-in server behind one, and tell it which proxies to trust with `--forwarded-allow-ips`. See [Deployment](deployment.md#1-built-in-server-behind-a-reverse-proxy).

## root_path

The URL prefix an app is served under, like `/api`, when a proxy strips it before forwarding or when an app is mounted. The app needs it to build correct links.

In Korvex: `--root-path` on the server, `request.root_path` in a handler, and `app.mount(prefix, other_app)`. See [Options](server.md#options) and [Mounting another app](routes.md#mounting-another-app).

## CORS

Cross-Origin Resource Sharing: browsers block a page on one site from calling the API of another unless that API says it may. It is a browser rule; curl and other servers are not affected.

In Korvex: `cors(app, allow_origins=[...])`. See [CORS](middleware.md#cors-corsapp-allow_origins).

## CSRF

Cross-site request forgery: a page on another site makes the visitor's browser send a request to yours, with the visitor's cookies. It matters for any app that signs users in with a cookie.

In Korvex: `csrf(app)` refuses such requests with a 403. See [CSRF](sessions.md#csrf-csrfapp).

## Session

Data kept for one visitor between requests, such as who is signed in. The browser holds a cookie that links its requests together.

In Korvex: `sessions(app, secret_key=...)` stores it in a signed cookie, and `current_session()` reads it. See [What a session is](sessions.md#what-a-session-is).

## ETag

A label (a fingerprint) the server puts on a file's content. The browser sends it back, and if the file is unchanged the server answers 304 with no body, which saves bandwidth.

In Korvex: static files get an `ETag` and a `Last-Modified` on their own. See [Static files](routes.md#static-files).

## Range request

A request for only part of a file (`Range: bytes=0-999`). It allows resumable downloads and seeking in a video.

In Korvex: static files and `Response.file(path, request=request)` answer it with a 206. See [Static files](routes.md#static-files).

## WebSocket

A connection that stays open so the client and the server can send messages to each other at any time, like a chat.

In Korvex: `@app.websocket("/chat")` and a `WebSocket` parameter. See [WebSockets](websockets.md).

## Server-Sent Events

A stream of events from the server to the browser over one HTTP response, one way only. Simpler than a WebSocket when the client only listens.

In Korvex: `Response.sse(events())`, with a generator that yields `ServerSentEvent`s. See [Server-Sent Events](requests-and-responses.md#server-sent-events).

## Background task

Work started during a request but run after the response is sent, such as sending an email, so the client does not wait for it.

In Korvex: `tasks.add(func, ...)`; a coroutine function is awaited and a plain function runs in a thread. See [Background tasks](requests-and-responses.md#background-tasks).
