"""The app's rules, tested on the services directly: no HTTP involved."""

import uuid
from datetime import date, timedelta

import pytest

from taskboard.domain import (
    InvalidInput,
    ListDeleted,
    NotFound,
    TaskCreated,
    TaskDeleted,
    TaskUpdated,
)
from taskboard.services.events import EventBus


async def new_list(services, name="Work"):
    return await services.lists.create(name)


def test_lists_get_a_color_and_a_place(with_services):
    async def scenario(services):
        first = await services.lists.create("  Home   chores ")
        second = await services.lists.create("Work", color="red")
        assert (first.name, first.position, second.position, second.color) == ("Home chores", 0, 1, "red")
        renamed = await services.lists.update(first.id, name="House")
        assert renamed.color == first.color
        assert [(each.name, each.open_tasks) for each in await services.lists.find()] == [("House", 0), ("Work", 0)]

    with_services(scenario)


def test_the_last_list_stays(with_services):
    async def scenario(services):
        only = await new_list(services)
        with pytest.raises(InvalidInput):
            await services.lists.delete(only.id)
        other = await new_list(services, "Other")
        task = await services.tasks.create(other.id, "goes with its list")
        await services.lists.delete(other.id)
        with pytest.raises(NotFound):
            await services.tasks.get(task.id)

    with_services(scenario)


def test_new_tasks_go_on_top_and_titles_are_tidied(with_services):
    async def scenario(services):
        work = await new_list(services)
        first = await services.tasks.create(work.id, "first")
        second = await services.tasks.create(work.id, "  second   task ")
        assert second.title == "second task"
        assert [t.id for t in await services.tasks.find(list_id=work.id)] == [second.id, first.id]
        with pytest.raises(InvalidInput):
            await services.tasks.create(work.id, "   ")
        with pytest.raises(NotFound):
            await services.tasks.create(uuid.uuid4(), "nowhere")

    with_services(scenario)


def test_updates_change_only_what_is_given(with_services):
    async def scenario(services):
        work = await new_list(services)
        task = await services.tasks.create(work.id, "a", notes="n", due=date(2030, 1, 1))
        renamed = await services.tasks.update(task.id, title="b")
        assert (renamed.title, renamed.notes, renamed.due) == ("b", "n", date(2030, 1, 1))
        cleared = await services.tasks.update(task.id, due=None)  # None: remove the due date
        assert cleared.due is None and cleared.title == "b"

        done = await services.tasks.update(task.id, done=True)
        assert done.completed_at is not None
        assert (await services.tasks.update(task.id, done=False)).completed_at is None

    with_services(scenario)


def test_moving_a_task_puts_it_on_top_of_the_other_list(with_services):
    async def scenario(services):
        work, home = await new_list(services), await new_list(services, "Home")
        already = await services.tasks.create(home.id, "already there")
        moved = await services.tasks.update((await services.tasks.create(work.id, "moving")).id, list_id=home.id)
        assert [t.id for t in await services.tasks.find(list_id=home.id)] == [moved.id, already.id]
        assert [each.open_tasks for each in await services.lists.find()] == [0, 2]

    with_services(scenario)


def test_reordering(with_services):
    async def scenario(services):
        work, home = await new_list(services), await new_list(services, "Home")
        c, b, a = [await services.tasks.create(work.id, title) for title in "cba"]  # each goes on top: a, b, c
        stranger = await services.tasks.create(home.id, "elsewhere")
        await services.tasks.reorder(work.id, [c.id, stranger.id, a.id])  # b left out, a stranger ignored
        assert [t.title for t in await services.tasks.find(list_id=work.id)] == ["c", "a", "b"]
        assert [t.title for t in await services.tasks.find(list_id=home.id)] == ["elsewhere"]

    with_services(scenario)


def test_finding_by_due_date_and_text(with_services):
    async def scenario(services):
        work = await new_list(services)
        today = date(2026, 11, 5)
        late = await services.tasks.create(work.id, "late", due=today - timedelta(days=2))
        now = await services.tasks.create(work.id, "today", due=today)
        await services.tasks.create(work.id, "later", due=today + timedelta(days=3))
        await services.tasks.create(work.id, "someday")
        assert [t.id for t in await services.tasks.find(due_to=today, done=False)] == [late.id, now.id]
        assert [t.title for t in await services.tasks.find(due_from=today)] == ["today", "later"]

        await services.tasks.create(work.id, "Pay 100% of the bill", notes="before_friday")
        assert [t.title for t in await services.tasks.find(search="100%")] == ["Pay 100% of the bill"]
        assert [t.title for t in await services.tasks.find(search="E_F")] == ["Pay 100% of the bill"]
        assert await services.tasks.find(search="0_%") == []  # % and _ are literal, not wildcards

    with_services(scenario)


def test_clearing_finished_tasks(with_services):
    async def scenario(services):
        work = await new_list(services)
        keep = await services.tasks.create(work.id, "keep")
        gone = await services.tasks.create(work.id, "gone")
        await services.tasks.update(gone.id, done=True)
        assert await services.tasks.clear_done(work.id) == 1
        assert [t.id for t in await services.tasks.find(list_id=work.id)] == [keep.id]

    with_services(scenario)


def test_the_welcome_list_is_created_once(with_services):
    async def scenario(services):
        await services.tasks.welcome()
        await services.tasks.welcome()
        lists = await services.lists.find()
        assert [each.name for each in lists] == ["Inbox"] and lists[0].open_tasks == 4

    with_services(scenario)


def test_changes_are_announced(with_services):
    async def scenario(services):
        work, other = await new_list(services), await new_list(services, "Other")
        queue = services.events.subscribe()
        task = await services.tasks.create(work.id, "watched")
        await services.tasks.update(task.id, done=True)
        await services.tasks.update(task.id, done=True)  # no change: nothing announced
        await services.tasks.delete(task.id)
        await services.lists.delete(other.id)
        events = [queue.get_nowait() for _ in range(queue.qsize())]
        assert [type(e) for e in events] == [TaskCreated, TaskUpdated, TaskDeleted, ListDeleted]
        assert events[2] == TaskDeleted(task.id, work.id)

    with_services(scenario)


def test_a_listener_that_falls_behind_is_cut_off():
    bus = EventBus(queue_size=2)
    slow, fast = bus.subscribe(), bus.subscribe()
    for n in range(3):
        bus.publish(TaskDeleted(uuid.UUID(int=n), uuid.UUID(int=0)))
        fast.get_nowait()
    assert bus.listeners == 1
    left = [slow.get_nowait() for _ in range(slow.qsize())]
    assert left[-1] is None  # told it was cut off (its page closes and reloads)
    bus.publish(TaskDeleted(uuid.UUID(int=9), uuid.UUID(int=0)))
    assert slow.empty()  # and gets nothing more


def test_sessions(with_services):
    async def scenario(services):
        assert await services.auth.login("wrong") is None
        token = await services.auth.login("s3cret")
        assert token and await services.auth.user(token) == "admin"
        await services.auth.logout(token)
        assert await services.auth.user(token) is None

    with_services(scenario)


def test_the_data_and_sessions_survive_a_restart(with_services):
    saved = {}

    async def first(services):
        work = await new_list(services)
        saved["task"] = (await services.tasks.create(work.id, "persisted", due=date(2030, 1, 2))).id
        saved["token"] = await services.auth.login("s3cret")

    async def second(services):
        task = await services.tasks.get(saved["task"])
        assert (task.title, task.due) == ("persisted", date(2030, 1, 2))
        assert await services.auth.user(saved["token"]) == "admin"

    with_services(first)
    with_services(second)  # a new connection: migrations already done, data still there
