# The Orchestrated Ask Pipeline — Architecture Reference

**Project:** Coolattin Estate Records Explorer
**Status:** Current as of June 2026
**Entry point:** `_orchestrated_pipeline_stream()` in `backend/services/ask_service.py`
**Enabled by default:** `ASK_USE_NEW_PIPELINE=true`

---

## Overview

The Ask page (`/ask`) implements a multi-lane orchestrated pipeline that routes each natural-language question to the most appropriate retrieval strategy before ever calling an LLM. The pipeline combines deterministic slot-fill compilation, hybrid embedding retrieval, SPARQL knowledge-graph traversal, and LLM synthesis into one SSE-streamed response.

The key architectural decision is **intent-first routing**: a question about emigration counts takes a different path than a question about parish geography, and both take a different path than a comparative question spanning multiple sources. The pipeline never defaults to free-form LLM SQL generation unless every more-reliable path has been exhausted.

---

## System Diagram

```
User question (natural language)
        │
        ▼ [Phase 1] Entity resolution
┌──────────────────────────────────────────────────────┐
│  identity_resolver.py + _resolve_townland_context()  │
│  • Townland fuzzy match → sql_id + kg_uri            │
│  • Surname disambiguation (Mention/Person/Factoid)   │
└───────────────────────┬──────────────────────────────┘
                        │
        ▼ [Pre-flight] _analyse_question()
        • year / surname / radius extraction (regex)
        • primary_intent, output_mode, group_by, scope
        • No LLM call; ~1 ms
                        │
        ▼ FAST LANE CHECK (4 lanes, first match wins)
┌──────────────────────────────────────────────────────────────────────────┐
│  Fast Lane 1 — Rule-based slot-fill (semantic_layer.try_rule_based_fill) │
│    confidence ≥ 0.80 → compile SQL directly, skip intent routing         │
│                                                                          │
│  Fast Lane 2 — Verified template                                         │
│    exact match in VERIFIED_ANALYSIS_TEMPLATE_IDS → SQL, confidence = 1.0│
│                                                                          │
│  Fast Lane 3 — Direct memory reuse                                       │
│    token_sort_ratio + cosine ≥ 0.55 → reuse approved SQL from memory    │
│                                                                          │
│  Fast Lane 4 — Embedding template (embedding_index.py)                  │
│    TF-IDF + RRF cosine ≥ 0.68 AND required_keywords match               │
│    → use template SQL directly                                           │
└──────────────────────────────────────────────────────────────────────────┘
                        │  no fast lane fired
        ▼ [Phase 5] classify_intent()  — intent_router.py
        Priority: COMPARATIVE > RELATIONAL > ANALYTICAL > FALLBACK
                        │
        ┌───────────────┼──────────────────────────────┐
        ▼               ▼               ▼               ▼
  ANALYTICAL       RELATIONAL      COMPARATIVE       FALLBACK
  [Phase 2]        [Phase 3]       (both in          legacy path
  semantic_layer   subgraph_engine  parallel)
        │               │
        ▼               ▼
  Deterministic    VRTI SPARQL
  SQL via          + GraphDB
  slot-fill        traversal
  compiler
        │               │
        └───────┬────────┘
                │
        ▼ SQL execution + safety guardrail
        • FORBIDDEN_SQL regex (INSERT/UPDATE/DELETE/DROP/…)
        • conn.execute() with distance_km() registered
        • ≤ 500 rows returned
                │
        ▼ VRTI SPARQL enrichment (1-hour TTL cache per townland)
                │
        ▼ GraphDB SPARQL enrichment (co: ontology, parallel)
                │
        ▼ [Phase 6] Fusion & discrepancy detection
                │
        ▼ [Phase 7] LLM synthesis (OpenRouter → Ollama fallback)
                │
        ▼ SSE result event (table + chart + PDF link + provenance)
```

---

## Phase 1 — Entity Resolution

**Files:** `backend/services/identity_resolver.py`, `_resolve_townland_context()` in `ask_service.py`

### Townland resolution

