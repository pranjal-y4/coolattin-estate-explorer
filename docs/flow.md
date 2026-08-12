# Ask Pipeline — End-to-End Data Flow

**Project:** Coolattin Estate Records Explorer  
**Entry point:** `POST /api/ask/query` → `ask_service.py::_orchestrated_pipeline_stream()`  
**Status:** Current as of July 2026 (`v1.0-demo-freeze` validated 2026-06-10)

---

## Overview

Every question on the Ask page passes through this sequence. Results stream back to the browser as Server-Sent Events (SSE) — the user sees each stage progressively.

```
POST /api/ask/query
  │
  ▼
[PRE-FLIGHT]        < 5 ms, no LLM, no DB write
  townland resolution
  question analysis
  data coverage warnings
  │
  ▼
[FOUR FAST LANES]   first match short-circuits everything below
  Lane 1: Rule-based slot-fill (0 LLM, < 5 ms)
  Lane 2: Verified template (81 pre-written templates, 15 in VERIFIED_ANALYSIS_TEMPLATE_IDS)
  Lane 3: Direct memory reuse (thumbs-up approved SQL)
  Lane 4: Embedding template retrieval (TF-IDF + RRF ≥ 0.68)
  │ (if a lane fires → jump to SQL EXECUTION)
  │ (if all lanes miss → continue below)
  ▼
[PHASE 1]  Identity resolution        — identity_resolver.py
[PHASE 2]  Semantic layer             — semantic_layer.py
[PHASE 3]  Subgraph engine            — subgraph_engine.py
[PHASE 4]  Hybrid embedding retrieval — embedding_index.py
[PHASE 5]  Intent classification      — intent_router.py
[PHASE 6]  Fusion & reconciliation    — ask_service.py
[PHASE 7]  Multi-model synthesis      — ask_service.py
  │
  ▼
SSE result event → browser renders answer, table, chart, PDF link
```

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
  → _orchestrated_pipeline_stream()   [ASK_USE_NEW_PIPELINE=true, default]
```

---

## 2. Startup Seeding (once per process, no-op if already done)

Before the first question is answered, three seeding operations run:

| Function | What it seeds |
|---|---|
| `_ensure_unified_table_seeded()` | `unified_record` from `unified_processed.csv` (13,707 rows) with derived fields: `townland_norm`, `has_emigration_record`, `has_eviction_record`, `is_widow`, `is_canada_destination`, `children_count`, `holding_acres` |
| `_ensure_heritage_feature_seeded()` | `heritage_feature` from `holywells_wicklow.geojson` + `asi_wicklow.geojson` |
| `_ensure_query_memory_schema()` | `ask_query_memory` + `ask_query_feedback` tables |

---

## 3. Pre-flight (synchronous, < 5 ms, no LLM)

```
_resolve_townland_context(question, townland_hint)
  │  Load townland catalogue (cached 10 min)
  │  Tokenise + remove stopwords
  │  Exact match → fuzzy (token_set_ratio ≥ 80) → hint override
  └→ {name, sql_id, kg_uri, warning}

_analyse_question(question, townland_hint)
  │  year regex: \b(18[0-9]{2}|19[0-2][0-9])\b
  │  surname: 6 regex patterns
  │  radius: \b(\d{1,3})\s*km\b
  │  keyword matching against 14 metric keyword sets (METRIC_REGISTRY)
  └→ {primary_intent, output_mode, group_by, scope, preferred_tables,
      year, year_from, year_to, surname, radius_km, ...}

_question_data_coverage_warnings(question)
  └→ [] or ["Census data begins at 1841", ...]
