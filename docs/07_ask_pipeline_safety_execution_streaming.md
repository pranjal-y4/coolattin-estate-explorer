# 07 — Ask Pipeline: Safety, Execution, LLM Cascade, Streaming, PDF, Feedback

**Scope.** The machinery shared by both Ask pipelines: the read-only SQL guard, the SQLite execution
wrapper and its three-tier repair ladder, the four-provider LLM cascade, the two layers of rate
limiting, the hand-written PDF 1.4 writer, the SSE wire protocol, and the
`ask_query_memory` / `ask_query_feedback` read/write paths.

**Companion docs.** `05_ask_pipeline_default.md`, `06_ask_pipeline_legacy_and_routing.md`,
`01_architecture_overview.md` §2.5 (flask-limiter setup), `02_database_schema.md` §§3.3–3.4
(memory/feedback DDL).

---

## 1. The read-only SQL guard

### 1.1 `_sanitize_and_validate_sql()` (ask_service.py:7476)

The entire function:

```python
def _sanitize_and_validate_sql(sql: str) -> str:
    if not sql:
        raise ValueError("Empty SQL.")
    cleaned = _normalise_schema_compat_sql(sql.strip().rstrip(";").strip())
    if ";" in cleaned:
        raise ValueError("Multiple statements not allowed.")
    if not re.match(r"^\s*(SELECT|WITH)\b", cleaned, flags=re.IGNORECASE):
        raise ValueError("Only SELECT/WITH allowed.")
    if FORBIDDEN_SQL.search(cleaned):
        raise ValueError("Unsafe SQL keyword blocked.")
    return cleaned
```

It is **regex + string checks, not a SQL parser**. Four gates, in order:

| # | Gate | Failure message |
|---|---|---|
| 1 | non-empty after coercion | `Empty SQL.` |
| 2 | no `;` remaining after stripping **one** trailing semicolon | `Multiple statements not allowed.` |
| 3 | starts with `SELECT` or `WITH` (case-insensitive, leading whitespace allowed) | `Only SELECT/WITH allowed.` |
| 4 | no forbidden keyword anywhere in the text | `Unsafe SQL keyword blocked.` |

The forbidden-keyword regex (line 134) — word-boundary anchored, case-insensitive:

```python
FORBIDDEN_SQL = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|DETACH|PRAGMA|REINDEX|VACUUM|TRUNCATE|REPLACE)\b",
    flags=re.IGNORECASE,
)
```

17 keywords. Note what this implies:

- `ATTACH` / `DETACH` are blocked, so a query cannot reach another database file.
- `PRAGMA` is blocked, so the model cannot toggle SQLite behaviour mid-query.
- `REPLACE` is blocked, which also blocks the *scalar function* `REPLACE(str, from, to)` — legitimate
  string manipulation will be rejected as unsafe. This is a deliberate over-block.
- Gate 2's ordering matters: only one trailing `;` is stripped, so `SELECT 1; SELECT 2;` becomes
  `SELECT 1; SELECT 2` and is caught by the `";" in cleaned` test.
- The check is textual, so `SELECT 'DROP'` and a column literally named `create_date` would both be
  rejected. There is no string-literal or identifier awareness.
- There is **no `LIMIT` enforcement** here. Row volume is capped downstream (§3.1).

**On violation**: `ValueError` is raised. Callers never let it escape to the user:

| Caller | Handling |
|---|---|
| `_llm_generate_validated_sql()` (6391) | catches, builds a repair prompt with the exact validation error, re-calls the LLM once; a second failure propagates |
| Stage 2 in both pipelines | catches, substitutes either `_fallback_sql()` or `_diagnostic_message_sql()`, sets `llm_meta.mode = "no_validated_sql"` |
| `_execute_with_recovery()` (7555) | catches (as a generic `Exception`) and enters the repair ladder |
| `record_query_feedback()` (2504) | does **not** catch — the `ValueError` propagates to `backend/routes/ask.py::ask_feedback`, which returns HTTP 400 with the message |

### 1.2 `_normalise_schema_compat_sql()` (7489) — the clearances column shim

```python
def _normalise_schema_compat_sql(sql: str) -> str:
    clearances_count_col = _clearances_count_column()
    if clearances_count_col and clearances_count_col != "eviction_count":
        sql = re.sub(r"\beviction_count\b", clearances_count_col, sql)
    return sql
```

`_clearances_count_column()` (7496) runs `PRAGMA table_info(clearances_record)` once per process and
caches the answer under `_schema_cache_lock`:

```python
if   "eviction_count" in names: column = "eviction_count"
elif "count"          in names: column = "count"
else:                           column = "eviction_count"      # optimistic default
```

**[DRIFT]** `CLAUDE.md`'s schema table and `_ANNOTATED_SCHEMA` both describe `clearances_record.count`,
while the entire `QUESTION_TEMPLATES` library, `_dynamic_fallback_sql()`, and this shim are written
around `eviction_count`. The shim exists precisely so both spellings work: templates and the LLM are
told the live name via `_live_sqlite_schema_prompt_block()` and `_build_sql_prompt`'s `{clear_col}`
interpolation, and any residual `eviction_count` literal is rewritten at validation time. Note the
rewrite is one-directional — a query written against `count` on a DB that uses `eviction_count` is
**not** repaired.

### 1.3 What the guard does *not* protect against

- **String interpolation into SQL.** The pipeline builds SQL by f-string in many places
  (`_same_parish_sql`, `_dynamic_fallback_sql`, the townland summary queries, template placeholder
  substitution). Mitigation is `_sql_escape()` (10055) — `value.replace("'", "''")` — plus the fact
  that every interpolated townland/surname is a value already resolved against the DB catalog, not
  raw user text. The three `_run_read_only_query` inputs that *are* user-influenced (LLM-generated
  SQL) go through the guard.
- **Resource exhaustion.** A valid `SELECT` with a cartesian join will run to completion; there is no
  statement timeout, no `sqlite3.set_progress_handler`, and no `PRAGMA query_only` (which the
  keyword blocklist would itself reject).
- **Reading tables outside the intended set.** Any table in `coolattin.db` is readable.

---

## 2. LLM-generated SQL validation and the repair ladder

Three independent repair layers exist. A single question can trigger up to **four** SQL-generating LLM
calls plus one runtime repair.

### Layer 1 — syntax/safety repair, inside `_llm_generate_validated_sql()` (6391)

```python
raw_sql, meta = _llm_generate(prompt, purpose=purpose, max_tokens=260, temperature=0.0)
try:
    return _sanitize_and_validate_sql(raw_sql), meta, "llm_sql"
except ValueError as exc:
    repair_prompt = _build_sql_repair_prompt(base_prompt=prompt, invalid_sql=raw_sql,
                                             validation_error=str(exc), dialect_label=dialect_label)
    repaired_sql, repaired_meta = _llm_generate(repair_prompt, purpose=f"{purpose}_repair", max_tokens=260, temperature=0.0)
    repaired_sql = _sanitize_and_validate_sql(repaired_sql)     # ← not caught; propagates
    return repaired_sql, repaired_meta, "llm_sql_repaired"
```

