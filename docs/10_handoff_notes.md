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
| 1 | `intent_router.py` | Classifies question: ANALYTICAL / RELATIONAL / COMPARATIVE / FALLBACK |
| 2 | `embedding_index.py` | Hybrid TF-IDF + dense retrieval; fast-lane short-circuit for high-confidence template/memory hits |
| 3 | `semantic_layer.py` | Slot-fill compiler → deterministic SQL + SPARQL without LLM |
| 4 | `subgraph_engine.py` | KG traversal (VRTI + GraphDB) for relational/hierarchy/heritage questions |
| 5 | `ask_service.py` | LLM SQL generation (fallback only) |
| 6 | `identity_resolver.py` | Person name disambiguation (Mention/Person/Factoid three-layer model) |
| 7 | `ask_service.py` | Multi-model synthesis: aggregate SQL + KG + retrieved chunks → structured answer |

**Key SSE event stages added:** `retrieving_vectors`, `retrieving_sparse`, `fusing_results`, `synthesising_answer`, `done`

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