```

---

## 4. Four Fast Lanes (first match short-circuits the rest)

All fast lanes produce SQL that then goes directly to the SQL Safety Guardrail (§7) and execution (§8). If a lane fires, phases 1–7 are skipped entirely.

```
┌──────────────────────────────────────────────────────────────────────┐
│ FAST LANE 1 — Rule-based slot-fill                                   │
│   semantic_layer.try_rule_based_fill(question, analysis)             │
│   • Match question against 14 metric keyword sets in METRIC_REGISTRY │
│   • Compute confidence from keyword hit density                      │
│   • Confidence ≥ 0.80? → compile deterministic SQL → EXECUTE        │
│   • 0 LLM calls, < 5 ms                                             │
│   • Covers ~70% of all analytical questions                          │
├──────────────────────────────────────────────────────────────────────┤
│ FAST LANE 2 — Verified template                                      │
│   _try_verified_analysis(question, townland_norm, analysis)          │
│   • Score 81 pre-written SQL templates by:                           │
│       required_keywords (must all be present)                        │
│       optional_keywords (bonus scoring)                              │
│   • Template in VERIFIED_ANALYSIS_TEMPLATE_IDS + score above threshold?│
│       → Pre-written SQL used directly                                │
│   • Confidence = 1.0 (highest possible)                              │
│   • 7 templates emit a Chart.js chart spec                           │
├──────────────────────────────────────────────────────────────────────┤
│ FAST LANE 3 — Direct memory reuse                                    │
│   _find_similar_approved_queries(question, analysis, townland_norm)  │
│   • Query ask_query_memory (TTL 60 s cache)                          │
│   • score = max(token_sort_ratio, cosine_similarity)                 │
│   • score ≥ 0.55 → reuse stored SQL from thumbs-up feedback         │
├──────────────────────────────────────────────────────────────────────┤
│ FAST LANE 4 — Embedding template retrieval                           │
│   embedding_index._phase4_retrieve(question, ...)                    │
│   • TF-IDF unigram+bigram cosine over all templates                  │
│   • RRF fusion with dense embeddings (BGE / Voyage / Cohere)        │
│   • Top hit cosine ≥ 0.68 AND required_keywords match?              │
│       → Template SQL used directly                                   │
└──────────────────────────────────────────────────────────────────────┘
```

SSE emitted on fast-lane hit:
```json
{"type": "progress", "stage": "schema_sql", "status": "completed", "detail": "rule_fill", "duration_ms": 3}
```

---

## 5. Phase 1 — Identity Resolution

```
identity_resolver.py

For questions involving person names:
  • Phonetic blocking: jellyfish.metaphone(surname) → candidate group
  • Within-block scoring per candidate pair:
      Jaro-Winkler name similarity  (weight 0.40)
      Geographic proximity          (+0.20 same townland, +0.10 same parish)
      Temporal plausibility         (+0.10 gap ≤10 yr, -0.10 gap >30 yr)
      Family co-occurrence          (+0.15 same family_key)
  • score ≥ 0.75 → SAME_AS (confirmed individual)
  • score 0.50-0.74 → candidate (flagged in answer)

Output: resolved {sql_id, kg_uri} shared by all downstream phases
        identity_disambiguation note ("3 distinct individuals called John Murphy")
```

---

## 6. Phase 2 — Semantic Layer

Runs only for ANALYTICAL questions (or as the first attempt before intent classification).

```
semantic_layer.py

Layer 1 — Rule-based fill (try_rule_based_fill)
  • 14 metrics in METRIC_REGISTRY
  • confidence ≥ 0.80 → compile SQL → already handled in Fast Lane 1

Layer 2 — LLM slot-fill (if rule-fill confidence < 0.80)
  build_slot_fill_prompt(question, analysis)
  → LLM returns ONLY structured JSON:
    {metric, dimensions, filters, group_mode, confidence}
  → NEVER writes SQL
  parse_slot_fill(json_text) → SlotFill

Layer 3 — Deterministic SQL compiler
  compile_sql(slot_fill)
  • confidence ≥ 0.70 → deterministic SQL
  • confidence < 0.60 → fall through to FALLBACK
  compile_sparql(slot_fill) → SPARQL for RQ6 comparison (optional)

SSE: {stage: "slot_filling"} → {stage: "schema_sql"}
```

---

## 7. Phase 3 — Subgraph Engine

Runs only for RELATIONAL and COMPARATIVE questions.

```
subgraph_engine.py

