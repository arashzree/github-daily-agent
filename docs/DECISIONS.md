# Decisions

- **Python** — owner's main language.
- **Separate agent repo** — reusable, portfolio piece.
- **GitHub Models** — free, uses `GITHUB_TOKEN` in Actions.
- **Todoist** — free API, good mobile app.
- **Pluggable providers** — swap to Claude API / Motion / Notion later via
  config, without rewriting orchestration.
- **Dedupe via marker in task description** — avoids needing a database.
- **Agent targets its own repo** — for testing/dogfooding until the real
  target repo exists.
