# System Architecture and Complete Workflow
## Coolattin Estate Records Explorer

**Project:** Coolattin Estate Records Explorer  
**Candidate:** Pranjal Yadav  
**Document type:** Technical reference — covers every component, every function, every data flow  
**Status:** Living document — update whenever architecture changes

---

## 1. System Overview

The Coolattin Estate Records Explorer is a single-server Flask web application deployed on Azure App Service (Italy North region). The application integrates five heterogeneous historical data sources into a unified SQLite database and exposes that data through six web pages and a REST API. The most complex component is the Ask page, which implements a seven-stage natural-language-to-SQL pipeline backed by a large language model and enriched at runtime from the VRTI Knowledge Graph via SPARQL.

The system is intentionally minimal in its infrastructure footprint: one Python process, one SQLite file, no message queue, no cache server, no separate worker. This simplicity is appropriate for a research tool with a small number of concurrent users and is well-matched to the deployment constraints of an academic dissertation project.

---

## 2. High-Level Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  DATA SOURCES (external / local files)                                      │
│                                                                             │
│  ┌──────────────────┐  ┌─────────────────┐  ┌──────────────────────────┐   │
│  │ Estate GeoJSON   │  │ unified_         │  │ NMS Heritage GeoJSON     │   │
│  │ townlands.json   │  │ processed.csv    │  │ holywells + asi          │   │
│  │ (152 townlands)  │  │ (person records) │  │ (monuments)              │   │
│  └────────┬─────────┘  └────────┬────────┘  └─────────────┬────────────┘   │
│           │                     │                          │                │
│  ┌────────┴─────────┐  ┌────────┴────────┐  ┌─────────────┴────────────┐   │
│  │ VRTI SPARQL KG   │  │ Townlands.ie    │  │ Workhouse Excel          │   │
│  │ virtuoso.virtual │  │ seed JSON       │  │ workhouse_data_final.xlsx│   │
│  │ treasury.ie      │  │ (alias map)     │  │                          │   │
│  └──────────────────┘  └─────────────────┘  └──────────────────────────┘   │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │  INGEST (batch, one-shot jobs)
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  LOCAL DATABASE  (coolattin.db — SQLite 3, WAL mode)                        │
│                                                                             │
│  townland │ census_record │ clearances_record │ refresh_state              │
│  unified_record (seeded by Ask pipeline on first query)                     │
│  heritage_feature (seeded by Ask pipeline on first query)                  │
│  ask_query_memory │ ask_query_feedback                                      │
│  match_review │ source_mentions │ entity_resolution_candidates             │
│  workhouse_unified_links │ entity_resolution_decisions                     │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │  SERVE (Flask app, gunicorn, port 5001)
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  FLASK APPLICATION  (create_app.py → 7 blueprints)                          │
│                                                                             │
│  main.py     ┌── / · /analytics · /census · /ask · /heritage · /about      │
│  ask.py      ┌── /api/ask/query (SSE) · /feedback · /llm-status · /pdf     │
│  census.py   ┌── /api/census/ · /townlands · /summary · /townland · /refresh│
│  unified.py  ┌── /api/unified/records · /stats · /townlands · /surnames    │
│  map.py      ┌── /api/map/layers · /config                                 │
│  townlands.py── /api/townlands · /detail · /geojson                        │
│  exports.py  ── /api/exports/census · /regenerate                          │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │  RUNTIME ENRICHMENT (parallel, per Ask query)
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  EXTERNAL RUNTIME SERVICES                                                  │
│                                                                             │
│  ┌──────────────────────────┐   ┌──────────────────────────────────────┐    │
│  │ OpenRouter API           │   │ VRTI SPARQL endpoint                 │    │
│  │ openrouter.ai/api/v1     │   │ virtuoso.virtualtreasury.ie/sparql/  │    │
│  │ 19 free models supported │   │ TTL cache: 1 hour per townland       │    │
│  │ Fallback: Ollama local   │   │ Cooldown: 5 min on unavailability    │    │
│  └──────────────────────────┘   └──────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Application Bootstrap Sequence

### 3.1 Entry point (`app.py`)

```python
from create_app import create_app
app = create_app()
app.run(host="0.0.0.0", port=5001, debug=True)
```

### 3.2 Application factory (`create_app.py`)

Called once on startup. Executed in this order:

1. Resolve `_root` — the absolute project root path, regardless of working directory
2. Instantiate `Flask` with absolute `template_folder` and `static_folder` paths
3. Load `ActiveConfig` from `config.py` (dev or prod based on `FLASK_ENV`)
4. Call `init_db(config_class.DATABASE_PATH)` — registers the SQLite file path in `extensions._DB_PATH`
5. Call `ensure_schema()` — creates 4 tables if they don't exist; runs `ALTER TABLE ADD COLUMN` migrations for any columns added since the DB was created
6. Register 7 blueprints with their URL prefixes
7. Register 2 legacy compatibility routes (`/api/centroids`, `/api/workhouse/match/<id>`)

The application is now ready to serve requests. The `unified_record` and `heritage_feature` tables, and the `ask_query_memory`/`ask_query_feedback` tables, are created lazily on the first Ask query.

### 3.3 Configuration hierarchy (`config.py`)

All tunable values live in `Config`, `DevelopmentConfig`, or `ProductionConfig`. Environment variables override class defaults at import time.

| Config key | Class default | Env var override | Purpose |
|---|---|---|---|
| `DATABASE_PATH` | `./coolattin.db` | `DATABASE_PATH` | SQLite file location |
| `DATABASE_URL` | — | `DATABASE_URL` | PostgreSQL URL; enables pgvector backend for Ask retrieval |
| `VRTI_SPARQL_ENDPOINT` | `https://virtuoso.virtualtreasury.ie/sparql/` | — | VRTI endpoint |
| `VRTI_REQUEST_TIMEOUT` | `30` s | `VRTI_REQUEST_TIMEOUT` | SPARQL call timeout |
| `CENSUS_STALE_AFTER_DAYS` | `7` (dev) · `1` (prod) | — | Cache TTL for census data |
| `TOWNLAND_STALE_AFTER_DAYS` | `30` (dev) · `7` (prod) | — | Cache TTL for townland data |
| `STATIC_DATA_DIR` | `frontend/static/data/` | — | GeoJSON and CSV files |
| `EXPORTS_DIR` | `exports/` | — | PDF and Excel output |
| `LOG_LEVEL` | `INFO` | `LOG_LEVEL` | Logging verbosity |
| `EMBEDDING_PROVIDER` | `local` | `EMBEDDING_PROVIDER` | `local` / `cohere` / `voyage` — dense embedding provider for Ask retrieval |
| `GRAPHDB_ENABLED` | `true` | `GRAPHDB_ENABLED` | Query local GraphDB alongside SQLite and VRTI |
| `GRAPHDB_SPARQL_ENDPOINT` | `http://localhost:7200/...` | `GRAPHDB_SPARQL_ENDPOINT` | GraphDB SPARQL endpoint |
| `GRAPHDB_REQUEST_TIMEOUT` | `15` s | `GRAPHDB_REQUEST_TIMEOUT` | GraphDB query timeout |

