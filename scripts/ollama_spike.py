"""T1 spike: pull local models via Ollama and check Persian JSON output.

Runs on the GitHub Actions runner (see .github/workflows/models-spike.yml).
Each argument is a comma-separated list of candidate tags; the first one
that pulls successfully is used.
"""

import json
import subprocess
import sys
import time
import urllib.request

BASE_URL = "http://localhost:11434/v1"

SYSTEM = (
    "You turn GitHub activity into a Persian task summary. Reply with ONLY "
    'a JSON object of the form {"done":[string],"remaining":[string]}. '
    "All strings in Persian. No markdown, no code fences."
)
USER = (
    "Commits:\n"
    "- feat: add login page\n"
    "- fix: crash when config file is missing\n"
    "- docs: update README install steps\n\n"
    "Open issues:\n"
    "- #12 Dark mode toggle does not persist\n"
    "- #15 Add CSV export for reports"
)


def pull(candidates: list[str]) -> tuple[str | None, float]:
    start = time.monotonic()
    for tag in candidates:
        print(f"pulling {tag} ...", flush=True)
        result = subprocess.run(["ollama", "pull", tag], capture_output=True, text=True, check=False)
        if result.returncode == 0:
            return tag, time.monotonic() - start
        print(f"  failed: {result.stderr.strip()[-300:]}", flush=True)
    return None, time.monotonic() - start


def chat(model: str) -> tuple[str, float]:
    body = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": USER},
        ],
    }
    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    start = time.monotonic()
    with urllib.request.urlopen(req, timeout=900) as resp:
        data = json.load(resp)
    return data["choices"][0]["message"]["content"], time.monotonic() - start


def parses(text: str) -> bool:
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return False
    return isinstance(obj, dict) and {"done", "remaining"} <= obj.keys()


def main(groups: list[str]) -> int:
    failed = False
    for group in groups:
        candidates = group.split(",")
        print(f"\n===== {group} =====", flush=True)
        model, pull_s = pull(candidates)
        print(f"pull: {pull_s:.1f}s -> {model}")
        if model is None:
            failed = True
            continue
        subprocess.run(["ollama", "show", model], check=False)
        try:
            content, infer_s = chat(model)
        except Exception as exc:  # noqa: BLE001 - spike: report and move on
            print(f"inference error: {exc!r}")
            failed = True
            continue
        print(f"inference: {infer_s:.1f}s")
        print("--- raw output ---")
        print(content)
        print("--- end raw output ---")
        print(f"JSON parses with done/remaining keys: {parses(content)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
