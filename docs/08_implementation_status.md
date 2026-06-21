# Implementation Status — Tracking Plan vs Codebase

**Project:** Coolattin Estate Records Explorer  
**Candidate:** Pranjal Yadav  
**Reference plan:** Tracking Plan dated 13 July 2026  
**This document:** What is already in the code, what is not, and what is partial.

---

## Summary

| Deliverable | Status | Notes |
|---|---|---|
| D1 — Architecture Defence Package | **Partial** | Code exists; dissertation chapter not written |
| D2 — Revised Research Questions | **Partial** | RQs defined in tracking plan; not in dissertation |
| D3 — Unified Dataset Audit | **Not done** | No formal audit table produced |
| D4 — Geospatial Alignment Audit | **Not done** | Alignment code works; audit figures not collected |
| D5 — Workhouse Linkage Prototype (place-first) | **Done** | Full ER pipeline: phonetic blocking, 7-signal scoring (60 pt), confidence bands CONFIRMED≥0.75/POSSIBLE≥0.50/WEAK<0.50, 140 confirmed links persisted in 4 SQLite tables |
| D6 — Explainable Ask Improvements | **Mostly done** | SQL, route, provenance, vector retrieval meta, identity disambiguation all wired up |
| D7 — Graphical Insight Layer | **Mostly done** | Chart spec built and rendered for 7 template types; KG explore D3 graph live |
| D8 — RDF/KG Comparative Prototype | **Partial** | GraphDB SPARQL integration done; co: ontology endpoint live; semantic_layer.compile_sparql() generates SPARQL from same SlotFill; comparison UI exists; RQ6 table formally run (see docs/11_demo_freeze.md §1.4) — local co: repo not yet loaded with data |
| D9 — Technical Evaluation Pack | **Done** | 75-question formal evaluation run 2026-06-10; 89.3% routing accuracy, 100% aggregation correctness; full results in `eval_results/eval_graphrag_on.json` + `docs/11_demo_freeze.md` |
| D10 — LLM Evaluation Pack | **Partial** | GraphRAG enrichment evaluated (9 R-series cases, 100% numeric delta = 0, avg usefulness 4.4/5); full free-form LLM eval not yet formally tabulated |
| D11 — User Evaluation Pack | **Not done** | No participants, no task sheet |
| D12 — Final Dissertation Evidence Pack | **Not done** | Dissertation not written |
| D13 — Demo Freeze Package | **Done** | Git tag `v1.0-demo-freeze` created 2026-06-10; evaluation results pinned; canonical config documented in `docs/11_demo_freeze.md` |
| **NEW** — Orchestrated Ask Pipeline | **Done** | `_orchestrated_pipeline_stream()` in ask_service.py; 4 fast lanes + intent routing + 3 ANALYTICAL/RELATIONAL/COMPARATIVE dispatch lanes; enabled by default (`ASK_USE_NEW_PIPELINE=true`) |
| **NEW** — Intent Router | **Done** | `intent_router.py`; ANALYTICAL/RELATIONAL/COMPARATIVE/FALLBACK; Core Rule 1 heritage/sensemaking override; exact keyword sets per class |
| **NEW** — Semantic Layer | **Done** | `semantic_layer.py`; 22-metric registry; rule-based slot-fill (confidence ≥ 0.80, 0 LLM); LLM slot-fill (confidence ≥ 0.70); deterministic SQL compiler; SPARQL compiler for RQ6 |
| **NEW** — Subgraph Engine | **Done** | `subgraph_engine.py`; VRTI multi-hop SPARQL + GraphDB k=2 neighbourhood; qualitative context only (counts always from SQL) |
| **NEW** — Identity Resolution | **Done** | `identity_resolver.py`; three-layer Mention/Person/Factoid model; Jaro-Winkler + Metaphone phonetic blocking + geo/temporal scoring |
| **NEW** — Hybrid Embedding Retrieval | **Done** | `embedding_index.py`; TF-IDF unigram+bigram cosine + RRF; fast-lane threshold 0.68; pgvector optional backend |
| **NEW** — GraphDB Integration | **Done** | `graphdb_sparql.py`; co: ontology (`https://coolattin.ie/ontology#`); query() + get_entity_neighborhood(); fusion + discrepancy detection in Phase 6 |
| **NEW** — KG Explore Page | **Done** | `/kg-explore` page; `/api/kg/graph` (D3 force graph, 152 nodes), `/api/kg/compare` (4 canned SQL vs SPARQL scenarios + custom), `/api/kg/scenarios` |
| **NEW** — GraphRAG Pipeline | **Done** | In-process property graph (`graphrag.py`): 49,081 nodes · 64,342 edges · 28,078 BGE-embedded; vector seed → k-hop BFS → linearised subgraph; additive enrichment (zero numeric delta validated); graph built by `scripts/build_graph.py` |
| **NEW** — Multi-model Synthesis Chain | **Done** | LLM priority chain: Claude (`anthropic`) → Grok (`xAI`) → OpenRouter → Ollama; failure at any stage silently falls to next; synthesis model configurable via `ASK_SYNTHESIS_MODEL` |
| **NEW** — Voyage AI Embedding Provider | **Done** | `voyage_embeddings.py` routes to `voyageai.Client` when `EMBEDDING_PROVIDER=voyage`; required on Azure where torch/sentence-transformers are excluded from build |
| **NEW** — Azure CI/CD Pipeline | **Done** | `azure-deploy.yml`: OIDC login → `requirements-azure.txt` swap → zip deploy → Oryx build → startup command enforcement; deploys to `coolattin-app.azurewebsites.net` (coolattin-rg2) |
| **NEW** — Security Hardening | **Done** | `FLASK_ENV` defaults to production; `ADMIN_API_KEY` guard on admin endpoints; audit log on Ask; PDF download hardening; `flask-limiter` in requirements |
| **NEW** — Parallel Map Loading + Instant Townland Dropdown | **Done** | Map loads GeoJSON + unified data in parallel (saves ~half download wait); Ask townland catalog pre-loaded client-side (no per-keystroke round-trip) |

