# Changelog

## 0.2.0

Breaking: the API was simplified and made consistent.

| 0.1                                         | 0.2                                       |
|---------------------------------------------|-------------------------------------------|
| `router.add_route(path, handler_name, ...)` | `router.route(path, handler, ...)`        |
| `router.match_route(path, method)`          | `router.match(path, method)`              |
| `router.remove_route(path, method)`         | `router.remove(path, method)`             |
| `exc.args[0]` on `MethodNotAllowed`         | `exc.allowed`                             |
| `method`/`middleware` positional            | keyword-only: `route(p, h, method="POST")` |
| `RouteGroup` methods return the group       | they return `None`                        |
| `Headers.setdefault`, `del headers[name]`, `Headers.items()` | removed (`list(headers)` replaces `items()`) |

Internals were rewritten for readability; matching speed is unchanged.

The benchmark now checks that every router finds the right route, adds
Litestar, and separates cached from uncached lookups (see the README).

## 0.1.1

- First release with tested wheels for every platform.