VRTI SPARQL queries (for geographic hierarchy):
  get_townland_hierarchy(name)
    → civil parish, barony, county chain
  get_sibling_townlands(parish_uri)
    → all townlands in the same parish
  get_external_links(kg_uri)
    → logainm.ie, townlands.ie, OSM links

GraphDB neighbourhood:
  graphdb_sparql.get_entity_neighborhood(name, k=2, max_nodes=40)
    → k=2 hop neighbourhood SPARQL
    → Timeout: 15 s (graceful skip on failure)

Community summaries:
  data/seed/community_summaries.json
    → Pre-computed prose blurb for "history of X" questions

In-process GraphRAG enrichment (graphrag.py):
  vector_seed(question, top_k=8)
    → BAAI/bge-large-en-v1.5 cosine ANN over 28,078 node embeddings
    → Top-8 seed node_ids
  BFS traversal (k_hops=2, max_nodes=120)
    → Follows all edges 2 hops from each seed
    → Pruned by PageRank score to GRAPHRAG_MAX_NODES
  Linearise subgraph → compact triple table
  community_summary blurb attached if available
  Returns: GraphRAGResult{nodes, edges, linearized_text, provenance_path}
  Provenance: "vector_seed(8) → k-hop BFS → 47 triples"

KEY RULE: KG / GraphRAG provides qualitative context only.
          Counts and aggregates ALWAYS come from SQL, NEVER from KG.

SSE: {stage: "querying_subgraph"}
```

---

## 8. Phase 4 — Hybrid Embedding Retrieval

```
embedding_index.py

Runs in parallel with Phase 3 (or alone in FALLBACK).

TF-IDF sparse retrieval:
  • Unigram+bigram vectoriser over all template questions
  • Cosine similarity → ranked list L1

Dense retrieval:
  • Embed question using active provider:
      EMBEDDING_PROVIDER=local   → BAAI/bge-large-en-v1.5 (SentenceTransformers)
      EMBEDDING_PROVIDER=voyage  → voyage-large-2 (Azure production)
      EMBEDDING_PROVIDER=cohere  → embed-english-v3.0 (asymmetric)
  • Cosine ANN over pre-embedded corpus → ranked list L2

RRF fusion:
  score(d) = Σ 1/(60 + rank_i(d))
  Combined ranked list

Result:
  • Top hit cosine ≥ 0.68 AND required keywords present?
      → Already handled in Fast Lane 4
  • Otherwise: top-N results passed to FALLBACK template scoring
```

---

## 9. Phase 5 — Intent Classification

```
intent_router.py::classify_intent(question, analysis, slot_fill)

Priority order (first match wins):

1. COMPARATIVE — any comparative keyword:
   compare / compared to / versus / vs / difference between /
   contrast / relative to / how does / how did / better than /
   worse than / more than / less than / higher than / lower than / against

2. RELATIONAL — geography intent OR any keyword from:
   Relational: related to / connected to / link between / in the same parish /
               same barony / part of / neighbouring / adjacent / bordering
   Hierarchy:  which parish / what parish / civil parish / in the barony /
               townlands in / where is / where does / located in / situated in
   Heritage:   heritage / archaeological / monument / ring fort / holy well /
               history of / tell me about / describe / historically
   Sensemaking: overview / about the estate / describe the estate / summary of
   ──────────────────────────────────────────────────────────────────
   Core Rule 1 exception: if ONLY heritage/sensemaking keywords triggered
   (no relational/hierarchy/geography signal) AND output_mode=count/aggregate
   AND any analytical keyword present → classified as ANALYTICAL instead

3. ANALYTICAL — any of:
   • primary_intent in {population, eviction, emigration, tenancy}
   • output_mode in {count, aggregate, trend}
   • any analytical keyword: how many / how much / total / count of /
     number of / average / mean / proportion / percent / per year /
     by year / trend / over time / distribution / breakdown /
     most / least / highest / lowest / maximum / minimum / sum of / rate / ratio
   • slot_fill is not None