The repair prompt (`_build_sql_repair_prompt`, 6421) is the **full original prompt** plus:

```
The previous {dialect_label} SQL output was invalid.
VALIDATION ERROR: {validation_error}
PREVIOUS OUTPUT:
{invalid_sql or "<empty>"}

Return ONLY one corrected read-only SQL query. No markdown. No explanation.
SQL:
```

Note `max_tokens=260` for SQL generation — deliberately tight, since the output is a single query.
`temperature=0.0` throughout.

### Layer 2 — semantic repair, inside `_generate_sql()` (5040)

After Layer 1 succeeds, `_requires_verified_fallback(question, sql)` (7614) checks that the SQL
addresses the question's topic (full rule table in doc 05 §4.3). A mismatch triggers a second full
generation with `_build_sql_semantic_repair_prompt()` (5878):

```
{full original prompt}

The previous SQL passed syntax checks but did not satisfy the app's semantic rules for the question.
PREVIOUS SQL:
{invalid_sql or "<empty>"}

Return ONLY one corrected SQLite SELECT/WITH query that answers the question more faithfully.
SQL:
```

This nested call has its own Layer-1 repair, so `_generate_sql` alone can issue 4 LLM calls.

### Layer 3 — runtime repair, inside `_execute_with_recovery()` (7555)

See §3.2.

### Output cleanup — `_strip_sql_formatting()` (6815)

Applied by both the OpenRouter and Ollama transports before returning:

```python
out = re.sub(r"^```(?:sql)?\s*", "", out, flags=re.IGNORECASE)   # opening fence
out = re.sub(r"\s*```\s*$", "", out)                             # closing fence
out = re.sub(r"^SQL\s*[:\-]?\s*", "", out, flags=re.IGNORECASE)  # "SQL:" prefix
# then: drop everything from the first line matching ^(This |Note |The |Here |I |--\s+[A-Z])
```

The trailing-prose heuristic is aggressive — a legitimate SQL line beginning with `-- Comment` (a
`--` followed by a capital) will truncate the query. This is one reason the prompts insist "No
comments." Note that the Anthropic and Grok transports do **not** apply this cleanup; their raw text
goes straight into `_sanitize_and_validate_sql`, which will reject a fenced block at gate 3.

---

## 3. SQLite execution

### 3.1 `_run_read_only_query()` (7523)

```python
def _run_read_only_query(sql: str) -> tuple[list[str], list[dict]]:
    conn = get_db_conn()
    try:
        conn.create_function("distance_km", 4, _distance_km_sql)
        cur  = conn.execute(sql)
        cols = [d[0] for d in (cur.description or [])]
        rows = [dict(r) for r in cur.fetchall()]
        return cols, rows[:300]
    finally:
        conn.close()
```

Three things to note:

- **`distance_km` is a Python UDF registered per connection.** `_distance_km_sql()` (9631) is a
  haversine on radius 6371.0 km, returning `None` when any argument is null or non-numeric. Because
  registration is per-connection, `distance_km` is available *only* inside this helper — a query run
  through any other connection (e.g. the townland-summary block in doc 05 §6) would fail with
  "no such function".
- **`rows[:300]` is the only volume cap in the system.** `cur.fetchall()` materialises the *entire*
  result set first, then slices. A query returning 500,000 rows will consume that memory before the
  cap applies. Templates mitigate this with explicit `LIMIT 200`/`LIMIT 100` clauses, but LLM-generated
  SQL has no enforced limit.
- Rows are converted to plain `dict` (via `sqlite3.Row`, configured in `extensions.py`) so they are
  JSON-serialisable for SSE.

### 3.2 `_execute_with_recovery()` (7555) — what "recovery" means

```python
try:
    if _requires_verified_fallback(question, sql):
        raise ValueError("semantic_constraint_mismatch")     # synthetic failure
    cols, rows = _run_read_only_query(sql)
    return sql, cols, rows, None, None                       # ← happy path, meta is None
except Exception as exc:
    try:
        repaired_sql, repair_meta, repair_mode = _llm_generate_validated_sql(
            prompt=_build_sql_runtime_repair_prompt(question=…, townland_hint=…,
                                                    failing_sql=sql, execution_error=str(exc),
                                                    approved_examples=…),
            purpose="sqlite_sql_runtime_repair", dialect_label="SQLite")
        if _requires_verified_fallback(question, repaired_sql):
            raise ValueError("semantic_constraint_mismatch")
        cols, rows = _run_read_only_query(repaired_sql)
        return repaired_sql, cols, rows,
               f"SQL was repaired after execution issue ({type(exc).__name__}).",
               {**repair_meta, "mode": repair_mode}
    except Exception:
        # tier 3 …
```

**Return shape**: `(sql, columns, rows, query_warning, execution_meta)`. `execution_meta is None`
signals "ran as generated"; anything else tells the pipeline that recovery happened.

**Tier 1 — semantic pre-check.** Before executing anything, `_requires_verified_fallback` is
re-applied. This catches the case where SQL survived generation-time repair but is still off-topic
(e.g. it reached Stage 3 via the Stage-2 fallback path). The synthetic `ValueError` deliberately
routes it into the repair ladder rather than executing a wrong-but-valid query.

**Tier 2 — LLM runtime repair.** The prompt (`_build_sql_runtime_repair_prompt`, 7535) is the full
`_build_sql_prompt` output plus:

```
The previous SQL failed when executed against SQLite.
EXECUTION ERROR: {execution_error}
PREVIOUS SQL:
{failing_sql or "<empty>"}

