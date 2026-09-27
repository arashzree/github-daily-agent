"""Ollama provider: native /api/chat with thinking off and a JSON-schema `format`.

See docs/API_NOTES.md (T1) for why each option is set.
"""

import logging

import requests

from agent.providers.base import (
    SUMMARY_SCHEMA,
    Activity,
    AIError,
    Summary,
    build_messages,
    parse_summary,
)

log = logging.getLogger(__name__)

DEFAULT_URL = "http://localhost:11434"
# CPU inference on the Actions runner takes minutes for a full prompt.
TIMEOUT_SECONDS = 900
NUM_CTX = 8192


class OllamaProvider:
    def __init__(self, model: str, base_url: str = DEFAULT_URL, session: requests.Session | None = None) -> None:
        self.model = model
        self.base_url = base_url
        self._session = session or requests.Session()

    def summarize(self, activity: Activity) -> Summary:
        payload = {
            "model": self.model,
            "stream": False,
            "think": False,
            "format": SUMMARY_SCHEMA,
            "options": {"temperature": 0, "num_ctx": NUM_CTX},
            "messages": build_messages(activity),
        }
        try:
            resp = self._session.post(f"{self.base_url}/api/chat", json=payload, timeout=TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            raise AIError(f"Ollama request failed: {exc}") from exc
        if not resp.ok:
            raise AIError(f"Ollama returned {resp.status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
            content = data["message"]["content"]
        except (ValueError, KeyError, TypeError) as exc:
            raise AIError(f"Ollama response has no message.content: {resp.text[:200]!r}") from exc

        log.info(
            "Ollama %s: %s prompt / %s output tokens in %.1fs",
            self.model,
            data.get("prompt_eval_count"),
            data.get("eval_count"),
            data.get("total_duration", 0) / 1e9,
        )
        return parse_summary(content)
