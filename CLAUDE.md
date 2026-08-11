# CLAUDE.md — Coolattin Estate Records Explorer

Project context for AI-assisted development. Read this before making any changes.

## What this project is

A web application for exploring historical records from the **Coolattin Estate** in County Wicklow, Ireland (mid-19th century). It integrates tenancy, eviction, emigration, and census data into a unified searchable interface with an interactive map, analytics dashboards, and a natural-language Q&A system backed by an LLM.

This is a **Masters Dissertation** project — the codebase must remain stable and reproducible for academic submission.

## Tech stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12 · Flask (application factory pattern) |
| Database | SQLite via raw `sqlite3` (no ORM) |
| External KG | VRTI (Virtual Record Treasury of Ireland) — SPARQL endpoint |
| LLM | OpenRouter (cloud) or Ollama (local fallback) — OpenAI-compatible API |
| Frontend | Vanilla JS · Leaflet.js · Jinja2 templates |
| Node deps | `@supabase/supabase-js` · `leaflet` (used as static files) |
| PDF export | Hand-written PDF 1.4 (no library dependency) |
| Data formats | GeoJSON · CSV · JSON · Excel (openpyxl) |

## Directory layout

```
Coolattin-app/
├── app.py                  # Entry point — runs the Flask dev server
├── create_app.py           # Application factory (register blueprints, init DB)
├── config.py               # All config in one place; env vars override defaults
├── extensions.py           # DB singleton (init_db, get_db_conn, ensure_schema)
│
├── backend/
│   ├── routes/             # Flask blueprints (one file = one URL prefix)
│   │   ├── ask.py          #   /api/ask/*  — LLM Q&A + PDF export
│   │   ├── census.py       #   /api/census/*
│   │   ├── exports.py      #   /api/exports/*
│   │   ├── main.py         #   / (page routes)
│   │   ├── map_config.py   #   /api/map/*
│   │   ├── townlands.py    #   /api/townlands/*
│   │   └── unified.py      #   /api/unified/*
│   ├── services/           # Business logic (called by routes)
│   │   ├── ask_service.py  #   Orchestrated 7-phase Ask pipeline + SSE streaming
│   │   ├── ask_eval.py     #   Evaluation harness (eval_results/ JSON baselines)
│   │   ├── intent_router.py #  Intent classification: ANALYTICAL/RELATIONAL/COMPARATIVE/FALLBACK
│   │   ├── semantic_layer.py #  Slot-fill compiler → deterministic SQL + SPARQL (no LLM)
│   │   ├── subgraph_engine.py # KG traversal for relational/hierarchy/heritage questions
│   │   ├── embedding_index.py # Hybrid TF-IDF + dense retrieval; fast-lane template/memory hits
│   │   ├── identity_resolver.py # Three-layer identity model: Mention→Person→Factoid
│   │   ├── voyage_embeddings.py # Cohere Embed v3 dense embeddings client
│   │   ├── ask_pgvector.py #   Optional pgvector retrieval backend (requires Postgres DATABASE_URL)
│   │   ├── local_embeddings.py # Local BAAI/bge-large-en-v1.5 SentenceTransformer embeddings
│   │   ├── retrieval_chunks.py # Chunk builders for retrieval corpus (person/place/event)
│   │   ├── entity_resolver.py  # Entity resolution utilities
│   │   ├── workhouse_entity_resolution.py # Persisted workhouse→unified-record matching pipeline
│   │   ├── entity_resolution/ #  Subpackage: normalise.py · candidates.py · scoring.py
│   │   ├── census_service.py
│   │   ├── export_service.py
│   │   ├── map_service.py
│   │   ├── refresh_service.py
│   │   ├── townland_service.py
│   │   ├── townland_resolution.py # Source townland → canonical entity ER flow (xref + provenance)
│   │   ├── unified_service.py
│   │   └── workhouse_service.py
│   ├── repositories/       # All SQL queries (no raw SQL outside here)
│   │   └── match_review_repository.py  # CRUD for entity resolution match review
│   ├── models/             # Dataclass/typed-dict definitions
│   ├── integrations/       # External API clients
│   │   ├── vrti_sparql.py  #   VRTI SPARQL queries (expanded)
│   │   ├── graphdb_sparql.py # Local GraphDB SPARQL client (co: ontology)
│   │   └── townlands_reference.py
│   └── jobs/               # One-shot ingest jobs (run manually or at startup)
│       ├── full_ingest.py
│       ├── source_townland_ingest.py  # Estate-record townland names → canonical entities
│       ├── census_ingest.py
│       └── townlands_ingest.py
│
├── analytics/              # Pluggable analytics modules (KPI + chart data)
│   ├── base.py             # Protocol definitions (AnalyticsModule, KPI, Chart)
│   ├── registry.py         # Module auto-discovery
│   └── *.py                # One file per dataset (emigrations, evictions, …)
│
├── frontend/
│   ├── templates/          # Jinja2 HTML (base.html + one per page)
│   └── static/
│       ├── css/main.css
│       ├── js/             # One JS file per page (ask.js, census.js, …)
│       ├── data/           # Static GeoJSON, CSV, Excel, and seed data served by Flask
│       │                   # (unified_processed.csv, workhouse_data_final.xlsx,
│       │                   #  unified_census.csv, townlands.json, *.geojson)
│       └── images/
│
├── data/
│   ├── seed/               # Non-CSV reference data: community_summaries.json,
│   │                       # townland_aliases.json, coolattin.ttl (RDF uplift)
│   └── source_snapshots/   # Local copies of external API responses (gitignored)
│
├── scripts/                # One-off data processing scripts
├── extra_datasets/         # NMS heritage open-data CSVs
├── exports/                # Runtime output: Excel + PDF (gitignored)
└── _archive/               # Deprecated code kept for reference only
```