Return ONLY one corrected SQLite SELECT/WITH query that avoids the error and still answers the question.
SQL:
```

The literal SQLite error text (`no such column: u.ship`, `misuse of aggregate`, …) is fed back to the
model — this is the mechanism `CLAUDE.md` means by "auto-repairs failed SQL via LLM". The repaired
query is re-checked semantically and re-executed. Note the repair prompt itself goes through Layer 1
validation, so this tier can cost two more LLM calls.

**Tier 3 — terminal fallback**, forked on `ASK_ALLOW_HEURISTIC_FALLBACK`:

| Flag | SQL executed | warning | `execution_meta.mode` |
|---|---|---|---|
| on | `_sanitize_and_validate_sql(_fallback_sql(question, townland_hint))` | `Emergency heuristic SQL used after execution failure (OperationalError).` | `fallback_rule` |
| off (default) | `_diagnostic_message_sql("I could not produce a validated SQL query…")` | `Returned safe guidance after SQL execution failure (OperationalError).` | `no_validated_sql` |

Both tier-3 branches still *execute* something, so `columns`/`rows` are always well-formed and the
rest of the pipeline (availability, chart, summary, PDF) needs no null-handling. The diagnostic query
returns exactly one row with one column named `message`, which `_is_message_only_result()` (8149)
detects — it treats column names `message`, `availability_message`, `diagnostic_message` as
message-only results and routes them into `availability.state = "no_data"` rather than displaying a
one-row table.

`ASK_ALLOW_HEURISTIC_FALLBACK` (line 62) is off unless set to one of `1/true/yes/on`. Its purpose is
academic integrity: with it off, the system says "I could not answer this" instead of silently
substituting a keyword-guessed query whose numbers would look authoritative.

### 3.3 The heuristic fallback builders

Only reachable when the flag is on. `_fallback_sql()` (6836) first routes to
`_dynamic_fallback_sql()` (6894) if the question contains any "advanced marker"
(` by `, `per `, `group`, `list`, `show`, `who`, `which`, `around`, `nearby`, `radius`, `within`,
`20km`, `20 km`, `between`, `trend`, `compare`, `breakdown`, `population`, `census`, `people`,
`person`, `names`, `evict`, `clearance`, `tenant`, `parish`, `barony`, `county`); otherwise it uses
one of five narrow static templates. `_dynamic_fallback_sql` re-runs `_analyse_question` and
constructs SQL from `primary_intent` × `output_mode` × `scope` × `group_by`, including radius CTEs
using `distance_km`. `_fallback_vrti_postgres_sql()` / `_dynamic_fallback_vrti_postgres_sql()` (7352)
are the PostgreSQL analogues, computing great-circle distance inline with
`6371.0 * ACOS(LEAST(1.0, GREATEST(-1.0, …)))` since there is no UDF on that side.

---

## 4. The multi-provider LLM cascade

### 4.1 Provider ordering — `_llm_provider_order()` (6507)

```python
provider = (ASK_LLM_PROVIDER or "auto").lower()
if provider in {"off", "none", "disabled"}:
    return []
order = []
if ANTHROPIC_API_KEY  and LLM_ALLOW_PAID: order.append("claude")
if GROK_API_KEY       and LLM_ALLOW_PAID: order.append("grok")
if OPENROUTER_API_KEY:                    order.append("openrouter")
order.append("ollama")                                   # always last, always present
if provider in {"claude","grok","openrouter","ollama"} and provider in order:
    order = [provider] + [p for p in order if p != provider]
return order
```

Key semantics, stated in the docstring: **`ASK_LLM_PROVIDER` controls which provider goes *first*, not
which is the only one tried.** Setting it to `grok` still leaves Claude/OpenRouter/Ollama as fallbacks.
An unrecognised value logs a warning and behaves as `auto`.

`ollama` is unconditionally appended — even with no Ollama daemon running — so the cascade always has
a terminal element that will raise a connection error rather than returning an empty list.

`LLM_ALLOW_PAID` (line 67, default true, disabled by `0/false/no/off`) removes both paid providers
from the order entirely — the cost kill-switch for unattended evaluation runs.

### 4.2 Env-var surface

| Variable | Default | Effect |
|---|---|---|
| `ASK_LLM_PROVIDER` | `auto` | first provider; `off`/`none`/`disabled` kills all LLM use |
| `ASK_SYNTHESIS_MODEL` | `claude` | primary for `_claude_synthesize_answer` only |
| `LLM_ALLOW_PAID` | `true` | gates Anthropic + Grok |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` | — / `claude-sonnet-4-6` | base URL and API version are **hardcoded** (`https://api.anthropic.com/v1`, `2023-06-01`) |
| `GROK_API_KEY` / `GROK_MODEL` / `GROK_BASE_URL` | — / `grok-3-mini` / `https://api.x.ai/v1` | |
| `OPENROUTER_API_KEY` / `OPENROUTER_MODEL` / `OPENROUTER_BASE_URL` | — / `openai/gpt-oss-20b:free` / `https://openrouter.ai/api/v1` | |
| `OPENROUTER_CONNECT_TIMEOUT` / `OPENROUTER_REQUEST_TIMEOUT` | `10` / `80` s | `requests` tuple timeout |
| `OPENROUTER_MAX_RETRIES` | `2` (min 1) | outer attempt loop |
| `OPENROUTER_FALLBACK_MODELS` | — | comma-separated, inserted after `OPENROUTER_MODEL` |
| `OPENROUTER_SITE_URL` / `OPENROUTER_APP_TITLE` | `http://127.0.0.1:5001` / `Coolattin Archive Ask` | `HTTP-Referer` / `X-Title` attribution headers |
| `OPENROUTER_STATUS_TIMEOUT` / `OPENROUTER_STATUS_CACHE_TTL` | `5` s / `60` s | `/llm-status` probe |
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL` | `http://127.0.0.1:11434` / `""` | empty model ⇒ auto-resolve |
| `OLLAMA_CONNECT_TIMEOUT` / `OLLAMA_REQUEST_TIMEOUT` | `8` / `180` s | |
| `OLLAMA_MAX_RETRIES` / `OLLAMA_MODEL_CACHE_TTL` / `OLLAMA_KEEP_ALIVE` | `2` / `120` s / `10m` | |
| `RATE_LIMIT_CLAUDE_RPM` / `_GROK_RPM` / `_OPENROUTER_RPM` | `20` / `10` / `30` | in-process sliding window |

**[DRIFT]** All of these are read directly from `os.environ` at module import in `ask_service.py`,
**not** from `config.py`. This contradicts `CLAUDE.md`'s "`config.py` is the single source of truth —
never hard-code paths or timeouts in service files." Only `ActiveConfig.EXPORTS_DIR`,
`ActiveConfig.STATIC_DATA_DIR`, `ActiveConfig.GRAPHDB_ENABLED`, and the `GRAPHRAG_*` settings come
from config.

### 4.3 The dispatcher — `_llm_generate()` (6438)

```python
last_exc = None
for provider in _llm_provider_order():
    if provider in skip_providers: continue
    try:
        if provider == "claude":
            if not _RATE_LIMIT_CLAUDE.is_allowed(): continue          # skip, don't fail
            text, meta = _llm_generate_claude(system_prompt="You are a careful data assistant …",
                                              user_content=prompt, max_tokens=…, temperature=…)
            if text: return text, meta
            continue                                                  # empty ⇒ next provider
        if provider == "grok":
            if not _RATE_LIMIT_GROK.is_allowed(): continue
            text, meta = _llm_generate_grok(prompt, …)
            if text: return text, meta
            continue
        if provider == "openrouter":
            if not _RATE_LIMIT_OPENROUTER.is_allowed(): continue
            return _openrouter_generate(prompt=prompt, purpose=purpose, …)   # raises on failure
        if provider == "ollama":
            text, model = _ollama_generate(prompt=prompt, purpose=purpose, …)
            return text, {"provider": "ollama", "model": model}
    except Exception as exc:
        last_exc = exc
        log.warning("ask_service.llm_provider_failed provider=%s purpose=%s error=%s", provider, purpose, exc)

if last_exc: raise RuntimeError(f"No LLM provider succeeded for {purpose}: {last_exc}")
raise RuntimeError("No LLM provider configured. Set ANTHROPIC_API_KEY, GROK_API_KEY, OPENROUTER_API_KEY, or ASK_LLM_PROVIDER=ollama.")
```