4. FALLBACK — default

SSE: {stage: "classifying_intent", detail: "ANALYTICAL"}
```

---

## 10. Route Dispatch

### ANALYTICAL

```
If semantic layer rule-fill ≥ 0.80:  use that SQL (already done in Fast Lane 1)
If LLM slot-fill confidence ≥ 0.70:  compile deterministic SQL
If slot-fill confidence < 0.60:       fall to FALLBACK

SSE: slot_filling → schema_sql → framing_query → querying_database
```

### RELATIONAL / HERITAGE

```
Phase 3 subgraph engine (VRTI SPARQL + GraphDB + in-process GraphRAG)
  → qualitative context + place graph + GraphRAG triples

Any numeric counts needed? → SQL path runs in parallel
  → RULE: counts always from SQL, never from KG

SSE: querying_subgraph
```

### COMPARATIVE

```
[ANALYTICAL SQL lane] in parallel with [RELATIONAL subgraph lane]
Phase 6 fusion reconciles results, surfaces discrepancies
```

### FALLBACK

```
_try_verified_analysis()          ← score 81 templates again
_phase4_retrieve()                ← embedding retrieval
_find_similar_approved_queries()  ← memory lookup again
_generate_sql()                   ← LLM free-form SQL with:
    • full annotated schema (all tables, columns, row counts, samples)
    • approved memory hits as few-shot examples
    • MUST start with SELECT or WITH

SSE: contacting_llm
```

---

## 11. SQL Safety Guardrail

Runs on ALL SQL regardless of source (compiled, template, memory, or LLM-generated).

```
_sanitize_and_validate_sql(sql)
  1. Strip markdown code fences (```sql ... ```)
  2. FORBIDDEN_SQL.search(sql):
       regex: \b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|DETACH|
                 PRAGMA|REINDEX|VACUUM|TRUNCATE|REPLACE)\b
       → raises ValueError on match → error SSE event
  3. Must start with SELECT or WITH
  4. Return sanitised SQL

SSE: {stage: "framing_query", status: "completed"}
```

---

## 12. SQLite Execution

```
_execute_with_recovery(question, townland_hint, sql, approved_examples)
  1. conn = get_db_conn()            ← WAL mode, foreign_keys=ON, Row factory
  2. conn.create_function("distance_km", 4, _haversine_km)
  3. conn.execute(sql).fetchall()
  4. On sqlite3.OperationalError:
       LLM repair: send question + bad SQL + exact error → repaired SQL
       re-execute; if still fails → return empty result + error note
  5. Cap at 500 rows
  6. Serialise non-JSON types (dates → strings, Decimals → floats)

SSE: {stage: "querying_database", detail: "N rows returned", duration_ms: N}
```

---

## 13. VRTI Enrichment (parallel, non-blocking)

```
_kg_context(question, townland_norm, force=True)
  Check _VRTI_STATUS_CACHE['down_until'] → skip if in cooldown (5 min)
  Check _VRTI_PARISH_CACHE[townland_key] → return cached if fresh (TTL 3600 s)

  vrti_sparql.get_townland_details_by_name(name)
    SPARQL: name, Irish name, WKT, centroid, parish, barony, county, OSM/OSI IDs
    Timeout: VRTI_REQUEST_TIMEOUT = 30 s

  On timeout/ConnectionError:
    _VRTI_STATUS_CACHE['down_until'] = now + 300 s
    return empty (pipeline continues without enrichment)

  Store in _VRTI_PARISH_CACHE with expiry now + 3600

SSE: {stage: "querying_vrti_graph", detail: "N townland(s) enriched"}
```

---

## 14. GraphDB Enrichment (optional, parallel)

```
graphdb_sparql.query(sparql)
  Endpoint: GRAPHDB_SPARQL_ENDPOINT
    default: http://localhost:7200/repositories/coolattin
    Azure:   http://51.120.71.162:7200/repositories/coolattin
  Timeout: GRAPHDB_REQUEST_TIMEOUT = 15 s
  On failure: return ([], []) — pipeline continues without GraphDB result

