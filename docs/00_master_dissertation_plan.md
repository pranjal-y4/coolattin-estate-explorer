# Master Dissertation Plan
## Coolattin Estate Records Explorer

| Field | Detail |
|---|---|
| **Candidate** | Pranjal Yadav |
| **Email** | yadavp2@tcd.ie |
| **Programme** | MSc Computer Science (Interactive Digital Media) |
| **Institution** | Trinity College Dublin |
| **Supervisors** | Dr Ciarán Wallace (VRTI) · Prof Declan O'Sullivan (CS) |
| **Submission deadline** | 3 August 2026 |
| **Demo date** | TBC (late July 2026) |
| **Live deployment** | Azure App Service — Italy North region |
| **Repository** | Git · branch `main` · one commit (`800f41d`) |

---

## Contents

1. [Project Overview](#1-project-overview)
2. [Complete Codebase Reference](#2-complete-codebase-reference)
3. [Dissertation Contribution Statement](#3-dissertation-contribution-statement)
4. [Evaluation Strategy](#4-evaluation-strategy)
5. [12-Week Time Plan](#5-12-week-time-plan)
6. [Future Scope and Extra Deliverables](#6-future-scope-and-extra-deliverables)

---

# 1. Project Overview

## 1.1 What the system is

The Coolattin Estate Records Explorer is a full-stack web application that integrates heterogeneous nineteenth-century Irish archival records — estate tenancy ledgers, assisted emigration lists, Famine-era eviction records, official census returns, and National Monuments Service heritage data — into a unified, publicly accessible interface with:

- An **interactive Leaflet map** of the 152 Coolattin Estate townlands in County Wicklow
- A **census page** showing population data from 1827 to 1891 per townland
- **Analytics dashboards** for emigration, evictions, tenancies, and the full unified dataset
- A **heritage landscape page** overlaying ring forts, holy wells, and earthworks on the estate boundary
- A **natural-language Ask page** that lets historians and genealogists query the integrated database by typing plain English questions

The system is built for the MSc dissertation and is deployed live on Azure. It is the first computationally integrated interface for the Coolattin Estate records.

## 1.2 Tech stack at a glance

| Layer | Technology |
|---|---|
| Backend runtime | Python 3.12 · Flask (application factory) |
| Database | SQLite 3 via raw `sqlite3` (WAL mode, foreign keys on) |
| External Knowledge Graph | VRTI (Virtual Record Treasury of Ireland) — OpenLink Virtuoso SPARQL endpoint |
| LLM providers | OpenRouter (19 free models, cloud) · Ollama (local fallback) |
| Frontend | Vanilla JS · Leaflet.js 1.x · Chart.js · Jinja2 templates |
| PDF export | Hand-written PDF 1.4 (zero library dependencies) |
| Data processing | pandas · openpyxl · rapidfuzz (optional) |
| Deployment | Azure App Service (Italy North) · gunicorn |
| Dev dependencies | None beyond `requirements.txt` |

## 1.3 Data sources

| Source | Format | What it provides |
|---|---|---|
| Coolattin estate GeoJSON (`townlands.json`) | GeoJSON | 152 townland names + Irish names + area measurements + estate population surveys (1827, 1839, 1848, 1850, 1860, 1868) + clearances per year (1847–1856) |
| VRTI Knowledge Graph | RDF via SPARQL | Boundary WKT + centroid coordinates + civil parish / barony / county hierarchy + OSM/OSI/VRTI identifiers + images + external links + standard census years (1841, 1851, 1861, 1871, 1881, 1891) with male/female/inhabited/uninhabited breakdowns |
| `unified_processed.csv` | CSV | Integrated person-level records: tenants, emigrants, evictees — forename, surname, townland, year, role, departure port, arrival port, ship, holding acres, family members |
| NMS open data — holy wells | CSV + GeoJSON | Holy well locations across County Wicklow, with townland attribution |
| NMS open data — ring forts / monuments | CSV + GeoJSON | Archaeological monument locations (ring forts, earthworks, souterrains) |
| NMS monuments to visit | CSV | Curated heritage site listings |
| Townlands.ie reference | JSON | Canonical townland name list, alias resolution, place-name hierarchy |
| Workhouse records | CSV | Fuzzy-linked workhouse records for cross-referencing tenant records |

---

# 2. Complete Codebase Reference

## 2.1 Directory structure (annotated)

```
Coolattin-app/                      ← project root / working directory
├── app.py                          entry point — calls create_app(), runs dev server on :5001
├── create_app.py                   Flask application factory — registers all blueprints
├── config.py                       all env-overridable config (DB path, VRTI URL, timeouts)
├── extensions.py                   DB singleton (init_db, get_db_conn, ensure_schema)
├── requirements.txt                Python dependencies (Flask, pandas, requests, openpyxl, rapidfuzz…)
├── .env.example                    template for .env.local (OPENROUTER_API_KEY etc.)
│
├── backend/
│   ├── routes/                     Flask Blueprints — one file per URL prefix
│   │   ├── main.py                 GET / · /about · /analytics · /census · /info · /ask · /heritage
│   │   ├── ask.py                  POST /api/ask/query · POST /feedback · GET /llm-status · GET /pdf/<name>
│   │   ├── census.py               GET /api/census · /townlands · /summary · /townland · POST /refresh
│   │   ├── unified.py              GET /api/unified/records · /stats · /townlands · /surnames · /surname-suggest
│   │   ├── map_config.py           GET /api/map/layers · /config
│   │   ├── townlands.py            GET /api/townlands · /detail · /geojson
│   │   └── exports.py              GET /api/exports/census · POST /regenerate
│   │
│   ├── services/                   business logic — routes call services, never raw DB
│   │   ├── ask_service.py          5,987 lines — the entire Ask pipeline
│   │   ├── census_service.py       436 lines — DB-first / KG-second census queries
│   │   ├── export_service.py       252 lines — Excel generation via openpyxl
│   │   ├── map_service.py          111 lines — layer config + centroid computation
│   │   ├── refresh_service.py       81 lines — triggers KG re-ingest
│   │   ├── townland_service.py     282 lines — name normalisation, alias resolution, fuzzy matching
│   │   ├── unified_service.py      121 lines — search + stats over unified_processed.csv
│   │   └── workhouse_service.py    205 lines — fuzzy cross-reference against workhouse records
│   │
│   ├── repositories/               all SQL queries — services call repos, never raw SQL outside
│   │   ├── census_repository.py    CRUD + aggregation for census_record table
│   │   ├── clearances_repository.py CRUD for clearances_record table
│   │   ├── townland_repository.py  CRUD + look-ups for townland table
│   │   └── refresh_state_repository.py read/write refresh_state table
│   │
│   ├── models/
│   │   └── census_models.py        CensusFilters dataclass · CensusResponse dataclass · CensusResultMeta
│   │
│   ├── integrations/
│   │   ├── vrti_sparql.py          676 lines — centralised SPARQL client (the ONLY place with SPARQL)
│   │   └── townlands_reference.py  111 lines — Townlands.ie canonical reference
│   │
│   └── jobs/                       one-shot ingest jobs — run manually or at startup
│       ├── full_ingest.py          ingest from estate GeoJSON + VRTI KG → townland + census + clearances
│       ├── census_ingest.py        incremental census refresh from VRTI KG
│       ├── census_seed.py          seed census from local CSV when KG unavailable
│       └── townlands_ingest.py     townland table refresh from Townlands.ie reference
│
├── analytics/                      pluggable analytics modules
│   ├── base.py                     Protocol: AnalyticsModule · KPI · Chart · AnalyticsResult dataclasses
│   ├── registry.py                 auto-discovery via importlib glob (finds any file exposing MODULE)
│   ├── emigrations.py              EmigrationAnalytics — timeline + top destinations
│   ├── evictions.py                EvictionsAnalytics — clearances per year per townland
│   ├── tenancies.py                TenanciesAnalytics — top townlands + surname distribution
│   ├── townland_geo.py             TownlandGeoAnalytics — estate spatial overview
│   ├── unified.py                  UnifiedAnalytics — cross-source KPIs + charts
│   └── workhouse.py               WorkhouseAnalytics — workhouse match rates
│
├── frontend/
│   ├── templates/                  Jinja2 HTML (base.html + one per page)
│   │   ├── base.html               shared shell: nav, footer, i18n script, Chart.js, Leaflet
│   │   ├── index.html              home page — hero video, map embed, section links
│   │   ├── analytics.html          analytics page — dataset selector, KPI cards, Chart.js canvases
│   │   ├── census.html             census page — year filter, townland list, population chart
│   │   ├── ask.html                Ask page — question input, SSE progress bar, result panel, PDF link
│   │   ├── heritage.html           heritage page — Leaflet map with monument layers
│   │   ├── about.html              project description and methodology
│   │   └── info.html               data sources and provenance notes
│   │
│   └── static/
│       ├── css/main.css            all custom styles (dark theme, hero, cards, ask panel)
│       ├── js/
│       │   ├── main.js             home page: YouTube hero background, section navigation
│       │   ├── ask.js              Ask page: SSE consumer, progress rendering, chart rendering
│       │   ├── census.js           census page: API calls, population chart, townland detail
│       │   ├── heritage.js         heritage page: Leaflet layer management, monument popups
│       │   ├── analytics.js        analytics page: dataset load, KPI + chart rendering
│       │   ├── map.js              shared map utilities (centroid overlay, townland click)
│       │   └── i18n.js             English/Irish bilingual string switching
│       ├── data/
│       │   ├── townlands.json      estate GeoJSON — 152 townlands with geometry and population fields
│       │   ├── unified_processed.csv  person-level integrated records (served statically)
│       │   ├── holywells_wicklow.geojson  NMS holy well locations
│       │   └── asi_wicklow.geojson    NMS archaeological survey monuments
│       └── images/                 estate photos and map tiles
│
├── data/
│   ├── seed/
│   │   ├── townland_aliases.json   canonical name → alias list (for fuzzy resolution)
│   │   └── wicklow_townlands_reference.json  Townlands.ie canonical reference data
│   └── source_snapshots/          local copies of KG responses (gitignored, used in CI)
│
├── extra_datasets/                 NMS open-data CSVs (source for heritage features)
│   ├── NMS_OpenData_20230420_HolyWell_csv.csv
│   ├── NMS_OpenData_20230823.csv
│   └── NMSMonumentsToVisit.csv
│
├── exports/                       runtime output — Excel and PDF (gitignored)
├── scripts/                       one-off data processing scripts
├── _archive/                      deprecated code (kept for reference, never imported)
└── docs/                          dissertation planning documents (this folder)
    ├── 00_master_dissertation_plan.md   ← this document
    ├── 01_contribution_statement.md
    ├── 02_evaluation_strategy.md
    ├── 03_time_plan.md
    └── 04_future_scope.md
```

---

## 2.2 Database schema (five tables)

The schema is defined and auto-migrated in `extensions.py::ensure_schema()`, with the `unified_record` and `heritage_feature` tables managed inside `ask_service.py` (seeded on first Ask query).

### `townland` — canonical townland reference

| Column | Type | Source | Notes |
|---|---|---|---|
| `id` | INTEGER PK | auto | |
| `name` | TEXT UNIQUE | GeoJSON / KG | Canonical UPPER-CASE English name |
| `name_gaelic` | TEXT | GeoJSON | Irish/Gaelic name |
| `barony` | TEXT | KG | Place hierarchy |
| `civil_parish` | TEXT | KG | Place hierarchy |
| `electoral_division` | TEXT | KG | |
| `placename_theme` | TEXT | manual | |
| `description` | TEXT | manual | |
| `td_id` | TEXT | GeoJSON | Estate identifier |
| `guid` | TEXT | GeoJSON | Estate identifier |
| `area_sqm` | REAL | GeoJSON | Area in m² |
| `kg_uri` | TEXT | KG | VRTI subject URI |
| `wkt_geometry` | TEXT | KG | Boundary WKT polygon |
| `centroid_lat` | REAL | KG | |
| `centroid_lon` | REAL | KG | |
| `county` | TEXT | KG | |
| `osm_id` | TEXT | KG | OpenStreetMap ID |
| `osi_id` | TEXT | KG | Ordnance Survey Ireland ID |
| `vrti_id` | TEXT | KG | VRTI short ID |
| `images_json` | TEXT | KG | JSON array of image URLs |
| `links_json` | TEXT | KG | JSON array of external links |
| `source` | TEXT | — | `'json'` \| `'kg'` \| `'manual'` |

### `census_record` — population per townland × year

| Column | Type | Source | Notes |
|---|---|---|---|
| `id` | INTEGER PK | auto | |
| `townland_id` | INTEGER FK | — | → `townland.id` |
| `year` | INTEGER | GeoJSON / KG | Estate years: 1827, 1839, 1848, 1850, 1860, 1868. KG years: 1841, 1851, 1861, 1871, 1881, 1891 |
| `male` | INTEGER | KG | NULL for estate survey years (total only) |
| `female` | INTEGER | KG | NULL for estate survey years |
| `total` | INTEGER | both | |
| `inhabited` | INTEGER | KG | Houses |
| `uninhabited` | INTEGER | KG | Houses |
| `source` | TEXT | — | `'json'` \| `'kg'` |
| `kg_uri` | TEXT | KG | KG entity URI |
| UNIQUE | (`townland_id`, `year`) | | |

### `clearances_record` — evictions per townland × year (1847–1856)

| Column | Type | Source | Notes |
|---|---|---|---|
| `id` | INTEGER PK | auto | |
| `townland_id` | INTEGER FK | — | → `townland.id` |
| `year` | INTEGER | GeoJSON | 1847–1856 |
| `count` | INTEGER | GeoJSON | Number of persons cleared |
| `source` | TEXT | — | `'json'` |
| UNIQUE | (`townland_id`, `year`) | | |

### `refresh_state` — dataset freshness tracking

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `dataset_key` | TEXT UNIQUE | e.g. `'full_ingest'`, `'census_kg'` |
| `last_synced_at` | TEXT | ISO 8601 |
| `source` | TEXT | `'kg_refresh'` \| `'json'` \| `'manual'` |
| `query_hash` | TEXT | MD5 of the SPARQL query used |
| `record_count` | INTEGER | |
| `export_file` | TEXT | Path to most recent Excel export |

### `unified_record` — person-level integrated records (seeded by Ask pipeline)

Seeded from `unified_processed.csv` at first Ask query. Holds every person-row with cross-source flags.

| Column | Type | Notes |
|---|---|---|
| `record_id` | TEXT | Unique row identifier from source CSV |
| `canonical_name` | TEXT | Preferred display name |
| `forename` | TEXT | |
| `surname` | TEXT | |
| `chief_tenant_forename` | TEXT | |
| `chief_tenant_surname` | TEXT | |
| `townland` | TEXT | Display name |
| `townland_norm` | TEXT | `UPPER(townland)` — JOIN key |
| `parish` | TEXT | |
| `year` | INTEGER | |
| `month` | INTEGER | |
| `role` | TEXT | e.g. `'head'`, `'tenant'`, `'emigrant'` |
| `legal_action` | TEXT | |
| `estate` | TEXT | |
| `gender` | TEXT | `'M'` \| `'F'` \| null |
| `age` | INTEGER | |
| `occupation` | TEXT | |
| `departure` | TEXT | Port of departure |
| `arrival` | TEXT | Port / place of arrival |
| `ship_name` | TEXT | |
| `household_list` | TEXT | Family members text |
| `holding_acres` | REAL | Best-available holding size |
| `children_count` | INTEGER | Derived: sons + daughters count |
| `family_size_estimate` | INTEGER | Derived: household size estimate |
| `family_key` | TEXT | Grouping key for family units |
| `is_widow` | INTEGER | Derived flag: 1 if widow-labelled |
| `is_canada_destination` | INTEGER | Derived: 1 if arrival mentions Quebec / Grosse Isle / Canada |
| `has_emigration_record` | INTEGER | 1 = emigrated |
| `has_eviction_record` | INTEGER | 1 = evicted |
| `has_tenancy_record` | INTEGER | 1 = tenant record |

### `heritage_feature` — NMS monument features (seeded by Ask pipeline)

Seeded from `holywells_wicklow.geojson` and `asi_wicklow.geojson`.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `name` | TEXT | Monument name |
| `feature_group` | TEXT | `'holy_well'` \| `'ring_fort'` \| `'earthwork'` etc. |
| `monument_class` | TEXT | Source class label (e.g. `'Ritual site - holy well'`) |
| `townland_raw` | TEXT | Original townland text from GeoJSON |
| `townland_norm` | TEXT | Normalised for joining |
| `lat` | REAL | |
| `lon` | REAL | |
| `source_dataset` | TEXT | `'holywells'` \| `'asi'` |

Indices: `idx_heritage_townland_norm`, `idx_heritage_feature_group`.

### Additional Ask-internal tables

| Table | Purpose |
|---|---|
| `ask_query_memory` (schema version v1) | Stores approved (thumbs-up) question → SQL pairs for future reuse |
| `ask_query_feedback` | Stores all feedback events (up/down) with full question, SQL, and result metadata |

---

## 2.3 API surface (all endpoints)

### Page routes (`/`)

| Method | Path | Template | Description |
|---|---|---|---|
| GET | `/` | `index.html` | Home page with hero video and map |
| GET | `/analytics` | `analytics.html` | Analytics dashboards |
| GET | `/census` | `census.html` | Census population explorer |
| GET | `/ask` | `ask.html` | Natural-language Q&A |
| GET | `/heritage` | `heritage.html` | Heritage landscape map |
| GET | `/about` | `about.html` | Project description |
| GET | `/info` | `info.html` | Data sources reference |

### Ask API (`/api/ask/`)

| Method | Path | Description |
|---|---|---|
| POST | `/api/ask/query` | Stream SSE pipeline events for a natural-language question |
| POST | `/api/ask/feedback` | Record thumbs-up/down feedback; approved queries enter query memory |
| GET | `/api/ask/llm-status` | LLM provider health check (OpenRouter / Ollama) |
| GET | `/api/ask/townland-suggest?q=` | Fuzzy townland autocomplete suggestions |
| GET | `/api/ask/pdf/<filename>` | Download a generated PDF report |

### Census API (`/api/census/`)

| Method | Path | Query params | Description |
|---|---|---|---|
| GET | `/api/census/` | `year`, `townland`, `barony`, `page`, `limit` | Paginated census records (max 2000/page) |
| GET | `/api/census/townlands` | — | Townland names with census data |
| GET | `/api/census/summary` | `year` | Aggregate stats (totals by year) |
| GET | `/api/census/townland` | `name` | Full census history for one townland |
| POST | `/api/census/refresh` | JSON `{year}` | Force KG re-ingest (ignores TTL) |
| GET | `/api/census/export/latest` | — | Most recent Excel export metadata |
| POST | `/api/census/export/regenerate` | `year` | Re-generate Excel from local DB |

### Unified records API (`/api/unified/`)

| Method | Path | Query params | Description |
|---|---|---|---|
| GET | `/api/unified/records` | `surname`, `forename`, `townland`, `year`, `estate`, `limit` | Search person records |
| GET | `/api/unified/stats` | — | Record counts and field coverage |
| GET | `/api/unified/townlands` | — | Townland list from unified records |
| GET | `/api/unified/surnames` | — | Surname list |
| GET | `/api/unified/surname-suggest` | `q`, `townland` | Surname autocomplete |

### Other APIs

| Method | Path | Description |
|---|---|---|
| GET | `/api/map/layers` | Layer configuration for Leaflet |
| GET | `/api/map/config` | Map bounds and centre |
| GET | `/api/townlands` | All townlands with metadata |
| GET | `/api/townlands/detail` | Single townland detail |
| GET | `/api/townlands/geojson` | GeoJSON for map rendering |
| GET | `/api/centroids` | Townland centroid lat/lon (legacy) |
| GET | `/api/workhouse/match/<id>` | Workhouse fuzzy matches for a record |
| GET | `/api/exports/census` | Census Excel export download |

---

## 2.4 The Ask pipeline in full detail

The Ask pipeline in `ask_service.py` (5,987 lines) is the most complex component. Every user question goes through these stages sequentially, each emitting an SSE progress event:

### Stage 0 — Database seeding (startup, once)

Before any question is answered, two seeding operations run if not already done:
- `_ensure_unified_table_seeded()` — loads `unified_processed.csv` into the `unified_record` SQLite table, deriving all computed columns (`is_widow`, `is_canada_destination`, `children_count`, `family_size_estimate`, `family_key`, `has_emigration_record`, `has_eviction_record`, `has_tenancy_record`)
- `_ensure_heritage_feature_seeded()` — loads `holywells_wicklow.geojson` and `asi_wicklow.geojson` into `heritage_feature`
- `_ensure_query_memory_schema()` — creates `ask_query_memory` and `ask_query_feedback` tables if missing

### Stage 1 — Pre-flight analysis (synchronous, no LLM)

1. **Townland resolution** (`_resolve_townland_context`) — scans the question for townland names. Resolution: exact match in normalised catalogue → fuzzy match via `rapidfuzz` (threshold 80) → "did you mean?" suggestion. If `townland_hint` provided by frontend: use as authoritative.
2. **Question analysis** (`_analyse_question`) — extracts `year` (regex), `surname` (6 patterns), `radius_km`; classifies `primary_intent`, `output_mode`, `group_by`, `scope`. No LLM, no DB.
3. **Data coverage warnings** (`_question_data_coverage_warnings`) — checks for 1821 references (data starts 1841), future dates, etc.

### Stage 2 — Four Fast Lanes (first match short-circuits all routing)

1. **Rule-based slot-fill** (`semantic_layer.try_rule_based_fill`) — 14 metric keyword sets; confidence scoring starts at 1.0, penalised for competing metrics and no-filter queries. If confidence ≥ 0.80 → compile SQL directly. **0 LLM calls, < 5 ms.**
2. **Verified template** (`_try_verified_analysis`) — 81 templates scored by `required_keywords` + `optional_keywords`. If match in `VERIFIED_ANALYSIS_TEMPLATE_IDS` → SQL, confidence 1.0.
3. **Direct memory reuse** — `ask_query_memory` (TTL 60 s cache); `token_sort_ratio + cosine ≥ 0.55` → reuse approved SQL.
4. **Embedding fast lane** (`embedding_index.py`) — TF-IDF + RRF; cosine ≥ 0.68 AND all required_keywords present → template SQL.

### Stage 3 — Intent Classification → Route Dispatch (SSE: `classifying_intent`)

`classify_intent(question, analysis, slot_fill)` in `intent_router.py`:
- **COMPARATIVE** → ANALYTICAL (semantic layer SQL) + RELATIONAL (subgraph SPARQL) in parallel
- **RELATIONAL** → `subgraph_engine.retrieve_subgraph()`: VRTI multi-hop + GraphDB neighbourhood (k=2)
- **ANALYTICAL** → semantic layer slot-fill → `compile_sql()` deterministic compiler
- **FALLBACK** → LLM free-form SQL

### Stage 4 — SQL Acquisition

**ANALYTICAL lane (semantic_layer.py):**
- 14-metric registry; each metric has SQL aggregate, dimensions, filter templates, and SPARQL equivalent
- Rule-fill path (confidence ≥ 0.80): direct `compile_sql(slot_fill)` — no LLM
- LLM slot-fill path (confidence ≥ 0.70): LLM returns JSON `{metric, dimensions, filters}` → `compile_sql()`
- `compile_sparql(slot_fill)` generates GraphDB SPARQL equivalent for RQ6 comparison

**FALLBACK lane:**
- `_generate_sql()`: annotated schema + approved examples → LLM → SQL
- LLM: OpenRouter (primary) → Ollama (fallback). Up to `OPENROUTER_MAX_RETRIES` (default 2) retries.

### Stage 5 — SQL guardrail (SSE: `framing_query`)

`_sanitize_and_validate_sql(sql)`:
1. `FORBIDDEN_SQL` regex blocks `INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|DETACH|PRAGMA|REINDEX|VACUUM|TRUNCATE|REPLACE`
2. Must start with `SELECT` or `WITH`
3. Execute via `_execute_with_recovery()`: registers `distance_km()` haversine function; 1 LLM repair attempt on syntax error

### Stage 6 — LLM Answer Rewrite (SSE: `synthesizing_answer`)

The raw table rows and VRTI/GraphDB context are sent to the LLM with the question, requesting historically-contextualised prose. No hallucination of figures not in the data. If LLM unavailable: return raw `actual_answer` unmodified.

### Stage 7 — VRTI + GraphDB Enrichment (parallel, non-blocking)

- VRTI: `get_townland_details_by_name()` → parish, barony, county context; TTL 1 hour; 5-min cooldown on failure
- GraphDB: `graphdb_sparql.query()` → co: ontology neighbourhood; 15-second timeout; fails silently

### Stage 8 — Fusion + PDF + Final Result

- Phase 6 fusion: `_fuse_lanes()` compares SQLite vs GraphDB numeric results; flags discrepancies by magnitude
- PDF: `_write_pdf_report()` → hand-written PDF 1.4 (no reportlab/fpdf); `exports/ask/ask_report_<timestamp>.pdf`
- Final SSE `type: result`: `answer`, `llm_rephrased_answer`, `sql`, `columns`, `rows`, `chart`, `vrti_context`, `fusion`, `discrepancies`, `pdf_url`, `query_provenance`, `llm_meta`

---

## 2.5 Metric registry — the 22 semantic layer metrics

The `METRIC_REGISTRY` in `semantic_layer.py` covers these categories:

| Category | Metrics | Examples |
|---|---|---|
| Emigration | emigration_count, canada_emigration_count | Total emigrants; Canada-only; by townland/year |
| Eviction/clearances | eviction_event_count, evicted_person_count | Total evicted; by year; by townland |
| Census/population | population, population_change, uninhabited_houses | Population by year; Famine-era change; house stats |
| Tenancy/land | tenancy_count, avg_holding_acres | Tenancy records; average holding size |
| People | person_count, widow_count | Distinct people; widows |
| Geography | townland_count, parish_count, townland_attribute | Place listing; attributes |

All 15 domain-expert competency questions are covered by metrics in the registry. The semantic layer compiles both SQL and SPARQL from the same `SlotFill` struct, enabling the SQL-vs-SPARQL comparison for RQ6.

**Verified templates (81 entries in `QUESTION_TEMPLATES`)** remain as the Fast Lane 2 path for exact keyword pattern matches. `VERIFIED_ANALYSIS_TEMPLATE_IDS` marks templates that are authoritative — the LLM path is never taken for them, and they include per-template `warning` strings surfaced to the user when data coverage is limited.

---

## 2.6 Analytics module architecture

The `analytics/` package uses automatic module discovery via `registry.py::discover_modules()`. Any Python file in the package that:
1. Exposes a module-level `MODULE` object
2. Where `MODULE.dataset_id` is a unique string
3. And `MODULE.compute()` returns an `AnalyticsResult`

…is automatically included in the analytics page selector. No manual registration step required.

| Module file | `dataset_id` | Key KPIs | Charts |
|---|---|---|---|
| `emigrations.py` | `emigration` | Total emigration records | Timeline (line), Top destinations (bar) |
| `evictions.py` | `evictions` | Total clearance events | Clearances by year (line), by townland (bar) |
| `tenancies.py` | `tenancies` | Total tenancy records | Top townlands (bar), Trend over time (line), Top surnames (bar) |
| `townland_geo.py` | `townland_geo` | Townland count, parishes, baronies | — |
| `unified.py` | `unified` | Total records, unique surnames, unique townlands | Timeline (line), Top surnames (bar), Top townlands (bar), Estate distribution (doughnut), Gender (doughnut) |
| `workhouse.py` | `workhouse` | Match rate | Matched vs unmatched (doughnut) |

---

## 2.7 VRTI SPARQL integration

`backend/integrations/vrti_sparql.py` (676 lines) is the sole module that constructs SPARQL queries. All SPARQL is centralised here — no other module sees SPARQL syntax.

**Endpoint:** `https://virtuoso.virtualtreasury.ie/sparql/`  
**Named graph:** `https://kg.virtualtreasury.ie/graph/present-day-places-v1`

**PREFIX block (always prepended):**
```sparql
PREFIX crm:  <http://erlangen-crm.org/current/>
PREFIX vrti: <https://ont.virtualtreasury.ie/ontology#>
PREFIX geo:  <http://www.opengis.net/ont/geosparql#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX owl:  <http://www.w3.org/2002/07/owl#>
PREFIX xsd:  <http://www.w3.org/2001/XMLSchema#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
```

**Exported functions:**

| Function | Purpose | Called from |
|---|---|---|
| `get_townlands(county, limit)` | Retrieve townlands with boundary WKT and place hierarchy | `full_ingest.py` |
| `get_wicklow_townlands(limit)` | Restricted to County Wicklow | `townlands_ingest.py` |
| `get_townland_details_by_name(name)` | Single townland with images, links, centroid | `ask_service.py` (VRTI enrichment) |
| `get_census_records_for_townland(uri)` | Census records for one townland | `census_ingest.py` |
| `get_census_records_for_county(county, year)` | All census records for a county × year | `census_ingest.py` |
| `get_parish_names(county)` | Civil parish names | `full_ingest.py` |
| `probe_endpoint()` | Health check — returns bool | `ask_service.check_llm_status` |

**DTOs (typed dataclasses):**
- `TownlandDTO` — uri, name, name_gaelic, wkt_geometry, centroid_lat/lon, barony, civil_parish, county, osm_id, osi_id, vrti_id, images, links
- `CensusRecordDTO` — townland_uri, townland_name, year, male, female, inhabited, uninhabited

---

## 2.8 LLM integration details

**Provider priority:** OpenRouter (primary) → Ollama (local fallback)

**OpenRouter configuration (env vars):**

| Variable | Default | Purpose |
|---|---|---|
| `OPENROUTER_API_KEY` | (required) | API authentication |
| `OPENROUTER_MODEL` | `openai/gpt-oss-20b:free` | Model to use |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | API base |
| `OPENROUTER_CONNECT_TIMEOUT` | `10` s | |
| `OPENROUTER_REQUEST_TIMEOUT` | `80` s | |
| `OPENROUTER_MAX_RETRIES` | `2` | |
| `ASK_LLM_PROVIDER` | `auto` | Force `openrouter` or `ollama` |

**19 free OpenRouter models supported** (auto-fallback list): includes `openai/gpt-oss-20b:free`, `openai/gpt-oss-120b:free`, `meta-llama/llama-3.3-70b-instruct:free`, `google/gemma-3-27b-it:free`, `qwen/qwen3-next-80b-a3b-instruct:free`, and others.

**Ollama configuration:**

| Variable | Default | Purpose |
|---|---|---|
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Local Ollama instance |
| `OLLAMA_MODEL` | (auto-detect first available) | |
| `OLLAMA_REQUEST_TIMEOUT` | `180` s | |

---

## 2.9 Derived field computation (data quality notes)

These fields are computed during CSV→SQLite seeding in `_ensure_unified_table_seeded()`. Their accuracy determines the accuracy of answers to the most sensitive competency questions.

| Field | Derivation logic | Affects question(s) |
|---|---|---|
| `is_widow` | 1 if canonical_name or household_list contains widow-label pattern | Q2, Q3, Q4 |
| `is_canada_destination` | 1 if arrival text contains: `canada`, `quebec`, `st andrews`, `grosse isle` | Q14, Q15 |
| `children_count` | Sum of recorded sons and daughters fields | Q3, Q5 |
| `family_size_estimate` | Maximum of household-member count fields | Q6 |
| `family_key` | Composite key: `surname|townland_norm` (or explicit family ID from CSV) | Q6, Q15 |
| `has_emigration_record` | 1 if departure port or ship name present, or role indicates emigrant | Q5, Q9, Q14, Q15 |
| `has_eviction_record` | 1 if source row is from eviction/clearance dataset | Q4, Q6 |
| `has_tenancy_record` | 1 if source row is from tenancy dataset | Q1, Q10, Q11 |

---

# 3. Dissertation Contribution Statement

## 3.1 Research problem

Nineteenth-century Irish estate records — tenancy rentals, assisted emigration lists, eviction ledgers, and census returns — are held across multiple formats and institutions. They have never been computationally integrated into a single queryable system that a historian or descendant researcher can interrogate in plain English without knowledge of SQL, SPARQL, or any technical query language. Comparative analyses that cross record types (for example: were widows disproportionately evicted? did emigration from a townland correlate with its subsequent population decline?) require bespoke programming effort per question.

This dissertation addresses that gap for the Coolattin Estate, County Wicklow — one of the largest assisted-emigration estates in nineteenth-century Ireland, with records spanning 1827–1891.

## 3.2 Computer science contributions

### CS-1: Reproducible multi-source data warehouse for heterogeneous archival data

The primary CS contribution is a reproducible ingest pipeline that unifies five structurally dissimilar source types (estate GeoJSON, VRTI SPARQL KG, CSV person records, NMS GeoJSON heritage data, Townlands.ie reference) into a single SQLite serving layer. The pipeline applies:

- **Fuzzy place-name normalisation** (`townland_service.py` + `rapidfuzz`) to resolve variant spellings across sources
- **Canonical townland resolution** against an alias map (`data/seed/townland_aliases.json`)
- **Derived-field inference** at ingest time (widow detection, Canada destination classification, family size estimation, family key construction, record-type flags)
- **Idempotent schema migration** (`extensions.py::ensure_schema`) — safe to run on every startup with `IF NOT EXISTS` and `ALTER TABLE ... ADD COLUMN` guards

This is explicitly a **data warehouse pattern**: sources are uplifted into the serving layer in batch, and all runtime queries run against SQLite. This is the appropriate architecture for historically static records that change only when a new source is added or re-ingested.

### CS-2: Multi-stage NL→SQL pipeline with template-first matching

The Ask pipeline implements a seven-stage architecture:

1. Template-first matching (81 verified templates, keyword scoring, O(n) over template list)
2. Fuzzy townland resolution (rapidfuzz, cached catalogue)
3. LLM SQL generation with bounded schema injection (OpenRouter / Ollama fallback)
4. Read-only SQL guardrail (regex-based forbidden-statement detection)
5. LLM natural-language answer rewrite
6. VRTI SPARQL enrichment (parallel, with in-process TTL cache)
7. Hand-written PDF report generation

The template-first approach ensures that the 15 domain-expert competency questions return deterministic, verifiable answers without an LLM call — eliminating hallucination risk for the highest-stakes queries.

### CS-3: Pluggable analytics module registry

The `analytics/` package uses Python `importlib` for automatic module discovery. Any file in the package that exposes a `MODULE` object implementing the `AnalyticsModule` protocol is included at runtime — no central registration step. This pattern enables independent extension without modifying shared code.

### CS-4: Server-Sent Events streaming with per-stage progress

The Ask pipeline streams progress via SSE, yielding a JSON event at the start and end of each of the seven stages. This enables the browser to render a live progress indicator during multi-second LLM operations, rather than blocking on a single response.

### CS-5: Evaluation against a domain-expert competency question set

Systematic evaluation of the pipeline against 15 questions specified by the VRTI Programme Director constitutes a domain-expert acceptance test — a methodologically grounded approach to evaluating NL-to-database systems for specialised historical corpora.

## 3.3 Digital humanities contributions

### DH-1: First integrated computational interface for Coolattin Estate records

No prior system has brought tenancy, emigration, eviction, census, and heritage landscape data for the Coolattin Estate into a unified searchable interface. The system makes these records accessible to genealogical researchers, historians, and digital humanities researchers.

### DH-2: Heritage landscape integration

By spatially joining the NMS monument data (holy wells, ring forts) to the estate townland network, the system enables a class of questions that crosses social history and landscape history — whether settlement and demographic patterns correlate with monument distributions.

### DH-3: Reproducible archival research infrastructure

All source data, ingest scripts, schema, and query templates are version-controlled. The complete database can be reconstructed from source in a single command. This meets emerging standards for computational reproducibility in the humanities.

## 3.4 Positioning relative to prior work

| System | Record types | Query interface | Integration depth |
|---|---|---|---|
| VRTI Knowledge Graph | All-Ireland places + census | SPARQL (technical users only) | KG-native |
| Landed Estates DB (NUI Galway) | Multiple estates | Free-text name search | Single source |
| Griffith's Valuation Online | Valuation records only | Name search | Single source |
| IrishGenealogy.ie | Church + civil registers | Name search | Single source |
| **This dissertation** | Tenancy + emigration + eviction + census + heritage | Natural language | Five-source integration |

## 3.5 One-paragraph summary for supervisor communication

> This dissertation contributes (1) a reproducible data warehouse architecture integrating five heterogeneous Irish historical archival sources — estate GeoJSON, VRTI SPARQL KG, person-level CSV records, NMS heritage GeoJSON, and Townlands.ie reference — into a unified SQLite serving layer; (2) a seven-stage NL→SQL pipeline with template-first matching (81 verified templates), LLM fallback SQL generation via OpenRouter/Ollama, read-only guardrail, VRTI SPARQL enrichment, SSE streaming, and PDF export, evaluated against 15 domain-expert competency questions; and (3) the first publicly accessible integrated computational interface for the Coolattin Estate records, enabling genealogical and historical research that previously required bespoke programming.

---

# 4. Evaluation Strategy

## 4.1 Primary instrument: 15 competency questions

Dr Ciarán Wallace (VRTI Programme Director) specified these 15 questions as representative of the analytical needs of historians and genealogical researchers working with the Coolattin records. These constitute the primary evaluation instrument.

| # | Question (verbatim) | Template ID | Data limitation to document |
|---|---|---|---|
| Q1 | On average, did male tenants have more land than female tenants? | `tenant_land_gender_average` | Gender field is sparse — report coverage % |
| Q2 | How many widows appear in the records? | `widows_count` | Widow detection is derived from name/note labels |
| Q3 | What proportion of widows had children? | `widows_with_children_proportion` | children_count derived from sons+daughters fields |
| Q4 | What proportion of widows appear on the eviction records? | `widows_eviction_proportion` | Widow identification is derived |
| Q5 | How many children emigrated? | `children_emigrated` | Only records with explicit age field included |
| Q6 | What was the range of family sizes in the eviction records? | `eviction_family_size_range` | family_size_estimate requires matching family_key |
| Q7 | Are the most populous townlands in 1841 still the most populous in 1861? | `most_populous_1841_vs_1861` | Requires KG census data ingested |
| Q8 | What is the overall population trend 1821–1861? | `population_trend_1841_1861` | Data begins 1841; 1821 not available — document explicitly |
| Q9 | Is there a relationship between emigration townlands and population trends? | `emigration_population_townland_trend` | Population change is 1841→1861 not 1821→1861 |
| Q10 | What tenants had more land at the end of the record dates? | `largest_latest_tenant_holdings` | Requires holding_acres in tenancy records |
| Q11 | Which townlands had the tenants with the smallest plots? | `smallest_townland_plots` | Same — holding_acres coverage |
| Q12 | Is there a statistical relationship between holy wells and high population? | `holy_well_population_relationship` | Descriptive comparison only; not significance-tested |
| Q13 | Is there a statistical relationship between ring forts and high population? | `ring_fort_population_relationship` | Same — descriptive comparison |
| Q14 | What was the peak period for emigration to Canada? | `canada_emigration_peak_period` | Canada detection based on arrival text keywords |
| Q15 | Which ship carried the most Coolattin families to Canada? | `ship_most_families_canada` | family_key fallback: surname+townland when no explicit key |

### Evaluation method per question

For each of the 15 questions:

1. Enter the question verbatim into the Ask page in a clean browser session (no cookies, no prior query memory).
2. Record: pipeline route (template ID or LLM), SQL executed, SSE stage durations.
3. Run the same SQL directly against `coolattin.db` via CLI to independently verify row counts.
4. For 3–5 questions: cross-check figures against the original source CSV.
5. Score the NL rewrite on three criteria (1–5 scale): factual consistency, historical appropriateness, appropriate hedging.
6. Classify outcome: **Correct** / **Partially correct** (right SQL, imprecise rewrite) / **Incorrect** / **No answer**.

### Outcome table format (for dissertation)

| # | Pipeline route | Template matched | Outcome | Latency | Verified against source? |
|---|---|---|---|---|---|
| Q1 | Template | `tenant_land_gender_average` | Correct | < 1 s | Yes |
| … | … | … | … | … | … |

## 4.2 Pipeline reliability metrics

Run the 15 competency questions plus 10 additional free-form questions (not in the template library) and record:

| Metric | Definition |
|---|---|
| Template match rate (15 Qs) | Target: 100% |
| LLM SQL error rate (10 free-form Qs) | % that fail on first attempt |
| Self-repair success rate | % of errors recovered by LLM repair loop |
| Answer delivery rate (all 25 Qs) | % that return any usable answer |
| Median latency — template path | Target: < 2 s end-to-end |
| Median latency — LLM path | Target: < 15 s end-to-end |

## 4.3 Data completeness audit

Run these queries directly against `coolattin.db` and report results:

```sql
-- Record coverage
SELECT 'Total unified records' AS metric, COUNT(DISTINCT record_id) AS value FROM unified_record
UNION ALL
SELECT 'With emigration flag', COUNT(DISTINCT record_id) FROM unified_record WHERE has_emigration_record=1
UNION ALL
SELECT 'With eviction flag', COUNT(DISTINCT record_id) FROM unified_record WHERE has_eviction_record=1
UNION ALL
SELECT 'With tenancy flag', COUNT(DISTINCT record_id) FROM unified_record WHERE has_tenancy_record=1
UNION ALL
SELECT 'With known age', COUNT(DISTINCT record_id) FROM unified_record WHERE age IS NOT NULL
UNION ALL
SELECT 'With known gender', COUNT(DISTINCT record_id) FROM unified_record WHERE gender IS NOT NULL AND gender != ''
UNION ALL
SELECT 'With holding_acres', COUNT(DISTINCT record_id) FROM unified_record WHERE holding_acres IS NOT NULL
UNION ALL
SELECT 'With ship_name', COUNT(DISTINCT record_id) FROM unified_record WHERE ship_name IS NOT NULL AND ship_name != ''
UNION ALL
SELECT 'Widow-flagged', COUNT(DISTINCT record_id) FROM unified_record WHERE is_widow=1
UNION ALL
SELECT 'Canada-destination flagged', COUNT(DISTINCT record_id) FROM unified_record WHERE is_canada_destination=1;

-- Townland coverage
SELECT 'Townlands total' AS metric, COUNT(*) AS value FROM townland
UNION ALL
SELECT 'Townlands with census data', COUNT(DISTINCT townland_id) FROM census_record
UNION ALL
SELECT 'Townlands with clearances data', COUNT(DISTINCT townland_id) FROM clearances_record
UNION ALL
SELECT 'Townlands with heritage features', COUNT(DISTINCT townland_norm) FROM heritage_feature;

-- Census year coverage
SELECT year, SUM(total) AS estate_population, COUNT(*) AS townland_count FROM census_record GROUP BY year ORDER BY year;
```

## 4.4 Source-to-database traceability sample

Select 20 emigration records from the source CSV (`unified_processed.csv`). For each:
- Locate the corresponding row in `unified_record` by `record_id`
- Verify: `ship_name`, `townland_norm`, `year`, `is_canada_destination` match expected values
- Report: clean match / normalisation applied / not found

## 4.5 Answer quality scoring

For each of the 15 NL rewrites:

| Criterion | Score 1 | Score 3 | Score 5 |
|---|---|---|---|
| **Factual consistency** | Figures contradict the data table | Minor imprecision | Perfectly consistent with raw rows |
| **Historical appropriateness** | Anachronistic or generic language | Adequate | Uses appropriate C19 Irish historical vocabulary |
| **Appropriate hedging** | Overclaims or hides data gaps | Some hedging | Clearly flags coverage limitations |

## 4.6 Optional comparative evaluation (Declan's recommendation)

If implemented before submission, compare the existing NL→SQL pipeline against a minimal NL→SPARQL prototype on the subset of questions best suited to graph traversal:

| Criterion | NL→SQL (SQLite) | NL→SPARQL (Fuseki) |
|---|---|---|
| Correctness on Q7, Q8, Q9, Q14, Q15 | | |
| Median latency | | |
| Pre-1841 census data accessible | No | Yes (if uplifted) |
| Cross-source joins | Native SQL | SPARQL OPTIONAL / UNION |
| Template-matching possible | Yes | Not applicable |
| Hallucination risk | Low (template path) | Medium (LLM SPARQL) |
| Explainability | SQL is readable | SPARQL is readable |

---

# 5. 12-Week Time Plan

**Start:** 12 May 2026 · **Submission target:** 3 August 2026

## Gantt overview

```
Week  Date range          Phase
─────────────────────────────────────────────────────────────────────
 1    12–18 May           System hardening · send plan to supervisors
 2    19–25 May           Data quality audit · code freeze
 3    26 May–1 Jun        Evaluation run: 15 competency questions
 4    2–8 Jun             Evaluation run: pipeline reliability + quality
 5    9–15 Jun            Writing: Introduction + Literature Review (part 1)
 6    16–22 Jun           Writing: Literature Review (part 2) + Background
 7    23–29 Jun           Writing: System Design + Architecture
 8    30 Jun–6 Jul        Writing: Implementation / Methodology
 9    7–13 Jul            Writing: Evaluation + Discussion
10    14–20 Jul           Writing: Conclusion + Abstract + References
11    21–27 Jul           Full draft review · revisions · demo prep
12    28 Jul–3 Aug        Final polish · formatting · submission
```

## Week-by-week detail

### Week 1 — 12–18 May 2026
**Send plan. Verify system.**

- [ ] Send this document to Prof Declan O'Sullivan and Dr Ciarán Wallace
- [ ] Run all 15 competency questions on the live Azure deployment — confirm each returns an answer
- [ ] Confirm `population_trend_1841_1861` warning about 1821 is visible in the response
- [ ] Check whether the estate GeoJSON contains any pre-1841 population data beyond 1827 — if so, document it; if not, confirm the 1821 data gap in writing
- [ ] Verify Azure deployment is stable (environment variables, gunicorn process, WAL mode)
- [ ] Fix any template keyword-matching issues discovered during live testing

**Deliverable:** Supervisor plan sent. 15 questions verified on live site.

---

### Week 2 — 19–25 May 2026
**Data quality audit. Code freeze.**

- [ ] Run data completeness SQL queries (§4.3) against `coolattin.db`; save results as a working document
- [ ] Spot-check 20 source rows for `is_widow` accuracy (compare against raw CSV)
- [ ] Spot-check 20 source rows for `is_canada_destination` accuracy (compare against `arrival` text)
- [ ] Report `family_key` and `family_size_estimate` coverage for Q6
- [ ] Verify `holy_well` and `ring_fort` features are loaded and townland-normalised for Q12/Q13
- [ ] **Code freeze for core features** — no new features after this point unless evaluation reveals a critical gap
- [ ] Tag Git commit: `git tag v1.0-evaluation-start`

**Deliverable:** Data quality working document. Code freeze tag.

---

### Week 3 — 26 May – 1 June 2026
**Formal evaluation: competency questions.**

- [ ] Clean browser session; run all 15 questions verbatim
- [ ] For each: record pipeline route, SQL, NL rewrite text, stage durations from SSE events
- [ ] Independently verify each answer by running SQL against `coolattin.db` via CLI
- [ ] Cross-check Q2, Q5, Q14, Q15 figures against source CSV
- [ ] Classify each outcome: Correct / Partially correct / Incorrect / No answer
- [ ] Fill in the evaluation table (§4.1 outcome table format)
- [ ] Share Q7/Q8/Q9 answers with Dr Ciarán Wallace for domain-expert review

**Deliverable:** Complete evaluation table for all 15 questions.

---

### Week 4 — 2–8 June 2026
**Formal evaluation: pipeline reliability and answer quality.**

- [ ] Run 10 additional free-form questions not in template library; record LLM SQL error rate, repair rate, delivery rate
- [ ] Measure latency: median for template path and LLM path
- [ ] Score NL rewrites for all 15 questions on the three quality criteria (§4.5)
- [ ] Complete source-to-database traceability exercise (§4.4) — 20 emigration records
- [ ] (Optional) Begin setting up Apache Jena Fuseki for comparative NL→SPARQL prototype

**Deliverable:** Full evaluation dataset ready (all metrics collected).

---

### Week 5 — 9–15 June 2026
**Writing: Introduction + Literature Review (part 1).**

- [ ] Write Introduction (~1,500 words): research problem, Coolattin historical context, system overview, dissertation structure
- [ ] Write Literature Review part 1 (~1,200 words): NL-to-SQL systems — WikiSQL benchmark, Spider benchmark, RAG-SQL approaches, LLM-based SQL generation accuracy surveys
- [ ] Begin Literature Review part 2: DH linked data, Irish historical systems, KG enrichment

**Target word count by end of week: ~2,700 words**

---

### Week 6 — 16–22 June 2026
**Writing: Literature Review (complete) + Background.**

- [ ] Complete Literature Review (~1,300 more words): VRTI, Griffith's Valuation, Landed Estates DB, reproducibility in computational humanities
- [ ] Write Background chapter (~1,000 words): Coolattin Estate history, source data provenance, why data warehouse is appropriate for static historical records
- [ ] Supervisor check-in: share Introduction + Literature Review draft via email
- [ ] (Optional) Complete NL→SPARQL prototype if pursuing comparative evaluation

**Target cumulative word count: ~5,000 words**

---

### Week 7 — 23–29 June 2026
**Writing: System Design and Architecture.**

- [ ] Write System Design chapter (~2,500 words) covering:
  - Overall architecture diagram (sources → ingest → SQLite → Flask → frontend)
  - Data integration pipeline: source formats, normalisation, derived fields
  - Database schema design rationale
  - Ask pipeline seven stages in full
  - Analytics module discovery pattern
  - Frontend architecture: SSE consumption, chart rendering, Leaflet map
  - Deployment architecture (Azure App Service, gunicorn, WAL mode)
- [ ] Produce architecture diagram (Mermaid or draw.io)
- [ ] Address Declan's point: justify the data warehouse approach explicitly (static data assumption)

**Target cumulative word count: ~7,500 words**

---

### Week 8 — 30 June – 6 July 2026
**Writing: Implementation.**

- [ ] Write Implementation chapter (~2,000 words) covering:
  - Ingest pipeline in detail: GeoJSON parsing, VRTI SPARQL queries, derived-field inference
  - Template library design: scoring function, threshold, verified set
  - LLM integration: prompt construction (`_build_prompt_schema`), schema injection, repair loop
  - VRTI enrichment: parallel execution, caching, graceful degradation
  - PDF generation: hand-written PDF 1.4, no library dependency
  - Workhouse cross-reference: fuzzy matching logic
- [ ] Include code excerpts: template scoring, SSE event format, guardrail regex

**Target cumulative word count: ~9,500 words**

---

### Week 9 — 7–13 July 2026
**Writing: Evaluation + Discussion.**

- [ ] Write Evaluation chapter (~2,500 words): all five evaluation dimensions with collected data
- [ ] Write Discussion (~1,000 words): what worked, where data limits answers, comparison with prior systems, implications for DH research infrastructure
- [ ] If NL→SPARQL prototype was implemented: include comparative results table

**Target cumulative word count: ~13,000 words**

---

### Week 10 — 14–20 July 2026
**Writing: Conclusion + Abstract + References + Appendices.**

- [ ] Write Conclusion (~1,000 words): summary of CS and DH contributions, key evaluation findings, reflection on NL→SQL vs NL→SPARQL trade-off, pointer to future work
- [ ] Write Abstract (~300 words)
- [ ] Compile References (target: 30–50 citations; include NL-to-SQL benchmarks, DH linked data, Irish history sources)
- [ ] Assemble Appendices:
  - A: Full database schema (DDL)
  - B: Template library (representative sample of 30)
  - C: Complete competency question evaluation table
  - D: Sample PDF export screenshot

**Target cumulative word count: ~14,300 words + appendices**

---

### Week 11 — 21–27 July 2026
**Review + revisions + demo preparation.**

- [ ] Share full draft with supervisors — request targeted feedback: Declan on CS rigour (evaluation chapter); Ciarán on historical framing
- [ ] Self-review: verify every system claim against the actual code
- [ ] Check all figures and tables for consistency with actual system output
- [ ] Proofread for grammar, clarity, academic register
- [ ] **Demo preparation (4 hours):**
  - Prepare 12-minute demonstration script: home/map → census → analytics → heritage → Ask (run Q1, Q2, Q14, Q15 live) → PDF export
  - Test live Azure deployment
  - Prepare fallback: recorded video + screenshots

**Deliverable:** Revised dissertation ready for final formatting. Demo script complete.

---

### Week 12 — 28 July – 3 August 2026
**Final polish and submission.**

- [ ] Apply final supervisor feedback
- [ ] Final proofread
- [ ] Format to TCD dissertation template (title page, declaration, table of contents, page numbers)
- [ ] Export final PDF
- [ ] Submit via TCD online submission system by **3 August 2026**
- [ ] Tag the final commit: `git tag v1.0-dissertation-submission`

---

## Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Azure deployment unstable before demo | Medium | High | Keep local version runnable; pre-record demo video |
| VRTI SPARQL endpoint unavailable | Medium | Low | Ask page degrades gracefully; VRTI enrichment is parallel, non-blocking |
| 1821 data not recoverable anywhere | High | Low | Already documented as known limitation; system handles it with a warning |
| Supervisor feedback in Week 11 requires major changes | Low | High | Share early drafts in Weeks 6 and 10 to catch issues early |
| LLM API (OpenRouter) rate-limited or key expiry | Low | Low | Ollama local fallback already implemented |
| Word count exceeds programme limit | Medium | Medium | Write to chapter word targets; cut implementation details first |
| NL→SPARQL prototype not complete in time | High | Low | Design study sufficient; full implementation is a bonus, not required |

---

# 6. Future Scope and Extra Deliverables

Items are organised by effort level and academic/research impact. Priority 0 is supervisor-endorsed for this dissertation cycle. All others are post-submission.

## Priority 0 — Supervisor-endorsed extension (for this dissertation if time permits)

### P0.1 — NL→SPARQL Comparative Pipeline

Prof Declan O'Sullivan specifically recommended this as a CS contribution of value. The existing pipeline (NL→SQL→SQLite) would be compared with a second pipeline (NL→SPARQL→Fuseki) on the same question set.

**Minimum viable implementation for dissertation:**
1. Write a Python script to uplift 5–10 townlands × census + emigration records into Turtle RDF (no RML toolchain required — hand-written mapping is fine for a prototype)
2. Load into Apache Jena Fuseki running locally (single JAR, free, no cloud required)
3. Write one Python function that replaces `_run_read_only_query(sql)` with a SPARQL equivalent against the Fuseki endpoint
4. Run Q7, Q8, Q9, Q14, Q15 through both pipelines; fill in the comparison table (§4.6)

**Why this is achievable:** The LLM prompt construction, call, and response parsing already exist in `ask_service.py`. The only new code is the schema descriptor for SPARQL (replacing the SQL schema block) and the Fuseki HTTP call (replacing `sqlite3.connect`). Estimated: 2–3 weeks if started in Week 6.

**If full implementation is not possible:** A design study is acceptable — architecture diagram, sample SPARQL queries, analysis of trade-offs. This still addresses Declan's point and is publishable.

---

## Category 1 — CS / Architecture Extensions (post-submission)

### 1.1 RML/R2RML Mapping Pipeline

Replace the Python ingest scripts with a declarative RML or R2RML mapping. RML is a W3C standard for heterogeneous-source-to-RDF transformation. This would make the integration pipeline inspectable, transferable, and citable as a methodological contribution in the semantic web space.

**Effort:** 4–6 weeks.

### 1.2 Local Triplestore as Runtime Query Engine

Replace (or parallel to) SQLite with Apache Jena Fuseki or GraphDB Community as the primary query engine, with the full dataset in RDF. Enables SPARQL graph traversal, federated queries joining local data with the live VRTI endpoint, and OWL reasoning.

**Challenge:** NL→SPARQL accuracy is currently lower than NL→SQL for relational-shaped data (fewer few-shot examples, more complex syntax). A hybrid approach (SQLite for analytics, triplestore for graph traversal) is more practical.

**Effort:** 6–10 weeks for full replacement.

### 1.3 RAG-Enhanced Query Pipeline

Add a Retrieval-Augmented Generation layer: before the LLM SQL generation call, retrieve the top-K most similar approved questions from a vector store of the `ask_query_memory` table and include them as few-shot examples. The existing feedback loop already collects the training data.

**Implementation:** `sentence-transformers/all-MiniLM-L6-v2` + SQLite-VSS (vector search extension, zero extra infrastructure).

**Effort:** 2–3 weeks.

### 1.4 Multi-Model LLM Evaluation

Run the 15 competency questions through multiple LLMs (GPT-4o, Claude Sonnet 4.6, Mistral-7B, Llama-3-8B via Ollama) and compare SQL accuracy, NL rewrite quality, and latency. The existing pipeline already supports provider switching via env vars — the new work is only in running and tabulating the experiment.

**Effort:** 1–2 weeks. Publishable result.

### 1.5 Formal NL-to-SQL Benchmark Comparison

Evaluate the LLM SQL generation component (not the full pipeline) against a subset of the Spider or WikiSQL benchmark to establish a baseline accuracy figure that places the system in the broader literature.

**Effort:** 1–2 weeks.

---

## Category 2 — Data and Coverage Extensions (post-submission)

### 2.1 Pre-1841 Population Data

The estate GeoJSON includes survey years 1827 and 1839. The VRTI KG census data begins at 1841. The gap: 1821 (the year Dr Wallace's Q8 asks about) is not in either source. Possible sources: National Archives of Ireland 1821 census fragments, Wicklow County Archives, Fitzwilliam Estate Papers (National Library of Ireland). If obtained in CSV form, the ingest pipeline requires only a new `census_seed.py` variant.

**Effort:** Data sourcing is the bottleneck. Ingest is trivial once data is in hand.

### 2.2 Additional Estate Datasets

Extend to neighbouring Wicklow estates (Fitzwilliam, Tighe) or Landed Estates Court records for the same area. Enables cross-estate comparison of eviction rates and emigration patterns.

**Effort:** Depends on data format. The ingest architecture is designed for extension.

### 2.3 Ship Voyage and Passenger Manifests

Cross-reference ship names in the emigration records against Library and Archives Canada or National Archives passenger manifests. Enables verification and enrichment of Q14/Q15 and new questions: "Which passengers from the Dunbrody also appear in the eviction records?"

**Effort:** 3–4 weeks if manifests are in machine-readable form.

---

## Category 3 — Digital Humanities Features (post-submission)

### 3.1 Genealogical Record Linkage

Implement a probabilistic record linkage algorithm to connect the same individual across record types: tenant → emigrant → census household. Blocking by townland + surname, scoring by forename similarity, year proximity, household composition. This is one of the hardest open problems in genealogical computing and a genuinely original research contribution.

**Effort:** 4–6 weeks. Quality must be evaluated — precision and recall on a hand-labelled sample.

### 3.2 Family Network Visualisation

Add a D3.js or Cytoscape.js graph view showing family connections: households grouped by surname and townland, edges for shared emigration voyages, co-tenancy, or family key links.

**Effort:** 3–4 weeks.

### 3.3 Irish Language (Gaeilge) Interface

Add Irish-language translations of all interface text, and optionally accept questions in Irish (translation step before the NL→SQL pipeline). Aligns with VRTI's bilingual mandate.

**Effort:** 2–3 weeks for interface translation. Irish-language question input needs a translation prefix in the LLM prompt.

### 3.4 IIIF Document Viewer

If scanned images of the original ledger pages are available from the National Archives or National Library of Ireland, embed a IIIF viewer (Universal Viewer or Mirador) to show the source document alongside the structured record.

**Effort:** 2–3 weeks if IIIF manifests are available from the holding institution.

### 3.5 Community Annotation Layer

Allow authenticated users (family researchers, local historians) to add corrections and contextual notes to individual records. Annotations stored separately and displayed alongside structured data.

**Effort:** 4–6 weeks (authentication, annotation schema, moderation).

---

## Category 4 — Infrastructure (post-submission)

### 4.1 PostgreSQL Migration

Replace SQLite with a PostgreSQL instance (e.g., Azure Database for PostgreSQL) for multi-user concurrent access, full-text search (`tsvector`), and PostGIS spatial queries. The `repositories/` layer isolates all SQL, making the migration less disruptive than it would otherwise be.

**Effort:** 2–3 weeks.

### 4.2 OpenAPI REST Layer

Expose the core data queries as a documented REST API with an OpenAPI 3.1 specification, allowing other DH projects to programmatically access Coolattin data.

**Effort:** 2–3 weeks.

### 4.3 Scheduled Data Refresh

Implement a nightly Azure Container Job to re-query the VRTI SPARQL endpoint for any updates and refresh the serving layer. Add uptime monitoring and answer delivery rate tracking.

**Effort:** 1–2 weeks.

---

## Priority matrix

| Item | CS Impact | DH Impact | Effort | For this dissertation? |
|---|---|---|---|---|
| P0.1 NL→SPARQL comparison | High | Medium | Medium (2–3 wk) | **Yes — highest priority** |
| 1.3 RAG pipeline | High | Low | Low (2–3 wk) | Possible if time allows |
| 1.4 Multi-model LLM eval | High | Low | Low (1–2 wk) | Possible if time allows |
| 2.1 Pre-1841 data | Low | High | Data-dependent | Data sourcing may block |
| 3.1 Record linkage | High | High | High (4–6 wk) | Post-submission |
| 3.2 Family network visualisation | Medium | High | Medium (3–4 wk) | Post-submission |
| 1.1 RML/R2RML mapping | High | Medium | High (4–6 wk) | Post-submission |
| 1.2 Triplestore runtime | High | Medium | Very high | Post-submission |
| 3.4 IIIF viewer | Low | Very high | Medium | Depends on image access |
| 4.1 PostgreSQL migration | Medium | Low | Medium | Post-submission |
| 3.3 Irish language | Low | High | Medium | Post-submission |
| 3.5 Community annotation | Low | High | High | Post-submission |

---

*Document generated: 11 May 2026. Update this document when significant architectural changes are made or when supervisor feedback changes the evaluation or contribution framing.*