**How failure is detected and the next provider tried** — three distinct mechanisms:

| Mechanism | Providers | Behaviour |
|---|---|---|
| Rate-limit refusal | claude, grok, openrouter | `is_allowed()` false ⇒ `continue`; the provider is *skipped*, `last_exc` unchanged |
| Empty-string return | claude, grok | their own wrappers never raise; `if text:` false ⇒ `continue` |
| Raised exception | openrouter, ollama (and anything unexpected) | caught, recorded in `last_exc`, loop continues |

Note the asymmetry: the OpenRouter and Ollama branches use `return` rather than the `if text: return`
guard, because their helpers raise on empty/failed responses instead of returning `""`.

`skip_providers` (a `frozenset`) exists solely to break recursion: `_llm_generate_claude` and
`_llm_generate_grok` both fall back into `_llm_generate` on error, which would otherwise re-select the
same provider (since it is first in the order) and recurse infinitely.

`ollama` has **no** rate limiter — it is local.

### 4.4 Per-provider transports

#### Anthropic — `_llm_generate_claude()` (5926)

```python
if not ANTHROPIC_API_KEY or not LLM_ALLOW_PAID:
    combined = f"{system_prompt}\n\n{user_content}"
    text, meta = _llm_generate(combined, purpose="synthesis", …)
    return text, {**meta, "via": "fallback_openrouter_or_ollama"}

resp = requests.post(f"{ANTHROPIC_BASE_URL}/messages",
    headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": ANTHROPIC_API_VERSION,
             "content-type": "application/json"},
    json={"model": ANTHROPIC_MODEL, "max_tokens": …, "temperature": …,
          "system": system_prompt, "messages": [{"role": "user", "content": user_content}]},
    timeout=60)
resp.raise_for_status()
text = data["content"][0]["text"] if data.get("content") else ""
return text, {"provider": "anthropic", "model": ANTHROPIC_MODEL,
              "input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens")}
```

- Native Messages API with a real `system` field — the only provider that keeps the system/user split.
- Non-streaming; `timeout=60` is a **hardcoded scalar**, so it is a total timeout with no separate
  connect budget.
- **Never raises.** On HTTP error it retries via `_llm_generate(..., skip_providers={"claude"})` and
  tags the meta `via: "fallback_after_claude_error"`; if that also fails it returns
  `("", {"provider": "none", "error": …})`.
- Token usage is captured but only ever surfaced in `llm_rewrite` meta and the PDF.

#### xAI Grok — `_llm_generate_grok()` (5991)

```python
if not GROK_API_KEY or not LLM_ALLOW_PAID:
    return "", {"provider": "grok", "error": "GROK_API_KEY not configured or LLM_ALLOW_PAID=false"}
_grok_models = [GROK_MODEL, "grok-3-mini-fast", "grok-beta"]
for _gmodel in _grok_models:
    try:
        resp = requests.post(f"{GROK_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {GROK_API_KEY}", "Content-Type": "application/json"},
            json={"model": _gmodel, "messages": [{"role":"user","content":prompt}],
                  "max_tokens": …, "temperature": …}, timeout=60)
        …
    except Exception as exc:
        if "403" not in str(exc) and "401" not in str(exc):
            break     # non-auth errors won't be fixed by another model
```

- OpenAI-compatible endpoint; single user message (no system role).
- **Model-level fallback**: three models tried, but only when the error looks like a 401/403 — the
  reasoning being that auth/entitlement errors are model-specific while network or 5xx errors are not.
- On exhaustion, recurses into `_llm_generate(..., skip_providers={"grok"})`.

#### OpenRouter — `_openrouter_generate()` (6548)

```python
for attempt in range(1, OPENROUTER_MAX_RETRIES + 1):
    for model in _candidate_openrouter_models():
        resp = requests.post(f"{OPENROUTER_BASE_URL}/chat/completions", headers=…,
            json={"model": model, "messages": [{"role":"system","content":"You are a careful data assistant. …"},
                                               {"role":"user","content":prompt}],
                  "temperature": …, "max_tokens": …},
            timeout=(OPENROUTER_CONNECT_TIMEOUT, OPENROUTER_REQUEST_TIMEOUT))
        if resp.status_code == 429: raise RuntimeError("OpenRouter rate limit or free daily quota reached.")
        if resp.status_code in {401,403}: raise RuntimeError("OpenRouter API key was rejected.")
        if resp.status_code == 402: raise RuntimeError("OpenRouter account requires credits for this request.")
        resp.raise_for_status()
        text = _extract_chat_content(data)
        if not text.strip(): raise RuntimeError("Empty response from OpenRouter.")
        return _strip_sql_formatting(text), {"provider": "openrouter", "model": data.get("model") or model, "requested_model": model}
    time.sleep(min(0.35 * attempt, 1.0))
```

- **Nested retry**: 2 attempts × the full candidate model list. `_candidate_openrouter_models()` (6618)
  is `[OPENROUTER_MODEL] + OPENROUTER_FALLBACK_MODELS + _OPENROUTER_FREE_MODELS`, deduped — 21 free
  models by default (gpt-oss, Llama 3.2/3.3, Gemma 3 family, Qwen3, GLM-4.5-air, MiniMax, Nemotron,
  LFM 2.5, Hermes 3 405B, Trinity, Dolphin-Mistral). So a full sweep can be ~42 HTTP requests.
- **Shared-failure short-circuit**: rate-limit, auth, and credit errors `raise` out of both loops
  immediately, because retrying another model cannot fix an account-level problem. Only per-model
  errors burn iterations.
- Linear backoff between attempts, capped at 1 s.
- `_extract_chat_content()` (6635) handles both string content and the list-of-parts shape.
- Output passes through `_strip_sql_formatting()`.
- Attribution headers are set by `_openrouter_headers()` (6536): `HTTP-Referer`, `X-Title`, and a
  duplicate `X-OpenRouter-Title`.

#### Ollama — `_ollama_generate()` (6652)

```python
for attempt in range(1, OLLAMA_MAX_RETRIES + 1):
    for model in _candidate_ollama_models(force_refresh=(attempt > 1)):
        resp = requests.post(f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False,
                  "keep_alive": OLLAMA_KEEP_ALIVE,
                  "options": {"temperature": …, "num_predict": max_tokens}},
            timeout=(OLLAMA_CONNECT_TIMEOUT, OLLAMA_REQUEST_TIMEOUT))
        …
        _remember_ollama_model(model)
        return _strip_sql_formatting(sql_text), model
```

