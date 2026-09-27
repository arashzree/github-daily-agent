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
