# Korvex guide

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

And for the curious:

- [Performance](performance.md): the benchmarks, and how to reproduce them.
- [The standalone router](router.md): Korvex's Rust router on its own.
