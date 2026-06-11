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

The Ask page (`/ask`) runs an orchestrated 7-phase pipeline (`ASK_USE_NEW_PIPELINE=true` by default):

1. **Intent routing** (`intent_router.py`) — classifies questions as ANALYTICAL / RELATIONAL / COMPARATIVE / FALLBACK.
2. **Hybrid retrieval / fast lane** (`embedding_index.py`) — TF-IDF + optional dense vector retrieval over templates, approved memory, and corpus chunks; high-confidence hits short-circuit remaining phases.
3. **Semantic layer** (`semantic_layer.py`) — slot-fill compiler maps analytical questions to deterministic SQL + equivalent SPARQL without any LLM call.
4. **Subgraph engine** (`subgraph_engine.py`) — KG traversal for relational/hierarchy/heritage questions via VRTI and local GraphDB.
5. **LLM SQL generation** (fallback only) — invoked only when no earlier phase produced a valid query.
6. **Identity resolution** (`identity_resolver.py`) — disambiguates repeated names using Jaro-Winkler + phonetic blocking + geographic/temporal scoring.
7. **Multi-model synthesis** — aggregates SQL, KG results, and retrieved chunks; detects cross-source discrepancies; produces provenance-annotated answer.

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
