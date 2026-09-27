import json
from datetime import UTC, datetime
from typing import Any

import pytest
import requests
from fakes import response

from agent import ai
from agent.github_client import Commit, Issue
from agent.providers.base import (
    Activity,
    AIError,
    RemainingItem,
    Summary,
    parse_summary,
    render_activity,
    system_prompt,
)
from agent.providers.ollama import OllamaProvider


def make_issue(number: int, title: str = "") -> Issue:
    return Issue(number=number, title=title or f"Issue {number}", labels=[], body="", url=f"https://x/{number}")


COMMIT = Commit(sha="abc1234", message="feat: x", author="octo", date=datetime(2026, 9, 26, tzinfo=UTC), url="u")


def make_activity(open_numbers: list[int], *, commits: bool = True) -> Activity:
    return Activity(
        repo="owner/repo",
        commits=[COMMIT] if commits else [],
        open_issues=[make_issue(n) for n in open_numbers],
        closed_issues=[],
    )


class ScriptedProvider:
    def __init__(self, *results: Summary | AIError) -> None:
        self.results = list(results)
        self.calls = 0

    def summarize(self, activity: Activity) -> Summary:
        self.calls += 1
        result = self.results.pop(0)
        if isinstance(result, AIError):
            raise result
        return result


def test_parse_summary_rejects_wrong_shape_and_empty_strings() -> None:
    ok = parse_summary('{"done": [" کار "], "remaining": [{"issue": 1, "title": "بنویس"}]}')
    assert ok.done == ["کار"]

    for bad in ['{"done": "a, b", "remaining": []}', '{"done": [""], "remaining": []}', "not json", '{"done": []}']:
        with pytest.raises(AIError):
            parse_summary(bad)


def test_unknown_issue_dropped_and_missing_issue_falls_back() -> None:
    activity = make_activity([1, 2, 3])
    summary = Summary(
        done=["الف"],
        remaining=[
            RemainingItem(issue=3, title="سوم"),
            RemainingItem(issue=99, title="ساختگی"),
            RemainingItem(issue=1, title="اول"),
        ],
    )
    result = ai.summarize(ScriptedProvider(summary), activity)

    assert [(r.issue, r.title) for r in result.remaining] == [(1, "اول"), (2, "Issue 2"), (3, "سوم")]
    assert result.done == ["الف"]


def test_duplicate_items_keep_first() -> None:
    summary = Summary(
        done=["الف", "الف"],
        remaining=[RemainingItem(issue=1, title="اول"), RemainingItem(issue=1, title="دوباره")],
    )
    result = ai.reconcile(summary, make_activity([1]))
    assert result.done == ["الف"]
    assert result.remaining[0].title == "اول"


def test_done_dropped_without_commits_or_closed_issues() -> None:
    summary = Summary(done=["ساختگی"], remaining=[])
    assert ai.reconcile(summary, make_activity([], commits=False)).done == []


def test_retries_once_on_invalid_output() -> None:
    provider = ScriptedProvider(AIError("bad json"), Summary(done=[], remaining=[]))
    ai.summarize(provider, make_activity([]))
    assert provider.calls == 2


def test_raises_after_second_invalid_output() -> None:
    provider = ScriptedProvider(AIError("bad"), AIError("still bad"))
    with pytest.raises(AIError, match="still bad"):
        ai.summarize(provider, make_activity([]))
    assert provider.calls == 2


def test_system_prompt_language_and_style_rules() -> None:
    en, fa = system_prompt("en"), system_prompt("fa")
    for prompt in (en, fa):
        assert "at most ~8 words" in prompt
        assert "README, CI," in prompt and "dry-run, client" in prompt
        assert "{language_rule}" not in prompt
        assert '{"done": [string]' in prompt
    assert "Write all text in English." in en
    assert "Persian (Farsi), except the technical terms above" in fa


def test_render_activity_lists_everything() -> None:
    activity = make_activity([7])
    activity.open_issues[0].body = "line one\nline two"
    text = render_activity(activity)
    assert "- feat: x" in text
    assert "- #7 Issue 7" in text
    assert "line one line two" in text
    assert "Closed issues:\n(none)" in text


class FakePostSession:
    def __init__(self, result: requests.Response | Exception) -> None:
        self.result = result
        self.payload: dict[str, Any] = {}

    def post(self, url: str, json: dict[str, Any], timeout: float) -> requests.Response:
        self.url = url
        self.payload = json
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_ollama_request_and_parse() -> None:
    content = json.dumps({"done": ["الف"], "remaining": [{"issue": 1, "title": "اول"}]})
    session = FakePostSession(response(body={"message": {"content": content}, "eval_count": 5}))
    summary = OllamaProvider("qwen3.5:9b", session=session).summarize(make_activity([1]))  # type: ignore[arg-type]

    assert summary.remaining == [RemainingItem(issue=1, title="اول")]
    assert session.url == "http://localhost:11434/api/chat"
    p = session.payload
    assert (p["model"], p["think"], p["stream"], p["options"]["temperature"]) == ("qwen3.5:9b", False, False, 0)
    assert p["format"]["required"] == ["done", "remaining"]
    assert [m["role"] for m in p["messages"]] == ["system", "user"]
    assert "Write all text in English." in p["messages"][0]["content"]


def test_ollama_uses_configured_language() -> None:
    content = json.dumps({"done": [], "remaining": []})
    session = FakePostSession(response(body={"message": {"content": content}}))
    OllamaProvider("m", "fa", session=session).summarize(make_activity([]))  # type: ignore[arg-type]
    assert "Persian" in session.payload["messages"][0]["content"]


@pytest.mark.parametrize(
    "result",
    [requests.ConnectionError("down"), response(500, body={"error": "x"}), response(body={"nope": 1})],
)
def test_ollama_failures_raise_ai_error(result: requests.Response | Exception) -> None:
    with pytest.raises(AIError):
        OllamaProvider("m", session=FakePostSession(result)).summarize(make_activity([]))  # type: ignore[arg-type]


def test_make_provider_passes_language() -> None:
    config = type("C", (), {"ai_provider": "ollama", "ai_model": "m", "output_language": "fa"})()
    provider = ai.make_provider(config)  # type: ignore[arg-type]
    assert isinstance(provider, OllamaProvider)
    assert provider.language == "fa"


def test_make_provider_rejects_unknown() -> None:
    config = type("C", (), {"ai_provider": "nope", "ai_model": "m"})()
    with pytest.raises(ValueError):
        ai.make_provider(config)  # type: ignore[arg-type]