`_resolve_townland_context(question, townland_hint)`:
1. Load townland catalogue from DB — cached 10 min (`_TOWNLAND_CATALOG_CACHE`)
2. Tokenise question; remove stopwords from `_TOWNLAND_STOPWORDS` (175 terms)
3. Try exact match: normalise each candidate token, check against catalogue
4. Try fuzzy match via `rapidfuzz.fuzz.token_set_ratio`; threshold: 80
5. If `townland_hint` provided by the frontend: use as authoritative, skip scan
6. Returns: `{name, name_norm, warning, method: 'exact'|'fuzzy'|'hint'|None}`

### Person identity (three-layer model)

`identity_resolver.py` models identity as three layers:
- **Mention** — a raw name occurrence in a source record
- **Person** — a disambiguated identity node
- **Factoid** — an individual biographical fact attributed to a Person

Disambiguation scoring: Jaro-Winkler name similarity + Metaphone phonetic blocking + geographic/temporal co-occurrence weighting. Surfaces "3 distinct individuals called John Murphy found" rather than silently picking one.

---

## Pre-flight Analysis

**Function:** `_analyse_question(question, townland_hint)` — pure text, no LLM, no DB

Extracts:
- `year` — regex `\b(18[0-9]{2}|19[0-2][0-9])\b`
- `surname` — 6 regex patterns
- `radius_km` — regex `\b(\d{1,3})\s*km\b`

Classifies (returned as dict with 13 keys):
- `primary_intent` — `population | eviction | emigration | tenancy | people | geography | overview`
- `output_mode` — `count | aggregate | trend | grouped | list | detail`
- `group_by` — `year | parish | townland | surname | ship_name | None`
- `scope` — `radius | townland | global`

---

## Four Fast Lanes

These run before intent classification. The first lane that fires short-circuits the rest of the pipeline entirely.

### Fast Lane 1 — Rule-based slot-fill

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
- If confidence ≥ 0.80: compile SQL immediately, bypass intent routing
- **0 LLM calls**; latency < 5 ms

### Fast Lane 2 — Verified template

Pre-validated SQL templates with `verified_at` timestamps. If the question exactly matches a template in `VERIFIED_ANALYSIS_TEMPLATE_IDS`, return that SQL with confidence = 1.0.

### Fast Lane 3 — Direct memory reuse

`_find_similar_approved_queries()` scans `ask_query_memory` (approved thumbs-up pairs). If `token_sort_ratio + cosine ≥ 0.55`: reuse the approved SQL directly. Memory cache TTL: 60 seconds.

### Fast Lane 4 — Embedding template (Phase 4)

`embedding_index.py`: TF-IDF unigram+bigram cosine + keyword overlap, merged by RRF (Reciprocal Rank Fusion) over templates and approved memory.
- Threshold: cosine ≥ **0.68** AND all `required_keywords` present
- Short-circuits: emits template SQL directly, no LLM

---

## Phase 5 — Intent Classification

**File:** `backend/services/intent_router.py`, function `classify_intent(question, analysis, slot_fill)`

Priority order (first match wins):

### 1. COMPARATIVE

Any of these keywords present:
> `compare`, `compared to`, `versus`, `vs`, `difference between`, `contrast`, `relative to`, `how does`, `how did`, `better than`, `worse than`, `more than`, `less than`, `higher than`, `lower than`, `against`

### 2. RELATIONAL

Any keyword from:
- *Relational*: `related to`, `connected to`, `link between`, `in the same parish`, `same barony`, `part of`, `neighbouring`, `adjacent to`, `bordering`, `relationship between`, `linked to`
- *Hierarchy*: `which parish`, `what parish`, `civil parish`, `in the barony`, `townlands in`, `where is`, `where does`, `located in`, `situated in`, `falls within`
- *Heritage*: `heritage`, `archaeological`, `monument`, `ring fort`, `holy well`, `history of`, `tell me about`, `describe`, `historically`, `fortification`, `earthwork`
- *Sensemaking*: `overview`, `about the estate`, `about coolattin`, `describe the estate`, `what kind of`, `background`, `summary of`, `general context`

