"""Tests for `Router` and `RouteGroup`.

The matching rules are tested in depth in Rust (`src/tree.rs`); these
tests check the Python API on top of them.
"""

from uuid import UUID

import pytest

from korvex import MethodNotAllowed, RouteGroup, Router


def make_router(*routes):
    router = Router()
    for path, handler, method in routes:
        router.route(path, handler, method=method)
    return router


def test_match_returns_handler_params_and_middleware():
    router = Router()
    router.route("/users/{id}", "get_user", middleware=["auth"])

    assert router.match("/users/42") == ("get_user", {"id": "42"}, ("auth",))


def test_match_results_are_immutable():
    router = Router()
    router.route("/users/{id}", "get_user", middleware=["auth"])
    _, params, middleware = router.match("/users/42")

    with pytest.raises(TypeError):
        params["id"] = "666"
    assert isinstance(middleware, tuple)
    assert router.match("/users/42")[1] == {"id": "42"}


def test_match_is_cached():
    router = Router()
    router.route("/users/{id}", "get_user")

    first = router.match("/users/42")
    assert router.match("/users/42") is first
    assert router.match.cache_info().hits == 1


def test_adding_or_removing_a_route_clears_the_cache():
    router = Router()
    router.route("/users/{id}", "get_user")
    assert router.match("/users/me")[0] == "get_user"

    router.get("/users/me", "get_me")  # through a shortcut
    assert router.match("/users/me")[0] == "get_me"

    router.group("/users").get("/me", "get_me_v2")  # through a group
    assert router.match("/users/me")[0] == "get_me_v2"

    router.remove("/users/me")
    assert router.match("/users/me")[0] == "get_user"


def test_match_returns_none_when_nothing_matches():
    router = make_router(("/health", "health", "GET"))

    assert router.match("/unknown") is None


def test_slashes_at_either_end_are_ignored():
    router = make_router(("/users/", "list_users", "GET"))

    assert router.match("users")[0] == "list_users"
    assert router.match("/users/")[0] == "list_users"


def test_matching_priority():
    router = make_router(
        ("/files/*rest", "wildcard", "GET"),
        ("/files/{name}", "param", "GET"),
        ("/files/{id:int}", "int_param", "GET"),
        ("/files/readme", "literal", "GET"),
    )

    assert router.match("/files/readme")[0] == "literal"
    assert router.match("/files/42")[:2] == ("int_param", {"id": 42})
    assert router.match("/files/notes")[:2] == ("param", {"name": "notes"})
    assert router.match("/files/a/b")[:2] == ("wildcard", {"rest": "a/b"})


@pytest.mark.parametrize(
    ("value", "matches"),
    [("123e4567-e89b-12d3-a456-426614174000", True), ("123e4567", False), ("not-a-uuid", False)],
)
def test_uuid_constraint(value, matches):
    router = make_router(("/items/{id:uuid}", "item", "GET"))

    assert (router.match(f"/items/{value}") is not None) == matches


def test_params_are_converted_by_their_constraint():
    router = make_router(("/u/{id:int}/{key:uuid}/{name}/{alias:str}/*rest", "x", "GET"))
    key = "123E4567-E89B-12D3-A456-426614174000"

    _, params, _ = router.match(f"/u/-7/{key}/ada/al/a/b")

    assert params == {"id": -7, "key": UUID(key), "name": "ada", "alias": "al", "rest": "a/b"}
    assert type(params["id"]) is int
    assert type(params["key"]) is UUID


def test_method_defaults_to_get():
    router = make_router(("/users", "list_users", "GET"), ("/users", "create_user", "POST"))

    assert router.match("/users")[0] == "list_users"
    assert router.match("/users", "POST")[0] == "create_user"


def test_wrong_method_raises_method_not_allowed():
    router = make_router(("/users", "list_users", "GET"), ("/users", "create_user", "POST"))

    with pytest.raises(MethodNotAllowed) as exc_info:
        router.match("/users", "DELETE")

    assert exc_info.value.allowed == ["GET", "POST"]