---

## 4. Database Layer

### 4.1 Connection management (`extensions.py`)

Every database connection goes through `get_db_conn()`:
```python
conn = sqlite3.connect(str(_DB_PATH))
conn.row_factory = sqlite3.Row   # enables dict-style column access
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA foreign_keys=ON")
return conn
```

Callers are responsible for closing the connection (using `try/finally conn.close()` pattern). No connection pool is used; SQLite's WAL mode provides adequate concurrency for the read-predominantly workload.

The custom `distance_km` SQLite function (haversine formula) is registered by the Ask pipeline within `ask_service._run_read_only_query()` before executing radius queries. It is not registered globally on startup, only within the Ask pipeline's execution context.

### 4.2 Schema migrations

`ensure_schema()` runs on every startup and is idempotent:
- Tables are created with `CREATE TABLE IF NOT EXISTS`
- New columns are added with `ALTER TABLE ADD COLUMN` wrapped in `try/except` to silently skip already-existing columns
- Column definitions added post-initial-schema: `county`, `osm_id`, `osi_id`, `vrti_id`, `images_json`, `links_json`, `td_id`, `guid`, `area_sqm` on `townland`

### 4.3 Repository pattern

All SQL queries are isolated in `backend/repositories/`. Services import repository functions; no raw SQL appears outside repositories (except in `ask_service.py`, which builds SQL dynamically as part of the pipeline). The four repository files are:

**`census_repository.py`** — `find(filters)`, `upsert(record)`, `get_summary(year)`, `get_townland_detail(name)`, `list_townlands()`  
**`clearances_repository.py`** — `find(townland_id, year)`, `upsert(record)`, `list_by_townland(townland_id)`  
**`townland_repository.py`** — `find_by_name(name)`, `upsert(townland)`, `list_all()`, `find_by_kg_uri(uri)`  
**`refresh_state_repository.py`** — `get(dataset_key, stale_after_days)`, `upsert(key, source, record_count, export_file)`

---

## 5. Ingest Pipeline

The ingest pipeline populates the database from external sources. It runs at setup time and can be triggered again via the `/api/census/refresh` endpoint or manually.

### 5.1 `full_ingest.py` — Complete estate database build

**Input sources:**
- `frontend/static/data/townlands.json` — estate GeoJSON with 152 features
- VRTI SPARQL endpoint — townland metadata and census records

**Estate GeoJSON processing:**
Each GeoJSON feature contains these properties (among others):
- `TD_NAME` — English townland name
- `NAME_GA` — Irish name
- `AREA` — area in square metres
- `TD_ID`, `GUID` — estate identifiers
- `T_POP_1827`, `T_POP_1839_`, `T_POP_1848`, `T_POP_1850`, `T_POP_1860`, `T_POP_1868` — estate survey populations (totals only, no male/female breakdown)
- `CLEARANCES_1847` through `CLEARANCES_1856` — evictions per year

**Processing steps:**
1. Parse GeoJSON features
2. For each feature: upsert `townland` row with name, Irish name, area, TD_ID, GUID
3. For each estate survey year: upsert `census_record` with `source='json'`
4. For each clearance year: upsert `clearances_record`

**VRTI SPARQL enrichment (per townland):**
1. Call `get_townlands(county='Wicklow')` — returns `TownlandDTO` list
2. For each matching townland: update `townland` with KG URI, WKT geometry, centroid lat/lon, barony, civil parish, county, OSM ID, OSI ID, VRTI ID, images JSON, links JSON
3. Call `get_census_records_for_county(county='Wicklow', year=Y)` for each census year (1841, 1851, 1861, 1871, 1881, 1891)
4. For each returned `CensusRecordDTO`: upsert `census_record` with male, female, inhabited, uninhabited, `source='kg'`

**Output:** Populated `townland`, `census_record`, `clearances_record`, `refresh_state` tables.

### 5.2 `census_ingest.py` — Incremental census refresh

Called by `census_service.py` when census data is stale (TTL exceeded) or missing. Fetches only the census records for the requested year/townland filter combination, updates `census_record`, and updates `refresh_state`. Does not touch `townland` or `clearances_record`.

### 5.3 `census_seed.py` — CSV fallback seeding

If the VRTI endpoint is unavailable during a cache miss, this job loads census data from a local CSV snapshot (`data/source_snapshots/census_seed.csv`). Used as a fallback to avoid blocking the user with a network error.

### 5.4 `townlands_ingest.py` — Townlands.ie reference refresh

Downloads the Townlands.ie canonical name list for County Wicklow and writes it to `data/seed/wicklow_townlands_reference.json`. Also builds the alias map (`data/seed/townland_aliases.json`) from the reference data. Run manually; output is version-controlled.

---

## 6. Townland Name Resolution

Townland name resolution is the foundation of spatial filtering across the entire system. It is managed by `townland_service.py`, which is the **single authority** for name normalisation.

### 6.1 Normalisation pipeline (`normalize_townland_name`)

Six-step pipeline applied to every raw townland name before storage or comparison:

1. Strip leading/trailing whitespace
2. Collapse internal whitespace (`" ".join(s.split())`)
3. Strip "Townland of" prefix (`re.sub(r"^[Tt]ownland\s+of\s+"`)
4. Remove bracket content (`re.sub(r"[()]", "")`) — "Ballinacor (North)" → "Ballinacor North"
5. Remove punctuation except hyphens and apostrophes (`re.sub(r"[^\w\s\-']", "")`)
6. Convert to UPPER CASE; replace Unicode smart quotes with ASCII apostrophe

### 6.2 Alias resolution

The alias map `data/seed/townland_aliases.json` maps variant spellings (from source CSVs) to canonical names. `resolve_alias(normalised_name)` does a dict lookup; if the name is in the alias map, the canonical name is returned; otherwise the input is returned unchanged. `canonical_name(raw)` runs the full pipeline: `resolve_alias(normalize_townland_name(raw))`.

### 6.3 Fuzzy matching in the Ask pipeline

`_resolve_townland_context()` in `ask_service.py` scans the user's question for townland names using this sequence:

1. Load the townland catalogue (all canonical names from the `townland` table) — cached in-process with a 10-minute TTL (`_TOWNLAND_CATALOG_CACHE`)
2. Scan the question: for each word/phrase that could be a townland name (after removing stopwords), attempt exact match against the catalogue
3. If no exact match: use `rapidfuzz.fuzz.token_set_ratio` to find the highest-scoring candidate above threshold (80)
4. If a near-match is found: return the canonical name with a "did you mean?" warning string
5. If `townland_hint` was provided by the frontend (from the map's selected townland), use that as the canonical name directly

---

## 7. Census Service — DB-First / KG-Second Pattern

`census_service.py` implements the most architecturally significant pattern in the system: DB-first retrieval with KG fallback. This pattern ensures that:
- Every response is served from the fast local SQLite database when data is available and fresh
- The VRTI SPARQL endpoint is only called when local data is missing or stale
- Routes never see the KG directly — they only see the `CensusResponse` envelope

### 7.1 Decision flow for `get_census_data(filters)`

```
get_census_data(filters)
│
├─ Step 1: census_repository.find(filters) → records (may be empty)
├─ Step 2: refresh_state_repository.get(dataset_key) → state (fresh/stale/missing)
│
├─ Case A: records exist AND state is fresh
│   └─ return records from DB immediately (cache_status: "hit")
│
├─ Case B: records exist AND state is stale
│   ├─ serve records from DB immediately (cache_status: "stale_refresh")
│   └─ _schedule_background_refresh(filters) — async thread refresh
│
└─ Case C: records empty (DB miss)
    ├─ _ingest_from_kg_or_seed(filters)
    │   ├─ try: query VRTI KG via census_ingest.py
    │   │   ├─ success: persist to census_record, generate Excel, update refresh_state
    │   │   └─ KG empty/unavailable: fall back to census_seed.py CSV
    │   └─ return fresh records (cache_status: "miss", source: "kg_refresh" or "csv_seed")
```

### 7.2 Response envelope

All census API responses follow a consistent envelope:
```json
{
  "data": [ ... ],
  "meta": {
    "source": "kg_refresh | csv_seed | db_cache | json",
    "cache_status": "hit | stale_refresh | miss",
    "generated_at": "2026-05-11T...",
    "record_count": 42,
    "export_file": "exports/census_2026.xlsx"
  }
}
```

---

## 8. Unified Records Service

`unified_service.py` serves the person-level records from `unified_processed.csv` via an in-process Pandas DataFrame cache (`_UNIFIED_CACHE`). This is a process-lifetime cache — the DataFrame is loaded once on the first request and reused for all subsequent requests.

### 8.1 `search_records(surname, forename, townland, year, estate, limit)`

Applies filters sequentially on the cached DataFrame using Pandas boolean indexing:
- `surname`: case-insensitive partial match on `surname` column
- `forename`: case-insensitive partial match on `forename` column
- `townland`: case-insensitive partial match on `townland` column
- `year`: exact match on `year` column (converted to string for comparison)
- `estate`: case-insensitive partial match on `estate` column
- `limit`: if > 0, apply `.head(limit)` after filtering

Returns a list of dicts (one per matching row).

### 8.2 Workhouse cross-reference

`api_unified_records` in `routes/unified.py` enriches every search result with workhouse match data:
```python
match_index = get_match_index()
for r in recs:
    rid = str(r.get("record_id") or "")
    matches = match_index.get(rid, [])
    r["has_workhouse_record"] = bool(matches)
    r["workhouse_record_count"] = len(matches)
```

The match index is built lazily on first access and cached for the process lifetime.

---

## 9. Workhouse Service

`workhouse_service.py` cross-references the unified estate records against workhouse pauper register data from two Excel sheets (`workhouse_data_final.xlsx`).

### 9.1 Data structure

**Sheet "1-127":** Columns include `Pauper Name`, `Number in Register`. Name format: "Surname Forename" (reversed). Fields parsed: `raw_name`, `forename`, `surname`, `register_number`.

**Sheet "from 128":** Columns include `Names and Surnames of Paupers`, `Electoral division`, `Sex`, `Age`, `If Adult...`, `Employment or Calling`, `religious denomination`, `If disable then description`, `Name of wife or husband`, `Number of children`, `date when admitted or born in workhouse`, `Date when died or left workhouse`. Much richer record with 13 parsed fields.

### 9.2 Match index construction

`get_match_index()` builds a dict mapping `unified_record.record_id` → list of matching workhouse records:

1. Build `wh_by_name`: for each workhouse record, generate name variants (forename+surname, surname+forename, canonical) and index them
2. For each unified record: generate its name variants, look up in `wh_by_name`
3. Score matches: if the workhouse record's `electoral_division` matches the unified record's `townland` or `parish` (normalised), `location_match = True`
4. Sort matches: location-matched records first, then alphabetically by name

---

## 10. Analytics Module System

### 10.1 Protocol definition (`analytics/base.py`)

```python
@dataclass class KPI: label, value, hint
@dataclass class Chart: chart_id, title, type, data, options
@dataclass class AnalyticsResult: dataset_id, dataset_name, description, kpis, charts, notes
class AnalyticsModule(Protocol): dataset_id, dataset_name, description; compute() → AnalyticsResult
```

### 10.2 Auto-discovery (`analytics/registry.py`)

`discover_modules()` globs `analytics/*.py`, imports each via `importlib.import_module`, and checks for a `MODULE` attribute with `dataset_id` and `compute`. Duplicate `dataset_id` values raise `RuntimeError` immediately — no silent override.

### 10.3 Module inventory

**`emigrations.py` — `EmigrationAnalytics`**
- Source: `data/emigrations_records.csv`
- Column detection: flexible `_pick_col()` matching candidates against actual column names (exact then substring)
- KPIs: total records, detected column names
- Charts: "Emigration Over Time" (line, year-grouped); "Top Destinations" (bar, top 10 by destination)

**`evictions.py` — `EvictionsAnalytics`**
- Source: `data/evictions_records.csv`
- Charts: "Evictions by Decade" (line, decade-grouped); "Top Townlands (Evictions)" (bar, top 10)

**`tenancies.py` — `TenanciesAnalytics`**
- Source: `data/tenancies.csv`
- Charts: "Top Townlands (Tenancies)" (bar); "Tenancies Over Time" (line); "Top Family Names (Surnames)" (bar) — surname extraction splits on space and takes last token
- Note: `find_data_file()` searches multiple root paths for portability

**`townland_geo.py` — `TownlandsGeoAnalytics`**
- Source: `data/townlands.json` (estate GeoJSON)
- KPIs: feature count, property key count, map readiness, most common property key
- No charts — metadata only

**`unified.py` — `UnifiedAnalytics`**
- Source: `data/unified_processed.csv`
- KPIs: total records, unique surnames, unique townlands, records with year, records with estate
- Charts: "Records Over Time" (line); "Top Family Names" (bar, top 15); "Top Townlands" (bar, top 15); "Records by Estate" (doughnut, top 10); "Gender Distribution" (doughnut)

**`workhouse.py` — `WorkhouseAnalytics`**
- Source: via `workhouse_service.get_workhouse()`
- KPIs: total records loaded, source sheets detected
- No charts (match rate computation deferred to per-record lookup)

---

## 11. The Ask Pipeline — Complete Specification

### 11.1 Startup seeding (runs once per process)

Before any question is answered, three seeding operations are called:

#### `_ensure_unified_table_seeded()`

1. Checks `refresh_state` for key `'ask_unified_seed'` with schema version `'v2'`
2. If not seeded or version mismatch: reads `frontend/static/data/unified_processed.csv` into a list of dicts
3. Creates `unified_record` table if not exists (full schema with 30+ columns)
4. Creates indices: `idx_ur_townland_norm`, `idx_ur_surname`, `idx_ur_year`, `idx_ur_emigration`, `idx_ur_eviction`, `idx_ur_tenancy`, `idx_ur_widow`, `idx_ur_canada`
5. For each CSV row, computes derived fields:

| Field | Logic |
|---|---|
| `townland_norm` | `UPPER(canonical townland name after normalisation)` |
| `holding_acres` | `MAX(acres, acres_english, acres_irish / 1.6196)` — best available acreage |
| `children_count` | `(sons or 0) + (daughters or 0)` |
| `family_size_estimate` | `MAX(children_count, household_member_count fields)` |
| `is_widow` | `1` if name/notes contains widow pattern; `0` otherwise |
| `is_canada_destination` | `1` if `arrival` text contains any of: `canada`, `quebec`, `st andrews`, `grosse isle`, `grosse-isle`, `st andrew`; `0` otherwise |
| `has_emigration_record` | `1` if `departure` or `ship_name` present, or `role` indicates emigrant |
| `has_eviction_record` | `1` if source dataset is eviction/clearance |
| `has_tenancy_record` | `1` if source dataset is tenancy |

6. Bulk inserts using `executemany` for performance
7. Updates `refresh_state` with `('ask_unified_seed', now, 'csv', schema_version_v2, row_count)`

#### `_ensure_heritage_feature_seeded()`

1. Checks `refresh_state` for key `'ask_heritage_seed'` with schema version `'v1'`
2. Creates `heritage_feature` table if not exists (8 columns)
3. Processes `holywells_wicklow.geojson`:
   - For each feature: extract name, coordinates, monument class
   - Set `feature_group = 'holy_well'`, `source_dataset = 'holywells'`
   - Normalise townland: look up county field in GeoJSON properties, apply `normalize_townland_name()`
4. Processes `asi_wicklow.geojson` (Archaeological Survey of Ireland):
   - For each feature: extract monument class → map to `feature_group`
   - Mapping: `'Ring Fort'` → `'ring_fort'`; `'Holy Well'` → `'holy_well'`; others → lowercase with underscores
5. Bulk inserts, updates `refresh_state`

#### `_ensure_query_memory_schema()`

Creates `ask_query_memory` (schema v1) and `ask_query_feedback` tables if not exist. Tables store the history of approved queries and all feedback events respectively.

---

### 11.2 Stage 0 — Pre-flight analysis (synchronous, ~1 ms)

#### `_resolve_townland_context(question, townland_hint)`

1. Load townland catalogue from DB (cached 10 min)
2. Tokenise question; remove stopwords from `_TOWNLAND_STOPWORDS` set (175 stop terms)
3. Try exact match: normalise each candidate token and check against catalogue
4. Try fuzzy match via `rapidfuzz.fuzz.token_set_ratio` on candidates; threshold: 80
5. If `townland_hint` provided: use as authoritative townland, skip scanning
6. Returns: `{name: str, name_norm: str, warning: str|None, method: 'exact'|'fuzzy'|'hint'|None}`

#### `_analyse_question(question, townland_hint)`

Pure text classification — no LLM, no DB:
- Extracts: `year` (regex `\b(18[0-9]{2}|19[0-2][0-9])\b`), `surname` (6 regex patterns), `radius_km` (regex `\b(\d{1,3})\s*km\b`)
- Classifies intent flags: `asks_population`, `asks_emigration`, `asks_eviction`, `asks_tenancy`, `asks_people`, `asks_parish`, `asks_barony`, `asks_county`, `mentions_townland`
- Determines `group_by`: year / parish / townland / surname / ship_name / None
- Determines `output_mode`: grouped / list / count / detail
- Determines `primary_intent`: population / eviction / tenancy / emigration / people / geography / overview
- Determines `scope`: radius / townland / global
- Determines `preferred_tables`: list of table names to prioritise in LLM schema

Returns a dict with 13 fields used throughout the pipeline.

#### `_question_data_coverage_warnings(question)`

Returns a list of warning strings for known data gaps:
- If question contains `'1821'` + population/census/trend keywords: warns that census data begins at 1841
- If year extracted > 1900: warns that records end at 1891
- Other domain-specific coverage warnings

#### `_try_verified_analysis(question, townland_norm, analysis)`

Scores all 83 `QUESTION_TEMPLATES` against the question:

**Scoring algorithm:**
1. Lowercase the question
2. For each template: check that ALL `required_keywords` appear in the question (short-circuit if any missing)
3. Count how many `optional_keywords` appear; apply bonus score for each
4. Apply additional bonus if the template's category matches `analysis['primary_intent']`
5. If template requires `{townland_norm}` and no townland resolved: skip
6. If template requires `{year}` and no year extracted: skip
7. Select the highest-scoring template above threshold (0)
8. Substitute `{townland_norm}`, `{year}`, `{surname}` placeholders into the SQL
9. Return: `{sql, meta: {analysis_id, chart_hint, warning}, chart_hint}`

If a template in `VERIFIED_ANALYSIS_TEMPLATE_IDS` is matched, the pipeline takes the fast path — no LLM call.

#### `_find_similar_approved_queries(question, analysis, townland_norm)`

Queries `ask_query_memory` for approved SQL entries with `feedback='up'`. Scores each against the current question using:
- Keyword overlap (Jaccard-like token matching)
- Same `primary_intent`
- Same townland (if applicable)
- Same year (if applicable)

Returns sorted list; top entry is used if similarity score exceeds direct-match threshold.

---

### 11.3 Stage 1 — SQL acquisition (SSE stage: `contacting_llm`)

**Fast path A — Verified analysis template:**
- SQL taken directly from `verified_analysis['sql']`
- `llm_meta = {provider: 'verified_analysis', model: 'curated_sql', mode: 'verified_analysis', analysis_id: ...}`
- `chart_hint` set from template definition
- No LLM call; typical latency < 5 ms

**Fast path B — Approved query memory:**
- SQL taken from `direct_memory_match['sql_text']`
- `llm_meta = {provider: 'query_memory', model: 'approved_sql', mode: 'approved_memory_reuse', memory_id: ..., memory_similarity: ...}`
- No LLM call

**LLM path — `_generate_sql(question, schema, townland_norm, analysis, approved_examples)`:**

1. Build the prompt:
   ```
   SYSTEM: You are an expert SQLite query generator for the Coolattin Estate historical archive...
   
   [schema descriptor: tables, columns, row counts, categorical examples, flag distributions, query rules]
   
   [APP-INTERPRETED QUESTION PLAN:
     primary_intent: emigration
     output_mode: count
     scope: global
     ...]
   
   [approved examples: up to 3 similar approved question→SQL pairs]
   
   USER: [question]
   
   Generate a single read-only SQLite SELECT query. Return SQL only, no markdown.
   ```

2. Call `_call_llm_with_retry(messages, max_retries=OPENROUTER_MAX_RETRIES)`:
   - Primary: POST to `https://openrouter.ai/api/v1/chat/completions`
   - Headers: `Authorization: Bearer {OPENROUTER_API_KEY}`, `HTTP-Referer: {OPENROUTER_SITE_URL}`, `X-Title: {OPENROUTER_APP_TITLE}`
   - On `401`/`403` (auth failure): try next free model from `_OPENROUTER_FREE_MODELS` list
   - On timeout: retry up to `OPENROUTER_MAX_RETRIES` times
   - If all OpenRouter attempts fail: fall back to Ollama (`_call_ollama(messages)`)
   - If Ollama also fails and `ASK_ALLOW_HEURISTIC_FALLBACK=true`: use emergency `_fallback_sql()` template
   - If fallback disabled: return `_diagnostic_message_sql()` — a safe `SELECT` that returns a guidance message

3. Extract SQL from model response: strip markdown code fences, extract first `SELECT` statement

4. `_generate_vrti_postgres_query()` — simultaneously generates a conceptual SPARQL/PostgreSQL equivalent query against the VRTI schema (for display in the PDF; not executed). This call is run after the main SQL generation to avoid concurrent load on free LLM APIs.

---

### 11.4 Stage 2 — SQL guardrail (SSE stage: `framing_query`)

`_sanitize_and_validate_sql(sql)`:

1. Strip leading/trailing whitespace and markdown artifacts
2. Apply `FORBIDDEN_SQL.search(sql)` — regex: `\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|DETACH|PRAGMA|REINDEX|VACUUM|TRUNCATE|REPLACE)\b` (case-insensitive)
3. If forbidden keyword found: raise `ValueError("SQL contains forbidden operation")`
4. Check that the statement starts with `SELECT` or `WITH` (for CTEs)
5. Return sanitised SQL

If validation fails:
- If `ASK_ALLOW_HEURISTIC_FALLBACK=true`: use `_fallback_sql()` with the emergency heuristic
- Otherwise: return `_diagnostic_message_sql()` — a synthetic SELECT returning a guidance message row

---

### 11.5 Stage 3 — Database query execution (SSE stage: `querying_database`)

`_execute_with_recovery(question, townland_hint, sql, approved_examples)`:

1. **Register custom function:** `conn.create_function("distance_km", 4, _haversine_km)` — registered on every connection before query execution
2. **Execute SQL:** `conn.execute(safe_sql).fetchall()` — converts `sqlite3.Row` objects to plain dicts
3. **On `sqlite3.OperationalError`:** trigger LLM repair:
   - Send the original question, the erroneous SQL, and the SQLite error message to the LLM
   - Ask it to return a corrected SQL
   - Apply guardrail to the repaired SQL
   - Execute repaired SQL
   - If repair also fails: return empty result with `execution_meta = {mode: 'repair_failed'}`
4. **Result post-processing:**
   - Serialize non-JSON-serialisable types (dates, Decimal) to strings
   - Apply row limit if result set is very large (> 500 rows: truncate and add warning)
5. Return: `(sql_used, column_names, rows_as_dicts, warning, execution_meta)`

The `_haversine_km` function implements the great-circle distance formula:
```python
R = 6371.0  # Earth radius in km
φ1, λ1, φ2, λ2 = map(math.radians, [lat1, lon1, lat2, lon2])
dφ = φ2 - φ1; dλ = λ2 - λ1
a = math.sin(dφ/2)**2 + math.cos(φ1)*math.cos(φ2)*math.sin(dλ/2)**2
return R * 2 * math.asin(math.sqrt(a))
```

---

### 11.6 Stage 4 — VRTI Knowledge Graph enrichment (SSE stage: `querying_vrti_graph`)

`_kg_context(question, townland_norm, force=True)`:

1. Check `_VRTI_STATUS_CACHE['down_until']` — if current time < `down_until`, return immediately with empty context (cooldown active)
2. Check `_VRTI_PARISH_CACHE` for the townland key — if cached and not expired (TTL 3600 s), return cached result
3. Call `get_townland_details_by_name(name)` from `vrti_sparql.py`:
   - SPARQL query: fetches name, Irish name, WKT geometry, centroid WKT, civil parish, barony, county, OSM ID, OSI ID, VRTI ID, images, links for the named townland
   - Timeout: `VRTI_REQUEST_TIMEOUT = 30` seconds
4. If SPARQL call raises `requests.Timeout` or `requests.ConnectionError`: set `_VRTI_STATUS_CACHE['down_until'] = now + 300`; return empty context with warning
5. Format result as `{townlands: [{name, civil_parish, barony, county, ...}], parish_count: N}`
6. Store in `_VRTI_PARISH_CACHE` with expiry `now + 3600`
7. Return context

`_kg_context_to_table(kg_context)` — converts the KG context dict into column-names list + rows-list format for display and inclusion in the PDF.

---

### 11.7 Stage 5 — Output assembly (SSE stage: `preparing_output`)

Multiple sub-functions run in sequence:

#### `_build_availability_payload(question, analysis, columns, rows, townland_resolution)`

Assesses what data is available for the question:
- `has_local_data`: bool — did the SQL return any rows?
- `has_vrti_data`: bool — was VRTI enrichment populated?
- `townland_resolved`: bool — was a townland identified in the question?
- `suggested_questions`: list — follow-up question suggestions based on `analysis['primary_intent']` and the result set

#### `_build_related_insights(question, analysis, rows, townland_norm)`

Generates 1–3 related question prompts the user could ask next, based on what the current question covered and what related data exists.

#### `_build_chart_spec(question, columns, rows, availability, chart_hint)`

Determines whether the result set is chart-renderable:
- If `chart_hint` from verified analysis template: use that type directly
- Otherwise: heuristic based on column names and row counts
  - Single numeric column: suggest "number" display
  - Two columns (label + value): suggest "bar" chart
  - Time-series (year column + value): suggest "line" chart
- Returns `{type, labels, datasets, options}` compatible with Chart.js

#### `_build_answer_text(question, columns, rows, townland_norm, kg_context, availability)`

Constructs the raw (pre-LLM-rewrite) answer text from the data table:
- If zero rows: returns a "no data found" message with suggestions
- If one row, one column: returns the scalar value with column label
- If multiple rows: constructs a compact tabular text summary (top 10 rows shown)
- Appends VRTI context if populated (parish, barony, county for named townland)
- Appends relevant warnings

#### `_generate_rephrased_answer(question, actual_answer, summary_block, data_context, supporting_context, kg_context)`

Calls the LLM to rephrase the raw answer in natural language:

Prompt structure:
```
SYSTEM: You are a digital historian specialising in 19th century Irish social history.
Rephrase the following data answer about the Coolattin Estate in clear, historically-informed 
natural language. Use the actual data values. Do not invent figures. Be appropriately 
hedged where data coverage is limited.

DATA ANSWER: [actual_answer]
DATA TABLE: [llm_data_context — first 20 rows in compact format]
VRTI CONTEXT: [kg_context if available]
SUPPORTING CONTEXT: [related figures]

USER: [original question]

Provide a natural language answer (2–4 paragraphs). Cite specific figures from the data.
```

Uses the same LLM provider chain (OpenRouter → Ollama) as the SQL generation step. If unavailable, the raw `actual_answer` is used directly.

---

### 11.8 Stage 6 — PDF report generation

`_write_pdf_report(question, answer, sql, columns, rows, llm_meta, kg_context, ...)`:

Constructs a `lines: list[str]` structure containing:
1. Header: "Coolattin Archive – Ask Report" + UTC timestamp
2. Question
3. Actual data answer
4. LLM rephrased answer (if available)
5. SQL provenance: `{provider} | {model} | {mode}` for both SQL LLM and rewrite LLM
6. SQLite query text (indented)
7. VRTI PostgreSQL equivalent query (if generated)
8. KG context (townland → parish → barony lines)
9. Local results header: `N rows`
10. Column names
11. Up to 160 rows in compact `col=value | col=value` format; truncation note if > 160
12. VRTI results header + up to 100 rows
13. Summary block: `final_summary_text` + stats dict

`_build_simple_pdf(lines)` renders the lines as a raw PDF 1.4 binary:
- Page geometry: 792 pt height, 48 pt margins, 13 pt line step → ~54 lines per page
- Font: Helvetica Type1 (embedded as PDF standard font, no file loading)
- PDF structure: Catalog → Pages → Font → (Page → Content) per page
- `_escape_pdf_text(text)` encodes each line: replaces `\`, `(`, `)` with PDF escape sequences; replaces non-latin-1 characters with `?`
- Cross-reference table and trailer written manually
- Returns raw `bytes` — no external library dependency

Output: `exports/ask/ask_report_{UTC_timestamp}.pdf`

---

### 11.9 Stage 7 — Final SSE result event

The final event has `type: "result"` and contains:

```json
{
  "type": "result",
  "question": "...",
  "answer": "...",                          // raw data answer
  "actual_answer": "...",                   // same as answer
  "llm_rephrased_answer": "...",            // LLM natural-language version (may be null)
  "columns": ["col1", "col2", ...],
  "rows": [{...}, {...}, ...],
  "row_count": 42,
  "sql": "SELECT ...",                      // only if show_sql=true
  "vrti_postgres_sql": "SELECT ...",        // conceptual VRTI equivalent
  "vrti_context": {...},
  "vrti_columns": [...],
  "vrti_rows": [...],
  "chart": {                                // Chart.js-compatible spec
    "type": "bar",
    "labels": [...],
    "datasets": [{"label": "...", "data": [...]}],
    "options": {...}
  },
  "warnings": ["...", "..."],
  "pdf_url": "/api/ask/pdf/ask_report_20260511_143022.pdf",
  "availability": {
    "has_local_data": true,
    "has_vrti_data": true,
    "townland_resolved": false,
    "suggested_questions": [...]
  },
  "related_insights": [...],
  "query_provenance": {
    "strategy": "verified_analysis",        // or "llm_sql", "approved_query_memory", etc.
    "used_approved_memory": false,
    "reused_memory_id": null,
    "direct_memory_reuse": false,
    "execution_mode": "executed_as_generated",
    "approved_query_candidates": [...]
  },
  "llm_meta": {
    "provider": "openrouter",
    "model": "openai/gpt-oss-20b:free",
    "mode": "verified_analysis",
    "analysis_id": "emigration_total"
  }
}
```

---

## 12. Query Feedback and Memory System

### 12.1 Feedback recording (`POST /api/ask/feedback`)

`record_query_feedback(question, townland_hint, sql_text, vrti_postgres_sql, feedback, note, result_row_count, availability_state, llm_meta, reused_memory_id, sample_answer, summary_json)`:

1. Upsert into `ask_query_feedback` — one row per question, updated on re-submission
2. If `feedback = 'up'` and `sql_text` is not empty:
   - Check if a similar approved query already exists in `ask_query_memory`
   - If yes: update existing entry (increment `use_count`, update `last_used_at`)
   - If no: insert new entry into `ask_query_memory` with question, SQL, townland, year, category
3. Invalidate the in-process query memory cache (`_QUERY_MEMORY_CACHE['expires_at'] = 0`)

### 12.2 Memory retrieval

`_find_similar_approved_queries` queries `ask_query_memory` for all entries with `feedback='up'`. It loads them into the in-process cache (TTL 60 seconds), then scores each against the current question. The cache prevents excessive DB reads when many similar questions arrive in sequence.

The query provenance field `approved_query_candidates` in the result event shows the top 3 memory candidates and their scores, giving the frontend visibility into whether memory influenced the answer.

---

## 13. Frontend Architecture

### 13.1 Template structure

All pages extend `base.html`, which provides:
- Navigation bar with links to all pages and a language switcher (EN/GA)
- Chart.js CDN import
- Leaflet.js CDN import (for map-enabled pages)
- `i18n.js` import and initialization
- Footer

### 13.2 JavaScript modules

**`i18n.js`** — Bilingual string switching. All user-facing text is marked with `data-i18n="key"` attributes. The module defines an `STRINGS` object with EN/GA pairs and rewrites `textContent` of all marked elements on language toggle. Current language is stored in `localStorage`.

**`main.js`** — Home page: loads YouTube IFrame API for the hero video background; implements section-scroll navigation; initialises the embedded Leaflet map with townland GeoJSON overlay.

**`ask.js`** — The most complex frontend module. Manages:
- Question submission via `fetch('/api/ask/query', {method: 'POST', body: JSON.stringify({question, show_sql})})`
- SSE event parsing: reads the `ReadableStream` from the response body, splits on `data:`, parses JSON
- Progress bar: each `progress` event updates a stage indicator (stage name, status, duration_ms)
- Result rendering: populates the answer panel, SQL panel, data table, Chart.js chart, VRTI context panel, PDF download link, related insights
- Feedback buttons: sends `POST /api/ask/feedback` with thumbs up/down, question, SQL, result metadata
- Townland autocomplete: calls `GET /api/ask/townland-suggest?q=` on input; renders dropdown

**`census.js`** — Fetches census data from `/api/census/`, renders population chart (Chart.js line), townland list sidebar, and townland detail panel.

**`heritage.js`** — Manages Leaflet map with multiple toggle-able layers:
- Estate boundary polygon (from `townlands.json`)
- Holy well markers (from `holywells_wicklow.geojson`)
- Ring fort / ASI monument markers (from `asi_wicklow.geojson`)
- Monument popup: name, class, coordinates
- Layer control panel with show/hide toggles

**`analytics.js`** — Fetches analytics data from `/api/analytics/<dataset_id>`, renders KPI cards and Chart.js charts dynamically. Dataset selector triggers re-fetch.

**`map.js`** — Shared map utilities: centroid overlay markers for all townlands, click-to-select townland (sends `townland_hint` to Ask page), popup with name and record count.

### 13.3 Static data files

Files in `frontend/static/data/` are served directly by Flask's static file handler:

| File | Size | Purpose |
|---|---|---|
| `townlands.json` | ~2.5 MB | Estate GeoJSON with 152 features and all property fields |
| `unified_processed.csv` | ~varies | Person records served for analytics and unified search |
| `holywells_wicklow.geojson` | ~varies | Holy well point features |
| `asi_wicklow.geojson` | ~varies | Archaeological survey monument features |

---

## 14. VRTI SPARQL Client — Complete Function Reference

All functions in `backend/integrations/vrti_sparql.py`:

### `_execute(query: str) → list[dict]`

Internal: sends a SPARQL query to the endpoint with the PREFIXES block prepended. Uses `requests.get` with `params={'query': prefixes + query, 'format': 'application/sparql-results+json'}`. Timeout: `REQUEST_TIMEOUT = 30` seconds. Returns the `results.bindings` list, or raises on HTTP error.

### `_val(binding, key) → str | None`

Extracts the string value from a SPARQL binding dict: `binding[key]['value']` with None fallback.

### `_int_val(binding, key) → int | None`

Extracts and casts an integer value from a SPARQL binding.

### `_parse_point_wkt(wkt) → (lat, lon) | (None, None)`

Parses `POINT(lon lat)` WKT format into `(lat, lon)` tuple (note: WKT uses lon-lat order).

### `get_townlands(county, limit) → list[TownlandDTO]`

SPARQL query over the `present-day-places-v1` graph. Retrieves for each townland:
- `rdfs:label` (English name, Irish name)
- `geo:hasGeometry/geo:asWKT` (boundary polygon WKT)
- `vrti:hasCentroid/geo:asWKT` (centroid point WKT)
- `crm:P89_falls_within` chain (civil parish → barony → county labels)
- `owl:sameAs` (OSM and OSI identifiers)
- VRTI identifier
- Images and external links

### `get_wicklow_townlands(limit) → list[TownlandDTO]`

Calls `get_townlands(county='Wicklow', limit=limit)`. Used by `townlands_ingest.py`.

### `get_townland_details_by_name(name) → TownlandDTO | None`

Single-townland query by English name label. Used by Ask pipeline VRTI enrichment. Returns full TownlandDTO including images and links.

### `get_census_records_for_townland(uri) → list[CensusRecordDTO]`

Query by townland URI. Returns all census records linked to that townland. Used by `census_ingest.py`.

### `get_census_records_for_county(county, year) → list[CensusRecordDTO]`

Query for all townlands in a county for a specific year (or all years if `year=None`). Used by `full_ingest.py`.

### `get_parish_names(county) → list[str]`

Returns distinct civil parish names for a county from the KG. Used during ingest for validation.

### `probe_endpoint() → bool`

Sends a minimal `ASK { ?s ?p ?o }` query to the endpoint. Returns `True` if the endpoint responds with any result, `False` on any exception. Used by `ask_service.check_llm_status()`.

---

## 15. Deployment Configuration

### 15.1 Azure App Service

The application runs on Azure App Service in the Italy North region. The deployment uses:
- Python 3.12 runtime
- gunicorn as the WSGI server: `gunicorn -w 4 -b 0.0.0.0:8000 app:app`
- Azure-managed HTTPS termination
- Azure environment variables for secrets (`OPENROUTER_API_KEY`, `SECRET_KEY`)
- `DATABASE_PATH` configured to a persistent storage volume

### 15.2 Environment variables (complete list)

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `OPENROUTER_API_KEY` | Yes | — | LLM provider authentication |
| `SECRET_KEY` | Yes | `dev-secret-change-in-prod` | Flask session signing |
| `DATABASE_PATH` | No | `./coolattin.db` | SQLite file location |
| `FLASK_ENV` | No | `development` | `production` activates ProductionConfig |
| `ASK_LLM_PROVIDER` | No | `auto` | Force `openrouter` or `ollama` |
| `OPENROUTER_MODEL` | No | `openai/gpt-oss-20b:free` | Model for SQL generation |
| `OPENROUTER_BASE_URL` | No | `https://openrouter.ai/api/v1` | API endpoint |
| `OPENROUTER_CONNECT_TIMEOUT` | No | `10` | Connection timeout seconds |
| `OPENROUTER_REQUEST_TIMEOUT` | No | `80` | Request timeout seconds |
| `OPENROUTER_MAX_RETRIES` | No | `2` | Retry count on failure |
| `OPENROUTER_STATUS_TIMEOUT` | No | `5.0` | Health check timeout |
| `OPENROUTER_STATUS_CACHE_TTL` | No | `60` | Provider status cache seconds |
| `OLLAMA_BASE_URL` | No | `http://127.0.0.1:11434` | Local Ollama instance |
| `OLLAMA_MODEL` | No | (auto-detect) | Local model name |
| `OLLAMA_REQUEST_TIMEOUT` | No | `180` | Ollama timeout seconds |
| `OLLAMA_CONNECT_TIMEOUT` | No | `8` | |
| `OLLAMA_MAX_RETRIES` | No | `2` | |
| `OLLAMA_MODEL_CACHE_TTL` | No | `120` | Available model list cache TTL |
| `OLLAMA_KEEP_ALIVE` | No | `10m` | Keep model loaded for N minutes |
| `VRTI_REQUEST_TIMEOUT` | No | `30` | SPARQL query timeout seconds |
| `LOG_LEVEL` | No | `INFO` | `DEBUG` / `INFO` / `WARNING` |
| `ASK_GENERATE_VRTI_SQL_WITH_LLM` | No | `false` | Generate VRTI SPARQL with LLM |
| `ASK_ALLOW_HEURISTIC_FALLBACK` | No | `false` | Use emergency SQL templates on LLM failure |

### 15.3 Local development setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env.local
# Edit .env.local: add OPENROUTER_API_KEY=sk-or-...
python3 app.py
# → http://127.0.0.1:5001
```

Optional: populate the database from the VRTI KG:
```bash
python -m backend.jobs.full_ingest
```

---

## 16. In-Process Cache Summary

The application maintains several in-process caches to avoid redundant DB queries and network calls:

| Cache name | Location | TTL | Content |
|---|---|---|---|
| `_TOWNLAND_CATALOG_CACHE` | `ask_service.py` | 10 min | All canonical townland names from DB |
| `_VRTI_PARISH_CACHE` | `ask_service.py` | 60 min per townland | VRTI enrichment result per townland |
| `_VRTI_STATUS_CACHE['down_until']` | `ask_service.py` | 5 min cooldown | VRTI unavailability flag |
| `_OPENROUTER_STATUS_CACHE` | `ask_service.py` | `OPENROUTER_STATUS_CACHE_TTL` (60 s) | OpenRouter health check result |
| `_OLLAMA_MODEL_CACHE` | `ask_service.py` | `OLLAMA_MODEL_CACHE_TTL` (120 s) | Available Ollama models list |
| `_PROMPT_SCHEMA_CACHE` | `ask_service.py` | 5 min | Full schema descriptor JSON |
| `_QUERY_MEMORY_CACHE` | `ask_service.py` | 60 s | Approved query memory rows |
| `_SCHEMA_COMPAT_CACHE` | `ask_service.py` | process lifetime | Clearances column name (count/persons) |
| `_UNIFIED_CACHE` | `unified_service.py` | process lifetime | unified_processed.csv DataFrame |
| `_CENTROIDS_CACHE` | `unified_service.py` | process lifetime | Townland centroid dict |
| `_WORKHOUSE_CACHE` | `workhouse_service.py` | process lifetime | Workhouse Excel DataFrame |
| `_WORKHOUSE_MATCH_INDEX` | `workhouse_service.py` | process lifetime | record_id → workhouse matches |

All process-lifetime caches are reset on application restart. TTL-based caches use `time.perf_counter()` or `time.time()` comparisons rather than a dedicated cache framework.

---

## 17. Error Handling and Degradation Paths

The system is designed to degrade gracefully at every external dependency:

| Dependency | Failure mode | System response |
|---|---|---|
| VRTI SPARQL endpoint | Timeout or connection error | VRTI enrichment stage returns empty; 5-min cooldown; warning appended to response |
| OpenRouter API | Auth failure | Try next free model from the 19-model list |
| OpenRouter API | All models failed | Fall back to Ollama |
| Ollama | Not running | If `ASK_ALLOW_HEURISTIC_FALLBACK=true`: use emergency SQL template. Otherwise: return safe guidance message |
| SQL execution | Syntax error | One LLM repair attempt; if that also fails, return empty result with error warning |
| `unified_processed.csv` | File not found | `unified_record` table not seeded; Ask queries against it return empty |
| Heritage GeoJSON | File not found | `heritage_feature` not seeded; Q12/Q13 return no data |
| Workhouse Excel | File not found | `get_workhouse()` returns empty DataFrame; no workhouse matches shown |
| Census KG call | KG empty or failed | Fall back to `census_seed.py` CSV snapshot |

---

## 18. Complete Data Flow Diagram — Ask Query End-to-End

```
Browser: POST /api/ask/query
  {question: "How many widows appear in the records?", show_sql: true}
         │
         ▼
ask.py (route handler)
  → stream_with_context(generate())
  → answer_question_stream(question, townland_hint=None, include_sql=True)
         │
         ▼ [synchronous, ~1 ms]
Stage 0a: _ensure_unified_table_seeded()    ← noop if already seeded
Stage 0b: _ensure_heritage_feature_seeded() ← noop if already seeded
Stage 0c: _ensure_query_memory_schema()     ← noop if tables exist
         │
         ▼
_resolve_townland_context("how many widows appear in the records?", None)
  → no townland found → townland_resolution = {name_norm: None, warning: None}
         │
         ▼
_analyse_question(question, None)
  → primary_intent: "people"
  → output_mode: "count" (detected "how many")
  → group_by: None
  → scope: "global"
  → preferred_tables: ["unified_record"]
         │
         ▼
_question_data_coverage_warnings(question) → [] (no warnings)
         │
         ▼
_try_verified_analysis(question, None, analysis)
  → Scoring template "widows_count":
      required_keywords: ["widow"] ✓ (found in "widows")
      optional_keywords: ["how many"] ✓ → score = 2
  → MATCH: template_id = "widows_count"
  → sql = "SELECT COUNT(DISTINCT record_id) AS widow_records 
           FROM unified_record WHERE is_widow=1"
  → warning = "Widows are identified from widow-labelled names or notes..."
  → chart_hint: None (not in VERIFIED_ANALYSIS_CHART_HINTS)
         │
         ▼ SSE: {"type":"progress","stage":"contacting_llm","status":"completed","detail":"Using verified analysis SQL (widows_count)","duration_ms":2}
         │
         ▼
Stage 2: _sanitize_and_validate_sql(sql)
  → FORBIDDEN_SQL.search(sql) → no match ✓
  → starts with SELECT ✓
  → safe_sql = "SELECT COUNT(DISTINCT record_id) AS widow_records FROM unified_record WHERE is_widow=1"
         │
         ▼ SSE: {"type":"progress","stage":"framing_query","status":"completed","detail":"Read-only query validated","duration_ms":1}
         │
         ▼
Stage 3: _execute_with_recovery(question, None, safe_sql, [])
  → conn = get_db_conn()
  → conn.create_function("distance_km", 4, _haversine_km)
  → conn.execute("SELECT COUNT(DISTINCT record_id) AS widow_records FROM unified_record WHERE is_widow=1")
  → rows = [{"widow_records": 47}]  (example result)
  → columns = ["widow_records"]
         │
         ▼ SSE: {"type":"progress","stage":"querying_database","status":"completed","detail":"1 row returned","duration_ms":8}
         │
         ▼
Stage 4: _kg_context(question, None, force=True)
  → no townland in question → returns {townlands: [], parish_count: 0}
  → vrti_rows = [] (empty, no townland to enrich)
         │
         ▼ SSE: {"type":"progress","stage":"querying_vrti_graph","status":"completed","detail":"0 townland(s) enriched","duration_ms":5}
         │
         ▼
Stage 5: Output assembly
  → _build_answer_text → "47 widow records found in the Coolattin Estate database.\n[warning text]"
  → _generate_rephrased_answer → "The Coolattin Estate records contain 47 individuals identified as widows..."
  → _build_chart_spec → None (single scalar, not chart-renderable)
  → _write_pdf_report → exports/ask/ask_report_20260511_143022.pdf
         │
         ▼ SSE: {"type":"progress","stage":"preparing_output","status":"completed","detail":"PDF generated","duration_ms":1847}
         │
         ▼
Final SSE: {"type":"result",
  "question": "How many widows appear in the records?",
  "answer": "47 widow records found...",
  "llm_rephrased_answer": "The Coolattin Estate records contain 47 individuals identified as widows...",
  "columns": ["widow_records"],
  "rows": [{"widow_records": 47}],
  "sql": "SELECT COUNT(DISTINCT record_id) AS widow_records FROM unified_record WHERE is_widow=1",
  "warnings": ["Widows are identified from widow-labelled names or notes in the source rows."],
  "pdf_url": "/api/ask/pdf/ask_report_20260511_143022.pdf",
  "query_provenance": {"strategy": "verified_analysis", ...},
  "llm_meta": {"provider": "verified_analysis", "model": "curated_sql", "mode": "verified_analysis", "analysis_id": "widows_count"}
}
         │
         ▼
Browser: ask.js receives SSE stream
  → Renders progress bar stages as they arrive
  → On "result" event:
      → Populates answer panel with llm_rephrased_answer
      → Shows SQL in collapsible panel (show_sql=true)
      → Shows data table: 1 row × 1 column
      → Shows warning badge
      → Shows PDF download link
      → Renders feedback (thumbs up/down) buttons
```

**Total end-to-end latency for this example:** ~15–25 ms (template path, no LLM call for SQL, LLM rewrite adds ~3–8 s)

---

## 19. Security Considerations

| Attack surface | Mitigation |
|---|---|
| SQL injection via LLM-generated queries | `FORBIDDEN_SQL` regex blocks all write operations; SQLite connection is read-only by convention (no `sqlite3.connect` with `check_same_thread=False` outside ingest); parameterised queries used in repositories |
| SQL injection via template placeholder substitution | Placeholders (`{townland_norm}`, `{year}`, `{surname}`) are values extracted by the analysis layer, not direct user string concatenation; `{year}` is extracted only from `\b(18[0-9]{2}|19[0-2][0-9])\b` regex (integer only); `{townland_norm}` is the normalised upper-case name from the catalogue |
| Prompt injection via user question | The LLM prompt separates system instructions from user input clearly; the FORBIDDEN_SQL guardrail operates on the output regardless of prompt content |
| PDF path traversal | `ask_pdf_download` extracts only the filename component: `Path(filename).name` — strips any directory components |
| Excessive resource consumption | SQL results capped at 500 rows; LLM call has `OPENROUTER_REQUEST_TIMEOUT = 80 s`; PDF export truncated at 160 rows |
| VRTI endpoint abuse | 5-minute cooldown on unavailability; 1-hour per-townland cache |