**Core Rule 1 override:** If only heritage/sensemaking keywords triggered (no relational/hierarchy/geography signal) AND `output_mode` is `count`/`aggregate` AND any analytical keyword is present → falls through to ANALYTICAL instead.

### 3. ANALYTICAL

Any of:
- `primary_intent` in `{population, eviction, emigration, tenancy}`
- `output_mode` in `{count, aggregate, trend}`
- Any keyword: `how many`, `how much`, `total`, `count of`, `number of`, `average`, `mean`, `proportion`, `percent`, `percentage`, `per year`, `by year`, `trend`, `over time`, `distribution`, `breakdown`, `most`, `least`, `highest`, `lowest`, `maximum`, `minimum`, `sum of`, `rate`, `ratio`
- `slot_fill is not None`

### 4. FALLBACK

Default when nothing above matched.

---

## ANALYTICAL Lane — Semantic Layer

**File:** `backend/services/semantic_layer.py`

### Slot-fill model

```python
@dataclass
class SlotFill:
    metric: str                  # key into METRIC_REGISTRY (22 metrics)
    dimensions: list[str]        # GROUP BY columns
    filters: dict[str, Any]      # WHERE conditions
    group_mode: str              # "aggregate"|"trend"|"grouped"|"detail"
    limit: int | None
    order_by_override: str | None
    confidence: float = 1.0      # 1.0 = rule-based; 0.0–1.0 = LLM-filled
    source: str = "rule"         # "rule" | "llm"
```

### Metric registry (22 metrics)

Each metric entry defines:
- `aggregate` — SQL expression (`COUNT(DISTINCT record_id)`, `SUM()`, `AVG()`)
- `from_clause` — SQL FROM with optional JOINs
- `base_where` — metric-inherent WHERE clause
- `dim_select` / `dim_group_by` — dimension-specific SELECT/GROUP BY
- `filter_where` — filter templates with `{val}` placeholder
- `sparql_agg` — SPARQL equivalent (or None)
- `keywords` — trigger substrings for rule detection

Metrics include: `emigration_count`, `eviction_event_count`, `population`, `tenancy_count`, `widow_count`, `avg_holding_acres`, `population_change`, `uninhabited_houses`, `person_count`, `townland_count`, `parish_count`, `canada_emigration_count`, `evicted_person_count`, and others.

### Three compilation paths

**Path A — Rule-based fill (0 LLM calls):**
- Keyword matching against metric `keywords` entries
- Applies filters/dimensions from `analysis` dict
- Confidence ≥ 0.80 → compile SQL directly

**Path B — LLM slot-fill:**
- Builds `SlotFillPrompt` with annotated metric registry + question
- LLM returns JSON: `{metric, dimensions, filters, group_mode, confidence}`
- `parse_slot_fill()` validates; if confidence ≥ 0.70 → compile SQL
- If confidence < 0.60: reject, fall through to FALLBACK

**Path C — Deterministic SQL compiler:**
`compile_sql(slot_fill)` assembles SQL from registry parts — never free-form LLM SQL:

```sql
SELECT {dim_selects}, {aggregate} AS {alias}
FROM {from_clause}
WHERE {base_where} {filter_wheres}
GROUP BY {dim_group_bys}
ORDER BY {order_by}
LIMIT {limit}
```

### SPARQL compilation (RQ6)

`compile_sparql(slot_fill)` generates equivalent SPARQL from `sparql_agg` template. Used for the SQL-vs-SPARQL comparison (D8) and GraphDB validation.

---

## RELATIONAL Lane — Subgraph Engine

**File:** `backend/services/subgraph_engine.py`, function `retrieve_subgraph(question, entity_uri, k=2)`

Retrieval flow:
1. **VRTI multi-hop SPARQL** — traverses townland → parish → barony → county; sibling townlands; external links (OSM, OSI, Logainm)
2. **GraphDB neighborhood expansion** — `get_entity_neighborhood(name, k=2, max_nodes=40)` from local `co:` ontology; returns up to 40 entity nodes with relationships
3. **Community summaries** — each node cluster carries a pre-computed factual summary

