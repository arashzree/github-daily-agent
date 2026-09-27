from datetime import UTC, datetime
from typing import Any

import pytest
from fakes import FakeTodoist

from agent import main
from agent.config import Config
from agent.github_client import Commit, Issue
from agent.providers.base import Activity, AIError, RemainingItem, Summary

NOW = datetime(2026, 9, 27, 4, 0, tzinfo=UTC)
CONFIG = Config(
    target_repo="owner/repo",
    timezone="Asia/Tehran",
    ai_provider="ollama",
    ai_model="m",
    task_sink="todoist",
    todoist_project="Daily Agent",
)
COMMIT = Commit(sha="abc1234", message="feat: x", author="octo", date=NOW, url="u")
ISSUE = Issue(number=1, title="Issue 1", labels=[], body="", url="https://x/1")


class FakeGitHub:
    def __init__(self, *, commits: list[Commit], open_issues: list[Issue]) -> None:
        self.commits = commits
        self.open_issues = open_issues

    def fetch_commits(self, repo: str, since: datetime) -> list[Commit]:
        return self.commits

    def fetch_open_issues(self, repo: str) -> list[Issue]:
        return self.open_issues

    def fetch_recently_closed_issues(self, repo: str, since: datetime) -> list[Issue]:
        return []


class FakeProvider:
    def __init__(self, result: Summary | AIError) -> None:
        self.result = result
        self.calls = 0

    def summarize(self, activity: Activity) -> Summary:
        self.calls += 1
        if isinstance(self.result, AIError):
            raise self.result
        return self.result


SUMMARY = Summary(done=["Add Todoist client"], remaining=[RemainingItem(issue=1, title="Write ai module")])


def run(todoist: FakeTodoist, provider: FakeProvider, *, dry_run: bool, **github: Any) -> None:
    gh = FakeGitHub(commits=github.get("commits", [COMMIT]), open_issues=github.get("open_issues", [ISSUE]))
    main.run(CONFIG, github=gh, provider=provider, todoist=todoist, dry_run=dry_run, now=NOW)  # type: ignore[arg-type]


def test_dry_run_prints_plan_and_writes_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    todoist = FakeTodoist()
    run(todoist, FakeProvider(SUMMARY), dry_run=True)

    out = capsys.readouterr().out
    assert "Plan: 2 action(s)" in out
    assert "Write ai module (#1)" in out
    # 04:00 UTC is 07:30 in Tehran, so "today" is the 27th; the summary is due the 28th.
    assert "Done on 2026-09-27  (due 2026-09-28)" in out
    assert todoist.writes == []


def test_real_run_executes_then_second_run_is_empty(capsys: pytest.CaptureFixture[str]) -> None:
    todoist = FakeTodoist()
    run(todoist, FakeProvider(SUMMARY), dry_run=False)
    assert len(todoist.writes) == 2

    run(todoist, FakeProvider(SUMMARY), dry_run=False)
    assert len(todoist.writes) == 2
    assert capsys.readouterr().out.rstrip().endswith("Plan: 0 actions")


def test_no_activity_skips_ai_and_todoist() -> None:
    provider = FakeProvider(SUMMARY)
    todoist = FakeTodoist()
    run(todoist, provider, dry_run=False, commits=[], open_issues=[])
    assert provider.calls == 0
    assert todoist.writes == []


def test_bad_ai_output_writes_nothing() -> None:
    todoist = FakeTodoist()
    with pytest.raises(AIError):
        run(todoist, FakeProvider(AIError("bad")), dry_run=False)
    assert todoist.writes == []


def test_main_returns_nonzero_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> Config:
        raise RuntimeError("no config")

    monkeypatch.setattr(main, "load_config", boom)
    assert main.main(["--dry-run"]) == 1
