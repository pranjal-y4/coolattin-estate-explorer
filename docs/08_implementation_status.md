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
| D5 — Workhouse Linkage Prototype (place-first) | **Done** | Full ER pipeline with phonetic blocking, fuzzy scoring, confidence bands, and persisted SQLite tables |
| D6 — Explainable Ask Improvements | **Mostly done** | SQL, route, provenance, vector retrieval meta, identity disambiguation all wired up |
| D7 — Graphical Insight Layer | **Mostly done** | Chart spec built and rendered for 7 template types |
| D8 — RDF/KG Comparative Prototype | **Partial** | GraphDB SPARQL integration done; co: ontology endpoint live; comparison UI exists; Turtle uplift script not committed |
| D9 — Technical Evaluation Pack | **Partial** | `ask_eval.py` harness built; eval_results/ baselines captured (phase 0–5+) |
| D10 — LLM Evaluation Pack | **Not done** | Tests not formally run and recorded |
| D11 — User Evaluation Pack | **Not done** | No participants, no task sheet |
| D12 — Final Dissertation Evidence Pack | **Not done** | Dissertation not written |
| D13 — Demo Freeze Package | **Not done** | Git tag not created |
| **NEW** — Orchestrated Ask Pipeline | **Done** | 7-phase pipeline (intent→retrieval→semantic→subgraph→LLM→identity→synthesis); enabled by default |
| **NEW** — Identity Resolution | **Done** | Three-layer model (Mention/Person/Factoid); phonetic blocking; Jaro-Winkler + geo/temporal scoring |
| **NEW** — Hybrid Embedding Retrieval | **Done** | TF-IDF + Cohere/local BGE dense; pgvector optional; fast-lane short-circuit |

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
- `backend/services/entity_resolution/normalise.py` — case/punctuation normalisation, initials expansion (`Jno→John`, `Wm→William`, `Jas→James`), Mc/Mac/O variants, phonetic encoding via `jellyfish.metaphone`
- `backend/services/entity_resolution/candidates.py` — blocking strategy: exact normalised name, surname+initial, phonetic surname, place+name combos; returns up to 25 ranked candidates per mention
- `backend/services/entity_resolution/scoring.py` — multi-signal scoring: name similarity (rapidfuzz token_sort_ratio), place match, date window, phonetic match → maps to `CONFIRMED_MATCH` / `POSSIBLE_MATCH` / `WEAK_CANDIDATE` / `NO_MATCH`

**Approach:**
- Place-first: candidates are filtered by matching `electoral_division` / `townland_norm` before name scoring
- Date window: ±1 year around the estate record's event year
- Confidence bands: High (CONFIRMED_MATCH ≥ 0.75) / Medium (POSSIBLE_MATCH 0.50–0.74) / Low (WEAK_CANDIDATE < 0.50)
- Persisted results: stored in `source_mentions`, `entity_resolution_candidates`, `workhouse_unified_links`, `entity_resolution_decisions` SQLite tables
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

- `backend/integrations/graphdb_sparql.py` — full SPARQL client for a local GraphDB instance running the `co:` (Coolattin ontology) repository at `http://localhost:7200/repositories/coolattin` (also deployed at `http://51.120.71.162:7200/repositories/coolattin`)
- `GRAPHDB_ENABLED`, `GRAPHDB_SPARQL_ENDPOINT`, `GRAPHDB_REQUEST_TIMEOUT` env vars wired into `config.py`
- The Ask pipeline queries GraphDB in parallel with SQLite when `GRAPHDB_ENABLED=true`; results are merged and discrepancies between SQLite and GraphDB are surfaced in the SSE payload (`fusion`, `discrepancies` fields)
- `semantic_layer.py::compile_sparql()` generates equivalent SPARQL for any slot-fill that has a KG mapping — this is the side-by-side SQL-vs-SPARQL comparison mechanism

### What is still outstanding

1. **Turtle uplift script** — a script that reads `unified_record` rows and writes a `.ttl` file into the `co:` ontology; the GraphDB instance needs to be populated by hand currently
2. **Formal comparison table** — the 5-question SQL-vs-SPARQL comparison table for the dissertation appendix (queries exist; table not written up)
3. **`rdflib` in-process fallback** — a pure-Python path for offline/exam demos without a running GraphDB instance

