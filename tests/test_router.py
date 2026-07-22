"""Integration tests for the public korvex.Router API."""

import pytest

from korvex import MethodNotAllowed, RouteGroup, Router


def test_matches_static_route():
    router = Router()
    router.add_route("/health", "health_check")

    result = router.match_route("/health")

    assert result == ("health_check", {}, [])


def test_matches_root_path():
    router = Router()
    router.add_route("/", "root")

    assert router.match_route("/") == ("root", {}, [])
    assert router.match_route("") == ("root", {}, [])


def test_matches_dynamic_segment_and_captures_param():
    router = Router()
    router.add_route("/users/{id}", "get_user")

    result = router.match_route("/users/42")

    assert result == ("get_user", {"id": "42"}, [])


def test_matches_multiple_dynamic_segments():
    router = Router()
    router.add_route("/users/{id}/posts/{post_id}", "get_user_post")

    result = router.match_route("/users/42/posts/7")

    assert result == ("get_user_post", {"id": "42", "post_id": "7"}, [])


def test_prefers_static_over_dynamic_route():
    router = Router()
    router.add_route("/users/{id}", "get_user")
    router.add_route("/users/me", "get_current_user")

    result = router.match_route("/users/me")

    assert result == ("get_current_user", {}, [])


def test_returns_none_for_unknown_route():
    router = Router()
    router.add_route("/health", "health_check")

    assert router.match_route("/unknown") is None


def test_ignores_leading_and_trailing_slashes():
    router = Router()
    router.add_route("/health", "health_check")

    assert router.match_route("health/") == ("health_check", {}, [])


def test_route_registered_after_lookup_is_found():
    router = Router()
    router.add_route("/health", "health_check")
    router.match_route("/health")
    router.add_route("/status", "status_check")

    assert router.match_route("/status") == ("status_check", {}, [])


def test_matches_specific_method():
    router = Router()
    router.add_route("/users", "create_user", method="POST")

    assert router.match_route("/users", method="POST") == ("create_user", {}, [])


def test_defaults_to_get_when_method_omitted():
    router = Router()
    router.add_route("/users", "list_users")

    assert router.match_route("/users") == ("list_users", {}, [])


def test_raises_method_not_allowed_for_wrong_method():
    router = Router()
    router.add_route("/users", "list_users", method="GET")

    with pytest.raises(MethodNotAllowed):
        router.match_route("/users", method="POST")


def test_method_not_allowed_lists_allowed_methods():
    router = Router()
    router.add_route("/users", "list_users", method="GET")

    with pytest.raises(MethodNotAllowed) as exc_info:
        router.match_route("/users", method="POST")

    assert exc_info.value.args[0] == ["GET"]


def test_unknown_route_is_not_found_regardless_of_method():
    router = Router()
    router.add_route("/health", "health_check")

    assert router.match_route("/unknown", method="POST") is None


def test_different_methods_same_path_have_independent_handlers():
    router = Router()
    router.add_route("/users", "list_users", method="GET")
    router.add_route("/users", "create_user", method="POST")

    assert router.match_route("/users", method="GET") == ("list_users", {}, [])
    assert router.match_route("/users", method="POST") == ("create_user", {}, [])


def test_add_route_rejects_unknown_method():
    router = Router()

    with pytest.raises(ValueError):
        router.add_route("/users", "list_users", method="TRACE")


def test_match_route_rejects_unknown_method():
    router = Router()
    router.add_route("/users", "list_users")

    with pytest.raises(ValueError):
        router.match_route("/users", method="TRACE")


def test_int_constraint_matches_numeric_segment():
    router = Router()
    router.add_route("/users/{id:int}", "get_user")

    assert router.match_route("/users/42") == ("get_user", {"id": "42"}, [])


def test_int_constraint_rejects_non_numeric_and_falls_through_to_sibling():
    router = Router()
    router.add_route("/users/{id:int}", "get_user_by_id")
    router.add_route("/users/{slug}", "get_user_by_slug")

    assert router.match_route("/users/42") == ("get_user_by_id", {"id": "42"}, [])
    assert router.match_route("/users/abc") == ("get_user_by_slug", {"slug": "abc"}, [])


def test_uuid_constraint_matches_well_formed_uuid():
    router = Router()
    router.add_route("/orders/{id:uuid}", "get_order")

    uuid = "123e4567-e89b-12d3-a456-426614174000"
    assert router.match_route(f"/orders/{uuid}") == ("get_order", {"id": uuid}, [])


def test_uuid_constraint_rejects_malformed_uuid():
    router = Router()
    router.add_route("/orders/{id:uuid}", "get_order")

    assert router.match_route("/orders/not-a-uuid") is None


def test_str_constraint_is_alias_for_unconstrained():
    router = Router()
    router.add_route("/users/{id:str}", "get_user")

    assert router.match_route("/users/anything") == ("get_user", {"id": "anything"}, [])


def test_unknown_constraint_raises_at_registration():
    router = Router()

    with pytest.raises(ValueError):
        router.add_route("/users/{id:float}", "get_user")


def test_conflicting_param_names_at_same_depth_raises_at_registration():
    router = Router()
    router.add_route("/users/{id}", "get_user")

    with pytest.raises(ValueError):
        router.add_route("/users/{slug}", "get_user_alt")


def test_wildcard_captures_multi_segment_path():
    router = Router()
    router.add_route("/static/*filepath", "serve_static")

    result = router.match_route("/static/a/b/c.png")

    assert result == ("serve_static", {"filepath": "a/b/c.png"}, [])


