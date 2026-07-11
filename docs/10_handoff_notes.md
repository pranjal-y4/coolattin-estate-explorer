# Handoff Notes — June 2026 Development Sprint

**Project:** Coolattin Estate Records Explorer  
**Candidate:** Pranjal Yadav  
**Sprint period:** Late May – 9 June 2026  
**Commits covered:** `661fcdf`, `3c3174d`, `4d18308` (plus unstaged new files)

This document records what was built, what changed, where the code lives, and what remains outstanding. It is the primary handoff reference for anyone picking up this codebase after this sprint.

---

## 1. What Was Built

### 1.1 Orchestrated Ask Pipeline (7 phases, enabled by default)

The Ask page pipeline was rewritten from a flat 7-step sequence into a fully orchestrated, routed multi-phase architecture. **`ASK_USE_NEW_PIPELINE=true` is the default as of commit `4d18308`.**

| Phase | Module | What it does |
|---|---|---|
| Pre-flight | `ask_service.py` | 4 fast lanes checked in order: rule-fill (conf ≥ 0.80) → verified template → memory reuse (cosine ≥ 0.55) → embedding retrieval (cosine ≥ 0.68) |
| 1 | `identity_resolver.py` | Resolve townland + person identity once; `sql_id` + `kg_uri` shared downstream |
| 2 | `semantic_layer.py` | Slot-fill compiler → deterministic SQL + SPARQL; 14-metric registry; 0 LLM for rule-fill path |
| 3 | `subgraph_engine.py` | KG traversal — VRTI multi-hop SPARQL + GraphDB k=2 neighbourhood expansion |
| 4 | `embedding_index.py` | Hybrid TF-IDF + RRF; fast-lane threshold 0.68 for template short-circuit |
| 5 | `intent_router.py` | Classifies question: ANALYTICAL / RELATIONAL / COMPARATIVE / FALLBACK (priority order) |
| 6 | `ask_service.py` | Fusion + discrepancy detection across SQL + VRTI + GraphDB results |
| 7 | `ask_service.py` | LLM synthesis → provenance-annotated answer; FALLBACK lane uses LLM SQL generation only |

**Key SSE event stages:** `resolving_identity`, `slot_filling`, `schema_sql`, `classifying_intent`, `querying_subgraph`, `embedding_retrieval`, `contacting_llm`, `framing_query`, `querying_database`, `querying_vrti_graph`, `querying_graphdb`, `querying_fusion`, `synthesizing_answer`, `preparing_output`

**Files changed:** `backend/services/ask_service.py` (major), `frontend/static/js/ask.js`, `frontend/templates/ask.html`

### 1.2 Identity Resolution (`identity_resolver.py`)

Three-layer model for repeated-name disambiguation:
- **Mention** — immutable; one per name occurrence in a source record
- **Person** — inferred individual; linked to one or more Mentions via SAME_AS (confidence ≥ 0.75 = confirmed; 0.50–0.74 = candidate)
- **Factoid** — reified claim; contradictory records survive without hard-merging

Algorithm: `jellyfish.metaphone` phonetic blocking → within block: Jaro-Winkler name similarity + geographic proximity (+0.20 same townland, +0.10 same parish) + temporal plausibility (±10 yr: +0.10; >30 yr: −0.10) + family co-occurrence (+0.15).

Cache: module-level, 10-minute TTL per name key.

**File:** `backend/services/identity_resolver.py` (394 lines)

### 1.3 Workhouse Entity Resolution (separate from Ask pipeline)

A purpose-built entity-resolution subsystem for linking workhouse admission records to unified estate records. Uses deterministic normalisation and fuzzy scoring — no embeddings or LLM.

**Modules:**
- `backend/services/workhouse_entity_resolution.py` — pipeline orchestrator
- `backend/services/entity_resolution/__init__.py` — public API
- `backend/services/entity_resolution/normalise.py` — name normalisation, initials expansion, phonetic coding
- `backend/services/entity_resolution/candidates.py` — blocking + candidate generation (up to 25 per mention)
- `backend/services/entity_resolution/scoring.py` — multi-signal scoring → CONFIRMED/POSSIBLE/WEAK/NO_MATCH

**New SQLite tables (added to `extensions.py`):**

