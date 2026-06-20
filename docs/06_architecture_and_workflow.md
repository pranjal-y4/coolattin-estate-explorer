# System Architecture and Complete Workflow
## Coolattin Estate Records Explorer

**Project:** Coolattin Estate Records Explorer  
**Candidate:** Pranjal Yadav  
**Document type:** Technical reference — covers every component, every function, every data flow  
**Status:** Living document — update whenever architecture changes

---

## 1. System Overview

The Coolattin Estate Records Explorer is a single-server Flask web application deployed on Azure App Service (Italy North region). The application integrates five heterogeneous historical data sources into a unified SQLite database and exposes that data through six web pages and a REST API. The most complex component is the Ask page, which implements a seven-phase orchestrated natural-language pipeline backed by a large language model and enriched at runtime from two knowledge graphs (VRTI Virtuoso + local GraphDB).

The system is intentionally minimal in its infrastructure footprint: one Python process, one SQLite file, no message queue, no cache server, no separate worker.

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
│  unified_record │ heritage_feature                                          │
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
│  │ LLM Synthesis Chain      │   │ VRTI SPARQL endpoint                 │    │
│  │ Claude (Anthropic) [1]   │   │ virtuoso.virtualtreasury.ie/sparql/  │    │
│  │ Grok (xAI) [2]           │   │ TTL cache: 1 hour per townland       │    │
│  │ OpenRouter [3]           │   │ Cooldown: 5 min on unavailability    │    │
│  │ Ollama local [4]         │   └──────────────────────────────────────┘    │
│  └──────────────────────────┘                                               │
│                                                                             │
│  ┌──────────────────────────────────────────────────┐                       │
│  │ Local GraphDB (co: ontology)                     │                       │
│  │ localhost:7200/repositories/coolattin             │                       │
│  │ or 51.120.71.162:7200 (Azure)                    │                       │
│  │ SPARQL comparison + entity neighbourhood         │                       │
│  └──────────────────────────────────────────────────┘                       │
│                                                                             │
│  ┌──────────────────────────────────────────────────┐                       │
│  │ In-process GraphRAG (graphrag.py)                │                       │
│  │ NetworkX property graph loaded from SQLite        │                       │
│  │ 49,081 nodes · 64,342 edges · BGE-large embedded  │                       │
│  │ vector seed → k-hop BFS → linearised subgraph    │                       │
│  └──────────────────────────────────────────────────┘                       │
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

1. Resolve `_root` — the absolute project root path
2. Instantiate `Flask` with absolute `template_folder` and `static_folder` paths
3. Load `ActiveConfig` from `config.py` (dev or prod based on `FLASK_ENV`)
4. Call `init_db(config_class.DATABASE_PATH)` — registers the SQLite file path in `extensions._DB_PATH`
5. Call `ensure_schema()` — creates all tables if they don't exist; runs `ALTER TABLE ADD COLUMN` migrations for any columns added since DB creation
6. Register 7 blueprints with their URL prefixes
7. Register 2 legacy compatibility routes (`/api/centroids`, `/api/workhouse/match/<id>`)

### 3.3 Configuration hierarchy (`config.py`)

| Config key | Class default | Env var override | Purpose |
|---|---|---|---|
| `DATABASE_PATH` | `./coolattin.db` | `DATABASE_PATH` | SQLite file location |
| `DATABASE_URL` | — | `DATABASE_URL` | PostgreSQL URL; enables pgvector backend |
| `VRTI_SPARQL_ENDPOINT` | `https://virtuoso.virtualtreasury.ie/sparql/` | — | VRTI endpoint |
| `VRTI_REQUEST_TIMEOUT` | `30` s | `VRTI_REQUEST_TIMEOUT` | SPARQL call timeout |
| `GRAPHDB_ENABLED` | `true` | `GRAPHDB_ENABLED` | Query local GraphDB alongside SQLite + VRTI |
| `GRAPHDB_SPARQL_ENDPOINT` | `http://localhost:7200/...` | `GRAPHDB_SPARQL_ENDPOINT` | GraphDB SPARQL endpoint |
| `GRAPHDB_REQUEST_TIMEOUT` | `15` s | `GRAPHDB_REQUEST_TIMEOUT` | GraphDB query timeout |
| `GRAPHRAG_ENABLED` | `true` | `GRAPHRAG_ENABLED` | In-process property-graph enrichment |
| `GRAPHRAG_VECTOR_TOP_K` | `8` | `GRAPHRAG_VECTOR_TOP_K` | BGE seed nodes per query |
| `GRAPHRAG_K_HOPS` | `2` | `GRAPHRAG_K_HOPS` | BFS traversal depth |
| `GRAPHRAG_MAX_NODES` | `120` | `GRAPHRAG_MAX_NODES` | Max subgraph size per query |
| `EMBEDDING_PROVIDER` | `local` | `EMBEDDING_PROVIDER` | `local` / `cohere` / `voyage` |
| `ASK_USE_NEW_PIPELINE` | `true` | `ASK_USE_NEW_PIPELINE` | Orchestrated pipeline on/off |
| `ASK_SYNTHESIS_MODEL` | `openrouter` | `ASK_SYNTHESIS_MODEL` | `claude` / `openrouter` / `ollama` |
| `LLM_ALLOW_PAID` | `false` | `LLM_ALLOW_PAID` | Allow paid API calls (required for Claude/Grok) |
| `ANTHROPIC_API_KEY` | — | `ANTHROPIC_API_KEY` | Required for `ASK_SYNTHESIS_MODEL=claude` |
| `XAI_API_KEY` | — | `XAI_API_KEY` | Grok (xAI) API key — second in synthesis chain |
| `CENSUS_STALE_AFTER_DAYS` | `7` (dev) · `1` (prod) | — | Census data cache TTL |
| `EXPORTS_DIR` | `exports/` | — | PDF and Excel output |