## Running the application

```bash
# 1. Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Copy and configure environment
cp .env.example .env.local
# Edit .env.local — at minimum add OPENROUTER_API_KEY for the Ask page

# 4. Start the server
python3 app.py
# → http://127.0.0.1:5001
```

The database (`coolattin.db`) is created and migrated automatically on first run. To populate it with live data from the VRTI Knowledge Graph, visit `/api/census/refresh` or trigger a full ingest via the `/ingest` slash command.

## Key architectural decisions

- **No ORM** — all SQL is in `backend/repositories/`. Keep it that way. Raw `sqlite3` is intentional for transparency and portability.
- **Application factory** — `create_app()` in `create_app.py` is the only place blueprints are registered. Never import `app` directly from `app.py` in other modules.
- **`extensions.py` is the DB singleton** — any module needing a DB connection imports `get_db_conn()` from here. Never create a `sqlite3.connect()` elsewhere.
- **`config.py` is the single source of truth** — all tunable values live in `Config`, `DevelopmentConfig`, or `ProductionConfig`. Never hard-code paths or timeouts in service files.
- **SSE streaming in ask_service** — the `/api/ask/query` endpoint streams Server-Sent Events. Each pipeline stage yields a JSON event with `type`, `stage`, and `detail`. Do not buffer this response.
- **Analytics are pluggable** — add a new analytics module by creating a class that implements the `AnalyticsModule` protocol in `analytics/base.py` and registering it in `analytics/registry.py`.

## LLM / Ask pipeline

The Ask page (`/ask`) has two pipelines controlled by `ASK_USE_NEW_PIPELINE` (default `true`).
Public entry point: `answer_question_stream()` → delegates based on the flag.

### Default pipeline (`ASK_USE_NEW_PIPELINE=true`) — `_orchestrated_pipeline_stream()`

Runs in this exact order. No intent routing; `intent_route` is always `"direct"`.

| Step | Code location | What it does |
|---|---|---|
| Phase 1 | `identity_resolver.py` | Entity resolution once: fuzzy townland match + optional person identity; `sql_id` + `kg_uri` shared downstream |
| SQL | `ask_service._generate_sql()` | Direct LLM SQL generation — no fast lanes, no memory reuse, no semantic layer |
| GraphRAG | `graphrag.retrieve_subgraph()` | Only if townland resolved: load in-process NetworkX graph → exact townland seed → k-hop BFS → prune → linearise community summaries + place hierarchy + triples |
| Townland summary | inline SQL in `ask_service` | 5 hardcoded queries: emigration / eviction / tenancy / census / workhouse counts for synthesis context |
| Stage 2 | `_sanitize_and_validate_sql()` | Read-only SQL safety guard |
| Stage 3 | `_execute_with_recovery()` | SQLite execution; auto-repairs failed SQL via LLM |
| Stage 4 | `_kg_context()` | VRTI SPARQL — townland/parish metadata enrichment |
| GraphRAG injection | `ask_service` | Appends GraphRAG linearized block to `kg_context["subgraph_linearized"]` |
| Phase 6 | `_fuse_lanes()` | Cross-source discrepancy detection between SQL + KG |
| Phase 7 | `_synthesize_answer()` | LLM cascade: Claude (Anthropic API) → Grok (xAI) → OpenRouter/Ollama; numeric hallucination gate |