@pytest.mark.parametrize("shortcut", ["get", "post", "put", "patch", "delete", "head", "options"])
def test_method_shortcuts(shortcut):
    router = Router()
    getattr(router, shortcut)("/thing", "handler", middleware=["m"])

    assert router.routes() == [("/thing", shortcut.upper(), "handler", ("m",))]


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/users", "FETCH"),  # unknown method
        ("/users/{id:float}", "GET"),  # unknown constraint
        ("/static/*", "GET"),  # unnamed wildcard
        ("/static/*path/more", "GET"),  # wildcard not last
    ],
)
def test_invalid_routes_raise_value_error(path, method):
    with pytest.raises(ValueError):
        Router().route(path, "handler", method=method)


def test_conflicting_param_names_raise_value_error():
    router = make_router(("/users/{id}", "get_user", "GET"))

    with pytest.raises(ValueError, match="conflicts"):
        router.route("/users/{slug}/posts", "get_posts")


def test_match_rejects_unknown_method():
    with pytest.raises(ValueError):
        Router().match("/users", "FETCH")


def test_routes_lists_patterns_sorted():
    router = make_router(
        ("/users/{id:int}", "get_user", "GET"),
        ("/users", "create_user", "POST"),
        ("/users", "list_users", "GET"),
    )

    assert router.routes() == [
        ("/users", "GET", "list_users", ()),
        ("/users", "POST", "create_user", ()),
        ("/users/{id:int}", "GET", "get_user", ()),
    ]
    assert repr(router) == "Router(routes=3)"


def test_remove():
    router = make_router(("/users/{id:int}", "get_user", "GET"), ("/users/{id:int}", "delete_user", "DELETE"))

    assert router.remove("/users/{id}") is False  # a different pattern
    assert router.remove("/users/{id:int}") is True
    assert router.remove("/users/{id:int}") is False
    assert router.routes() == [("/users/{id:int}", "DELETE", "delete_user", ())]


def test_group_prefixes_paths_and_prepends_middleware():
    router = Router()
    api = router.group("/api/", middleware=["auth"])
    api.get("/users", "list_users")
    api.post("users", "create_user", middleware=["rate_limit"])

    assert router.routes() == [
        ("/api/users", "GET", "list_users", ("auth",)),
        ("/api/users", "POST", "create_user", ("auth", "rate_limit")),
    ]


def test_nested_groups_combine_prefix_and_middleware():
    router = Router()
    admin = router.group("/api", middleware=["auth"]).group("/admin", middleware=["admin"])
    admin.route("/stats", "stats", method="DELETE")

    assert router.routes() == [("/api/admin/stats", "DELETE", "stats", ("auth", "admin"))]


def test_group_without_prefix_only_adds_middleware():
    router = Router()
    group = RouteGroup(router, middleware=["auth"])
    group.get("/", "home")

    assert router.match("/") == ("home", {}, ("auth",))
    assert repr(group) == "RouteGroup(prefix='', middleware=['auth'])"


def test_custom_constraints():
    router = Router()
    router.constraint("slug", r"[a-z0-9]+(?:-[a-z0-9]+)*")
    router.constraint("slug", r"[a-z0-9]+(?:-[a-z0-9]+)*")  # the same again: fine
    router.get("/posts/{post:slug}", "by_slug")
    router.get("/posts/{other}", "anything")
    assert router.match("/posts/hello-world") == ("by_slug", {"post": "hello-world"}, ())
    assert router.match("/posts/Hello")[0] == "anything"  # doesn't fit: the plain route
    assert ("/posts/{post:slug}", "GET", "by_slug", ()) in router.routes()
    assert router.constraints() == [("slug", r"[a-z0-9]+(?:-[a-z0-9]+)*")]
    assert router.remove("/posts/{post:slug}") is True


def test_custom_constraints_are_checked():
    router = Router()
    router.constraint("hex", "[0-9a-f]+")
    with pytest.raises(ValueError, match="already defined"):
        router.constraint("hex", "[0-9A-F]+")  # would change routes already there
    with pytest.raises(ValueError, match="built-in"):
        router.constraint("int", "[0-9]+")
    with pytest.raises(ValueError):
        router.constraint("bad", "(")
    with pytest.raises(ValueError, match="unknown constraint"):
        router.get("/x/{id:nope}", "x")