---

## 4. Database Layer

### 4.1 Connection management (`extensions.py`)

Every database connection goes through `get_db_conn()`:
```python
conn = sqlite3.connect(str(_DB_PATH))
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA foreign_keys=ON")
return conn
```

The custom `distance_km` SQLite function (haversine formula) is registered by the Ask pipeline within `ask_service._run_read_only_query()` before executing radius queries. It is not registered globally.

### 4.2 Schema migrations

`ensure_schema()` runs on every startup and is idempotent:
- Tables created with `CREATE TABLE IF NOT EXISTS`
- New columns added with `ALTER TABLE ADD COLUMN` wrapped in `try/except`

### 4.3 Repository pattern

All SQL queries are isolated in `backend/repositories/`. Services import repository functions; no raw SQL appears outside repositories (except in `ask_service.py` which builds SQL dynamically as part of the pipeline).

---

## 5. Ingest Pipeline

### 5.1 `full_ingest.py` — Complete estate database build

**Input sources:**
- `frontend/static/data/townlands.json` — estate GeoJSON with 152 features
- VRTI SPARQL endpoint — townland metadata and census records

**Estate GeoJSON processing:**
Each GeoJSON feature contains: `TD_NAME`, `NAME_GA`, `AREA`, `TD_ID`, `GUID`, `T_POP_1827`–`T_POP_1868` (estate survey populations), `CLEARANCES_1847`–`CLEARANCES_1856` (evictions per year).

**Processing steps:**
1. Parse GeoJSON features
2. Upsert `townland` row per feature
3. Upsert `census_record` per estate survey year
4. Upsert `clearances_record` per clearance year

**VRTI SPARQL enrichment:**
1. `get_townlands(county='Wicklow')` → TownlandDTO list
2. Update `townland` with KG URI, WKT geometry, centroid, barony, civil parish, OSM/OSI/VRTI IDs
3. `get_census_records_for_county(county='Wicklow', year=Y)` for each census year (1841–1891)
4. Upsert `census_record` with male, female, inhabited, uninhabited, `source='kg'`

### 5.2 `census_ingest.py` — Incremental census refresh

Called by `census_service.py` when census data is stale. Fetches only the records for the requested year/townland filter, updates `census_record`, updates `refresh_state`.

### 5.3 `townlands_ingest.py` — Townlands.ie reference refresh

Downloads the Townlands.ie canonical name list for County Wicklow; builds the alias map (`data/seed/townland_aliases.json`). Run manually; output is version-controlled.

---

## 6. Townland Name Resolution

Managed by `townland_service.py`, the single authority for name normalisation.

### 6.1 Normalisation pipeline (`normalize_townland_name`)

1. Strip leading/trailing whitespace
2. Collapse internal whitespace
3. Strip "Townland of" prefix
4. Remove bracket content — "Ballinacor (North)" → "Ballinacor North"
5. Remove punctuation except hyphens and apostrophes
6. Convert to UPPER CASE; replace Unicode smart quotes with ASCII apostrophe

### 6.2 Alias resolution

`data/seed/townland_aliases.json` maps variant spellings to canonical names. `canonical_name(raw)` chains: `resolve_alias(normalize_townland_name(raw))`.

### 6.3 Fuzzy matching in the Ask pipeline

`_resolve_townland_context()` scans the user question:
1. Load townland catalogue from DB (cached 10 min, `_TOWNLAND_CATALOG_CACHE`)
2. Tokenise; remove stopwords from `_TOWNLAND_STOPWORDS` (175 terms)
3. Exact match → fuzzy match via `rapidfuzz.fuzz.token_set_ratio` (threshold 80)
4. If `townland_hint` provided by the frontend: use as authoritative

---

## 7. Census Service — DB-First / KG-Second Pattern

### 7.1 Decision flow for `get_census_data(filters)`

```
get_census_data(filters)
│
├─ Case A: records exist AND state is fresh    → return from DB (cache_status: "hit")
├─ Case B: records exist AND state is stale   → serve from DB + background refresh
└─ Case C: records empty (DB miss)
    ├─ try VRTI KG via census_ingest.py
    └─ fallback: census_seed.py CSV snapshot
```

### 7.2 Response envelope

```json
{
  "data": [...],
  "meta": {
    "source": "kg_refresh | csv_seed | db_cache | json",
    "cache_status": "hit | stale_refresh | miss",
    "generated_at": "...",
    "record_count": 42
  }
}
```

---

## 8. Unified Records Service

`unified_service.py` serves person-level records from `unified_processed.csv` via an in-process Pandas DataFrame cache (`_UNIFIED_CACHE`). Loaded once on first request; reused for all subsequent requests.

### 8.1 `search_records(surname, forename, townland, year, estate, limit)`

Applies filters sequentially on the cached DataFrame using boolean indexing. Returns a list of dicts. Results are enriched with workhouse match data from `get_match_index()`.

---

## 9. Workhouse Service

`workhouse_service.py` loads `workhouse_data_final.xlsx` (two sheets) into a cached DataFrame. Match index construction: name variants for each workhouse record are built and indexed; unified records are matched by name variants + place normalisation.

---

## 10. Analytics Module System

### 10.1 Protocol (`analytics/base.py`)

```python
@dataclass class KPI: label, value, hint
@dataclass class Chart: chart_id, title, type, data, options
class AnalyticsModule(Protocol): dataset_id, dataset_name, description; compute() → AnalyticsResult
```

### 10.2 Auto-discovery (`analytics/registry.py`)

`discover_modules()` globs `analytics/*.py`, imports via `importlib`, checks for `MODULE` attribute. Duplicate `dataset_id` raises `RuntimeError`.

### 10.3 Module inventory

