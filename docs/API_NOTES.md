# API notes (T1 spikes)

Verified with curl on 2026-09-26. Tokens come from env vars only.

## Todoist — API v1

Base: `https://api.todoist.com/api/v1`
Auth: `Authorization: Bearer $TODOIST_API_TOKEN`

### GET /projects

Response is a paginated envelope, not a bare list:

```json
{"results": [{"id": "6hch6gWvXhgvrPvM", "name": "Inbox", "inbox_project": true, "parent_id": null, "is_archived": false, ...}],
 "next_cursor": null}
```

- IDs are opaque strings (`"6hcrr8mG4q9FmMrH"`), not integers.
- No server-side filter by name: list, then match `name` client-side.

### POST /projects

Body: `{"name": "Daily Agent"}` → 200 with the project object (same shape
as one item of `results`). We didn't test whether names are unique, so
look the project up by name before creating it.

"Daily Agent" project id: `6hcrr8mG4q9FmMrH`.

### POST /tasks

Headers: `Content-Type: application/json`, `X-Request-Id: <unique id>`.

Body used:

```json
{"content": "T1 test", "project_id": "6hcrr8mG4q9FmMrH",
 "labels": ["gh-agent"], "due_string": "today",
 "description": "[gh-agent:test-1]"}
```

- Only `content` is required.
- `labels` takes label **names** (`["gh-agent"]`), not IDs, and the name
  comes back on the task. We didn't check whether a personal label has
  to exist first.
- `due_string: "today"` resolves to
  `"due": {"date": "2026-09-26", "string": "26 Sep", "lang": "en", "timezone": null, "is_recurring": false}`.
- Response is 200 with the full task object: `id`, `project_id`,
  `content`, `description`, `labels`, `due`, `priority`, `checked`,
  `added_at`, etc.

### GET /tasks?project_id=...

```json
{"results": [{"id": "6hcrrJvmWFxX3v3q", "content": "T1 test",
              "description": "[gh-agent:test-1]", "labels": ["gh-agent"], ...}],
 "next_cursor": null}
```

- `description` comes back byte-for-byte → the `[gh-agent:<key>]` marker
  works for dedupe.
- Pagination is cursor-based. `limit=1` returned
  `"next_cursor": "sDZo...M_8i..."`. Pass it back as `?cursor=...` until
  it is `null`. **The client has to loop over pages.**
- Only open tasks are listed. Dedupe against completed tasks would need a
  separate endpoint (not checked yet).

### DELETE /tasks/{id}

`DELETE /api/v1/tasks/6hcrrJvmWFxX3v3q` → `204` with an empty body (no
JSON to parse). After deleting both test tasks,
`GET /tasks?project_id=...` returned `{"results": [], "next_cursor": null}`.

### POST /tasks/{id} (update)

Verified 2026-09-27. Body `{"due_date": "2026-09-28"}` → 200 with the
full task. Creating with `due_date` (instead of `due_string`) also works
and is what the client uses, since it's unambiguous.

### POST /tasks/{id}/close

Verified 2026-09-27. → `204`, empty body. The task disappears from
`GET /tasks`.

### GET /tasks/completed/by_completion_date

