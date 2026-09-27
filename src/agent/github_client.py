"""Fetches commits and issues from the target GitHub repo for the lookback window."""

import sys
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import requests
from pydantic import BaseModel

from agent.config import Config, load_config, require_env
from agent.retry import send

API_URL = "https://api.github.com"
PER_PAGE = 100


class GitHubError(RuntimeError):
    """A GitHub API call failed in a way retrying won't fix."""


class Commit(BaseModel):
    sha: str
    message: str
    author: str
    date: datetime
    url: str


class Issue(BaseModel):
    number: int
    title: str
    labels: list[str]
    body: str
    url: str
    closed_at: datetime | None = None


def _iso_utc(when: datetime) -> str:
    if when.tzinfo is None:
        raise ValueError("since must be timezone-aware")
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _truncate(text: str | None, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    if limit == 0:
        return ""
    return text[: limit - 1].rstrip() + "…"


class GitHubClient:
    def __init__(
        self,
        token: str,
        *,
        max_commits: int = 50,
        max_issues: int = 30,
        issue_body_chars: int = 300,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.max_commits = max_commits
        self.max_issues = max_issues
        self.issue_body_chars = issue_body_chars
        self._sleep = sleep
        self._session = session or requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "github-daily-agent",
            }
        )

    @classmethod
    def from_config(cls, config: Config, token: str) -> "GitHubClient":
        return cls(
            token,
            max_commits=config.max_commits,
            max_issues=config.max_issues,
            issue_body_chars=config.issue_body_chars,
        )

    def _get(self, url: str, params: dict[str, Any] | None) -> requests.Response:
        resp = send(
            self._session, "GET", url, params=params, service="GitHub", error=GitHubError, sleep=self._sleep
        )
        status = resp.status_code
        if status in (403, 429) and resp.headers.get("X-RateLimit-Remaining") == "0":
            reset = resp.headers.get("X-RateLimit-Reset")
            when = datetime.fromtimestamp(int(reset), UTC).isoformat() if reset else "unknown"
            raise GitHubError(f"GitHub rate limit exhausted (resets at {when})")
        if status in (401, 403, 404):
            raise GitHubError(f"GitHub returned {status} for {url}: check GH_PAT / repo access")
        if not resp.ok:
            raise GitHubError(f"GitHub returned {status} for {url}: {resp.text[:200]}")
        return resp

    def _paginate(self, path: str, params: dict[str, Any]) -> Iterator[dict[str, Any]]:
        """Yield items from every page, following the Link header."""
        url: str | None = f"{API_URL}{path}"
        page_params: dict[str, Any] | None = {**params, "per_page": PER_PAGE}
        while url:
            resp = self._get(url, page_params)
            yield from resp.json()
            url = resp.links.get("next", {}).get("url")
            page_params = None  # the next URL already carries the query

    def _to_issue(self, item: dict[str, Any]) -> Issue:
        return Issue(
            number=item["number"],
            title=item["title"],
            labels=[label["name"] for label in item.get("labels", [])],
            body=_truncate(item.get("body"), self.issue_body_chars),
            url=item["html_url"],
            closed_at=datetime.fromisoformat(item["closed_at"]) if item.get("closed_at") else None,
        )

    def fetch_commits(self, repo: str, since: datetime) -> list[Commit]:
        """Non-merge commits since `since`, newest first, capped at max_commits."""
        commits: list[Commit] = []
        for item in self._paginate(f"/repos/{repo}/commits", {"since": _iso_utc(since)}):
            if len(item.get("parents", [])) > 1:
                continue
            git = item["commit"]
            author = (item.get("author") or {}).get("login") or git["author"]["name"]
            commits.append(
                Commit(
                    sha=item["sha"][:7],
                    message=git["message"].split("\n", 1)[0],
                    author=author,
                    date=datetime.fromisoformat(git["author"]["date"]),
                    url=item["html_url"],
                )
            )
            if len(commits) >= self.max_commits:
                break
        return commits

    def fetch_open_issues(self, repo: str) -> list[Issue]:
        """Open issues (no PRs), most recently updated first, capped at max_issues."""
        issues: list[Issue] = []
        params = {"state": "open", "sort": "updated", "direction": "desc"}
        for item in self._paginate(f"/repos/{repo}/issues", params):
            if "pull_request" in item:
                continue
            issues.append(self._to_issue(item))
            if len(issues) >= self.max_issues:
                break
        return issues

    def fetch_recently_closed_issues(self, repo: str, since: datetime) -> list[Issue]:
        """Issues (no PRs) closed at/after `since`, capped at max_issues.

        The API's `since` filters on updated_at, so closed_at is checked here.
        """
        issues: list[Issue] = []
        params = {"state": "closed", "since": _iso_utc(since), "sort": "updated", "direction": "desc"}
        for item in self._paginate(f"/repos/{repo}/issues", params):
            if "pull_request" in item or not item.get("closed_at"):
                continue
            if datetime.fromisoformat(item["closed_at"]) < since:
                continue
            issues.append(self._to_issue(item))
            if len(issues) >= self.max_issues:
                break
        return issues


def main() -> None:
    """Manual check: print a summary of recent activity in the target repo."""
    config = load_config()
    client = GitHubClient.from_config(config, require_env("GH_PAT"))
    since = datetime.now(UTC) - timedelta(hours=config.lookback_hours)
    repo = config.target_repo
    try:
        commits = client.fetch_commits(repo, since)
        open_issues = client.fetch_open_issues(repo)
        closed = client.fetch_recently_closed_issues(repo, since)
    except GitHubError as exc:
        sys.exit(f"error: {exc}")

    print(f"{repo} — since {since:%Y-%m-%d %H:%M} UTC ({config.lookback_hours}h)\n")
    print(f"Commits ({len(commits)}):")
    for c in commits:
        print(f"  {c.sha} {c.date:%Y-%m-%d %H:%M} {c.author}: {c.message}")
    print(f"\nOpen issues ({len(open_issues)}):")
    for i in open_issues:
        labels = f" [{', '.join(i.labels)}]" if i.labels else ""
        print(f"  #{i.number} {i.title}{labels}")
    print(f"\nRecently closed issues ({len(closed)}):")
    for i in closed:
        print(f"  #{i.number} {i.title} (closed {i.closed_at:%Y-%m-%d %H:%M})")


if __name__ == "__main__":
    main()