- Native `/api/generate`, `stream: False`, `keep_alive` to avoid model reload churn.
- Candidate resolution (`_candidate_ollama_models`, 6689) prefers, in order: `OLLAMA_MODEL`, models
  currently loaded (`GET /api/ps`), the cached resolved model, other cached models, then a fresh
  `GET /api/tags` sweep ordered by `_preferred_ollama_models()`
  (`llama3.2:latest`, `llama3.1:8b`, `llama3.1:latest`, `llama3:latest`, `qwen2.5:latest`,
  `mistral:latest`, `gemma2:latest`, then anything else).
- `_remember_ollama_model()` promotes a successful model to the front of the cache — successive calls
  in a session converge on one model.
- `force_refresh` on attempt 2 re-queries `/api/tags`, handling the case where the model list changed.

### 4.5 Rate limiting layer 1 — in-process, per provider

`_RateLimiter` (line 177) is a lock-guarded sliding window:

```python
def is_allowed(self) -> bool:
    now = time.time()
    with self._lock:
        self._calls = [t for t in self._calls if now - t < self._window]
        if len(self._calls) >= self._max: return False
        self._calls.append(now)
        return True
```

Instances (line 204): Claude `RATE_LIMIT_CLAUDE_RPM=20`/60 s, Grok `10`/60 s, OpenRouter `30`/60 s.

Critical property: **hitting the limit does not error, it skips.** A burst of questions with Claude
saturated silently degrades to Grok, then OpenRouter, then Ollama. The `wait_time()` method is
implemented but never called — nothing in the codebase waits for a window to clear.

The window is per-process. Under a multi-worker gunicorn deployment each worker keeps its own counter,
so the effective ceiling is `workers × rpm`.

### 4.6 Rate limiting layer 2 — flask-limiter, per IP

Configured in `create_app.py` and documented in `01_architecture_overview.md` §2.5. The Ask-specific
application is `_apply_ask_rate_limits()` (create_app.py:167), applied **after** blueprint registration
because it decorates resolved view functions:

```python
limiter.limit("30 per minute; 200 per hour")(app.view_functions["ask_api.ask_query"])
limiter.limit("60 per minute")(app.view_functions["ask_api.ask_feedback"])
```

`default_limits=[]`, so no other endpoint is limited. When `flask_limiter` is not installed,
`app.extensions["limiter"] = None` and `_apply_ask_rate_limits` returns immediately — rate limiting is
silently disabled.

### 4.7 How the two layers relate

They are **independent and non-communicating**:

| | Layer 2 (flask-limiter) | Layer 1 (`_RateLimiter`) |
|---|---|---|
| Keyed on | client IP | LLM provider |
| Scope | one HTTP request | one outbound LLM call |
| Storage | flask-limiter in-memory | module-level lists |
| Exceeded | HTTP 429 before the pipeline starts | provider skipped, cascade continues |
| Aware of the other | no | no |

Nothing in `ask_service.py` reads `app.extensions["limiter"]`, and flask-limiter has no idea that a
single `/api/ask/query` request can issue anywhere from **0** LLM calls (all four legacy fast lanes
short-circuit) to **~10** (SQL gen + syntax repair + semantic repair + its own syntax repair + runtime
repair ×2 + SPARQL gen + mismatch explanation + synthesis ×2 + provider-switch retries + cross-verify).
A single IP inside the 30/min budget can therefore drive several hundred provider calls per minute,
which is what layer 1 is there to bound.

### 4.8 `/api/ask/llm-status` — `check_llm_status()` (4399)

Reports the *first available* provider, priority Claude → Grok → OpenRouter → Ollama, honouring
`ASK_LLM_PROVIDER` when it names a specific one. Returns `{available, provider, configured_provider,
active_model, priority_order, hint}`; the route maps `available` to HTTP 200/503.

Claude and Grok are reported available purely on key presence — **no network probe**. OpenRouter is
probed live via `GET {base}/key` with results cached for `OPENROUTER_STATUS_CACHE_TTL` (60 s) and
distinct `connection_state` values: `connected`, `rejected` (401/403), `rate_limited` (429), `disabled`
(key exists but disabled), plus `dns_unreachable` / `timeout` / `unreachable` classified by
`_friendly_openrouter_connection_issue()` (4577) from the exception text. Ollama is probed via
`GET /api/tags`, distinguishing "running but no models installed" from "cannot connect".

`GET /api/ask/ollama-status` is a backward-compatible alias returning the same payload.

---

## 5. SSE streaming

### 5.1 Wire format — `_sse()` (2721)

```python
def _sse(type_: str, **kw: Any) -> str:
    return f"data: {json.dumps({'type': type_, **kw})}\n\n"
```

One `data:` line, one JSON object, terminated by a blank line. No `event:` field, no `id:`, no
`retry:` — the client dispatches on the JSON `type` key, not on SSE event names.

Three message types:

```jsonc
// progress
{"type":"progress","stage":"querying_database","status":"completed",
 "label":"Querying SQLite","detail":"12 rows returned · 41 ms","duration_ms":41}

// error (terminal)
{"type":"error","message":"Database not ready: no such table: townland"}

// result (terminal, large)
{"type":"result","question":"…","answer":"…","columns":[…],"rows":[…], …}
```

Field contract:

| Field | Presence | Notes |
|---|---|---|
| `type` | always | `progress` \| `result` \| `error` |
| `stage` | progress only | machine key |
| `status` | progress only | `started` \| `completed`; the client treats anything ≠ `started` as completed |
| `label` | progress only | human label, overrides the client's own table |
| `detail` | progress, usually | free text, also written to the status line |
| `duration_ms` | progress `completed`, usually | `int((time.perf_counter() - t0) * 1000)` |
| `message` | error only | |

`duration_ms` is omitted on the `resolving_identity`, `synthesising_answer`, and `done` completions in
the orchestrated pipeline — those stages emit `completed` without a timing.

### 5.2 Stage inventory

| `stage` | Default pipeline | Legacy pipeline |
|---|---|---|
| `resolving_identity` | ✓ | — |
| `contacting_llm` | ✓ (label `Building Query`) | ✓ (label `Contacting LLM`; can emit `started` twice — once at Stage 1 and again after routing) |
| `querying_graphrag` | ✓ (townland resolved + graph available) | — |
| `framing_query` | ✓ | ✓ |
| `querying_database` | ✓ | ✓ |
| `querying_vrti_graph` | ✓ | ✓ |
| `querying_subgraph` | — | ✓ (when forced or detected) |
| `querying_graphdb` | never (dead branch) | ✓ (when `GRAPHDB_ENABLED`) |
| `querying_fusion` | ✓ | ✓ |
| `synthesising_answer` | ✓ | — |
| `preparing_output` | ✓ | ✓ |
| `done` | ✓ | — |

### 5.3 Client consumption — `frontend/static/js/ask.js`

The client uses `fetch` + a manual `ReadableStream` reader (not `EventSource`, which is GET-only):