| Table | Purpose |
|---|---|
| `source_mentions` | One row per name occurrence in a source record |
| `entity_resolution_candidates` | Scored candidate links: mention → unified_record |
| `workhouse_unified_links` | Final accepted links |
| `entity_resolution_decisions` | Human review decisions |
| `match_review` | Human-review queue for borderline candidates |

**Related:** `backend/repositories/match_review_repository.py` provides CRUD for the review queue.

### 1.4 Hybrid Embedding Retrieval (`embedding_index.py`)

Phase 4 of the new Ask pipeline. Hybrid search over templates, approved memory, and corpus chunks:

1. TF-IDF unigram+bigram vectors; cosine similarity top-50
2. Required-keyword hard pre-filter for template/metric hits
3. RRF (Reciprocal Rank Fusion) combines dense + sparse ranked lists
4. Fast-lane: template or memory hit above threshold (0.68) short-circuits routing
5. `retrieve_chunks_with_meta()` — returns `(chunks, meta)` where `meta` carries `dense_backend`, `dense_status`, `dense_count`, `sparse_count`, `fused_count` for SSE provenance display

No external dependencies beyond the Python standard library (TF-IDF is hand-rolled).

**File:** `backend/services/embedding_index.py` (558 lines total after both commits)

### 1.5 Dense Embedding Providers

Three interchangeable providers, selected by `EMBEDDING_PROVIDER` env var:

| Provider | Module | Notes |
|---|---|---|
| `local` (default) | `local_embeddings.py` | BAAI/bge-large-en-v1.5 via SentenceTransformers; 1024-dim; CPU; no API key |
| `cohere` | `voyage_embeddings.py` | Cohere Embed v3 (`embed-english-v3.0`, 1024-dim); asymmetric input_type; 5 calls/min rate limit; 12s inter-call sleep |
| `voyage` | `voyage_embeddings.py` | Legacy; same interface |

**Critical:** queries and documents use different `input_type` values for Cohere/Voyage. Passing the same type for both silently degrades retrieval quality.

### 1.6 pgvector Retrieval Backend (`ask_pgvector.py`)

Optional persistent vector store for Ask-page retrieval. Activated when `DATABASE_URL` points to a PostgreSQL instance with the `pgvector` extension.

- Syncs rich chunks from SQLite to pgvector on first use (TTL 1 hour)
- Falls back to in-process TF-IDF if psycopg is not installed or `DATABASE_URL` is absent
- Chunk types: person passport, place passport, workhouse record, estate survey, census, emigration, eviction, community summary, source/table summary

**File:** `backend/services/ask_pgvector.py`  
**Chunk builder:** `backend/services/retrieval_chunks.py`

### 1.7 Semantic Layer (`semantic_layer.py`)

Deterministic SQL + SPARQL compiler for ANALYTICAL questions. No LLM needed for the fast path.

Architecture:
1. `try_rule_based_fill()` — keyword + entity pattern matching → `SlotFill | None`
2. `build_slot_fill_prompt()` — tight JSON-only prompt for LLM slot filling
3. `parse_slot_fill()` — validates LLM JSON response into typed `SlotFill`
4. `compile_sql()` — assembles guaranteed-valid SQLite from `SlotFill`; never raises
5. `compile_sparql()` — equivalent SPARQL for `co:` ontology (local GraphDB); returns `None` when no KG equivalent exists

Adding a new metric = one entry in `METRIC_REGISTRY` + optional keyword in `_METRIC_KEYWORDS`. No other changes needed.

**File:** `backend/services/semantic_layer.py` (1112 lines)

### 1.8 Subgraph Engine (`subgraph_engine.py`)

KG traversal for relational/hierarchy/heritage questions. Five-step pipeline:
1. Entity linking — resolves mentions to KG node URIs (VRTI + GraphDB)
2. k-hop neighbourhood expansion via SPARQL (place hierarchy as single crm:P89_falls_within traversal)
3. Subgraph pruning — relevance prune + size cap
4. Linearisation — compact triple table or prose block for LLM context
5. Community summaries — precomputed blurbs from `data/seed/community_summaries.json`

Core rule: the linearised subgraph is for *reading* qualitative context only. Counts/aggregates always come from the SQL path.

**File:** `backend/services/subgraph_engine.py` (518 lines)

### 1.9 GraphDB Integration (`graphdb_sparql.py`)