- `EmigrationAnalytics` — `data/emigrations_records.csv`; charts: "Over Time" (line), "Top Destinations" (bar)
- `EvictionsAnalytics` — `data/evictions_records.csv`; charts: "By Decade" (line), "Top Townlands" (bar)
- `TenanciesAnalytics` — `data/tenancies.csv`; charts: "Top Townlands", "Over Time", "Top Surnames"
- `TownlandsGeoAnalytics` — `data/townlands.json`; KPIs only (metadata)
- `UnifiedAnalytics` — `data/unified_processed.csv`; 5 KPIs, 5 charts
- `WorkhouseAnalytics` — via `workhouse_service.get_workhouse()`; KPIs only

---

## 11. The Ask Pipeline — Orchestrated 7-Phase System

**Entry point:** `_orchestrated_pipeline_stream()` in `backend/services/ask_service.py`  
**Enabled by:** `ASK_USE_NEW_PIPELINE=true` (default)  
**Legacy fallback:** `_simple_pipeline_stream()` (if flag is false or orchestrated fails)

### 11.1 Startup seeding (runs once per process)

#### `_ensure_unified_table_seeded()`

1. Checks `refresh_state` for key `'ask_unified_seed'` with schema version `'v2'`
2. Creates `unified_record` table (30+ columns) if not exists; creates 9 indexes
3. Reads `unified_processed.csv` (13,707 rows); computes derived fields:

| Derived field | Logic |
|---|---|
| `townland_norm` | `UPPER(canonical_name(townland))` |
| `holding_acres` | `MAX(acres, acres_english, acres_irish / 1.6196)` |
| `children_count` | `(sons or 0) + (daughters or 0)` |
| `is_widow` | `1` if name/notes contains widow pattern |
| `is_canada_destination` | `1` if arrival text contains canada/quebec/grosse isle |
| `has_emigration_record` | `1` if departure/ship_name present or role indicates emigrant |
| `has_eviction_record` | `1` if source dataset is eviction/clearance |
| `has_tenancy_record` | `1` if source dataset is tenancy |

#### `_ensure_heritage_feature_seeded()`

Creates `heritage_feature` table from `holywells_wicklow.geojson` + `asi_wicklow.geojson`. Monument class → `feature_group` mapping: `'Ring Fort'→'ring_fort'`, `'Holy Well'→'holy_well'`.

#### `_ensure_query_memory_schema()`

Creates `ask_query_memory` and `ask_query_feedback` tables if not exists.

---

### 11.2 Pre-flight Analysis (synchronous, ~1–5 ms, 0 LLM calls)

#### `_resolve_townland_context(question, townland_hint)`

1. Load townland catalogue from DB (cached 10 min, `_TOWNLAND_CATALOG_CACHE`)
2. Tokenise question; remove stopwords from `_TOWNLAND_STOPWORDS` (175 terms)
3. Try exact match; then fuzzy match via `rapidfuzz.fuzz.token_set_ratio` (threshold 80)
4. If `townland_hint` provided: use as authoritative
5. Returns: `{name, name_norm, sql_id, kg_uri, warning, method}`

#### `_analyse_question(question, townland_hint)`

Pure text classification — no LLM, no DB:
- Extracts: `year` (regex `\b(18[0-9]{2}|19[0-2][0-9])\b`), `surname` (6 patterns), `radius_km`
- Classifies: `primary_intent`, `output_mode`, `group_by`, `scope`, `preferred_tables`
- Returns dict with 13 fields used throughout the pipeline

---

### 11.3 Four Fast Lanes (first match short-circuits routing)

These run before intent classification. If any fires, the pipeline skips to SQL execution.

#### Fast Lane 1 — Rule-based slot-fill

`semantic_layer.try_rule_based_fill(question, analysis)`:
- Matches question against 22 registered metric `keywords` entries
- Confidence scoring:
  ```
  confidence = 1.0
  if competing_metrics > 1:
      confidence = max(0.82, 1.0 - 0.06 × (competing_metrics - 1))
  if not filters and not dimensions:
      confidence = min(confidence, 0.90)
  ```
- If confidence ≥ 0.80 → compile SQL directly (0 LLM calls, < 5 ms)

#### Fast Lane 2 — Verified template

`_try_verified_analysis(question, townland_norm, analysis)` scores 83 `QUESTION_TEMPLATES`:
1. Lowercase the question
2. For each template: check ALL `required_keywords` appear (short-circuit if any missing)
3. Count `optional_keywords` for bonus score; apply intent category bonus
4. Skip templates with unresolved `{townland_norm}` or `{year}` placeholders
5. Select highest-scoring template; substitute placeholders into SQL
6. If template in `VERIFIED_ANALYSIS_TEMPLATE_IDS`: confidence = 1.0

#### Fast Lane 3 — Direct memory reuse

`_find_similar_approved_queries()` queries `ask_query_memory` (TTL 60 s cache).  
Scoring: `token_sort_ratio` (rapidfuzz) + cosine similarity.  
Threshold: combined score ≥ 0.55 → reuse approved SQL directly.

#### Fast Lane 4 — Embedding template (Phase 4 retrieval)

`embedding_index._phase4_retrieve(question)`:
- TF-IDF unigram+bigram cosine similarity over templates and approved memory
- Merged by RRF (Reciprocal Rank Fusion)
- Threshold: cosine ≥ **0.68** AND ALL `required_keywords` present in question
- Hit → use template SQL directly (no LLM)

---

### 11.4 Intent Classification — Phase 5

**File:** `backend/services/intent_router.py`, function `classify_intent(question, analysis, slot_fill)`

Priority order (first match wins):

**1. COMPARATIVE** — any keyword present: `compare`, `compared to`, `versus`, `vs`, `difference between`, `contrast`, `relative to`, `how does`, `how did`, `better than`, `worse than`, `more than`, `less than`, `higher than`, `lower than`, `against`

