# Decisions

- **Python** — owner's main language.
- **Separate agent repo** — reusable, portfolio piece.
- ~~GitHub Models~~ — dropped: fully retired 2026-07-30 (see
  `docs/API_NOTES.md`).
- **Local LLM via Ollama inside GitHub Actions** — hosted LLM providers
  (Gemini, Groq, NVIDIA) are blocked for the owner's account/region. The
  model runs on the CPU runner via Ollama's native API
  (`POST http://localhost:11434/api/chat`). No API key and no cost. Slower, and
  output quality is capped by what fits a 16 GB CPU runner. Side benefits:
  $0 per run, repo activity is never sent to a third-party LLM vendor (only
  the finished tasks go to Todoist), and `ai.py` only depends on the
  provider interface in `providers/base.py`, so a hosted provider can be
  swapped in via `ai_provider`.
- **Model: `qwen3.5:9b`** (Q4_K_M) with `"think": false` and a JSON-schema
  `format`. In T1 it returned the correct Persian JSON in ~20 s on CPU and
  loads faster than `gemma4:e4b` (5 s vs 14 s).
- **No `actions/cache` for models** — pull from the Ollama registry on
  every run. In T1 the 14 GB cache restore took 2 min 46 s and then timed
  out, while a direct pull took 24 s for Qwen. Caching adds time and a
  failure mode but no speedup.
- **Todoist** — free API, good mobile app.
- **Pluggable providers** — swap to Claude API / Motion / Notion later via
  config, without rewriting orchestration.
- **Dedupe via marker in task description** — avoids needing a database.
- **Target switched to `arashzree/Regulars`** (2026-09-27, from dogfooding its own repo) for the T7 3-night live test.
- **English output by default** (`output_language`: `en` | `fa`) — the
  first Persian runs read awkwardly (technical terms were translated, e.g.
  "client" became "customer"). The prompt now keeps technical terms as-is
  and asks for short imperative titles.
- **Summary task due tomorrow** — like issue tasks, so the day's recap is
  on the list in the morning. Its title keeps the activity date.
- **Nightly at 20:00 UTC (23:30 Tehran)** — Iran has no DST since 2022, so
  a fixed UTC cron is stable. Scheduled runs write; manual runs default to
  dry-run. A concurrency group prevents overlapping runs creating
  duplicates.
- **Failure alert via Todoist** — a failed run files "Daily agent failed —
  <run url>" due today, so failures show up where the owner already looks. Uses
  plain `curl` so it works even if Python setup failed; step-level
  timeouts make hangs count as failures (a job timeout would cancel the
  job and skip the alert).
- **Keepalive workflow** — GitHub disables schedules in public repos after
  60 days without repo activity. The agent only reads, so a monthly job
  re-enables `agent.yml` (and itself) via the API.