Verified 2026-09-27. This is how the agent knows the user completed a task
while its issue is still open (so it isn't recreated).

```
GET /api/v1/tasks/completed/by_completion_date
    ?since=2026-09-20T00:00:00Z&until=2026-09-28T23:59:59Z&project_id=...&limit=200
→ {"items": [{"id": ..., "content": "T4 spike", "description": "[gh-agent:spike#0]",
              "checked": true, "completed_at": "2026-09-27T04:27:52.895147Z",
              "due": {"date": "2026-09-28", ...}, "labels": ["gh-agent"], ...}]}
```

- Items are full task objects and `description` is included, so the
  marker check works on completed tasks too.
- The list is under `items`, not `results`. `next_cursor` was absent
  when there was one page; the client treats missing and `null` the same.
- **Range limit:** `since`→`until` must not exceed 3 months. A wider range
  returns 400 `"completion date range must not exceed 3 months"`. The
  client looks back 89 days.
- `limit` max is 200 (201 → 400, `"threshold": 200`).
- Also exists: `/tasks/completed/by_due_date` (same `{"items": [...]}`).
  Not used.
- Limitation: a task completed more than 89 days ago while its issue is
  still open will be recreated. A task the user *deletes* (rather than
  completes) is also recreated on the next run: deleted tasks aren't
  listed anywhere.

### Persian / UTF-8

`"content": "تست فارسی"` round-trips unchanged through POST and GET. The
body is sent as a UTF-8 file (`--data-binary @file.json`). No escaping
is needed.

### Gotchas

- From the dev machine the connection was very flaky: repeated
  `curl: (28) Connection timed out` and
  `curl: (35) TLS connect error ... unexpected eof`, which then worked on
  retry. The client needs timeouts and retries.
- Retrying a POST: send the same `X-Request-Id` on every attempt. During
  the spike the failed attempts were connect timeouts, so they never
  reached the server and we got no duplicate. Nothing here proves the
  server dedupes on `X-Request-Id`, so the marker-based dedupe stays the
  real safeguard.

## GitHub Models — RETIRED, not usable

Endpoint tried: `POST https://models.github.ai/inference/chat/completions`,
model `openai/gpt-4.1-mini`.

**GitHub Models was fully retired on 2026-07-30.** The docs
(https://docs.github.com/en/rest/models/inference) say: "As of July 30,
2026, GitHub Models has been fully retired. The playground, model
catalog, inference API, and bring your own key (BYOK) are no longer
available to any customer."

What the host returns now, for every path, with or without auth:

```
HTTP/2 200
content-type: text/plain

OK
```

- Verified from the dev machine (direct and via VPN) and from a GitHub
  Actions runner using `GITHUB_TOKEN` with `permissions: models: read`
  (workflow `models-spike.yml`, run 36230108453). Same stub everywhere.
- The status is 200, so a client that only checks the status code would
  treat this as success. Any provider client must parse and validate the
  JSON body.
- Earlier TLS "unexpected eof" errors were a separate local-network
  problem. They didn't cause the stub.

## Other hosted providers — blocked

Gemini, Groq and NVIDIA are blocked for the owner's account/region
(reported by the owner, not re-tested in this spike). So no hosted LLM
API is used.

## Ollama (local, inside GitHub Actions) — CHOSEN

Workflow: `.github/workflows/models-spike.yml` (manual trigger), script:
`scripts/ollama_spike.py`.

- Install: `curl -fsSL https://ollama.com/install.sh | sh` (~100–140 s
  on the runner). The spike stops the installer's systemd service and
  runs `ollama serve` itself. Models are pulled fresh every run with no
  cache (see Gotchas).
- Endpoint: `POST http://localhost:11434/v1/chat/completions`
  (OpenAI-compatible). No auth, no rate limits.
- Request: `{"model", "temperature": 0, "messages": [system, user]}`.
  Response: OpenAI shape, text in `choices[0].message.content`.

### Results

Run 36257652692 (2026-09-26): `ubuntu-latest`, 4 vCPU, 15 GiB RAM, no
GPU, Ollama 0.34.4. Cold cache (miss), cache saved afterwards under
`ollama-Linux-qwen3.5-9b-gemma4-e4b-v1`.

| | `qwen3.5:9b` | `gemma4:e4b` |
|---|---|---|
| Params / quant | 9.7B, Q4_K_M | 8.0B raw (~4B effective), Q4_K_M |
| Pull (cold) | 27.9 s | 69.6 s |
| Inference (first call, incl. model load) | **203.6 s** | **23.6 s** |
| JSON parses, has `done`/`remaining` | yes | yes |
| Values are lists of strings | **yes** | **no**: each is one comma-joined string |
| Persian | correct, natural | correct, natural |

Other steps: Ollama install + start took 139 s. This first run still used
`actions/cache` (removed later, see Gotchas). Total pull + inference
was 325 s.

Raw outputs:

```
qwen3.5:9b
{"done":["افزودن صفحه ورود","رفع باگ کرش در صورت عدم وجود فایل تنظیمات","به‌روزرسانی مراحل نصب در مستندات README"],"remaining":["رفع مشکل عدم حفظ حالت تاریک","افزودن قابلیت خروجی CSV برای گزارش‌ها"]}

gemma4:e4b
{"done":"افزودن صفحه ورود، رفع مشکل کرش هنگام نبود فایل کانفیگ، به‌روزرسانی مراحل نصب در README","remaining":"تغییر حالت تاریک که ذخیره نمی‌شود، افزودن خروجی CSV برای گزارش‌ها"}
```

### Rerun: thinking off + schema-enforced (run 36258740621)

Native API instead of `/v1`:

```
POST http://localhost:11434/api/chat
{"model": ..., "stream": false, "think": false,
 "format": {"type": "object",
            "properties": {"done": {"type": "array", "items": {"type": "string"}},
                           "remaining": {"type": "array", "items": {"type": "string"}}},
            "required": ["done", "remaining"]},
 "options": {"temperature": 0}, "messages": [system, user]}
```

The response text is in `message.content`. Timing fields are in
nanoseconds: `load_duration`, `prompt_eval_count`, `eval_count`,
`eval_duration`. `message.thinking` was absent for both models.

| | `qwen3.5:9b` | `gemma4:e4b` |
|---|---|---|
| Pull (fresh, see cache note) | 23.7 s | 71.2 s |
| Inference, wall clock | **20.2 s** (was 203.6 s) | **24.2 s** (was 23.6 s) |
| of which model load | 5.4 s | 13.9 s |
| Tokens in / out, generation time | 111 / 57, 8.4 s | 107 / 73, 6.7 s |
| JSON parses | yes | yes |
| Schema ok (lists of non-empty strings) | **yes** | **yes** (was no) |

```
qwen3.5:9b
{"done":["صفحه ورود اضافه شد","رفع باگ هنگام عدم وجود فایل پیکربندی","به‌روزرسانی مراحل نصب در README"],"remaining":["پایداری حالت تاریک (Dark mode)","افزودن قابلیت خروجی CSV برای گزارش‌ها"]}

gemma4:e4b
{"done":["افزودن صفحه ورود","رفع مشکل کرش هنگام نبود فایل کانفیگ","به‌روزرسانی مراحل نصب در README"],"remaining":["تغییر حالت تاریک که ذخیره نمی‌شود (مشکل #12)","افزودن قابلیت خروجی CSV برای گزارش‌ها (مشکل #15)"]}
```

### Gotchas

- Both models have **thinking on by default**. It made Qwen 10× slower
  (204 s → 20 s with `"think": false`). Always send `"think": false`.
- Always send `format` with the JSON schema: without it Gemma returned
  strings instead of lists. `ai.py` should still validate the parsed
  object and fail loudly on a mismatch.
- **`actions/cache` on `~/.ollama` doesn't pay off at this size.** Both
  models together are 14.0 GB. The restore reported a cache hit, ran
  2 min 46 s, timed out at 88% (`Failed to restore: The operation
  cannot be completed in timeout`), and then the models were pulled
  fresh anyway in 24 s + 71 s. Pulling straight from the Ollama
  registry is faster. **Decision: no cache**, pull every run. The cache
  step has been removed from the workflow.
- Model defaults are `temperature 1` (Qwen also `presence_penalty 1.5`),
  so always set `temperature` explicitly.
- `gemma4:e4b` is 8B raw weights, not a true ~4B model. Both picks sit in
  the 8–10B range and fit comfortably in RAM (~14 GiB available).
- `actions/cache@v4` and `actions/checkout@v4` log a Node.js 20
  deprecation warning.
- Downloading Actions logs from the dev machine needs the VPN proxy:
  `results-receiver.actions.githubusercontent.com` and
  `*.blob.core.windows.net` are unreachable directly.