**Core Rule 1:** The subgraph provides qualitative context only. Exact counts always come from SQL, never from KG traversal.

---

## COMPARATIVE Lane

Runs ANALYTICAL (semantic layer → SQL) and RELATIONAL (subgraph engine → SPARQL) **in parallel**, then fuses the results in Phase 6.

---

## FALLBACK Lane

When no fast lane fires and intent is FALLBACK:
1. `_try_verified_analysis()` — scores 83 question templates by keyword
2. `_phase4_retrieve()` — embedding retrieval (TF-IDF + RRF)
3. `_find_similar_approved_queries()` — approved memory lookup
4. `_generate_sql()` — LLM free-form SQL with annotated schema + approved examples
5. `_sanitize_and_validate_sql()` — FORBIDDEN_SQL guardrail
6. `_execute_with_recovery()` — execute; on `OperationalError`, one LLM repair attempt

---

## SQL Execution

**Function:** `_execute_with_recovery()` in `ask_service.py`

1. Register `distance_km(lat1, lon1, lat2, lon2)` — haversine formula custom SQLite function
2. Apply `FORBIDDEN_SQL` guardrail: blocks `INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|DETACH|PRAGMA|REINDEX|VACUUM|TRUNCATE|REPLACE`
3. Must start with `SELECT` or `WITH`
4. On `sqlite3.OperationalError`: one LLM repair attempt, then return empty result
5. Cap result: > 500 rows → truncate with warning

---

## VRTI SPARQL Enrichment

**File:** `backend/integrations/vrti_sparql.py`

Functions called per Ask query:
- `get_townland_details_by_name(name)` — spatial + hierarchy data for a named townland
- TTL cache: 1 hour per townland (`_VRTI_PARISH_CACHE`)
- Cooldown: 5 minutes on connection failure (`_VRTI_STATUS_CACHE`)
- Endpoint: `https://virtuoso.virtualtreasury.ie/sparql/`

---

## GraphDB SPARQL Integration

**File:** `backend/integrations/graphdb_sparql.py`

- Ontology: `co:` namespace (`https://coolattin.ie/ontology#`)
- Repository: `http://localhost:7200/repositories/coolattin` (or configured via `GRAPHDB_SPARQL_ENDPOINT`)
- `query(sparql)` — generic SPARQL SELECT, returns list of dicts
- `get_entity_neighborhood(name, k=2, max_nodes=40)` — k-hop subgraph expansion
- Enabled by: `GRAPHDB_ENABLED=true` (default)
- Timeout: `GRAPHDB_REQUEST_TIMEOUT=15` seconds
- On failure: returns `([], [])`, pipeline continues without GraphDB section

**Purpose:** Enables the SQL-vs-SPARQL comparison (Dissertation objective D8 / RQ6). `semantic_layer.compile_sparql()` generates the SPARQL equivalent for any slot-fill that has a `sparql_agg` entry.

---

## Phase 6 — Fusion and Reconciliation

`_fuse_lanes(sql_result, graphdb_result, entity_label, kg_uri)`:
- Compares numeric results across SQLite and GraphDB
- Calculates `delta = |sqlite_value - graphdb_value|`
- Labels discrepancies by magnitude: minor (< 5%), moderate (5–20%), significant (> 20%)
- Returns `{discrepancy_count, agreement_count, fusion_text, source_provenance}`

---

## Phase 7 — LLM Synthesis

**Function:** `_generate_rephrased_answer()` + `_llm_generate()` in `ask_service.py`

Prompt structure:
```
SYSTEM: You are a digital historian specialising in 19th century Irish social history.
Rephrase the following data answer in clear, historically-informed natural language.
Use actual data values. Do not invent figures.

DATA ANSWER: [raw tabular answer]
DATA TABLE: [first 20 rows]
VRTI CONTEXT: [townland → parish → barony → county if available]
FUSION NOTES: [cross-source discrepancies if detected]

USER: [original question]
```

