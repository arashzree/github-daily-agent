"""AI provider interface, the data it exchanges with ai.py, and the shared prompt."""

from typing import Annotated, Any, Protocol

from pydantic import BaseModel, StringConstraints, ValidationError

from agent.github_client import Commit, Issue

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class Activity(BaseModel):
    """What happened in the repo during the lookback window."""

    repo: str
    commits: list[Commit]
    open_issues: list[Issue]
    closed_issues: list[Issue]

    def is_empty(self) -> bool:
        return not (self.commits or self.open_issues or self.closed_issues)


class RemainingItem(BaseModel):
    issue: int
    title: Text


class Summary(BaseModel):
    """AI output. Contains no markers or IDs; code attaches those."""

    done: list[Text]
    remaining: list[RemainingItem]


class AIError(RuntimeError):
    """The provider failed or returned output that doesn't match the schema."""


class AIProvider(Protocol):
    def summarize(self, activity: Activity) -> Summary:
        """Return a schema-valid Summary or raise AIError."""
        ...


# Sent as Ollama's `format`; kept explicit (no $defs) so the model sees a flat schema.
SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "done": {"type": "array", "items": {"type": "string"}},
        "remaining": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"issue": {"type": "integer"}, "title": {"type": "string"}},
                "required": ["issue", "title"],
            },
        },
    },
    "required": ["done", "remaining"],
}

SYSTEM_PROMPT = """\
You turn a GitHub repository's recent activity into a developer's Persian to-do list.
Reply with ONLY a JSON object: {"done": [string], "remaining": [{"issue": int, "title": string}]}.

- "done": what was accomplished, based ONLY on the commits and closed issues. Merge
  related commits into one item. Short past-tense Persian phrases, at most 10 items.
  Empty list if there are no commits and no closed issues.
- "remaining": exactly one entry per open issue, using that issue's number. "title" is a
  short, actionable Persian task (imperative, at most 10 words) saying what to do next.
- Write natural Persian. Keep code identifiers, file names and product names in English.
- Never invent issues. No markdown, no code fences, no issue numbers inside titles."""


def render_activity(activity: Activity) -> str:
    """Plain-text view of the activity for the user message."""
    lines = [f"Repository: {activity.repo}", "", "Commits:"]
    lines += [f"- {c.message}" for c in activity.commits] or ["(none)"]
    lines += ["", "Closed issues:"]
    lines += [f"- #{i.number} {i.title}" for i in activity.closed_issues] or ["(none)"]
    lines += ["", "Open issues:"]
    for issue in activity.open_issues:
        labels = f" [{', '.join(issue.labels)}]" if issue.labels else ""
        lines.append(f"- #{issue.number} {issue.title}{labels}")
        if issue.body:
            lines.append("  " + issue.body.replace("\n", " "))
    if not activity.open_issues:
        lines.append("(none)")
    return "\n".join(lines)


def parse_summary(text: str) -> Summary:
    """Validate raw model output against the Summary schema."""
    try:
        return Summary.model_validate_json(text)
    except ValidationError as exc:
        raise AIError(f"AI output doesn't match schema: {exc.error_count()} error(s); got {text[:200]!r}") from exc


def build_messages(activity: Activity) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": render_activity(activity)},
    ]