**Not active in the default pipeline:** semantic layer (Phase 2), embedding index (Phase 4), intent router (Phase 5), subgraph engine (Phase 3), GraphDB SPARQL (Stage 4.5 — dead because `intent_route` is always `"direct"`).

### Legacy pipeline (`ASK_USE_NEW_PIPELINE=false`) — inline in `answer_question_stream()`

Runs when the env var is explicitly set to false. Has four fast lanes that short-circuit before any LLM call, then intent-based routing.

**Fast lanes (checked in order, first hit wins):**

1. **Semantic layer rule-based fill** — `semantic_layer.try_rule_based_fill()` confidence ≥ 0.80 → deterministic SQL, 0 LLM calls.
2. **Phase 4 template fast lane** — `embedding_index` TF-IDF/RRF cosine ≥ 0.68 → use embedded template SQL directly.
3. **Verified analysis** — question matches a pre-validated hard-coded SQL template.
4. **Direct memory reuse** — approved thumbs-up query (token-sort-ratio + cosine ≥ 0.55) → reuse cached SQL from `ask_query_memory`.

**If no fast lane fires → `intent_router.classify_intent()` (priority order):**

**1. COMPARATIVE** — any comparative keyword: `compare`, `versus`, `vs`, `difference between`, `contrast`, `relative to`, `how does`, `how did`, `better/worse/more/less/higher/lower than`, `against`

**2. RELATIONAL** — geography intent OR keywords from:
- *Relational*: `related to`, `connected to`, `link between`, `in the same parish`, `same barony`, `part of`, `neighbouring`, `adjacent to`, `bordering`, `relationship between`, `linked to`
- *Hierarchy*: `which parish`, `what parish`, `civil parish`, `in the barony`, `townlands in`, `where is`, `where does`, `located in`, `situated in`, `falls within`
- *Heritage*: `heritage`, `archaeological`, `monument`, `ring fort`, `holy well`, `history of`, `tell me about`, `describe`, `historically`, `fortification`, `earthwork`
- *Sensemaking*: `overview`, `about the estate`, `about coolattin`, `describe the estate`, `what kind of`, `background`, `summary of`, `general context`
- **Exception**: if *only* heritage/sensemaking keywords triggered AND `output_mode` is `count`/`aggregate` AND any analytical keyword → routes to **ANALYTICAL** instead.

**3. ANALYTICAL** — `primary_intent` in `{population, eviction, emigration, tenancy}`, OR `output_mode` in `{count, aggregate, trend}`, OR any of: `how many`, `how much`, `total`, `count of`, `number of`, `average`, `mean`, `proportion`, `percent`, `per year`, `by year`, `trend`, `over time`, `distribution`, `breakdown`, `most`, `least`, `highest`, `lowest`, `maximum`, `minimum`, `sum of`, `rate`, `ratio`, OR `slot_fill is not None`.

**4. FALLBACK** — default.

**Dispatch per route (legacy pipeline only):**

| Route | What runs |
|---|---|
| **ANALYTICAL** | Phase 2 semantic_layer LLM slot-fill → deterministic SQL compiler. Never free-form LLM SQL. |
| **RELATIONAL / HERITAGE** | Phase 3 `subgraph_engine` (VRTI SPARQL + GraphDB) for qualitative context; SQL handles all counts. |
| **COMPARATIVE** | ANALYTICAL SQL + RELATIONAL subgraph; Phase 6 fuses discrepancies. |
| **FALLBACK** | LLM free-form SQL via `_generate_sql()`. |

All routes then: safety check → SQLite execution → VRTI → Phase 3 subgraph (if relational/comparative) → Phase 6 fusion → Phase 7 LLM synthesis.

SSE streaming: each phase yields `{type, stage, status, detail, duration_ms}` events. Do not buffer.
PDF generation is hand-written (no reportlab/fpdf dependency), written to `exports/ask/`.