---

## D1 — Architecture Defence Package

**Status: Partial — code is there, dissertation framing is not written**

Everything described in the architecture is implemented in the codebase:
- Relational uplift: `unified_processed.csv` → `unified_record` SQLite table ✓
- KG enrichment: VRTI SPARQL pull into `townland` table ✓
- Static archival assumption: batch ingest, read-only serving layer ✓
- Template-first Ask pipeline protecting deterministic answers ✓
- SQL vs SPARQL tradeoff: the SQL side exists; the SPARQL comparison prototype does **not** exist yet (that is D8)

What is missing is the written dissertation chapter that frames these as deliberate research design choices rather than implementation conveniences.

---

## D2 — Revised Research Questions

**Status: Partial — RQs defined in the tracking plan, not yet in dissertation**

The seven RQs (RQ1–RQ7) are written in the tracking plan. The system provides evidence for most of them:
- RQ1 (data cleaning, geospatial alignment): code exists, audit figures not collected
- RQ2 (KG linkage): VRTI integration works, coverage figures not formally measured
- RQ3 (workhouse linkage): name matching exists, place-first method not yet upgraded
- RQ4 (NL-to-SQL pipeline): 83 templates + LLM fallback fully implemented
- RQ5 (explainable AI): SQL display, provenance, coverage notes all exist in the UI
- RQ6 (SQL vs SPARQL comparison): SQL side exists; RDF/SPARQL prototype not built
- RQ7 (graphical summaries): chart layer works for 7 template types

---

## D3 — Unified Dataset Audit

**Status: Not done**

