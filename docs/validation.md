# Validation

Use this when you want Korvex to read, check and convert the input of a
[handler](glossary.md#handler) from its annotations.

Annotations say where a parameter comes from, and what it must be.
[Models](glossary.md#model) can be [msgspec](https://jcristharif.com/msgspec/) structs or
[pydantic](https://docs.pydantic.dev/) models; Korvex needs neither.

```python
import msgspec

from korvex import App

app = App()


class NewUser(msgspec.Struct):
    name: str
    age: int


class User(msgspec.Struct):
    id: int
    name: str


@app.post("/users", status=201)
def create_user(body: NewUser, notify: bool = False) -> User:
    return User(id=1, name=body.name)
```

## Where a parameter comes from

| Parameter | Comes from |
|---|---|
| a model, or a list of models (`body: NewUser`) | the JSON [body](glossary.md#request-body) |
| named like a [path parameter](glossary.md#path-parameter) (`id`) | the path |
| `str`, `int`, `float`, `bool`, `UUID`, `date`, `datetime`, or an `Optional` or `list` of them | the [query string](glossary.md#query-string) |
| `Header()`, `Cookie()`, `Form()`, `Query(alias=...)` | there |
| `Depends(func)` | what `func` returns: see [Dependencies](dependencies.md) |
| named `request`, or annotated `Request` | the request itself |

- A default makes a parameter optional. Dates are ISO 8601.
- Anything else is refused with a message when the route is declared, so
  a mistake shows at import.
- When a field is repeated (`?tag=a&tag=b`), a single value takes the
  first, and a `list[str]` takes them all.

## Headers, cookies and forms

```python
from korvex import App, Cookie, Form, Header, Query, UploadedFile

app = App()


@app.post("/photos")
async def upload(
    title: str = Form(),  # a form field, urlencoded or multipart
    photo: UploadedFile = Form(),  # a file from a multipart form
    x_client: str = Header("web"),  # the X-Client header, "web" if missing
    theme: str = Cookie("light"),
    page_size: int = Query(20, alias="page-size"),
): ...
```

- `x_client` reads `X-Client`: underscores become hyphens, and case
  doesn't matter. `alias=` gives the exact name. `list[str]` gets every
  value of a repeated header.
- An empty form field counts as missing, except for a `str`.
- A whole model can come from a form, field by field:
  `signup: Signup = Form()`. A checkbox's `on` is true, and text converts
  to numbers and dates as the model says.
- All four also work in `Annotated[...]` (from `typing`):
  `theme: Annotated[str, Cookie()] = "light"`.

## What the handler returns

- A model, with a return annotation (`-> User`): sent as JSON, with the
  route's `status` (200 by default).
- `None`, annotated `-> None`: an empty response, for `status=204`.
- A `Response`: sent as it is, with its own status.

## Errors

Invalid input fails [validation](glossary.md#validation) and gets a 422 listing the problems:

```json
{"detail": [{"loc": ["body", "age"], "msg": "Expected `int`, got `str`", "type": "validation"}]}
```

`loc` says where: `["query", "page"]`, `["header", "x-client"]`,
`["form", "title"]`. pydantic lists every problem; msgspec stops at the
first. To answer differently, handle the 422; the exception is a
`RequestValidationError`, with the list in `errors`:

```python
from korvex import App, RequestValidationError, Response

app = App()


@app.exception_handler(422)
def invalid(request, exc: RequestValidationError):
    return Response.json({"problems": [e["loc"] for e in exc.errors]}, status=422)
```

Annotations are read once, when the route is declared.

Next: [Requests and responses](requests-and-responses.md).