```js
const reader = res.body.getReader();
const decoder = new TextDecoder();
let buffer = "";
while (true) {
  const { value, done } = await reader.read();
  if (done) break;
  buffer += decoder.decode(value, { stream: true });
  const parts = buffer.split("\n\n");
  buffer = parts.pop() || "";                 // keep the incomplete tail
  for (const part of parts) {
    const lines = part.split("\n").map(l => l.trim()).filter(l => l.startsWith("data:"));
    const dataText = lines.map(l => l.slice(5).trim()).join("\n");
    onEvent(JSON.parse(dataText));
  }
}
```

Dispatch (line ~1098):

```js
if (evt.type === "progress") { setStage(evt); return; }
if (evt.type === "error")    throw new Error(evt.message || "Unknown stream error.");
if (evt.type === "result")   finalPayload = evt;
```

`setStage()` writes into a `Map` keyed by `evt.stage` and re-renders. Rendering iterates a **fixed
`progressOrder` array** (ask.js:55):

```js
classifying_intent → "Routing Question"      // never emitted by either pipeline
contacting_llm     → "Building SQL Query"
slot_filling       → "Slot Filling"          // never emitted
framing_query      → "Framing Query"
querying_database  → "Querying Database"
querying_subgraph  → "KG Townland Lookup"
querying_vrti_graph→ "VRTI Geographic Context"
querying_fusion    → "Synthesising Answer"
preparing_output   → "Preparing Output"
```

**[DRIFT]** This array has drifted from the backend in both directions. Two keys
(`classifying_intent`, `slot_filling`) are never emitted by any code path. Five keys the backend does
emit — `resolving_identity`, `querying_graphrag`, `querying_graphdb`, `synthesising_answer`, `done` —
are **absent**, so those stages never appear as progress cards. They are still stored in `progressMap`
and their `detail` still reaches the status line via `setStatus()`, so the user sees the text
transiently but no persistent chip. `CLAUDE.md`'s rule "Do not change the SSE streaming protocol in
`ask_service.py` without updating `frontend/static/js/ask.js` to match" describes a constraint that
has already been violated.

### 5.4 Why buffering must not happen

`backend/routes/ask.py::ask_query`:

```python
@stream_with_context
def generate():
    try:
        for event in answer_question_stream(question=…, townland_hint=…, include_sql=…, force_llm=…):
            yield event
    except Exception as exc:
        log.exception("ask_api.stream_failed")
        yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

return Response(generate(), content_type="text/event-stream",
                headers={"Cache-Control": "no-cache",
                         "X-Accel-Buffering": "no",     # disable nginx / gunicorn buffering
                         "Connection": "keep-alive"})
```

Passing a generator to `Response` makes Flask use chunked transfer encoding and write each yielded
string as it is produced. What breaks if that is defeated:

1. **The progress UI stops working.** A full Ask request commonly takes 15–60 s (SQL generation +
   VRTI SPARQL + synthesis, each a network round trip; a full OpenRouter model sweep alone can exceed
   80 s). Buffered, the user gets a blank screen for the whole duration and then everything at once.
   `duration_ms` per stage becomes retrospective trivia rather than live feedback.
2. **Proxy and browser timeouts fire.** With no bytes on the wire, an intermediary applying a 30 s
   idle timeout kills the connection. `X-Accel-Buffering: no` is specifically for nginx, which
   otherwise buffers proxied responses by default.
3. **Memory.** The `result` event embeds up to 300 rows plus `structured_output`, `supporting_context`
   (25 sample people, full census/clearances series), `kg_context`, and `graphrag_context` — hundreds
   of KB. Buffering the whole stream holds progress events alive alongside it for no benefit.
4. **Error semantics degrade.** The `except` inside `generate()` yields an `error` event *mid-stream*,
   after status 200 and several progress events have already been sent. Buffering would let a
   framework layer convert that into a 500 with an HTML error page, which the client's
   `JSON.parse` path cannot handle. As written the client throws a JS `Error` carrying the backend
   message and renders it in the UI.

`@stream_with_context` is required because the generator body touches `request`-scoped state
indirectly (config, logging context) and Flask tears the request context down as soon as the view
returns — which for a streamed response is *before* the generator has run. Without it the first
context access inside the generator raises "Working outside of request context".

### 5.5 Input sanitisation at the route

```python
_MAX_QUESTION_LEN       = 600
_MAX_TOWNLAND_HINT_LEN  = 120

def _sanitize_input(raw: str, max_len: int) -> str:
    cleaned = "".join(ch for ch in (raw or "")
                      if unicodedata.category(ch)[0] != "C" or ch in ("\n", "\t"))
    return cleaned.strip()[:max_len]
```

Strips all Unicode control-category characters except newline and tab, then truncates. Applied to
`question` and `townland_hint` only — never to system strings, per the docstring. The audit log records
IP and question **length**, not text:

```python
log.info("ask_api.query ip=%s q_len=%d townland_hint=%s", _ip, len(question), bool(townland_hint))
```

with `_ip` taken from the first `X-Forwarded-For` entry, falling back to `request.remote_addr`.

---

## 6. PDF export — hand-written PDF 1.4

No `reportlab`, no `fpdf`. `requirements.txt` has no PDF dependency.

### 6.1 Two-stage design

**Stage A — `_write_pdf_report()` (9194)** turns the answer into a list of
`(section_type, content)` tuples. Eight section types:

| Type | Content | Rendering |
|---|---|---|
| `header` | `{title, subtitle, date}` | full-bleed dark banner, page 1 only |
| `section` | heading string | tinted bar + left accent rule |
| `para` | text | wrapped body paragraph |
| `kv` | `(key, value)` | bold key, body value |
| `blank` | `None` | 6 pt spacer |
| `rule` | `None` | 0.5 pt hairline |
| `table` | `(columns, rows)` | header row + zebra rows, repeats header across page breaks |
| `code` | list of lines | dark block, Courier |

Document order: header → Research Question → Summary Answer (LLM synthesis, markdown-stripped) *or*
Data Answer (deterministic fallback) → Database Results table (capped at **80 rows × 8 columns**, with
a "N additional rows not shown" note) → Query Traceability (`provider / model / mode` for SQL and for
the summary) → Generated SQL (code block) → Knowledge Graph townland context → Synthesised Output +
Statistical Summary → footer rule + attribution line.

Output path: `ActiveConfig.EXPORTS_DIR / "ask" / f"ask_report_{YYYYmmdd_HHMMSS}.pdf"` (UTC). The
directory is created on demand. There is **no cleanup** — every question leaves a file.

**Stage B — `_build_professional_pdf()` (9398)** emits raw PDF bytes.

### 6.2 How the bytes are constructed

**Object graph.** A fixed prelude plus two objects per page:

```python
objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
objects[2] = f"<< /Type /Pages /Count {n} /Kids [{ids}] >>"      # written last
objects[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"        # F1
objects[4] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>"   # F2
objects[5] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>"          # F3
# then, per page: objects[pid] = Page dict, objects[cid] = content stream
```

Only the 14 standard Type 1 base fonts are used, so no font program has to be embedded — this is the
central trick that makes a library-free writer viable.