SPARQL client for the local Coolattin RDF repository at `http://localhost:7200/repositories/coolattin` (also deployed at `http://51.120.71.162:7200/repositories/coolattin`). Uses the `co:` ontology (`https://coolattin.ie/ontology#`).

The Ask pipeline queries GraphDB in parallel with SQLite when `GRAPHDB_ENABLED=true`. Results are merged and discrepancies surfaced in the SSE payload under `fusion` and `discrepancies` keys.

**Env vars:** `GRAPHDB_ENABLED` (default `true`), `GRAPHDB_SPARQL_ENDPOINT`, `GRAPHDB_REQUEST_TIMEOUT` (default 15s)

**File:** `backend/integrations/graphdb_sparql.py` (155 lines)

### 1.10 Evaluation Harness (`ask_eval.py` + `eval_results/`)

A 2125-line evaluation framework for the Ask pipeline. Captures question-level metrics: template ID hit vs LLM fallback, SQL generated, result rows, latency, correctness classification.

Baselines captured (stored as JSON in `backend/services/eval_results/`):
- `eval_baseline.json` / `eval_phase0_baseline.json`
- `eval_integration_baseline.json`
- `eval_new_pipeline.json`
- `eval_phase1.json` through `eval_phase5_expanded.json`
- `eval_pre_fix_baseline.json` / `eval_post_fix.json`

**File:** `backend/services/ask_eval.py`

---

## 2. What Changed in Existing Files

| File | What changed |
|---|---|
| `backend/services/ask_service.py` | Grew from ~400 to ~2800 lines. Added orchestrated pipeline dispatcher (`_orchestrated_pipeline_stream`), multi-model synthesis (Part F), identity retrieval integration, vector retrieval meta reporting, new SSE stages |
| `backend/services/embedding_index.py` | Added `retrieve_chunks_with_meta()` returning `(chunks, meta_dict)` for SSE provenance; expanded from 347 to 558 lines |
| `backend/services/entity_resolver.py` | Extended with additional resolution utilities |
| `backend/services/townland_service.py` | Grew from ~300 to ~900 lines with alias expansion, reconciliation gap tracking, and KG enrichment improvements |
| `backend/integrations/vrti_sparql.py` | Expanded with additional SPARQL patterns (+184 lines) |
| `backend/repositories/townland_repository.py` | New queries for KG URI lookup and enrichment (+126 lines) |
| `extensions.py` | Added 5 new tables + indexes; grew from ~180 to ~477 lines |
| `config.py` | Added `DATABASE_URL`, `EMBEDDING_PROVIDER`, `GRAPHDB_*` config keys |
| `.env.example` | Added `ASK_USE_NEW_PIPELINE`, `EMBEDDING_PROVIDER`, `COHERE_API_KEY`, `GRAPHDB_*` vars |
| `requirements.txt` | Added `cohere`, `sentence-transformers`, `jellyfish` |
| `frontend/static/js/ask.js` | Updated SSE event handling for new stages (+70 lines) |
| `frontend/templates/ask.html` | Minor UI tweaks for new pipeline stages |
| `data/seed/townland_aliases.json` | Added 36 new alias entries |
| `data/seed/community_summaries.json` | New file: 9 precomputed community/place summary blurbs |

---

## 3. New Tests

| File | What it tests |
|---|---|
| `tests/test_townland_resolution.py` | 342-line test suite for townland normalisation, alias resolution, and fuzzy matching |
| `tests/test_ask_pgvector.py` | pgvector backend: sync, retrieval, fallback behaviour |
| `tests/test_ask_pipeline_flags.py` | Pipeline flag combinations (new vs legacy, EMBEDDING_PROVIDER values) |
| `tests/test_config_env_loading.py` | Config loading from env vars |
| `tests/test_workhouse_entity_resolution.py` | Workhouse ER pipeline: normalise, candidates, scoring, persist |

---

## 4. New Scripts

| File | Purpose |
|---|---|
| `scripts/bulk_ingest_local.py` | Bulk-ingest corpus chunks into the local embedding index |
| `scripts/cohere_sample_validate.py` | Validate Cohere embed output dimensions and asymmetric encoding |
| `scripts/link_workhouse_records.py` | Run the workhouse entity resolution pipeline and persist results |
| `scripts/validate_ann_scale.py` | Validate ANN retrieval at scale |
| `scripts/validate_workhouse_er.py` | End-to-end smoke test for the workhouse ER pipeline |

