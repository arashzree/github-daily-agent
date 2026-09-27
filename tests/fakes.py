"""Test doubles shared by the test modules."""

import json
from typing import Any

import requests


def response(
    status: int = 200, body: Any = None, headers: dict[str, str] | None = None
) -> requests.Response:
    resp = requests.Response()
    resp.status_code = status
    resp._content = json.dumps(body if body is not None else []).encode()
    resp.headers.update(headers or {})
    return resp


def next_link(url: str) -> dict[str, str]:
    return {"Link": f'<{url}>; rel="next"'}


class FakeSession:
    """Stands in for requests.Session: returns queued responses or raises queued errors."""

    def __init__(self, *results: requests.Response | Exception) -> None:
        self.headers: dict[str, str] = {}
        self.results = list(results)
        self.calls: list[tuple[str, dict[str, Any] | None]] = []
        self.requests: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        self.calls.append((url, kwargs.get("params")))
        self.requests.append((method, url, kwargs))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeTodoist:
    """In-memory Todoist with the TodoistClient methods the sync code uses."""

    def __init__(self, project_id: str | None = "p1") -> None:
        from agent.todoist_client import Task

        self._task_cls = Task
        self.project_id = project_id
        self.open: list[Task] = []
        self.completed: list[Task] = []
        self.writes: list[str] = []

    def find_project(self, name: str) -> str | None:
        return self.project_id

    def create_project(self, name: str) -> str:
        self.writes.append(f"create_project {name}")
        self.project_id = "p-new"
        return self.project_id

    def list_open_tasks(self, project_id: str) -> list[Any]:
        return list(self.open)

    def list_completed_tasks(self, project_id: str, since: Any, until: Any) -> list[Any]:
        return list(self.completed)

    def create_task(self, project_id: str, content: str, description: str, due: Any) -> Any:
        self.writes.append(f"create {content}")
        task = self._task_cls(id=f"t{len(self.writes)}", content=content, description=description, due_date=due)
        self.open.append(task)
        return task

    def update_due(self, task_id: str, due: Any) -> None:
        self.writes.append(f"update_due {task_id}")
        for task in self.open:
            if task.id == task_id:
                task.due_date = due

    def close_task(self, task_id: str) -> None:
        self.writes.append(f"close {task_id}")
        task = next(t for t in self.open if t.id == task_id)
        self.open.remove(task)
        self.completed.append(task)