SSE: {stage: "querying_graphdb"}
```

---

## 15. Phase 6 — Fusion & Reconciliation

```
_fuse_lanes(sql_result, graphdb_result, graphrag_result, entity_label, kg_uri)

  For each numeric metric shared between SQL and GraphDB:
    delta = |sqlite_value - graphdb_value|
    relative = delta / sqlite_value
    label: "minor" (<5%) | "moderate" (5-20%) | "significant" (>20%)

  GraphRAG result merged as qualitative enrichment only (never numeric)

  Returns: {
    discrepancy_count,
    agreement_count,
    fusion_text,         ← prose summary of discrepancies
    source_provenance    ← list of sources consulted
  }

SSE: {stage: "querying_fusion", detail: "N discrepancies detected"}
```

---

## 16. Phase 7 — Multi-Model LLM Synthesis

```
_generate_rephrased_answer(question, actual_answer, data_context, kg_context, graphrag_context)

Provider chain (silent fallback, first available wins):
  [1] Claude (Anthropic)   ANTHROPIC_API_KEY + LLM_ALLOW_PAID=true
  [2] Grok (xAI)           GROK_API_KEY + LLM_ALLOW_PAID=true
  [3] OpenRouter            OPENROUTER_API_KEY
  [4] Ollama local          http://localhost:11434

SYSTEM prompt:
  "You are a digital historian specialising in 19th century Irish social history.
   Answer based only on the provided data. Do not introduce numbers not in the data."

DATA: first 20 rows in compact format

USER: original question + actual_answer + vrti_context + graphrag_context

Validation:
  • If LLM introduces numbers not in SQL rows → use actual_answer instead
  • Empty response → use actual_answer

SSE: {stage: "synthesizing_answer"}
```

---

## 17. Output Assembly

```
_build_availability_payload()  → has_local_data, has_vrti_data, suggested_questions
_build_related_insights()      → 1–3 follow-up question prompts
_build_chart_spec()            → Chart.js {type, labels, datasets} (for 7 template types)
_write_pdf_report()            → exports/ask/ask_report_{UTC}.pdf (hand-written PDF 1.4)

SSE: {stage: "preparing_output", detail: "PDF generated"}
```

PDF contains: question, SQL (if show_sql=true), answer text, data table, VRTI context, timestamp.

---

## 18. Final SSE Result Event

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
  "chart": {"type": "bar|line", "labels": [...], "datasets": [...]},
  "vrti_context": {
    "townlands": [{"name": "...", "parish": "...", "barony": "...", "county": "..."}],
    "parish_count": N
  },
  "graphrag_context": {
    "nodes": 23,
    "edges": 41,
    "linearized_text": "...",
    "provenance_path": "vector_seed(8) → k-hop BFS → 41 triples"
  },
  "fusion": {"discrepancy_count": 0, "agreement_count": 1, "fusion_text": "..."},
  "discrepancies": [],
  "warnings": ["Census data begins at 1841"],
  "identity_disambiguation": "2 distinct individuals named John Murphy found",
  "pdf_url": "/api/ask/pdf/ask_report_20260617_143022.pdf",
  "availability": {"has_local_data": true, "has_vrti_data": true},
  "related_insights": ["How many families were evicted in 1847?"],
  "query_provenance": {
    "strategy": "rule_fill | verified_analysis | memory_reuse | template_embedding | slot_fill_llm | llm_sql | subgraph | comparative",
    "used_approved_memory": false,
    "direct_memory_reuse": false,
    "execution_mode": "executed_as_generated | repaired | fallback"
  },
  "llm_meta": {
    "provider": "anthropic | grok | openrouter | ollama | verified_analysis | rule_fill",
    "model": "claude-3-5-haiku-20241022",
    "mode": "analytical_semantic | relational_subgraph | comparative | fallback"
  }
}
```