---

## 5. New Docs

| File | Purpose |
|---|---|
| `docs/ask_page_pgvector.md` | Why pgvector is limited to Ask page; runtime path; chunk types |
| `docs/workhouse_entity_resolution.md` | Why separate from pgvector; modules; tables; normalisation; candidate generation |
| `docs/01_Map_Entity_Resolution.md` | Map entity resolution pipeline (townland normalisation, alias resolution, VRTI enrichment) |
| `docs/02_GraphRAG_and_RAG_System.md` | Full GraphRAG/RAG architecture including all 5 paths and SSE protocol |
| `docs/03_Graph_Data_Uplift.md` | RDF/KG uplift strategy and co: ontology design |
| `docs/Project_Walkthrough.md` | Plain-language walkthrough of the entire system (now updated for new pipeline) |

---

## 6. Environment Variables Reference (new in this sprint)

| Variable | Default | Description |
|---|---|---|
| `ASK_USE_NEW_PIPELINE` | `true` | Enable orchestrated 7-phase pipeline (vs legacy flat path) |
| `EMBEDDING_PROVIDER` | `local` | `local` / `cohere` / `voyage` |
| `COHERE_API_KEY` | — | Required for `EMBEDDING_PROVIDER=cohere` |
| `DATABASE_URL` | — | PostgreSQL URL; activates pgvector backend if set |
| `GRAPHDB_ENABLED` | `true` | Query local GraphDB alongside SQLite |
| `GRAPHDB_SPARQL_ENDPOINT` | `http://localhost:7200/...` | GraphDB SPARQL endpoint |
| `GRAPHDB_REQUEST_TIMEOUT` | `15` | GraphDB timeout in seconds |

---

## 7. Outstanding Items

| Item | Priority | Notes |
|---|---|---|
| Turtle uplift script (`scripts/rdf_uplift.py`) | High | Needed for D8 dissertation evidence |
| SQL vs SPARQL formal comparison table | High | Run 5 competency questions; record in dissertation |
| Workhouse review UI | Low | Tables exist; no web page yet |
| D3 dataset audit | Medium | SQL queries to run, numbers to record |
| D4 geospatial alignment audit | Medium | 4 SQL queries against `townland` table |
| Technical evaluation (D9) | High | Use `ask_eval.py` harness; record results |
| LLM evaluation (D10) | Medium | Run 10+ free-form questions; record |
| User evaluation (D11) | Medium | 4–6 participants; task-based session |
| Demo freeze git tag | Required | `git tag v1.0-demo-freeze` before submission |

---

## 8. Architecture Decision Log (this sprint)

**Why a separate workhouse ER subsystem?** Semantic retrieval (Ask pipeline) and record linkage (workhouse matching) are different problems. Ask needs to find contextually relevant chunks fast. Workhouse ER needs to produce auditable, reviewable candidate links with explicit evidence trails. Mixing them would obscure both purposes. The pgvector backend is for the Ask page only.

**Why Cohere Embed instead of Voyage?** Voyage AI changed pricing/API terms. Cohere's `embed-english-v3.0` has the same 1024-dim output as the previous Voyage model — no schema migration needed. The `voyage_embeddings.py` module handles both providers via a runtime flag.

**Why BAAI/bge-large-en-v1.5 as local fallback?** MIT-licensed, CPU-friendly, 1024-dim (matching Cohere schema), no API key. Designed for asymmetric retrieval: queries get a "Represent this sentence for searching relevant passages:" prefix; documents are encoded raw.

**Why keep TF-IDF alongside dense embeddings?** Dense retrieval struggles on exact entity names and rare historical terms. TF-IDF keyword overlap catches what dense misses. RRF fusion reliably outperforms either alone on the template/memory matching task.

**Why the semantic layer generates both SQL and SPARQL?** The dissertation research question (RQ6) asks whether SPARQL over a purpose-built KG gives different results from SQL over a relational schema. The semantic layer is the mechanism that answers this: the same `SlotFill` struct is compiled to both, so results can be directly compared without LLM variance.

---

# Handoff Notes — June 21 Azure Deployment & Security Sprint