The unified dataset (`unified_processed.csv`, 13,707 rows) exists and is in use. What has not been produced:
- Null rate per column (e.g. how many rows have `age`? `gender`? `ship_name`?)
- Before/after duplicate count (how many record_ids are unique?)
- Year field validity check (are all years integers in expected range?)
- `townland_norm` coverage (how many rows have a non-null, resolvable townland?)
- `has_emigration_record` / `has_eviction_record` / `has_tenancy_record` breakdown

This is a data analysis task, not a coding task. Run it against `coolattin.db` with a few SQL queries:

```sql
-- Null rates for key fields
SELECT
  COUNT(*) AS total,
  SUM(CASE WHEN age IS NULL THEN 1 ELSE 0 END) AS null_age,
  SUM(CASE WHEN gender IS NULL THEN 1 ELSE 0 END) AS null_gender,
  SUM(CASE WHEN ship_name IS NULL THEN 1 ELSE 0 END) AS null_ship_name,
  SUM(CASE WHEN townland_norm IS NULL THEN 1 ELSE 0 END) AS null_townland_norm,
  SUM(CASE WHEN children_count IS NULL THEN 1 ELSE 0 END) AS null_children_count,
  SUM(has_emigration_record) AS emigration_records,
  SUM(has_eviction_record) AS eviction_records,
  SUM(has_tenancy_record) AS tenancy_records
FROM unified_record;
```

---

## D4 — Geospatial Alignment Audit

**Status: Not done**

The alignment code is fully implemented:
- `townland_service.normalize_townland_name()` canonicalises all names ✓
- `reconcile_with_reference()` enriches with barony/parish from townlands.ie reference ✓
- KG enrichment overlays centroid, WKT, VRTI identifiers ✓
- `reconciliation_gaps.csv` is written to `data/source_snapshots/` when unresolved cases exist ✓

What has not been produced is the audit table:

| Metric | Value needed |
|---|---|
| Townlands with centroid (lat/lon) | Run: `SELECT COUNT(*) FROM townland WHERE centroid_lat IS NOT NULL` |
| Townlands with WKT boundary | Run: `SELECT COUNT(*) FROM townland WHERE wkt_geometry IS NOT NULL` |
| Townlands with VRTI URI | Run: `SELECT COUNT(*) FROM townland WHERE kg_uri IS NOT NULL` |
| Townlands with civil_parish | Run: `SELECT COUNT(*) FROM townland WHERE civil_parish IS NOT NULL` |
| Unresolved townlands | Read `data/source_snapshots/reconciliation_gaps.csv` |

Run these queries once and record the numbers. That is the entire audit.

---

## D5 — Workhouse Linkage Prototype

**Status: Done — full entity resolution pipeline implemented**

### What is implemented (June 2026)

The workhouse linkage is now a dedicated entity-resolution subsystem, separate from the Ask pipeline:

**Core modules:**
- `backend/services/workhouse_entity_resolution.py` — main pipeline orchestrator
- `backend/services/entity_resolution/normalise.py` — Unicode NFKD → uppercase → editorial annotations stripped → abbreviations expanded (`JNO→JOHN`, `WM→WILLIAM`, `JAS→JAMES`, `THOS→THOMAS`) → Mc/Mac/O variants normalised → `jellyfish.metaphone()` phonetic encoding
- `backend/services/entity_resolution/candidates.py` — blocking: exact normalised name, surname+initial, phonetic surname, place+name combos; returns up to 25 ranked candidates per mention
- `backend/services/entity_resolution/scoring.py` — 7-signal scoring over a 60-point scale (normalised to 0.0–1.0):

| Signal | Max pts | Rule |
|---|---|---|
| Full name similarity (token_sort_ratio) | 10 | ≥90%→10; ≥75%→7; ≥60%→4; else→0 |
| Exact surname | 10 | Exact→10; Metaphone match→7; else→0 |
| Forename | 10 | Either missing→5 (neutral); exact→10; ≥80%→7; ≥60%→4; else→0+conflict |
| Townland normalisation | 10 | Exact→10; variant→6; else→0 |
| Birth-year alignment | 5 | Gap≤3y→5; ≤8y→3; else→0 |
| Gender | 10 | Both missing→5 (neutral); match→10; mismatch→0+conflict |
| Timeline alignment | 5 | Age-progression consistency |