The comparison framework is architecturally complete; what is missing is a documented run of the 5 competency questions with side-by-side results written up as a dissertation table.

---

## D9, D10, D11 — Evaluation Packs

**Status: Not done — evaluation data collection not started**

| Pack | What to do |
|---|---|
| D9 (technical) | Run all 15 competency questions. Record: template ID hit or LLM fallback, SQL generated, result rows, latency (from SSE stage durations), correctness classification (Correct / Partially correct / Incorrect / No answer) |
| D10 (LLM) | Run 10+ free-form questions. Record: SQL validity, self-repair invocations, answer delivery, hallucination checks against direct SQL |
| D11 (user) | Task-based session with 4–6 participants. Measure task completion, trust rating, clarity score |

These produce no code. They produce spreadsheet tables that feed the dissertation evaluation chapter.

---

## D12, D13 — Evidence Pack + Demo Freeze

**Status: Not done — dependent on D9–D11 completing**

D13 (demo freeze):
```bash
git tag v1.0-demo-freeze-july13
git push origin v1.0-demo-freeze-july13
```

Screenshots and a recorded demo video as backup need to be made once the demo path is rehearsed.

---

## What Actually Needs Coding Work

After the June 2026 updates, the remaining coding items are smaller:

### 1. D8 — Turtle uplift script + formal comparison table
**New file:** `scripts/rdf_uplift.py` — reads `unified_record` rows, writes `data/seed/coolattin_sample.ttl` in the `co:` ontology  
**New file:** `scripts/sparql_comparison_table.py` — runs the 5 competency questions as SQL and SPARQL, records latency, outputs a comparison table  
The GraphDB client and comparison framework already exist (`graphdb_sparql.py`, `semantic_layer.compile_sparql()`).

### 2. D5 — Workhouse review UI (minor)
The entity resolution pipeline and tables are fully implemented. What is missing is a web page for reviewing `entity_resolution_candidates` and confirming / rejecting links through the UI. This is low-priority (the data is there for the dissertation without a review UI).

### 3. D6 (minor) — Explicit source table list in Ask UI
The `source_tables` field (e.g. `["unified_record", "census_record"]`) is not yet surfaced visibly in the UI answer block.

---

## What Does NOT Need Any New Code

| Item | Reason |
|---|---|
| D3 — Dataset audit | Run SQL queries against the existing DB, put numbers in a table |
| D4 — Geospatial alignment audit | Run 4 SQL queries against `townland` table, read `reconciliation_gaps.csv` |
| D5 (mostly) | Pipeline implemented; just needs batch run + write-up |
| D6 (mostly) | SQL display, route, provenance, vector retrieval meta, identity disambiguation all work |
| D7 (mostly) | Chart layer is live for 7 templates; just needs screenshots taken |
| D8 (comparison) | GraphDB integration done; need to run the 5 Qs and record the table |
| D9 — Technical evaluation | Use `ask_eval.py` harness or browse Ask page; record results |
| D10 — LLM evaluation | Run questions, inspect SSE log, record results |
| D11 — User evaluation | Recruit participants, run tasks, record observations |
| D12, D13 | Writing, git tag, screenshots |

---

## Priority Order for Remaining Work

Given submission on 3 August 2026:

| Priority | Item | Effort | Dissertation impact |
|---|---|---|---|
| 1 | D8 — Turtle uplift + comparison table | 1 day coding + write-up | Highest — directly answers RQ6 |
| 2 | D3, D4 — Data and alignment audits | 2 hours (SQL + write-up) | Medium — feeds methods chapter |
| 3 | D9 — Technical evaluation (15 Qs via ask_eval.py) | 2 hours | High — needed for evaluation chapter |
| 4 | D10 — LLM evaluation | 2 hours | Medium |
| 5 | D11 — User evaluation | 1 day with participants | Medium |
| 6 | D7 screenshots | 30 mins | Low effort, needed for appendix |
| 7 | D5 review UI | Half day | Low — data is already there |
| 8 | D12, D13 — Evidence pack + git tag | 1 hour | Required to close out |
