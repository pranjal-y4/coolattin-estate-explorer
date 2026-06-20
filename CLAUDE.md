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
│       ├── data/           # Static GeoJSON, CSV, and seed data served by Flask
│       └── images/
│
├── data/
│   ├── seed/               # Canonical reference data checked into git
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

The Ask page (`/ask`) runs an orchestrated pipeline (`ASK_USE_NEW_PIPELINE=true` by default). Entry point: `_orchestrated_pipeline_stream()` in `ask_service.py`.

### Pipeline phases

| Phase | File | What it does |
|---|---|---|
| Phase 1 | `identity_resolver.py` | Entity resolution — resolves townland + person identity once; `sql_id` + `kg_uri` shared by all downstream lanes |
| Phase 2 | `semantic_layer.py` | Slot-fill compiler — maps analytical questions to deterministic SQL/SPARQL; three sub-layers: rule-based fill → LLM slot-fill → deterministic compiler |
| Phase 3 | `subgraph_engine.py` | KG traversal — multi-hop VRTI SPARQL + GraphDB queries for relational/hierarchy/heritage questions |
| Phase 4 | `embedding_index.py` | Hybrid retrieval — TF-IDF unigram+bigram cosine + keyword overlap → RRF over templates and approved memory |
| Phase 5 | `intent_router.py` | Intent classification → ANALYTICAL / RELATIONAL / COMPARATIVE / FALLBACK |
| Phase 6 | `ask_service.py` | Fusion & reconciliation — cross-source discrepancy detection between SQL + KG results |
| Phase 7 | `ask_service.py` | Multi-model synthesis — LLM rewrites aggregated data into provenance-annotated answer |

### Intent classification flow (Phase 5)

Before `classify_intent` is ever called, **four fast lanes** can short-circuit routing entirely:

1. **Semantic layer rule-based fill** — `try_rule_based_fill()` confidence ≥ 0.80 → deterministic SQL, no LLM, no routing.
2. **Verified analysis** — question matches a pre-validated hard-coded SQL template.
3. **Direct memory reuse** — approved thumbs-up query (high token-sort-ratio + cosine ≥ 0.55) → reuse cached SQL.
4. **Phase 4 template fast lane** — TF-IDF/RRF cosine ≥ 0.68 on embedded templates → use template SQL directly.

If no fast lane fires, `classify_intent(question, analysis, slot_fill)` runs with this **priority order** (first match wins):

**1. COMPARATIVE** — any comparative keyword present:
> `compare`, `compared to`, `compared with`, `versus`, `vs`, `difference between`, `contrast`, `relative to`, `how does`, `how did`, `better than`, `worse than`, `more than`, `less than`, `higher than`, `lower than`, `against`

**2. RELATIONAL** — geography intent from `_analyse_question`, OR any keyword from these groups:
- *Relational*: `related to`, `connected to`, `link between`, `in the same parish`, `same barony`, `part of`, `neighbouring`, `adjacent to`, `bordering`, `relationship between`, `linked to`
- *Hierarchy*: `which parish`, `what parish`, `civil parish`, `in the barony`, `townlands in`, `where is`, `where does`, `located in`, `situated in`, `falls within`
- *Heritage*: `heritage`, `archaeological`, `monument`, `ring fort`, `holy well`, `history of`, `tell me about`, `describe`, `historically`, `fortification`, `earthwork`
- *Sensemaking*: `overview`, `about the estate`, `about coolattin`, `describe the estate`, `what kind of`, `background`, `summary of`, `general context`
- **Exception** (Core Rule 1 override): if *only* heritage/sensemaking keywords triggered (no relational/hierarchy/geography signal) AND `output_mode` is `count`/`aggregate` AND any analytical keyword is present → falls through to **ANALYTICAL** instead.

**3. ANALYTICAL** — any of:
- `primary_intent` in `{population, eviction, emigration, tenancy}`
- `output_mode` in `{count, aggregate, trend}`
- Any analytical keyword: `how many`, `how much`, `total`, `count of`, `number of`, `average`, `mean`, `proportion`, `percent`, `percentage`, `per year`, `by year`, `trend`, `over time`, `distribution`, `breakdown`, `most`, `least`, `highest`, `lowest`, `maximum`, `minimum`, `sum of`, `rate`, `ratio`
- `slot_fill is not None` (semantic layer found any candidate)

**4. FALLBACK** — default when nothing above matched.

### Dispatch per route

| Route | What runs |
|---|---|
| **ANALYTICAL** | Phase 2 semantic_layer: rule-based slot-fill (0 LLM) → LLM slot-fill if confidence < 0.80 → deterministic SQL compiler. Never free-form LLM SQL. |
| **RELATIONAL / HERITAGE** | Phase 3 subgraph (VRTI SPARQL + GraphDB) for qualitative context, then FALLBACK SQL lane for any numeric counts (counts always come from SQL, never the KG). |
| **COMPARATIVE** | ANALYTICAL SQL + RELATIONAL subgraph in parallel. Phase 6 reconciliation handles cross-source discrepancies. |
| **FALLBACK** | Old pipeline: verified_analysis → Phase 4 embedding template → approved memory → LLM free-form SQL generation. |

All lanes then continue through safety check → SQLite execution → VRTI → GraphDB → Phase 6 fusion → Phase 7 LLM rewrite → SSE result.

SSE streaming: each phase yields `{type, stage, status, detail, duration_ms}` events. Do not buffer.
PDF generation is hand-written (no reportlab/fpdf dependency), written to `exports/ask/`.

**Workhouse entity resolution** is a separate subsystem from the Ask pipeline:
- `workhouse_entity_resolution.py` orchestrates mention building → candidate generation → scoring → persistence
- `entity_resolution/` subpackage handles normalise, candidates, scoring
- Results stored in `source_mentions`, `entity_resolution_candidates`, `workhouse_unified_links`, `entity_resolution_decisions` tables
- Does not use the LLM, pgvector, or the Ask pipeline

## Database schema

All tables created/migrated by `extensions.py::ensure_schema()`:

| Table | Purpose |
|---|---|
| `townland` | Canonical townland reference — enriched from VRTI KG + estate GeoJSON |
| `census_record` | Population per townland × year (1841–1891 from KG, 1827–1868 from estate) |
| `clearances_record` | Estate evictions per townland × year (1847–1856) |
| `refresh_state` | Dataset freshness tracking |
| `ask_query_memory` | Approved question→SQL pairs (thumbs-up feedback; reused by retrieval) |
| `ask_query_feedback` | All feedback submissions (up + down) for review |
| `match_review` | Human-review queue for entity resolution candidates |
| `source_mentions` | One row per name occurrence in a source record (workhouse ER) |
| `entity_resolution_candidates` | Scored candidate links: mention → unified_record (workhouse ER) |
| `workhouse_unified_links` | Final accepted workhouse→estate record links |
| `entity_resolution_decisions` | Human review decisions on candidates |

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
