# github-daily-agent

Nightly agent: GitHub → LLM → Todoist. Runs only in GitHub Actions; no
server, no database.

## Layout

One module per concern:

- `github_client.py` — reads commits/issues from the target repo
- `ai.py` — summarizes activity into task candidates via an LLM provider
- `todoist_client.py` — creates/dedupes tasks in Todoist
- `main.py` — orchestrates the above only; no business logic here
- `providers/` — swappable AI providers and task sinks

## Rules

- Secrets only via environment variables. Never in code or commits.
- Type hints everywhere.
- Todoist API v1 only (`https://api.todoist.com/api/v1`). Never REST v2.
- Every run must support `--dry-run` (print, don't write).

## Commits

Small, conventional: `feat:`, `fix:`, `docs:`, `chore:`, `test:`.

## Before saying "done"

Run `ruff check .` and the relevant command, and show the real terminal
output.

## Task status

Lives in `docs/PLAN.md`, not here.