**Confidence bands:**
- CONFIRMED_MATCH: score ≥ 0.75
- POSSIBLE_MATCH: 0.50 ≤ score < 0.75
- WEAK_CANDIDATE: score < 0.50
- NO_MATCH: all signals missing or hard negative rule triggered

**Approach:**
- Place-first blocking: candidates filtered by `electoral_division` / `townland_norm` match before name scoring
- Date window: ±1 year around event_year
- Hard negative rules block impossible age/date conflicts, gender mismatches
- Persisted in `source_mentions`, `entity_resolution_candidates`, `workhouse_unified_links`, `entity_resolution_decisions`
- `match_review_repository.py` provides CRUD for human review of borderline candidates

**What is not yet done:**
- UI for reviewing persisted match candidates (the tables exist but no review page is wired up)
- Batch run triggered from the UI (currently callable via script/API endpoint only)

---

## D6 — Explainable Ask Improvements

**Status: Mostly done — all major XAI elements are wired up**

| XAI measure | Required by plan | Implemented? |
|---|---|---|
| Query route label | Show template / LLM / memory route | **Yes** — `query_provenance.strategy` field in SSE payload; shown in Ask UI |
| SQL visibility | Show executed SQL in collapsible section | **Yes** — `local_sqlite_query` rendered in `askSqliteQuery` element |
| Source trace (tables used) | List which tables the SQL touched | **Partial** — `_PROMPT_CATEGORY_COLUMNS` defines table context for prompts; not explicitly listed in UI output |
| Data coverage note | Warn when answer depends on sparse fields | **Partial** — `_question_data_coverage_warnings()` exists but only handles the 1821 census gap; no null-rate warnings for other fields |
| Uncertainty label for fuzzy matches | High/Medium/Low confidence | **Partial** — fuzzy match warning text exists ("Did you mean X?") but no numeric confidence label shown to user |
| Answer consistency check | Compare final text vs result table | **Not done** — no automated consistency check; manual review only |
| Provenance PDF | Question + SQL + rows + source notes | **Yes** — PDF export includes question, SQL, answer text |
| Failure transparency | Explain blocked/unsupported queries | **Yes** — `_sanitize_and_validate_sql()` blocks writes; diagnostic messages explain failures |

**What is missing for full D6:**
- Explicit "Source tables: unified_record, census_record" line in the Ask UI response
- Extending `_question_data_coverage_warnings()` to cover other known sparse fields (age, gender, ship_name for questions Q5, Q10, Q15)
- Showing numeric confidence (e.g. 0.87) alongside fuzzy match warnings

---

## D7 — Graphical Insight Layer

**Status: Mostly done — chart infrastructure exists and works for 7 template types**

### What is implemented:
- `VERIFIED_ANALYSIS_CHART_HINTS` maps 7 template IDs to chart types: `bar` or `line` ✓
- `_build_chart_spec()` in `ask_service.py` builds `{type, title, labels, values}` from SQL result columns ✓
- `renderChart()` in `ask.js` renders line charts (SVG path) and bar charts (inline CSS bars) ✓
- The 7 chart-enabled templates are:

| Template | Chart type |
|---|---|
| `tenant_land_gender_average` | bar |
| `most_populous_1841_vs_1861` | bar |
| `population_trend_1841_1861` | line |
| `holy_well_population_relationship` | bar |
| `ring_fort_population_relationship` | bar |
| `canada_emigration_peak_period` | line |
| `smallest_townland_plots` | bar |

### What is not yet done for D7:
- **Screenshots for 5 example questions** — the plan requires documented screenshots as dissertation evidence. No screenshots have been captured yet.
- **Chart spec documentation** — needed as an appendix item.
- The plan mentions map highlight (highlight townlands returned by a query) — this is **not implemented**. Ask results do not highlight polygons on the map.