**Page geometry** (lines 9317–9323): US Letter `612 × 792` pt, 48 pt margins, content width 516 pt.
`(0,0)` is bottom-left, so the cursor `y` starts at `792 - 48 = 744` and decreases.

**Content-stream primitives** — each returns latin-1 bytes of PDF operators:

```python
_pdf_rect(x, y, w, h, fill)     → f"{r} {g} {b} rg\n{x} {y} {w} {h} re f"
_pdf_hline(x, y, w, color, lw)  → f"{lw} w {r} {g} {b} RG {x} {y} m {x+w} {y} l S"
_pdf_text(x, y, text, font, sz, color)
                                → f"BT /{font} {sz} Tf {r} {g} {b} rg {x} {y} Td ({escaped}) Tj ET"
```

`_pdf_text_block()` (9372) adds wrapping and leading via the text-object operators:

```
BT /F1 10.0 Tf  0.118 0.161 0.231 rg  48.00 700.00 Td  0 -13.00 TL
(first line) Tj
T* (second line) Tj
T* (third line) Tj
ET
```

Line breaking is width-estimated, not metric-accurate:

```python
char_w    = size * 0.52          # rough Helvetica advance
max_chars = max(1, int(_PDF_CW / char_w))
wrapped   = _wrap_line(text, width=max_chars)
```

`_wrap_line()` (9609) is a greedy word wrapper with no hyphenation; a word longer than the width is
emitted whole and overflows the margin.

**Text escaping** — `_escape_pdf_text()` (9604):

```python
raw = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
return raw.encode("latin-1", "replace").decode("latin-1")[:250]
```

Backslash and both parentheses are escaped because the literal-string syntax is `( … )`.
Non-latin-1 characters become `?` — Irish-language `name_gaelic` values with fadas render as `?`
unless the character happens to exist in latin-1. The 250-character truncation is a hard cap per
draw call, which is why `_pdf_text_block` wraps *before* escaping.

**Pagination.** `need_space(stream_ops, y, needed, page_num)` flushes the current page and opens a new
one when `y - needed < _PDF_MB + 20`. Tables have their own inner check that re-draws the header row
on the new page. `flush_page()` writes the page-number footer, wraps the operator list in a
`<< /Length N >> stream … endstream` object, and emits the Page dict with all three fonts in
`/Resources`.

**Assembly and xref.** Objects are concatenated in numeric order while recording byte offsets:

```python
chunks = [b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"]
for n in range(1, total + 1):
    obj_bytes = f"{n} 0 obj\n".encode("latin-1") + objects[n] + b"\nendobj\n"
    offsets.append(cursor); chunks.append(obj_bytes); cursor += len(obj_bytes)

xref_lines = [f"xref\n0 {total+1}\n".encode(), b"0000000000 65535 f \n"]
for off in offsets[1:]:
    xref_lines.append(f"{off:010d} 00000 n \n".encode("latin-1"))
trailer = f"trailer\n<< /Size {total+1} /Root 1 0 R >>\nstartxref\n{xref_start}\n%%EOF\n".encode()
return b"".join(chunks + xref_lines + [trailer])
```

The `%\xe2\xe3\xcf\xd3` second line is the conventional binary marker telling tools the file is not
plain text. Cross-reference entries are the mandated fixed 20-byte format (`%010d 00000 n ` plus a
trailing newline). No compression, no `/Filter`, no incremental update, no `/Info` dictionary.

**Markdown stripping** — `_strip_markdown_for_pdf()` (9178) removes ATX headings, bullet and ordered
list markers, `*`/`**`/`***` emphasis, and horizontal rules before text reaches the PDF. This is a
different function from `_strip_answer_formatting()` (9162), which *preserves* markdown for the HTML
frontend.

### 6.3 Download route

```python
@bp.get("/pdf/<path:filename>")
def ask_pdf_download(filename: str):
    safe_name = Path(filename).name                      # path-traversal defence
    if not safe_name.lower().endswith(".pdf"):
        abort(400, description="Only PDF files may be downloaded from this endpoint.")
    pdf_path = ActiveConfig.EXPORTS_DIR / "ask" / safe_name
    if not pdf_path.exists(): abort(404, description="Report not found.")
    return send_file(pdf_path, as_attachment=True, download_name=safe_name, mimetype="application/pdf")
```

Two guards: `Path(...).name` collapses any `../` segments to a bare basename, and the `.pdf` extension
check prevents the endpoint being used as a generic file reader for `exports/ask/`.

---

## 7. Query memory and feedback

Both tables are created lazily by `_ensure_query_memory_schema()` (2265) rather than by
`extensions.py::ensure_schema()`. Full DDL is in `02_database_schema.md` §§3.3–3.4. Four indexes are
created: signature and townland on `ask_query_memory`, signature and `created_at` on
`ask_query_feedback`.

### 7.1 Read path

`_load_approved_query_memory()` (2339):

```sql
SELECT * FROM ask_query_memory
WHERE approved_count > rejected_count
ORDER BY approved_count DESC, updated_at DESC, id DESC
LIMIT 250
```

A row that accumulates as many thumbs-down as thumbs-up drops out of the pool automatically.
Cached in `_QUERY_MEMORY_CACHE` for `_QUERY_MEMORY_CACHE_TTL = 60` s behind
`_query_memory_cache_lock`; `_clear_query_memory_cache()` invalidates it on every write.

Consumers: `_find_similar_approved_queries()` (scoring, doc 06 §3.4), `_phase4_retrieve()`
(embedding re-ranking), and `_approved_query_examples_block()` (2465), which renders the top **3** into
the SQL prompt as:

```
Approved user-validated SQL examples for similar questions:
- similarity_score: 96.5
  question: How many people emigrated from Coolboy?
  townland_scope: COOLBOY
  approvals: 3
  SQL:
    SELECT COUNT(DISTINCT record_id) …
Reuse the structure only if it truly matches the new question.
```

### 7.2 `question_signature` — definition

`_question_signature()` (2249):

```python
tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]{1,}", (question or "").lower())
stopwords = {the, a, an, of, for, to, in, on, at, by, from, this, that, these, those,
             is, are, was, were, be, do, does, did, what, which, who, how, many, much,
             there, any, all, with, and, or, than, then, into, about, around, within,
             across, over, under, show, list, tell, me, please, records, record}
cleaned = [t for t in tokens if t not in stopwords]
if not cleaned: cleaned = tokens[:8]
return " ".join(cleaned[:18]).strip()
```

Properties:

- Tokens must be ≥ 2 characters (the `{1,}` quantifier after the leading class), so single letters and
  most punctuation vanish.
- Apostrophes and hyphens are kept inside tokens (`o'brien`, `ballinacor-upper`).
- 42 stopwords removed — note `record`/`records` and the polite fillers `show`/`list`/`tell`/`me`/
  `please` are treated as noise.