**2. RELATIONAL** — any keyword from:
- *Relational*: `related to`, `connected to`, `link between`, `in the same parish`, `same barony`, `part of`, `neighbouring`, `adjacent to`, `bordering`, `relationship between`, `linked to`
- *Hierarchy*: `which parish`, `what parish`, `civil parish`, `in the barony`, `townlands in`, `where is`, `where does`, `located in`, `situated in`, `falls within`
- *Heritage*: `heritage`, `archaeological`, `monument`, `ring fort`, `holy well`, `history of`, `tell me about`, `describe`, `historically`, `fortification`, `earthwork`
- *Sensemaking*: `overview`, `about the estate`, `about coolattin`, `describe the estate`, `what kind of`, `background`, `summary of`, `general context`
- **Core Rule 1 override:** if ONLY heritage/sensemaking keywords (no relational/hierarchy/geography signal) AND `output_mode` is `count`/`aggregate` AND any analytical keyword present → falls through to ANALYTICAL

**3. ANALYTICAL** — any of:
- `primary_intent` in `{population, eviction, emigration, tenancy}`
- `output_mode` in `{count, aggregate, trend}`
- Any keyword: `how many`, `how much`, `total`, `count of`, `average`, `proportion`, `percent`, `per year`, `by year`, `trend`, `over time`, `distribution`, `breakdown`, `most`, `least`, `highest`, `lowest`, `sum of`, `rate`, `ratio`
- `slot_fill is not None`

**4. FALLBACK** — default

SSE event: `{type:"progress", stage:"classifying_intent", status:"completed"}`

---

### 11.5 ANALYTICAL Lane — Semantic Layer (Phase 2)

**File:** `backend/services/semantic_layer.py`

#### Slot-fill model

```python
@dataclass
class SlotFill:
    metric: str                  # key into METRIC_REGISTRY (22 metrics)
    dimensions: list[str]        # GROUP BY columns
    filters: dict[str, Any]      # WHERE conditions
    group_mode: str              # "aggregate"|"trend"|"grouped"|"detail"
    limit: int | None
    order_by_override: str | None
    confidence: float = 1.0
    source: str = "rule"         # "rule" | "llm"
```

#### Metric registry (22 metrics, selected)

| Metric key | Aggregate | Base WHERE |
|---|---|---|
| `emigration_count` | `COUNT(DISTINCT record_id)` | `has_emigration_record = 1` |
| `eviction_event_count` | `COUNT(DISTINCT record_id)` | `has_eviction_record = 1` |
| `population` | `SUM(total)` | `source='json' OR source='kg'` (census_record) |
| `tenancy_count` | `COUNT(DISTINCT record_id)` | `has_tenancy_record = 1` |
| `widow_count` | `COUNT(DISTINCT record_id)` | `is_widow = 1` |
| `avg_holding_acres` | `AVG(holding_acres)` | `holding_acres IS NOT NULL` |
| `canada_emigration_count` | `COUNT(DISTINCT record_id)` | `is_canada_destination = 1` |
| `population_change` | `SUM(total) GROUP BY year` | (census_record join) |

Each metric also defines `dim_select`, `dim_group_by`, `filter_where`, `sparql_agg`, and `keywords`.

#### Three compilation paths

**Path A — Rule-based (0 LLM calls):**
- Match question keywords against metric `keywords` entries
- Build `SlotFill` from `analysis` dict fields
- If confidence ≥ 0.80 → `compile_sql()` directly

**Path B — LLM slot-fill:**
- Build prompt with annotated metric registry + question
- LLM returns JSON: `{metric, dimensions, filters, group_mode, confidence}`
- `parse_slot_fill()` validates; if confidence ≥ 0.70 → `compile_sql()`
- If confidence < 0.60 → reject, fall through to FALLBACK

**Path C — Deterministic SQL compiler:**

`compile_sql(slot_fill)` assembles SQL from registry — never free-form LLM SQL:
```sql
SELECT {dim_selects}, {aggregate} AS {alias}
FROM {from_clause}
WHERE {base_where} {filter_wheres}
GROUP BY {dim_group_bys}
ORDER BY {order_by}
LIMIT {limit}
```

#### SPARQL compilation (RQ6)

`compile_sparql(slot_fill)` generates SPARQL from `sparql_agg` template for GraphDB comparison.

SSE events: `slot_filling` (LLM slot-fill) → `schema_sql` (compilation)

---

### 11.6 RELATIONAL Lane — Subgraph Engine (Phase 3)

**File:** `backend/services/subgraph_engine.py`

`retrieve_subgraph(question, entity_uri, k=2)`:

1. **VRTI multi-hop SPARQL** — townland → parish → barony → county; sibling townlands; external links (OSM, OSI, Logainm, VRTI)
2. **GraphDB neighbourhood expansion** — `get_entity_neighborhood(name, k=2, max_nodes=40)` over `co:` ontology; returns up to 40 entity nodes with relationships
3. **Place hierarchy assembly** — flattens traversal into prose context for LLM synthesis

**Core Rule:** The subgraph provides qualitative context only. Exact counts/aggregates always come from SQL, never from graph traversal.

SSE event: `querying_subgraph`

---

### 11.7 COMPARATIVE Lane

Runs ANALYTICAL (semantic layer → SQL) and RELATIONAL (subgraph → SPARQL) in **parallel**. Phase 6 fusion reconciles the results.

---

### 11.8 FALLBACK Lane (legacy path)

When no fast lane fires and intent is FALLBACK:
1. `_try_verified_analysis()` — score 83 templates
2. `_phase4_retrieve()` — embedding retrieval
3. `_find_similar_approved_queries()` — memory lookup
4. `_generate_sql(question, schema, townland_norm, analysis, approved_examples)` — LLM free-form SQL