---

## D8 — RDF/KG Comparative Prototype

**Status: Partial — GraphDB integration done; Turtle uplift script and formal comparison table outstanding**

### What is implemented (June 2026)

- `backend/integrations/graphdb_sparql.py` — full SPARQL client for a local GraphDB instance running the `co:` (Coolattin ontology) repository at `http://localhost:7200/repositories/coolattin` (also deployed at `http://51.120.71.162:7200/repositories/coolattin`). Functions: `query(sparql)`, `get_entity_neighborhood(name, k=2, max_nodes=40)`
- `GRAPHDB_ENABLED`, `GRAPHDB_SPARQL_ENDPOINT`, `GRAPHDB_REQUEST_TIMEOUT` env vars wired into `config.py`
- The Ask pipeline queries GraphDB in parallel with SQLite when `GRAPHDB_ENABLED=true`; results merged; discrepancies between SQLite and GraphDB surfaced in SSE payload (`fusion`, `discrepancies` fields)
- `semantic_layer.py::compile_sparql(slot_fill)` generates equivalent SPARQL from the same SlotFill struct that produces SQL — this is the direct SQL-vs-SPARQL comparison mechanism for RQ6
- `backend/routes/kg_explore.py` — KG explore page with 3 endpoints:
  - `GET /api/kg/graph` — D3.js force graph (152 townland nodes, geographic hierarchy edges)
  - `GET /api/kg/scenarios` — 4 canned comparison scenarios (emigration_count_by_townland, eviction_count_by_year, surname_frequency, person_event_detail)
  - `POST /api/kg/compare` — executes both SQLite and GraphDB SPARQL, returns side-by-side results with timing

### What is still outstanding

1. **Turtle uplift script** — `scripts/rdf_uplift.py` that reads `unified_record` rows and writes a `.ttl` file into the `co:` ontology; GraphDB population is currently manual
2. **Formal comparison table** — the 5-question SQL-vs-SPARQL comparison table for the dissertation appendix (competency questions exist in `docs/sparql_competency_questions.md`; table not formally run and written up)

The comparison framework is architecturally complete; what is missing is a documented run of the 5 competency questions with side-by-side results written up as a dissertation table.

---

## D9, D10, D11 — Evaluation Packs

**D9 status: Done — 75-question formal evaluation run 2026-06-10**

Evaluation run against 75 competency questions (A-series analytical, R-series relational, C-series comparative, G-series out-of-scope) with GraphRAG both ON and OFF:

| Metric | GraphRAG ON | GraphRAG OFF |
|---|---|---|
| Questions run | 75 | 75 |
| Routing accuracy | 89.3% | 89.3% |
| Aggregation correctness | 100.0% | 100.0% |
| SQL exec success | 100.0% | 100.0% |
| Template hit rate | 100.0% | 100.0% |
| LLM calls required | 0 | 0 |
| p50 latency | 372 ms | 365 ms |
| p90 latency | 2,095 ms | 2,049 ms |

Full results: `eval_results/eval_graphrag_on.json`, `eval_results/eval_graphrag_on.md`, `docs/11_demo_freeze.md`

**Known issues documented (non-blocking):**
- Honest-refusal rate 0%: G-series out-of-scope questions are routed by the semantic layer (partial keyword matches trigger tenancy/eviction templates) rather than refused — an explicit out-of-scope classifier would fix this
- Lane routing 72%: Several census/geography questions are correctly answered as ANALYTICAL but labelled RELATIONAL by intent router — SQL result is correct, only intent label disagrees

**D10 status: Partial — GraphRAG enrichment evaluated; full free-form LLM eval outstanding**

GraphRAG enrichment evaluation (9 R-series + multi-hop cases):
- Numeric delta = 0 for all 9 cases (acceptance gate passed)
- Avg auto-usefulness score: 4.4/5
- Avg latency overhead (ON − OFF): +46 ms at p90 (warm BGE)

