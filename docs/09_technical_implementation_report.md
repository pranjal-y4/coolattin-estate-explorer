# Coolattin Estate Records Explorer — Full Technical Implementation Report

**Project:** Masters Dissertation — MSc Computer Science (Interactive Digital Media)  
**Application:** Coolattin Estate Records Explorer  
**Institution:** Trinity College Dublin  
**Candidate:** Pranjal Yadav  
**Version:** July 2026 (`v1.0-demo-freeze` tagged 2026-06-10)  
**Deployment:** Azure App Service — Italy North (`coolattin-app.azurewebsites.net`)

---

## Table of Contents

1. [Project Overview and Historical Context](#1-project-overview-and-historical-context)
2. [System Architecture](#2-system-architecture)
3. [Technology Stack](#3-technology-stack)
4. [Database Design — All 17 Tables](#4-database-design--all-17-tables)
5. [Application Factory and Configuration](#5-application-factory-and-configuration)
6. [Data Ingestion Pipeline](#6-data-ingestion-pipeline)
7. [Feature: Interactive Map](#7-feature-interactive-map)
8. [Feature: Census Explorer](#8-feature-census-explorer)
9. [Feature: Unified Estate Records Search](#9-feature-unified-estate-records-search)
10. [Feature: Analytics Dashboard](#10-feature-analytics-dashboard)
11. [Feature: Heritage Landscape Layer](#11-feature-heritage-landscape-layer)
12. [Feature: Natural-Language Ask — Seven-Phase LLM Pipeline](#12-feature-natural-language-ask--seven-phase-llm-pipeline)
13. [Feature: In-Process GraphRAG Engine](#13-feature-in-process-graphrag-engine)
14. [Feature: Multi-Model LLM Synthesis Chain](#14-feature-multi-model-llm-synthesis-chain)
15. [Feature: KG Explore — SQL vs SPARQL Comparison](#15-feature-kg-explore--sql-vs-sparql-comparison)
16. [Feature: Workhouse Entity Resolution](#16-feature-workhouse-entity-resolution)
17. [Feature: PDF Export](#17-feature-pdf-export)
18. [Feature: Excel Export](#18-feature-excel-export)
19. [Feature: Internationalisation (English / Irish)](#19-feature-internationalisation-english--irish)
20. [API Reference — All Endpoints](#20-api-reference--all-endpoints)
21. [Frontend Architecture](#21-frontend-architecture)
22. [Security Architecture](#22-security-architecture)
23. [Performance Design and Caching](#23-performance-design-and-caching)
24. [Deployment and CI/CD](#24-deployment-and-cicd)
25. [Evaluation Results](#25-evaluation-results)
26. [Codebase Metrics](#26-codebase-metrics)

---

## 1. Project Overview and Historical Context

The **Coolattin Estate Records Explorer** is a full-stack web application that makes nineteenth-century Irish archival records searchable, visualisable, and queryable in natural language. It was built as a Masters Dissertation artefact at Trinity College Dublin, under the supervision of Dr Ciarán Wallace (VRTI) and Prof Declan O'Sullivan (CS).

### Historical Context

The Coolattin Estate was the County Wicklow seat of the Fitzwilliam family. In the Famine decade and its aftermath, the estate is documented as having overseen:

- **6,016 emigrations** (sponsored passage to Canada and elsewhere) between 1847 and 1856
- **7,763 clearance records** across 122 townlands from 1847 to 1856
- **~5,000 tenancy records** documenting landholding relationships
- Population data from **1827 to 1891** across 152 townlands
- Individual-level workhouse pauper register entries from the same period

The unified dataset (`unified_processed.csv`, 13,707 rows) integrates emigration, eviction, and tenancy ledgers into a single searchable table.

### Dissertation Objectives and Research Questions

| RQ | Question | Implementation |
|---|---|---|
| RQ1 | Data cleaning and geospatial alignment | Townland normalisation, VRTI enrichment, reconciliation gaps audit |
| RQ2 | KG linkage coverage | VRTI SPARQL pull into `townland` table; `kg_uri` field coverage |
| RQ3 | Workhouse record linkage | Dedicated ER pipeline: 7-signal scoring, 140 confirmed links |
| RQ4 | NL-to-SQL pipeline accuracy | 7-phase orchestrated pipeline; 75-question evaluation; 100% aggregation correctness |
| RQ5 | Explainable AI | SQL display, route provenance, query strategy labels, streaming stages |
| RQ6 | SQL vs SPARQL comparison | `semantic_layer.compile_sparql()` + GraphDB + KG explore page |
| RQ7 | Graphical summaries | 7 Chart.js templates + D3 force graph + KG compare timeline |

### Core Technical Constraints (Dissertation)

All technical choices were made under these constraints:
- **Reproducible** — anyone can clone the repo and get the same results
- **Transparent** — every claim is traceable to a known data source
- **Self-contained** — no hard dependencies on running external services at exam time
- **Stable** — `v1.0-demo-freeze` git tag pins the evaluation state

---

## 2. System Architecture

### 2.1 Architectural Principles

The system uses a strict three-layer separation throughout:

```
Routes (backend/routes/)      ← thin HTTP adapters; no logic
   └── Services (backend/services/)  ← all business logic
         └── Repositories (backend/repositories/)  ← all SQL
               └── SQLite (coolattin.db)
```

Additionally: no ORM, no raw SQL outside repositories, `extensions.py` as the sole DB singleton, `config.py` as the sole source of truth for tunable values.

### 2.2 High-Level Component Diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│  BROWSER  (Vanilla JS · Leaflet.js · D3.js · Chart.js · SSE)         │
└──────────────────────────┬───────────────────────────────────────────┘
                           │ HTTP / SSE / EventSource
┌──────────────────────────▼───────────────────────────────────────────┐
│  FLASK APPLICATION  (Python 3.12 · Gunicorn · Application Factory)   │
│                                                                      │
│  8 Blueprints: main · ask · census · unified · map · townlands ·     │
│                exports · kg_explore                                  │
│                                                                      │
│  Services: ask_service(10K loc) · semantic_layer · intent_router ·   │
│            subgraph_engine · graphrag · embedding_index ·            │
│            identity_resolver · workhouse_entity_resolution · ...     │
└──────────────────────────┬───────────────────────────────────────────┘
                           │
         ┌─────────────────┼──────────────────────┐
         ▼                 ▼                      ▼
┌─────────────────┐  ┌──────────────┐  ┌──────────────────────────┐
│  coolattin.db   │  │  VRTI SPARQL │  │  LLM Synthesis Chain     │
│  SQLite 3 / WAL │  │  (live, 1h   │  │  [1] Claude (Anthropic)  │
│  17 tables      │  │   TTL cache) │  │  [2] Grok (xAI)          │
│  ~65 MB         │  └──────────────┘  │  [3] OpenRouter          │
└─────────────────┘                    │  [4] Ollama (local)      │
         │                             └──────────────────────────┘
         │ graph_nodes / graph_edges
         ▼
┌─────────────────────────────────────────────────────────────────┐
│  In-Process GraphRAG (graphrag.py)                              │
│  NetworkX MultiDiGraph: 49,081 nodes · 64,308 edges             │
│  BGE-large-en-v1.5 embeddings: 28,078 nodes × 1024 dim         │
│  vector seed → k-hop BFS → linearised subgraph enrichment       │
└─────────────────────────────────────────────────────────────────┘
         │
         ▼ (D8 comparison prototype)
┌─────────────────────────────────────────────────────────────────┐
│  Local GraphDB  (co: ontology, http://localhost:7200)           │
│  SPARQL comparison: same SlotFill → SQL AND SPARQL simultaneously│
└─────────────────────────────────────────────────────────────────┘
```

### 2.3 Request Flow — Ask Page

```
Browser  POST /api/ask/query
  ↓
ask.py::query_endpoint()
  → answer_question_stream(question, townland_hint, show_sql)
  → _orchestrated_pipeline_stream()    [ASK_USE_NEW_PIPELINE=true]
        │
        ├─ Pre-flight  (~5 ms, sync)
        │    _resolve_townland_context()  → townland_norm, sql_id, kg_uri
        │    _analyse_question()          → intent, output_mode, year, ...
        │    _question_data_coverage_warnings()
        │
        ├─ Four Fast Lanes  (no LLM if any lane fires)
        │    Lane 1: rule-based slot-fill     (semantic_layer.try_rule_based_fill)
        │    Lane 2: verified template        (_try_verified_analysis, 81 templates)
        │    Lane 3: direct memory reuse      (_find_similar_approved_queries)
        │    Lane 4: embedding template       (embedding_index._phase4_retrieve)
        │
        ├─ Phase 1: Identity resolution       (identity_resolver.py)
        ├─ Phase 2: Semantic layer            (semantic_layer.py)
        ├─ Phase 3: Subgraph engine           (subgraph_engine.py + graphrag.py)
        ├─ Phase 4: Hybrid embedding          (embedding_index.py)
        ├─ Phase 5: Intent classification     (intent_router.py)
        ├─ Phase 6: Fusion                    (ask_service.py)
        └─ Phase 7: LLM synthesis             (ask_service.py)

Each phase yields SSE event → browser renders progressively
Final SSE event type="result" → full answer JSON payload
```

---

## 3. Technology Stack

### 3.1 Backend

| Component | Technology | Purpose |
|---|---|---|
| Language | Python 3.12 | All backend code |
| Web framework | Flask 3.x | HTTP routing, Jinja2 templates, SSE |
| WSGI server | Gunicorn (gthread, 4 workers) | Production server |
| Database | SQLite 3 via raw `sqlite3` | Local persistence (no ORM) |
| HTTP client | `requests` | SPARQL + LLM API calls |
| Data processing | `pandas` | CSV loading, workhouse data |
| Fuzzy matching | `rapidfuzz` | Townland name matching, ER scoring |
| Phonetics | `jellyfish` | Metaphone encoding for ER blocking |
| Graph engine | `networkx` | In-process GraphRAG property graph |
| Dense embeddings (local) | `sentence-transformers` + BAAI/bge-large-en-v1.5 | 1024-dim node embeddings (dev only) |
| Dense embeddings (cloud) | `voyageai` (voyage-large-2) | 1024-dim embeddings (Azure production) |
| Dense embeddings (alt) | `cohere` (embed-english-v3.0) | 1024-dim embeddings (alternative) |
| Numpy | `numpy` | Matrix operations for cosine ANN |
| Rate limiting | `flask-limiter` | Ask endpoint abuse prevention |
| Excel export | `openpyxl` | Census data Excel download |
| GeoJSON | stdlib `json` | Townland boundary serving |
| PDF export | Hand-written PDF 1.4 | No library dependency |

### 3.2 Frontend

| Component | Technology | Purpose |
|---|---|---|
| Language | Vanilla JavaScript (ES2020) | All interactive logic — no framework |
| Mapping | Leaflet.js 1.9 | Choropleth map, polygon rendering |
| Graph viz | D3.js 7 | Force-directed KG visualisation |
| Charting | Chart.js 4.x | Analytics bar/line/doughnut charts |
| Templating | Jinja2 3.x | Server-side HTML rendering |
| Streaming | `EventSource` API | SSE consumer for Ask pipeline |
| Styling | Custom CSS (main.css) | No CSS framework |
| Markdown | marked.min.js | LLM answer Markdown rendering |

### 3.3 External Services

| Service | Role | Protocol | Fallback |
|---|---|---|---|
| VRTI Virtuoso | Townland metadata + census KG | SPARQL/HTTPS | In-DB cache + seed CSV |
| Claude (Anthropic) | LLM synthesis (first priority) | REST | Next in chain |
| Grok (xAI) | LLM synthesis (second priority) | REST | Next in chain |
| OpenRouter | LLM synthesis (third priority) | REST (OpenAI compat) | Next in chain |
| Ollama | LLM synthesis (local fallback) | REST | raw actual_answer |
| Voyage AI | Dense embeddings (Azure prod) | REST | local BGE (dev) |
| Cohere | Dense embeddings (alternative) | REST | local BGE |
| GraphDB (local) | co: ontology SPARQL (D8) | SPARQL/HTTP | Skip (optional) |

### 3.4 Why These Choices

| Decision | Reason |
|---|---|
| SQLite over PostgreSQL | Zero setup; single file; examinable directly |
| Raw SQL over ORM | Every query is auditable; no magic |
| Vanilla JS over React | 8 pages; no build step needed; readable by anyone |
| Hand-written PDF | No external dependency that can rot |
| LLM last, not first | Historical data demands accuracy; deterministic paths first |
| Multi-provider LLM chain | Academic demos must work offline (Ollama) and in the cloud |

---

## 4. Database Design — All 17 Tables

All tables created and migrated by `extensions.py::ensure_schema()`. The database uses WAL mode (`PRAGMA journal_mode=WAL`) and `PRAGMA foreign_keys=ON`. The singleton connection is accessed only via `get_db_conn()` from `extensions.py`.

### 4.1 Core Reference Tables

#### `townland`
152 rows — one per Coolattin Estate townland. The canonical reference for all townland-related queries. `entity_id` is a UUID surrogate used by cross-reference and provenance tables; `name` holds the canonical UPPERCASE English form and has no UNIQUE constraint to allow same-named townlands in different baronies to coexist.

```sql
CREATE TABLE townland (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id           TEXT,                   -- UUID surrogate key
    name                TEXT NOT NULL,          -- canonical UPPERCASE English name
    qualifier           TEXT,                   -- locational qualifier: UPPER/LOWER/etc.
    logainm_id          TEXT,                   -- logainm.ie place identifier
    name_gaelic         TEXT,
    barony              TEXT,
    civil_parish        TEXT,
    electoral_division  TEXT,
    placename_theme     TEXT,
    description         TEXT,
    td_id               TEXT,
    guid                TEXT,
    area_sqm            REAL,
    kg_uri              TEXT,
    wkt_geometry        TEXT,
    centroid_lat        REAL,
    centroid_lon        REAL,
    county              TEXT,
    osm_id              TEXT,
    osi_id              TEXT,
    vrti_id             TEXT,
    images_json         TEXT DEFAULT '[]',
    links_json          TEXT DEFAULT '[]',
    geometry_flag       TEXT,
    source              TEXT DEFAULT 'json',
    created_at          TEXT DEFAULT (datetime('now')),
    updated_at          TEXT DEFAULT (datetime('now'))
)
```

#### `townland_xref`
Cross-reference table. Maps `(source, source_record_id)` pairs to the canonical `entity_id`.

```sql
CREATE TABLE townland_xref (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id        TEXT NOT NULL,
    source           TEXT NOT NULL,          -- 'geojson' | 'kg' | 'reference' | 'manual'
    source_record_id TEXT NOT NULL,          -- TD_ID, kg_uri, townlands.ie URL, etc.
    confidence       REAL,
    match_method     TEXT,
    created_at       TEXT DEFAULT (datetime('now')),
    UNIQUE(source, source_record_id)
)
```

#### `census_record`
Population data per townland × year. Estate surveys (1827–1868) and national censuses (1841–1891).

```sql
CREATE TABLE census_record (
    id                  INTEGER PRIMARY KEY,
    townland_id         INTEGER REFERENCES townland(id),
    year                INTEGER NOT NULL,
    total_population    INTEGER,
    males               INTEGER,   -- national census years only
    females             INTEGER,   -- national census years only
    inhabited_houses    INTEGER,
    uninhabited_houses  INTEGER,
    source              TEXT   -- 'vrti' | 'estate_survey' | 'seed_csv'
)
```

#### `clearances_record`
Estate evictions per townland × year (1847–1856).

```sql
CREATE TABLE clearances_record (
    id          INTEGER PRIMARY KEY,
    townland_id INTEGER REFERENCES townland(id),
    year        INTEGER NOT NULL,
    count       INTEGER NOT NULL
)
```

#### `refresh_state`
Dataset freshness tracking. One row per dataset.

```sql
CREATE TABLE refresh_state (
    dataset         TEXT PRIMARY KEY,
    last_refreshed  TEXT,     -- ISO timestamp
    record_count    INTEGER
)
```

#### `field_provenance`
Field-level source tracking. Records which source contributed each field value.

```sql
CREATE TABLE field_provenance (
    id          INTEGER PRIMARY KEY,
    table_name  TEXT NOT NULL,
    row_id      INTEGER NOT NULL,
    field_name  TEXT NOT NULL,
    source      TEXT NOT NULL,
    confidence  REAL,
    recorded_at TEXT
)
```

### 4.2 Runtime-Seeded Tables

Populated on first Ask request (or first page load requiring them).

#### `unified_record`
13,707 individual estate person records. Seeded from `unified_processed.csv` by `_ensure_unified_table_seeded()`.

Key columns: `record_id` (TEXT, UNIQUE), `canonical_name`, `forename`, `surname`, `townland_norm`, `year`, `age`, `gender`, `ship_name`, `destination`, `has_emigration_record` (INT 0/1), `has_eviction_record` (INT 0/1), `has_tenancy_record` (INT 0/1), `is_widow` (INT 0/1), `is_canada_destination` (INT 0/1), `children_count`, `family_size_estimate`, `holding_acres`, `family_key`.

#### `heritage_feature`
NMS archaeological/heritage monument data. Seeded from GeoJSON files.

Key columns: `id`, `name`, `feature_type` (`holy_well` / `monument` / `asi`), `lat`, `lon`, `description`, `townland_norm`.

### 4.3 Ask Pipeline Tables

#### `ask_query_memory`
Approved question→SQL pairs from thumbs-up feedback. Powers Fast Lanes 3 and 4.

| Column | Description |
|---|---|
| `id` | Surrogate PK |
| `question` | Original user question |
| `sql` | Approved SQL query |
| `question_norm` | Normalised question for matching |
| `tags` | JSON array of topic tags |
| `created_at` | ISO timestamp |

#### `ask_query_feedback`
All feedback submissions (both thumbs-up and thumbs-down) for audit.

| Column | Description |
|---|---|
| `question` | Original question |
| `sql` | Executed SQL |
| `feedback` | `positive` / `negative` |
| `session_id` | Browser session identifier |
| `created_at` | ISO timestamp |

### 4.4 Entity Resolution Tables

#### `source_mentions`
One row per name occurrence in a source record (workhouse ER). Each workhouse row becomes one mention.

| Column | Description |
|---|---|
| `source_table` | Dataset name (`workhouse`) |
| `source_record_id` | Row identifier in the source (UNIQUE) |
| `raw_name` | Name as it appears in the source |
| `normalised_name` | Full normalised name (uppercase, abbreviations expanded) |
| `forename` | Parsed forename |
| `surname` | Parsed surname |
| `phonetic_forename` | Metaphone encoding of forename |
| `phonetic_surname` | Metaphone encoding of surname |
| `raw_place` | Place name as it appears in the source |
| `normalised_place` | Normalised place name |
| `canonical_townland_id` | FK → `townland.id` |
| `event_year` | Year of the event |
| `age` | Age at time of event |
| `inferred_birth_year` | Derived from event_year − age |
| `occupation` | Occupation if recorded |
| `household_fields` | Additional household context |
| `source_payload_json` | Full source record as JSON |

#### `entity_resolution_candidates`
Scored candidate links. Up to 25 per mention.

| Column | Description |
|---|---|
| `mention_id` | FK → `source_mentions.id` |
| `candidate_source_table` | Target table (`unified_record`) |
| `candidate_record_id` | Target record identifier |
| `candidate_name` | Name of the candidate record |
| `candidate_place` | Place of the candidate record |
| `candidate_year` | Year of the candidate record |
| `score` | Composite score (0.0–1.0) |
| `label` | `CONFIRMED_MATCH` / `POSSIBLE_MATCH` / `WEAK_CANDIDATE` / `NO_MATCH` |
| `evidence_json` | Signals that support the match |
| `conflicts_json` | Signals where evidence contradicts |
| `missing_evidence_json` | Signals that could not be evaluated |
| `review_required` | 1 if flagged for human review |

#### `workhouse_unified_links`
Final accepted workhouse→estate record links (140 CONFIRMED_MATCH).

#### `entity_resolution_decisions`
Full audit trail of human review decisions.

#### `match_review`
Uncertain townland-pair review queue. Holds pairs of townland rows with similar names that the ingest pipeline could not automatically reconcile, pending a human reviewer decision. CRUD in `match_review_repository.py`.

### 4.5 GraphRAG Tables

Built by `scripts/build_graph.py`. Loaded at runtime into a NetworkX process-lifetime graph.

#### `graph_nodes` (49,081 rows)

| Column | Description |
|---|---|
| `node_id` | TEXT PK — unique node identifier |
| `label` | Node type: `Person` / `Townland` / `Event` / `Ship` |
| `name` | Display name of the node |
| `props` | JSON blob of node properties |
| `community` | Community/cluster label from graph partitioning |
| `embedding` | BLOB — 1024-dim BGE-large float32 embedding (28,078 nodes have this) |

#### `graph_edges` (64,308 rows)

Primary key is composite `(src, dst, rel_type)` — no separate integer `id` column.

| Column | Description |
|---|---|
| `src` | FK → `graph_nodes.node_id` |
| `dst` | FK → `graph_nodes.node_id` |
| `rel_type` | `EMIGRATED_FROM` / `LIVES_IN` / `EVICTED_FROM` / `SAME_AS` / `BELONGS_TO` |
| `props` | JSON blob of edge properties |

---

## 5. Application Factory and Configuration

### 5.1 Bootstrap Sequence

```python
# app.py
from backend.app import create_app
app = create_app()
app.run(host="0.0.0.0", port=5001, debug=True)

# create_app.py
def create_app() -> Flask:
    app = Flask(...)
    app.config.from_object(ActiveConfig)    # from config.py
    init_db(app)                            # ensure_schema() → all 17 tables
    init_limiter(app)                       # flask-limiter
    register_blueprints(app)               # 8 blueprints
    return app
```

### 5.2 Configuration Hierarchy

```
config.py::Config         ← base defaults
  ↑ DevelopmentConfig     ← DEBUG=True, LOG_LEVEL=DEBUG
  ↑ ProductionConfig      ← DEBUG=False, CENSUS_STALE=1 day

ActiveConfig = ProductionConfig   (when FLASK_ENV != "development")
```

All tunable values in `Config`. Environment variables override defaults. The `FLASK_ENV` defaults to production when unset — Azure deployments are safe without explicit configuration.

### 5.3 DB Singleton (`extensions.py`)

```python
from extensions import get_db_conn

conn = get_db_conn()    # WAL mode, foreign_keys=ON, Row factory
```

`ensure_schema()` is idempotent — runs on every startup, creates missing tables, adds missing columns via `ALTER TABLE`, never drops data.

---

## 6. Data Ingestion Pipeline

### 6.1 How Ingest Works

Ingest is batch and one-shot — not at request time. The application serves from the local SQLite database; the VRTI endpoint is queried only during ingest (and for per-question live enrichment on the Ask page, which is cached).

```
Trigger: POST /api/census/refresh  (or /ingest slash command)
  → refresh_service.trigger_refresh()
  → full_ingest.run()
       ├─ townlands_ingest.run()  → reads townlands.json + VRTI SPARQL → townland table
       └─ census_ingest.run()    → VRTI SPARQL → census_record table
```

### 6.2 Townland Name Normalisation

The central data integration challenge. Spelling variants across sources:
- `"Ballinacor"`, `"Ballinacor North"`, `"BALLINACOR"` → canonical `"BALLINACOR NORTH"`
- `"Aghowle Lower"`, `"Aghowle"`, `"AGHOWLE"` → canonical `"AGHOWLE LOWER"`

`townland_service.normalize_townland_name()` applies:
1. Uppercase
2. Strip punctuation and diacritics
3. Lookup in `townland_aliases.json` (manually curated alias map)
4. Fuzzy match fallback (token_set_ratio ≥ 80 via rapidfuzz)

Unresolved cases are written to `data/source_snapshots/reconciliation_gaps.csv`.

### 6.3 DB-First, KG-Second Pattern

```
Request comes in
    ↓
Is data in local DB? → Yes, is it fresh? → Yes → Serve from DB (< 1 ms)
                                          → No  → Serve from DB + refresh in background
                      → No → Call VRTI KG → Save to DB → Serve result
                                          (if VRTI is down → serve from seed CSV)
```

A direct VRTI SPARQL call takes 500 ms–2 s. A local SQLite query takes < 1 ms. Users get a fast, consistent experience regardless of VRTI's availability.

---

## 7. Feature: Interactive Map

**Page:** `/`  
**Frontend:** `main.js`, `map.js`  
**Data:** `GET /api/townlands/geojson` + `GET /api/map/layers`

The landing page is a **choropleth map** rendered by Leaflet.js. Each of the 152 Coolattin Estate townlands is drawn as a GeoJSON polygon, with colour intensity representing the selected data layer.

### Data Layers
- Population (estate survey years: 1827, 1839, 1848, 1850, 1860, 1868)
- Evictions/clearances per year (1847–1856)
- Emigration count per townland (from `unified_record`)

### Performance Optimisation
GeoJSON and unified data are loaded in **parallel** on page load (`Promise.all`), cutting the initial load wait roughly in half. The townland catalog for the Ask page dropdown is also pre-loaded on page load — no per-keystroke round-trips.

### Click Interaction
Clicking a townland polygon opens a sidebar showing:
- Townland name (English + Irish)
- Civil parish and barony
- Population data for selected year
- VRTI external links (logainm.ie, townlands.ie)

---

## 8. Feature: Census Explorer

**Page:** `/census`  
**Frontend:** `census.js`  
**Data:** `GET /api/census/townlands`, `GET /api/census/townland/<name>`

A year slider moves from 1827 to 1891. The choropleth updates as population rises and falls across the estate's 152 townlands.

### Dual-Source Challenge

Estate surveys (1827, 1839, 1848, 1850, 1860, 1868) record **total population only** — no male/female breakdown, no house counts. National censuses (1841, 1851, 1861, 1871, 1881, 1891) record **males, females, inhabited houses, uninhabited houses** separately.

Both source types are stored in `census_record`. The `source` column distinguishes them. The census page handles both formats:
- For estate survey years: renders total bar only
- For national census years: renders male/female split + house count

### Data Sources for Census
- **1827–1868 estate surveys:** from `townlands.json` GeoJSON properties (`T_POP_1827`, etc.)
- **1841–1891 national censuses:** from VRTI SPARQL (pulled during ingest, stored in `census_record`)
- **Fallback:** `unified_census.csv` seed (165 townlands × 6 national census years)

---

## 9. Feature: Unified Estate Records Search

**Page:** Accessible via the Ask page and unified API  
**API:** `GET /api/unified/records`, `GET /api/unified/person/<id>`  
**Service:** `unified_service.py`

Full-text search over the 13,707 estate person records. Supports filtering by:
- Free-text name search (across `canonical_name`, `forename`, `surname`)
- Townland filter
- Record type (`has_emigration_record`, `has_eviction_record`, `has_tenancy_record`)
- Year range

### Person Detail Enrichment

`GET /api/unified/person/<id>` returns a single record enriched with:
- Workhouse entity resolution results from `workhouse_unified_links`
- Identity disambiguation from `identity_resolver.py`
- Related family members (same `family_key`)
- VRTI townland metadata

The response includes `linked_workhouse_records`, `possible_workhouse_matches`, `please_check_records`, `identity_is_ambiguous`, and `supporting_evidence` / `conflicting_evidence` arrays.

---

## 10. Feature: Analytics Dashboard

**Page:** `/analytics`  
**Frontend:** `analytics.js`  
**Route:** `GET /analytics?d=<module_slug>` (server-rendered; no separate JSON API)

### Pluggable Module Architecture

Each analytics view is a self-contained module implementing the `AnalyticsModule` protocol from `analytics/base.py`:

```python
class AnalyticsModule(Protocol):
    name: str
    slug: str
    description: str
    def get_kpis(self) -> list[KPI]: ...
    def get_charts(self) -> list[Chart]: ...
```

Adding a new analytics view = creating one new file in `analytics/`. Nothing else changes. The `analytics/registry.py` auto-discovers all modules in the directory.

### Registered Modules

| Module | Slug | Dataset |
|---|---|---|
| Emigrations | `emigrations` | `unified_record` (emigration rows) |
| Evictions | `evictions` | `clearances_record` |
| Tenancies | `tenancies` | `unified_record` (tenancy rows) |
| Unified Dataset | `unified` | All of `unified_record` |
| Workhouse Links | `workhouse` | `workhouse_unified_links` |
| Townland Geography | `townland_geo` | `townland` + `census_record` |

### Chart Rendering

Charts use Chart.js rendered via `analytics.js`. KPI cards show with trend indicators. Each module defines its own chart types (`bar`, `line`, `doughnut`).

---

## 11. Feature: Heritage Landscape Layer

**Page:** `/heritage`  
**Frontend:** `heritage.js`  
**Data:** `frontend/static/data/*.geojson` (served as static files)

An overlay of archaeological monuments and holy wells from the **National Monuments Service** open data, rendered on a Wicklow base map via Leaflet.js.

### Data Sources (all static GeoJSON)
- `holywells_wicklow.geojson` — holy wells in County Wicklow
- `monuments_wicklow.geojson` — national monuments
- `asi_wicklow.geojson` — Architectural Survey of Ireland (ASI)

### Features
- Filter panel by monument type (holy well / monument / earthwork / fort)
- Click to open detail popup (name, type, description, grid reference)
- Toggle layers on/off independently

---

## 12. Feature: Natural-Language Ask — Seven-Phase LLM Pipeline

**Page:** `/ask`  
**Frontend:** `ask.js` (SSE consumer)  
**Entry point:** `POST /api/ask/query` → `ask_service.py::_orchestrated_pipeline_stream()`

The Ask page is the system's most technically sophisticated component — 10,192 lines of Python implementing a seven-phase orchestrated pipeline. The pipeline is designed so that the **LLM never generates numbers** — all aggregates come from deterministic SQL, and the LLM rewrites the result for readability.

### 12.1 Pipeline Philosophy

| Principle | Implementation |
|---|---|
| Deterministic first | 4 fast lanes before any LLM call |
| LLM for structure, not facts | Slot-fill JSON → compiler → SQL (never LLM SQL in ANALYTICAL path) |
| Transparent | Every stage emits an SSE event; SQL is shown to the user |
| Graceful degradation | VRTI down? Skip enrichment. LLM down? Return raw SQL result |
| Provenance-annotated | `query_provenance.strategy` tells user exactly how the answer was produced |

### 12.2 Pre-Flight (< 5 ms, no LLM)

```
_resolve_townland_context(question, townland_hint)
  → Tokenise question, remove stopwords
  → Exact → fuzzy (token_set_ratio ≥ 80) → hint override
  → Result: {name, sql_id, kg_uri, warning}

_analyse_question(question)
  → year regex, surname regex, radius regex
  → keyword matching against 14 metric sets (METRIC_REGISTRY)
  → Result: {primary_intent, output_mode, scope, year, surname, ...}

_question_data_coverage_warnings(question)
  → Result: [] or ["Census data begins at 1841", ...]
```

### 12.3 Four Fast Lanes

First match short-circuits all downstream phases. These lanes answer ~100% of standard analytical questions with zero LLM SQL calls (confirmed by the 75-question evaluation).

| Lane | Mechanism | Confidence threshold | LLM? |
|---|---|---|---|
| Lane 1 | Rule-based slot-fill (14 metrics) | ≥ 0.80 | 0 |
| Lane 2 | Verified template (81 pre-written SQL; 15 in VERIFIED_ANALYSIS_TEMPLATE_IDS) | 1.0 | 0 |
| Lane 3 | Direct memory reuse (thumbs-up approved) | token_sort_ratio + cosine ≥ 0.55 | 0 |
| Lane 4 | Embedding template (TF-IDF + RRF) | cosine ≥ 0.68 | 0 |

### 12.4 Intent Classification (Phase 5)

If no fast lane fires, `intent_router.classify_intent()` classifies the question:

| Intent | Key signals | Route |
|---|---|---|
| COMPARATIVE | "compare", "versus", "vs", "difference between", "higher than" | Both SQL + subgraph in parallel |
| RELATIONAL | "which parish", "in the barony", "heritage", "history of", "about the estate" | Subgraph engine + GraphRAG |
| ANALYTICAL | `primary_intent`, `output_mode=count/aggregate`, analytical keywords | Semantic layer → deterministic SQL |
| FALLBACK | Default | LLM free-form SQL generation |

**Core Rule 1 override:** heritage/sensemaking keywords alone + count/aggregate output → classified as ANALYTICAL, not RELATIONAL.

### 12.5 SQL Safety Guardrail

Every SQL query (from any source) passes through:
```python
FORBIDDEN_SQL = re.compile(
    r'\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|DETACH|'
    r'PRAGMA|REINDEX|VACUUM|TRUNCATE|REPLACE)\b', re.IGNORECASE
)
# Also: must start with SELECT or WITH
```
LLM-generated SQL that fails this check raises `ValueError` and the error is returned to the user.

### 12.6 Feedback Loop

Every Ask answer has thumbs-up / thumbs-down buttons. A thumbs-up saves the question→SQL pair to `ask_query_memory`. Future similar questions hit Fast Lane 3 (direct memory reuse) or Fast Lane 4 (embedding retrieval). Over time, the validated query library grows without any manual curation.

### 12.7 SSE Streaming

Each pipeline stage emits a progress event. The user sees the pipeline working in real time:
```json
{"type": "progress", "stage": "classifying_intent", "status": "completed", "detail": "ANALYTICAL", "duration_ms": 12}
```
The final event has `"type": "result"` with the complete answer payload (see §20 for full schema).

---

## 13. Feature: In-Process GraphRAG Engine

**Module:** `backend/services/graphrag.py` (573 lines)  
**Tables:** `graph_nodes` (49,081), `graph_edges` (64,308)  
**Build script:** `scripts/build_graph.py`

The GraphRAG engine provides subgraph enrichment for RELATIONAL and COMPARATIVE questions. It runs entirely in-process — no external graph server required. The graph is stored in SQLite and loaded into a NetworkX `MultiDiGraph` at startup.

### 13.1 Graph Construction

`scripts/build_graph.py`:
1. Reads all tables from `coolattin.db`
2. Creates nodes: `Person` (unique people), `Townland` (152), `Event` (emigrations, evictions), `Ship`
3. Creates edges: `EMIGRATED_FROM`, `LIVES_IN`, `EVICTED_FROM`, `SAME_AS`, `BELONGS_TO`
4. Embeds node "passports" (name + type + key properties as prose) using BAAI/bge-large-en-v1.5 → 1024-dim float32
5. Writes to `graph_nodes` (49,081 rows) and `graph_edges` (64,308 rows)

28,078 nodes have BGE passport embeddings (those with sufficient text content).

### 13.2 Vector Seed

```python
graphrag.vector_seed(question, top_k=8)
  → Embed question → cosine ANN over 28,078 passport vectors
  → Returns: top-k node_ids as BFS starting points
```

On Azure (where `sentence-transformers` is excluded from the build), `voyage_embeddings.py` provides equivalent 1024-dim embeddings via the Voyage AI API.

### 13.3 Subgraph Retrieval

```python
graphrag.retrieve_subgraph(question, intent, entity_hints, k_hops=2)
  1. vector_seed(question, top_k=8) → seed nodes
  2. Add nodes from entity_hints (resolved townland/person IDs)
  3. BFS from each seed, k=2 hops, following all edge types
  4. Prune to GRAPHRAG_MAX_NODES=120 by PageRank score
  5. Linearise: convert subgraph to compact triple table
  6. Attach community summary blurb (data/seed/community_summaries.json — JSON keyed by townland name)
  7. Return: GraphRAGResult{nodes, edges, linearized_text, provenance_path}
```

Provenance path logged in the SSE result: `"vector_seed(8) → k-hop BFS → 47 triples"`.

### 13.4 Key Design Rules

- GraphRAG enrichment is **additive only** — SQL aggregates are never modified
- If the graph is empty or unavailable, the pipeline answers from SQL only (no failure)
- The linearised subgraph is passed to the LLM to *read* context — not to answer count questions

### 13.5 Evaluation

9 R-series + multi-hop cases tested:
- Numeric delta = 0 for all 9 cases (acceptance gate passed — SQL counts unchanged)
- Avg provenance usefulness: 4.4/5 auto-scored
- p90 latency overhead (ON − OFF): +46 ms (warm BGE)

---

## 14. Feature: Multi-Model LLM Synthesis Chain

Every Ask response ends with an LLM rewrite of the SQL result into readable prose. A four-provider chain ensures the pipeline works with or without paid API keys, and offline.

### 14.1 Provider Chain

```
[1] Claude (Anthropic)   claude-3-5-haiku-20241022
    Key: ANTHROPIC_API_KEY
    Guard: LLM_ALLOW_PAID=true (default true)

[2] Grok (xAI)           grok-3-mini-beta
    Key: GROK_API_KEY
    Guard: LLM_ALLOW_PAID=true

[3] OpenRouter           openai/gpt-oss-20b:free (configurable)
    Key: OPENROUTER_API_KEY
    Timeout: connect 10 s, request 80 s

[4] Ollama local         auto-detected model
    URL: http://localhost:11434
    No key required — fully offline
```

Each provider is tried in order. Failure at any point triggers the next silently. If the entire chain fails, the raw SQL result is returned with a note.

### 14.2 What the LLM Receives

The synthesis prompt contains:
- System persona: "digital historian specialising in 19th century Irish social history"
- Instruction: answer only from the provided data, cite sources, do not introduce numbers not in the data
- First 20 rows of SQL result in compact format
- VRTI context (parish, barony, county, centroid)
- GraphRAG linearised subgraph (if RELATIONAL or COMPARATIVE)
- Original user question

### 14.3 Answer Validation

Before the LLM answer is included in the SSE result:
- If the LLM introduces a number not present in the SQL rows → `actual_answer` used instead
- If the response is empty or malformed → `actual_answer` used

This ensures hallucinated numbers never reach the user.

---

## 15. Feature: KG Explore — SQL vs SPARQL Comparison

**Page:** `/kg-explore`  
**Frontend:** `kg_explore.js` (D3.js)  
**API:** `/api/kg/graph`, `/api/kg/scenarios`, `/api/kg/compare`

Built for dissertation deliverable D8 (RQ6). Demonstrates side-by-side SQL and SPARQL results for the same historical question.

### 15.1 D3.js Force Graph

`GET /api/kg/graph` returns 152 townland nodes with geographic hierarchy edges (townland → parish → barony). The D3 force simulation renders a navigable knowledge graph where clicking a node shows its properties.

### 15.2 SQL vs SPARQL Comparison

`POST /api/kg/compare`:
1. Runs the SQL query against `coolattin.db`
2. Runs the SPARQL query against GraphDB at `http://localhost:7200/repositories/coolattin`
3. Returns both result sets, their execution times, and any numeric discrepancies

### 15.3 The co: Ontology

The local GraphDB repository uses the Coolattin ontology (`https://coolattin.ie/ontology#`, prefix `co:`). The `semantic_layer.compile_sparql(slot_fill)` function generates SPARQL from the same `SlotFill` struct that produces SQL — making the comparison directly equivalent.

**Current state:** The `co:` repository is provisioned and the comparison framework is fully operational. The local repository is not yet populated with data (`scripts/rdf_uplift.py` generates Turtle but the load step is pending). SPARQL queries return 0/empty (open-world assumption).

### 15.4 Four Canned Scenarios

| Scenario | SQL source | SPARQL target |
|---|---|---|
| `emigration_count_by_townland` | `unified_record` COUNT | `co:emigrationCount` property |
| `eviction_count_by_year` | `clearances_record` | `co:clearanceCount` |
| `surname_frequency` | `unified_record` GROUP BY | `co:person rdfs:label` frequency |
| `person_event_detail` | `unified_record` JOIN | `co:Event` triple pattern |

---

## 16. Feature: Workhouse Entity Resolution

**Module:** `backend/services/workhouse_entity_resolution.py` + `entity_resolution/` subpackage  
**Tables:** `source_mentions`, `entity_resolution_candidates`, `workhouse_unified_links`, `entity_resolution_decisions`  
**Run:** `python scripts/link_workhouse_records.py`

A dedicated entity-resolution subsystem that links workhouse admission records to unified estate records. Separate from the Ask pipeline because it is a record-linkage problem (explicit, reviewable candidate links with transparent scores) rather than a semantic retrieval problem.

### 16.1 Pipeline

```
Step 1  Load workhouse data
        workhouse_service.get_workhouse()
        → reads workhouse_data_final.xlsx (two sheets, ~500 rows)

Step 2  Normalise each mention
        normalise.normalise_person_fields(raw_name)
        • Unicode NFKD → uppercase
        • Remove editorial annotations ([?], [illegible], (Sic))
        • Expand abbreviations: JNO→JOHN, WM→WILLIAM, JAS→JAMES, THOS→THOMAS,
          RD→RICHARD, EDWD→EDWARD, SAML→SAMUEL, ELIZH→ELIZABETH, MARGT→MARGARET
        • Normalise Mc/Mac/O variants
        • jellyfish.metaphone() phonetic encoding

Step 3  Build unified index
        build_unified_index()
        → 13,707 unified_record rows, first blocking pass by place

Step 4  Generate candidates
        candidates.generate_candidates(mention, unified_index)
        → up to 25 ranked candidates per mention
        → blocking: exact name, surname+initial, phonetic, place+name, fuzzy, year ±1

Step 5  Score candidates (7-signal, 60-point scale)
Step 6  Assign confidence bands
Step 7  Persist to 4 SQLite tables
Step 8  Review queue (match_review_repository.py)
```

### 16.2 Scoring

| Signal | Max pts | Rule |
|---|---|---|
| Full name similarity (token_sort_ratio) | 10 | ≥90%→10; ≥75%→7; ≥60%→4; else→0 |
| Exact surname | 10 | Exact→10; Metaphone→7; else→0 |
| Forename | 10 | Missing→5 (neutral); exact→10; ≥80%→7; ≥60%→4; conflict→0 |
| Townland normalisation | 10 | Exact→10; variant→6; else→0 |
| Birth-year alignment | 5 | Gap≤3y→5; ≤8y→3; else→0 |
| Gender | 10 | Both missing→5; match→10; mismatch→0 |
| Timeline alignment | 5 | Age-progression consistency |
| **TOTAL** | **60** | Normalised to 0.0–1.0 |

### 16.3 Confidence Bands

| Band | Score | Action |
|---|---|---|
| `CONFIRMED_MATCH` | ≥ 0.75 | Auto-accepted; → `workhouse_unified_links` |
| `POSSIBLE_MATCH` | 0.50–0.74 | Flagged for human review |
| `WEAK_CANDIDATE` | < 0.50 | Requires explicit review |
| `NO_MATCH` | — | Hard negative rule or all signals missing |

### 16.4 Result

**140 confirmed links** (CONFIRMED_MATCH ≥ 0.75) persisted in the seed DB. Visible in the person detail endpoint and surfaced in the unified search API response.

### 16.5 Worked Example

**Workhouse mention:** "Jno Murphy, Aghowle, 1851, age 35, male"  
**Candidate:** "John Murphy, AGHOWLE LOWER, 1851, age 37, male"

| Signal | Score |
|---|---|
| Full name similarity: token_sort_ratio("JOHN MURPHY","JOHN MURPHY")=100% | 10 |
| Exact surname: "MURPHY"="MURPHY" | 10 |
| Forename: "JNO"→"JOHN"="JOHN" | 10 |
| Townland: "AGHOWLE" variant → "AGHOWLE LOWER" | 6 |
| Birth-year: \|1851-35 – 1851-37\| = 2 yrs ≤ 3 | 5 |
| Gender: male=male | 10 |
| Timeline: consistent | 5 |
| **Total: 56/60 = 0.93 → CONFIRMED_MATCH** | |

---

## 17. Feature: PDF Export

**Endpoint:** `GET /api/ask/pdf/<filename>`  
**Generator:** `_write_pdf_report()` in `ask_service.py`  
**Format:** Hand-written PDF 1.4 (no external library)

Every Ask response generates a PDF report in `exports/ask/ask_report_{UTC}.pdf` containing:
- Original question
- SQL query (if show_sql=true)
- Answer text (LLM-rephrased)
- Data table (first 50 rows)
- VRTI context (townland, parish, barony)
- Timestamp and provenance notes

### Why Hand-Written PDF

PDF 1.4 is ultimately a structured text format. Writing it by hand avoids a dependency on `reportlab`, `fpdf`, or `weasyprint` — all of which add significant overhead or have complex install requirements on Azure. The hand-written generator implements only the features needed (text, tables, basic layout) and is completely self-contained.

### Security

Filenames are validated before serving: only files in `exports/ask/` matching the pattern `ask_report_*.pdf` are served. Path traversal prevention is enforced.

---

## 18. Feature: Excel Export

**Endpoint:** `GET /api/exports/census`  
**Generator:** `export_service.py` via `openpyxl`

Downloads census data as an Excel workbook (`.xlsx`). Columns: townland, year, population, males, females, inhabited houses, uninhabited houses, source. Used by researchers who want the raw data in spreadsheet form.

---

## 19. Feature: Internationalisation (English / Irish)

**Module:** `frontend/static/js/i18n.js`  
**Toggled:** Language switch button in the navbar

All UI labels are available in both English and Irish Gaelic. `i18n.js` holds the translation dictionary and applies translations to elements with `data-i18n` attributes on toggle. Map polygon labels show both `TL_ENGLISH` and `TL_GAEILGE` from the GeoJSON properties.

---

## 20. API Reference — All Endpoints

### Page Routes

| Route | Method | Returns |
|---|---|---|
| `/` | GET | Home (map) HTML |
| `/census` | GET | Census explorer HTML |
| `/ask` | GET | Ask page HTML |
| `/analytics` | GET | Analytics dashboard HTML |
| `/heritage` | GET | Heritage landscape HTML |
| `/about` | GET | About page HTML |
| `/info` | GET | Technical info HTML |
| `/kg-explore` | GET | KG compare page HTML |

### Ask API (`/api/ask/`)

| Route | Method | Description |
|---|---|---|
| `/api/ask/query` | POST | SSE stream — main Ask pipeline. Body: `{question, townland_hint, show_sql}` |
| `/api/ask/feedback` | POST | Save feedback. Body: `{question, sql, feedback}` |
| `/api/ask/llm-status` | GET | LLM health check. Returns `{available, provider, model}` |
| `/api/ask/pdf/<name>` | GET | Download PDF report |
| `/api/ask/audit-log` | GET | Recent audit log (ADMIN_API_KEY required) |

### Census API (`/api/census/`)

| Route | Method | Description |
|---|---|---|
| `/api/census/townlands` | GET | All census data (all townlands, all years) |
| `/api/census/summary` | GET | Estate-wide totals per year |
| `/api/census/townland/<name>` | GET | Census data for one townland |
| `/api/census/refresh` | POST | Trigger VRTI refresh (admin) |

### Unified Records API (`/api/unified/`)

| Route | Method | Description |
|---|---|---|
| `/api/unified/records` | GET | Paginated search. Params: `q`, `townland`, `page`, `per_page` |
| `/api/unified/stats` | GET | Dataset statistics |
| `/api/unified/townlands` | GET | Townlands with record counts |
| `/api/unified/surnames` | GET | Surname frequency table |
| `/api/unified/person/<id>` | GET | Person detail + workhouse enrichment |

### Townlands API (`/api/townlands/`)

| Route | Method | Description |
|---|---|---|
| `/api/townlands` | GET | All townlands (name, id, parish, barony) |
| `/api/townlands/<name>/detail` | GET | Townland detail: census + clearances + VRTI |
| `/api/townlands/geojson` | GET | Full GeoJSON for map rendering |

### KG Explore API (`/api/kg/`)

| Route | Method | Description |
|---|---|---|
| `/api/kg/graph` | GET | D3.js force graph JSON |
| `/api/kg/scenarios` | GET | 4 canned comparison scenarios |
| `/api/kg/compare` | POST | Execute SQL + SPARQL, return side-by-side |

### Ask SSE Result Schema

The final `type: "result"` event payload:

```json
{
  "type": "result",
  "question": "string",
  "answer": "string",
  "llm_rephrased_answer": "string",
  "columns": ["string"],
  "rows": [["value"]],
  "row_count": 0,
  "sql": "string",
  "chart": {"type": "bar|line", "labels": [], "datasets": []},
  "vrti_context": {"townlands": [], "parish_count": 0},
  "graphrag_context": {"nodes": 0, "edges": 0, "linearized_text": "", "provenance_path": ""},
  "fusion": {"discrepancy_count": 0, "agreement_count": 0, "fusion_text": ""},
  "discrepancies": [],
  "warnings": [],
  "identity_disambiguation": "string",
  "pdf_url": "string",
  "availability": {"has_local_data": true, "has_vrti_data": true},
  "related_insights": ["string"],
  "query_provenance": {
    "strategy": "rule_fill | verified_analysis | memory_reuse | template_embedding | slot_fill_llm | llm_sql | subgraph | comparative",
    "used_approved_memory": false,
    "direct_memory_reuse": false,
    "execution_mode": "executed_as_generated | repaired | fallback"
  },
  "llm_meta": {
    "provider": "anthropic | grok | openrouter | ollama | verified_analysis | rule_fill",
    "model": "string",
    "mode": "analytical_semantic | relational_subgraph | comparative | fallback"
  }
}
```

---

## 21. Frontend Architecture

### 21.1 Template Structure

```
frontend/templates/
  base.html         ← shared layout (navbar, footer, CSS/JS imports)
  index.html        ← extends base.html, {% block content %}
  census.html
  ask.html
  analytics.html
  heritage.html
  about.html
  info.html
  kg_explore.html
```

### 21.2 JavaScript — One File Per Page

| File | Page | Key patterns |
|---|---|---|
| `main.js` | Home / Map | Leaflet choropleth, GeoJSON, layer switching |
| `map.js` | (shared) | Shared Leaflet helpers, polygon utilities |
| `census.js` | Census | Year slider, sidebar chart, townland click |
| `ask.js` | Ask | EventSource SSE consumer, progressive rendering, Chart.js |
| `analytics.js` | Analytics | Module tabs, KPI cards, Chart.js |
| `heritage.js` | Heritage | Monument overlay, filter panel |
| `kg_explore.js` | KG Explore | D3 force graph, comparison panel |
| `i18n.js` | All | English/Irish toggle |
| `marked.min.js` | Ask | Markdown rendering for LLM answers |

### 21.3 Why No Frontend Framework

Eight pages with independent functionality. A JavaScript framework (React, Vue) would add:
- A build step
- A dependency manager
- Component lifecycle concepts
- Hundreds of kB of library code

None of these trade-offs are worth it for this use case. The `EventSource` SSE consumer in `ask.js` is cleaner without a framework. The code is readable by anyone who knows JavaScript.

---

## 22. Security Architecture

### 22.1 SQL Injection

All SQL in `backend/repositories/` uses parameterised queries (`?` placeholders). The `_sanitize_and_validate_sql()` guardrail in `ask_service.py` blocks all write operations in LLM-generated SQL. No SQL is constructed via string concatenation in route handlers.

### 22.2 Admin Endpoint Protection

The `ADMIN_API_KEY` environment variable gates access to:
- `GET /api/ask/audit-log`
- `POST /api/exports/regenerate`
- `POST /api/census/refresh`

Requests without the correct `X-Admin-Key` header receive 403.

### 22.3 Rate Limiting

`flask-limiter` is initialised in `create_app.py`. The `/api/ask/query` endpoint is rate-limited to prevent LLM API abuse.

### 22.4 Environment Safety

`FLASK_ENV` defaults to `production` when unset. Azure deployments never accidentally enable debug mode even if the environment variable is not set explicitly.

### 22.5 PDF Security

PDF filenames are validated to prevent directory traversal. Only files in `exports/ask/` matching the expected pattern `ask_report_*.pdf` are served.

### 22.6 Audit Log

All Ask requests are logged with question text (truncated), route taken, LLM provider, and latency. Accessible via the admin endpoint.

---

## 23. Performance Design and Caching

### 23.1 DB-First Pattern

VRTI SPARQL calls take 500 ms–2 s. SQLite queries take < 1 ms. All VRTI data is pulled once during ingest and served from SQLite at runtime. The live VRTI endpoint is called at runtime only for per-question Ask enrichment (optional, non-blocking, 1-h TTL cache).

### 23.2 In-Process Caches

| Cache | TTL | Contents |
|---|---|---|
| Townland catalog | 10 min | All canonical names |
| VRTI parish data | 60 min per townland | VRTI enrichment per townland |
| VRTI circuit breaker | 5 min cooldown | Down flag after timeout |
| OpenRouter status | 60 s | Health check result |
| Ollama model list | 120 s | Available models |
| Schema descriptor | 5 min | Annotated schema for LLM prompts |
| Query memory | 60 s | Approved SQL from thumbs-up |
| Clearances schema | process lifetime | Column name variant |
| Unified CSV | process lifetime | pandas DataFrame |
| NetworkX graph | process lifetime | 49,081 nodes + 64,308 edges |

### 23.3 Parallel Loading

Map page: GeoJSON + unified data loaded in parallel (`Promise.all`). Ask page: townland catalog pre-loaded on page load.

### 23.4 BGE Model Cold Start

The first GraphRAG request after process start triggers a BGE model load (~17 s on cold hardware). Subsequent requests use the cached model (< 1 ms). On Azure, Voyage AI is used instead (no model to load).

### 23.5 SQLite WAL Mode

WAL mode allows concurrent reads during a write (ingest). The application is single-writer (ingest is batch, not concurrent), so WAL is used purely for read concurrency during the ingest window.

---

## 24. Deployment and CI/CD

### 24.1 Azure App Service

- **Region:** Italy North
- **Resource group:** `coolattin-rg2`
- **App:** `coolattin-app.azurewebsites.net`
- **Runtime:** Python 3.12 on Linux
- **WSGI:** Gunicorn 4 gthread workers
- **Startup:** `startup.sh` → `gunicorn "create_app:create_app()" --bind 0.0.0.0:8000 --worker-class gthread --threads 4`

### 24.2 CI/CD Pipeline (`azure-deploy.yml`)

GitHub Actions triggered on push to `main`:

```
1. Checkout → OIDC login to Azure (no long-lived secrets in GitHub)
2. Swap requirements.txt → requirements-azure.txt
   (removes torch, sentence-transformers — too large for Oryx build)
3. Zip deploy artifact
4. az webapp deploy (zip deploy)
5. Oryx build inside the container (pip install)
6. az webapp config set --startup-file startup.sh
```

### 24.3 Azure-Specific Configuration

Because `torch` and `sentence-transformers` are excluded from the Azure build:
- `EMBEDDING_PROVIDER=voyage` in production env vars → uses `voyageai.Client` for 1024-dim embeddings
- `VOYAGE_API_KEY` must be set in Azure App Service configuration
- `requirements-azure.txt` excludes the `torch`, `sentence-transformers`, `numpy` (bundled) stack

### 24.4 Environment Variables on Azure

Set via Azure App Service "Configuration" → "Application settings":
- `OPENROUTER_API_KEY` — required for cloud LLM
- `ANTHROPIC_API_KEY` — Claude synthesis (first in chain)
- `VOYAGE_API_KEY` — dense embeddings (Azure production)
- `FLASK_ENV=production`
- `EMBEDDING_PROVIDER=voyage`
- `ADMIN_API_KEY` — admin endpoint guard

### 24.5 Demo Freeze

Git tag `v1.0-demo-freeze` (2026-06-10) pins the evaluation state:
- 75-question evaluation results in `eval_results/eval_graphrag_on.json`
- GraphRAG enrichment evaluation in `eval/graphrag_enrichment_eval.py`
- Canonical configuration documented in `docs/11_demo_freeze.md`

---

## 25. Evaluation Results

### 25.1 Full Regression — 75 Competency Questions (2026-06-10)

Run: `python3 -m backend.services.ask_eval --phase graphrag_on`

| Metric | Result |
|---|---|
| Questions run | 75 |
| Routing accuracy | **89.3%** |
| Aggregation correctness | **100.0%** |
| SQL execution success | **100.0%** |
| Entity label accuracy | **100.0%** |
| Template hit rate | **100.0%** |
| Lane routing accuracy | 72.0% |
| LLM SQL calls required | **0** |
| p50 latency | 372 ms |
| p90 latency | 2,095 ms |
| p95 latency | 4,152 ms |

All 75 questions answered via fast lanes or the semantic layer — the LLM was never invoked for SQL generation.

**Known issues (non-blocking):**
- Honest-refusal rate 0%: G-series out-of-scope questions answered by semantic layer (partial keyword matches). An explicit out-of-scope classifier would fix this.
- Lane routing accuracy 72%: several questions correctly answered as ANALYTICAL but labelled RELATIONAL. SQL result is correct; only the intent label disagrees.

### 25.2 GraphRAG Enrichment (OFF vs ON)

| Metric | Value |
|---|---|
| Cases tested | 9 |
| Numeric delta = 0 | 9/9 (100%) |
| Avg auto-usefulness | 4.4/5 |
| p90 latency overhead | +46 ms |

GraphRAG enrichment is purely additive — SQL aggregates unchanged.

### 25.3 RQ6 SQL vs SPARQL

6 competency questions run; all SPARQL results return 0/empty (co: repository not loaded with data). The comparison framework is complete; data loading is the remaining gap.

---

## 26. Codebase Metrics

| File | Lines | Role |
|---|---|---|
| `ask_service.py` | 10,192 | 7-phase orchestrated LLM pipeline |
| `semantic_layer.py` | 1,185 | Slot-fill compiler, deterministic SQL/SPARQL |
| `extensions.py` | ~400 | DB singleton, schema, migrations |
| `create_app.py` | ~200 | Application factory |
| `graphrag.py` | 573 | In-process GraphRAG engine |
| `workhouse_entity_resolution.py` | ~600 | ER pipeline orchestrator |
| `embedding_index.py` | ~500 | Hybrid TF-IDF + dense retrieval |
| `identity_resolver.py` | ~400 | Three-layer identity model |
| `subgraph_engine.py` | ~520 | VRTI + GraphDB traversal |
| `intent_router.py` | ~140 | Intent classification |
| `config.py` | ~150 | Configuration |

**Total backend:** ~18,000 lines of Python  
**Frontend JS:** ~3,500 lines  
**Templates:** 9 HTML files  
**DB tables:** 17  
**Graph nodes:** 49,081  
**Graph edges:** 64,308  
**Evaluation questions:** 75 (A-series + R-series + C-series + G-series)  
**Confirmed workhouse links:** 140