**Sprint period:** 21 June 2026  
**Commits covered:** `aefd1c1`, `4cd49f1`, `4bdbeea`, `d2c93b6`, `594a4ee`, `f4a1ba0`, `3e7ebce`, `1131be0`, `0e4096b`, `1e6f2ac`, `e2189b3`, `755f6ad`

---

## 1. What Was Fixed / Built

### 1.1 Analytics Page 500 + KG Explore Route (`aefd1c1`)

The `/analytics` page was returning 500 because the route handler was passing undefined Jinja2 variables to the template. The template and JS were stubs that predated the analytics registry pattern.

**Fix:**
- `backend/routes/main.py`: call `discover_modules()` and `compute()` from the analytics registry; pass `datasets`, `result`, and `error` to the template
- `frontend/templates/analytics.html`: rewritten as a proper dataset picker + KPI cards + Chart.js canvases template
- `frontend/static/js/analytics.js`: replaced misplaced Jinja2 HTML block with Chart.js initialisation script that reads inline JSON config blocks
- `backend/routes/main.py`: added `/kg-explore` as an alias for `/explore-knowledge` so both paths serve the KG explore page

**Files changed:** `backend/routes/main.py`, `frontend/templates/analytics.html`, `frontend/static/js/analytics.js`

### 1.2 Voyage AI SDK Support (`4cd49f1`)

The `EMBEDDING_PROVIDER=voyage` path was calling Cohere's SDK internally rather than the official Voyage AI SDK.

**Fix:**
- `backend/services/voyage_embeddings.py`: added `voyageai.Client` initialisation from `VOYAGE_API_KEY`; `embed_texts()` now routes to `_embed_voyage()` when `provider=voyage` and `_embed_cohere()` when `provider=cohere`
- `requirements.txt`: added `voyageai>=0.2`
- Azure App Service settings updated with: `ANTHROPIC_API_KEY`, `GROK_API_KEY`, `ASK_USE_NEW_PIPELINE=true`, `ASK_SYNTHESIS_MODEL=claude`, `ASK_LLM_PROVIDER=auto`, `EMBEDDING_PROVIDER=voyage`, `VOYAGE_API_KEY`, `DATABASE_PATH` (fixed to `/home/site/wwwroot/coolattin.db`)

**Files changed:** `backend/services/voyage_embeddings.py`, `requirements.txt`

### 1.3 Azure SSE Fix — gthread Workers (`4bdbeea`)

Two Azure-specific failures:
1. `No final result received` on the Ask page — backend error events were silently swallowed by the `catch` block in `ask.js`
2. SSE connections were deadlocking: a single gunicorn sync worker can serve only one long-lived SSE stream at a time, blocking all other requests

**Fix:**
- `frontend/static/js/ask.js`: separated `JSON.parse` from `onEvent` dispatch so backend error events surface to the user rather than being caught silently
- `startup.txt`: switched to `--worker-class gthread --threads 4` (2 workers × 4 threads), allowing concurrent SSE streams without deadlock; uses `${PORT:-8000}` so Azure's injected `$PORT` is respected
- `backend/services/ask_service.py`: wrapped both `_write_pdf_report` call-sites in `try/except` so PDF write failures do not crash the SSE generator; guarded `pdf_url` against `None` pdf_path

**Files changed:** `backend/services/ask_service.py`, `frontend/static/js/ask.js`, `startup.txt`

### 1.4 Procfile + Torch Removal (`d2c93b6`)

Azure pip install was timing out or OOM-ing on `torch` + `sentence-transformers` (~2 GB).

**Fix:**
- `requirements.txt`: commented out `torch`, `sentence-transformers`, and `psycopg`; imports in `local_embeddings.py` are already lazy so no startup error results
- `Procfile` added so Azure Oryx auto-detects the gunicorn startup command without requiring portal configuration

**Files changed:** `Procfile`, `requirements.txt`

### 1.5 Azure CI/CD Pipeline Stabilisation (`594a4ee`, `f4a1ba0`, `3e7ebce`, `1131be0`, `0e4096b`, `1e6f2ac`)

Six successive commits stabilised the GitHub Actions → Azure deployment pipeline. Root cause sequence:

| Commit | Problem | Fix |
|---|---|---|
| `3e7ebce` | No CI pipeline for new `coolattin-rg2` app | New `azure-deploy.yml` workflow; `requirements-azure.txt` (strips psycopg) |
| `1131be0` | `webapps-deploy@v3` requires AAD service principal (unavailable on student account) | Switch to Kudu curl deploy with publish-profile credentials |
| `0e4096b` | Publish-profile auth rejected; needed OIDC managed identity | Created `coolattin-gh-identity` in `coolattin-rg2`; OIDC federated credential for `pranjal-y4/coolattin-estate-explorer main`; `startup.sh` lazy pip install + `antenv/bin/python3 -m gunicorn`; `.webappignore` added |
| `1e6f2ac` | Startup command reset by Oryx after each deploy | Add explicit `az webapp config set` step after each deploy to enforce the gunicorn command |
| `f4a1ba0` | CI runner's system Python venv not transferable across OS; gunicorn missing on Azure | Pre-build `antenv` in CI; ship gunicorn with the artifact |
| `594a4ee` | Azure never received the startup command override | Set `startup-command` in `webapps-deploy@v3` to write `appCommandLine` directly to App Service config |

**Final working approach (current `azure-deploy.yml`):** OIDC login → copy `requirements-azure.txt` → create zip → `az webapp deploy --type zip` (Oryx builds venv on target) → `az webapp config set` to enforce startup command.

**New files:** `requirements-azure.txt`, `Procfile`, `startup.sh`, `.webappignore`, `.github/workflows/azure-deploy.yml`

### 1.6 Parallel Map Data Loading + Instant Ask Townland Dropdown (`e2189b3`)

Two separate performance regressions on page load:
1. Map page fetched `townlands.json` (6.2 MB) and `unified_data` (4.4 MB) sequentially, then fetched `townlands.json` a second time for the options dropdown
2. Ask page was firing a network round-trip per keystroke in the townland search box

**Fix:**
- `frontend/static/js/main.js`: run `loadTownlandsGeo` and `loadUnifiedData` in `Promise.all` (parallel); skip re-downloading `townlands.json` in `loadOptions` when already cached
- `backend/routes/ask.py`: new `GET /api/ask/townland-catalog` endpoint returning all Wicklow townlands with `name` + `civil_parish` for client-side filtering
- `frontend/static/js/ask.js`: pre-load the catalog on page load; filter client-side on keystrokes; falls back to the API if the catalog is not yet ready

**Files changed:** `backend/routes/ask.py`, `frontend/static/js/ask.js`, `frontend/static/js/main.js`

### 1.7 Security Hardening (`755f6ad`)

Pre-publish security audit identified several hardening gaps.

**Fixes applied:**
- `config.py`: `FLASK_ENV` now defaults to `production` (not `development`); `debug=False` unless explicitly set; `SECRET_KEY` logs an error if the placeholder value is present in production
- `backend/routes/census.py`: `ADMIN_API_KEY` guard on `POST /api/census/refresh` and `POST /api/census/export/regenerate` — returns 403 when the key is unset or wrong (checked via `X-Admin-Key` header)
- `backend/routes/ask.py`: audit log records client IP + question length on each Ask query for abuse detection
- `backend/routes/ask.py`: PDF download rejects non-`.pdf` filenames; forces `application/pdf` mimetype
- `requirements.txt`: `flask-limiter` promoted to a required dependency with minimum version pins

**New env var:** `ADMIN_API_KEY` — set in production to protect admin endpoints.

**Files changed:** `.env.example`, `app.py`, `backend/routes/ask.py`, `backend/routes/census.py`, `config.py`, `create_app.py`, `requirements.txt`

---

## 2. Outstanding Items (as of 2026-06-21)

| Item | Priority | Notes |
|---|---|---|
| D8 — Load co: ontology repo with data | High | `scripts/rdf_uplift.py` + GraphDB data load; needed for RQ6 full comparison |
| D3 — Dataset audit | Medium | 4 SQL queries against `coolattin.db`; just needs running and write-up |
| D4 — Geospatial alignment audit | Medium | 4 SQL queries against `townland` table; read `reconciliation_gaps.csv` |
| D10 — Free-form LLM eval | Medium | Run 10+ non-template questions; record SQL validity + repair rate |
| D11 — User evaluation | Medium | 4–6 participants; task-based session |
| D5 review UI | Low | Entity resolution candidate review page; data is already there |
| Grok `XAI_API_KEY` / `GROK_API_KEY` | Low | Set in Azure App Service to enable the full Claude → Grok → OpenRouter chain |
| D12 — Dissertation write-up | Required | Weeks 7–12 of plan |