What is still outstanding: a formal table of 10+ free-form questions with SQL validity, self-repair invocations, and hallucination checks.

**D11 status: Not done** — No participants recruited, no task sheet prepared.

---

## D12, D13 — Evidence Pack + Demo Freeze

**D13 status: Done — git tag `v1.0-demo-freeze` created 2026-06-10**

The freeze captures:
- Full regression results (75 questions, GraphRAG ON + OFF)
- GraphRAG enrichment evaluation (9 R-series cases)
- RQ6 SQL-vs-SPARQL comparison table
- Canonical configuration for reproducible deployment (see `docs/11_demo_freeze.md §2`)

**D12 status: Not done** — Dissertation not written; evidence pack assembled partially via `docs/dissertation_evidence_dossier.md`.

---

## What Actually Needs Coding Work

The remaining coding items are small. All major infrastructure is complete.

### 1. D8 — Load local co: ontology repository with data
The GraphDB SPARQL endpoint and comparison framework are complete. The local `coolattin` repository is provisioned but not loaded with data (RQ6 comparison returns 0/empty — see `docs/11_demo_freeze.md §1.4`). Fix: run `scripts/rdf_uplift.py` to write Turtle and load it into GraphDB, or document the open-world empty-result as a finding.

### 2. D5 — Workhouse review UI (low priority)
The entity resolution pipeline, 140 confirmed links, and all SQLite tables are fully implemented. Missing: a web page for reviewing `entity_resolution_candidates`. The data is complete for dissertation evidence without the UI.

### 3. D6 (minor) — Explicit source table list in Ask UI
The `source_tables` field from `query_provenance` is not yet surfaced as a visible label in the UI answer block.

### 4. Set `GROK_API_KEY` in Azure production environment
Grok is the second provider in the multi-model synthesis chain (Claude → Grok → OpenRouter → Ollama). Without the key, the chain silently falls to OpenRouter. Low risk — OpenRouter works fine — but enabling Grok completes the full synthesis chain as designed.

---

## What Does NOT Need Any New Code

| Item | Reason |
|---|---|
| D3 — Dataset audit | Run SQL queries against the existing DB, put numbers in a table |
| D4 — Geospatial alignment audit | Run 4 SQL queries against `townland` table, read `reconciliation_gaps.csv` |
| D5 (mostly) | 140 confirmed links already in seed DB; just needs write-up |
| D6 (mostly) | SQL display, route, provenance, vector retrieval meta, identity disambiguation all work |
| D7 (mostly) | Chart layer live for 7 templates + KG explore D3 graph; just needs screenshots |
| D8 (comparison) | RQ6 table produced; local co: repo loading is the only gap |
| D9 — Technical evaluation | **Done** — 75-question eval in `eval_results/` |
| D10 — LLM evaluation | GraphRAG eval done; free-form LLM eval needs tabulation |
| D11 — User evaluation | Recruit participants, run tasks, record observations |
| D12, D13 | D13 done (git tag created); D12 = writing |

---

## Priority Order for Remaining Work

Given submission on 3 August 2026:

| Priority | Item | Effort | Dissertation impact |
|---|---|---|---|
| 1 | D3, D4 — Data and alignment audits | 2 hours (SQL + write-up) | Medium — feeds methods chapter |
| 2 | D8 — Load co: repo OR document as open-world finding | Half day | High — directly answers RQ6 |
| 3 | D10 — Free-form LLM evaluation (10+ questions) | 2 hours | Medium — feeds evaluation chapter |
| 4 | D11 — User evaluation | 1 day with participants | Medium |
| 5 | D7 screenshots | 30 mins | Low effort, needed for appendix |
| 6 | D5 review UI | Half day | Low — data already there |
| 7 | D12 — Write dissertation | Weeks 7–12 | Required |
