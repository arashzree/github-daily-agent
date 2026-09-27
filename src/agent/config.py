"""Loads and validates config.json plus environment-provided secrets."""

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.json"


class Config(BaseModel):
    """Non-secret settings from config.json. Unknown keys are rejected."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    target_repo: str = Field(pattern=r"^[\w.-]+/[\w.-]+$")
    timezone: str
    ai_provider: str
    ai_model: str
    task_sink: str
    todoist_project: str
    lookback_hours: int = Field(default=24, gt=0)
    # Caps keep the prompt small enough for the local LLM's context.
    max_commits: int = Field(default=50, gt=0)
    max_issues: int = Field(default=30, gt=0)
    issue_body_chars: int = Field(default=300, ge=0)


def load_config(path: Path = CONFIG_PATH) -> Config:
    """Read and validate config.json."""
    return Config.model_validate_json(path.read_text(encoding="utf-8"))


def require_env(name: str) -> str:
    """Return a secret from the environment, loading .env first if present.

    Existing environment variables win over .env, so in GitHub Actions the
    repository secrets are used as-is.
    """
    load_dotenv()
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is not set (add it to .env locally or to repo secrets)")
    return value