---

## 19. Worked Example: "How many emigrants left from Aghowle in 1852?"

```
Pre-flight:
  townland resolved: "AGHOWLE LOWER" → sql_id=42, kg_uri="http://virtualtreasury.ie/..."
  _analyse_question: primary_intent=emigration, output_mode=count, year=1852, scope=townland

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

SSE events:
  schema_sql → framing_query → querying_database(3 rows) → querying_vrti_graph → synthesizing_answer

Result:
  answer: "47 emigration records from Aghowle Lower in 1852"
  llm_rephrased_answer: "In 1852, forty-seven individuals from Aghowle Lower emigrated..."
  sql: [shown if show_sql=true]
  query_provenance.strategy: "rule_fill"
  llm_meta.provider: "openrouter"
  Total latency: ~3–8 s (synthesis only; no LLM SQL generation)
```

---

## 20. Performance Characteristics

| Route | LLM calls | Typical latency |
|---|---|---|
| Fast Lane 1 (rule-fill) | 0 SQL + 1 synthesis | 3–8 s |
| Fast Lane 2 (verified template) | 0 SQL + 1 synthesis | 3–8 s |
| Fast Lane 3 (memory reuse) | 0 SQL + 1 synthesis | 3–8 s |
| Fast Lane 4 (embedding template) | 0 SQL + 1 synthesis | 3–8 s |
| ANALYTICAL (LLM slot-fill) | 1 slot-fill + 1 synthesis | 8–20 s |
| RELATIONAL (subgraph) | 0 SQL + 1 synthesis | 5–15 s (SPARQL bound) |
| COMPARATIVE | 1–2 + 1 synthesis | 10–25 s |
| FALLBACK (LLM SQL) | 1 SQL + 1 synthesis | 10–30 s |

LLM calls shown are for SQL generation. Every path calls the synthesis LLM for the final answer rewrite.

Evaluation result (75-question benchmark, 2026-06-10):
- LLM SQL calls required: 0 (all questions hit fast lanes or semantic layer)
- p50 latency: 372 ms
- p90 latency: 2,095 ms

---

## 21. In-Process Caches

| Cache | TTL | Contents |
|---|---|---|
| `_TOWNLAND_CATALOG_CACHE` | 10 min | All canonical townland names |
| `_VRTI_PARISH_CACHE[key]` | 60 min per townland | VRTI enrichment per townland |
| `_VRTI_STATUS_CACHE` | 5 min cooldown | VRTI unavailability circuit breaker |
| `_OPENROUTER_STATUS_CACHE` | 60 s | OpenRouter health check result |
| `_OLLAMA_MODEL_CACHE` | 120 s | Available Ollama model list |
| `_PROMPT_SCHEMA_CACHE` | 5 min | Annotated schema descriptor string |
| `_QUERY_MEMORY_CACHE` | 60 s | Approved memory rows from SQLite |
| `_SCHEMA_COMPAT_CACHE` | process lifetime | clearances column name variant |
| `_UNIFIED_CACHE` | process lifetime | unified_processed.csv DataFrame |
| NetworkX graph | process lifetime | graph_nodes + graph_edges loaded in graphrag.py |

---

## 22. Error and Fallback States

| Failure | Behaviour |
|---|---|
| VRTI timeout | Skip enrichment; return empty `vrti_context`; set cooldown 5 min |
| GraphDB timeout | Skip GraphDB; continue with SQL result |
| GraphRAG graph not loaded | Skip GraphRAG enrichment; pipeline answers as before |
| SQL OperationalError | LLM repair attempt (up to 3 retries); if still fails, return empty result |
| LLM provider failure | Try next provider in chain (Claude → Grok → OpenRouter → Ollama) |
| Entire synthesis chain fails | Return `actual_answer` (pre-formatted SQL result) with note |
| All fast lanes miss, intent = FALLBACK | LLM generates SQL; if that fails, return "no answer" with explanation |