---

## 3. Architecture Decisions (June 21)

**Why use Voyage AI instead of BAAI/bge-large locally on Azure?** `torch` + `sentence-transformers` total ~2 GB and cause pip OOM or timeout during Azure Oryx builds. Voyage AI is a lightweight API client with the same 1024-dim output schema — no schema migration needed. The local BGE path remains available for development.

**Why gthread workers for SSE?** Gunicorn's default `sync` worker can only handle one concurrent request per worker. A long-lived SSE stream blocks the worker for its entire duration. `gthread` workers use a thread pool per worker, so concurrent SSE connections (one per Ask query) don't deadlock each other.

**Why enforce the startup command after every deploy?** `az webapp up` and some Oryx heuristics reset `appCommandLine` to a default on each deploy. The CI workflow's `az webapp config set` step at the end of each deploy pins the exact gunicorn command, overriding any auto-detected value.

**Why `ADMIN_API_KEY` rather than role-based auth?** The refresh and export endpoints are infrequently used admin actions on a dissertation project with no user accounts. A shared secret header is the simplest mechanism that closes the unauthenticated POST vulnerability without adding a session/auth layer.

---

# Handoff Notes — June 2026 Performance & Stability Sprint

**Sprint period:** 10–21 June 2026  
**Commits covered:** `666e790`, `371f7b8`, `c82377a`, `fddfdd8`, `dd02e46`

---

## 1. What Was Fixed / Built

### 1.1 Workhouse ER Performance (`666e790`)

The workhouse entity resolution pipeline was producing 15–25 s page loads on the home page because the ER `_match_index` was being rebuilt on every request.

**Fix:** Cached the match index in the `workhouse_service` module with a 10-minute in-process TTL. Batched the ER candidate query from individual `SELECT` calls per record to a single `WHERE id IN (...)` query (max batch size 200).

**Security hardening applied in the same commit:**
- Replaced f-string SQL interpolation with parameterised `sqlite3` placeholders throughout `workhouse_entity_resolution.py`, `scoring.py`, and `normalise.py` to eliminate SQL injection risk
- Validated GeoJSON `lat`/`lon` values are numeric before use in the Leaflet map marker constructor (crashed browser on `null` or `"N/A"` geometry)
- Removed a reflected-input XSS vector in the home page search box — user input is now escaped before insertion into the DOM

**Files changed:** `backend/services/workhouse_entity_resolution.py`, `backend/services/entity_resolution/scoring.py`, `backend/services/entity_resolution/normalise.py`, `backend/services/workhouse_service.py`, `frontend/static/js/main.js`, `frontend/templates/base.html`

### 1.2 Seed Database Updated (`371f7b8`)

The committed `coolattin.db` seed snapshot was updated to include:
- 140 confirmed workhouse→estate entity resolution links (in `workhouse_unified_links`)
- The full in-process property graph (49,081 `graph_nodes` rows, 64,342 `graph_edges` rows) pre-loaded so the app starts without needing to run `scripts/build_graph.py`

This means fresh Azure deployments that use the seed DB get both the ER links and GraphRAG graph immediately without a separate build step.

### 1.3 Map Load + Unified Records Cache (`c82377a`)

**Problems:**
- The Leaflet map was silently failing to render on Azure because `/api/unified/records` (called by the map marker layer) was timing out — the unified DataFrame was being rebuilt from CSV on every cold start under gunicorn
- `/api/unified/records` with no filter was returning all 13,707 rows synchronously, blocking the gunicorn worker for 8–12 s

**Fixes:**
- Added a module-level `_UNIFIED_RECORDS_CACHE` in `unified_service.py` with a 5-minute TTL; the DataFrame is loaded once and served from cache on subsequent requests
- Added `limit=500` default cap to the `/api/unified/records` endpoint when called without explicit filters; the map marker layer now requests only what it needs
- Batched the ER enrichment call in `unified_service.search_records()`: instead of one DB round-trip per row, a single `WHERE record_id IN (...)` query fetches all workhouse link data

