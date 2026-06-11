# Coolattin Estate Records Explorer — Full Technical Implementation Report

**Project:** Masters Dissertation  
**Application:** Coolattin Estate Records Explorer  
**Version:** Current (as of June 2026)  
**Author:** Pranjal  
**Stack:** Python 3.12 · Flask · SQLite · GraphDB · SPARQL · LLM (OpenRouter / Ollama) · D3.js · Leaflet.js  

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [System Architecture](#2-system-architecture)
3. [Technology Stack](#3-technology-stack)
4. [Database Design](#4-database-design)
5. [Application Factory & Configuration](#5-application-factory--configuration)
6. [Data Ingestion Pipeline](#6-data-ingestion-pipeline)
7. [Feature: Unified Estate Records Search](#7-feature-unified-estate-records-search)
8. [Feature: Interactive Map](#8-feature-interactive-map)
9. [Feature: Census Explorer](#9-feature-census-explorer)
10. [Feature: Analytics Dashboard](#10-feature-analytics-dashboard)
11. [Feature: Historic Landscape / Heritage Layer](#11-feature-historic-landscape--heritage-layer)
12. [Feature: Natural-Language Ask (LLM Q&A)](#12-feature-natural-language-ask-llm-qa)
13. [Feature: Knowledge Graph Visualiser](#13-feature-knowledge-graph-visualiser)
14. [Feature: GraphDB SPARQL Integration](#14-feature-graphdb-sparql-integration)
15. [Feature: PDF Export](#15-feature-pdf-export)
16. [Feature: Excel Export](#16-feature-excel-export)
17. [Feature: Workhouse Matching](#17-feature-workhouse-matching)
17b. [Feature: Workhouse Entity Resolution (June 2026)](#17b-feature-workhouse-entity-resolution)
18. [Feature: Internationalisation (English / Irish)](#18-feature-internationalisation-english--irish)
19. [API Reference](#19-api-reference)
20. [Frontend Architecture](#20-frontend-architecture)
21. [Security & Data Integrity](#21-security--data-integrity)
22. [Performance Design](#22-performance-design)
23. [RDF / Knowledge Graph Uplift Script](#23-rdf--knowledge-graph-uplift-script)
24. [Deployment & Operations](#24-deployment--operations)
25. [Codebase Metrics](#25-codebase-metrics)
26. [June 2026 Sprint — Orchestrated Pipeline and Identity Resolution](#26-june-2026-sprint)

---

## 1. Project Overview

The **Coolattin Estate Records Explorer** is a full-stack web application built as the centrepiece of a Masters Dissertation in Digital Humanities / Information Technology. It provides a unified, searchable interface over historical records from the **Coolattin Estate**, County Wicklow, Ireland, covering the mid-nineteenth century — one of the most significant periods in Irish history, encompassing the Great Famine (1845–1852), mass emigration, and large-scale estate clearances.

### Historical Context

The Coolattin Estate was the County Wicklow seat of the Fitzwilliam family. In the decade following the Famine, the estate oversaw:

- **6,016 emigrations** (sponsored passage, mainly to Canada) between 1847 and 1856
- **4,108 eviction records** spanning the estate's recorded history
- **5,247 tenancy records** documenting landholding relationships
- Clearances data across **122 townlands** from 1847 to 1856
- Population decline visible through census records from **1827 to 1891**

The application makes these records accessible through multiple interfaces — direct search, map visualisation, analytics, natural-language questions answered by an LLM, and a knowledge graph explorer — enabling both genealogical research and academic historical analysis.

### Core Objectives

| Objective | Implementation |
|-----------|---------------|
| Full-text search over 13,707 estate records | Unified Records Search (SQL + pandas) |
| Spatial visualisation of records | Leaflet.js interactive map with GeoJSON |
| Population trend analysis | Census explorer + analytics dashboard |
| Natural-language Q&A | 7-phase orchestrated LLM pipeline (OpenRouter / Ollama / local BGE) |
| Knowledge graph representation | GraphDB + D3.js force-directed graph |
| SQL vs SPARQL comparison | semantic_layer.py compiles both from same SlotFill; GraphDB integration live |
| Workhouse record linkage | Dedicated ER pipeline with phonetic blocking, fuzzy scoring, confidence bands |
| Person disambiguation | Three-layer identity model (Mention/Person/Factoid) in identity_resolver.py |
| Reproducible academic artefact | SQLite, deterministic ingest, no-ORM |

---

## 2. System Architecture

### High-Level Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  Browser (Vanilla JS · Leaflet.js · D3.js · Chart.js)            │
└────────────────────────┬─────────────────────────────────────────┘
                         │  HTTP / SSE
┌────────────────────────▼─────────────────────────────────────────┐
│  Flask Application  (Python 3.12 · Application Factory Pattern)  │
│                                                                  │
│  Routes Layer   ──── Services Layer ──── Repository Layer        │
│  (Blueprints)        (Business Logic)    (SQL Queries)           │
│                            │                     │               │
│                    ┌───────┼──────────┐           │               │
│                    │       │          │           ▼               │
│               LLM Svc  KG Svc   Census Svc    SQLite DB          │
└────────────────────┬───────┴────┬─────────────────────────────────┘
                     │            │
          ┌──────────▼──┐  ┌──────▼──────────┐
          │  OpenRouter  │  │  GraphDB 10.x   │
          │  (primary)   │  │  SPARQL endpoint│
          │  Ollama      │  │  143,123 triples│
          │  (fallback)  │  └─────────────────┘
          └─────────────┘         │
                                  │ also
                          ┌───────▼──────────┐
                          │  VRTI SPARQL     │
                          │  (external KG)   │
                          └──────────────────┘
```

### Layered Architecture

The application follows a strict three-layer separation:

1. **Route layer** (`backend/routes/*.py`) — thin Flask blueprints. Each blueprint handles one URL prefix. Routes parse request parameters, call a service function, and return JSON or render a template. No business logic lives here.

2. **Service layer** (`backend/services/*.py`) — all business logic. Services orchestrate repository calls, external API calls, caching decisions, and data transformation. The most complex service (`ask_service.py`) is 6,651 lines and implements the full LLM Q&A pipeline.

3. **Repository layer** (`backend/repositories/*.py`) — all SQL. Each repository module exposes typed functions that execute parameterised queries against SQLite. No raw SQL appears in services or routes.

The `extensions.py` singleton provides `get_db_conn()` to the repository layer; every SQLite connection in the entire application passes through this single point, ensuring consistent PRAGMA settings and thread safety.

### Request Flow (typical page load)

```
Browser GET /ask
  → main.py:ask() renders ask.html
  → Browser loads ask.js (cached 24h)

User submits question
  → POST /api/ask/query
  → ask.py:query_endpoint()
  → ask_service.answer_question_stream()
  → yields SSE events:
      initializing → loading_context → running_template_match
      → townland_resolution → [contacting_llm] → querying_database
      → querying_vrti_graph → [querying_graphdb] → preparing_output
      → complete
  → ask.js consumes stream, updates UI progressively
```

---

## 3. Technology Stack

### Backend

| Component | Technology | Version | Purpose |
|-----------|-----------|---------|---------|
| Language | Python | 3.12 | Primary backend language |
| Web framework | Flask | 3.x | HTTP routing, SSE, Jinja2 templating |
| Database | SQLite (raw sqlite3) | 3.x | Local persistence; no ORM |
| HTTP client | requests | 2.x | SPARQL + LLM API calls |
| Data processing | pandas | 2.x | Unified records CSV; workhouse matching |
| Excel export | openpyxl | 3.x | Census data Excel export |
| RDF processing | rdflib | 7.x | In-process SPARQL against TTL file |
| RDF database | GraphDB | 10.x | 143,123-triple SPARQL endpoint |
| LLM (primary) | OpenRouter | API | Cloud LLM (gpt-oss-20b:free + 13 fallbacks) |
| LLM (fallback) | Ollama | — | Local LLM (configurable model) |

### Frontend

| Component | Technology | Version | Purpose |
|-----------|-----------|---------|---------|
| Language | Vanilla JavaScript | ES2020 | All interactive logic; no framework |
| Mapping | Leaflet.js | 1.9 | Interactive map with GeoJSON overlays |
| Graph viz | D3.js | 7 | Force-directed knowledge graph |
| Charting | Chart.js | 4.x | Analytics bar/line/doughnut charts |
| Templating | Jinja2 | 3.x | Server-side HTML rendering |
| Styling | Custom CSS | — | Single `main.css` (no framework) |

### External Services

| Service | Role | Protocol |
|---------|------|----------|
| VRTI (Virtual Record Treasury of Ireland) | Townland metadata + census KG | SPARQL over HTTPS |
| OpenRouter | LLM SQL generation + rewriting | REST (OpenAI-compatible) |
| Ollama | Local LLM fallback | REST |
| GraphDB (local) | Coolattin RDF graph queries | SPARQL + REST |

---

## 4. Database Design

### Schema Overview

The SQLite database (`coolattin.db`) contains four tables, all created and migrated idempotently by `extensions.py::ensure_schema()`. No ORM is used; the design was intentional for transparency and academic reproducibility.

### Table: `townland`

The canonical townland reference table. Populated from two sources: the estate GeoJSON file (primary) and the VRTI Knowledge Graph (enrichment).

```sql
CREATE TABLE townland (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT NOT NULL UNIQUE,
    name_gaelic       TEXT,
    barony            TEXT,
    civil_parish      TEXT,
    electoral_division TEXT,
    placename_theme   TEXT,
    description       TEXT,
    td_id             TEXT,   -- Estate internal ID
    guid              TEXT,   -- Global unique identifier
    area_sqm          REAL,   -- Area in square metres
    kg_uri            TEXT,   -- VRTI Knowledge Graph URI
    wkt_geometry      TEXT,   -- WKT boundary polygon
    centroid_lat      REAL,
    centroid_lon      REAL,
    county            TEXT,
    osm_id            TEXT,   -- OpenStreetMap ID
    osi_id            TEXT,   -- Ordnance Survey Ireland ID
    vrti_id           TEXT,   -- VRTI internal ID
    images_json       TEXT,   -- JSON array of image URLs
    links_json        TEXT,   -- JSON array of external links
    source            TEXT,
    created_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at        DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

**Statistics:** 4,225 townlands (all Wicklow; 152 with full estate data + GeoJSON boundaries).

**Indexes:** `civil_parish`, `barony`, `county`, `kg_uri`

### Table: `census_record`

Population data per townland per year. Sources: VRTI KG (1841–1891 standard census years) and estate surveys (1827, 1839, 1848, 1850, 1860, 1868).

```sql
CREATE TABLE census_record (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    townland_id     INTEGER NOT NULL REFERENCES townland(id),
    year            INTEGER NOT NULL,
    male            INTEGER,
    female          INTEGER,
    total           INTEGER,
    inhabited       INTEGER,   -- Inhabited houses
    uninhabited     INTEGER,   -- Uninhabited houses
    source          TEXT,
    kg_uri          TEXT,
    last_synced_at  DATETIME,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(townland_id, year)
);
```

**Statistics:** 8,033 records across 12 years (1827, 1839, 1841, 1848, 1850, 1851, 1860, 1861, 1868, 1871, 1881, 1891).

**Indexes:** `year`, `(townland_id, year)`

### Table: `clearances_record`

Estate eviction clearance data per townland per year, sourced from the estate GeoJSON file covering the Famine clearances period.

```sql
CREATE TABLE clearances_record (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    townland_id INTEGER NOT NULL REFERENCES townland(id),
    year        INTEGER NOT NULL,
    count       INTEGER,   -- Number of households/persons cleared
    source      TEXT,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(townland_id, year)
);
```

**Statistics:** 1,211 records across 10 years (1847–1856), covering 122 townlands.

**Indexes:** `year`, `(townland_id, year)`

### Table: `refresh_state`

Tracks data freshness for cache invalidation decisions. Each dataset (census, townlands) has one row.

```sql
CREATE TABLE refresh_state (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_key     TEXT NOT NULL UNIQUE,
    last_synced_at  DATETIME,
    source          TEXT,
    query_hash      TEXT,
    record_count    INTEGER,
    export_file     TEXT
);
```

### Table: `ask_query_memory` (runtime-created)

Stores approved query feedback for LLM reuse. Created dynamically by `ask_service.py`.

```sql
CREATE TABLE IF NOT EXISTS ask_query_memory (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    question         TEXT NOT NULL,
    sql_text         TEXT,
    result_summary   TEXT,
    confidence_score REAL,
    approved         INTEGER DEFAULT 0,
    created_at       DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### Table: `unified_record` (runtime-seeded)

A denormalised view of all 13,707 estate records sourced from `unified_processed.csv`. Created at startup by `ask_service._ensure_unified_table_seeded()` as a temporary table for SQL Q&A queries.

Key columns include: `record_id`, `forename`, `surname`, `canonical_name`, `townland`, `townland_norm`, `parish`, `year`, `occupation`, `has_emigration_record` (0/1), `has_eviction_record` (0/1), `has_tenancy_record` (0/1), `acres`, `holding_acres`, `family_size_estimate`, `rent_owed`, `chief_tenant_surname`, `under_tenant_surname`, `is_widow`, `is_canada_destination`, and ~50 additional fields.

### Performance Pragmas

Every connection from `get_db_conn()` applies these SQLite PRAGMA settings for maximum read performance:

```sql
PRAGMA journal_mode=WAL;          -- Write-Ahead Logging (concurrent reads + writes)
PRAGMA synchronous=NORMAL;        -- Balanced durability with WAL
PRAGMA cache_size=-65536;         -- 64 MB page cache
PRAGMA temp_store=2;              -- In-memory temp tables
PRAGMA mmap_size=268435456;       -- 256 MB memory-mapped I/O
PRAGMA foreign_keys=ON;           -- Referential integrity
```

---

## 5. Application Factory & Configuration

### Application Factory (`create_app.py`)

The Flask application is instantiated through the factory pattern. `create_app()` is the only function that registers blueprints, ensuring testability and environment-specific configuration.

```python
def create_app(config_class=None):
    app = Flask(__name__, template_folder="frontend/templates",
                static_folder="frontend/static")
    config_class = config_class or ActiveConfig
    app.config.from_object(config_class)

    init_db(config_class.DATABASE_PATH)
    ensure_schema()

    # Blueprint registration
    app.register_blueprint(main_bp)
    app.register_blueprint(census_bp,   url_prefix="/api/census")
    app.register_blueprint(unified_bp,  url_prefix="/api/unified")
    app.register_blueprint(map_bp,      url_prefix="/api/map")
    app.register_blueprint(townlands_bp,url_prefix="/api/townlands")
    app.register_blueprint(exports_bp,  url_prefix="/api/exports")
    app.register_blueprint(ask_bp,      url_prefix="/api/ask")
    app.register_blueprint(kg_explore_bp, url_prefix="/api/kg")
    _register_legacy_routes(app)
    return app
```

### Configuration Hierarchy (`config.py`)

```
Config (base)
├── DevelopmentConfig   (DEBUG=True, CENSUS_STALE_AFTER_DAYS=7)
└── ProductionConfig    (DEBUG=False, CENSUS_STALE_AFTER_DAYS=1)
```

`ActiveConfig` is resolved at import time from `FLASK_ENV` and consumed by all modules via `from config import ActiveConfig`. All tunable values live here — no scattered constants.

Key configuration values:

| Variable | Default | Purpose |
|----------|---------|---------|
| `SECRET_KEY` | `dev-secret-…` | Flask session security |
| `DATABASE_PATH` | `project_root/coolattin.db` | SQLite file path |
| `VRTI_SPARQL_ENDPOINT` | VRTI production URL | External KG queries |
| `VRTI_REQUEST_TIMEOUT` | 30s | SPARQL query timeout |
| `GRAPHDB_SPARQL_ENDPOINT` | `localhost:7200/…/coolattin` | Local GraphDB |
| `GRAPHDB_ENABLED` | `true` | Enable/disable GraphDB |
| `GRAPHDB_REQUEST_TIMEOUT` | 15s | GraphDB query timeout |
| `CENSUS_STALE_AFTER_DAYS` | 7 (dev) / 1 (prod) | Cache TTL |
| `EXPORTS_DIR` | `project_root/exports/` | PDF + Excel output |
| `SEND_FILE_MAX_AGE_DEFAULT` | 86400 (24h) | Browser cache for static assets |

LLM configuration (environment variables only):

| Variable | Default | Purpose |
|----------|---------|---------|
| `ASK_LLM_PROVIDER` | `auto` | `auto` \| `openrouter` \| `ollama` |
| `OPENROUTER_API_KEY` | (required) | API authentication |
| `OPENROUTER_MODEL` | `openai/gpt-oss-20b:free` | Primary model |
| `OLLAMA_BASE_URL` | `localhost:11434` | Local Ollama server |
| `OLLAMA_MODEL` | configurable | Local model name |

---

## 6. Data Ingestion Pipeline

### Overview

Data flows from three primary sources into the SQLite database through dedicated ingest jobs:

```
Estate GeoJSON  ──────┐
                       ├──→  full_ingest.py  ──→  SQLite (townland, census, clearances)
VRTI KG (SPARQL) ─────┘

unified_processed.csv  ──→  ask_service.py  ──→  SQLite (unified_record, temp)

scripts/rdf_uplift.py  ──→  data/coolattin_sample.ttl  ──→  GraphDB (143,123 triples)
```

### Full Ingest (`backend/jobs/full_ingest.py`)

`run_full_ingest()` is the primary data pipeline. It runs on first startup and can be re-triggered manually.

**Stage 1 — GeoJSON processing:**
Reads `frontend/static/data/townlands.json` (estate-specific GeoJSON with 152 townlands). For each feature, it extracts:
- Canonical name + Irish name
- Estate TD_ID and GUID
- Area in square metres
- Population surveys for years: 1827, 1839, 1848, 1850, 1860, 1868
- Per-year clearances counts for 1847–1856

**Stage 2 — VRTI KG enrichment:**
For each townland identified in Stage 1, the ingest calls `vrti_sparql.fetch_townlands()` and `vrti_sparql.fetch_census_records()`. The SPARQL queries retrieve:
- WKT boundary geometry (polygon) + centroid coordinates
- Civil parish, barony, county hierarchy
- External identifiers: OSM ID, OSI ID, VRTI ID
- Image URLs and external links
- Standard census data: 1841, 1851, 1861, 1871, 1881, 1891 (male, female, inhabited/uninhabited houses)

**Stage 3 — Persistence:**
All data is upserted into `townland`, `census_record`, and `clearances_record` tables via the repository layer. The `refresh_state` table is updated with timestamp and record count.

**Output stats (current):**
- 152 estate townlands processed from GeoJSON
- 4,225 townlands in reference table (from VRTI)
- 8,033 census records (12 years × 1,319 townlands)
- 1,211 clearances records (10 years × 122 townlands)

### Census Ingest (`backend/jobs/census_ingest.py`)

`run_census_ingest(year=None)` performs incremental updates. If `year` is specified, only that year's data is re-fetched from VRTI; otherwise all years are refreshed.

### Unified Record Seeding

The 13,707 unified estate records (stored in `unified_processed.csv`) are loaded into a SQLite temporary table at Ask page startup by `ask_service._ensure_unified_table_seeded()`. This is not a persistent table — it is created fresh each server start from the CSV, which is checked into the git repository as the canonical data source.

---

## 7. Feature: Unified Estate Records Search

### Route
`GET /` (home page) + `GET /api/unified/records`

### What It Does
The primary search interface over all 13,707 unified estate records. Users can filter by surname, forename, townland, year, and estate. Results include all metadata: family composition, land holdings, emigration/eviction/tenancy flags, relationships, occupations, and original source references.

### Implementation

**`backend/services/unified_service.py`**

The CSV is loaded once per process into a pandas DataFrame (`_UNIFIED_CACHE`). Subsequent requests are served entirely from memory, making search sub-millisecond.

```python
def search_records(surname=None, forename=None, townland=None,
                   year=None, estate=None, limit=200):
    df = get_unified()          # cached pandas DataFrame
    mask = pd.Series([True] * len(df))
    if surname:
        mask &= df["surname"].str.upper().str.contains(surname.upper())
    if townland:
        mask &= df["townland_norm"].str.contains(normalize(townland), na=False)
    # ... additional filters ...
    return df[mask].head(limit).to_dict("records")
```

**Frontend (`frontend/static/js/main.js`)**

The search form submits to `/api/unified/records`. Results render in a paginated table. Each row is clickable to open a detail modal showing all 60+ fields, including:
- Family key (groups household members)
- Workhouse match confidence (fuzzy-matched from workhouse register)
- Primary event type badge (Emigration / Eviction / Tenancy)

**Record Type Distribution:**
| Event Type | Records | Notes |
|------------|---------|-------|
| Emigration | 6,016 | Fitzwilliam-sponsored, mainly to Quebec/Toronto |
| Eviction | 4,108 | Court sessions and estate clearances |
| Tenancy | 5,247 | Landholding relationships |
| Total | 13,707 | Some records have multiple flags |

---

## 8. Feature: Interactive Map

### Route
`GET /census` (census page includes map)  
`GET /api/map/layers` — basemap tile layer definitions  
`GET /api/centroids` — townland centroid coordinates  

### What It Does
A Leaflet.js interactive map displaying townland boundaries from the estate GeoJSON, with colour-coded choropleth overlays showing census population, eviction intensity, or emigration rates. Users can click a townland to see a detail panel with all census years and clearances data.

### Implementation

**`backend/services/map_service.py`**

`get_layer_config()` returns four basemap options:
1. **Standard** — OpenStreetMap tiles
2. **Satellite** — Esri World Imagery
3. **Terrain** — OpenTopoMap
4. **Historic OSM** — historical.openstreetmap.org (optional)

`build_centroids()` reads the `townland` table and returns `{name: [lat, lon]}` — used to place clickable markers on the map.

**`frontend/static/data/townlands.json`**

The estate GeoJSON file (152 townland polygons) is served as a static file with a 24-hour browser cache. The file includes all boundary coordinates plus the per-year population surveys and clearances data embedded as feature properties.

**Leaflet.js Layer Control**

The frontend uses Leaflet's native layer control to switch between basemaps and toggle overlays (estate boundaries, historical sites). Boundary fill colour is computed client-side from census population or clearances data embedded in GeoJSON properties.

---

## 9. Feature: Census Explorer

### Routes
```
GET  /census                    → census.html
GET  /api/census/               → paginated census records
GET  /api/census/townland?name= → full history for one townland
GET  /api/census/summary        → aggregate stats by year
POST /api/census/refresh        → force KG re-ingestion
GET  /api/census/export/latest  → latest export info
POST /api/census/export/regenerate → regenerate Excel
```

### What It Does
The census explorer shows population data for Coolattin Estate townlands across twelve survey years (1827–1891). Users can filter by year, townland, or barony, and see the population trajectory — the dramatic decline from pre-Famine to post-Famine years is directly visible in the data.

### DB-First / KG-Second Caching Strategy

This is the canonical implementation of the application's caching pattern:

```
Request arrives
    │
    ▼
Check refresh_state table:
    │   is last_synced_at within TTL?
    │
    ├─ YES (fresh) ──→ query census_record table ──→ return {cache_status: "hit"}
    │
    ├─ YES (stale) ──→ return DB data + queue background refresh
    │                           {cache_status: "stale_refresh"}
    │
    └─ NO (miss)  ──→ query VRTI SPARQL ──→ persist to DB
                     ──→ generate Excel ──→ return fresh data
                           {cache_status: "miss", source: "kg_refresh"}
```

The TTL is 7 days in development and 1 day in production, configurable via `CENSUS_STALE_AFTER_DAYS`.

**Response Envelope:**
```json
{
  "data": [{ "townland": "Aghowle", "year": 1841, "total": 312, "male": 158, "female": 154 }],
  "meta": {
    "source": "database",
    "cache_status": "hit",
    "generated_at": "2026-05-18T12:00:00Z",
    "record_count": 8033,
    "export_file": "exports/census_wicklow_all_20260518.xlsx"
  }
}
```

---

## 10. Feature: Analytics Dashboard

### Route
`GET /analytics`

### What It Does
A pluggable analytics dashboard showing KPIs and charts for each dataset (emigrations, evictions, tenancies, townland geography, unified overview). Each panel shows key performance indicators and interactive Chart.js visualisations.

### Pluggable Module Architecture

The analytics system is designed for extensibility. Every analytics dataset is an independent Python module following the `AnalyticsModule` Protocol defined in `analytics/base.py`.

**Protocol definition:**
```python
class AnalyticsModule(Protocol):
    dataset_id:   str
    dataset_name: str
    description:  str

    def compute(self) -> AnalyticsResult: ...
```

**Adding a new analytics module** requires only:
1. Creating `analytics/my_dataset.py` with a `MODULE` object
2. No registration step — `analytics/registry.py` auto-discovers via `importlib`

**Currently auto-discovered modules:**

| Module File | Dataset ID | Key KPIs / Charts |
|-------------|------------|-------------------|
| `emigrations.py` | `emigration_stats` | Total emigrants, year breakdown, top townlands, Canada vs other |
| `evictions.py` | `evictions_stats` | Total evictions, per-year trend, clearances breakdown |
| `tenancies.py` | `tenancy_stats` | Tenancy records, family size distribution, holding acres |
| `townland_geo.py` | `townland_geography` | Townland count, area distribution, parish breakdown |
| `unified.py` | `unified_overview` | Total records, surname frequency, coverage metrics |

**KPI and Chart dataclasses:**
```python
@dataclass
class KPI:
    label: str
    value: str | int | float
    hint:  str | None = None

@dataclass
class Chart:
    chart_id: str
    title:    str
    type:     Literal["bar", "line", "doughnut"]
    data:     dict        # Chart.js data format
    options:  dict | None = None
```

---

## 11. Feature: Historic Landscape / Heritage Layer

### Route
`GET /heritage`

### What It Does
A dedicated map view showing archaeological and heritage features across the Coolattin Estate area — holy wells, ring forts, historic sites from the Archaeological Survey of Ireland, and historical landscape overlays.

### Implementation

**`frontend/static/js/heritage.js`**

Loads multiple GeoJSON overlays from static files:
- `holywells_wicklow.geojson` — Holy well sites
- `asi_wicklow.geojson` — Archaeological Survey of Ireland features (ring forts, souterrains, etc.)

Each feature layer is independently togglable via Leaflet's layer control. Clicking a feature opens a popup with the feature's name, period, type, and OSI/ASI reference number.

**Heritage seeding for Ask page:**

`ask_service._ensure_heritage_feature_seeded()` creates temporary SQLite tables `holy_wells` and `ring_forts` from the same GeoJSON files. This allows the LLM to answer questions like "how many holy wells are within 5 km of Aghowle?" using SQL with a custom `distance_km()` SQLite function.

---

## 12. Feature: Natural-Language Ask (LLM Q&A)

### Route
`POST /api/ask/query` (Server-Sent Events stream)

### What It Does
The Ask page accepts a free-text historical research question in English, processes it through a multi-stage pipeline, and returns a precise, data-backed answer. The pipeline combines rule-based SQL template matching (fast path, no LLM required) with LLM-generated SQL (slow path), VRTI and GraphDB enrichment, natural-language rewriting, and PDF report generation.

### Pipeline Architecture

The pipeline runs inside `ask_service.answer_question_stream()` and yields SSE events at each stage so the user sees live progress:

```
Stage 1: initializing
Stage 2: loading_context      — seed unified_record + heritage tables
Stage 3: running_template_match — keyword-scored SQL template search
Stage 4: townland_resolution   — exact → fuzzy → "did you mean?"
Stage 5: [contacting_llm]      — if no template matched
Stage 6: [framing_query]       — LLM generating SQL
Stage 7: querying_database     — execute SQL against SQLite
Stage 8: querying_vrti_graph   — VRTI SPARQL enrichment (parallel)
Stage 9: [querying_graphdb]    — GraphDB SPARQL query (if enabled)
Stage 10: preparing_output     — LLM rewrite + GraphDB comparison
Stage 11: complete             — final result JSON
```

Each stage emits a JSON-encoded SSE event:
```
data: {"type": "stage_update", "stage": "querying_database", "detail": "Executing SQL..."}

data: {"type": "complete", "answer": "...", "sql": "SELECT...", "columns": [...], ...}
```

### Stage 3: Template Matching

The fast path avoids LLM calls entirely for common research questions. `_match_and_build_template()` ranks 100+ pre-verified SQL templates using `difflib.SequenceMatcher` keyword scoring.

**Example templates:**
- "How many people emigrated?" → `SELECT COUNT(*) … WHERE has_emigration_record=1`
- "What townlands had the most evictions?" → `SELECT townland, COUNT(*) … GROUP BY townland ORDER BY`
- "List people with surname [X]" → `SELECT … WHERE UPPER(surname)=UPPER(?)`
- "What was the population of [townland] in [year]?" → join on census_record

If template confidence exceeds threshold, the answer is returned instantly — typical latency under 100ms.

**Verified Analysis Templates** are a separate set of 15 high-confidence research queries for academic analysis:
- `tenant_land_gender_average` — Average land holding by gender
- `widows_with_children_proportion` — Widows as proportion of emigrants
- `emigration_population_townland_trend` — Cross-join emigration with census population
- `clearances_eviction_comparison` — Clearances vs eviction record correlation
- Each includes a `chart_hint` (`bar`, `line`, `doughnut`) for frontend visualisation.

### Stage 4: Townland Resolution

A three-step resolution process for townland references in the question:
1. **Exact match:** `UPPER(name) = UPPER(input)` in townland table
2. **Fuzzy match:** `difflib.SequenceMatcher` ratio ≥ 0.72
3. **Alias lookup:** `townland_aliases.json` maps variant spellings to canonical names

If confidence is below 0.85, a "did you mean?" suggestion is added to the result. If no townland is found but the question seems townland-specific, alternative suggestions are returned.

### Stage 5–6: LLM SQL Generation

When no template matches, `_generate_sql_with_llm()` is called. The prompt is built by `_build_sql_prompt()` and includes:

- Live SQLite schema with row counts and sampled categorical values (`_live_sqlite_schema_prompt_block()`)
- Previously approved query examples from `ask_query_memory` (semantic similarity ranking)
- Mandatory rules (enforced via prompt engineering):
  - `COUNT(DISTINCT record_id)` for person counts
  - `NEVER use GROUP_CONCAT` — return one row per person
  - Person lists: `LIMIT 50`
  - Townland filtering: `townland_norm='NAME'` or `UPPER(t.name)='NAME'`
  - No hallucinated column names

The LLM generation uses a 3-attempt retry loop with semantic repair on failure.

**LLM Provider Selection (`_llm_generate()`):**
```
ASK_LLM_PROVIDER=auto:
  1. Check OPENROUTER_API_KEY → try OpenRouter (primary)
  2. If OpenRouter fails → try next free model from _OPENROUTER_FREE_MODELS list (14 models)
  3. If all OpenRouter models fail → fall back to Ollama
  4. If Ollama unreachable → return error
```

Free OpenRouter models tried in order:
- openai/gpt-oss-20b:free
- meta-llama/llama-3.3-70b-instruct:free
- google/gemma-3-27b-it:free
- deepseek/deepseek-r1-distill-llama-70b:free
- (and ~10 more)

### Stage 7: SQL Safety Guardrails

Before any SQL is executed, `_is_safe_sql()` checks against a regex pattern that blocks all write operations:

```python
FORBIDDEN_SQL = re.compile(
    r'\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|TRUNCATE'
    r'|PRAGMA(?!\s+table_info)|ATTACH|DETACH|VACUUM|REINDEX)\b',
    re.IGNORECASE
)
```

Only `SELECT` and `WITH` (CTEs) are permitted.

### Stage 8: VRTI Graph Enrichment

Parallel to database execution, `_fetch_vrti_kg_context()` calls the VRTI SPARQL endpoint to enrich the answer with:
- Civil parish and barony for the resolved townland
- Historical population context (KG URI, VRTI identifiers)
- Related townlands in the same parish

This enrichment is used in the LLM rewrite prompt and displayed in the "Data Provenance" section of the Ask UI.

### Stage 9: GraphDB SPARQL Query (D8 Prototype)

When `GRAPHDB_ENABLED=true`, the pipeline also runs the equivalent SPARQL query against the local GraphDB instance (143,123 triples). This enables side-by-side comparison of SQL vs SPARQL results in the Ask page.

**SPARQL generation uses three steps:**

1. **Rule-based template matching** (`_match_sparql_template()`) — handles ~15 common question patterns with hard-coded, verified SPARQL
2. **LLM generation** (if no template) — prompt includes the Coolattin RDF ontology schema and a `FORBIDDEN` properties list (SQLite column names that must never appear as RDF predicates)
3. **Post-validation** (`_sparql_uses_forbidden_props()`) — if banned properties detected, fall back to template or generic listing

**Mismatch explanation:** If SQLite and GraphDB return different result counts or values, `_explain_result_mismatch()` calls the LLM with both queries and result samples to generate a structured academic explanation of the discrepancy (closed-world vs open-world semantics, NULL handling differences, etc.).

### Stage 10: LLM Rewrite

`_generate_rephrased_answer()` takes the raw SQL result and rewrites it into 1–3 sentences of plain English. The prompt (`_build_rephrase_prompt()`) enforces:
- Use ONLY supplied data (no hallucination)
- Keep all numbers identical to actual results
- For results with >10 rows: state count + 1–2 examples, do not enumerate all
- No markdown, bullets, SQL, or preamble

Individual cell values are pre-truncated to 300 characters before being sent to the LLM (`_build_llm_data_context._truncate_row()`), preventing prompt inflation from large list-type results.

### Query Memory (Learning from Feedback)

Users can rate answers with thumbs-up/down via `POST /api/ask/feedback`. Approved (thumbs-up) queries are stored in `ask_query_memory` with the SQL text, result summary, and confidence score.

`_find_similar_approved_queries()` uses `_memory_similarity_score()` (trigram + SequenceMatcher hybrid) to find previously approved queries similar to the current question. These are injected into the LLM SQL prompt as examples, improving accuracy over time.

---

## 13. Feature: Knowledge Graph Visualiser

### Route
`GET /explore-knowledge` → `kg_explore.html`  
`GET /api/kg/graph` → full graph topology  
`GET /api/kg/townland/<name>` → person drill-down  

### What It Does
An interactive D3.js force-directed graph visualising the **entire Coolattin Estate database** as a knowledge graph. The graph represents all 13,707 estate records aggregated across 516 townlands, grouped under 20 civil parishes, and connected to event hubs (Emigration, Eviction, Tenancy), a Census Records data hub, and a Clearances data hub. Clicking any townland loads its individual person records on demand.

### Graph Architecture

**Node types:**

| Type | Count | Visual | Size |
|------|-------|--------|------|
| EventHub | 3 | Fixed at canvas corners | 22px radius |
| DataHub | 2 | Fixed at canvas corners | 18px radius |
| Parish | 20 | Purple (#7c3aed) | 16px radius |
| Townland | 516 | Colour = dominant event | 8–22px (scaled by records) |

**Edge types:**

| Type | Count | Meaning |
|------|-------|---------|
| `parish_townland` | 178 | Parish contains Townland |
| `townland_event` | 820 | Townland → EventHub (has events of type) |
| `townland_census` | 147 | Townland → Census hub (has population data) |
| `townland_clearances` | 109 | Townland → Clearances hub (has clearances data) |
| **Total** | **1,254** | — |

**Hub fixed positions** (D3 `fx/fy`):
- Emigration hub: top-left (12% W, 18% H)
- Eviction hub: top-right (88% W, 18% H)
- Tenancy hub: bottom-centre (50% W, 88% H)
- Census hub: bottom-left (12% W, 82% H)
- Clearances hub: bottom-right (88% W, 82% H)

**Townland node colour coding:**
- Green (#15803d) — emigration-dominant (emigrant_count ≥ eviction_count AND ≥ tenancy_count)
- Red (#b91c1c) — eviction-dominant
- Blue (#1d4ed8) — tenancy-dominant

**Townland node size scaling:**
```javascript
size = Math.min(8 + total_records / 30, 22)
// Carnew (550 records) → size 22  
// small townland (10 records) → size 8
```

### Backend: Graph Builder (`kg_service._build_graph_inner()`)

The graph is built from four SQLite queries:

**Query 1** — aggregated stats per townland:
```sql
SELECT townland,
       COUNT(*)                      AS total_records,
       SUM(has_emigration_record)    AS emigrant_count,
       SUM(has_eviction_record)      AS eviction_count,
       SUM(has_tenancy_record)       AS tenancy_count
FROM unified_record
WHERE townland IS NOT NULL AND townland != ''
GROUP BY townland
ORDER BY total_records DESC
```

**Query 2** — parish mapping (from unified_record.parish, filtering noise):
Townland → parish from `unified_record.parish` (most common clean value per townland). Bad values filtered: `"?"`, slash-separated strings, "Co Wexford", "County Wexford", values under 3 characters.

**Query 3** — fallback enrichment from townland table:
For townlands not matched by Query 2, a UPPER-key lookup into the `townland` reference table provides `civil_parish`, `barony`, `county`.

**Query 4 + 5** — census and clearances membership:
`SELECT DISTINCT townland_id FROM census_record` and `clearances_record` are used to determine which townland nodes connect to the data hubs.

The result is cached in `_GRAPH_CACHE` (process-level singleton) for the lifetime of the server process.

### D3.js Force Simulation

The simulation uses differentiated force parameters to create a semantically meaningful layout:

```javascript
d3.forceLink(edges)
  .distance(e => {
    parish_townland:    55,   // parishes close to their townlands
    townland_event:    110,   // townlands pulled toward relevant hubs
    townland_census:   130,   // data hubs at distance
    townland_clearances: 130
  })
  .strength(e => {
    parish_townland: 0.55,    // strong parish grouping
    townland_event:  0.25,
    default:         0.15
  })

d3.forceManyBody().strength(d => {
  EventHub: -900,   // strong repulsion keeps hubs apart
  DataHub:  -700,
  Parish:   -350,
  Townland:  -45    // mild repulsion between townlands
})
```

`alphaDecay(0.022)` gives the simulation longer to settle, producing a cleaner final layout.

### On-Demand Person Drill-Down

Clicking a Townland node triggers `showTownlandDetail(d)`, which asynchronously calls:
```
GET /api/kg/townland/{name}
```

This route calls `kg_service.get_townland_persons(townland_name, limit=50)`, which returns up to 50 persons with name, year, event type, and occupation. The detail card shows:
- Aggregated stats (emigrant/eviction/tenancy counts)
- Parish, barony, county
- Scrollable person list with event-type colour badges

---

## 14. Feature: GraphDB SPARQL Integration

### Technical Background

The Coolattin estate data was uplifted to RDF using a custom ontology and loaded into a local GraphDB 10.x instance. The repository contains **143,123 triples** covering:
- `co:Person` instances (one per estate record)
- `co:Event` instances (linked to persons via `co:hasEvent`)
- `co:townland`, `co:year`, `co:eventType` properties
- `schema:givenName`, `schema:familyName` (Schema.org vocabulary)

**Custom ontology prefixes:**
```sparql
PREFIX co:     <https://coolattin.ie/ontology#>
PREFIX ex:     <https://coolattin.ie/resource/>
PREFIX schema: <https://schema.org/>
```

### GraphDB Client (`backend/integrations/graphdb_sparql.py`)

This is the **only module** that communicates with GraphDB. All other modules call `graphdb_sparql.query()` or `graphdb_sparql.probe()`.

**Critical implementation note — POST not GET:**
The GraphDB instance stalls indefinitely on GET requests (connection starts but response never completes). All SPARQL queries use `POST` with `application/x-www-form-urlencoded` encoding per SPARQL 1.1 Protocol §2.1.3:

```python
resp = requests.post(
    endpoint,
    data={"query": full_query},
    headers={
        "Accept": "application/sparql-results+json",
        "Content-Type": "application/x-www-form-urlencoded",
    },
    timeout=timeout,
)
```

**Probe mechanism (GraphDB REST /size):**
`probe()` and `triple_count()` use the GraphDB REST `/size` endpoint instead of a SPARQL query. This returns a plain-text integer (triple count) instantly without SPARQL overhead and without the GET stalling issue:

```python
def _size_endpoint() -> str:
    return ActiveConfig.GRAPHDB_SPARQL_ENDPOINT.rstrip("/") + "/size"

def probe() -> bool:
    resp = requests.get(_size_endpoint(), timeout=5)
    return resp.status_code == 200
```

**Safe fallback:** All GraphDB calls wrap exceptions and return `([], [])` on connection error, timeout, or any exception — the application degrades gracefully when GraphDB is unavailable.

**Forbidden properties validation:**
The LLM sometimes confuses SQLite column names with RDF predicates. `_SPARQL_FORBIDDEN_PROPS` contains banned property names (e.g., `co:hasEmigrationRecord`, `co:totalFamilySize`). `_sparql_uses_forbidden_props()` validates generated SPARQL before execution and triggers a fallback if banned properties are detected.

### Comparison Scenarios

Four pre-defined scenarios enable SQL vs SPARQL comparison in the KG Explore page:

| Scenario | SQL | SPARQL | Key Difference |
|----------|-----|--------|---------------|
| Emigration by townland | `GROUP BY townland` in unified_record | `co:hasEvent` traversal → GROUP BY | SQL NULLs excluded by WHERE; SPARQL naturally excludes unbound |
| Evictions per year | `WHERE has_eviction_record=1 AND year IS NOT NULL` | `?ev co:eventType "eviction"` | 4 NULL years cause SQL=38 rows if excluded vs SPARQL=38 rows always |
| Top 10 surnames | `GROUP BY surname ORDER BY COUNT DESC` | `schema:familyName GROUP BY` | Usually identical — cleanest parity example |
| Person + event detail | CASE WHEN for event_type | `?ev co:eventType` graph walk | Graph walk vs relational join pattern |

---

## 15. Feature: PDF Export

### Route
`GET /api/ask/pdf/<filename>`

### What It Does
After every Ask pipeline run, a PDF report is generated and made available for download. The report includes the original question, the SQL query used, the data table, the LLM-rewritten natural-language answer, VRTI enrichment context, townland information, and LLM metadata.

### Implementation

The PDF is generated **without any PDF library** — it is hand-written binary PDF 1.4 format in `ask_service._write_pdf_report()`. This was a deliberate design decision to eliminate a large dependency and maintain full control over formatting.

The PDF writer:
1. Opens with PDF 1.4 header (`%PDF-1.4`)
2. Writes a catalog, pages tree, and font object (Helvetica)
3. Constructs a single content stream with `BT ... ET` text blocks
4. Line-wraps text at 80 characters using `_wrap_line()`
5. Stores XRef table and trailer

**File naming convention:** `exports/ask/ask_report_YYYYMMDD_HHMMSS.pdf`

**PDF content sections:**
- Header: "Coolattin Archive – Ask Report" + UTC timestamp
- Question (as submitted)
- Actual data answer (raw SQL result summary)
- LLM rephrased answer (natural language)
- SQL LLM metadata (provider, model, mode)
- Rewrite LLM metadata
- SQLite SQL query text
- Local results table (formatted, per-row)
- VRTI graph context (townland URIs, parish, barony)
- VRTI PostgreSQL SQL (if available)

---

## 16. Feature: Excel Export

### Routes
```
GET  /api/exports/census/latest      → latest export metadata
GET  /api/exports/census/download    → download .xlsx file
POST /api/census/export/regenerate   → regenerate from DB
```

### What It Does
Census data can be exported as a structured Excel file using openpyxl. The export includes all census records with townland metadata, plus a separate metadata sheet documenting the source, query scope, and generation time.

### Implementation (`backend/services/export_service.py`)

`export_census(records, scope, extra_meta)` creates a workbook with two sheets:
- **Data sheet:** columns = townland, year, male, female, total, inhabited, uninhabited, source, kg_uri
- **Metadata sheet:** source, generated_at, query_scope, record_count, columns

Files are saved to `exports/census_wicklow_<scope>_YYYYMMDD_HHMMSS.xlsx` and their path is stored in `refresh_state` for tracking.

---

## 17. Feature: Workhouse Matching

### Route
`GET /api/workhouse/match/<record_id>`

### What It Does
Each unified estate record can be cross-referenced against workhouse admission and discharge registers. The matching algorithm attempts to identify the same person in both the estate records and workhouse registers, providing a richer picture of what happened to evicted or emigrating families.

### Matching Algorithm (`backend/services/workhouse_service.py`)

The algorithm uses a **place-first, date-windowed fuzzy matching** strategy:

**Stage 1 — Place filtering:**
Filter workhouse records where `electoral_division` matches the estate record's townland or parish name (normalised, case-insensitive).

**Stage 2 — Date window:**
Within place candidates, retain only records within ±1 year of the estate record's year.

**Stage 3 — Name scoring:**
Apply `difflib.SequenceMatcher` to the concatenated `forename + surname` strings.

**Stage 4 — Occupation bonus:**
If both records have occupations, check for shared keywords (labourer, farmer, servant, widow, etc.). Match → +0.05 bonus on score.

**Confidence bands:**
| Band | Criteria |
|------|----------|
| High | Place match AND date match AND score ≥ 0.80 |
| Medium | (Place OR date match) AND score ≥ 0.60 |
| Low | Score ≥ 0.60, no place or date match |

**Fallback:** If no place+date candidates exist, score all workhouse records by name only (prevents zero results for records with unusual townlands).

**Data source:** `frontend/static/data/workhouse_data_final.xlsx` — two sheets ("1-127" and "from 128") loaded once into pandas at startup.

---

## 18. Feature: Internationalisation (English / Irish)

### Implementation (`frontend/static/js/i18n.js`)

The application supports two languages: English (default) and Irish (Gaeilge). A lightweight client-side i18n framework handles all translations without a server round-trip.

**Mechanism:**
- HTML elements with `data-i18n="key"` attribute are auto-translated on page load
- `i18n.init(lang)` loads the translation dictionary for the selected language
- `i18n.translate(key)` returns the translated string
- Language toggle button in the navigation bar switches between EN/GA

**Translation coverage:** All navigation items, section headings, CTA buttons, form labels, and key UI strings have Irish translations.

---

## 19. API Reference

### Page Routes (Blueprint: `main`)

| Method | Path | Response |
|--------|------|----------|
| GET | `/` | index.html |
| GET | `/about` | about.html |
| GET | `/analytics` | analytics.html |
| GET | `/census` | census.html |
| GET | `/info` | info.html |
| GET | `/ask` | ask.html |
| GET | `/heritage` | heritage.html |
| GET | `/explore-knowledge` | kg_explore.html |

### Census API (`/api/census`)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/` | Paginated census records with filters (`year`, `townland`, `barony`, `page`, `limit`) |
| GET | `/records` | Backward-compat alias |
| GET | `/townlands` | List of townlands with census data |
| GET | `/summary` | Aggregate stats by year |
| GET | `/townland?name=` | Full history for one townland |
| POST | `/refresh` | Force KG re-ingestion |
| GET | `/export/latest` | Latest export metadata |
| POST | `/export/regenerate` | Re-generate Excel from DB |

### Unified Records API (`/api/unified`)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/records` | Search records (`surname`, `forename`, `townland`, `year`, `estate`, `limit`) |
| GET | `/stats` | Record counts and field coverage |
| GET | `/townlands` | Unique townlands with record counts |
| GET | `/surnames` | Unique surnames with record counts |
| GET | `/surname-suggest?q=` | Autocomplete surnames |

### Ask API (`/api/ask`)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/query` | SSE pipeline stream; body: `{question, townland_hint?, show_sql?, force_llm?}` |
| POST | `/feedback` | Record thumbs-up/down with metadata |
| GET | `/llm-status` | LLM health check |
| GET | `/townland-suggest?q=` | Townland autocomplete |
| GET | `/pdf/<filename>` | Download PDF report |

### Knowledge Graph API (`/api/kg`)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/graph` | Graph nodes + edges for D3.js |
| GET | `/scenarios` | Canned SQL vs SPARQL comparison scenarios |
| POST | `/compare` | Execute scenario or custom SQL/SPARQL |
| POST | `/explain-mismatch` | LLM explanation of result differences |
| GET | `/graphdb-status` | Live GraphDB connectivity check |
| GET | `/townland/<name>` | Person records for a specific townland |
| GET | `/rdf-status` | TTL file health + triple count |

### Townlands API (`/api/townlands`)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/` | All townlands (optional `?county=` filter) |
| GET | `/wicklow` | Wicklow townlands |
| POST | `/refresh` | Force KG refresh |

### Map API (`/api/map`)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/layers` | Basemap tile layer definitions |
| GET | `/centroids` | Townland centroid coordinates |

### Exports API (`/api/exports`)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/census/latest` | Latest census export metadata |
| GET | `/census/download` | Download .xlsx file |

---

## 20. Frontend Architecture

### Design Philosophy

The frontend is written in **vanilla JavaScript with no framework** (no React, Vue, or Angular). This was a deliberate choice to:
- Minimise dependency surface for long-term reproducibility
- Keep the codebase simple and auditable for academic submission
- Avoid build tooling (no webpack, no transpilation)
- Ensure the application runs from `python app.py` with zero frontend build steps

### JavaScript File Structure

| File | Lines | Purpose |
|------|-------|---------|
| `ask.js` | ~530 | Ask page SSE stream consumer, result rendering, feedback |
| `main.js` | ~800 | Home page records search, modal, workhouse, glossary |
| `kg_explore.js` | ~360 | D3.js force simulation, node detail, search |
| `heritage.js` | ~200 | Heritage layer Leaflet map |
| `census.js` | ~300 | Census explorer map + sidebar |
| `analytics.js` | ~150 | Analytics dashboard Chart.js rendering |
| `i18n.js` | ~100 | EN/GA internationalisation |
| `map.js` | ~150 | Basemap layer control |

### ask.js: SSE Stream Consumption

```javascript
async function submitQuestion() {
    const resp = await fetch("/api/ask/query", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ question, townland_hint, show_sql })
    });
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const text = decoder.decode(value);
        // Parse "data: {...}\n\n" lines
        for (const line of text.split("\n")) {
            if (line.startsWith("data: ")) {
                const event = JSON.parse(line.slice(6));
                handleStreamEvent(event);
            }
        }
    }
}
```

Each `handleStreamEvent()` call updates a specific UI section based on `event.type` (stage_update, complete, error).

### renderTable() — Long Cell Value Handling

A known issue in historical data: GROUP_CONCAT or aggregated queries can produce very long comma-separated cell values. `renderTable()` detects these and renders them as collapsible lists:

```javascript
function renderCell(val) {
    const raw = String(val ?? "");
    if (raw.length > 200 && raw.includes(",")) {
        const parts = raw.split(",").map(s => s.trim()).filter(Boolean);
        const preview = parts.slice(0, 5).map(escapeHtml).join(", ");
        const rest = parts.length - 5;
        return `<td>
          <span>${preview}</span>
          <span style="color:#94a3b8;">…and ${rest} more</span>
          <details><summary>Show all ${parts.length}</summary>
            <div>${parts.map(escapeHtml).join("<br>")}</div>
          </details>
        </td>`;
    }
    return `<td>${escapeHtml(raw)}</td>`;
}
```

### Static File Caching

All static files (GeoJSON, CSS, JavaScript) are served with a 24-hour `Cache-Control` header configured via `SEND_FILE_MAX_AGE_DEFAULT = 86400`. JavaScript files are cache-busted with `?v=N` query parameters in templates (e.g., `ask.js?v=14`).

---

## 21. Security & Data Integrity

### SQL Injection Prevention

All user-provided values are passed as parameterised query arguments to SQLite (never string-interpolated). Additionally, the LLM-generated SQL is validated by a strict regex before execution:

```python
FORBIDDEN_SQL = re.compile(
    r'\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|TRUNCATE'
    r'|PRAGMA(?!\s+table_info)|ATTACH|DETACH|VACUUM|REINDEX)\b',
    re.IGNORECASE
)
```

Only `SELECT` statements and CTEs (`WITH ... SELECT`) pass the guardrail.

### XSS Prevention

All user-supplied values rendered in the frontend pass through `escapeHtml()`:
```javascript
function escapeHtml(s) {
    return String(s ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
}
```

All innerHTML assignments use only escaped values.

### Read-Only Database Architecture

The application has no routes that write to the database from user input. All writes happen through:
- Ingest jobs (run manually or at startup)
- Feedback recording (own table, no user-controlled columns)
- Cache refresh (triggered explicitly, writes only to well-defined tables)

### API Key Isolation

`OPENROUTER_API_KEY` and other secrets are loaded from `.env.local` (gitignored). The `.env.example` file documents all required variables with safe placeholder values. No secrets appear in the codebase.

---

## 22. Performance Design

### Database-Level Optimisations

| Optimisation | Implementation | Benefit |
|-------------|----------------|---------|
| WAL mode | `PRAGMA journal_mode=WAL` | Concurrent reads during writes |
| Page cache | `PRAGMA cache_size=-65536` (64 MB) | Avoids repeated disk reads |
| Memory-mapped I/O | `PRAGMA mmap_size=268435456` (256 MB) | OS-level page cache bypass |
| Covering indexes | `(townland_id, year)` composite | Avoids table scans in census queries |
| Temp in memory | `PRAGMA temp_store=2` | Sort + aggregate without temp files |

### Application-Level Caching

| Cache | Location | Mechanism | Contents |
|-------|----------|-----------|---------|
| Unified records | `unified_service._UNIFIED_CACHE` | Process-level variable | 13,707-row pandas DataFrame |
| Centroids | `unified_service._CENTROIDS_CACHE` | Process-level variable | `{name: (lat, lon)}` dict |
| Workhouse index | `workhouse_service._MATCH_INDEX` | Process-level variable | Place-first inverted index |
| RDF graph | `kg_service._RDF_GRAPH` | Thread-locked singleton | rdflib Graph object |
| KG topology | `kg_service._GRAPH_CACHE` | Thread-locked singleton | D3.js nodes+edges dict |
| Census data | `refresh_state` + SQLite | DB-first with TTL | Paginated census results |

### Template Matching (Fast Path)

The most impactful performance optimisation for the Ask page: 100+ SQL templates matched by keyword scoring completely bypass the LLM. Response time without LLM: **< 100ms**. Response time with LLM: 3–15 seconds (dependent on provider). Template matching succeeds for ~60% of common research questions in testing.

### Browser Caching

Static files (GeoJSON, CSS, JS) are served with 24-hour `Cache-Control` max-age. The 6.2 MB `townlands.json` GeoJSON file is particularly important to cache — without it, every page load would cost significant bandwidth.

---

## 23. RDF / Knowledge Graph Uplift Script

### Script: `scripts/rdf_uplift.py`

This script converts `unified_processed.csv` into a Turtle (`.ttl`) RDF file using a custom Coolattin ontology, and optionally loads it directly into GraphDB.

**Usage:**
```bash
python3 scripts/rdf_uplift.py                   # Generate TTL only
python3 scripts/rdf_uplift.py --import          # Generate + POST to GraphDB
python3 scripts/rdf_uplift.py --limit 500       # Sample 500 rows (development)
python3 scripts/rdf_uplift.py --repo my-repo    # Custom GraphDB repository name
```

**Output:** `data/coolattin_sample.ttl`

### Ontology Design

**Namespace prefixes:**
```
co:     https://coolattin.ie/ontology#    (Coolattin domain ontology)
ex:     https://coolattin.ie/resource/    (instance URIs)
schema: https://schema.org/               (standard person properties)
xsd:    http://www.w3.org/2001/XMLSchema#
rdf:    http://www.w3.org/1999/02/22-rdf-syntax-ns#
rdfs:   http://www.w3.org/2000/01/rdf-schema#
```

**Classes:**
- `co:Person` — estate record subject
- `co:Event` — discrete event (emigration, eviction, or tenancy)

**Properties:**
- `schema:givenName`, `schema:familyName` — person name components
- `co:townland` — townland name (string)
- `co:estate` — estate name
- `co:hasEvent` — person → event link
- `co:forPerson` — event → person back-link
- `co:eventType` — "emigration" | "eviction" | "tenancy"
- `co:year` — year (xsd:integer)

**Instance URI pattern:**
- Person: `ex:person_{record_id}`
- Event: `ex:event_{record_id}`

**Current GraphDB load:** 143,123 triples (full dataset)

**SPARQL verification example:**
```sparql
SELECT (COUNT(DISTINCT ?person) AS ?emigrantCount)
WHERE {
  ?person a co:Person ;
          co:hasEvent ?event .
  ?event co:eventType "emigration" .
}
# Returns: 6016 (matches SQLite: SELECT COUNT(*) WHERE has_emigration_record=1)
```

---

## 24. Deployment & Operations

### Starting the Application

```bash
# 1. Create and activate virtual environment
python3 -m venv venv && source venv/bin/activate

# 2. Install dependencies (75 Python files; key: flask, requests, pandas, openpyxl, rdflib)
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env.local
# Set OPENROUTER_API_KEY for LLM features
# Set GRAPHDB_ENABLED=false if not running GraphDB locally

# 4. Start application
python3 app.py
# → http://127.0.0.1:5001
```

The database is created automatically on first run. To populate with live data:
```bash
python -m backend.jobs.full_ingest
```

### Starting GraphDB (local)

GraphDB must be running on port 7200 with a repository named `coolattin`:
```bash
# Start GraphDB
./graphdb-free/bin/graphdb &

# Import TTL data (if not already loaded)
python3 scripts/rdf_uplift.py --import
```

The application degrades gracefully if GraphDB is unavailable — all features work; only the SPARQL comparison column in Ask results is hidden.

### Environment Variables (.env.local)

```bash
FLASK_ENV=development
SECRET_KEY=your-secret-key-here
OPENROUTER_API_KEY=sk-or-v1-...
GRAPHDB_ENABLED=true
GRAPHDB_SPARQL_ENDPOINT=http://localhost:7200/repositories/coolattin
ASK_LLM_PROVIDER=auto
```

### Production Deployment

```bash
# Use gunicorn with multiple workers
gunicorn --bind 0.0.0.0:5001 --workers 4 --timeout 120 app:app

# Or with uvicorn + ASGI adapter (alternative)
# Set FLASK_ENV=production in environment
```

**Production checklist:**
- `SECRET_KEY` set to a strong random value
- `FLASK_ENV=production` (disables DEBUG, tightens TTLs)
- `DATABASE_PATH` pointing to a persistent volume
- `EXPORTS_DIR` writable and persistent
- GraphDB reachable and repository pre-loaded
- `OPENROUTER_API_KEY` set

---

## 25. Codebase Metrics

### Size

| Metric | Value |
|--------|-------|
| Python files | 75 |
| JavaScript files (static) | 11 |
| HTML templates | 9 |
| Lines in ask_service.py | 6,651 |
| Functions in ask_service.py | 154 |
| Total Python LOC (approx.) | ~12,000 |
| Total JS LOC (approx.) | ~4,000 |

### Data

| Dataset | Records | Notes |
|---------|---------|-------|
| Estate records (unified_record) | 13,707 | Source: unified_processed.csv |
| Townland references | 4,225 | Source: VRTI KG |
| Census records | 8,033 | 12 years × 1,319 townlands |
| Clearances records | 1,211 | 10 years × 122 townlands |
| GraphDB triples | 143,123 | Full estate dataset in RDF |
| Unique townlands (in estate records) | 516 | From unified_record |
| Unique surnames | 977 | From unified_record |
| Civil parishes (in reference) | 22 | From townland table |

### Coverage

| Event Type | Records |
|------------|---------|
| Emigration | 6,016 (43.9%) |
| Eviction | 4,108 (30.0%) |
| Tenancy | 5,247 (38.3%) |
| Note: some records have multiple flags | |

Record years span 1841–1886, concentrated in 1847–1856 (Famine period).

---

## Summary

The Coolattin Estate Records Explorer is a comprehensive full-stack web application implementing the following distinct research and user-facing features:

| # | Feature | Technologies |
|---|---------|-------------|
| 1 | Unified estate records search | pandas, SQLite, Jinja2 |
| 2 | Interactive map with choropleth | Leaflet.js, GeoJSON, SQLite |
| 3 | Census explorer (12 years) | SQLite, VRTI SPARQL, openpyxl |
| 4 | Analytics dashboard (KPIs + charts) | Chart.js, pluggable module protocol |
| 5 | Historic heritage landscape | Leaflet.js, GeoJSON overlays |
| 6 | Natural-language Q&A (LLM) | OpenRouter, Ollama, SSE streaming |
| 7 | SQL template matching (fast path) | difflib, 100+ templates |
| 8 | VRTI Knowledge Graph enrichment | SPARQL, external endpoint |
| 9 | GraphDB SPARQL integration | GraphDB 10, custom ontology |
| 10 | SQL vs SPARQL comparison | rdflib, D3.js, LLM mismatch analysis |
| 11 | Knowledge graph visualiser | D3.js v7 force simulation |
| 12 | Townland drill-down (persons) | SQLite, async fetch |
| 13 | PDF report generation | Hand-written PDF 1.4 |
| 14 | Excel export | openpyxl |
| 15 | Workhouse fuzzy matching | pandas, difflib, SequenceMatcher |
| 16 | Feedback / query memory | SQLite, semantic similarity |
| 17 | Townland autocomplete | fuzzy matching, alias resolution |
| 18 | Internationalisation (EN/GA) | Vanilla JS i18n framework |
| 19 | RDF uplift (CSV → TTL → GraphDB) | rdflib, custom co: ontology |
| 20 | DB-first / KG-second caching | SQLite, SPARQL, TTL-based refresh |

---

---

## 26. June 2026 Sprint — Orchestrated Pipeline and Identity Resolution

This section records the significant additions made in the June 2026 development sprint (commits `661fcdf`, `3c3174d`, `4d18308`). See `docs/10_handoff_notes.md` for the full detailed handoff.

### 26.1 Orchestrated 7-Phase Ask Pipeline

The Ask pipeline was rewritten from a flat sequence into a routed, orchestrated architecture. `ASK_USE_NEW_PIPELINE=true` is the default as of 2 June 2026.

| Phase | Module | What it does |
|---|---|---|
| 1 — Intent routing | `intent_router.py` | Classifies: ANALYTICAL / RELATIONAL / COMPARATIVE / FALLBACK |
| 2 — Hybrid retrieval | `embedding_index.py` | TF-IDF + optional dense; RRF fusion; fast-lane short-circuit |
| 3 — Semantic layer | `semantic_layer.py` | Slot-fill → deterministic SQL + SPARQL; no LLM on fast path |
| 4 — Subgraph engine | `subgraph_engine.py` | KG traversal (VRTI + GraphDB) for relational questions |
| 5 — LLM SQL gen | `ask_service.py` | Fallback only; annotated schema + few-shot examples |
| 6 — Identity resolution | `identity_resolver.py` | Mention/Person/Factoid disambiguation; Jaro-Winkler + phonetic |
| 7 — Synthesis | `ask_service.py` | Aggregate SQL + KG + chunks; discrepancy detection; provenance |

### 26.2 Workhouse Entity Resolution

A separate ER subsystem (not part of the Ask pipeline) for linking workhouse records to estate records:
- `workhouse_entity_resolution.py` — pipeline orchestrator
- `entity_resolution/` — normalise, candidates, scoring subpackage
- New tables: `source_mentions`, `entity_resolution_candidates`, `workhouse_unified_links`, `entity_resolution_decisions`, `match_review`
- Confidence bands: High (≥0.75) / Medium (0.50–0.74) / Low (<0.50)

### 26.3 Embeddings and Retrieval

- `voyage_embeddings.py` — Cohere Embed v3 client (`embed-english-v3.0`, 1024-dim); asymmetric input_type
- `local_embeddings.py` — BAAI/bge-large-en-v1.5 local model (no API key; CPU)
- `ask_pgvector.py` — optional pgvector backend when `DATABASE_URL` (Postgres) is set
- `retrieval_chunks.py` — chunk builders for person/place/event retrieval corpus

### 26.4 New Config Variables

`ASK_USE_NEW_PIPELINE`, `EMBEDDING_PROVIDER`, `COHERE_API_KEY`, `DATABASE_URL`, `GRAPHDB_ENABLED`, `GRAPHDB_SPARQL_ENDPOINT`, `GRAPHDB_REQUEST_TIMEOUT`

### 26.5 Evaluation Infrastructure

`ask_eval.py` (2125 lines) provides a full evaluation harness. Baselines captured in `backend/services/eval_results/` (phases 0–5+, pre/post fix comparisons).

---

*Report generated: June 2026*  
*Application version: as committed to main branch*
