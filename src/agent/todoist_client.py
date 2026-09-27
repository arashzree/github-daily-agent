"""Creates and deduplicates tasks in Todoist via the API v1.

Dedupe works without a database: each task carries a marker in its
description, `[gh-agent:<owner/repo>#<N>]` for an issue and
`[gh-agent:<owner/repo>:summary:<YYYY-MM-DD>]` for a day's summary.
`build_plan` compares markers with the current Todoist state and returns
the actions to take; `execute` applies them.
"""

import logging
import re
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

import requests
from pydantic import BaseModel

from agent.config import Language
from agent.providers.base import Activity, Summary
from agent.retry import send

log = logging.getLogger(__name__)

API_URL = "https://api.todoist.com/api/v1"
LABEL = "gh-agent"
PAGE_LIMIT = 200  # API maximum
# The completed-tasks endpoint rejects ranges over 3 months.
COMPLETED_LOOKBACK = timedelta(days=89)
MARKER_RE = re.compile(r"\[gh-agent:[^\]]+\]")
# Title of the daily summary task; {date} is the activity date.
SUMMARY_TITLES: dict[Language, str] = {"en": "Done on {date}", "fa": "کارهای انجام‌شده {date}"}


class TodoistError(RuntimeError):
    """A Todoist API call failed in a way retrying won't fix."""


class Task(BaseModel):
    id: str
    content: str
    description: str = ""
    due_date: date | None = None

    @property
    def marker(self) -> str | None:
        match = MARKER_RE.search(self.description)
        return match.group(0) if match else None


def issue_marker(repo: str, number: int) -> str:
    return f"[gh-agent:{repo}#{number}]"


def summary_marker(repo: str, day: date) -> str:
    return f"[gh-agent:{repo}:summary:{day.isoformat()}]"


def _to_task(item: dict[str, Any]) -> Task:
    due = item.get("due")
    return Task(
        id=item["id"],
        content=item["content"],
        description=item.get("description") or "",
        # due.date is "YYYY-MM-DD" or a datetime when a time is set.
        due_date=date.fromisoformat(due["date"][:10]) if due else None,
    )