def test_wildcard_lowest_priority():
    router = Router()
    router.add_route("/files/*rest", "wildcard")
    router.add_route("/files/readme", "static")
    router.add_route("/files/{name}", "param")

    assert router.match_route("/files/readme") == ("static", {}, [])
    assert router.match_route("/files/other") == ("param", {"name": "other"}, [])
    assert router.match_route("/files/a/b") == ("wildcard", {"rest": "a/b"}, [])


def test_wildcard_requires_at_least_one_segment():
    router = Router()
    router.add_route("/static/*filepath", "serve_static")

    assert router.match_route("/static") is None


def test_wildcard_registration_error_when_not_last_segment():
    router = Router()

    with pytest.raises(ValueError):
        router.add_route("/static/*filepath/extra", "serve_static")


def test_wildcard_registration_error_when_unnamed():
    router = Router()

    with pytest.raises(ValueError):
        router.add_route("/static/*", "serve_static")


def test_add_route_stores_and_returns_middleware():
    router = Router()
    router.add_route("/users", "list_users", middleware=["auth", "log"])

    assert router.match_route("/users") == ("list_users", {}, ["auth", "log"])


def test_middleware_is_empty_by_default():
    router = Router()
    router.add_route("/users", "list_users")

    assert router.match_route("/users") == ("list_users", {}, [])


def test_method_shorthands_register_correct_method():
    router = Router()
    router.get("/users", "list_users")
    router.post("/users", "create_user")
    router.put("/users/{id}", "replace_user")
    router.patch("/users/{id}", "update_user")
    router.delete("/users/{id}", "delete_user")
    router.head("/users", "head_users")
    router.options("/users", "options_users")

    assert router.match_route("/users") == ("list_users", {}, [])
    assert router.match_route("/users", method="POST") == ("create_user", {}, [])
    assert router.match_route("/users/1", method="PUT") == ("replace_user", {"id": "1"}, [])
    assert router.match_route("/users/1", method="PATCH") == ("update_user", {"id": "1"}, [])
    assert router.match_route("/users/1", method="DELETE") == ("delete_user", {"id": "1"}, [])
    assert router.match_route("/users", method="HEAD") == ("head_users", {}, [])
    assert router.match_route("/users", method="OPTIONS") == ("options_users", {}, [])


def test_route_group_applies_prefix():
    router = Router()
    api = router.group("/api/v1")
    api.get("/users/{id}", "get_user")

    assert router.match_route("/api/v1/users/42") == ("get_user", {"id": "42"}, [])
    assert router.match_route("/users/42") is None


def test_route_group_propagates_middleware_to_routes():
    router = Router()
    api = router.group("/api", middleware=["auth"])
    api.get("/users", "list_users")

    assert router.match_route("/api/users") == ("list_users", {}, ["auth"])


def test_route_specific_middleware_appends_after_group_middleware():
    router = Router()
    api = router.group("/api", middleware=["auth"])
    api.get("/users", "list_users", middleware=["cache"])

    assert router.match_route("/api/users") == ("list_users", {}, ["auth", "cache"])


def test_nested_route_groups_concatenate_prefix_and_middleware():
    router = Router()
    api = router.group("/api", middleware=["auth"])
    admin = api.group("/admin", middleware=["require_admin"])
    admin.get("/stats", "get_stats")

    assert router.match_route("/api/admin/stats") == (
        "get_stats",
        {},
        ["auth", "require_admin"],
    )


def test_route_group_route_method_supports_all_http_methods():
    router = Router()
    api = router.group("/api")
    api.route("/users", "create_user", method="POST")

    assert router.match_route("/api/users", method="POST") == ("create_user", {}, [])


def test_route_group_methods_are_chainable():
    router = Router()
    api = router.group("/api")
    result = api.get("/users", "list_users").post("/users", "create_user")

    assert result is api
    assert router.match_route("/api/users") == ("list_users", {}, [])
    assert router.match_route("/api/users", method="POST") == ("create_user", {}, [])


def test_route_group_can_be_constructed_directly():
    router = Router()
    group = RouteGroup(router, "/api", middleware=["auth"])
    group.get("/users", "list_users")

    assert router.match_route("/api/users") == ("list_users", {}, ["auth"])


def test_routes_lists_registered_routes():
    router = Router()
    router.add_route("/health", "health_check")
    router.add_route("/users/{id:int}", "get_user", middleware=["auth"])

    routes = router.routes()

    assert ("/health", "GET", "health_check", []) in routes
    assert ("/users/{id:int}", "GET", "get_user", ["auth"]) in routes


def test_routes_is_empty_for_fresh_router():
    router = Router()

    assert router.routes() == []


def test_routes_reflects_removal():
    router = Router()
    router.add_route("/users", "list_users")
    router.remove_route("/users")

    assert router.routes() == []


def test_routes_distinguishes_methods_on_same_path():
    router = Router()
    router.get("/users", "list_users")
    router.post("/users", "create_user")

    routes = router.routes()

    assert ("/users", "GET", "list_users", []) in routes
    assert ("/users", "POST", "create_user", []) in routes


def test_remove_route_removes_a_registered_route():
    router = Router()
    router.add_route("/users", "list_users")

    assert router.remove_route("/users") is True
    assert router.match_route("/users") is None


def test_remove_route_returns_false_for_unregistered_route():
    router = Router()

    assert router.remove_route("/unknown") is False


def test_remove_route_only_affects_given_method():
    router = Router()
    router.get("/users", "list_users")
    router.post("/users", "create_user")

    router.remove_route("/users", method="GET")

    with pytest.raises(MethodNotAllowed):
        router.match_route("/users")
    assert router.match_route("/users", method="POST") == ("create_user", {}, [])


def test_remove_route_rejects_unknown_constraint():
    router = Router()
    router.add_route("/users/{id:int}", "get_user")

    with pytest.raises(ValueError):
        router.remove_route("/users/{id:float}")
