"""Summarizes GitHub activity into task candidates using a configured LLM provider."""

import logging

from agent.config import Config
from agent.providers.base import Activity, AIError, AIProvider, RemainingItem, Summary
from agent.providers.ollama import OllamaProvider

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 2  # one retry on invalid output, then give up


def make_provider(config: Config) -> AIProvider:
    if config.ai_provider == "ollama":
        return OllamaProvider(config.ai_model, config.output_language)
    raise ValueError(f"unknown ai_provider: {config.ai_provider!r}")


def summarize(provider: AIProvider, activity: Activity) -> Summary:
    """Ask the provider for a summary, retrying once, then align it with the input.

    Raises AIError if both attempts fail, so nothing is written on bad output.
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            summary = provider.summarize(activity)
        except AIError as exc:
            if attempt == MAX_ATTEMPTS:
                raise
            log.warning("AI attempt %d failed (%s); retrying once", attempt, exc)
            continue
        return reconcile(summary, activity)
    raise AssertionError("unreachable")


def reconcile(summary: Summary, activity: Activity) -> Summary:
    """Make `remaining` exactly one item per open issue, in input order.

    Items for unknown issue numbers are dropped; open issues the AI skipped
    fall back to their original title. `done` is emptied when there was
    nothing to report, since it may only come from commits and closed issues.
    """
    open_numbers = {issue.number for issue in activity.open_issues}
    titles: dict[int, str] = {}
    for item in summary.remaining:
        if item.issue not in open_numbers:
            log.warning("AI returned unknown issue #%d; dropping it", item.issue)
            continue
        titles.setdefault(item.issue, item.title)

    remaining: list[RemainingItem] = []
    for issue in activity.open_issues:
        if issue.number not in titles:
            log.warning("AI skipped issue #%d; using its original title", issue.number)
        remaining.append(RemainingItem(issue=issue.number, title=titles.get(issue.number, issue.title)))

    done = list(dict.fromkeys(summary.done))
    if done and not (activity.commits or activity.closed_issues):
        log.warning("AI returned done items with no commits or closed issues; ignoring them")
        done = []
    return Summary(done=done, remaining=remaining)
