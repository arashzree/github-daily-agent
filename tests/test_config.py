import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent.config import load_config

BASE = {
    "target_repo": "owner/repo",
    "timezone": "Asia/Tehran",
    "ai_provider": "ollama",
    "ai_model": "m",
    "task_sink": "todoist",
    "todoist_project": "Daily Agent",
}


def write(tmp_path: Path, **extra: object) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({**BASE, **extra}))
    return path


def test_defaults(tmp_path: Path) -> None:
    config = load_config(write(tmp_path))
    assert config.output_language == "en"
    assert (config.max_commits, config.max_issues, config.issue_body_chars) == (50, 30, 300)


def test_output_language_fa(tmp_path: Path) -> None:
    assert load_config(write(tmp_path, output_language="fa")).output_language == "fa"


@pytest.mark.parametrize("extra", [{"output_language": "de"}, {"unknown_key": 1}, {"target_repo": "no-slash"}])
def test_rejects_invalid(tmp_path: Path, extra: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        load_config(write(tmp_path, **extra))


def test_repo_config_is_valid() -> None:
    assert load_config().output_language == "en"