class TodoistClient:
    def __init__(
        self,
        token: str,
        *,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._sleep = sleep
        self._session = session or requests.Session()
        self._session.headers.update({"Authorization": f"Bearer {token}"})

    def _request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None, body: dict[str, Any] | None = None
    ) -> Any:
        # Same X-Request-Id on every retry of one logical write.
        headers = {"X-Request-Id": str(uuid.uuid4())} if method == "POST" else None
        resp = send(
            self._session,
            method,
            f"{API_URL}{path}",
            params=params,
            json=body,
            headers=headers,
            service="Todoist",
            error=TodoistError,
            sleep=self._sleep,
        )
        if resp.status_code in (401, 403):
            raise TodoistError(f"Todoist returned {resp.status_code} for {path}: check TODOIST_API_TOKEN")
        if not resp.ok:
            raise TodoistError(f"Todoist returned {resp.status_code} for {method} {path}: {resp.text[:200]}")
        return resp.json() if resp.content else None

    def _paginate(self, path: str, params: dict[str, Any], key: str = "results") -> Iterator[dict[str, Any]]:
        cursor: str | None = None
        while True:
            page_params = {**params, "limit": PAGE_LIMIT}
            if cursor:
                page_params["cursor"] = cursor
            page = self._request("GET", path, params=page_params)
            yield from page[key]
            cursor = page.get("next_cursor")
            if not cursor:
                return

    def find_project(self, name: str) -> str | None:
        """ID of the first project with this exact name (no server-side filter)."""
        for project in self._paginate("/projects", {}):
            if project["name"] == name:
                return str(project["id"])
        return None

    def create_project(self, name: str) -> str:
        return str(self._request("POST", "/projects", body={"name": name})["id"])

    def list_open_tasks(self, project_id: str) -> list[Task]:
        return [_to_task(item) for item in self._paginate("/tasks", {"project_id": project_id})]

    def list_completed_tasks(self, project_id: str, since: datetime, until: datetime) -> list[Task]:
        params = {
            "project_id": project_id,
            "since": since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "until": until.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        items = self._paginate("/tasks/completed/by_completion_date", params, key="items")
        return [_to_task(item) for item in items]

    def create_task(self, project_id: str, content: str, description: str, due: date) -> Task:
        body = {
            "project_id": project_id,
            "content": content,
            "description": description,
            "labels": [LABEL],
            "due_date": due.isoformat(),
        }
        return _to_task(self._request("POST", "/tasks", body=body))

    def update_due(self, task_id: str, due: date) -> None:
        self._request("POST", f"/tasks/{task_id}", body={"due_date": due.isoformat()})

    def close_task(self, task_id: str) -> None:
        self._request("POST", f"/tasks/{task_id}/close")


@dataclass
class ProjectState:
    name: str
    id: str | None  # None: project doesn't exist yet, created on first write
    open_tasks: list[Task]
    completed_tasks: list[Task]


def load_state(client: TodoistClient, project_name: str, now: datetime) -> ProjectState:
    project_id = client.find_project(project_name)
    if project_id is None:
        log.info("Todoist project %r not found; it will be created on the first write", project_name)
        return ProjectState(project_name, None, [], [])
    return ProjectState(
        project_name,
        project_id,
        client.list_open_tasks(project_id),
        client.list_completed_tasks(project_id, now - COMPLETED_LOOKBACK, now),
    )


class Action(BaseModel):
    kind: Literal["create", "update_due", "close"]
    marker: str
    content: str
    description: str = ""
    due: date | None = None
    task_id: str | None = None


def build_plan(
    activity: Activity, summary: Summary, state: ProjectState, today: date, language: Language = "en"
) -> list[Action]:
    """Pure: decide what to write. Running it again after `execute` yields [].

    1. Open issue without an open task → create, due tomorrow.
    2. Open issue with an open task → roll its due date to tomorrow if earlier.
    3. Recently closed issue with an open task → close the task.
    4. Task completed in Todoist while the issue is still open → leave it.
    5. Non-empty `done` and no summary task for today → create one, due tomorrow
       so it shows up in the morning. Its title and marker keep today's date.
    """
    repo = activity.repo
    tomorrow = today + timedelta(days=1)
    open_by_marker: dict[str, Task] = {}
    for task in state.open_tasks:
        if task.marker:
            open_by_marker.setdefault(task.marker, task)
    completed_markers = {task.marker for task in state.completed_tasks if task.marker}
    urls = {issue.number: issue.url for issue in activity.open_issues}

    plan: list[Action] = []
    for item in summary.remaining:
        marker = issue_marker(repo, item.issue)
        task = open_by_marker.get(marker)
        if task:
            if task.due_date and task.due_date < tomorrow:
                plan.append(
                    Action(kind="update_due", marker=marker, content=task.content, due=tomorrow, task_id=task.id)
                )
        elif marker not in completed_markers:
            plan.append(
                Action(
                    kind="create",
                    marker=marker,
                    content=f"{item.title} (#{item.issue})",
                    description=f"{urls.get(item.issue, '')}\n\n{marker}".strip(),
                    due=tomorrow,
                )
            )

    for issue in activity.closed_issues:
        marker = issue_marker(repo, issue.number)
        task = open_by_marker.get(marker)
        if task:
            plan.append(Action(kind="close", marker=marker, content=task.content, task_id=task.id))

    marker = summary_marker(repo, today)
    if summary.done and marker not in open_by_marker and marker not in completed_markers:
        bullets = "\n".join(f"- {item}" for item in summary.done)
        plan.append(
            Action(
                kind="create",
                marker=marker,
                content=SUMMARY_TITLES[language].format(date=today.isoformat()),
                description=f"{bullets}\n\n{marker}",
                due=tomorrow,
            )
        )
    return plan


def format_plan(plan: list[Action]) -> str:
    if not plan:
        return "Plan: 0 actions"
    lines = [f"Plan: {len(plan)} action(s)"]
    for action in plan:
        if action.kind == "create":
            lines.append(f"  + create  {action.content}  (due {action.due})  {action.marker}")
            lines += [f"            {line}" for line in action.description.splitlines() if line.startswith("- ")]
        elif action.kind == "update_due":
            lines.append(f"  ~ due →{action.due}  {action.content}  {action.marker}")
        else:
            lines.append(f"  ✓ close   {action.content}  {action.marker}")
    return "\n".join(lines)


def execute(client: TodoistClient, state: ProjectState, plan: list[Action]) -> None:
    if not plan:
        return
    if state.id is None:
        state.id = client.create_project(state.name)
        log.info("created Todoist project %r", state.name)
    for action in plan:
        if action.kind == "create":
            assert action.due is not None
            client.create_task(state.id, action.content, action.description, action.due)
        elif action.kind == "update_due":
            assert action.task_id and action.due
            client.update_due(action.task_id, action.due)
        else:
            assert action.task_id
            client.close_task(action.task_id)
        log.info("done: %s %s", action.kind, action.marker)