**Files changed:** `backend/services/unified_service.py`, `backend/routes/unified.py`

### 1.4 Gunicorn Worker Timeout Fix (`fddfdd8`)

The `/api/unified/records` endpoint was hitting the gunicorn 30 s worker timeout on Azure on cold start because the CSV load + DataFrame build was running synchronously in the request cycle.

**Fix:** Pre-warm the unified cache at application startup in `create_app.py` by calling `unified_service._warm_cache()` inside the app context after blueprints are registered. The first gunicorn worker to start pays the CSV load cost (~2 s); subsequent requests and workers hit the in-process cache.

**Files changed:** `create_app.py`, `backend/services/unified_service.py`

### 1.5 CSP Fix — Leaflet and D3.js CDN Resources (`dd02e46`)

The Content Security Policy headers in `base.html` were blocking Leaflet tile requests (`*.openstreetmap.org`) and D3.js CDN script loads (`cdn.jsdelivr.net`) on Azure, producing silent map/graph failures in production.

**Fix:** Updated the `Content-Security-Policy` header in `base.html`:
- Added `https://*.openstreetmap.org` and `https://*.tile.openstreetmap.org` to `img-src` and `connect-src`
- Added `https://cdn.jsdelivr.net` to `script-src` and `style-src`
- Added `https://unpkg.com` to `script-src` (Leaflet CDN fallback)

**Files changed:** `frontend/templates/base.html`

---

## 2. What Changed in Existing Files (June 14–21)

| File | What changed |
|---|---|
| `backend/services/unified_service.py` | Added `_UNIFIED_RECORDS_CACHE` (5-min TTL), `_warm_cache()`, batch ER enrichment; `search_records()` now cache-first |
| `backend/routes/unified.py` | Added `limit=500` default cap on no-filter requests |
| `backend/services/workhouse_service.py` | Match index cached with 10-min TTL |
| `backend/services/workhouse_entity_resolution.py` | Parameterised all SQL; removed f-string interpolation |
| `backend/services/entity_resolution/scoring.py` | Parameterised SQL queries |
| `backend/services/entity_resolution/normalise.py` | Parameterised SQL queries |
| `frontend/static/js/main.js` | Fixed null-geometry crash on Leaflet marker construction; escaped DOM insertion of user search input |
| `frontend/templates/base.html` | CSP header updated to allow Leaflet CDN + D3.js CDN + OSM tiles |
| `create_app.py` | Added `unified_service._warm_cache()` call on startup |
| `coolattin.db` | Seed snapshot updated with 140 ER links + full graph_nodes/graph_edges |

---

## 3. Outstanding Items (as of 2026-06-21)

| Item | Priority | Notes |
|---|---|---|
| D8 — Load co: ontology repo with data | High | `scripts/rdf_uplift.py` + GraphDB data load; needed for RQ6 full comparison |
| D3 — Dataset audit | Medium | 4 SQL queries against `coolattin.db`; just needs running and write-up |
| D4 — Geospatial alignment audit | Medium | 4 SQL queries against `townland` table; read `reconciliation_gaps.csv` |
| D10 — Free-form LLM eval | Medium | Run 10+ non-template questions; record SQL validity + repair rate |
| D11 — User evaluation | Medium | 4–6 participants; task-based session |
| D5 review UI | Low | Entity resolution candidate review page; data is already there |
| D12 — Dissertation write-up | Required | Weeks 7–12 of plan |

---

## 4. Architecture Decision Log (June 14–21)

**Why pre-warm the unified cache at startup rather than on first request?** Under gunicorn with multiple workers, first-request warming means whichever worker handles the first `/api/unified/records` call pays the full cold-start cost and the response is slow. Pre-warming in `create_app.py` ensures all workers share a warm cache before traffic arrives.

**Why cap `/api/unified/records` at 500 by default?** The map marker layer never needs all 13,707 rows — it renders pins for the visible viewport. Sending all rows to the browser on every page load was responsible for both the gunicorn timeout and the slow map render. The explicit `?limit=` parameter still allows callers to request more.

**Why batch ER enrichment rather than per-row queries?** The original `search_records()` loop called `workhouse_service.get_matches(record_id)` for each returned row, producing N+1 queries for every search. Replacing with a single `WHERE record_id IN (...)` reduces round-trips from O(n) to O(1) regardless of result set size.
