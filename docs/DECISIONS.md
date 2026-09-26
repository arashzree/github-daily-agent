# Decisions

- **Python** — owner's main language.
- **Separate agent repo** — reusable, portfolio piece.
- ~~GitHub Models~~ — dropped: fully retired 2026-07-30 (see
  `docs/API_NOTES.md`).
- **Local LLM via Ollama inside GitHub Actions** — hosted LLM providers
  (Gemini, Groq, NVIDIA) are blocked for the owner's account/region. The
  model runs on the CPU runner via Ollama's native API
  (`POST http://localhost:11434/api/chat`). No API key and no cost. Slower, and
  output quality is capped by what fits a 16 GB CPU runner.
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
- **Agent targets its own repo** — for testing/dogfooding until the real
  target repo exists.