LLM prompt structure:
```
SYSTEM: You are an expert SQLite query generator...
[annotated schema: tables, columns, row counts, categorical examples, flag distributions]
[APP-INTERPRETED QUESTION PLAN: primary_intent, output_mode, scope, ...]
[approved examples: up to 3 similar approved question→SQL pairs]
USER: [question]
Generate a single read-only SQLite SELECT query. Return SQL only, no markdown.
```

Provider chain: OpenRouter (primary) → next free model on 401/403 → Ollama → heuristic fallback (if `ASK_ALLOW_HEURISTIC_FALLBACK=true`) → diagnostic message SQL.

SSE event: `contacting_llm`

---

### 11.9 SQL Safety Guardrail

`_sanitize_and_validate_sql(sql)`:
1. Strip markdown code fences
2. Apply `FORBIDDEN_SQL.search(sql)`: regex `\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|DETACH|PRAGMA|REINDEX|VACUUM|TRUNCATE|REPLACE)\b` — raises `ValueError` on match
3. Verify starts with `SELECT` or `WITH`

SSE event: `framing_query`

---

### 11.10 Database Execution

`_execute_with_recovery(question, townland_hint, sql, approved_examples)`:
1. `conn = get_db_conn()` — WAL mode, Row factory
2. Register `distance_km(lat1, lon1, lat2, lon2)` — haversine formula
3. `conn.execute(safe_sql).fetchall()`
4. On `sqlite3.OperationalError`: one LLM repair attempt; if that fails: return empty result with `{mode: 'repair_failed'}`
5. Serialize non-JSON types; cap at 500 rows with truncation warning

`_haversine_km` formula:
```python
R = 6371.0
φ1, λ1, φ2, λ2 = map(math.radians, [lat1, lon1, lat2, lon2])
a = math.sin((φ2-φ1)/2)**2 + math.cos(φ1)*math.cos(φ2)*math.sin((λ2-λ1)/2)**2
return R * 2 * math.asin(math.sqrt(a))
```

SSE event: `querying_database` with detail "N rows returned"

---

### 11.11 VRTI Knowledge Graph Enrichment

`_kg_context(question, townland_norm, force=True)`:
1. Check `_VRTI_STATUS_CACHE['down_until']` — skip if in cooldown
2. Check `_VRTI_PARISH_CACHE[key]` — return cached if fresh (TTL 3600 s)
3. Call `vrti_sparql.get_townland_details_by_name(name)` — fetches name, Irish name, WKT, centroid, parish, barony, county, OSM/OSI/VRTI IDs, images, links
4. On `requests.Timeout` or `ConnectionError`: set `down_until = now + 300`; return empty

SSE event: `querying_vrti_graph`

---

### 11.12 GraphDB SPARQL Enrichment

`graphdb_sparql.query(sparql)`:
- Endpoint: `GRAPHDB_SPARQL_ENDPOINT` (default: `localhost:7200/repositories/coolattin`)
- Ontology: `co:` namespace (`https://coolattin.ie/ontology#`)
- Timeout: `GRAPHDB_REQUEST_TIMEOUT = 15` s
- On failure: returns `([], [])` — pipeline continues without GraphDB results

SSE event: `querying_graphdb`

---

### 11.13 Phase 6 — Fusion and Discrepancy Detection

`_fuse_lanes(sql_result, graphdb_result, entity_label, kg_uri)`:
- For each shared numeric metric: compute `delta = |sqlite_value - graphdb_value|`
- Label discrepancy by magnitude: minor (< 5%), moderate (5–20%), significant (> 20%)
- Returns `{discrepancy_count, agreement_count, fusion_text, source_provenance}`

SSE event: `querying_fusion`

---

### 11.14 Phase 7 — LLM Synthesis

`_generate_rephrased_answer(question, actual_answer, data_context, kg_context)`:

Prompt:
```
SYSTEM: You are a digital historian specialising in 19th century Irish social history.
Rephrase the following data answer in clear, historically-informed natural language.
Use actual data values. Do not invent figures.

DATA ANSWER: [actual_answer]
DATA TABLE: [first 20 rows in compact format]
VRTI CONTEXT: [townland → parish → barony → county if available]
FUSION NOTES: [cross-source discrepancies if detected]

USER: [original question]
```

If LLM unavailable: return `actual_answer` unmodified.

SSE event: `synthesizing_answer`

---

### 11.15 PDF Report Generation

`_write_pdf_report()` / `_build_simple_pdf()`:
- Hand-written PDF 1.4 binary (no third-party library dependency)
- Page geometry: 792 pt, 48 pt margins, 13 pt line step (~54 lines/page)
- Font: Helvetica Type1 (standard PDF font)
- Content: question + SQL provenance + data table (≤ 160 rows) + VRTI context
- Output: `exports/ask/ask_report_{UTC}.pdf`
- Served via `GET /api/ask/pdf/<filename>` — path-traversal safe: `Path(name).name`

SSE event: `preparing_output` (includes PDF generation)

---

### 11.16 Final SSE Result Event

```json
{
  "type": "result",
  "question": "...",
  "answer": "...",
  "llm_rephrased_answer": "...",
  "columns": ["col1", "col2"],
  "rows": [{...}],
  "row_count": 42,
  "sql": "SELECT ...",
  "chart": {"type": "bar", "labels": [...], "datasets": [...]},
  "vrti_context": {"townlands": [...], "parish_count": N},
  "fusion": {"discrepancy_count": 0, "agreement_count": 1, "fusion_text": "..."},
  "discrepancies": [...],
  "warnings": [...],
  "pdf_url": "/api/ask/pdf/ask_report_20260617_143022.pdf",
  "availability": {"has_local_data": true, "has_vrti_data": true, "suggested_questions": [...]},
  "related_insights": [...],
  "query_provenance": {
    "strategy": "rule_fill | verified_analysis | slot_fill_llm | template | memory | llm_sql",
    "used_approved_memory": false,
    "direct_memory_reuse": false,
    "execution_mode": "executed_as_generated"
  },
  "llm_meta": {
    "provider": "openrouter | ollama | verified_analysis | rule_fill",
    "model": "openai/gpt-oss-20b:free",
    "mode": "analytical_semantic | relational_subgraph | comparative | fallback | verified_analysis"
  }
}
```

