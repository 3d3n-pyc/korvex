# Korvex

A fast Python web framework, with a router and an HTTP server written in
Rust. You write plain Python functions; Korvex calls them for the right
requests, checks what comes in, and documents your API.

```sh
pip install korvex
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

- `name` comes from the path, `polite` from the query string, already
  turned into a `bool`: a parameter's name and type say where it comes
  from and what it must be.
- A wrong `polite` (`?polite=maybe`) gets a clear 422 error, without any
  code of yours.
- `Response.json()` sends JSON. With a model (msgspec or pydantic) and a
  return annotation, returning the model is enough: see
  [Validation](validation.md).
- `app.docs()` would add an interactive page at `/docs`, listing every
  route.

Korvex is young (alpha): the API can still change before 1.0, each change
listed in the [changelog](https://github.com/3d3n-pyc/korvex/blob/main/CHANGELOG.md)
with how to update.

## The guide

Read in order the first time; each page stands alone afterwards.

1. [Routes](routes.md): paths, parameters, groups, hosts, links between routes, static files.
2. [Requests and responses](requests-and-responses.md): what a handler gets and returns.
3. [Validation](validation.md): typed parameters, models, headers, cookies and forms.
4. [Dependencies](dependencies.md): code shared by routes, like signing in.
5. [Errors and hooks](errors-and-hooks.md): errors, code that runs around every request, startup and shutdown.
6. [WebSockets](websockets.md)
7. [OpenAPI and security](openapi.md): the API's description, Swagger UI, sign-in schemes.
8. [Ready-made middleware](middleware.md): CORS, request IDs, rate limiting, compression, security headers.
9. [Testing](testing.md)
10. [The built-in server](server.md): running in production, workers and threads.
11. [Deployment](deployment.md): behind nginx or Caddy, in Docker, on AWS Lambda or Vercel.

The [reference](reference/app.md) lists every class, function and option,
from the code's own docstrings.

And for the curious:

- [Performance](performance.md): the benchmarks, and how to reproduce them.
- [The standalone router](router.md): Korvex's Rust router on its own.
- [Examples](https://github.com/3d3n-pyc/korvex/tree/main/examples): from
  a few lines to a complete app laid out like a real project.
