# System Architecture and Complete Workflow
## Coolattin Estate Records Explorer

**Project:** Coolattin Estate Records Explorer  
**Candidate:** Pranjal Yadav  
**Programme:** MSc Computer Science (Interactive Digital Media) — Trinity College Dublin  
**Supervisors:** Dr Ciarán Wallace (VRTI) · Prof Declan O'Sullivan (CS)  
**Document type:** Definitive technical reference — covers every component, every service, every data flow  
**Last updated:** July 2026  
**Deployment:** Azure App Service — Italy North region (`coolattin-app.azurewebsites.net`)

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [High-Level Architecture Diagram](#2-high-level-architecture-diagram)
3. [Infrastructure and Deployment](#3-infrastructure-and-deployment)
4. [Data Sources and Ingestion Pipeline](#4-data-sources-and-ingestion-pipeline)
5. [Database Schema — All Tables](#5-database-schema--all-tables)
6. [Application Bootstrap Sequence](#6-application-bootstrap-sequence)
7. [Flask Blueprint Layer — API Reference](#7-flask-blueprint-layer--api-reference)
8. [Service Layer — Business Logic](#8-service-layer--business-logic)
9. [Analytics Modules](#9-analytics-modules)
10. [Frontend Architecture](#10-frontend-architecture)
11. [The Ask Pipeline — Seven-Phase Orchestrator](#11-the-ask-pipeline--seven-phase-orchestrator)
12. [Four Fast Lanes — Pre-Classification Short-Circuits](#12-four-fast-lanes--pre-classification-short-circuits)
13. [Intent Classification and Route Dispatch](#13-intent-classification-and-route-dispatch)
14. [Semantic Layer — Deterministic SQL Compiler](#14-semantic-layer--deterministic-sql-compiler)
15. [Subgraph Engine — Knowledge Graph Traversal](#15-subgraph-engine--knowledge-graph-traversal)
16. [In-Process GraphRAG Engine](#16-in-process-graphrag-engine)
17. [Hybrid Embedding Retrieval](#17-hybrid-embedding-retrieval)
18. [Identity Resolution — Three-Layer Model](#18-identity-resolution--three-layer-model)
19. [Multi-Model LLM Synthesis Chain](#19-multi-model-llm-synthesis-chain)
20. [Workhouse Entity Resolution Subsystem](#20-workhouse-entity-resolution-subsystem)
21. [KG Explore Page — SQL vs SPARQL Comparison](#21-kg-explore-page--sql-vs-sparql-comparison)
22. [Security Architecture](#22-security-architecture)
23. [Configuration Reference](#23-configuration-reference)
24. [In-Process Caches](#24-in-process-caches)
25. [SSE Streaming Protocol](#25-sse-streaming-protocol)
26. [Evaluation Results](#26-evaluation-results)

---

## 1. System Overview

The Coolattin Estate Records Explorer is a single-server Flask web application that integrates five heterogeneous nineteenth-century Irish archival data sources into a unified SQLite database and exposes them through eight web pages and a REST API. The system is intentionally minimal in infrastructure: one Python process, one SQLite file, no message queue, no cache server, no separate worker process.

The most technically complex component is the **Ask page**, which implements a seven-phase orchestrated natural-language pipeline. The pipeline runs deterministic SQL before ever touching an LLM, caches VRTI SPARQL responses per-townland for one hour, enriches relational answers from an in-process NetworkX property graph, and streams results to the browser via Server-Sent Events.

**Why this stack?** The application is a Masters Dissertation prototype. Every technical choice prioritises auditability and reproducibility over operational sophistication:

- **SQLite** (not PostgreSQL) — single file, zero setup, examiners can open it directly
- **Raw SQL** (not ORM) — every query is a plain text string in `backend/repositories/`
- **Vanilla JS** (not React) — six pages; a framework adds a build step with no benefit
- **Hand-written PDF 1.4** (not reportlab) — no external dependency that can break
- **LLM last** (not first) — the pipeline answers 100% of analytical questions via deterministic SQL; the LLM rewrites the answer for readability, never generates the numbers

---

## 2. High-Level Architecture Diagram

```
┌───────────────────────────────────────────────────────────────────────────────┐
│  DATA SOURCES (files committed to repo or live SPARQL)                        │
│                                                                               │
│  unified_processed.csv   townlands.json      workhouse_data_final.xlsx        │
│  (13,707 person rows)    (152 GeoJSON polys)  (workhouse pauper register)     │
│                                                                               │
│  holywells_wicklow.geojson  asi_wicklow.geojson  monuments_wicklow.geojson    │
│  (NMS heritage open data — holy wells, monuments, archaeological inventory)   │
│                                                                               │
│  VRTI Virtuoso SPARQL endpoint  ──────────────────────────────────────────    │
│  https://virtuoso.virtualtreasury.ie/sparql/ (census + KG enrichment)        │
└──────────────────────────┬────────────────────────────────────────────────────┘
                           │  one-shot INGEST JOBS (backend/jobs/)
                           │  full_ingest.py · census_ingest.py · townlands_ingest.py
                           ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│  LOCAL DATABASE  (coolattin.db — SQLite 3, WAL mode, foreign_keys=ON)         │
│                                                                               │
│  Canonical tables (ingest-populated):                                         │
│    townland            │ townland_xref       │ field_provenance               │
│    census_record       │ clearances_record   │ refresh_state                  │
│                                                                               │
│  Runtime-seeded tables (populated on first Ask request):                      │
│    unified_record      │ heritage_feature                                      │
│                                                                               │
│  LLM pipeline tables:                                                         │
│    ask_query_memory    │ ask_query_feedback                                    │
│                                                                               │
│  Entity resolution tables:                                                    │
│    source_mentions     │ entity_resolution_candidates                          │
│    workhouse_unified_links │ entity_resolution_decisions │ match_review        │
│                                                                               │
│  GraphRAG tables (built by scripts/build_graph.py):                           │
│    graph_nodes (49,081 nodes)  │  graph_edges (64,308 edges)                  │
└──────────────────────────┬────────────────────────────────────────────────────┘
                           │  SERVE (Flask, gunicorn 4 gthread workers, port 5001)
                           ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│  FLASK APPLICATION  (create_app.py — application factory, 8 blueprints)       │
│                                                                               │
│  main.py      →  / · /census · /ask · /analytics · /heritage · /about        │
│  ask.py       →  /api/ask/query (SSE) · /feedback · /llm-status · /pdf       │
│  census.py    →  /api/census/townlands · /summary · /townland · /refresh      │
│  unified.py   →  /api/unified/records · /stats · /townlands · /surnames       │
│  map_config.py→  /api/map/layers · /config                                   │
│  townlands.py →  /api/townlands · /detail · /geojson                         │
│  exports.py   →  /api/exports/census · /regenerate                           │
│  kg_explore.py→  /kg-explore · /api/kg/graph · /compare · /scenarios         │
└──────────────────────────┬────────────────────────────────────────────────────┘
                           │  RUNTIME ENRICHMENT (Ask page only, per-question)
                           ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│  EXTERNAL RUNTIME SERVICES                                                    │
│                                                                               │
│  LLM Synthesis Chain (priority order, silent fallback):                       │
│    [1] Claude (Anthropic)  ANTHROPIC_API_KEY                                  │
│    [2] Grok (xAI)          GROK_API_KEY                                       │
│    [3] OpenRouter          OPENROUTER_API_KEY → openai/gpt-oss-20b:free       │
│    [4] Ollama local        http://localhost:11434  (offline fallback)         │
│                                                                               │
│  VRTI SPARQL endpoint (per-question enrichment, 1h TTL cache):                │
│    https://virtuoso.virtualtreasury.ie/sparql/                                │
│    Timeout: 30 s · Cooldown on failure: 5 min                                │
│                                                                               │
│  Local GraphDB (co: ontology, D8 comparison prototype):                       │
│    http://localhost:7200/repositories/coolattin                               │
│    or http://51.120.71.162:7200/repositories/coolattin (Azure)               │
│    Timeout: 15 s · Graceful skip on failure                                   │
│                                                                               │
│  In-process GraphRAG (graphrag.py — no external server):                      │
│    NetworkX MultiDiGraph loaded from graph_nodes/graph_edges SQLite tables    │
│    49,081 nodes · 64,308 edges · 28,078 BGE-large-en-v1.5 passport vectors   │
│    vector seed (cosine ANN) → k-hop BFS → linearised subgraph enrichment     │
└───────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Infrastructure and Deployment

### 3.1 Azure App Service

- **Region:** Italy North
- **App name:** `coolattin-app.azurewebsites.net`
- **Resource group:** `coolattin-rg2`
- **Runtime:** Python 3.12 on Linux
- **WSGI server:** Gunicorn with 4 gthread workers (`--worker-class gthread --threads 4`)
- **Startup command:** `startup.sh` → `gunicorn create_app:create_app() --bind=0.0.0.0:8000 ...`
- **Static files:** Served by Flask (Whitenoise not used — development config)

### 3.2 CI/CD Pipeline (`azure-deploy.yml`)

GitHub Actions workflow triggered on push to `main`:

```
1. Checkout → OIDC login to Azure (no long-lived secrets)
2. Swap requirements.txt for requirements-azure.txt
   (excludes torch/sentence-transformers — too large for Azure Oryx build)
3. Zip deploy → Azure App Service
4. Oryx build (PIP install inside the container)
5. Startup command enforcement via az webapp config set
```

The `EMBEDDING_PROVIDER` is set to `voyage` in production because `torch` is excluded from the Azure build. Voyage AI (`voyageai.Client`) provides the same 1024-dim dense embeddings without a 2 GB PyTorch dependency.

### 3.3 Environment Variables

See `.env.example` for the full list. Key variables:

| Variable | Default | Description |
|---|---|---|
| `OPENROUTER_API_KEY` | — | Required for cloud LLM access |
| `ANTHROPIC_API_KEY` | — | Claude synthesis (first in chain) |
| `GROK_API_KEY` | — | Grok synthesis (second in chain) |
| `VOYAGE_API_KEY` | — | Voyage AI embeddings (Azure deployment) |
| `COHERE_API_KEY` | — | Cohere Embed v3 (alternative embeddings) |
| `ASK_USE_NEW_PIPELINE` | `true` | Enable orchestrated 7-phase pipeline |
| `GRAPHRAG_ENABLED` | `true` | Enable in-process GraphRAG enrichment |
| `GRAPHDB_ENABLED` | `true` | Enable local GraphDB SPARQL queries |
| `EMBEDDING_PROVIDER` | `local` | `local` / `cohere` / `voyage` |
| `FLASK_ENV` | `production` | Defaults to production for safety |
| `ADMIN_API_KEY` | — | Guards admin-only endpoints |
| `LLM_ALLOW_PAID` | `true` | Set false to skip Anthropic/Grok calls |

---

## 4. Data Sources and Ingestion Pipeline

### 4.1 Data Sources

| # | Source | File / URL | Format | Scale |
|---|---|---|---|---|
| 1 | Estate people records | `frontend/static/data/unified_processed.csv` | CSV | 13,707 rows |
| 2 | Townland boundaries + estate surveys | `frontend/static/data/townlands.json` | GeoJSON | 152 polygons |
| 3 | Workhouse pauper register | `frontend/static/data/workhouse_data_final.xlsx` | Excel (2 sheets) | ~500 rows |
| 4 | VRTI Knowledge Graph | `https://virtuoso.virtualtreasury.ie/sparql/` | RDF/SPARQL | Census 1841–1891 |
| 5 | NMS heritage features | `frontend/static/data/*.geojson` | GeoJSON | Hundreds of features |
| 6 | Standard census seed | `frontend/static/data/unified_census.csv` | CSV | 165 townlands × 6 years |
| 7 | Townlands.ie reference | `data/seed/townland_aliases.json` | JSON | Name alias map |

### 4.2 Source 1 — Estate People Records (`unified_processed.csv`)

The central dataset. A pre-assembled flat file merging three historical estate ledgers:
- **Emigration ledger** — people who emigrated 1847–1856: ship name, departure/arrival date, household members
- **Eviction/clearances ledger** — court records of evictions and notices to quit
- **Tenancy/rental ledger** — tenancy survey records: tenant names, holdings in acres, rent owed

One row = one person-record. A person appearing in multiple ledgers has multiple flags set (`has_emigration_record`, `has_eviction_record`, `has_tenancy_record`). Key derived columns:

| Column | Derivation |
|---|---|
| `canonical_name` | Normalised `forename + surname` |
| `townland_norm` | Uppercase, no qualifiers (`AGHOWLE LOWER`) |
| `family_key` | Groups household members (`murphy\|aghowle lower`) |
| `is_widow` | Derived from title/role text |
| `is_canada_destination` | "Quebec" / "Montreal" / "Canada" in arrival text |
| `children_count` | `sons + daughters` |
| `family_size_estimate` | Derived from household composition fields |

This file is a permanent seed — it is not generated by any code in the repo. It was assembled from archival sources at the National Archives of Ireland.

### 4.3 Source 2 — Townland GeoJSON (`townlands.json`)

152 polygon features, one per Coolattin Estate townland. Each `properties` object contains:
- `TL_ENGLISH`, `TL_GAEILGE` — English and Irish names
- `AREA` — area in square metres
- Estate population survey totals: `T_POP_1827`, `T_POP_1839_`, `T_POP_1848`, `T_POP_1850`, `T_POP_1860`, `T_POP_1868`
- Clearances counts per year 1847–1856: `Clearances_1847` … `Clearances_1856`

These are estate-survey totals (no male/female breakdown). The national census breakdowns (1841–1891 with male/female/houses) come from VRTI or the seed CSVs.

### 4.4 Source 4 — VRTI Knowledge Graph (SPARQL)

Queried during ingest (not at runtime) for:
- Census records 1841–1891: male, female, inhabited/uninhabited houses, per townland
- Townland boundary geometry (WKT polygon)
- Centroid coordinates (lat/lon)
- Civil parish, barony, county hierarchy
- External identifiers (OSM ID, OSI ID, VRTI URI)

All KG data is pulled once and stored in SQLite. The live endpoint is only queried at runtime for the optional per-question enrichment panel in the Ask page (parallel, non-blocking, 1 h TTL cache).

### 4.5 Ingest Jobs (`backend/jobs/`)

| Job | File | What it does |
|---|---|---|
| Full ingest | `full_ingest.py` | Runs all sub-jobs in order |
| Census ingest | `census_ingest.py` | Pulls VRTI SPARQL census data → `census_record` table |
| Townlands ingest | `townlands_ingest.py` | Reads GeoJSON + reconciles with VRTI + townlands.ie reference |

Trigger: visit `/api/census/refresh` or run the `/ingest` slash command. The `refresh_state` table records when each dataset was last ingested and whether it is stale.

---

## 5. Database Schema — All Tables

All tables are created and migrated by `extensions.py::ensure_schema()`. The database uses WAL mode and `foreign_keys=ON`. All code that needs a connection imports `get_db_conn()` from `extensions.py` — never calls `sqlite3.connect()` directly.

### 5.1 Core Data Tables

#### `townland`
Canonical townland reference — one row per Coolattin Estate townland (152 rows). The `name` column holds the canonical UPPERCASE English name; `entity_id` is a UUID surrogate used by cross-reference and provenance tables.

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-increment surrogate key |
| `entity_id` | TEXT | UUID surrogate key (v4) |
| `name` | TEXT NOT NULL | Canonical UPPERCASE English name |
| `qualifier` | TEXT | Locational qualifier: UPPER / LOWER / NORTH / SOUTH |
| `logainm_id` | TEXT | logainm.ie place identifier |
| `name_gaelic` | TEXT | Gaelic name |
| `barony` | TEXT | Barony from VRTI |
| `civil_parish` | TEXT | Civil parish from VRTI / townlands.ie |
| `electoral_division` | TEXT | Electoral division |
| `td_id` | TEXT | Townlands.ie numeric identifier |
| `guid` | TEXT | Townlands.ie GUID |
| `county` | TEXT | County (always Wicklow) |
| `centroid_lat` | REAL | Latitude of centroid |
| `centroid_lon` | REAL | Longitude of centroid |
| `wkt_geometry` | TEXT | WKT polygon from VRTI |
| `area_sqm` | REAL | Area in square metres from GeoJSON |
| `kg_uri` | TEXT | VRTI URI (`http://virtualtreasury.ie/...`) |
| `vrti_id` | TEXT | VRTI numeric identifier |
| `osm_id` | TEXT | OpenStreetMap ID |
| `osi_id` | TEXT | Ordnance Survey Ireland ID |
| `images_json` | TEXT | JSON array of image URLs from VRTI |
| `links_json` | TEXT | JSON array of external URLs |
| `geometry_flag` | TEXT | Quality flags from geometry validation |
| `source` | TEXT | Ingest source (`json` / `kg` / `reference`) |
| `created_at` | TEXT | ISO timestamp of row creation |
| `updated_at` | TEXT | ISO timestamp of last update |

Note: `name` has no UNIQUE constraint, allowing same-named townlands in different baronies to coexist as distinct rows identified by `entity_id`.

#### `townland_xref`
Cross-reference table. Maps `(source, source_record_id)` pairs to the canonical `entity_id`. Allows one estate townland to map to multiple external reference systems.

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Surrogate key |
| `entity_id` | TEXT NOT NULL | UUID of the canonical townland |
| `source` | TEXT NOT NULL | Source system (`geojson` / `kg` / `reference` / `manual`) |
| `source_record_id` | TEXT NOT NULL | TD_ID, kg_uri, townlands.ie URL, etc. |
| `confidence` | REAL | Match confidence 0..1 |
| `match_method` | TEXT | `exact_id` / `name_geo` / `manual` |
| `created_at` | TEXT | ISO timestamp |

#### `census_record`
Population data per townland × year. Covers both estate surveys (1827–1868) and national censuses (1841–1891).

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Surrogate key |
| `townland_id` | INTEGER FK | References `townland.id` |
| `year` | INTEGER | Census year |
| `total_population` | INTEGER | Total population |
| `males` | INTEGER | Male count (national census years only) |
| `females` | INTEGER | Female count (national census years only) |
| `inhabited_houses` | INTEGER | Inhabited houses (national census years only) |
| `uninhabited_houses` | INTEGER | Uninhabited houses (national census years only) |
| `source` | TEXT | `vrti` / `estate_survey` / `seed_csv` |

#### `clearances_record`
Estate evictions per townland × year (1847–1856).

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Surrogate key |
| `townland_id` | INTEGER FK | References `townland.id` |
| `year` | INTEGER | Year of eviction |
| `count` | INTEGER | Number of evictions |

#### `refresh_state`
Dataset freshness tracking. One row per dataset.

| Column | Type | Description |
|---|---|---|
| `dataset` | TEXT PK | Dataset name (`census`, `townlands`) |
| `last_refreshed` | TEXT | ISO timestamp of last successful ingest |
| `record_count` | INTEGER | Number of records ingested |

#### `field_provenance`
Field-level source tracking. Records which source contributed each field value, replacing order-dependent COALESCE upserts.

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Surrogate key |
| `entity_id` | TEXT NOT NULL | UUID of the townland this field belongs to |
| `field_name` | TEXT NOT NULL | Column name |
| `field_value` | TEXT | The contributed value |
| `source` | TEXT NOT NULL | Source system that provided this value |
| `source_record_id` | TEXT | Source record identifier |
| `rule` | TEXT | Survivorship rule applied (e.g. `kg_authoritative` / `first_non_null`) |
| `created_at` | TEXT | ISO timestamp |

### 5.2 Runtime-Seeded Tables

These tables are populated on first Ask request via `_ensure_*_seeded()` functions in `ask_service.py`.

#### `unified_record`
13,707 individual estate person records, seeded from `unified_processed.csv`.

Key columns: `record_id`, `canonical_name`, `forename`, `surname`, `townland_norm`, `year`, `age`, `gender`, `ship_name`, `destination`, `has_emigration_record` (BOOLEAN), `has_eviction_record` (BOOLEAN), `has_tenancy_record` (BOOLEAN), `is_widow`, `is_canada_destination`, `children_count`, `family_size_estimate`, `holding_acres`, `family_key`.

#### `heritage_feature`
NMS archaeological and heritage monument data, seeded from GeoJSON files.

Key columns: `id`, `name`, `feature_type` (holy_well / monument / asi), `lat`, `lon`, `description`, `townland_norm`.

### 5.3 Ask Pipeline Tables

#### `ask_query_memory`
Approved question→SQL pairs (thumbs-up feedback). Used for direct memory reuse (Fast Lane 3) and embedding retrieval (Fast Lane 4).

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Surrogate key |
| `question` | TEXT | Original user question |
| `sql` | TEXT | Approved SQL query |
| `question_norm` | TEXT | Normalised question for matching |
| `tags` | TEXT | JSON array of topic tags |
| `created_at` | TEXT | ISO timestamp |
| `approved_by` | TEXT | Source of approval (`thumbs_up`) |

#### `ask_query_feedback`
All feedback submissions (both thumbs-up and thumbs-down) for review and audit.

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Surrogate key |
| `question` | TEXT | Original question |
| `sql` | TEXT | SQL that was executed |
| `feedback` | TEXT | `positive` / `negative` |
| `session_id` | TEXT | Browser session identifier |
| `created_at` | TEXT | ISO timestamp |

### 5.4 Entity Resolution Tables

Four tables form the workhouse entity resolution subsystem. They preserve source-level evidence and keep uncertain links reviewable rather than silently merging records.

#### `source_mentions`
One row per name occurrence in a source record. Each workhouse row becomes one mention.

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Surrogate key |
| `source_table` | TEXT NOT NULL | Source dataset (`workhouse`) |
| `source_record_id` | TEXT UNIQUE | Row identifier in the source |
| `raw_name` | TEXT | Name as it appears in the source |
| `normalised_name` | TEXT | Full normalised name (uppercase, abbreviations expanded) |
| `forename` | TEXT | Parsed forename |
| `surname` | TEXT | Parsed surname |
| `phonetic_forename` | TEXT | Metaphone encoding of forename |
| `phonetic_surname` | TEXT | Metaphone encoding of surname |
| `raw_place` | TEXT | Place name as it appears in the source |
| `normalised_place` | TEXT | Normalised place name |
| `canonical_townland_id` | INTEGER FK | References `townland.id` |
| `event_year` | INTEGER | Year of the event |
| `age` | INTEGER | Age at time of event |
| `inferred_birth_year` | INTEGER | Derived from event_year − age |
| `occupation` | TEXT | Occupation if recorded |
| `household_fields` | TEXT | Additional household context |
| `source_payload_json` | TEXT | Full source record as JSON |
| `created_at` | TEXT | ISO timestamp |
| `updated_at` | TEXT | ISO timestamp |

#### `entity_resolution_candidates`
Scored candidate links: mention → unified_record. Up to 25 per mention.

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Surrogate key |
| `mention_id` | INTEGER FK | References `source_mentions.id` |
| `candidate_source_table` | TEXT | Target table (`unified_record`) |
| `candidate_record_id` | TEXT | Target record identifier |
| `candidate_name` | TEXT | Name of the candidate record |
| `candidate_place` | TEXT | Place of the candidate record |
| `candidate_year` | INTEGER | Year of the candidate record |
| `score` | REAL | Composite score (0.0–1.0, normalised over 60 pts) |
| `label` | TEXT | `CONFIRMED_MATCH` / `POSSIBLE_MATCH` / `WEAK_CANDIDATE` / `NO_MATCH` |
| `evidence_json` | TEXT | JSON list of signals that support the match |
| `conflicts_json` | TEXT | JSON list of signals where evidence contradicts |
| `missing_evidence_json` | TEXT | JSON list of signals that could not be evaluated |
| `review_required` | INTEGER | 1 if flagged for human review |

#### `workhouse_unified_links`
Final accepted workhouse→estate record links (CONFIRMED_MATCH and above-threshold decisions).

#### `entity_resolution_decisions`
Full audit trail of human review decisions on candidates.

#### `match_review`
Human review queue for borderline candidates. Managed by `match_review_repository.py`.

### 5.5 GraphRAG Tables

These tables are built by `scripts/build_graph.py` and loaded at runtime into a NetworkX in-process graph.

#### `graph_nodes`
49,081 nodes representing people, townlands, events, and entities.

| Column | Type | Description |
|---|---|---|
| `node_id` | TEXT PK | Unique node identifier |
| `label` | TEXT | Node type (`Person`, `Townland`, `Event`, `Ship`) |
| `name` | TEXT | Display name of the node |
| `props` | TEXT | JSON blob of node properties |
| `community` | TEXT | Community/cluster label from graph partitioning |
| `embedding` | BLOB | 1024-dim BGE-large-en-v1.5 float32 embedding (28,078 nodes have this) |

#### `graph_edges`
64,308 directed edges representing relationships between nodes. Primary key is composite `(src, dst, rel_type)`.

| Column | Type | Description |
|---|---|---|
| `src` | TEXT FK | Source node_id |
| `dst` | TEXT FK | Destination node_id |
| `rel_type` | TEXT | Relationship type (`EMIGRATED_FROM`, `LIVES_IN`, `EVICTED_FROM`, `SAME_AS`, `BELONGS_TO`) |
| `props` | TEXT | JSON blob of edge properties |

---

## 6. Application Bootstrap Sequence

### 6.1 Entry point (`app.py`)

```python
from create_app import create_app
app = create_app()
app.run(host="0.0.0.0", port=5001, debug=True)
```

In production (Gunicorn): `gunicorn "create_app:create_app()" --bind 0.0.0.0:8000`

### 6.2 Application factory (`create_app.py`)

```
create_app()
  ├─ logging.basicConfig()
  ├─ ActiveConfig loaded from config.py (FLASK_ENV → DevelopmentConfig / ProductionConfig)
  ├─ extensions.init_db(app) → ensure_schema() → creates all tables if not exist
  ├─ Flask-Limiter initialised (rate limiting middleware)
  ├─ Register blueprints:
  │    main_bp    →  /
  │    ask_bp     →  /api/ask
  │    census_bp  →  /api/census
  │    unified_bp →  /api/unified
  │    map_bp     →  /api/map
  │    townlands_bp→ /api/townlands
  │    exports_bp →  /api/exports
  │    kg_bp      →  /api/kg  (+ /kg-explore page)
  └─ return app
```

### 6.3 Database initialisation (`extensions.py`)

`ensure_schema()` runs on every startup. It:
1. Creates all tables that don't exist (idempotent `CREATE TABLE IF NOT EXISTS`)
2. Runs migrations: detects missing columns and adds them with `ALTER TABLE`
3. Sets `PRAGMA journal_mode=WAL` and `PRAGMA foreign_keys=ON`
4. Returns a `Row`-factory connection so rows are accessible as named tuples

`get_db_conn()` returns the singleton connection (one per process, thread-safe via WAL). Any module that needs DB access imports this function — never `sqlite3.connect()`.

---

## 7. Flask Blueprint Layer — API Reference

### 7.1 `main.py` — Page routes

| Route | Method | Response | Description |
|---|---|---|---|
| `/` | GET | HTML | Home page — choropleth map |
| `/census` | GET | HTML | Census explorer page |
| `/ask` | GET | HTML | Natural-language Q&A page |
| `/analytics` | GET | HTML | Analytics dashboard page |
| `/heritage` | GET | HTML | Heritage landscape page |
| `/about` | GET | HTML | About / project info page |
| `/info` | GET | HTML | Technical info page |
| `/kg-explore` | GET | HTML | KG compare page (served by `kg_explore.py`) |

### 7.2 `ask.py` — Natural-language Q&A

| Route | Method | Description |
|---|---|---|
| `POST /api/ask/query` | POST | SSE stream — main Ask endpoint. Body: `{question, townland_hint, show_sql}` |
| `POST /api/ask/feedback` | POST | Save thumbs-up / thumbs-down. Body: `{question, sql, feedback}` |
| `GET /api/ask/llm-status` | GET | LLM provider health check. Returns `{available, provider, model}` |
| `GET /api/ask/pdf/<filename>` | GET | Download generated PDF report |
| `GET /api/ask/audit-log` | GET | Admin-only (ADMIN_API_KEY required). Returns recent Ask audit log |

**SSE streaming protocol:** `POST /api/ask/query` returns `Content-Type: text/event-stream`. Each stage emits a JSON event line:
```
data: {"type": "progress", "stage": "classifying_intent", "status": "completed", "detail": "ANALYTICAL", "duration_ms": 12}
```
Final event has `"type": "result"` with the full answer payload.

### 7.3 `census.py` — Census data

| Route | Method | Description |
|---|---|---|
| `GET /api/census/townlands` | GET | All census data for all townlands (used by census page slider) |
| `GET /api/census/summary` | GET | Estate-wide totals per year |
| `GET /api/census/townland/<name>` | GET | Census data for one townland by name |
| `POST /api/census/refresh` | POST | Trigger VRTI data refresh (admin) |

### 7.4 `unified.py` — Estate people records

| Route | Method | Description |
|---|---|---|
| `GET /api/unified/records` | GET | Paginated search across unified_record. Params: `q`, `townland`, `page`, `per_page` |
| `GET /api/unified/stats` | GET | Dataset statistics (record counts by type) |
| `GET /api/unified/townlands` | GET | List of townlands with record counts |
| `GET /api/unified/surnames` | GET | Surname frequency table |
| `GET /api/unified/person/<id>` | GET | Person detail — enriched with workhouse links |

### 7.5 `townlands.py` — Townland reference data

| Route | Method | Description |
|---|---|---|
| `GET /api/townlands` | GET | All townlands (name, id, parish, barony) |
| `GET /api/townlands/<name>/detail` | GET | Single townland detail: census + clearances + VRTI |
| `GET /api/townlands/geojson` | GET | Full GeoJSON for map rendering |

### 7.6 `map_config.py` — Map configuration

| Route | Method | Description |
|---|---|---|
| `GET /api/map/layers` | GET | Available data layers (population, evictions, emigrations) |
| `GET /api/map/config` | GET | Map tile and style configuration |

### 7.7 `exports.py` — Data export

| Route | Method | Description |
|---|---|---|
| `GET /api/exports/census` | GET | Download census data as Excel (.xlsx) |
| `POST /api/exports/regenerate` | POST | Regenerate the Excel export (admin) |

### 7.8 `kg_explore.py` — KG comparison

| Route | Method | Description |
|---|---|---|
| `GET /kg-explore` | GET | HTML — KG explore page |
| `GET /api/kg/graph` | GET | D3.js force graph JSON (152 townland nodes + hierarchy edges) |
| `GET /api/kg/scenarios` | GET | 4 canned comparison scenarios |
| `POST /api/kg/compare` | POST | Execute both SQLite and GraphDB SPARQL, return side-by-side |

---

## 8. Service Layer — Business Logic

All business logic lives in `backend/services/`. Route handlers call service functions and return the result. Services call repository functions (in `backend/repositories/`) for all SQL.

| Service | File | Responsibility |
|---|---|---|
| Ask pipeline | `ask_service.py` (10,192 lines) | 7-phase orchestrated NL pipeline, SSE streaming, PDF export |
| Semantic layer | `semantic_layer.py` (1,185 lines) | Slot-fill compiler, deterministic SQL/SPARQL generator |
| Intent router | `intent_router.py` | ANALYTICAL/RELATIONAL/COMPARATIVE/FALLBACK classification |
| Subgraph engine | `subgraph_engine.py` | VRTI SPARQL + GraphDB k-hop KG traversal |
| GraphRAG engine | `graphrag.py` (573 lines) | In-process property graph — NetworkX + BGE vector search |
| Embedding index | `embedding_index.py` | Hybrid TF-IDF + dense retrieval, RRF fusion |
| Identity resolver | `identity_resolver.py` | Three-layer Mention/Person/Factoid model |
| Voyage embeddings | `voyage_embeddings.py` | Cohere Embed v3 compatible via `voyageai.Client` |
| Local embeddings | `local_embeddings.py` | BAAI/bge-large-en-v1.5 via SentenceTransformers |
| pgvector backend | `ask_pgvector.py` | Optional PostgreSQL+pgvector retrieval |
| Entity resolver | `entity_resolver.py` | Entity resolution utilities |
| Workhouse ER | `workhouse_entity_resolution.py` | Workhouse ↔ unified record matching pipeline |
| Census service | `census_service.py` | Census data retrieval and aggregation |
| Export service | `export_service.py` | Excel file generation via openpyxl |
| Map service | `map_service.py` | Map layer data preparation |
| Refresh service | `refresh_service.py` | VRTI staleness check and background refresh |
| Townland service | `townland_service.py` | Townland name normalisation and lookup |
| Unified service | `unified_service.py` | Person record search and enrichment |
| Workhouse service | `workhouse_service.py` | Excel sheet loader for workhouse data |
| KG service | `kg_service.py` | GraphDB and VRTI SPARQL clients |
| Retrieval chunks | `retrieval_chunks.py` | Chunk builders for retrieval corpus |
| Ask eval | `ask_eval.py` | Evaluation harness — 75-question benchmark |

---

## 9. Analytics Modules

Analytics dashboards are pluggable — each dataset has its own self-contained module. Adding a new analytics view means creating one file in `analytics/`; nothing else changes.

### 9.1 Module Protocol (`analytics/base.py`)

Each module must implement the `AnalyticsModule` protocol:
```python
class AnalyticsModule(Protocol):
    name: str           # display name
    slug: str           # URL slug
    description: str    # short description
    def get_kpis(self) -> list[KPI]: ...
    def get_charts(self) -> list[Chart]: ...
```

### 9.2 Registered Modules (`analytics/registry.py`)

| Module | File | Dataset | KPIs / Charts |
|---|---|---|---|
| Emigrations | `emigrations.py` | `unified_record` (emigration records) | Total emigrants, peak year, top destinations, by year chart |
| Evictions | `evictions.py` | `clearances_record` | Total evictions, peak year, most-cleared townlands, by year chart |
| Tenancies | `tenancies.py` | `unified_record` (tenancy records) | Total tenants, avg holding, widow proportion, by townland |
| Unified dataset | `unified.py` | `unified_record` (all) | Record type breakdown, surname distribution, gender split |
| Workhouse links | `workhouse.py` | `workhouse_unified_links` | Confirmed matches, match rate, confidence distribution |
| Townland geography | `townland_geo.py` | `townland` + `census_record` | Size distribution, population density, parish breakdown |

---

## 10. Frontend Architecture

### 10.1 Templating

All pages use Jinja2 templates in `frontend/templates/`. `base.html` defines the shared layout (navbar, footer, CSS/JS imports). Each page extends `base.html` with a `{% block content %}`.

### 10.2 JavaScript — One file per page

| File | Page | Key features |
|---|---|---|
| `main.js` | Home / Map | Leaflet choropleth, GeoJSON loading, data layer switching |
| `map.js` | (shared map utilities) | Shared Leaflet helpers, polygon rendering |
| `census.js` | Census | Year slider, per-townland population chart, sidebar |
| `ask.js` | Ask | SSE EventSource, progressive rendering, SQL display, chart rendering, PDF download |
| `analytics.js` | Analytics | Module tab switching, KPI card rendering, Chart.js charts |
| `heritage.js` | Heritage | Leaflet monument overlay, filter panel |
| `kg_explore.js` | KG Explore | D3 force graph, SQL vs SPARQL comparison panel |
| `i18n.js` | All pages | English/Irish language toggle |

### 10.3 Static Data Files (`frontend/static/data/`)

| File | Used by |
|---|---|
| `townlands.json` | Map page — polygon boundaries |
| `holywells_wicklow.geojson` | Heritage page — holy well locations |
| `monuments_wicklow.geojson` | Heritage page — national monuments |
| `asi_wicklow.geojson` | Heritage page — architectural survey |
| `community_summaries.json` | Subgraph engine — pre-computed townland blurbs |

### 10.4 The Ask Page SSE Consumer (`ask.js`)

The Ask page uses the browser's native `EventSource` API to consume the SSE stream from `POST /api/ask/query`. It handles each event type:

- `progress` events: update a live status bar showing which pipeline stage is running
- `result` event: render the answer text, data table, SQL block (if `show_sql=true`), Chart.js visualisation, PDF download link, related insights panel, fusion/discrepancy notes

The townland dropdown is pre-loaded from `/api/townlands` on page load (no per-keystroke round-trips). GeoJSON and unified data are loaded in parallel on the map page (parallel `Promise.all`) to halve the initial load time.

---

## 11. The Ask Pipeline — Seven-Phase Orchestrator

The Ask pipeline is the system's most complex component. Entry point: `_orchestrated_pipeline_stream()` in `ask_service.py`. Enabled when `ASK_USE_NEW_PIPELINE=true` (default).

A question flows through three layers before the seven phases:

```
[PRE-FLIGHT]
  _resolve_townland_context()   → {name_norm, sql_id, kg_uri, warning}
  _analyse_question()           → {primary_intent, output_mode, scope, year, surname, ...}
  _question_data_coverage_warnings()

[FOUR FAST LANES]
  Lane 1: Rule-based slot-fill     (confidence ≥ 0.80 → SQL compiled, 0 LLM)
  Lane 2: Verified template        (81 pre-written templates, 15 in VERIFIED_ANALYSIS_TEMPLATE_IDS)
  Lane 3: Direct memory reuse      (token_sort_ratio + cosine ≥ 0.55)
  Lane 4: Embedding template       (TF-IDF + RRF cosine ≥ 0.68)

[SEVEN PHASES]
  Phase 1: Identity resolution     (identity_resolver.py)
  Phase 2: Semantic layer          (semantic_layer.py)
  Phase 3: Subgraph engine         (subgraph_engine.py)
  Phase 4: Hybrid embedding        (embedding_index.py)
  Phase 5: Intent classification   (intent_router.py)
  Phase 6: Fusion & reconciliation (ask_service.py)
  Phase 7: Multi-model synthesis   (ask_service.py)
```

### 11.1 Pre-flight (synchronous, < 5 ms, no LLM)

**`_resolve_townland_context(question, townland_hint)`**
- Loads townland catalogue from SQLite (cached 10 min)
- Tokenises question, removes stopwords
- Exact name match → fuzzy match (token_set_ratio ≥ 80) → hint override
- Returns: `{name_norm, sql_id, kg_uri, warning}`

**`_analyse_question(question, townland_hint)`**
- Year extraction: regex `\b(18[0-9]{2}|19[0-2][0-9])\b`
- Surname detection: 6 regex patterns
- Radius detection: `\b(\d{1,3})\s*km\b`
- Keyword matching against 14 metric keyword sets (METRIC_REGISTRY)
- Returns: `{primary_intent, output_mode, group_by, scope, preferred_tables, year, surname, ...}`

**`_question_data_coverage_warnings(question)`**
- Checks question for known data gaps
- Example: questions mentioning 1821 return "Census data begins at 1841; 1827 is the earliest estate survey"
- Returns empty list or list of warning strings

---

## 12. Four Fast Lanes — Pre-Classification Short-Circuits

Fast lanes run in order. The first match terminates the pre-classification section and the pipeline jumps directly to SQL execution (skipping intent classification and all slow paths). All fast lane hits emit an SSE `schema_sql` or `contacting_llm` event.

### Lane 1 — Rule-based Slot-Fill

```
semantic_layer.try_rule_based_fill(question, analysis, townland_resolution)
  • Match question against 14 metric keyword sets in METRIC_REGISTRY
  • Compute confidence based on keyword hit density
  • If confidence ≥ 0.80:
      compile deterministic SQL from the slot-fill
      → SQL execution (no LLM called)
```

**Key properties:**
- Zero LLM calls
- Latency: < 5 ms
- Covers ~70% of all analytical questions in the 75-question evaluation

### Lane 2 — Verified Template Match

```
_try_verified_analysis(question, townland_norm, analysis)
  • Score 81 pre-written SQL templates by:
      required_keywords (must all be present)
      optional_keywords (bonus scoring)
  • Template ID in VERIFIED_ANALYSIS_TEMPLATE_IDS + score above threshold?
      → Use pre-written SQL directly
  • Confidence = 1.0 (highest possible)
```

`VERIFIED_ANALYSIS_TEMPLATE_IDS` contains 15 high-confidence templates. Of these, 7 also emit a Chart.js chart spec:

| Template ID | Chart type |
|---|---|
| `tenant_land_gender_average` | bar |
| `most_populous_1841_vs_1861` | bar |
| `population_trend_1841_1861` | line |
| `holy_well_population_relationship` | bar |
| `ring_fort_population_relationship` | bar |
| `canada_emigration_peak_period` | line |
| `smallest_townland_plots` | bar |

### Lane 3 — Direct Memory Reuse

```
_find_similar_approved_queries(question, analysis, townland_norm)
  • Query ask_query_memory (TTL 60 s in-process cache)
  • For each approved memory row:
      score = max(token_sort_ratio(question, memory.question_norm),
                  cosine_similarity(question_tfidf, memory_tfidf))
  • If score ≥ 0.55:
      → Reuse stored SQL directly (no LLM, no template matching)
```

Over time, thumbs-up feedback builds a validated query library that the pipeline reuses without any computation.

### Lane 4 — Embedding Template Retrieval

```
embedding_index._phase4_retrieve(question, analysis, townland_norm)
  • TF-IDF unigram+bigram cosine similarity over all templates
  • RRF (Reciprocal Rank Fusion) over dense + sparse ranked lists
  • Top hit: cosine ≥ 0.68 AND required_keywords all present?
      → Use template SQL directly
```

---

## 13. Intent Classification and Route Dispatch

If no fast lane fires, `classify_intent(question, analysis, slot_fill)` in `intent_router.py` classifies the question. Priority order — first match wins:

### Classification Priority

**1. COMPARATIVE** — any of these keywords present:
> `compare`, `compared to`, `compared with`, `versus`, `vs`, `difference between`, `contrast`, `relative to`, `how does`, `how did`, `better than`, `worse than`, `more than`, `less than`, `higher than`, `lower than`, `against`

**2. RELATIONAL** — geography intent from `_analyse_question`, OR any keyword from:
- *Relational*: `related to`, `connected to`, `link between`, `in the same parish`, `same barony`, `part of`, `neighbouring`, `adjacent to`, `bordering`, `relationship between`, `linked to`
- *Hierarchy*: `which parish`, `what parish`, `civil parish`, `in the barony`, `townlands in`, `where is`, `where does`, `located in`, `situated in`, `falls within`
- *Heritage*: `heritage`, `archaeological`, `monument`, `ring fort`, `holy well`, `history of`, `tell me about`, `describe`, `historically`, `fortification`, `earthwork`
- *Sensemaking*: `overview`, `about the estate`, `about coolattin`, `describe the estate`, `what kind of`, `background`, `summary of`, `general context`
- **Core Rule 1 Exception**: if *only* heritage/sensemaking keywords triggered (no relational/hierarchy/geography signal) AND `output_mode` is `count`/`aggregate` AND any analytical keyword present → falls through to **ANALYTICAL** instead

**3. ANALYTICAL** — any of:
- `primary_intent` in `{population, eviction, emigration, tenancy}`
- `output_mode` in `{count, aggregate, trend}`
- Any analytical keyword: `how many`, `how much`, `total`, `count of`, `number of`, `average`, `mean`, `proportion`, `percent`, `percentage`, `per year`, `by year`, `trend`, `over time`, `distribution`, `breakdown`, `most`, `least`, `highest`, `lowest`, `maximum`, `minimum`, `sum of`, `rate`, `ratio`
- `slot_fill is not None` (semantic layer found any candidate)

**4. FALLBACK** — default when nothing above matched

### Route Dispatch

#### ANALYTICAL route
```
semantic_layer.build_slot_fill_prompt(question, analysis)
  → LLM slot-fill (only if rule-fill confidence < 0.80)
  → parse_slot_fill() → SlotFill{metric, dimensions, filters, group_mode, confidence}
  → if confidence ≥ 0.70: compile_sql(slot_fill) → deterministic SQL
  → if confidence < 0.60: fall through to FALLBACK
SSE stages: slot_filling → schema_sql → framing_query → querying_database
```

#### RELATIONAL / HERITAGE route
```
subgraph_engine.retrieve_subgraph(question, entity_uri, k=2)
  → VRTI SPARQL: townland → parish → barony → county hierarchy
  → get_sibling_townlands(), get_external_links()
  → graphdb_sparql.get_entity_neighborhood(name, k=2, max_nodes=40)
  → Returns: qualitative context + place graph

graphrag.retrieve_subgraph(question, entity_hints, k_hops=2)
  → vector_seed (top-8 BGE ANN) → BFS traversal → linearised triples
  → community summary blurb (from data/seed/community_summaries.json)

Rule: counts/aggregates always from SQL, never from KG
SSE stage: querying_subgraph
```

#### COMPARATIVE route
```
[ANALYTICAL lane] ‖ [RELATIONAL lane]   (run in parallel)
Phase 6 fusion reconciles both results, detects discrepancies
```

#### FALLBACK route
```
_try_verified_analysis()          (score 81 templates again)
_phase4_retrieve()                (embedding retrieval again)
_find_similar_approved_queries()  (memory lookup again)
_generate_sql()                   (LLM free-form SQL with annotated schema)
  → SYSTEM: full schema, sampled categories, approved memory as few-shot examples
  → Must start with SELECT or WITH
SSE stage: contacting_llm
```

---

## 14. Semantic Layer — Deterministic SQL Compiler

`semantic_layer.py` (1,185 lines) is the core of the ANALYTICAL path. It follows a strict three-layer flow:

### Layer 1 — Rule-Based Slot-Fill (`try_rule_based_fill`)

Matches the question against 14 defined metrics in `METRIC_REGISTRY` using keyword sets. No LLM involved.

```
METRIC_REGISTRY = {
  "emigration_count":       {"keywords": ["emigrat"], "table": "unified_record", ...},
  "canada_emigration_count":{"keywords": ["canada", "quebec", "montreal"], ...},
  "eviction_event_count":   {"keywords": ["evict", "clearance", "clear"], ...},
  "evicted_person_count":   {"keywords": ["evict", "clearance", "cleared person"], ...},
  "population":             {"keywords": ["populat", "people", "inhabitants"], ...},
  "population_change":      {"keywords": ["populat", "change", "decline", "growth"], ...},
  "uninhabited_houses":     {"keywords": ["uninhabited", "empty house"], ...},
  "tenancy_count":          {"keywords": ["tenant", "tenancy", "holding"], ...},
  "avg_holding_acres":      {"keywords": ["average", "holding", "acres"], ...},
  "widow_count":            {"keywords": ["widow"], ...},
  "person_count":           {"keywords": ["person", "people", "individual"], ...},
  "townland_count":         {"keywords": ["townland"], ...},
  "parish_count":           {"keywords": ["parish"], ...},
  "townland_attribute":     {"keywords": ["parish", "barony", "county", "where is"], ...},
  ... (14 total metrics)
}
```

Filters extracted: `townland_norm`, `year`, `year_from`, `year_to`, `gender`, `destination`, `surname`.

### Layer 2 — LLM Slot-Fill (`build_slot_fill_prompt`)

If rule-fill confidence < 0.80, send the LLM a tight JSON-only prompt:

```json
{
  "metric": "emigration_count",
  "dimensions": ["year"],
  "filters": {"townland_norm": "AGHOWLE LOWER", "year": 1852},
  "group_mode": "none",
  "confidence": 0.95
}
```

The LLM returns only a structured JSON blob — never SQL. This is the key separation: the LLM classifies the question into a typed vocabulary; the compiler produces SQL.

### Layer 3 — Deterministic SQL Compiler (`compile_sql`)

```python
SlotFill(metric="emigration_count",
         filters={"townland_norm": "AGHOWLE LOWER", "year": 1852})
↓
SELECT COUNT(DISTINCT record_id) AS emigration_count
FROM unified_record
WHERE has_emigration_record = 1
  AND townland_norm = 'AGHOWLE LOWER'
  AND year = 1852
```

The compiler is a pure function: same `SlotFill` always produces the same SQL. It is guaranteed to produce valid SQLite because it only uses the declared metric/dimension/filter vocabulary.

### SPARQL Compiler (`compile_sparql`)

`compile_sparql(slot_fill)` generates an equivalent SPARQL query for the local GraphDB `co:` ontology. Used for the RQ6 SQL-vs-SPARQL comparison (D8). Returns `None` when no KG equivalent exists.

---

## 15. Subgraph Engine — Knowledge Graph Traversal

`subgraph_engine.py` handles RELATIONAL and COMPARATIVE questions. It queries two KG sources in sequence:

### 15.1 VRTI SPARQL Queries

```sparql
SELECT ?parish ?barony ?county ?centroid ?wkt WHERE {
  ?tl rdfs:label "Kilcommon"@en ;
      co:civilParish ?parish ;
      co:barony ?barony ;
      co:county ?county ;
      geo:hasGeometry/geo:asWKT ?wkt .
}
```

Functions called per question:
- `get_townland_hierarchy(name)` — parish, barony, county chain
- `get_sibling_townlands(parish_uri)` — all townlands in the same parish
- `get_external_links(kg_uri)` — links to logainm.ie, townlands.ie, OSM

### 15.2 GraphDB Neighbourhood Queries

```
graphdb_sparql.get_entity_neighborhood(name, k=2, max_nodes=40)
  → SPARQL: k=2 hop neighbourhood from the entity node
  → Returns: up to 40 nodes with types and labels
  → Timeout: 15 s (graceful skip on failure)
```

### 15.3 Community Summaries

For "history of / tell me about" questions, the engine pulls a pre-computed prose blurb from `data/seed/community_summaries.json` keyed by townland name. These blurbs are written from the estate records and provide qualitative context that the SQL path cannot produce.

### 15.4 Key Invariant

The subgraph engine provides **qualitative context** only. Count and aggregate answers always come from the SQL path. The linearised subgraph is passed to the LLM to *read*, never to answer numerical questions.

---

## 16. In-Process GraphRAG Engine

`graphrag.py` (573 lines) is the in-process property graph engine. It requires no external graph server — the graph is stored in `graph_nodes` / `graph_edges` SQLite tables and loaded into a `NetworkX.MultiDiGraph` at startup.

### 16.1 Graph Construction (`scripts/build_graph.py`)

The graph is built by a one-shot script:
1. Reads `unified_record`, `townland`, `census_record`, `clearances_record` from SQLite
2. Creates nodes: `Person` (one per unique person), `Townland` (152), `Event` (emigrations, evictions), `Ship`
3. Creates edges: `EMIGRATED_FROM`, `LIVES_IN`, `EVICTED_FROM`, `SAME_AS`, `BELONGS_TO`
4. Embeds node "passports" (name + type + key properties as text) using BAAI/bge-large-en-v1.5 → 1024-dim float32 vectors
5. Persists nodes to `graph_nodes`, edges to `graph_edges`

**Scale:** 49,081 nodes · 64,308 edges · 28,078 nodes have BGE embeddings

### 16.2 Runtime Loading (`_load_graph`)

On first call to any GraphRAG function, the graph is loaded into a process-lifetime `NetworkX.MultiDiGraph`. Thread-safe via `_graph_lock`. Loading takes ~2–5 s on warm hardware.

### 16.3 Vector Seed (`vector_seed`)

```python
vector_seed(question, top_k=8)
  → Embed question with BAAI/bge-large-en-v1.5 (or Voyage AI on Azure)
  → Cosine ANN over the 28,078 passport_vector embeddings in graph_nodes
  → Returns: list of top-k node_ids (starting seeds for BFS)
```

### 16.4 Subgraph Retrieval (`retrieve_subgraph`)

```python
retrieve_subgraph(question, intent, entity_hints, k_hops=2)
  1. vector_seed(question, top_k=8) → seed node list
  2. Add any nodes from entity_hints (resolved townland/person)
  3. BFS traversal from each seed: follow all edges up to k=2 hops
  4. Prune to GRAPHRAG_MAX_NODES (default: 120) by PageRank score
  5. Linearise: convert subgraph triples to compact text table
  6. Add community summary blurb if available
  7. Return: GraphRAGResult{nodes, edges, linearized_text, provenance_path}
```

Provenance path recorded in the SSE result: `"vector_seed(8) → k-hop BFS → 47 triples"`.

### 16.5 Key Design Rules

- GraphRAG enrichment is **additive only** — SQL aggregates are never modified
- If the graph is empty or unavailable, the pipeline continues as before (enrichment omitted)
- Never raises exceptions — all failure modes degrade gracefully

### 16.6 Comparison Subgraph (`comparison_subgraph`)

For COMPARATIVE intent, `comparison_subgraph(template_id)` retrieves graph-side corroboration for a specific relational template. Returns a `GraphRAGResult` containing edges that connect the two entities being compared.

---

## 17. Hybrid Embedding Retrieval

`embedding_index.py` powers Fast Lane 4 and the FALLBACK template search. It uses a hybrid TF-IDF + dense retrieval approach with RRF (Reciprocal Rank Fusion) to combine signals.

### 17.1 Retrieval Architecture

```
Question text
  │
  ├─ TF-IDF sparse retrieval
  │    • Unigram + bigram vectoriser over all template questions
  │    • Cosine similarity → ranked list L1
  │
  ├─ Dense retrieval (when embeddings available)
  │    • Embed question using active provider (BGE / Voyage / Cohere)
  │    • Cosine ANN over pre-embedded template corpus
  │    • Ranked list L2
  │
  └─ RRF fusion: score(d) = Σ 1/(k + rank_i(d))  [k=60]
       → Combined ranked list
       → Top hit above threshold (0.68) AND required keywords match?
          → Template SQL returned directly
```

### 17.2 Embedding Providers

| Provider | Config value | Model | Dimensions | Notes |
|---|---|---|---|---|
| Local (default) | `local` | BAAI/bge-large-en-v1.5 | 1024 | via SentenceTransformers; excluded from Azure build |
| Voyage AI | `voyage` | voyage-large-2 | 1024 | via `voyageai.Client`; used in Azure production |
| Cohere | `cohere` | embed-english-v3.0 | 1024 | via Cohere API; asymmetric: query=`search_query`, doc=`search_document` |

### 17.3 Optional pgvector Backend (`ask_pgvector.py`)

When `DATABASE_URL` is set to a PostgreSQL connection string, chunk embeddings are persisted in a `pgvector` column. The retrieval backend switches from in-memory cosine to `<=>` operator ANN queries. This is optional — the default SQLite path works without PostgreSQL.

---

## 18. Identity Resolution — Three-Layer Model

`identity_resolver.py` runs after SQL execution for person-name questions. It resolves the three-layer identity model:

### 18.1 The Three Layers

| Layer | Description | Key property |
|---|---|---|
| **Mention** | One immutable row per name occurrence in a source record | Never modified |
| **Person** | Inferred individual linked to one or more Mentions via `SAME_AS` | Confidence score ≥ 0.75 = confirmed |
| **Factoid** | Reified claim (mention, property, value, source) | Preserves contradictory records without hard-merging |

### 18.2 Scoring Algorithm

1. **Phonetic blocking** — group candidates by `jellyfish.metaphone(surname)` to restrict comparison to plausible matches
2. **Within-block scoring** for each candidate pair:

| Signal | Weight | Rule |
|---|---|---|
| Jaro-Winkler name similarity | 0.40 | `jellyfish.jaro_winkler(name1, name2)` |
| Geographic proximity | +0.20 | Same townland match |
| Geographic proximity | +0.10 | Same civil parish match |
| Temporal plausibility | +0.10 | Birth year gap ≤ 10 years |
| Temporal implausibility | −0.10 | Birth year gap > 30 years |
| Family co-occurrence | +0.15 | Same family_key in the same record |

3. **Thresholds:** score ≥ 0.75 → `SAME_AS` (confirmed); 0.50–0.74 → candidate

### 18.3 Benefit in the Answer

Instead of silently collapsing multiple people with the same name, the answer can say: **"3 distinct individuals called John Murphy were found — their records are shown separately."** This is provenance-annotated in the SSE result under `identity_disambiguation`.

---

## 19. Multi-Model LLM Synthesis Chain

The final phase of every Ask response uses a chain of LLM providers. Each provider is tried in order; failure at any stage silently falls to the next. The chain never exposes a raw error to the user.

### 19.1 Provider Priority

```
[1] Claude (Anthropic)
    Model: claude-3-5-haiku-20241022 (or configurable via ASK_SYNTHESIS_MODEL)
    Key:   ANTHROPIC_API_KEY
    Guard: LLM_ALLOW_PAID=true required

[2] Grok (xAI)
    Model: grok-3-mini-beta
    Key:   GROK_API_KEY
    Guard: LLM_ALLOW_PAID=true required

[3] OpenRouter
    Model: openai/gpt-oss-20b:free (configurable via OPENROUTER_MODEL)
    Key:   OPENROUTER_API_KEY
    Timeout: 80 s connect + 10 s

[4] Ollama (local)
    URL:   http://localhost:11434
    Model: auto-detected (first available model)
    No key required — fully offline fallback
```

### 19.2 What the Synthesis LLM Receives

```
SYSTEM: "You are a digital historian specialising in 19th century Irish social history.
         You have access to estate records from the Coolattin Estate in County Wicklow.
         Answer based only on the provided data. Do not introduce numbers not present
         in the data. Cite sources when available."

DATA: First 20 rows of SQL result in compact format

VRTI_CONTEXT: Parish, barony, county, centroid from VRTI enrichment

GRAPHRAG_CONTEXT: Linearised subgraph triples (if RELATIONAL or COMPARATIVE)

USER: Original question
```

### 19.3 Answer Validation

The synthesis LLM answer is validated before being included in the SSE result:
- If the LLM introduces a number not present in the SQL rows, the raw SQL result is used instead
- If the LLM response is empty or fails the guardrail, the answer falls back to `actual_answer` (a pre-formatted version of the SQL result)

### 19.4 SQL Safety Guardrail

All SQL — whether from the semantic layer, templates, memory, or the FALLBACK LLM path — passes through `_sanitize_and_validate_sql()`:

```python
FORBIDDEN_SQL = re.compile(
    r'\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|DETACH|'
    r'PRAGMA|REINDEX|VACUUM|TRUNCATE|REPLACE)\b',
    re.IGNORECASE
)
# Raises ValueError if matched
# Must start with SELECT or WITH
```

---

## 20. Workhouse Entity Resolution Subsystem

A dedicated entity-resolution pipeline separate from the Ask pipeline. It links workhouse admission records to unified estate records using deterministic normalisation and multi-signal scoring. No LLM is involved.

### 20.1 Why Separate from Ask

The Ask pipeline retrieves semantically relevant context for natural-language questions. Workhouse matching is a different problem: producing explicit, reviewable candidate identity links with transparent evidence scores. Conflating these two goals would make both worse.

### 20.2 Pipeline Steps

```
Step 1 — Load workhouse data
  workhouse_service.get_workhouse()
  → reads workhouse_data_final.xlsx (two sheets, ~500 rows)

Step 2 — Normalise each mention
  normalise.normalise_person_fields(raw_name)
  1. Unicode NFKD decomposition
  2. Uppercase
  3. Remove editorial annotations ([?], [illegible], (In Lease), (Sic))
  4. Expand abbreviations: JNO→JOHN, WM→WILLIAM, JAS→JAMES, THOS→THOMAS,
     RD→RICHARD, EDWD→EDWARD, SAML→SAMUEL, ELIZH→ELIZABETH, MARGT→MARGARET
  5. Surname variants: MCDONNELL/MACDONNELL→MCDONNELL, OBRIEN/O'BRIEN→OBRIEN
  6. Remove accents
  7. Phonetic encoding: jellyfish.metaphone()

Step 3 — Build unified index
  build_unified_index()
  → all 13,707 unified_record rows with normalised fields
  → first blocking pass: filter by place (electoral_division + townland_norm)

Step 4 — Generate candidates
  candidates.generate_candidates(mention, unified_index)
  → up to 25 ranked candidates per mention using:
     - Exact normalised name match
     - Surname + forename initial match
     - Phonetic surname match
     - Place + name combination
     - Fuzzy full-name similarity (rapidfuzz token_sort_ratio)
     - Year compatibility (±1 year window)

Step 5 — Score candidates (7-signal formula over 60 points)
Step 6 — Assign confidence bands
Step 7 — Persist to 4 SQLite tables
Step 8 — Human review (match_review_repository.py)
```

### 20.3 Scoring Formula

| Signal | Max pts | Rule |
|---|---|---|
| Full name similarity | 10 | ≥90%→10; ≥75%→7; ≥60%→4; else→0 |
| Exact surname | 10 | Exact→10; Metaphone→7; else→0 |
| Forename | 10 | Either missing→5 (neutral); exact→10; ≥80%→7; ≥60%→4; conflict→0 |
| Townland normalisation | 10 | Exact→10; variant→6; else→0 |
| Birth-year alignment | 5 | Gap≤3y→5; ≤8y→3; else→0 |
| Gender | 10 | Both missing→5 (neutral); match→10; mismatch→0 |
| Timeline alignment | 5 | Age-progression consistency |
| **TOTAL** | **60** | Normalised: `raw_points / 60.0 → 0.0–1.0` |

### 20.4 Confidence Bands

| Band | Score | Action |
|---|---|---|
| `CONFIRMED_MATCH` | ≥ 0.75 | Auto-accepted; persisted to `workhouse_unified_links` |
| `POSSIBLE_MATCH` | 0.50 – 0.74 | Flagged for human review |
| `WEAK_CANDIDATE` | < 0.50 | Requires explicit review |
| `NO_MATCH` | — | Hard negative rule triggered or all signals missing |

**Hard negative rules:** impossible age/date conflict, incompatible gender evidence, irreconcilable timeline.

### 20.5 Results

Running `scripts/link_workhouse_records.py` against the full workhouse dataset produces **140 confirmed links** (CONFIRMED_MATCH ≥ 0.75). Results are committed to the seed DB and visible in the unified person detail endpoint (`/api/unified/person/<id>`).

---

## 21. KG Explore Page — SQL vs SPARQL Comparison

`/kg-explore` is the D8 comparison prototype. It runs the same historical query as both a SQL query against the local SQLite database and a SPARQL query against the local GraphDB `co:` ontology, showing results side-by-side with timing.

### 21.1 Endpoints

**`GET /api/kg/graph`** — D3.js force graph data
- Returns 152 townland nodes with geographic hierarchy edges
- Used by the D3 force-directed graph visualisation
- Nodes: `{id, label, parish, barony}` · Edges: `{source, target, type}`

**`GET /api/kg/scenarios`** — 4 canned comparison scenarios:
1. `emigration_count_by_townland` — total emigration counts per townland
2. `eviction_count_by_year` — clearance counts per year
3. `surname_frequency` — top 20 surnames
4. `person_event_detail` — person records with event dates

**`POST /api/kg/compare`** — Execute and compare:
```json
{
  "scenario": "emigration_count_by_townland",
  "custom_sql": null,
  "custom_sparql": null
}
```
Response: `{sql_result, sparql_result, sql_time_ms, sparql_time_ms, discrepancies}`

### 21.2 The co: Ontology

The local GraphDB repository uses the Coolattin ontology namespace `https://coolattin.ie/ontology#` (prefix `co:`). The repository is provisioned at:
- Development: `http://localhost:7200/repositories/coolattin`
- Azure (D8 prototype): `http://51.120.71.162:7200/repositories/coolattin`

**Current status:** The local `co:` repository is provisioned but not populated with data. SPARQL queries return 0/empty (open-world assumption). The SQL-vs-SPARQL comparison framework is architecturally complete; the data loading step (`scripts/rdf_uplift.py`) generates Turtle from `unified_record` rows.

---

## 22. Security Architecture

### 22.1 Flask Configuration

- `FLASK_ENV` defaults to `production` even when unset, so Azure deployments never accidentally run in debug mode
- `SECRET_KEY` loaded from environment; defaults to a random development key
- `ADMIN_API_KEY` guards admin-only endpoints (`/api/ask/audit-log`, `/api/exports/regenerate`, `/api/census/refresh`)

### 22.2 SQL Injection Prevention

All SQL is in `backend/repositories/` using parameterised queries (`?` placeholders). The `_sanitize_and_validate_sql()` function in `ask_service.py` blocks any LLM-generated SQL that contains write operations. No raw SQL is constructed via string concatenation in route handlers.

### 22.3 Rate Limiting

`flask-limiter` is installed and initialised in `create_app.py`. The Ask endpoint is rate-limited to prevent abuse of LLM API calls.

### 22.4 PDF Download Security

PDF filenames are validated to prevent directory traversal. Only files in `exports/ask/` matching the expected pattern are served.

### 22.5 Audit Log

All Ask requests are logged with question text, route taken, LLM provider used, and latency. Accessible via `GET /api/ask/audit-log` with `ADMIN_API_KEY` authentication.

---

## 23. Configuration Reference

All configuration is in `config.py`. Environment variables override defaults.

### 23.1 Config Classes

```python
class Config:                   # Base — applies to all environments
class DevelopmentConfig(Config): DEBUG = True, LOG_LEVEL = "DEBUG"
class ProductionConfig(Config):  DEBUG = False, CENSUS_STALE_AFTER_DAYS = 1
```

`ActiveConfig` is selected by `FLASK_ENV`. Defaults to `ProductionConfig` when `FLASK_ENV` is unset (safe for Azure).

### 23.2 All Config Keys

| Key | Default | Description |
|---|---|---|
| `SECRET_KEY` | Random dev key | Flask session secret |
| `ADMIN_API_KEY` | `""` | Guard for admin endpoints |
| `DATABASE_PATH` | `{project_root}/coolattin.db` | SQLite file location |
| `DATABASE_URL` | `sqlite:///...` | Override for PostgreSQL (pgvector) |
| `VRTI_SPARQL_ENDPOINT` | `https://virtuoso.virtualtreasury.ie/sparql/` | VRTI endpoint |
| `VRTI_REQUEST_TIMEOUT` | `30` | VRTI request timeout (s) |
| `GRAPHDB_SPARQL_ENDPOINT` | `http://localhost:7200/repositories/coolattin` | GraphDB endpoint |
| `GRAPHDB_ENABLED` | `true` | Enable GraphDB queries |
| `GRAPHDB_REQUEST_TIMEOUT` | `15` | GraphDB timeout (s) |
| `GRAPHRAG_ENABLED` | `true` | Enable in-process GraphRAG |
| `GRAPHRAG_VECTOR_TOP_K` | `8` | BGE vector seeds per question |
| `GRAPHRAG_K_HOPS` | `2` | BFS traversal depth |
| `GRAPHRAG_MAX_NODES` | `120` | Max nodes in linearised subgraph |
| `CENSUS_STALE_AFTER_DAYS` | `7` (dev) / `1` (prod) | Census data TTL |
| `TOWNLAND_STALE_AFTER_DAYS` | `30` (dev) / `7` (prod) | Townland data TTL |
| `EMBEDDING_PROVIDER` | `local` | `local` / `cohere` / `voyage` |
| `STATIC_DATA_DIR` | `frontend/static/data/` | Static GeoJSON directory |
| `DATA_SEED_DIR` | `data/seed/` | Seed files directory |
| `EXPORTS_DIR` | `exports/` | Runtime PDF/Excel output |
| `LOG_LEVEL` | `INFO` | Python logging level |

---

## 24. In-Process Caches

All caches are module-level dictionaries or thread-local values inside `ask_service.py`. They are reset on process restart. No external cache server (Redis, Memcached) is used.

| Cache | TTL | Contents |
|---|---|---|
| `_TOWNLAND_CATALOG_CACHE` | 10 min | All canonical townland names from SQLite |
| `_VRTI_PARISH_CACHE` | 60 min per townland | VRTI enrichment per townland |
| `_VRTI_STATUS_CACHE` | 5 min cooldown | VRTI unavailability flag (circuit breaker) |
| `_OPENROUTER_STATUS_CACHE` | 60 s | OpenRouter health check result |
| `_OLLAMA_MODEL_CACHE` | 120 s | Available Ollama models |
| `_PROMPT_SCHEMA_CACHE` | 5 min | Annotated schema descriptor string |
| `_QUERY_MEMORY_CACHE` | 60 s | Approved memory rows from `ask_query_memory` |
| `_SCHEMA_COMPAT_CACHE` | process lifetime | `clearances_record` column name (schema variant) |
| `_UNIFIED_CACHE` | process lifetime | `unified_processed.csv` DataFrame |
| NetworkX graph | process lifetime | `graph_nodes` + `graph_edges` loaded in `graphrag.py` |

---

## 25. SSE Streaming Protocol

`POST /api/ask/query` streams Server-Sent Events. Each event is a JSON object on a `data:` line followed by a blank line.

### 25.1 Event Structure

**Progress events** (emitted as each stage starts/completes):
```json
{
  "type": "progress",
  "stage": "classifying_intent",
  "status": "completed",
  "detail": "ANALYTICAL",
  "duration_ms": 12
}
```

Stage values (in order of emission):
`schema_sql` · `contacting_llm` · `slot_filling` · `classifying_intent` · `framing_query` · `querying_subgraph` · `querying_database` · `querying_vrti_graph` · `querying_graphdb` · `querying_fusion` · `synthesizing_answer` · `preparing_output`

**Result event** (final event, type="result"):
```json
{
  "type": "result",
  "question": "How many emigrants left from Aghowle in 1852?",
  "answer": "47 emigration records from Aghowle Lower in 1852.",
  "llm_rephrased_answer": "In 1852, forty-seven individuals from Aghowle Lower...",
  "columns": ["emigration_count"],
  "rows": [[47]],
  "row_count": 1,
  "sql": "SELECT COUNT(DISTINCT record_id) AS emigration_count FROM unified_record WHERE ...",
  "chart": {
    "type": "bar",
    "labels": ["1852"],
    "datasets": [{"label": "Emigration count", "data": [47]}]
  },
  "vrti_context": {
    "townlands": [{"name": "Aghowle Lower", "parish": "Mullinacuff", ...}],
    "parish_count": 1
  },
  "graphrag_context": {
    "nodes": 23,
    "edges": 41,
    "linearized_text": "...",
    "provenance_path": "vector_seed(8) → k-hop BFS → 41 triples"
  },
  "fusion": {"discrepancy_count": 0, "agreement_count": 1, "fusion_text": "..."},
  "discrepancies": [],
  "warnings": [],
  "pdf_url": "/api/ask/pdf/ask_report_20260617_143022.pdf",
  "availability": {"has_local_data": true, "has_vrti_data": true},
  "related_insights": ["How many families were in Aghowle in 1841?", ...],
  "query_provenance": {
    "strategy": "rule_fill",
    "used_approved_memory": false,
    "direct_memory_reuse": false,
    "execution_mode": "executed_as_generated"
  },
  "llm_meta": {
    "provider": "openrouter",
    "model": "openai/gpt-oss-20b:free",
    "mode": "analytical_semantic"
  }
}
```

### 25.2 `query_provenance.strategy` Values

| Value | What it means |
|---|---|
| `rule_fill` | Fast Lane 1 — deterministic keyword matching, 0 LLM SQL calls |
| `verified_analysis` | Fast Lane 2 — pre-written template SQL |
| `memory_reuse` | Fast Lane 3 — direct reuse of thumbs-up approved SQL |
| `template_embedding` | Fast Lane 4 — TF-IDF + RRF retrieved template |
| `slot_fill_llm` | ANALYTICAL: LLM provided slot-fill JSON; compiler produced SQL |
| `llm_sql` | FALLBACK: LLM generated SQL directly |
| `subgraph` | RELATIONAL: subgraph engine + KG queries |
| `comparative` | COMPARATIVE: both ANALYTICAL + RELATIONAL in parallel |

---

## 26. Evaluation Results

### 26.1 Full Regression — 75 Competency Questions

Run: `python3 -m backend.services.ask_eval --phase graphrag_on`  
Date: 2026-06-10  
Tag: `v1.0-demo-freeze`

| Metric | Value |
|---|---|
| Questions run | 75 |
| Routing accuracy | 89.3% |
| Aggregation correctness | 100.0% |
| SQL execution success | 100.0% |
| Entity label accuracy | 100.0% |
| SQL-ID resolution | 100.0% |
| KG-URI resolution | 100.0% |
| Template hit rate | 100.0% |
| Lane routing accuracy | 72.0% |
| Analytical aggregation accuracy | 100.0% |
| Subgraph recall (relational) | 100.0% |
| Comparative SQLite capture | 100.0% |
| Comparative KG capture | 100.0% |
| Honest-refusal rate (out-of-scope) | 0.0% |
| LLM calls required | 0 |
| p50 latency | 372 ms |
| p90 latency | 2,095 ms |
| p95 latency | 4,152 ms |

**Question categories:** A-series (analytical), R-series (relational), C-series (comparative), G-series (out-of-scope)

**Known issues (pre-freeze, non-blocking):**
- Honest-refusal 0%: G-series out-of-scope questions are routed by the semantic layer (partial keyword matches trigger tenancy/eviction templates) rather than refused. Fixing this requires an explicit out-of-scope classifier before the semantic layer.
- Lane routing 72%: Several census/geography questions are correctly answered as ANALYTICAL but labelled RELATIONAL by intent_router. The SQL result is correct; only the intent label disagrees.

### 26.2 GraphRAG Enrichment Evaluation (OFF vs ON)

Run: `python3 -m eval.graphrag_enrichment_eval`

| Metric | Value |
|---|---|
| Cases tested | 9 (R-series + multi-hop) |
| Numeric delta = 0 | 9/9 (100%) — acceptance gate passed |
| Grounding OK | 5/9 (56%) |
| Provenance path present | 9/9 (100%) |
| Avg auto-usefulness score | 4.4 / 5 |
| p90 latency overhead (ON − OFF) | +46 ms (warm BGE) |

GraphRAG enrichment is **additive only** — SQL aggregates are never modified. BGE cold start on first request: ~17 s.

### 26.3 GraphRAG ON vs OFF Comparison

| Metric | GraphRAG ON | GraphRAG OFF | Delta |
|---|---|---|---|
| Routing accuracy | 89.3% | 89.3% | 0.0 |
| Aggregation correctness | 100.0% | 100.0% | 0.0 |
| All other accuracy metrics | same | same | 0.0 |
| p50 latency | 372 ms | 365 ms | +7 ms |
| p90 latency | 2,095 ms | 2,049 ms | +46 ms |

### 26.4 RQ6 — SQL vs SPARQL Comparison

Full results: `eval_results/rq6_sql_vs_sparql.md`

| Q | Question | SQL result | SPARQL result | Agreement |
|---|---|---|---|---|
| Q1 | Total emigration | 6,016 | 0 | ✗ co: repo not loaded |
| Q2 | Emigration Ballynultagh | 400 | 0 | ✗ co: repo not loaded |
| Q3 | Total evictions | 7,763 | 0 | ✗ co: repo not loaded |
| Q4 | Population 1841 | 119,300 | empty | ✗ co: repo not loaded |
| Q5 | Population Ballinacor 1841 | 55 | empty | ✗ co: repo not loaded |
| Q6 | Ballinacor parish/barony | Kilbride/Arklow | empty | ✗ VRTI disagrees on boundary values |

**Finding:** The local `co:` repository is provisioned but not loaded with data. Open-world queries return 0/empty rather than "no data" signals. The comparison framework is complete; the data loading step (`scripts/rdf_uplift.py`) is the remaining gap.

---

*This document is the definitive technical reference for the Coolattin Estate Records Explorer. It is maintained alongside the codebase and should be updated whenever architecture changes.*