---

## 12. Query Feedback and Memory System

### 12.1 Feedback recording (`POST /api/ask/feedback`)

`record_query_feedback(question, townland_hint, sql_text, feedback, note, ...)`:
1. Upsert into `ask_query_feedback`
2. If `feedback = 'up'` and `sql_text` not empty: upsert into `ask_query_memory`
3. Invalidate `_QUERY_MEMORY_CACHE` immediately

### 12.2 Memory retrieval

`_find_similar_approved_queries` queries `ask_query_memory` (TTL 60 s cache). Scores each against the current question using `token_sort_ratio` (rapidfuzz). Top entries feed Fast Lane 3 and the LLM prompt context.

---

## 13. Frontend Architecture

### 13.1 Template structure

All pages extend `base.html`: navigation bar, Chart.js CDN, Leaflet.js CDN, i18n.js, footer.

### 13.2 JavaScript modules

**`ask.js`** — The most complex frontend module:
- Question submission via `fetch('/api/ask/query', {method: 'POST', body: JSON.stringify({question, show_sql})})`
- SSE parsing: `ReadableStream` → split on `data:` → parse JSON
- Progress bar: each `progress` event updates stage indicator (name, status, duration_ms)
- Result rendering: answer panel, SQL panel, data table, Chart.js chart, VRTI context, PDF link, related insights
- Feedback buttons: `POST /api/ask/feedback`
- Townland autocomplete: `GET /api/ask/townland-suggest?q=`

**`i18n.js`** — Bilingual EN/GA string switching via `data-i18n` attributes; stored in `localStorage`.

**`main.js`** — Home page: YouTube IFrame API; section-scroll; Leaflet map with townland GeoJSON.

**`census.js`** — Census data fetch, Chart.js line chart, townland sidebar, detail panel.

**`heritage.js`** — Leaflet map with toggle-able layers: estate boundary, holy wells, ASI monuments.

**`analytics.js`** — Fetches `GET /api/analytics/<dataset_id>`, renders KPI cards and Chart.js charts.

**`kg_explore.js`** — D3.js force-directed graph; comparison scenario runner (SQL vs SPARQL).

### 13.3 Static data files

| File | Purpose |
|---|---|
| `townlands.json` | Estate GeoJSON (152 features, ~2.5 MB) |
| `unified_processed.csv` | Person records (13,707 rows) |
| `holywells_wicklow.geojson` | Holy well point features |
| `asi_wicklow.geojson` | Archaeological survey monument features |

---

## 14. VRTI SPARQL Client — Complete Function Reference

**File:** `backend/integrations/vrti_sparql.py`

All functions send queries to `https://virtuoso.virtualtreasury.ie/sparql/` with `PREFIXES` block prepended. Timeout: 30 s. Format: `application/sparql-results+json`.

| Function | What it returns |
|---|---|
| `get_townlands(county, limit)` | List of TownlandDTO with WKT, centroid, hierarchy, IDs, images, links |
| `get_townland_details_by_name(name)` | Single TownlandDTO by English name label |
| `get_census_records_for_townland(uri)` | All census records for a townland URI |
| `get_census_records_for_county(county, year)` | All townland census records for county/year |
| `get_parish_names(county)` | Distinct civil parish names |
| `get_place_hierarchy(uri)` | Townland → parish → barony → county path |
| `get_sibling_townlands(uri, limit)` | Townlands in the same civil parish |
| `get_external_links(uri)` | OSM, OSI, VRTI, Logainm links for a place |
| `probe_endpoint()` | Health check — returns True if endpoint responds |

WKT centroid parsing: `_parse_point_wkt()` applies Irish lat/lon sanity check (lat 51–55°N, lon −5° to −10°) and swaps coordinates if VRTI uses non-standard order.

---

## 15. Deployment Configuration

### 15.1 Azure App Service

Python 3.12 runtime; gunicorn: `gunicorn -w 4 -b 0.0.0.0:8000 app:app`; Azure-managed HTTPS; `DATABASE_PATH` on persistent storage volume.

### 15.2 Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `OPENROUTER_API_KEY` | Yes | — | LLM provider authentication |
| `SECRET_KEY` | Yes | `dev-secret-change-in-prod` | Flask session signing |
| `DATABASE_PATH` | No | `./coolattin.db` | SQLite file location |
| `FLASK_ENV` | No | `development` | `production` activates ProductionConfig |
| `ASK_LLM_PROVIDER` | No | `auto` | `openrouter` / `ollama` / `auto` |
| `OPENROUTER_MODEL` | No | `openai/gpt-oss-20b:free` | SQL generation model |
| `OPENROUTER_REQUEST_TIMEOUT` | No | `80` | Request timeout seconds |
| `OPENROUTER_MAX_RETRIES` | No | `2` | Retry count on failure |
| `OLLAMA_BASE_URL` | No | `http://127.0.0.1:11434` | Local Ollama instance |
| `OLLAMA_REQUEST_TIMEOUT` | No | `180` | Ollama timeout seconds |
| `VRTI_REQUEST_TIMEOUT` | No | `30` | SPARQL query timeout |
| `ASK_USE_NEW_PIPELINE` | No | `true` | Orchestrated pipeline toggle |
| `EMBEDDING_PROVIDER` | No | `local` | `local` / `cohere` / `voyage` |
| `GRAPHDB_ENABLED` | No | `true` | Enable local GraphDB queries |
| `GRAPHDB_SPARQL_ENDPOINT` | No | `http://localhost:7200/...` | GraphDB endpoint |
| `GRAPHDB_REQUEST_TIMEOUT` | No | `15` | GraphDB query timeout |
| `ASK_ALLOW_HEURISTIC_FALLBACK` | No | `false` | Emergency SQL heuristic |

