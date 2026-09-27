from collections.abc import Sequence
from datetime import UTC, date, datetime

import pytest
from fakes import FakeSession, FakeTodoist, response

from agent.github_client import Issue
from agent.providers.base import Activity, RemainingItem, Summary
from agent.todoist_client import (
    ProjectState,
    Task,
    TodoistClient,
    TodoistError,
    build_plan,
    execute,
    format_plan,
    issue_marker,
    load_state,
    summary_marker,
)

REPO = "owner/repo"
TODAY = date(2026, 9, 27)
TOMORROW = date(2026, 9, 28)
NOW = datetime(2026, 9, 27, 4, 0, tzinfo=UTC)
API = "https://api.todoist.com/api/v1"


# --- client ---------------------------------------------------------------


def make_client(session: FakeSession) -> TodoistClient:
    return TodoistClient("tok", session=session, sleep=lambda _: None)  # type: ignore[arg-type]


def task_json(id: str, description: str = "", due: str | None = "2026-09-27") -> dict[str, object]:
    return {"id": id, "content": f"task {id}", "description": description, "due": {"date": due} if due else None}


def test_open_tasks_paginate_by_cursor() -> None:
    session = FakeSession(
        response(body={"results": [task_json("a", "[gh-agent:owner/repo#1]")], "next_cursor": "c2"}),
        response(body={"results": [task_json("b", due="2026-09-30T10:00:00")], "next_cursor": None}),
    )
    tasks = make_client(session).list_open_tasks("p1")

    assert [t.id for t in tasks] == ["a", "b"]
    assert tasks[0].marker == "[gh-agent:owner/repo#1]"
    assert tasks[1].due_date == date(2026, 9, 30)
    assert session.calls[0] == (f"{API}/tasks", {"project_id": "p1", "limit": 200})
    assert session.calls[1][1] == {"project_id": "p1", "limit": 200, "cursor": "c2"}


def test_completed_tasks_use_completion_endpoint() -> None:
    session = FakeSession(response(body={"items": [task_json("a", due=None)]}))
    since = datetime(2026, 7, 1, tzinfo=UTC)
    tasks = make_client(session).list_completed_tasks("p1", since, NOW)

    assert tasks[0].due_date is None
    url, params = session.calls[0]
    assert url == f"{API}/tasks/completed/by_completion_date"
    assert params == {"project_id": "p1", "since": "2026-07-01T00:00:00Z", "until": "2026-09-27T04:00:00Z", "limit": 200}


def test_find_project_by_name() -> None:
    session = FakeSession(response(body={"results": [{"id": "x", "name": "Inbox"}, {"id": "y", "name": "Daily Agent"}]}))
    assert make_client(session).find_project("Daily Agent") == "y"


def test_create_task_sends_label_and_due() -> None:
    session = FakeSession(response(body=task_json("new", "[gh-agent:owner/repo#1]", "2026-09-28")))
    make_client(session).create_task("p1", "کار", "[gh-agent:owner/repo#1]", TOMORROW)

    method, url, kwargs = session.requests[0]
    assert (method, url) == ("POST", f"{API}/tasks")
    assert kwargs["json"] == {
        "project_id": "p1",
        "content": "کار",
        "description": "[gh-agent:owner/repo#1]",
        "labels": ["gh-agent"],
        "due_date": "2026-09-28",
    }
    assert kwargs["headers"]["X-Request-Id"]


def test_close_and_update_handle_empty_and_json_bodies() -> None:
    empty = response(204)
    empty._content = b""
    session = FakeSession(empty, response(body=task_json("a")))
    client = make_client(session)
    client.close_task("a")
    client.update_due("a", TOMORROW)

    assert session.requests[0][:2] == ("POST", f"{API}/tasks/a/close")
    assert session.requests[1][2]["json"] == {"due_date": "2026-09-28"}


def test_auth_error_is_clear() -> None:
    with pytest.raises(TodoistError, match="check TODOIST_API_TOKEN"):
        make_client(FakeSession(response(401))).list_open_tasks("p1")


# --- plan -----------------------------------------------------------------


def issue(number: int) -> Issue:
    return Issue(number=number, title=f"Issue {number}", labels=[], body="", url=f"https://github.com/{REPO}/issues/{number}")


def activity(open_numbers: Sequence[int] = (), closed_numbers: Sequence[int] = ()) -> Activity:
    return Activity(
        repo=REPO,
        commits=[],
        open_issues=[issue(n) for n in open_numbers],
        closed_issues=[issue(n) for n in closed_numbers],
    )


def summary(open_numbers: Sequence[int] = (), done: Sequence[str] = ()) -> Summary:
    return Summary(done=list(done), remaining=[RemainingItem(issue=n, title=f"کار {n}") for n in open_numbers])


