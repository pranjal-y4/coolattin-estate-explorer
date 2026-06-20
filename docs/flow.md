# Ask Pipeline — End-to-End Data Flow

**Project:** Coolattin Estate Records Explorer
**Status:** Current as of June 2026
**Covers:** `POST /api/ask/query` → SSE stream → browser

---

## 1. Request Entry

```
Browser: POST /api/ask/query
  Content-Type: application/json
  Body: { "question": "...", "townland_hint": "...", "show_sql": true }
         │
         ▼
ask.py route handler (backend/routes/ask.py)
  → stream_with_context(generate())
  → answer_question_stream(question, townland_hint, include_sql)
  → _orchestrated_pipeline_stream()   [if ASK_USE_NEW_PIPELINE=true, default]
```

---

## 2. Startup Seeding (once per process)

Before the first question is answered, three seeding operations run (no-op if already done):

| Function | What it seeds |
|---|---|
| `_ensure_unified_table_seeded()` | `unified_record` table from `unified_processed.csv` (13,707 rows) with derived fields: `townland_norm`, `has_emigration_record`, `has_eviction_record`, `is_widow`, `is_canada_destination`, `children_count`, `holding_acres` |
| `_ensure_heritage_feature_seeded()` | `heritage_feature` table from `holywells_wicklow.geojson` + `asi_wicklow.geojson` |
| `_ensure_query_memory_schema()` | `ask_query_memory` + `ask_query_feedback` tables |

---

## 3. Pre-flight (synchronous, ~1–5 ms, no LLM)

```
_resolve_townland_context(question, townland_hint)
  │  Load townland catalogue (cached 10 min)
  │  Tokenise + remove stopwords
  │  Exact → fuzzy (token_set_ratio ≥ 80) → hint override
  └→ {name_norm, sql_id, kg_uri, warning}

_analyse_question(question, townland_hint)
  │  year regex: \b(18[0-9]{2}|19[0-2][0-9])\b
  │  surname: 6 regex patterns
  │  radius: \b(\d{1,3})\s*km\b
  └→ {primary_intent, output_mode, group_by, scope, preferred_tables, ...}

_question_data_coverage_warnings(question)
  └→ [] or ["Census data begins at 1841", ...]
```

---

## 4. Four Fast Lanes (first match short-circuits the rest)

```
┌──────────────────────────────────────────────────────────────────────┐
│ FAST LANE 1 — Rule-based slot-fill                                   │
│   semantic_layer.try_rule_based_fill(question, analysis)             │
│   • Match question against 22 metric keyword sets                    │
│   • Confidence ≥ 0.80?  → compile SQL → SSE: schema_sql → EXECUTE   │
│   • 0 LLM calls, < 5 ms                                             │
├──────────────────────────────────────────────────────────────────────┤
│ FAST LANE 2 — Verified template                                      │
│   _try_verified_analysis(question, townland_norm, analysis)          │
│   • Score 83 templates by required_keywords + optional_keywords      │
│   • Template in VERIFIED_ANALYSIS_TEMPLATE_IDS + match? → SQL       │
│   • Confidence = 1.0                                                 │
├──────────────────────────────────────────────────────────────────────┤
│ FAST LANE 3 — Direct memory reuse                                    │
│   _find_similar_approved_queries(question, analysis, townland_norm)  │
│   • Query ask_query_memory (TTL 60 s cache)                          │
│   • token_sort_ratio + cosine ≥ 0.55 → reuse approved SQL           │
├──────────────────────────────────────────────────────────────────────┤
│ FAST LANE 4 — Embedding template retrieval                           │
│   embedding_index._phase4_retrieve(question, ...)                    │
│   • TF-IDF unigram+bigram cosine, merged by RRF                      │
│   • Top hit cosine ≥ 0.68 AND required_keywords match?              │
│   → use template SQL directly                                        │
└──────────────────────────────────────────────────────────────────────┘
```

SSE emitted on fast-lane hit: `{type:"progress", stage:"schema_sql"|"contacting_llm", status:"completed", detail:"...", duration_ms:N}`

---

## 5. Intent Classification (Phase 5)

```
classify_intent(question, analysis, slot_fill)   ← intent_router.py

Priority order (first match wins):
  COMPARATIVE  — any comparative keyword present
  RELATIONAL   — any relational/hierarchy/heritage/sensemaking keyword
                 EXCEPT: Core Rule 1 overrides to ANALYTICAL
                         if only heritage/sensemaking AND count/aggregate mode
  ANALYTICAL   — primary_intent in {population,eviction,emigration,tenancy}
                 OR output_mode in {count,aggregate,trend}
                 OR slot_fill is not None
  FALLBACK     — default
```

