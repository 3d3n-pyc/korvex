"""The HTTP API, through the whole app: routing, validation, hooks, errors."""

import pytest


def create(client, list_id, **fields):
    response = client.post("/api/tasks", json={"list_id": list_id, "title": "A task", **fields})
    assert response.status == 201, response.text
    return response.json()


def test_the_first_start_creates_a_welcome_list(client, inbox):
    assert inbox["name"] == "Inbox" and inbox["open_tasks"] == 4
    assert len(client.get(f"/api/tasks?list_id={inbox['id']}").json()) == 4


def test_lists(client, inbox):
    work = client.post("/api/lists", json={"name": "Work"}).json()
    assert work["color"] in {"blue", "green", "orange", "red", "purple", "pink", "teal", "gray"}
    assert client.patch(f"/api/lists/{work['id']}", json={"color": "red"}).json()["color"] == "red"
    assert [each["name"] for each in client.get("/api/lists").json()] == ["Inbox", "Work"]
    assert client.post("/api/lists", json={"name": "x", "color": "beige"}).status == 422  # not a color
    assert client.delete(f"/api/lists/{work['id']}").status == 204
    last = client.delete(f"/api/lists/{inbox['id']}")
    assert last.status == 422 and last.json()["detail"][0]["msg"] == "the last list can't be deleted"


def test_create_read_update_delete(client, inbox):
    task = create(client, inbox["id"], title="Write tests", notes="all of them", due="2026-11-20")
    assert (task["due"], task["done"], task["completed_at"]) == ("2026-11-20", False, None)
    assert client.get(f"/api/tasks/{task['id']}").json() == task

    done = client.patch(f"/api/tasks/{task['id']}", json={"done": True}).json()
    assert done["done"] and done["completed_at"]
    undated = client.patch(f"/api/tasks/{task['id']}", json={"due": None}).json()
    assert undated["due"] is None and undated["notes"] == "all of them"  # null removes, absent keeps

    assert client.delete(f"/api/tasks/{task['id']}").status == 204
    missing = client.get(f"/api/tasks/{task['id']}")
    assert missing.status == 404 and missing.json()["request_id"]


def test_views(client, inbox):
    # Years ahead, so the welcome tasks (due today and tomorrow) stay out of the way.
    for title, due in [("late", "2040-11-01"), ("today", "2040-11-05"), ("later", "2040-12-01")]:
        create(client, inbox["id"], title=title, due=due)
    today = [t["title"] for t in client.get("/api/tasks?due_from=2040-01-01&due_to=2040-11-05&done=false").json()]
    assert today == ["late", "today"]
    planned = [t["title"] for t in client.get("/api/tasks?due_from=2040-11-06").json()]
    assert planned == ["later"]
    assert [t["title"] for t in client.get("/api/tasks?q=LATE").json()] == ["late", "later"]


def test_reordering(client, inbox):
    ids = [t["id"] for t in client.get(f"/api/tasks?list_id={inbox['id']}").json()]
    assert client.put(f"/api/lists/{inbox['id']}/order", json={"task_ids": ids[::-1]}).status == 204
    assert [t["id"] for t in client.get(f"/api/tasks?list_id={inbox['id']}").json()] == ids[::-1]


def test_clearing_finished_tasks(client, inbox):
    task = create(client, inbox["id"])
    client.patch(f"/api/tasks/{task['id']}", json={"done": True})
    assert client.post(f"/api/lists/{inbox['id']}/clear-done").json() == {"deleted": 1}


def test_bodies_and_query_strings_are_validated(client, inbox):
    response = client.post("/api/tasks", json={"list_id": inbox["id"], "title": "x", "due": "next friday"})
    assert response.status == 422 and response.json()["detail"][0]["loc"] == ["body", "due"]
    assert client.post("/api/tasks", json={"list_id": inbox["id"], "title": "x", "owner": "me"}).status == 422
    assert client.get("/api/tasks?due_to=05/11/2026").json()["detail"][0]["loc"] == ["query", "due_to"]
    assert client.get("/api/tasks/not-a-uuid").status == 404  # {id:uuid} doesn't match
    missing_list = client.post("/api/tasks", json={"list_id": "00000000-0000-0000-0000-000000000000", "title": "x"})
    assert missing_list.status == 404


def test_rule_violations_look_like_validation_errors(client, inbox):
    response = client.post("/api/tasks", json={"list_id": inbox["id"], "title": "   "})
    assert response.status == 422
    assert response.json()["detail"] == [
        {"loc": ["body", "title"], "msg": "a title can't be blank", "type": "value_error"}
    ]


def test_export_streams_every_task(client):
    response = client.get("/api/export")
    assert response.headers["content-type"] == "application/x-ndjson"
    assert len(response.text.splitlines()) == 4


def test_every_response_says_how_long_it_took_and_which_request_it_was(client):
    response = client.get("/api/lists")
    assert response.headers["server-timing"].startswith("app;dur=")
    assert len(response.headers["x-request-id"]) == 32
    assert response.headers["ratelimit-limit"] == "600"
    assert "ratelimit-limit" not in client.get("/health").headers  # never limited


def test_errors_outside_the_api_are_text(anonymous):
    response = anonymous.get("/nowhere")
    assert (response.status, response.text) == (404, "Not Found")


def test_page_health_and_docs(anonymous):
    assert "Taskboard" in anonymous.get("/").text
    assert anonymous.get("/static/js/app.js").status == 200
    assert anonymous.get("/static/../app.py").status == 404
    assert anonymous.get("/health").text == "ok"
    assert "swagger-ui" in anonymous.get("/docs").text


def test_openapi_document(anonymous):
    document = anonymous.get("/openapi.json").json()
    assert {"NewTask", "TaskChanges", "Task", "TaskList", "TaskOrder"} <= set(document["components"]["schemas"])
    assert document["paths"]["/api/tasks"]["post"]["summary"] == "Create a task"
    validator = pytest.importorskip("openapi_spec_validator")
    validator.validate(document)
