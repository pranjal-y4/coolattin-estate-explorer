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
| D5 — Workhouse Linkage Prototype (place-first) | **Partial** | Name matching done; place-first filter NOT implemented |
| D6 — Explainable Ask Improvements | **Mostly done** | SQL, route, provenance, coverage notes all wired up |
| D7 — Graphical Insight Layer | **Mostly done** | Chart spec built and rendered for 7 template types |
| D8 — RDF/KG Comparative Prototype | **Not done** | No Turtle file, no SPARQL generation path, no Fuseki |
| D9 — Technical Evaluation Pack | **Not done** | Tests not formally run and recorded |
| D10 — LLM Evaluation Pack | **Not done** | Tests not formally run and recorded |
| D11 — User Evaluation Pack | **Not done** | No participants, no task sheet |
| D12 — Final Dissertation Evidence Pack | **Not done** | Dissertation not written |
| D13 — Demo Freeze Package | **Not done** | Git tag not created |

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

**Status: Partial — name matching done; place-first approach not yet implemented**

### What is already implemented (`workhouse_service.py`):
- Loads both Excel sheets ✓
- Parses forename + surname from `Pauper Name` / `Names and Surnames of Paupers` ✓
- Builds name variant index: `"forename surname"`, `"surname forename"`, `raw_name` ✓
- For each unified record, looks up name variants → exact string match on normalised name ✓
- Checks `electoral_division` vs `townland` and `parish` → sets `location_match=True/False` ✓
- Sorts results: location-confirmed matches first ✓

### What the tracking plan requires but is NOT yet implemented:

**1. Place-first filtering (currently it is name-first + location bonus)**

Current flow:
```
build name index from workhouse
→ for each estate person: look up name variants → all name matches returned
→ score by location_match as a boolean bonus
```

Required flow:
```
for each estate person: 
  filter workhouse rows to same electoral_division/townland first
  → then apply name matching only within that filtered subset
```

The difference matters: place-first dramatically reduces the candidate pool before names are compared, cutting false positives. Currently a person named "Mary Murphy" would match ALL workhouse Mary Murphys across the whole register, with location as a secondary sort only.

**2. Date-range filtering (not implemented at all)**

The workhouse records have `admitted_or_born` and `died_or_left` date fields. The tracking plan requires a ±1 year window around the estate event year. This is currently completely absent from `workhouse_service.py`.

**3. Confidence bands (not implemented)**

The plan calls for High / Medium / Low / Rejected bands based on the combination of place match + date window match + name similarity score. Currently there are only two states: `location_match=True` and `location_match=False`. No numeric similarity score is computed — the name lookup is an exact dict key match, not a scored fuzzy match.

**Work needed for D5:**
- Add date-range filtering: parse `admitted_or_born` / `died_or_left` into year integers, filter to ±1 year of the estate record's `year`
- Change match strategy: filter by place+date first, then name match within subset
- Add a similarity score to name matches (use `difflib.SequenceMatcher` or `rapidfuzz` already in the project)
- Map score + place + date combo to High/Medium/Low confidence bands
- Return confidence band in the match payload so the UI can display it

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

**Status: Not done**

Nothing for this exists in the codebase. Required:
1. A Python script that reads a sample of `unified_record` rows (5–10 townlands) and outputs a Turtle (`.ttl`) file
2. A local triplestore to query (Apache Jena Fuseki, or alternatively `rdflib` in-process which needs no JAR)
3. SPARQL query equivalents for 5 selected competency questions (Q7, Q8, Q9, Q14, Q15)
4. A comparison table: SQL query vs SPARQL query vs result vs latency

This is the most important outstanding coding deliverable for the dissertation's CS contribution. The `rdflib` approach (pure Python, no Fuseki JAR needed) is the lowest-friction path:

```python
# Minimal approach — no Fuseki, uses rdflib in-process
import rdflib
g = rdflib.Graph()
g.parse("coolattin_sample.ttl")
results = g.query("""
    SELECT ?townland (COUNT(?person) AS ?count)
    WHERE { ?person coolattin:hasTownland ?townland ;
                   coolattin:hasEmigrationRecord true . }
    GROUP BY ?townland
""")
```

`rdflib` is likely already installable (`pip install rdflib`). Fuseki requires downloading a JAR but gives a real SPARQL endpoint for a more credible comparison.

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

Only three deliverables require new code to be written. Everything else is either data collection, analysis, or dissertation writing.

### 1. D5 — Workhouse place-first + date-range + confidence bands
**File:** `backend/services/workhouse_service.py`  
**Change:** In `get_match_index()`, add:
- Parse `admitted_or_born` / `died_or_left` to extract year
- For each estate record, pre-filter workhouse rows by `electoral_division` matches `townland_norm`
- Apply date window (±1 year of `unified_record.year`)
- Score name match with `rapidfuzz.fuzz.token_sort_ratio` (already in the codebase)
- Map (place_match × date_match × name_score) → `confidence_band` ∈ {High, Medium, Low}
- Add `confidence_band` and `name_similarity_score` to the match payload

### 2. D8 — RDF/KG Comparative Prototype
**New file:** `scripts/rdf_comparison.py` (or `backend/jobs/rdf_uplift.py`)  
**New file:** `data/seed/coolattin_sample.ttl` (generated by the uplift script)  
**New file:** `scripts/sparql_comparison_queries.py` (runs SPARQL and SQL side-by-side)

### 3. D6 (minor gap) — Explicit source table list in Ask UI
**File:** `backend/services/ask_service.py`  
**Change:** In `answer_question_stream()`, after SQL execution, extract the table names from the SQL string and include them in the result payload as `source_tables: ["unified_record", "census_record"]`.

---

## What Does NOT Need Any New Code

| Item | Reason |
|---|---|
| D3 — Dataset audit | Run SQL queries against the existing DB, put numbers in a table |
| D4 — Geospatial alignment audit | Run 4 SQL queries against `townland` table, read `reconciliation_gaps.csv` |
| D6 (mostly) | SQL display, route, provenance, coverage notes all already work |
| D7 (mostly) | Chart layer is live for 7 templates; just needs screenshots taken |
| D9 — Technical evaluation | Browse the Ask page, record results in a spreadsheet |
| D10 — LLM evaluation | Run questions, inspect SSE log, record results |
| D11 — User evaluation | Recruit participants, run tasks, record observations |
| D12, D13 | Writing, git tag, screenshots |

---

## Priority Order for Remaining Work

Given demo on 13 July 2026:

| Priority | Item | Effort | Dissertation impact |
|---|---|---|---|
| 1 | D8 — RDF/KG prototype | 2–3 days coding | Highest — directly answers RQ6 and Declan's ask |
| 2 | D5 — Workhouse place-first + confidence bands | 1 day coding | High — directly answers RQ3 |
| 3 | D3, D4 — Data and alignment audits | 2 hours (SQL + write-up) | Medium — feeds methods chapter |
| 4 | D9 — Technical evaluation (15 Qs) | 2 hours running + recording | High — needed for evaluation chapter |
| 5 | D6 (minor) — Source table list in UI | 1 hour coding | Low — small improvement |
| 6 | D10 — LLM evaluation | 2 hours | Medium |
| 7 | D11 — User evaluation | 1 day with participants | Medium |
| 8 | D7 screenshots | 30 mins | Low effort, needed for appendix |