SSE: `{type:"progress", stage:"classifying_intent", status:"completed"}`

---

## 6. Route Dispatch

### ANALYTICAL

```
semantic_layer.build_slot_fill_prompt(question, analysis)
  → LLM slot-fill (if rule-fill confidence < 0.80)
  → parse_slot_fill() → SlotFill{metric, dimensions, filters, group_mode, confidence}
  → if confidence ≥ 0.70: compile_sql(slot_fill) → deterministic SQL
  → if confidence < 0.60: fall through to FALLBACK
SSE: slot_filling → schema_sql
```

### RELATIONAL / HERITAGE

```
subgraph_engine.retrieve_subgraph(question, entity_uri, k=2)
  → VRTI SPARQL: townland → parish → barony → county hierarchy
  → get_sibling_townlands(), get_external_links()
  → graphdb_sparql.get_entity_neighborhood(name, k=2, max_nodes=40)
  → Returns: qualitative context + place graph
  (counts still come from SQL, never from KG)
SSE: querying_subgraph
```

### COMPARATIVE

```
[ANALYTICAL lane] || [RELATIONAL lane]  ← parallel
Phase 6 fusion reconciles both results
```

### FALLBACK

```
_try_verified_analysis()          ← score 83 templates
_phase4_retrieve()                ← embedding retrieval
_find_similar_approved_queries()  ← memory lookup
_generate_sql()                   ← LLM free-form SQL with annotated schema
SSE: contacting_llm
```

---

## 7. SQL Safety Guardrail

```
_sanitize_and_validate_sql(sql)
  1. Strip markdown code fences
  2. FORBIDDEN_SQL.search(sql)
     regex: \b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|DETACH|
             PRAGMA|REINDEX|VACUUM|TRUNCATE|REPLACE)\b
     → raises ValueError on match
  3. Must start with SELECT or WITH
  4. Return sanitised SQL

SSE: {stage: "framing_query", status: "completed"}
```

---

## 8. SQLite Execution

```
_execute_with_recovery(question, townland_hint, sql, approved_examples)
  1. conn = get_db_conn()  ← WAL mode, foreign_keys=ON, Row factory
  2. conn.create_function("distance_km", 4, _haversine_km)
  3. conn.execute(sql).fetchall()
  4. On sqlite3.OperationalError:
     → LLM repair: send question + bad SQL + error → repaired SQL
     → re-execute; if still fails: return empty result
  5. Cap at 500 rows; serialize non-JSON types (dates → strings)

SSE: {stage: "querying_database", detail: "N rows returned", duration_ms: N}
```

---

## 9. VRTI Enrichment

```
_kg_context(question, townland_norm, force=True)
  Check _VRTI_STATUS_CACHE['down_until'] → skip if in cooldown
  Check _VRTI_PARISH_CACHE[townland_key] → return cached if fresh (TTL 3600 s)
  vrti_sparql.get_townland_details_by_name(name)
    → SPARQL: name, Irish name, WKT, centroid, parish, barony, county, OSM/OSI IDs
    → Timeout: VRTI_REQUEST_TIMEOUT = 30 s
  On timeout/ConnectionError: down_until = now + 300 s; return empty
  Store in _VRTI_PARISH_CACHE with expiry now + 3600

SSE: {stage: "querying_vrti_graph", detail: "N townland(s) enriched"}
```

---

## 10. GraphDB Enrichment

```
graphdb_sparql.query(sparql)
  Endpoint: GRAPHDB_SPARQL_ENDPOINT (default: localhost:7200/repositories/coolattin)
  Timeout: GRAPHDB_REQUEST_TIMEOUT = 15 s
  On failure: return ([], []) — pipeline continues

SSE: {stage: "querying_graphdb"}
```

---

## 11. Phase 6 — Fusion

```
_fuse_lanes(sql_result, graphdb_result, entity_label, kg_uri)
  For each numeric metric:
    delta = |sqlite_value - graphdb_value|
    label: minor (<5%) | moderate (5-20%) | significant (>20%)
  Returns: {discrepancy_count, agreement_count, fusion_text, source_provenance}

SSE: {stage: "querying_fusion", detail: "N discrepancies detected"}
```

---

## 12. Phase 7 — LLM Synthesis

