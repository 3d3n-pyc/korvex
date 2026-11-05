"""Live updates over the WebSocket."""


def test_pages_see_changes_as_they_happen(client, inbox):
    with client.websocket("/live") as page:
        assert page.receive_json() == {"event": "presence", "open": 1}

        task = client.post("/api/tasks", json={"list_id": inbox["id"], "title": "Seen live"}).json()
        assert page.receive_json() == {"event": "task_created", "task": task}

        done = client.patch(f"/api/tasks/{task['id']}", json={"done": True}).json()
        assert page.receive_json() == {"event": "task_updated", "task": done}

        client.put(f"/api/lists/{inbox['id']}/order", json={"task_ids": [task["id"]]})
        assert page.receive_json() == {"event": "tasks_reordered", "list_id": inbox["id"]}

        client.delete(f"/api/tasks/{task['id']}")
        assert page.receive_json() == {"event": "task_deleted", "id": task["id"], "list_id": inbox["id"]}

        work = client.post("/api/lists", json={"name": "Work"}).json()
        assert page.receive_json() == {"event": "list_created", "list": work}


def test_pages_know_how_many_are_open(client):
    with client.websocket("/live") as first:
        assert first.receive_json()["open"] == 1
        with client.websocket("/live") as second:
            assert first.receive_json()["open"] == 2
            assert second.receive_json()["open"] == 2
        assert first.receive_json()["open"] == 1