---

## 16. In-Process Cache Summary

| Cache | TTL | Contents |
|---|---|---|
| `_TOWNLAND_CATALOG_CACHE` | 10 min | All canonical townland names |
| `_VRTI_PARISH_CACHE` | 60 min per townland | VRTI enrichment per townland |
| `_VRTI_STATUS_CACHE['down_until']` | 5 min cooldown | VRTI unavailability flag |
| `_OPENROUTER_STATUS_CACHE` | 60 s | OpenRouter health + model |
| `_OLLAMA_MODEL_CACHE` | 120 s | Available Ollama models |
| `_PROMPT_SCHEMA_CACHE` | 5 min | Full annotated schema descriptor |
| `_QUERY_MEMORY_CACHE` | 60 s | Approved memory rows |
| `_SCHEMA_COMPAT_CACHE` | process lifetime | clearances column name |
| `_UNIFIED_CACHE` | process lifetime | unified_processed.csv DataFrame |
| `_UNIFIED_RECORDS_CACHE` | 5 min | Unified records search results (warm on startup) |
| `_CENTROIDS_CACHE` | process lifetime | Townland centroid dict |
| `_WORKHOUSE_CACHE` | process lifetime | Workhouse Excel DataFrame |
| `_WORKHOUSE_MATCH_INDEX` | 10 min | record_id → workhouse matches (batched ER query) |
| GraphRAG `_GRAPH` | process lifetime | NetworkX property graph (49K nodes, 64K edges) |
| GraphRAG `_NODE_EMBEDDINGS` | process lifetime | BGE-large embedding matrix (28K nodes × 1024 dim) |

---

## 17. Error Handling and Degradation Paths

| Dependency | Failure mode | System response |
|---|---|---|
| VRTI SPARQL endpoint | Timeout or connection error | Enrichment returns empty; 5-min cooldown; warning appended |
| OpenRouter API | Auth failure | Try next free model from list |
| OpenRouter API | All models failed | Fall back to Ollama |
| Ollama | Not running | Heuristic fallback (if enabled) or diagnostic message |
| SQL execution | Syntax error | One LLM repair attempt; if fails: empty result + warning |
| `unified_processed.csv` | File not found | `unified_record` not seeded; Ask queries return empty |
| Heritage GeoJSON | File not found | `heritage_feature` not seeded; heritage queries return nothing |
| Census KG call | KG empty or unavailable | Fall back to `census_seed.py` CSV snapshot |
| GraphDB | Unreachable | Returns `([], [])`; pipeline continues without GraphDB section |
| GraphRAG graph | Not built / load error | Enrichment skipped silently; SQL answer returned without KG context |
| LLM synthesis — Claude | Rate limit or auth error | Falls through to Grok |
| LLM synthesis — Grok | Rate limit or auth error | Falls through to OpenRouter |
| LLM synthesis — OpenRouter | All models failed | Falls through to Ollama |
| LLM synthesis — Ollama | Not running | Returns raw SQL result unmodified |

---

## 17a. GraphRAG Pipeline

**Module:** `backend/services/graphrag.py`  
**Graph build:** `scripts/build_graph.py` (one-time; stored in `graph_nodes` + `graph_edges` SQLite tables)

### Graph structure

The in-process property graph is a NetworkX `DiGraph` built from the `graph_nodes` and `graph_edges` SQLite tables. It is loaded once at first Ask request and kept in memory for the process lifetime (~2 GB RAM for the full 49K-node graph + BGE embeddings).

| Table | Contents |
|---|---|
| `graph_nodes` | id, label, node_type (townland/person/event/ship/place), properties JSON, embedding BLOB |
| `graph_edges` | src_id, dst_id, relation (LOCATED_IN/EMIGRATED_FROM/PART_OF/…), weight |

### Enrichment flow (per Ask query)

```
1. resolve_entity(question, sql_id) → seed_node_ids (top-K by BGE cosine)
2. bfs_subgraph(seed_nodes, k=GRAPHRAG_K_HOPS, max_nodes=GRAPHRAG_MAX_NODES)
   → NetworkX subgraph (G_sub)
3. linearise(G_sub) → compact triple table (subject · relation · object)
4. Inject linearised text into LLM synthesis prompt as "Knowledge graph context:"
```

The enrichment is **additive only**: the synthesis LLM receives the linearised subgraph as supplementary context but may not change numeric aggregates that came from SQL. This was validated across all 9 R-series cases in the demo-freeze evaluation (numeric delta = 0 in every case).

### Configuration

| Variable | Default | Effect |
|---|---|---|
| `GRAPHRAG_ENABLED` | `true` | Enable/disable enrichment entirely |
| `GRAPHRAG_VECTOR_TOP_K` | `8` | Number of BGE seed nodes retrieved per question |
| `GRAPHRAG_K_HOPS` | `2` | BFS traversal depth from seed nodes |
| `GRAPHRAG_MAX_NODES` | `120` | Hard cap on subgraph size (prevents large-graph linearisation) |

### Build command

```bash
python3 scripts/build_graph.py
# Output:
#   graph_nodes: 49,081 rows
#   graph_edges: 64,342 rows
#   BGE-embedded: 28,078 nodes
#   Runtime: ~3–5 min (first run downloads BAAI/bge-large-en-v1.5, ~1.3 GB)
```