```
_generate_rephrased_answer(question, actual_answer, data_context, kg_context)
  SYSTEM: "You are a digital historian specialising in 19th century Irish social history..."
  DATA: first 20 rows in compact format
  USER: original question
  → OpenRouter (primary) → Ollama (fallback) → raw answer (if neither available)

SSE: {stage: "synthesizing_answer"}
```

---

## 13. Output Assembly

```
_build_availability_payload()  → has_local_data, has_vrti_data, suggested_questions
_build_related_insights()      → 1–3 follow-up question prompts
_build_chart_spec()            → Chart.js {type, labels, datasets, options}
_write_pdf_report()            → exports/ask/ask_report_{UTC}.pdf

SSE: {stage: "preparing_output", detail: "PDF generated"}
```

---

## 14. Final SSE Result Event

```json
{
  "type": "result",
  "question": "...",
  "answer": "...",
  "llm_rephrased_answer": "...",
  "columns": [...],
  "rows": [...],
  "row_count": N,
  "sql": "SELECT ...",
  "chart": {"type": "bar", "labels": [...], "datasets": [...]},
  "vrti_context": {"townlands": [...], "parish_count": N},
  "fusion": {"discrepancy_count": 0, "agreement_count": 1, ...},
  "discrepancies": [...],
  "warnings": [...],
  "pdf_url": "/api/ask/pdf/ask_report_20260617_143022.pdf",
  "availability": {"has_local_data": true, "has_vrti_data": true, ...},
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
    "mode": "analytical_semantic | relational_subgraph | comparative | fallback"
  }
}
```

---

## 15. Worked Example: "How many emigrants left from Aghowle in 1852?"

```
Pre-flight:
  townland resolved: AGHOWLE LOWER → sql_id=42, kg_uri="https://..."
  primary_intent: emigration, output_mode: count, year: 1852, scope: townland

Fast Lane 1:
  metric "emigration_count" keywords ["emigrat"] match
  filters: {townland_norm: "AGHOWLE LOWER", year: 1852}
  confidence = 0.96 (≥ 0.80) → compile SQL directly

SQL compiled:
  SELECT COUNT(DISTINCT record_id) AS emigration_count
  FROM unified_record
  WHERE has_emigration_record = 1
    AND townland_norm = 'AGHOWLE LOWER'
    AND year = 1852

SSE: schema_sql → framing_query → querying_database → querying_vrti_graph → synthesizing_answer

Result:
  answer: "47 emigration records from Aghowle Lower in 1852"
  llm_rephrased_answer: "In 1852, forty-seven individuals from Aghowle Lower..."
  sql: [shown if show_sql=true]
  query_provenance.strategy: "rule_fill"
  Total latency: ~3–8 s (LLM synthesis only; no LLM SQL generation)
```

---

## 16. Performance Characteristics

| Route | LLM calls | Typical latency |
|---|---|---|
| Fast Lane 1 (rule-fill) | 0 (synthesis only) | 3–8 s |
| Fast Lane 2 (verified template) | 0 (synthesis only) | 3–8 s |
| Fast Lane 3 (memory) | 0 (synthesis only) | 3–8 s |
| Fast Lane 4 (embedding) | 0 (synthesis only) | 3–8 s |
| ANALYTICAL (LLM slot-fill) | 1 (slot-fill) + 1 (synthesis) | 8–20 s |
| RELATIONAL (subgraph) | 0 (synthesis only) | 5–15 s (SPARQL) |
| FALLBACK (LLM SQL) | 1 (SQL) + 1 (synthesis) | 10–30 s |

---

## 17. In-Process Caches

| Cache | TTL | Contents |
|---|---|---|
| `_TOWNLAND_CATALOG_CACHE` | 10 min | All canonical townland names |
| `_VRTI_PARISH_CACHE` | 60 min per townland | VRTI enrichment per townland |
| `_VRTI_STATUS_CACHE` | 5 min cooldown | VRTI unavailability flag |
| `_OPENROUTER_STATUS_CACHE` | 60 s | OpenRouter health |
| `_OLLAMA_MODEL_CACHE` | 120 s | Available Ollama models |
| `_PROMPT_SCHEMA_CACHE` | 5 min | Annotated schema descriptor |
| `_QUERY_MEMORY_CACHE` | 60 s | Approved memory rows |
| `_SCHEMA_COMPAT_CACHE` | process lifetime | clearances column name |
| `_UNIFIED_CACHE` | process lifetime | unified_processed.csv DataFrame |
