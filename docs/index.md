# Korvex

A fast Python web framework, with a router and an HTTP server written in
Rust. You write plain Python functions; Korvex calls them for the right
requests, checks their input, and documents your API. Use it for JSON
APIs, HTML sites and WebSockets where speed matters and plain functions are
enough.

New to web APIs? Start with the [tutorial](tutorial.md).

```sh
pip install --pre korvex
```

## A first app

```python
from korvex import App, Response

app = App()


@app.get("/hello/{name}")
def hello(name: str, polite: bool = False):
    return Response.json({"message": f"Good day, {name}" if polite else f"Hi {name}"})


if __name__ == "__main__":
    app.run()
```

Run `python main.py`, then open <http://127.0.0.1:8000/hello/Ada?polite=true>.

- `name` comes from the path, `polite` from the query string, turned into
  a `bool`: a parameter's name and type say where it comes from and what
  it must be.
- A wrong `polite` (`?polite=maybe`) gets a 422 error, with no code of yours.
- `Response.json()` sends JSON. With a model (msgspec or pydantic) and a
  return annotation, return the model: see [Validation](validation.md).
- `app.docs()` adds a Swagger UI page at `/docs`.

Korvex 1.0 is in release candidates: what rc1 froze won't break; features
added since can still change until 1.0.0. Install with
`pip install --pre korvex`. Every change is in the [changelog](https://github.com/3d3n-pyc/korvex/blob/main/CHANGELOG.md).

## The guide

Read in order the first time; each page stands alone after.

1. [Routes](routes.md): paths, parameters, groups, hosts, links between routes, static files.
2. [Validation](validation.md): typed parameters, models, headers, cookies and forms.
3. [Requests and responses](requests-and-responses.md): what a handler gets and returns, streams, Server-Sent Events, background tasks.
4. [Templates](templates.md): HTML pages, filters, escaping, hashed static URLs.
5. [Sessions and CSRF](sessions.md): signed-cookie sessions, flash messages, CSRF protection.
6. [Dependencies](dependencies.md): code shared by routes, like signing in.
7. [Errors and hooks](errors-and-hooks.md): errors, code that runs around every request, startup and shutdown.
8. [Ready-made middleware](middleware.md): CORS, request IDs, rate limiting, compression, security headers.
9. [WebSockets](websockets.md)
10. [OpenAPI and security](openapi.md): the API's description, Swagger UI, sign-in schemes.
11. [Testing](testing.md)
12. [The built-in server](server.md): running in production, workers and threads.
13. [Deployment](deployment.md): behind nginx or Caddy, in Docker, on AWS Lambda or Vercel.
14. [Performance](performance.md): the benchmarks, and how to reproduce them.

Terms you don't know are in the [glossary](glossary.md).

The [reference](reference/app.md) lists every class, function and option,
from the code's own docstrings.

Also:

- [The standalone router](router.md): Korvex's Rust router on its own.
- [Examples](https://github.com/3d3n-pyc/korvex/tree/main/examples): from
  a few lines to a complete app.