def task(id: str, marker: str, due: date | None = TOMORROW) -> Task:
    return Task(id=id, content=f"task {id}", description=f"https://x\n\n{marker}", due_date=due)


def state(open_tasks: Sequence[Task] = (), completed: Sequence[Task] = ()) -> ProjectState:
    return ProjectState("Daily Agent", "p1", list(open_tasks), list(completed))


def test_rule1_open_issue_without_task_is_created_due_tomorrow() -> None:
    plan = build_plan(activity([1]), summary([1]), state(), TODAY)

    assert len(plan) == 1
    action = plan[0]
    assert (action.kind, action.due, action.marker) == ("create", TOMORROW, "[gh-agent:owner/repo#1]")
    assert action.content == "کار 1 (#1)"
    assert action.description == f"https://github.com/{REPO}/issues/1\n\n[gh-agent:owner/repo#1]"


def test_rule2_existing_task_skipped_or_rolled_forward() -> None:
    tasks = [
        task("a", issue_marker(REPO, 1), due=TOMORROW),
        task("b", issue_marker(REPO, 2), due=date(2026, 9, 25)),
        task("c", issue_marker(REPO, 3), due=date(2026, 10, 5)),
        task("d", issue_marker(REPO, 4), due=None),
    ]
    plan = build_plan(activity([1, 2, 3, 4]), summary([1, 2, 3, 4]), state(tasks), TODAY)

    assert [(a.kind, a.task_id, a.due) for a in plan] == [("update_due", "b", TOMORROW)]


def test_rule3_closed_issue_closes_open_task() -> None:
    tasks = [task("a", issue_marker(REPO, 5))]
    plan = build_plan(activity([], [5, 6]), summary(), state(tasks), TODAY)

    assert [(a.kind, a.task_id) for a in plan] == [("close", "a")]


def test_rule4_task_completed_in_todoist_is_not_recreated() -> None:
    completed = [task("a", issue_marker(REPO, 1))]
    assert build_plan(activity([1]), summary([1]), state(completed=completed), TODAY) == []


def test_rule5_summary_created_once_per_day() -> None:
    plan = build_plan(activity(), summary(done=["الف", "ب"]), state(), TODAY)

    assert len(plan) == 1
    action = plan[0]
    assert action.content == "کارهای انجام‌شده 2026-09-27"
    assert action.description == "- الف\n- ب\n\n[gh-agent:owner/repo:summary:2026-09-27]"
    assert action.due == TODAY

    marker = summary_marker(REPO, TODAY)
    for existing in (state([task("s", marker)]), state(completed=[task("s", marker)])):
        assert build_plan(activity(), summary(done=["الف"]), existing, TODAY) == []


def test_rule5_no_summary_when_done_empty() -> None:
    assert build_plan(activity(), summary(done=[]), state(), TODAY) == []


def test_markers_are_scoped_to_repo() -> None:
    other = [task("a", "[gh-agent:someone/else#1]")]
    plan = build_plan(activity([1]), summary([1]), state(other), TODAY)
    assert [a.kind for a in plan] == ["create"]


def test_second_run_is_empty() -> None:
    todoist = FakeTodoist()
    todoist.open.append(task("old", issue_marker(REPO, 5)))
    todoist.open.append(task("late", issue_marker(REPO, 2), due=date(2026, 9, 20)))
    act = activity([1, 2, 3], [5])
    summ = summary([1, 2, 3], done=["الف"])

    first = build_plan(act, summ, load_state(todoist, "Daily Agent", NOW), TODAY)  # type: ignore[arg-type]
    assert sorted(a.kind for a in first) == ["close", "create", "create", "create", "update_due"]
    execute(todoist, load_state(todoist, "Daily Agent", NOW), first)  # type: ignore[arg-type]

    second = build_plan(act, summ, load_state(todoist, "Daily Agent", NOW), TODAY)  # type: ignore[arg-type]
    assert second == []
    assert format_plan(second) == "Plan: 0 actions"


def test_execute_creates_missing_project_first() -> None:
    todoist = FakeTodoist(project_id=None)
    st = load_state(todoist, "Daily Agent", NOW)  # type: ignore[arg-type]
    assert st.id is None
    execute(todoist, st, build_plan(activity([1]), summary([1]), st, TODAY))  # type: ignore[arg-type]
    assert todoist.writes == ["create_project Daily Agent", "create کار 1 (#1)"]


def test_format_plan_lists_actions() -> None:
    text = format_plan(build_plan(activity([1]), summary([1], done=["الف"]), state(), TODAY))
    assert text.startswith("Plan: 2 action(s)")
    assert "+ create  کار 1 (#1)  (due 2026-09-28)" in text
    assert "- الف" in text
