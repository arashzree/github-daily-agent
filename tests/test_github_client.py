from datetime import UTC, datetime
from typing import Any

import pytest
import requests
from fakes import FakeSession, next_link, response

from agent.github_client import GitHubClient, GitHubError

REPO = "owner/repo"
SINCE = datetime(2026, 9, 26, 0, 0, tzinfo=UTC)


def make_client(session: FakeSession, **kwargs: Any) -> GitHubClient:
    return GitHubClient("tok", session=session, sleep=lambda _: None, **kwargs)  # type: ignore[arg-type]


def commit(sha: str, message: str = "feat: x", parents: int = 1) -> dict[str, Any]:
    return {
        "sha": sha * 40,
        "html_url": f"https://github.com/{REPO}/commit/{sha}",
        "author": {"login": "octo"},
        "parents": [{"sha": "p"}] * parents,
        "commit": {"message": message, "author": {"name": "Octo Cat", "date": "2026-09-26T10:00:00Z"}},
    }


def issue(number: int, *, body: str | None = "body", closed_at: str | None = None, pr: bool = False) -> dict[str, Any]:
    item: dict[str, Any] = {
        "number": number,
        "title": f"Issue {number}",
        "labels": [{"name": "roadmap"}],
        "body": body,
        "html_url": f"https://github.com/{REPO}/issues/{number}",
        "closed_at": closed_at,
    }
    if pr:
        item["pull_request"] = {"url": "..."}
    return item


def test_sets_auth_header() -> None:
    session = FakeSession()
    make_client(session)
    assert session.headers["Authorization"] == "Bearer tok"


def test_commits_follow_pagination_and_skip_merges() -> None:
    session = FakeSession(
        response(body=[commit("a", "feat: one\n\nlong body"), commit("m", "Merge branch", parents=2)],
                 headers=next_link("https://api.github.com/page2")),
        response(body=[commit("b")]),
    )
    commits = make_client(session).fetch_commits(REPO, SINCE)

    assert [c.sha for c in commits] == ["aaaaaaa", "bbbbbbb"]
    assert commits[0].message == "feat: one"
    assert commits[0].author == "octo"
    assert commits[0].date == datetime(2026, 9, 26, 10, 0, tzinfo=UTC)
    first_url, first_params = session.calls[0]
    assert first_url == f"https://api.github.com/repos/{REPO}/commits"
    assert first_params == {"since": "2026-09-26T00:00:00Z", "per_page": 100}
    assert session.calls[1] == ("https://api.github.com/page2", None)


def test_commit_author_falls_back_to_git_name() -> None:
    item = commit("a")
    item["author"] = None
    commits = make_client(FakeSession(response(body=[item]))).fetch_commits(REPO, SINCE)
    assert commits[0].author == "Octo Cat"


def test_since_is_converted_to_utc_and_must_be_aware() -> None:
    session = FakeSession(response(body=[]))
    tehran = datetime.fromisoformat("2026-09-26T03:30:00+03:30")
    make_client(session).fetch_commits(REPO, tehran)
    assert session.calls[0][1]["since"] == "2026-09-26T00:00:00Z"  # type: ignore[index]

    with pytest.raises(ValueError):
        make_client(FakeSession()).fetch_commits(REPO, datetime(2026, 9, 26))  # noqa: DTZ001 - naive on purpose


def test_commit_cap_stops_paging() -> None:
    session = FakeSession(
        response(body=[commit("a"), commit("b")], headers=next_link("https://api.github.com/page2")),
    )
    commits = make_client(session, max_commits=2).fetch_commits(REPO, SINCE)
    assert len(commits) == 2
    assert len(session.calls) == 1


def test_open_issues_exclude_prs_and_paginate() -> None:
    session = FakeSession(
        response(body=[issue(1), issue(2, pr=True)], headers=next_link("https://api.github.com/page2")),
        response(body=[issue(3, pr=True), issue(4)]),
    )
    issues = make_client(session).fetch_open_issues(REPO)

    assert [i.number for i in issues] == [1, 4]
    assert issues[0].labels == ["roadmap"]
    assert session.calls[0][1]["state"] == "open"  # type: ignore[index]


def test_open_issues_cap() -> None:
    session = FakeSession(response(body=[issue(n) for n in range(1, 6)]))
    issues = make_client(session, max_issues=3).fetch_open_issues(REPO)
    assert [i.number for i in issues] == [1, 2, 3]


def test_issue_body_truncated_and_none_becomes_empty() -> None:
    session = FakeSession(response(body=[issue(1, body="x" * 50), issue(2, body="short"), issue(3, body=None)]))
    issues = make_client(session, issue_body_chars=10).fetch_open_issues(REPO)

    assert len(issues[0].body) == 10
    assert issues[0].body.endswith("…")
    assert issues[1].body == "short"
    assert issues[2].body == ""


def test_recently_closed_filters_on_closed_at_and_prs() -> None:
    session = FakeSession(
        response(
            body=[
                issue(1, closed_at="2026-09-26T05:00:00Z"),
                issue(2, closed_at="2026-09-25T23:59:59Z"),  # updated recently, closed before since
                issue(3, closed_at="2026-09-26T00:00:00Z"),  # exactly at since
                issue(4, closed_at="2026-09-26T06:00:00Z", pr=True),
            ]
        )
    )
    issues = make_client(session).fetch_recently_closed_issues(REPO, SINCE)

    assert [i.number for i in issues] == [1, 3]
    assert issues[0].closed_at == datetime(2026, 9, 26, 5, 0, tzinfo=UTC)
    params = session.calls[0][1]
    assert params is not None
    assert params["state"] == "closed"
    assert params["since"] == "2026-09-26T00:00:00Z"


@pytest.mark.parametrize("status", [401, 403, 404])
def test_auth_errors_are_clear_and_not_retried(status: int) -> None:
    session = FakeSession(response(status, body={"message": "Bad credentials"}))
    with pytest.raises(GitHubError, match="check GH_PAT / repo access"):
        make_client(session).fetch_open_issues(REPO)
    assert len(session.calls) == 1


def test_rate_limit_403_detected() -> None:
    session = FakeSession(
        response(403, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1790000000"})
    )
    with pytest.raises(GitHubError, match="rate limit"):
        make_client(session).fetch_open_issues(REPO)


def test_retries_5xx_and_timeouts_then_succeeds() -> None:
    session = FakeSession(response(502), requests.Timeout("slow"), response(body=[issue(1)]))
    sleeps: list[float] = []
    client = GitHubClient("tok", session=session, sleep=sleeps.append)  # type: ignore[arg-type]

    assert [i.number for i in client.fetch_open_issues(REPO)] == [1]
    assert sleeps == [1, 2]


def test_gives_up_after_three_tries() -> None:
    session = FakeSession(response(500), response(503), response(500))
    with pytest.raises(GitHubError, match="after 3 tries"):
        make_client(session).fetch_open_issues(REPO)
    assert len(session.calls) == 3