Provider chain: OpenRouter → Ollama → `ASK_ALLOW_HEURISTIC_FALLBACK` heuristic
If no provider available: return raw `actual_answer` unmodified.

---

## SSE Streaming Protocol

All pipeline stages emit `progress` events. The frontend `ask.js` renders a progress bar from these.

| `stage` value | Meaning |
|---|---|
| `resolving_identity` | Phase 1: entity resolution |
| `classifying_intent` | Phase 5: intent routing |
| `slot_filling` | Phase 2: semantic layer LLM slot-fill |
| `schema_sql` | Phase 2: compiling deterministic SQL |
| `querying_subgraph` | Phase 3: VRTI/GraphDB traversal |
| `embedding_retrieval` | Phase 4: TF-IDF + RRF search |
| `contacting_llm` | FALLBACK: LLM SQL generation |
| `framing_query` | Safety guardrail validation |
| `querying_database` | SQLite execution |
| `querying_vrti_graph` | VRTI SPARQL enrichment |
| `querying_graphdb` | GraphDB SPARQL enrichment |
| `querying_fusion` | Phase 6: discrepancy detection |
| `synthesizing_answer` | Phase 7: LLM rewrite |
| `preparing_output` | Final assembly |

**Final event** (`type: "result"`) carries:
- `answer` / `llm_rephrased_answer` — raw and prose answers
- `columns` / `rows` — result table
- `sql` — executed SQL (if `show_sql=true`)
- `chart` — Chart.js-compatible spec
- `vrti_context` — enrichment from VRTI
- `fusion` / `discrepancies` — cross-source comparison
- `pdf_url` — PDF export link
- `query_provenance` — route taken (`verified_analysis | rule_fill | slot_fill_llm | template | memory | llm_sql`)
- `llm_meta` — provider, model, mode

---

## PDF Export

`_write_pdf_report()` / `_build_simple_pdf()`:
- Hand-written PDF 1.4 binary — no reportlab/fpdf dependency
- Page geometry: 792 pt, 48 pt margins, 13 pt line step (~54 lines/page)
- Font: Helvetica Type1 (standard PDF font)
- Content: question + SQL + data table (≤ 160 rows) + VRTI context + provenance
- Output: `exports/ask/ask_report_{UTC}.pdf`
- Served via `GET /api/ask/pdf/<filename>` (path-traversal safe: `Path(name).name`)

---

## Query Memory and Feedback

### Feedback recording (`POST /api/ask/feedback`)

- All feedback (up/down) written to `ask_query_feedback`
- Thumbs-up only: written to `ask_query_memory` (the retrieval pool)
- Invalidates `_QUERY_MEMORY_CACHE` immediately

### Memory retrieval

- Cache TTL: 60 seconds
- Similarity: `token_sort_ratio` (rapidfuzz) for approximate matching
- Memory entries are candidates for Fast Lanes 3 and 4
- `query_provenance.approved_query_candidates` in result event shows top 3 matches

---

## Relationship to GraphRAG Concept

| Component | Retrieval type | Data source |
|---|---|---|
| Semantic layer (ANALYTICAL) | Deterministic SQL compilation | SQLite unified_record, census_record, clearances_record |
| Verified templates + memory (Fast Lanes 2–3) | Template retrieval (classic RAG) | Pre-verified SQL library + approved memory |
| Embedding index (Fast Lane 4) | Hybrid sparse+dense retrieval | TF-IDF + optional Cohere/BGE dense embeddings |
| Subgraph engine (RELATIONAL) | Graph traversal (GraphRAG) | VRTI SPARQL + GraphDB co: ontology |
| COMPARATIVE | Both SQL + graph in parallel | All sources, fused |
| FALLBACK | LLM-generated SQL (classic RAG) | SQLite with annotated schema |

The system never uses the KG for counts or aggregates — these always come from SQL. The KG (VRTI + GraphDB) provides relational context: place hierarchies, external identifiers, entity neighbourhoods, and qualitative heritage information.