The seed database (`coolattin.db`) committed to the repository includes pre-built graph tables so fresh deployments do not need to run this script.

---

## 18. Complete Data Flow — Ask Query End-to-End

```
Browser: POST /api/ask/query
  { question: "How many people emigrated from Aghowle in 1852?", show_sql: true }
         │
         ▼
ask.py → _orchestrated_pipeline_stream()
         │
         ▼ [Pre-flight, ~1 ms, 0 LLM]
_resolve_townland_context("how many people emigrated from Aghowle in 1852?", None)
  → AGHOWLE LOWER found (fuzzy match, score 92)
  → townland_resolution = {name_norm: "AGHOWLE LOWER", sql_id: 42, kg_uri: "https://..."}

_analyse_question(...)
  → primary_intent: "emigration"
  → output_mode: "count"
  → year: 1852
  → scope: "townland"
         │
         ▼ [Fast Lane 1 check]
semantic_layer.try_rule_based_fill(question, analysis)
  → metric "emigration_count" keywords ["emigrat"] match
  → filters: {townland_norm: "AGHOWLE LOWER", year: 1852}
  → confidence = 0.96 (≥ 0.80) → FAST LANE FIRES

SSE: { type:"progress", stage:"schema_sql", status:"completed",
       detail:"Rule-based slot-fill → emigration_count filter year=1852 townland=AGHOWLE LOWER",
       duration_ms: 3 }
         │
         ▼ [compiled SQL, no LLM]
SELECT COUNT(DISTINCT record_id) AS emigration_count
FROM unified_record
WHERE has_emigration_record = 1
  AND townland_norm = 'AGHOWLE LOWER'
  AND year = 1852
         │
         ▼ [Safety guardrail]
_sanitize_and_validate_sql(sql)
  → FORBIDDEN_SQL: no match ✓
  → starts with SELECT ✓

SSE: { stage:"framing_query", status:"completed", duration_ms: 1 }
         │
         ▼ [SQLite execution]
_execute_with_recovery(...)
  → conn.create_function("distance_km", 4, _haversine_km)
  → rows = [{"emigration_count": 47}]  ← 1 row

SSE: { stage:"querying_database", detail:"1 row returned", duration_ms: 12 }
         │
         ▼ [VRTI enrichment]
_kg_context("...", "AGHOWLE LOWER", force=True)
  → Cache miss → SPARQL call
  → vrti_rows: [{name: "Aghowle Lower", civil_parish: "Carnew", barony: "Shillelagh", county: "Wicklow"}]

SSE: { stage:"querying_vrti_graph", detail:"1 townland(s) enriched", duration_ms: 812 }
         │
         ▼ [GraphDB enrichment]
graphdb_sparql.query(...)
  → neighbourhood: 6 nodes (person events in Aghowle Lower 1852)

SSE: { stage:"querying_graphdb", duration_ms: 245 }
         │
         ▼ [Phase 6 fusion]
_fuse_lanes(sql_result={count:47}, graphdb_result={...}, ...)
  → discrepancy_count: 0 (GraphDB has no count, counts from SQL only)

SSE: { stage:"querying_fusion", detail:"0 discrepancies" }
         │
         ▼ [Phase 7 LLM synthesis]
_generate_rephrased_answer(question, "47 emigration records found...", ...)
  → LLM: "In 1852, forty-seven individuals from Aghowle Lower..."

SSE: { stage:"synthesizing_answer", duration_ms: 4218 }
         │
         ▼ [PDF generation]
_write_pdf_report(...) → exports/ask/ask_report_20260617_143022.pdf

SSE: { stage:"preparing_output", detail:"PDF generated", duration_ms: 52 }
         │
         ▼ [Final result]
SSE: {
  type: "result",
  question: "How many people emigrated from Aghowle in 1852?",
  answer: "47 emigration records found in Aghowle Lower for 1852.",
  llm_rephrased_answer: "In 1852, forty-seven individuals from Aghowle Lower...",
  columns: ["emigration_count"], rows: [{"emigration_count": 47}],
  sql: "SELECT COUNT(DISTINCT record_id) AS emigration_count...",
  vrti_context: {townlands: [{civil_parish: "Carnew", barony: "Shillelagh", ...}]},
  pdf_url: "/api/ask/pdf/ask_report_20260617_143022.pdf",
  query_provenance: {strategy: "rule_fill", ...},
  llm_meta: {provider: "openrouter", mode: "analytical_semantic"}
}
         │
         ▼
Browser: ask.js receives SSE stream
  → Renders progress bar stages as they arrive
  → On "result": populates answer panel, SQL panel, data table, VRTI panel, PDF link
  → Shows feedback (thumbs up/down) buttons

Total latency: ~5–7 s (rule-fill SQL: 3 ms; VRTI: ~800 ms; LLM synthesis: ~4 s)
```

---

## 19. Security Considerations

| Attack surface | Mitigation |
|---|---|
| SQL injection via LLM-generated queries | `FORBIDDEN_SQL` regex blocks all write operations; SQLite connection is read-only by convention; parameterised queries in repositories |
| SQL injection via placeholder substitution | `{year}` extracted only from `\b(18[0-9]{2}|19[0-2][0-9])\b` regex (integer only); `{townland_norm}` is normalised UPPER-case name from the catalogue |
| Prompt injection via user question | LLM prompt separates system instructions from user input; `FORBIDDEN_SQL` guardrail operates on output regardless of prompt content |
| PDF path traversal | `Path(filename).name` — strips any directory components |
| Excessive resource consumption | SQL results capped at 500 rows; LLM call timeout 80 s; PDF truncated at 160 rows |
| VRTI endpoint abuse | 5-minute cooldown on unavailability; 1-hour per-townland cache |
| GraphDB endpoint abuse | 15-second timeout; on failure returns empty rather than retrying |