- Capped at 18 tokens; degenerate all-stopword inputs fall back to the first 8 raw tokens.
- **Word order is preserved**, so the signature is not order-invariant — but it is compared with
  `fuzz.token_sort_ratio`, which is. The signature's job is noise removal; the ratio handles ordering.

It is stored on both tables and indexed, and is the equality key for the memory upsert (§7.4).

### 7.3 Write path — `POST /api/ask/feedback`

Route validation: `question` required (400 if blank), `feedback` must be exactly `up` or `down`
(400 otherwise). The route forwards 12 fields to `record_query_feedback()` and returns whatever it
produces, or 400 on `ValueError`.

`record_query_feedback()` (2504) preamble:

```python
if feedback_value not in {"up","down"}: raise ValueError("feedback must be 'up' or 'down'.")
_ensure_query_memory_schema()
analysis           = _analyse_question(question, townland_hint)
question_signature = _question_signature(question)
townland_norm      = _norm_townland(townland_hint)
now                = _utcnow_iso()                      # second-precision UTC ISO-8601
clean_sql          = _sanitize_and_validate_sql(sql_text) if sql_text else None
```

Note `clean_sql` re-validates client-supplied SQL. A tampered or unsafe `sql_text` raises `ValueError`
→ HTTP 400, so nothing unsafe can be persisted into memory via the feedback endpoint.

### 7.4 Insert vs. update semantics

**Always**: one `INSERT` into `ask_query_feedback` — every submission, up or down, is recorded with
`question_text`, `question_signature`, `townland_hint`, `townland_norm`, `sql_text` (validated),
`vrti_postgres_sql`, `feedback`, `note`, `result_row_count`, `availability_state`, the three
`llm_meta` fields (`provider`/`model`/`mode`), `reused_memory_id`, `created_at`. This table is
append-only; nothing ever updates or deletes from it.

**Then**, conditional on the feedback value:

| Case | Condition | Action on `ask_query_memory` |
|---|---|---|
| A | `up` + `clean_sql` + `reused_memory_id` given | `UPDATE` that id: `approved_count += 1`, refresh `sql_text`, `source_mode`, `llm_provider`, `llm_model`, `last_approved_at`, `updated_at`; `COALESCE` in `feedback_note`, `sample_answer`, `summary_json`, `vrti_postgres_sql` (existing values win) |
| B | `up` + `clean_sql`, no `reused_memory_id`, and a row exists with the same `(question_signature, COALESCE(townland_norm,''))` | `UPDATE` that row: `approved_count += 1` and overwrite `question_text`, `analysis_json`, `sql_text`, `vrti_postgres_sql`, `source_mode`, `llm_provider`, `llm_model`, `last_approved_at`, `updated_at`; `COALESCE` the three note/answer/summary fields |
| C | `up` + `clean_sql`, no match | `INSERT` a new row with `approved_count=1, rejected_count=0, reuse_count=0`, `analysis_json = json.dumps(analysis)`, and `created_at = updated_at = last_approved_at = now` |
| D | `down` + `reused_memory_id` | `UPDATE` that id: `rejected_count += 1`, `updated_at = now`. **No** new memory row is created. |
| E | `down` without `reused_memory_id` | nothing beyond the feedback row |
| F | `up` with no `sql_text` | nothing beyond the feedback row (`stored_in_memory` false) |

The dedupe key in case B is `question_signature` **plus** normalised townland — so the same question
asked with two different townland hints produces two memory rows, which is what makes the ±10/−16
townland term in `_memory_similarity_score` meaningful.

`analysis_json` is written on insert (case C) and on signature-match update (case B), but **not** on
case A. A memory row reused across many questions therefore keeps the analysis of whichever question
last matched by signature — and `_can_reuse_memory_directly` compares against that stored analysis.

Two cache invalidations fire after commit:

```python
_clear_query_memory_cache()
from backend.services.embedding_index import get_index as _get_embed_index
_get_embed_index().invalidate_memory()          # best-effort, try/except
```

Return payload: `{"ok": True, "feedback": "up"|"down", "stored_in_memory": bool, "memory_id": int|None}`.

### 7.5 Effect on later answers

**[DRIFT worth flagging.]** `CLAUDE.md` describes `ask_query_memory` as *"Approved question→SQL pairs
(thumbs-up feedback; reused by retrieval)"*, which is accurate for the legacy pipeline. In the
**default** pipeline — the one that ships — memory has no effect at all:

- `_find_similar_approved_queries` / `_can_reuse_memory_directly` are never called.
- `_generate_sql` receives `approved_examples=None`.
- `_execute_with_recovery` receives `approved_examples=[]`.
- `_phase4_retrieve` is never called.

Feedback is still recorded (both tables are written correctly), so the data collects for evaluation
and for any future re-enabling of the legacy path — but a thumbs-up cannot change a subsequent answer
while `ASK_USE_NEW_PIPELINE=true`.

---

## 8. Concurrency and caching inventory

Every module-level mutable cache in `ask_service.py`, its lock, and its TTL:

| Cache | Lock | TTL | Invalidation |
|---|---|---|---|
| `_VRTI_PARISH_CACHE` | `_vrti_cache_lock` | 3600 s | time only |
| `_VRTI_STATUS_CACHE` (`down_until`) | `_vrti_cache_lock` | 300 s cooldown | time only |
| `_OPENROUTER_STATUS_CACHE` | `_openrouter_status_cache_lock` | 60 s (env) | time only |
| `_OLLAMA_MODEL_CACHE` | `_ollama_cache_lock` | 120 s (env) | `force_refresh`, `_remember_ollama_model` |
| `_SCHEMA_COMPAT_CACHE` | `_schema_cache_lock` | **none** | process lifetime |
| `_PROMPT_SCHEMA_CACHE` | `_prompt_schema_cache_lock` | 300 s | time only |
| `_QUERY_MEMORY_CACHE` | `_query_memory_cache_lock` | 60 s | `_clear_query_memory_cache()` on every feedback write |
| `_TOWNLAND_CATALOG_CACHE` | `_townland_catalog_lock` | **none** — `loaded_at` written, never read | process lifetime |
| `_FORENAME_CACHE` | `_FORENAME_CACHE_LOCK` | 300 s | time only |
| `_load_kg_context._cache` | **none** | **none** | function-attribute memo, process lifetime |
| `_RateLimiter._calls` ×3 | per-instance `threading.Lock` | 60 s sliding | rolling |

Consequences worth knowing:

- Re-seeding `townland` at runtime does **not** refresh the townland catalog or the clearances column
  name — both survive until restart.
- `_load_kg_context` memoises via a function attribute with no lock; a race during first load can
  duplicate the parse but not corrupt the result (last writer wins on an immutable string).
- Every one of these caches is per-process, so a multi-worker deployment holds N independent copies.

DB connections are opened and closed per operation via `get_db_conn()` from `extensions.py`. There is
no pool. The heaviest single consumer is `_live_sqlite_schema_prompt_block()`, which issues ~60
introspection queries on one connection — hence its 300-second cache.