**Townland entity resolution** (`backend/services/townland_resolution.py`) turns a source townland record into a canonical `townland` entity:

- `resolve_source_townland(SourceTownland)` — normalise → xref replay / exact / alias (`data/seed/townland_aliases.json`, compound names in `townland_compound_map.json`) → blocked candidates → shared authority id → `townland_service.score_pair`/`decide_match` → merge, `match_review` pending, or new canonical
- One source record = one transaction; idempotent on `(source, source_record_id)`
- A name alone never merges, and an explicit county/barony/parish conflict blocks even an exact-name or authority-id match
- Callers: `backend/jobs/full_ingest.py` (estate GeoJSON) and `backend/jobs/source_townland_ingest.py` (estate record place names, plus `--enrich-geometry` for VRTI boundaries)

**Map data comes from the database.** `GET /static/data/townlands.json` is served by `create_app` from `map_service.build_townland_featurecollection()`: the estate GeoJSON is the geometry baseline, each feature is stamped with its canonical `entity_id`, and canonical townlands the database holds with geometry are appended. Never hard-code townlands in the frontend — ingest them.

**Workhouse entity resolution** is a separate subsystem from the Ask pipeline:
- `workhouse_entity_resolution.py` orchestrates mention building → candidate generation → scoring → persistence
- `entity_resolution/` subpackage handles normalise, candidates, scoring
- Results stored in `source_mentions`, `entity_resolution_candidates`, `workhouse_unified_links`, `entity_resolution_decisions` tables
- Does not use the LLM, pgvector, or the Ask pipeline

## Database schema

All tables created/migrated by `extensions.py::ensure_schema()`:

| Table | Purpose |
|---|---|
| `townland` | Canonical townland reference — `entity_id` UUID + `name` (not UNIQUE) + `qualifier` |
| `townland_xref` | Maps `(source, source_record_id)` → `entity_id`; multi-source cross-reference. Also keeps `source_name` (observed spelling), `status`, `evidence_json`, `conflicts_json` |
| `field_provenance` | Field-level survivorship: which source won each field value and why |
| `census_record` | Population per townland × year (1841–1891 from KG, 1827–1868 from estate) |
| `clearances_record` | Estate evictions per townland × year (1847–1856) |
| `refresh_state` | Dataset freshness tracking |
| `unified_record` | 13,707 estate person records (runtime-seeded from `unified_processed.csv`) |
| `heritage_feature` | NMS heritage monuments (runtime-seeded from GeoJSON files) |
| `ask_query_memory` | Approved question→SQL pairs (thumbs-up feedback; reused by retrieval) |
| `ask_query_feedback` | All feedback submissions (up + down) for review |
| `match_review` | Uncertain townland-pair review queue (ingest-time reconciliation review) |
| `source_mentions` | One row per name occurrence in a source record (workhouse ER); uses `source_table`/`normalised_name`/`phonetic_forename`/`phonetic_surname` columns |
| `entity_resolution_candidates` | Scored candidate links: `label` column (not `band`), `evidence_json`/`conflicts_json` |
| `workhouse_unified_links` | Final accepted workhouse→estate record links |
| `entity_resolution_decisions` | Human review decisions on candidates |
| `graph_nodes` | 49,081 GraphRAG nodes: `props` (JSON), `embedding` (BLOB), `community` columns |
| `graph_edges` | 64,308 directed edges: composite PK `(src, dst, rel_type)`, `props` (JSON) |

## Code conventions

- All Python files use `from __future__ import annotations`.
- Logging uses `log = logging.getLogger(__name__)` — never `print()` in backend code.
- Route handlers are thin — business logic goes in services.
- SQL stays in repositories — services call repository functions, not raw SQL.
- No comments unless the WHY is non-obvious. Well-named identifiers are self-documenting.

## What NOT to do

- Do not add an ORM (SQLAlchemy etc.) — it would break the existing query architecture.
- Do not modify `coolattin.db` directly — always go through the ingest jobs or API.
- Do not commit `.env.local`, `coolattin.db`, `venv/`, `exports/`, or `node_modules/`.
- Do not add new top-level Python files — route files go in `backend/routes/`, services in `backend/services/`.
- Do not introduce new npm packages without a clear reason — the frontend is intentionally dependency-light.
- Do not change the SSE streaming protocol in `ask_service.py` without updating `frontend/static/js/ask.js` to match.
