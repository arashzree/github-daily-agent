"""Orchestrates the nightly run: github_client -> ai -> todoist_client. Supports --dry-run."""

import argparse
import logging
import sys
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from agent import ai
from agent.config import Config, load_config, require_env
from agent.github_client import GitHubClient
from agent.providers.base import Activity, AIProvider
from agent.todoist_client import (
    TodoistClient,
    build_plan,
    execute,
    format_plan,
    load_state,
)

log = logging.getLogger("agent")


def run(
    config: Config,
    *,
    github: GitHubClient,
    provider: AIProvider,
    todoist: TodoistClient,
    dry_run: bool,
    now: datetime,
) -> None:
    repo = config.target_repo
    since = now - timedelta(hours=config.lookback_hours)
    log.info("fetching %s activity since %s", repo, since.isoformat(timespec="minutes"))
    activity = Activity(
        repo=repo,
        commits=github.fetch_commits(repo, since),
        open_issues=github.fetch_open_issues(repo),
        closed_issues=github.fetch_recently_closed_issues(repo, since),
    )
    log.info(
        "%d commits, %d open issues, %d recently closed",
        len(activity.commits),
        len(activity.open_issues),
        len(activity.closed_issues),
    )
    if activity.is_empty():
        log.info("no activity; nothing to do")
        return

    # Read Todoist before the slow AI call so a bad token fails fast.
    state = load_state(todoist, config.todoist_project, now)
    log.info("Todoist: %d open, %d recently completed tasks", len(state.open_tasks), len(state.completed_tasks))

    log.info("summarizing with %s/%s", config.ai_provider, config.ai_model)
    summary = ai.summarize(provider, activity)

    today = now.astimezone(ZoneInfo(config.timezone)).date()
    plan = build_plan(activity, summary, state, today, config.output_language)
    print(format_plan(plan), flush=True)

    if dry_run:
        log.info("dry run: nothing written to Todoist")
        return
    execute(todoist, state, plan)
    log.info("executed %d action(s)", len(plan))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent", description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print the plan, don't write to Todoist")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    try:
        config = load_config()
        run(
            config,
            github=GitHubClient.from_config(config, require_env("GH_PAT")),
            provider=ai.make_provider(config),
            todoist=TodoistClient(require_env("TODOIST_API_TOKEN")),
            dry_run=args.dry_run,
            now=datetime.now(UTC),
        )
    except Exception:
        log.exception("run failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
