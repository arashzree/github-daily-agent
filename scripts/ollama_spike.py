"""T1 spike: pull local models via Ollama and check Persian JSON output.

Uses the native /api/chat with thinking off and a JSON-schema format.
Runs on the GitHub Actions runner (see .github/workflows/models-spike.yml).
Each argument is a comma-separated list of candidate tags; the first one
that pulls successfully is used.
"""

import json
import subprocess
import sys
import time
import urllib.request

BASE_URL = "http://localhost:11434"

SCHEMA = {
    "type": "object",
    "properties": {
        "done": {"type": "array", "items": {"type": "string"}},
        "remaining": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["done", "remaining"],
}

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


def chat(model: str) -> tuple[str, float, dict[str, float]]:
    body = {
        "model": model,
        "stream": False,
        "think": False,
        "format": SCHEMA,
        "options": {"temperature": 0},
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": USER},
        ],
    }
    req = urllib.request.Request(
        f"{BASE_URL}/api/chat",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    start = time.monotonic()
    with urllib.request.urlopen(req, timeout=900) as resp:
        data = json.load(resp)
    stats = {
        "load_s": data.get("load_duration", 0) / 1e9,
        "prompt_tokens": data.get("prompt_eval_count", 0),
        "output_tokens": data.get("eval_count", 0),
        "gen_s": data.get("eval_duration", 0) / 1e9,
    }
    if data["message"].get("thinking"):
        print(f"WARNING: thinking present ({len(data['message']['thinking'])} chars)")
    return data["message"]["content"], time.monotonic() - start, stats


def parses(text: str) -> bool:
    try:
        return isinstance(json.loads(text), dict)
    except json.JSONDecodeError:
        return False


def schema_ok(text: str) -> bool:
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return False
    return isinstance(obj, dict) and all(
        isinstance(obj.get(key), list)
        and all(isinstance(item, str) and item.strip() for item in obj[key])
        for key in ("done", "remaining")
    )


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
            content, infer_s, stats = chat(model)
        except Exception as exc:  # noqa: BLE001 - spike: report and move on
            print(f"inference error: {exc!r}")
            failed = True
            continue
        print(f"inference: {infer_s:.1f}s (load {stats['load_s']:.1f}s, "
              f"{stats['prompt_tokens']:.0f} prompt tok, "
              f"{stats['output_tokens']:.0f} output tok in {stats['gen_s']:.1f}s)")
        print("--- raw output ---")
        print(content)
        print("--- end raw output ---")
        print(f"JSON parses: {parses(content)}")
        print(f"schema ok (done/remaining are lists of non-empty strings): {schema_ok(content)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
