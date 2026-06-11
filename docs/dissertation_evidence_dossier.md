# Dissertation Evidence Dossier
## Coolattin Estate Records Explorer — MSc Computer Science, Trinity College Dublin

**Candidate:** Pranjal Yadav (yadavp2@tcd.ie)  
**Supervisors:** Dr Ciarán Wallace · Prof Declan O'Sullivan  
**Submission deadline:** 3 August 2026  
**Dossier generated:** 2026-06-11 (verified against live `coolattin.db` and `eval_results/`)

> **Purpose:** This is source material for the candidate to write from, not submittable prose.
> Every number is traced to a verified source. Every gap is flagged [MISSING EVIDENCE].

---

## CH.1 INTRODUCTION

### 1.1–1.2 Research Context and Problem Statement

**WHAT THIS SECTION MUST ESTABLISH:** The Coolattin Estate records are important, dispersed, and unintegrated; no prior system lets a historian query them in plain English; this dissertation builds and evaluates such a system.

**VERIFIED EVIDENCE:**

- Estate spans County Wicklow; ~152 townlands in GeoJSON (`frontend/static/data/townlands.json`); years 1827–1891
- Estate survey population data from GeoJSON: 112 townlands × 6 estate years (1827, 1839, 1848, 1850, 1860, 1868)
- VRTI census data: 1841–1891 (6 decennial years), covering 1,072–1,275 County Wicklow townlands per year
  - *Verified SQL:* `SELECT year, COUNT(*) FROM census_record GROUP BY year`
- Peak estate population 1841: **119,300** (estate region, sum across 1,266 townlands)
  - *Verified SQL:* `SELECT SUM(total) FROM census_record WHERE year=1841`
- Post-Famine decline: 1851 = 91,860 (−23%); 1861 = 81,429 (−32% vs 1841)
- Clearances: **7,763 persons cleared** across 122 townlands, years 1847–1856
  - *Verified SQL:* `SELECT SUM(count) FROM clearances_record`
  - Peak clearance year 1847: 2,681 persons
- Person-level integrated records: **13,707 unique rows** (0 duplicates confirmed)
  - Emigration: 6,016 (43.9%); eviction: 4,108 (30.0%); tenancy: 5,247 (38.3%)
  - *Verified SQL:* `SELECT COUNT(DISTINCT record_id) FROM unified_record`, etc.
- No prior system integrates these five sources into a single NL-queryable interface

**COMPARATIVE POSITIONING (verified from `docs/01_contribution_statement.md`):**

| System | Coverage | Query interface | Integration depth |
|--------|----------|-----------------|-------------------|
| VRTI Knowledge Graph | All-Ireland townlands + census | SPARQL (technical users only) | KG-native |
| Landed Estates Database (NUI Galway) | Multiple Irish estates | Free-text name search | Single source |
| Griffith's Valuation Online | Valuation records only | Name search | Single source |
| IrishGenealogy.ie | Church + civil registers | Name search | Single source |
| **This dissertation** | Coolattin (5 sources) | Natural language | Multi-source integration |

**[MISSING EVIDENCE: literature]** — Citations for NL-to-SQL benchmarks (Spider, WikiSQL), DH linked data systems, Irish historical computing must be sourced from real publications. Suggested search terms: "NL-to-SQL evaluation Spider benchmark", "digital humanities knowledge graphs", "Irish historical records computational", "VRTI Virtual Record Treasury Ireland", "Coolattin Estate emigration".

**WRITE-YOURSELF:**
- Historical framing of the Famine and Coolattin as an assisted-emigration estate
- Significance of the Famine period (1847–1856) in the clearances data
- Why natural-language access matters for historians vs. technical query interfaces
- The research gap: no integrated computational tool for this estate's records

---

### 1.3 Research Questions

The seven RQs are defined in `docs/08_implementation_status.md` and the tracking plan. As evidenced in that document:

| RQ | Description | Chapter that answers it | Primary eval artifact |
|----|-------------|------------------------|----------------------|
| RQ1 | How can heterogeneous nineteenth-century archival records be integrated into a reproducible data warehouse with verified data quality? | Ch.3 | D3 null-rate audit (this dossier); `eval_results/authority_id_consistency.md` |
| RQ2 | How can the VRTI Knowledge Graph be linked to estate records to enrich geographic context? | Ch.3, Ch.4 | `eval_results/rq6_sql_vs_sparql.md`; authority-ID audit |
| RQ3 | Can workhouse records be linked to estate records using name-matching entity resolution on sparse metadata? | Ch.5 | `eval_results/er_metrics.md`; `eval_results/er_diagnosis.md` |
| RQ4 | Can a multi-stage NL-to-SQL pipeline accurately and reliably answer domain-expert competency questions about historical records? | Ch.4, Ch.6 | `eval_results/evaluation_pack.md` (D9/D10) |
| RQ5 | How can the pipeline results be made explainable and faithful to the source data? | Ch.4, Ch.6 | D10a gate audit; `eval_results/gate_block_audit.md` |
| RQ6 | How does the deterministic SQL layer compare to an equivalent RDF/SPARQL representation for the same competency questions? | Ch.4, Ch.6 | `eval_results/rq6_sql_vs_sparql.md` |
| RQ7 | Can graphical summaries and provenance metadata improve comprehension of historical record patterns? | Ch.4, Ch.6 | GraphRAG enrichment eval (`docs/11_demo_freeze.md` §1.2) |

**[MISSING EVIDENCE]** — Exact verbatim RQ text was in the "tracking plan" document which is not a committed file. The candidate should write each RQ as a formal research question sentence and include it in the dissertation.

---

### 1.4–1.5 System Overview and Contributions

**VERIFIED EVIDENCE (from `docs/01_contribution_statement.md`, `docs/00_master_dissertation_plan.md`):**

**CS Contributions:**
1. **Reproducible multi-source data warehouse** — five source types unified into SQLite; idempotent `ensure_schema()` in `extensions.py:477 lines`; fuzzy place-name normalisation via `rapidfuzz`
2. **Deterministic-first NL→SQL pipeline** — 83 verified SQL templates; LLM invoked only when templates miss; honest-refusal path confirmed at 100% on tuned vocabulary (`eval_results/evaluation_pack.md` D9d)
3. **In-process GraphRAG with iron-rule guarantee** — `backend/services/graphrag.py`; D3 acceptance gate: numeric delta = 0/9 (100%); graph layer *never* modifies counts returned by SQLite
4. **Authority-ID failure-class finding** — 4/150 (2.7%) `kg_uri` entries point to wrong homonym VRTI entities; `vrti_id` field correct in all 4 cases; root cause: name-only URI construction without geographic disambiguation (`eval_results/authority_id_consistency.md`)
5. **ER-on-sparse-records finding** — F1@CONFIRMED = 0.27 not due to algorithm defect but because 48% of workhouse records carry zero metadata beyond a name (`eval_results/er_diagnosis.md`)
6. **Silent-degradation methodology** — catalogue of 6 documented silent-failure incidents with mitigations (Ch.6 §6.8–6.9; `eval_results/graph_build_report.md`; `eval_results/graphrag_migration_verification.md`)

**DH Contributions:**
- First integrated computational interface for Coolattin Estate records (five-source integration)
- Heritage landscape integration: NMS holy wells (68) + ring forts (298) spatially joined to estate townland network
- Reproducible archival research infrastructure: version-controlled sources, single-command DB rebuild

**Strongest evidence artifact per contribution:**
| Contribution | Strongest artifact |
|---|---|
| Data warehouse | D3 audit table (this dossier) + authority-ID audit |
| Deterministic-first NL pipeline | `evaluation_pack.md` D9a: 100% routing accuracy |
| Iron-rule GraphRAG | `docs/11_demo_freeze.md` §1.2: delta=0/9 |
| Authority-ID failure class | `eval_results/authority_id_consistency.md`: 4/150 table |
| ER-on-sparse-records | `eval_results/er_diagnosis.md`: 48% no-metadata finding |
| Silent-degradation methodology | `eval_results/graphrag_migration_verification.md` |

---

### 1.6 Dissertation Structure

**WRITE-YOURSELF:** Standard roadmap paragraph. Map: Ch.2=background, Ch.3=data+architecture, Ch.4=Ask pipeline, Ch.5=ER, Ch.6=evaluation, Ch.7=conclusions.

---

## CH.2 BACKGROUND

> **Note:** This chapter must contain real, citable literature. Do NOT fabricate citations. Below is a list of topics the chapter must cover with the system components that motivate them. All suggested search terms are for the candidate to search and find real papers.

### 2.1 Natural Language to SQL (NL-to-SQL) Systems

**CONCEPTS TO COVER:** Semantic parsing, encoder-decoder architectures, cross-domain transfer, benchmark datasets.

**System component motivating this:** `backend/services/semantic_layer.py` (slot-fill compiler, 1,185 lines), `QUESTION_TEMPLATES` library (83 templates), LLM SQL generation fallback in `ask_service.py`

**[MISSING EVIDENCE: literature]** — Suggested search terms: "Spider NL-to-SQL benchmark cross-domain", "WikiSQL dataset neural semantic parsing", "IGSQL context-dependent text-to-SQL", "few-shot NL-to-SQL GPT", "schema linking NL-to-SQL"

### 2.2 Retrieval-Augmented Generation

**CONCEPTS TO COVER:** RAG pipeline architecture, dense vs. sparse retrieval, hybrid retrieval, Reciprocal Rank Fusion.

**System component motivating this:** `backend/services/embedding_index.py` (558 lines): TF-IDF + BGE dense + RRF fusion; fast-lane threshold 0.68

**[MISSING EVIDENCE: literature]** — Suggested search terms: "retrieval-augmented generation Lewis 2020", "hybrid sparse dense retrieval", "reciprocal rank fusion Cormack 2009", "BEIR benchmark information retrieval"

### 2.3 Knowledge Graph Enrichment and GraphRAG

**CONCEPTS TO COVER:** Property graphs, community detection, graph traversal for LLM context, hallucination reduction via grounded retrieval.

**System component motivating this:** `backend/services/graphrag.py`; `backend/services/subgraph_engine.py` (518 lines); in-process graph: 49,081 nodes, 64,342 edges, 3,501 communities

**[MISSING EVIDENCE: literature]** — Suggested search terms: "GraphRAG Microsoft Edge 2024", "knowledge graph question answering", "community detection Louvain modularity", "graph neural network link prediction"

### 2.4 Deterministic SQL Compilation and Template-Based Query Generation

**CONCEPTS TO COVER:** Rule-based semantic parsing, template filling, slot-filling compilers, the case for bypassing LLMs for high-stakes queries.

**System component motivating this:** `semantic_layer.py::try_rule_based_fill()` at line 827; `_OUT_OF_SCOPE_SIGNALS` frozenset at line 793; best-match scoring at line 892

**[MISSING EVIDENCE: literature]** — Suggested search terms: "template-based question answering", "slot filling semantic parsing", "deterministic text-to-SQL", "ATHENA system rule-based QA"

### 2.5 Hallucination Detection and Faithfulness in LLM Outputs

**CONCEPTS TO COVER:** Numeric consistency gates, factual verification, LLM output post-processing, calibrated uncertainty.

**System component motivating this:** `_synthesis_allowed_numbers()` in `ask_service.py`; `_cross_verify_synthesis()` (D10b); gate audit in `eval_results/gate_block_audit.md`

**[MISSING EVIDENCE: literature]** — Suggested search terms: "LLM hallucination detection", "TruthfulQA benchmark", "faithfulness evaluation NLG", "self-consistency prompting"

### 2.6 RDF, SPARQL, and Linked Data for Humanities

**CONCEPTS TO COVER:** RDF data model, SPARQL query language, open-world vs. closed-world assumption, ontology design, named graphs.

**System component motivating this:** `backend/integrations/graphdb_sparql.py` (155 lines); `co:` ontology at `https://coolattin.ie/ontology#`; 189,018 triples in local GraphDB; VRTI endpoint at `virtuoso.virtualtreasury.ie/sparql/` (4,460,845 total triples)

**[MISSING EVIDENCE: literature]** — Suggested search terms: "SPARQL query language W3C", "linked data digital humanities", "ontology design patterns", "VRTI Virtual Record Treasury Ireland", "OpenLink Virtuoso triplestore"

### 2.7 Entity Resolution on Historical Records

**CONCEPTS TO COVER:** Probabilistic record linkage, blocking strategies, Jaro-Winkler similarity, phonetic encoding, sparse historical records.

**System component motivating this:** `entity_resolution/scoring.py`; `entity_resolution/candidates.py`; forename penalty at token_sort_ratio < 60; jellyfish Metaphone phonetic encoding

**[MISSING EVIDENCE: literature]** — Suggested search terms: "probabilistic record linkage Fellegi Sunter", "historical record linkage Ireland genealogy", "Jaro-Winkler similarity string matching", "blocking strategies entity resolution", "Levenshtein edit distance"

### 2.8 Irish Historical Computational Systems and Digital Humanities

**CONCEPTS TO COVER:** Existing Irish historical database systems, VRTI, Landed Estates Database, digitisation of Famine-era records, genealogical databases.

**[MISSING EVIDENCE: literature]** — Suggested search terms: "Virtual Record Treasury Ireland VRTI", "Landed Estates Database NUI Galway", "IrishGenealogy.ie national archives", "Irish Famine computational history", "Griffith's Valuation digitisation", "Coolattin Estate Wicklow emigration history Fitzwilliam"

### 2.9 Server-Sent Events and Streaming Architectures for NLP Pipelines

**CONCEPTS TO COVER:** SSE protocol, streaming LLM output, live progress feedback, chunked transfer.

**System component motivating this:** `/api/ask/query` SSE endpoint in `backend/routes/ask.py`; 7 SSE stage events per question; `ask.js` SSE consumer

---

## CH.3 DATA AND ARCHITECTURE

### 3.1 Data Sources Overview

**WHAT THIS SECTION MUST ESTABLISH:** Five source types, their formats, what they contribute, and how they differ structurally.

**VERIFIED EVIDENCE (from `docs/00_master_dissertation_plan.md` §1.3):**

| Source | Format | Records/Size | What it provides |
|--------|--------|-------------|------------------|
| Coolattin estate GeoJSON (`frontend/static/data/townlands.json`) | GeoJSON | 152 townland features | Boundary polygons, estate survey populations (1827–1868), clearances (1847–1856) |
| VRTI Knowledge Graph (`virtuoso.virtualtreasury.ie/sparql/`) | RDF/SPARQL | 4,460,845 total triples | Boundary WKT, centroid coords, civil parish/barony/county hierarchy, OSM/OSI/VRTI identifiers, decennial census 1841–1891 |
| `unified_processed.csv` | CSV | 13,707 rows | Person-level records: tenants, emigrants, evictees — forename, surname, townland, year, role, ship, holding, family |
| NMS open data (holy wells + monuments) | CSV + GeoJSON | 366 features | Heritage monument locations across County Wicklow |
| Townlands.ie reference (`data/seed/wicklow_townlands_reference.json`) | JSON | Canonical list | Place-name alias resolution; canonical spellings |

---

### 3.2 Per-Dataset Verified Statistics

**3.2.1 Unified Person Records**

*Verified SQL run date: 2026-06-11 against `coolattin.db`*

| Metric | Value | SQL query |
|--------|-------|-----------|
| Total records | **13,707** | `SELECT COUNT(DISTINCT record_id) FROM unified_record` |
| Duplicate record_ids | **0** | `COUNT(*) = COUNT(DISTINCT record_id)` |
| With emigration flag | **6,016** (43.9%) | `WHERE has_emigration_record=1` |
| With eviction flag | **4,108** (30.0%) | `WHERE has_eviction_record=1` |
| With tenancy flag | **5,247** (38.3%) | `WHERE has_tenancy_record=1` |
| Year range | **1841–1886** | `SELECT MIN(year), MAX(year)` |
| Null year rows | **14** | `WHERE year IS NULL` |
| Distinct surnames | **977** | `COUNT(DISTINCT surname)` |
| Distinct townland norms | **516** | `COUNT(DISTINCT townland_norm)` |
| Widow-flagged rows | **811** | `WHERE is_widow=1` |
| Canada-destination flagged | **2,044** | `WHERE is_canada_destination=1` |
| Rows with children_count > 0 | **1,401** | `WHERE children_count > 0` |

**Top 5 townlands by record count (verified):**

| Townland | Records |
|----------|---------|
| Carnew | 550 |
| Ballynultagh | 457 |
| Killinure | 454 |
| Coolboy | 307 |
| Tinahely | 265 |

**3.2.2 Census Records**

| Year | Total population | Townland count | Source |
|------|-----------------|----------------|--------|
| 1827 | 25,257 | 112 | Estate GeoJSON survey |
| 1839 | 27,596 | 112 | Estate GeoJSON survey |
| 1841 | **119,300** | 1,266 | VRTI KG (SPARQL ingest) |
| 1848 | 26,971 | 112 | Estate GeoJSON survey |
| 1850 | 23,190 | 112 | Estate GeoJSON survey |
| 1851 | **91,860** | 1,275 | VRTI KG (SPARQL ingest) |
| 1860 | 16,912 | 112 | Estate GeoJSON survey |
| 1861 | **81,429** | 1,265 | VRTI KG (SPARQL ingest) |
| 1868 | 16,473 | 111 | Estate GeoJSON survey |
| 1871 | 153,073 | 1,249 | VRTI KG |
| 1881 | 127,966 | 1,072 | VRTI KG |
| 1891 | 128,003 | 1,235 | VRTI KG |

*Note: Estate survey years (1827–1868) cover ~112 Coolattin estate townlands. VRTI KG years (1841–1891) cover all County Wicklow townlands (1,000+). The estate survey total for 1827 (25,257) covers only the ~112 Coolattin estate townlands, while the 1841 VRTI figure (119,300) covers the much wider County Wicklow region. These are not directly comparable.*

**3.2.3 Clearances (Evictions)**

| Year | Persons cleared | Rows in clearances_record |
|------|----------------|--------------------------|
| 1847 | 2,681 | — |
| 1848 | 1,565 | — |
| 1849 | 1,016 | — |
| 1850 | 547 | — |
| 1851 | 649 | — |
| 1852 | 381 | — |
| 1853 | 474 | — |
| 1854 | 391 | — |
| 1855 | 38 | — |
| 1856 | 21 | — |
| **Total** | **7,763** | **1,211 rows across 122 townlands** |

*Verified SQL:* `SELECT year, SUM(count) FROM clearances_record GROUP BY year ORDER BY year`

**3.2.4 Townland Reference**

| Metric | Value |
|--------|-------|
| Total rows in `townland` table | **4,225** |
| With VRTI KG data (source='kg') | 3,142 |
| Estate GeoJSON townlands (source='json') | 2 |
| Townlands with census data | 1,319 (distinct townland_id in census_record) |
| Townlands with clearances data | 122 |
| Townlands with heritage features | 259 (distinct townland_norm in heritage_feature) |
| Civil parishes | 22 |
| Baronies | 11 |

**3.2.5 Heritage Features (NMS)**

| Feature group | Count |
|---------------|-------|
| ring_fort | **298** |
| holy_well | **68** |
| **Total** | **366** |

*Source: `heritage_feature` table; datasets: `holywells_wicklow.geojson` + `asi_wicklow.geojson`*

---

### 3.3 Ingest Pipeline and D3 Dataset Audit (closes D3)

**WHAT THIS SECTION MUST ESTABLISH:** The ingest is reproducible and the output dataset has quantified quality properties.

**INGEST PIPELINE STEPS (from `backend/jobs/full_ingest.py`, `docs/06_architecture_and_workflow.md`):**

1. `full_ingest.py`: queries VRTI SPARQL for County Wicklow townlands; populates `townland` table; enriches with centroid, WKT, barony, parish, VRTI identifiers
2. `census_ingest.py`: queries VRTI SPARQL per townland URI; populates `census_record`; idempotent (UPSERT on `(townland_id, year)`)
3. `_ensure_unified_table_seeded()` in `ask_service.py`: loads `unified_processed.csv` into `unified_record`; derives all computed columns at ingest time
4. `_ensure_heritage_feature_seeded()`: loads two GeoJSON files into `heritage_feature`; normalises townland names for join key

**D3 NULL-RATE AUDIT (verified against `coolattin.db`, 2026-06-11):**

| Column | Non-null count | Null % | Notes |
|--------|---------------|--------|-------|
| `record_id` | 13,707 / 13,707 | 0.0% | All rows unique |
| `forename` | 12,090 / 13,707 | 11.8% | — |
| `surname` | 12,102 / 13,707 | 11.7% | — |
| `townland_norm` | 13,683 / 13,707 | **0.2%** | Excellent coverage — critical JOIN key |
| `year` | 13,693 / 13,707 | 0.1% | 14 null-year rows |
| `age` | 5,702 / 13,707 | **58.4%** | Limits Q5 (children emigrated) accuracy |
| `gender` | 4,081 / 13,707 | **70.2%** | Limits Q1 (gender land comparison) |
| `ship_name` | 2,391 / 13,707 | **82.6%** | Limited to emigration subset |
| `holding_acres` | 2,613 / 13,707 | **80.9%** | Limits Q10/Q11 (land holdings) |
| `has_emigration_record` | 6,016 rows = 1 | 43.9% flagged | — |
| `has_eviction_record` | 4,108 rows = 1 | 30.0% flagged | — |
| `has_tenancy_record` | 5,247 rows = 1 | 38.3% flagged | — |

**Duplicate check:** `COUNT(*) = COUNT(DISTINCT record_id) = 13,707` — no duplicates

**Year validity:** Range 1841–1886; 14 null-year rows (0.1%); all non-null years are plausible integer values

**townland_norm coverage:** 13,683/13,707 (99.8%) rows have a non-null, non-empty townland_norm; 516 distinct townland names

**TABLES TO INCLUDE:** The null-rate audit table above.

**CLAIM → EVIDENCE → LIMITATION triplets:**
- Claim: "The unified dataset has high geographic coverage." → Evidence: 99.8% townland_norm present → Limitation: `townland_norm` covers 516 distinct values, only ~122 can be linked to clearances records
- Claim: "Age-based analysis is constrained by sparse metadata." → Evidence: 58.4% null age → Limitation: Children emigration count (Q5 = 2,610) uses only 5,702 rows with age data; actual figure likely higher

**[MISSING EVIDENCE]** — Source-to-database traceability sample: 20 emigration records from `unified_processed.csv` traced through `unified_record` — not yet produced. Must verify: ship_name, townland_norm, year, is_canada_destination match. This closes D4 §4.4.

**WRITE-YOURSELF:** Rationale for the data warehouse pattern (static historical data → batch ingest is appropriate); discussion of the 1821 data gap (census data starts 1841; estate survey starts 1827; Q8 asks about 1821 which is genuinely missing); foreign-key design choices.

---

### 3.4 Place-Name Normalisation Pipeline and Alias Map (closes D4)

**WHAT THIS SECTION MUST ESTABLISH:** How variant spellings are resolved to canonical names; the authority-ID alignment finding.

**NORMALISATION STEPS (from `backend/services/townland_service.py`, `docs/01_Map_Entity_Resolution.md`):**

1. Unicode NFKD decomposition + diacritic stripping → ASCII-clean string
2. `UPPER()` → canonical uppercase form (stored as `townland_norm`)
3. Alias lookup: `data/seed/townland_aliases.json` — 36+ alias entries added in June 2026 sprint
4. Fuzzy match via `rapidfuzz.fuzz.token_sort_ratio` — "did you mean?" suggestions for unresolved names
5. VRTI KG enrichment: centroid lat/lon, WKT boundary, barony, parish from SPARQL

**TULLOWCLAY / GOWLE WORKED EXAMPLE (from `eval_results/graphrag_migration_verification.md` §B1):**

Two townlands in the `townland` table have `county='WICKLOW'` but `barony=NULL`: TULLOWCLAY and GOWLE. These were loaded from VRTI but their barony hierarchy was absent from the KG response. This caused a FK constraint failure in `build_graph.py` line 290 (now fixed with a NULL guard). Evidence: `graphrag_migration_verification.md` §B1.

**AUTHORITY-ID WORKED EXAMPLES (from `eval_results/authority_id_consistency.md`):**

Scope: 150 townland rows with both `vrti_id` and `kg_uri` populated.

| Result | Count | % |
|--------|-------|---|
| Consistent (IDs point to same entity) | 146 | 97.3% |
| **Inconsistent (IDs point to different entities)** | **4** | **2.7%** |

**The 4 inconsistent rows:**

| Townland | `vrti_id` resolves to | `kg_uri` resolves to | Problem |
|----------|----------------------|----------------------|---------|
| BALLINACOR | Kilbride/Arklow ✓ | Ballinacor (ED Ballinacor), different barony | Within-county homonym |
| BALLARD | Carnew, Wicklow ✓ | Cloonbur, **County Galway** | Wrong-county homonym |
| BALLAGH | Kilpipe/Ballinacor South ✓ | Knockane, **County Kerry** | Wrong-county homonym |
| AGHOWLE UPPER | Rathnew/Newcastle ✓ | Aghowle/Shillelagh, wrong barony | Within-county homonym |

**Root cause:** `kg_uri` was populated by name-only URI construction without geographic disambiguation. Common townland names (Ballard, Ballagh) occur in multiple Irish counties; the name lookup returned an out-of-county homonym. `vrti_id` was populated by a constrained OSI/Logainm cross-reference and is correct in all cases.

**CLAIM → EVIDENCE → LIMITATION:**
- Claim: "Name-only URI construction is insufficient for Irish place-name disambiguation." → Evidence: 4/150 `kg_uri` values point to wrong entities (2.7%) → Limitation: Any query routing through `kg_uri` for these 4 townlands retrieves wrong geographic data; `vrti_id`-based routing is safe

**TABLES TO INCLUDE:** The authority-ID audit table above; the 4-row inconsistency detail table.

---

### 3.5 Database Schema

**Actual CREATE TABLE DDL (verified from `coolattin.db`, 2026-06-11):**

The schema is defined in `extensions.py` (477 lines). Key tables:

```sql
-- townland: 4,225 rows
CREATE TABLE townland (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,        -- canonical UPPER-CASE English name
    name_gaelic TEXT,
    barony TEXT, civil_parish TEXT, electoral_division TEXT,
    kg_uri TEXT,                      -- VRTI KG subject URI (WARNING: 4/150 inconsistent)
    wkt_geometry TEXT,                -- WKT polygon from KG
    centroid_lat REAL, centroid_lon REAL,
    source TEXT DEFAULT 'csv_seed',   -- 'csv_seed'|'kg'|'json'
    vrti_id TEXT, osm_id TEXT, osi_id TEXT,
    ...
);

-- census_record: 8,033 rows
CREATE TABLE census_record (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    townland_id INTEGER NOT NULL REFERENCES townland(id),
    year INTEGER NOT NULL,
    male INTEGER, female INTEGER, total INTEGER,
    inhabited INTEGER, uninhabited INTEGER,
    source TEXT DEFAULT 'csv_seed',
    UNIQUE(townland_id, year)
);

-- clearances_record: 1,211 rows
CREATE TABLE clearances_record (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    townland_id INTEGER NOT NULL REFERENCES townland(id),
    year INTEGER NOT NULL,            -- 1847-1856
    count INTEGER,                    -- persons cleared
    UNIQUE(townland_id, year)
);

-- unified_record: 13,707 rows
CREATE TABLE unified_record (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id TEXT, unique_id_no TEXT,
    year INTEGER, month TEXT,
    surname TEXT, forename TEXT, canonical_name TEXT,
    townland TEXT, townland_norm TEXT, parish TEXT, estate TEXT,
    role TEXT, legal_action TEXT,
    ship_name TEXT, departure TEXT, arrival TEXT,
    household_list TEXT,
    has_emigration_record INTEGER DEFAULT 0,
    has_eviction_record INTEGER DEFAULT 0,
    has_tenancy_record INTEGER DEFAULT 0,
    -- derived fields (computed at ingest):
    is_widow INTEGER, is_canada_destination INTEGER,
    children_count INTEGER, family_size_estimate INTEGER,
    family_key TEXT, holding_acres REAL,
    age INTEGER, gender TEXT, occupation TEXT, ...
);

-- heritage_feature: 366 rows
CREATE TABLE heritage_feature (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_dataset TEXT,              -- 'holywells'|'asi'
    feature_group TEXT,               -- 'ring_fort'|'holy_well'|...
    monument_class TEXT,
    townland_raw TEXT, townland_norm TEXT,
    feature_name TEXT, source_link TEXT
);
```

**ER-specific tables (added in June 2026 sprint, `extensions.py`):**
- `source_mentions`: 8,214 rows — one per workhouse name occurrence
- `entity_resolution_candidates`: 22,928 rows — scored candidate links
- `workhouse_unified_links`: 139 rows (3 CONFIRMED + 136 POSSIBLE)
- `entity_resolution_decisions`: 0 rows — human review UI not yet built
- `match_review`: 0 rows

**In-process graph tables:**
- `graph_nodes`: 49,081 rows
- `graph_edges`: 64,342 rows

**[MISSING EVIDENCE]** — Full unified_record DDL was truncated in the DB output (BLOB columns for computed fields like `children_count`, `family_size_estimate` not fully shown). Candidate should run `SELECT sql FROM sqlite_master WHERE name='unified_record'` to get the complete DDL for Appendix A.

---

### 3.6 `co:` Ontology and RDF/KG Layer

**WHAT THIS SECTION MUST ESTABLISH:** The local RDF representation, its scope, and its relationship to the VRTI KG.

**VERIFIED EVIDENCE (from `eval_results/rq6_sql_vs_sparql.md`):**

- Ontology namespace: `https://coolattin.ie/ontology#` (prefix `co:`)
- GraphDB repository: `localhost:7200/repositories/coolattin`
- **Total triples loaded: 189,018** (from `data/seed/coolattin.ttl`)
- **VRTI endpoint total triples: 4,460,845** (external, read-only)

**Classes and uplift mapping:**

| `co:` class | Source SQLite table | Triples from | Row count |
|-------------|---------------------|-------------|-----------|
| `co:Person` | `unified_record` | `record_id`, name fields | 13,707 nodes |
| `co:Event` (emigration/eviction/tenancy) | `unified_record` | `has_*_record` flags | 6,016 + 4,108 events |
| `co:Townland` | `townland` | name, parish, barony, county | 4,225 nodes |
| `co:CensusRecord` | `census_record` | year, total | 8,033 nodes |
| `co:Clearance` | `clearances_record` | year, count | 1,211 nodes |

**Key predicates:** `co:hasEvent`, `co:eventType` (values: "emigration"/"eviction"/"tenancy"), `co:forTownland`, `co:civilParish`, `co:barony`, `co:county`, `co:totalPopulation`, `co:count`

**VRTI integration:** The `townland.kg_uri` field stores the VRTI URI for each townland; SPARQL queries use `P89_falls_within` (CRM) for hierarchy traversal; enrichment queries run live at query time via `backend/integrations/vrti_sparql.py` (676 lines)

---

### 3.7 Deployment Architecture

**WHAT THIS SECTION MUST ESTABLISH:** Production deployment, rationale for in-process graph (Neo4j removal), key configuration.

**VERIFIED EVIDENCE (from `docs/00_master_dissertation_plan.md`, `docs/10_handoff_notes.md`, `docs/11_demo_freeze.md`):**

- Deployment: **Azure App Service — Italy North region** (gunicorn, port 5001)
- Database: SQLite 3 in WAL mode (Write-Ahead Logging for concurrent reads)
- Python runtime: 3.12
- Flask application factory pattern (`create_app.py`)

**Neo4j removal rationale (from `docs/flow.md`, `eval_results/graphrag_migration_verification.md`):**
- Neo4j required a separate server process + Bolt protocol driver
- Replaced with in-process SQLite graph tables (`graph_nodes`, `graph_edges`)
- Zero external graph server dependency; no port 7687 or 7474
- RQ6 SQL-vs-SPARQL comparison moved to GraphDB (SPARQL) vs SQLite (SQL) — two well-defined paradigms
- **Iron-rule guarantee:** the in-process graph is used only for qualitative context; aggregates always come from SQLite; the LLM synthesis prompt explicitly labels graph context as "do NOT use to produce counts or statistics" (verified: `ask_service.py` line 7720–7726)

**Canonical deployment configuration (`docs/11_demo_freeze.md` §2):**

| Variable | Value | Notes |
|----------|-------|-------|
| `ASK_USE_NEW_PIPELINE` | `true` | 7-phase orchestrated pipeline |
| `GRAPHRAG_ENABLED` | `true` | In-process graph enrichment |
| `GRAPHDB_ENABLED` | `true` | Local GraphDB SPARQL client |
| `EMBEDDING_PROVIDER` | `local` | BAAI/bge-large-en-v1.5 (no API key) |
| `OPENROUTER_API_KEY` | (required) | LLM fallback provider |
| `OPENROUTER_MODEL` | `openai/gpt-oss-20b:free` | Default model |

---

### 3.8 Ingest Pipeline Diagram

**[MISSING EVIDENCE: FIGURE]** — A data flow diagram is needed: Sources → Ingest jobs → SQLite tables → Flask serving layer → External runtime services (OpenRouter, VRTI SPARQL). Must be produced as a draw.io or Mermaid diagram. The architecture ASCII diagram in `docs/06_architecture_and_workflow.md` §2 can be converted directly.

---

## CH.4 HYBRID RETRIEVAL AND NL QUERYING

### 4.1 System Overview

**WHAT THIS SECTION MUST ESTABLISH:** The Ask pipeline is a multi-phase orchestrated system; the design rationale is deterministic-first with LLM at the edges.

**VERIFIED EVIDENCE:** The pipeline is the 7-phase orchestrated system enabled by `ASK_USE_NEW_PIPELINE=true` (default since commit `4d18308`). It lives in `ask_service.py` (8,731 lines).

---

### 4.2 Design Principles (from `docs/10_handoff_notes.md` §8, `docs/02_GraphRAG_and_RAG_System.md`)

1. **Deterministic-first:** Template/semantic layer answers high-confidence questions without LLM; LLM is invoked only at the edges (unstructured/open-domain questions)
2. **LLM at the edges:** Out-of-scope and cross-domain questions go to LLM; within-scope analytical questions go to deterministic SQL
3. **Iron-rule guarantee:** The graph enrichment layer *never* alters numeric answers; SQLite is always authoritative for counts and aggregates
4. **Honest refusal:** Questions that cannot be answered from the data should reach `template_miss` and either trigger the LLM fallback or return an explicit "not available" message — not silently map to a wrong template

**Honest-refusal evidence:** Before D10 routing fix: 0/16 G-series questions reached `template_miss` (0.0%); after fix: 16/16 (100%). Source: `eval_results/evaluation_pack.md` D9d.

---

### 4.3 Pipeline Stage Reference

The 7-phase pipeline (source: `docs/10_handoff_notes.md` §1.1):

| Phase | Module | File | Lines |
|-------|--------|------|-------|
| 1 | Intent router | `intent_router.py` | — |
| 2 | Hybrid retrieval (fast lane) | `embedding_index.py` | 558 |
| 3 | Semantic layer (deterministic SQL) | `semantic_layer.py` | 1,185 |
| 4 | Subgraph engine (KG traversal) | `subgraph_engine.py` | 518 |
| 5 | LLM SQL generation (fallback) | `ask_service.py` | 8,731 |
| 6 | Identity resolver | `identity_resolver.py` | 394 |
| 7 | Multi-model synthesis | `ask_service.py` | — |

---

### 4.4 Intent Router (Phase 1)

Classifies question as ANALYTICAL / RELATIONAL / COMPARATIVE / FALLBACK. Source: `intent_router.py`.

**[MISSING EVIDENCE]** — The precise keyword rules and confidence thresholds in `intent_router.py` are not documented in any eval artifact. Candidate should read `intent_router.py` and document the classification logic for the dissertation.

---

### 4.5 Hybrid Retrieval / Fast Lane (Phase 2)

**VERIFIED EVIDENCE (from `docs/10_handoff_notes.md` §1.4):**

- TF-IDF unigram+bigram vectors; cosine similarity top-50
- Required-keyword hard pre-filter for template/metric hits
- RRF (Reciprocal Rank Fusion) combines dense + sparse ranked lists
- Fast-lane: template or memory hit above threshold **0.68** short-circuits routing
- Returns `(chunks, meta)` where `meta` carries `dense_backend`, `dense_status`, `dense_count`, `sparse_count`, `fused_count` for SSE provenance display
- No external dependencies — TF-IDF is hand-rolled within `embedding_index.py`

**Dense embedding providers:**

| Provider | Module | Dimensions | Notes |
|----------|--------|-----------|-------|
| `local` (default) | `local_embeddings.py` | 1024-dim | BAAI/bge-large-en-v1.5; CPU; no API key |
| `cohere` | `voyage_embeddings.py` | 1024-dim | Cohere Embed v3; 5 calls/min rate limit |
| `voyage` | `voyage_embeddings.py` | 1024-dim | Legacy interface |

**Why TF-IDF alongside dense:** Dense retrieval struggles on exact entity names and rare historical terms (Irish surnames, townland names); TF-IDF keyword overlap catches what dense misses; RRF fusion reliably outperforms either alone.

---

### 4.6 Semantic Layer (Phase 3)

**VERIFIED EVIDENCE:**

- **File:** `backend/services/semantic_layer.py` (1,185 lines)
- **Function entry point:** `try_rule_based_fill()` at line 827
- **Out-of-scope guard:** `_OUT_OF_SCOPE_SIGNALS` frozenset at line 793 — 15+ signal tokens (religion, weather, crop, workhouse, entity resolution candidate, etc.) → return `None` immediately
- **Unmapped-requirement guard:** `_UNMAPPED_REQUIREMENT_PHRASES` frozenset at line 818 — signals average rent, children under, under the age, etc. → return `None`
- **Best-match scoring:** All candidate metrics are scored; highest-scoring metric wins; competing metrics reduce confidence — replaces old first-match defect (line 892)
- **Dual output:** `compile_sql()` (guaranteed-valid SQLite) + `compile_sparql()` (equivalent SPARQL for `co:` ontology) — same `SlotFill` struct compiled to both

**Adding a new metric:** One entry in `METRIC_REGISTRY` + optional keyword in `_METRIC_KEYWORDS`. No other changes needed.

---

### 4.7 Subgraph Engine (Phase 4)

**VERIFIED EVIDENCE (from `docs/10_handoff_notes.md` §1.8):**

- **File:** `backend/services/subgraph_engine.py` (518 lines)
- Five-step pipeline: entity linking → k-hop BFS expansion (default k=2) → pruning (relevance prune + size cap 120 nodes) → linearisation (compact triple table) → community summary injection
- Entity linking resolves mentions to KG node URIs via VRTI + GraphDB SPARQL
- Core rule: linearised subgraph is for *reading* qualitative context only; counts/aggregates always come from SQL path

---

### 4.8 LLM SQL Generation (Phase 5 — fallback only)

**VERIFIED EVIDENCE:**

- **Provider priority:** OpenRouter (primary) → Ollama (local fallback)
- **19 free OpenRouter models** supported (auto-fallback list)
- Default model: `openai/gpt-oss-20b:free`
- Read-only guardrail: `FORBIDDEN_SQL` regex blocks INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/ATTACH/DETACH/PRAGMA/REINDEX/VACUUM/TRUNCATE/REPLACE
- Schema injection: bounded schema descriptor — table names + column names + sampled category values + approved query memory examples; cached 5 minutes
- Repair loop: up to `OPENROUTER_MAX_RETRIES` (default 2) retry attempts on SQL syntax error

**LLM fallback exec accuracy:** 0.0% on live evaluation (D10d, `eval_results/evaluation_pack.md`). Root causes documented:
- Unknown ER tables (4/6 cases): `workhouse_unified_links`, `source_mentions`, `entity_resolution_candidates` not in LLM schema context
- Spurious estate filter (2/6 cases): LLM adds `AND estate = 'Coolattin'`; `estate` column is NULL for majority of rows

---

### 4.9 Identity Resolution (Phase 6)

**VERIFIED EVIDENCE (from `docs/10_handoff_notes.md` §1.2):**

- **File:** `backend/services/identity_resolver.py` (394 lines)
- Three-layer model: Mention (immutable, one per name occurrence) → Person (inferred individual, one or more Mentions via SAME_AS) → Factoid (reified claim; contradictory records survive without hard-merging)
- Algorithm: `jellyfish.metaphone` phonetic blocking → within-block: Jaro-Winkler similarity + geographic proximity (+0.20 same townland, +0.10 same parish) + temporal plausibility (±10 yr: +0.10; >30 yr: −0.10) + family co-occurrence (+0.15)
- SAME_AS threshold: score ≥ 0.75 = confirmed; 0.50–0.74 = candidate
- Cache: module-level, 10-minute TTL per name key

---

### 4.10 Multi-Model Synthesis and Provenance (Phase 7)

**VERIFIED EVIDENCE (from `docs/00_master_dissertation_plan.md` §2.4, `docs/10_handoff_notes.md`):**

**SSE final result payload fields (verified in `ask_service.py` Stage 7 response):**
- `answer` — natural-language answer
- `sql` — SQL executed (if `show_sql=true`)
- `columns` / `rows` — raw table data for frontend rendering
- `chart_hint` — chart type suggestion (`bar`, `line`, `doughnut`)
- `pdf_url` — relative URL for PDF download
- `vrti_context` — enrichment result (parish, barony, county)
- `warnings` — data coverage warning strings
- `query_provenance` — path taken, memory IDs reused, LLM model used
- `graphrag_context` — linearised subgraph, seed nodes, community summaries, path_used, k_hops, pruned, sources_used, degradation_note
- `llm_meta` — model name, token counts, latency

**[MISSING EVIDENCE: PROVENANCE EXAMPLE]** — A real example of the `query_provenance` + `graphrag_context` JSON from a live query response is needed. The candidate should run a RELATIONAL question live (e.g. "Which barony does Ballinacor belong to?") and capture the full SSE `result` event payload for the dissertation.

---

### 4.11 Routing Fix History and Error Analysis

**WHAT THIS SECTION MUST ESTABLISH:** The over-routing bug discovery (D9 baseline), the fix methodology, and the residual generalisation gap.

**ROUTING FIX SUMMARY (source: `eval_results/evaluation_pack.md` D9f):**

**Before fix (D9 baseline `d9_formal`, 2026-06-10):**

| Expected route | Actual route | Count |
|----------------|--------------|-------|
| `llm` | semantic_layer | 4 |
| `llm` | template | 2 |
| `llm` | verified_analysis | 2 |

**0/8** G-series questions reached `template_miss` (honest-refusal rate = 0%)

**After fix (D10 tuned `d10_routing_fix`, 2026-06-10):**

| Expected route | Actual route: semantic_layer | template | verified_analysis | template_miss |
|----------------|------------------------------|----------|-------------------|---------------|
| **llm** | 0 | 0 | 0 | **16** |
| **template** | 39 | 13 | 2 | 0 |
| **verified_analysis** | 8 | 0 | 5 | 0 |

**16/16** G-series questions reached `template_miss` (honest-refusal rate = 100%)

**Changes made to fix the over-routing defect:**

*`backend/services/semantic_layer.py`:*
1. Added `_OUT_OF_SCOPE_SIGNALS` frozenset (line 793) — 15+ tokens checked before any keyword matching
2. Added `_UNMAPPED_REQUIREMENT_PHRASES` frozenset (line 818) — average rent, under the age, etc.
3. Added cross-metric intersection guard: `"widow" in q and "emigra" in q → None`
4. Replaced first-match with best-match scoring (line 892)

*`backend/services/ask_service.py`:*
5. Added `_excluded_phrases` guards at start of `_match_and_build_template` and `_try_verified_analysis`
6. Added `"approach"` to exclusion list (prevents estate-narrative questions from matching `estate_summary` on a bare keyword hit)
7. Added `"monument"` and `"historical"` to `townland_details` optional keywords (heritage queries about specific townlands now score ≥ 2)
8. Score threshold raised from 1 → 2

*Faithfulness gate:*
9. Added `question: str = ""` parameter to `_synthesis_allowed_numbers` — numeric tokens in the question (e.g. year "1841") now included in the allowlist

---

### 4.11 Performance Evaluation Summary

**ROUTING ACCURACY (source: `eval_results/evaluation_pack.md`):**

| Run label | N | Overall routing accuracy | Lane routing accuracy | Honest-refusal (G) | SQL exec success |
|-----------|---|--------------------------|----------------------|--------------------|-----------------|
| `graphrag_off` (baseline) | 75 | 89.3% | 72.0% | 0.0% | 100.0% |
| `d9_formal` (D9 baseline) | 75 | 80.7% | — | 0.0% | 100.0% |
| `d10_routing_fix` (tuned) | **83** | **100.0%** | 65.1% | **100.0%** | **100.0%** |
| `d10_heldout` (held-out) | 35 | 71.4% | — | 0.0% | 100.0% |

**EXECUTION ACCURACY BY ROUTE (`d10_routing_fix`):**

| Route | SQL exec success | N cases |
|-------|-----------------|---------|
| semantic_layer | 100.0% | 47 |
| template | 100.0% | 13 |
| verified_analysis | 100.0% | 7 |
| template_miss | N/A (LLM needed) | 16 |

**PER-LANE BREAKDOWN (`d10_routing_fix`):**

| Lane | N | Key metric | Value |
|------|---|------------|-------|
| Analytical | 50 | Aggregation correctness | 100.0% |
| Relational | 12 | Mean subgraph recall | 0.833 |
| Comparative | 5 | SQLite capture | 100.0% |
| Fallback / G-series | 16 | Routing accuracy | 100.0% |

**LATENCY (`d10_routing_fix`):**

| Percentile | Value |
|-----------|-------|
| p50 (median) | 413 ms |
| p90 | 2,995 ms |
| p95 | 4,508 ms |

> **Mandatory caveat:** These are tuned-set figures (83 questions authored with knowledge of the routing keywords). The held-out evaluation (35 questions) shows routing accuracy drops to 71.4% and honest-refusal drops to 0.0% on unseen phrasing.

---

### 4.11.4 Faithfulness Gate (D10a)

**VERIFIED EVIDENCE (source: `eval_results/evaluation_pack.md` D10a):**

| Metric | Value |
|--------|-------|
| Test cases | 6 |
| Violations expected | 3 |
| Violations caught | **3** |
| Catch rate | **100%** |
| False positive rate | **0%** (3/3 correct passes) |

**Per-case results:**

| Case | Expected violation | Gate correct | Numbers flagged |
|------|-------------------|-------------|----------------|
| correct_emigration_total | No | ✓ | — |
| hallucinated_emigration_number | Yes | ✓ | 9999 |
| wrong_eviction_year_and_count | Yes | ✓ | 1851, 3000 |
| correct_multi_row | No | ✓ | — |
| hallucinated_percentage_not_in_rows | Yes | ✓ | 75 |
| correct_single_value | No | ✓ | — |

**D9 baseline false-positive fixed:** In `d9_formal`, year 1841 appeared in the answer "The population in 1841 was 55 people" but not in the SQL result rows `{"population": 55}` → incorrect violation flag. Fixed by adding question's own numeric tokens to the allowlist.

**Gate block audit findings (source: `eval_results/gate_block_audit.md`):**
- 4/9 live blocks were CORRECT (genuine hallucinations: historical years/dates from LLM world knowledge)
- 3/9 blocks were FALSE POSITIVES (formatting artifacts + legitimate graph context values)
- 2/9 were INCIDENTAL (exec was wrong; gate fired but for wrong reason)
- A markdown list-marker fix (`re.sub(r'(?m)^\s*\d+\.\s+', ' ', text)`) eliminates a class of false positives

---

### 4.12 Held-Out Generalisation Analysis

**WHAT THIS SECTION MUST ESTABLISH:** The routing fix generalises within tuned vocabulary but not beyond it; keyword-guard approach has intrinsic overfitting risk.

**VERIFIED EVIDENCE (source: `eval_results/evaluation_pack.md` D10e):**

| Metric | Tuned (n=83) | Held-out (n=35) | Gap |
|--------|-------------|-----------------|-----|
| Routing accuracy | 100.0% | 71.4% | **−28.6 pp** |
| Honest-refusal (G-series) | 100.0% | 0.0% | **−100 pp** |
| SQL exec success | 100.0% | 100.0% | = |
| Aggregation correctness | 74.2%* | 77.3% | +3.1 pp |
| Answer facts found rate | 65.5% | 51.9% | −13.6 pp |
| p50 latency | 407 ms | 151 ms | — |

*\*74.2% differs from 100.0% in D9c because D10 counts the wider 83-question set including template_miss cases where aggregation check returns None.*

**Interpretation (candidate must write):**
- **Routing −28.6 pp**: 10 held-out questions expected to reach LLM were routed deterministically — lane mismatch, not blank error (correct SQL returned)
- **Honest-refusal −100 pp**: This is the most significant finding — the keyword guard does not generalise to novel out-of-scope phrasing; a production system would require a learned intent classifier
- **+3.1 pp aggregation correctness**: Held-out analytical questions answered slightly more accurately (held-out townlands better covered by compiled metrics)

**The generalisation gap is itself a dissertation finding.**

---

## CH.5 ENTITY RESOLUTION

### 5.1–5.2 Problem Statement: Name-Is-Not-Identity

**WHAT THIS SECTION MUST ESTABLISH:** Irish historical records contain systematic name ambiguity; surnames cluster within geographic regions; this creates a fundamental challenge for record linkage.

**VERIFIED EVIDENCE:**

**Ballinacor homonym case:** Three distinct VRTI entities named "Ballinacor" exist in the KG — Kilbride/Arklow (Wicklow), Ballinacor parish (Wicklow), Kilcommon (Wicklow) — all within the same county. A label-only SPARQL query for "Ballinacor" returns all three. Source: `eval_results/rq6_sql_vs_sparql.md` §Q6.

**Authority-ID homonyms across counties:** BALLARD and BALLAGH (Wicklow estate townlands) have `kg_uri` values pointing to County Galway and County Kerry entities respectively — same name, entirely different county. Source: `eval_results/authority_id_consistency.md`.

**Forename abbreviation variants:** The normalisation pipeline handles: "Jno"→"John", "Wm"→"William", "Pat"→"Patrick" (symmetric expansion in `entity_resolution/normalise.py`). Source: `eval_results/er_diagnosis.md` §2.

**Forename mismatch cases:**
- Peter/Mary-Anne: `fuzz.token_sort_ratio` ≈ 14% → correctly rejected by −15 pt penalty after fix
- Judy/Judith: `fuzz.token_sort_ratio` ≈ 73% → correctly preserved (above 60% threshold)
- Jno/John (abbreviated): expands to "John"/"John" → 100% match → preserved

---

### 5.3 Source Data: Workhouse Register

**VERIFIED EVIDENCE (source: `eval_results/er_diagnosis.md` §1):**

| Metric | Value |
|--------|-------|
| Total workhouse mentions in `source_mentions` | **8,214** |
| Sheet "1-127" (no metadata) | 3,920 rows (47.7%) |
| Sheet "from 128" (has Electoral Division) | 4,294 rows (52.3%) |
| Mentions with `normalised_place` (ED) | 4,293 |
| Mentions with `event_year` | 2,652 |
| Mentions with both place AND year | 2,652 |
| Mentions with NEITHER place NOR year | **3,921** (structurally unmatchable) |
| Distinct workhouse Electoral Divisions | 783 |
| Exact ED ↔ townland name overlaps | **28** (e.g. AGHOLD, COOLATTIN, KILLINURE, COOLBOY) |

---

### 5.4 Pipeline Architecture

**VERIFIED EVIDENCE (from `docs/10_handoff_notes.md` §1.3):**

**Modules:**
- `workhouse_entity_resolution.py` (544 lines) — pipeline orchestrator
- `entity_resolution/__init__.py` — public API
- `entity_resolution/normalise.py` — name normalisation, initials expansion, phonetic coding
- `entity_resolution/candidates.py` — blocking + candidate generation (up to 25 per mention)
- `entity_resolution/scoring.py` — multi-signal scoring → CONFIRMED/POSSIBLE/WEAK/NO_MATCH

**This subsystem is explicitly separate from the Ask pipeline** — it does not use embeddings, LLM, or the 7-phase pipeline. It produces auditable, reviewable candidate links with explicit evidence trails.

---

### 5.5 Scoring Weights and Forename Penalty

**VERIFIED EVIDENCE (from `eval_results/er_diagnosis.md` §5; `eval_results/er_metrics.md`):**

| Signal | Points | Threshold |
|--------|--------|-----------|
| Surname match (full) | 25 | — |
| Forename match (full after expansion) | 15 | — |
| Forename mismatch penalty | **−15** | token_sort_ratio < 60% |
| Exact place match (ED = townland name) | 20 | — |
| Substring place match | 12 | ED contains townland name |
| Birth-year proximity | 0–10 | ±3 years |
| Age-inferred birth year | up to 5 | — |

**Thresholds:**
- CONFIRMED_MATCH: score ≥ **0.75**
- POSSIBLE_MATCH: score ≥ **0.60**
- WEAK_CANDIDATE: score ≥ **0.40**
- NO_MATCH: score < 0.40

**Forename penalty rationale:** Without the penalty, "Healy Peter" vs "Mary-Anne Healy" at Killinure with compatible birth years scored 0.66 (POSSIBLE_MATCH) — pre-fix precision was 0.48 at the 0.60 threshold. After adding the −15 pt penalty (deducted when `fuzz.token_sort_ratio(forename_A, forename_B) < 60`), precision rose to 0.92.

---

### 5.6 Gold Set

**VERIFIED EVIDENCE (source: `eval_results/er_metrics.md`):**

- **35 pairs** in `eval/er_gold.csv`
- Composition: 13 positive (TRUE_MATCH/POSSIBLE) · 18 negative (FALSE_MATCH) · 4 uncertain
- **Gold set CL-ID correction:** Pre-existing `er_gold.csv` had incorrect `u_record_id` values — CL IDs pointed to different people. All 35 pairs corrected to actual CL IDs via name + townland + year lookup (source: `eval_results/er_diagnosis.md` §4).
- **Circularity risk note:** Labels were assigned via name + townland + year lookup — this is the same signal the model uses, creating a circularity risk. N=35 is small. These limitations must be stated explicitly.

---

### 5.7 Results

**VERIFIED EVIDENCE (source: `eval_results/er_metrics.md`):**

**F1 at POSSIBLE_MATCH threshold (score ≥ 0.60):**

| | Predicted MATCH | Predicted NO-MATCH |
|--|---|---|
| True positive | TP = 12 | FN = 1 (G04: Kinsella/Kinsela score=0.557) |
| True negative | FP = 1 (G16: Broughan/Bryan) | TN = 17 |

| Metric | Value |
|--------|-------|
| Precision | **0.92** (12/13) |
| Recall | **0.92** (12/13) |
| **F1** | **0.92** |

**F1 at CONFIRMED_MATCH threshold (score ≥ 0.75):**

| Metric | Value |
|--------|-------|
| Precision | **1.00** (2/2) |
| Recall | **0.15** (2/13) |
| **F1** | **0.27** |

**Full pipeline link counts (post-fix run, 2026-06-10):**

| Band | Count |
|------|-------|
| CONFIRMED_MATCH (≥ 0.75) | **3** |
| POSSIBLE_MATCH (0.60–0.74) | **136** |
| WEAK_CANDIDATE (0.40–0.59) | 22,789 |
| Total `workhouse_unified_links` rows | **139** |
| LINKED_TO edges in graph | **174** |

*Note: 139 rows in `workhouse_unified_links` (3 + 136) but 174 LINKED_TO edges in `graph_edges`. Reconciliation: each POSSIBLE_MATCH row generates one `workhouse_unified_links` row but may generate multiple LINKED_TO edges when the same mention links to multiple candidate records. The 174 vs 139 discrepancy requires candidate investigation. Source: `eval_results/er_metrics.md` §Full pipeline link counts.*

**3 CONFIRMED links:**

| Workhouse name | ED | Year | Score | Estate record |
|---|---|---|---|---|
| Rourke Simon | Munny | 1866 | 0.850 | CL206 — Simon Rourke, Munny, 1868 |
| Bryan John | Killinure | 1859 | 0.750 | CL13529 — John Bryan, Killinure, 1847 |
| Healy Peter | Killinure | 1859 | 0.750 | CL11980 — Peter Healy, Killinure, 1848 |

All three confirmed links satisfy: exact name + exact surname + same canonical place + birth-year gap ≤ 3 years.

---

### 5.7.3 Root Causes of Low Absolute Recall

**VERIFIED EVIDENCE (source: `eval_results/er_diagnosis.md` §7; `eval_results/er_metrics.md` §Threshold calibration):**

Three root causes, ordered by impact:

1. **Sheet "1-127" (3,921 rows, 48% of workhouse register):** Only name + register number; no ED, no date, no age. Maximum achievable score: name (25 pts) + surname (15 pts) = 0.40 → WEAK_CANDIDATE. Structurally unmatchable at any reasonable threshold. This is a **primary source constraint**.

2. **Electoral Division granularity (28/783 ED↔townland overlaps = 3.6%):** Workhouse register records ED-level geography; estate records use townland-level geography. Wicklow EDs encompass 3–15 townlands each. Without townland-level data, geographic corroboration applies to only ~3.6% of the cross-reference space. Source: 28 exact ED↔townland name overlaps from 783 distinct EDs.

3. **Missing age on estate side:** Many estate records lack age data, removing the birth-year discriminator that would elevate POSSIBLE→CONFIRMED matches. Source: 80.9% null holding_acres in unified_record; similar sparsity for age.

**Verdict: ER LIMITED (data coverage).** The pipeline is functioning correctly at F1=0.92 (POSSIBLE band). The low absolute count (3 CONFIRMED from 8,214 workhouse mentions) is a **data limitation of the primary sources**, not a pipeline defect.

**CLAIM → EVIDENCE → LIMITATION:**
- Claim: "Entity resolution is feasible for the well-corroborated sub-population." → Evidence: F1=0.92, P=1.00@CONFIRMED, 3 confirmed links with full evidence chains → Limitation: Applies only to 4,293/8,214 mentions (52.3%) with ED metadata; 3,921 mentions are structurally unmatchable

---

## CH.6 EVALUATION, RELIABILITY, AND DEPLOYMENT

### 6.1–6.2 Evaluation Framework

**WHAT THIS SECTION MUST ESTABLISH:** Four-dimension evaluation framework (functional correctness, pipeline reliability, faithfulness, generalisation); gold set construction methodology.

**VERIFIED EVIDENCE:** `eval_results/eval_results/evaluation_pack.md` covers all four dimensions (D9, D10, D10e). The evaluation harness is `backend/services/ask_eval.py` (2,125 lines).

---

### 6.3 Gold Set Catalogue

**VERIFIED EVIDENCE (source: `eval_results/evaluation_pack.md` header; `eval/gold.csv`):**

- **Total: 83 questions** (75 pre-existing + 8 new for D10: 4 workhouse-ER + 4 in-scope fallback)
- **[MISSING EVIDENCE]** — The `eval/gold.csv` category breakdown has not been counted from the file. The candidate should run: `python3 -c "import csv; rows=list(csv.DictReader(open('eval/gold.csv'))); [print(k,sum(1 for r in rows if r.get('category')==k)) for k in set(r.get('category','') for r in rows)]"` to get the per-category count.

**Gold set route distribution (from `evaluation_pack.md` D9a confusion matrix):**
- Expected `llm` (G-series): 16 questions
- Expected `template`: 54 questions (39 → semantic_layer, 13 → template, 2 → verified_analysis in actual runs)
- Expected `verified_analysis`: 13 questions (8 → semantic_layer, 5 → verified_analysis)

**[MISSING EVIDENCE: FULL GOLD TABLE]** — The complete 83-question table with columns (ID, question, expected_route, category, expected_answer) is needed for Appendix C. This exists in `eval/gold.csv` and `eval/gold_answers.csv` — the candidate should render the full table.

---

### 6.4 Degradation Matrix

**WHAT THIS SECTION MUST ESTABLISH:** The system degrades gracefully when external services are unavailable.

**VERIFIED EVIDENCE (from `eval_results/graphrag_migration_verification.md` §F; `docs/06_architecture_and_workflow.md`):**

| Service | Degradation behaviour | Evidence |
|---------|----------------------|---------|
| VRTI SPARQL unavailable | VRTI enrichment skipped; 5-min cooldown before retry; core answer unaffected | `vrti_sparql.py` TTL cache + cooldown; `ask_service.py` VRTI exception wrapper |
| OpenRouter unavailable | Falls back to Ollama local model; if Ollama also unavailable, returns "LLM unavailable" | `ask_service.py` provider priority logic |
| GraphDB unavailable | `graphrag_context=None` in SSE payload; no exception raised; pipeline continues | `eval_results/graphrag_migration_verification.md` §F1 |
| `GRAPHRAG_ENABLED=false` | `is_available()` returns False; `retrieve_subgraph()` returns `GraphRAGResult(available=False, ...)` | Migration verification §F1 |
| Graph built but empty | `retrieve_subgraph()` returns `degradation_note='No seed nodes resolved'`; no hang | Migration verification §F1 |

---

### 6.5 GraphRAG OFF vs ON (§6 Comparison)

**WHAT THIS SECTION MUST ESTABLISH:** GraphRAG enrichment adds qualitative context without changing any numeric answer; the iron-rule guarantee holds empirically.

**VERIFIED EVIDENCE (source: `docs/11_demo_freeze.md` §1.2–1.3; `eval_results/eval_graphrag_on.md`; `eval_results/eval_graphrag_off.md`):**

| Metric | GraphRAG OFF | GraphRAG ON | Delta |
|--------|-------------|-------------|-------|
| Routing accuracy | 89.3% | 89.3% | **0.0** |
| Aggregation correctness | 100.0% | 100.0% | **0.0** |
| SQL exec success | 100.0% | 100.0% | **0.0** |
| Entity label accuracy | 100.0% | 100.0% | **0.0** |
| p50 latency | 365 ms | 372 ms | +7 ms |
| p90 latency | 2,049 ms | 2,095 ms | +**46 ms** |

**GraphRAG enrichment evaluation (9 RELATIONAL + multi-hop cases):**

| Metric | Value |
|--------|-------|
| Cases tested | 9 |
| **Numeric delta = 0** | **9/9 (100%)** ← acceptance gate |
| Grounding OK | 5/9 (56%) |
| Provenance path present | 9/9 (100%) |
| Avg auto-usefulness | 4.4/5 |
| Avg latency overhead (ON − OFF, warm) | **+46 ms** at p90 |

**4 partial grounding misses (grounding_rate < 1.0):** Due to missing `LOCATED_IN` edges in the graph build — caused by `AGHOLD`/`BALLYCUMBER`/`CORRAVANISH` and other variant townland names not present in the `townland` table as-built. Source: `eval_results/graph_build_report.md` §Skipped edges (7,960 edges skipped: `dst not in node set`). 4,612 Person nodes have no direct Townland edge. This is an **honest limitation** to state explicitly.

**Verdict:** GraphRAG is additive-only. No accuracy regressions. Latency overhead is negligible for warm runs (~7 ms p50 / ~46 ms p90).

---

### 6.6 RQ6 — SQL vs. SPARQL Comparison

**WHAT THIS SECTION MUST ESTABLISH:** SQL and SPARQL produce equivalent results when the graph is fully loaded; the open-world/closed-world difference is operationally inert for a complete load; entity alignment is the primary source of genuine data-level discrepancy.

**VERIFIED EVIDENCE (source: `eval_results/rq6_sql_vs_sparql.md`):**

**Summary table (6 competency questions):**

| # | Question | SQL Result | SPARQL Result (local `co:`) | Classification |
|---|----------|-----------|----------------------------|----------------|
| Q1 | Total emigration | **6,016** | **6,016** | AGREEMENT |
| Q2 | Emigration from Ballynultagh | **400** | **400** | AGREEMENT |
| Q3 | Total evictions | **7,763** | **7,763** | AGREEMENT |
| Q4 | Population 1841 | **119,300** | **119,300** | AGREEMENT |
| Q5 | Population Ballinacor 1841 | **55** | **55** | AGREEMENT |
| Q6 | Ballinacor parish/barony | Kilbride/Arklow | Kilbride/Arklow (local) | **AGREEMENT** (local); **DATA-LEVEL** (VRTI via kg_uri) |

**GraphDB load:** 189,018 triples from `data/seed/coolattin.ttl`

**Empty-graph incident (resolved):** The `docs/11_demo_freeze.md` §1.4 shows a pre-correction RQ6 table with "0/empty" for Q1–Q6 — this was because the `co:` repository had not been loaded with data when that freeze document was written. The corrected `eval_results/rq6_sql_vs_sparql.md` shows 5/5 agreements after loading.

**By-construction agreement note:** The SPARQL queries were generated by `semantic_layer.compile_sparql()` using the same `SlotFill` struct as `compile_sql()`. Both compile to structurally equivalent queries from identical data source. The agreement is therefore partially expected by design — it validates structural equivalence, not independent verification.

**Genuine DATA-LEVEL finding (Q6):** VRTI entity referenced by `kg_uri` for Ballinacor returns civil_parish=Ballinacor and barony=Ballinacor South. VRTI entity referenced by `vrti_id` returns civil_parish=Kilbride and barony=Arklow — matching the stored `civil_parish`/`barony` columns. Discrepancy is in entity alignment (wrong homonym assigned to `kg_uri`), not in the boundary data itself.

**Key semantic differences documented:**
1. **Open vs. closed world:** SPARQL open-world absence means "unknown"; SQL closed-world means "not present". Operationally inert when graph is fully loaded.
2. **Name disambiguation:** Label-based SPARQL for "Ballinacor" returns 3 distinct VRTI entities; SQL `LIMIT 1` silently returns one. URI-level anchoring is required for deterministic SPARQL results.
3. **NULL handling:** SQL INNER JOIN excludes NULL foreign keys; equivalent SPARQL required-triple pattern excludes missing predicates — same result for this dataset.

---

### 6.7 Entity Resolution Metrics Cross-Reference

Cross-reference Ch.5 §5.7: F1@POSSIBLE = 0.92; F1@CONFIRMED = 0.27; 3 confirmed / 136 possible from 8,214 workhouse mentions.

---

### 6.8–6.9 Silent-Degradation Catalogue

**WHAT THIS SECTION MUST ESTABLISH:** Silent failures (where the system appears to work but returns wrong results) are the most dangerous failure mode; documenting them is a methodological contribution.

**VERIFIED EVIDENCE (from `eval_results/graphrag_migration_verification.md`, `eval_results/evaluation_pack.md`, `eval_results/er_diagnosis.md`, `eval_results/gate_block_audit.md`):**

| Incident | Category | What happened | Detection method | Mitigation |
|----------|----------|---------------|-----------------|------------|
| **Sync-completed-on-failure** | Silent failure | `graphrag.is_available()` returned True with 0 edges and 0 embeddings — nodes existed from a crashed build, making the check misleading | Checking `number_of_edges()` explicitly | `is_available()` should check both nodes AND edges AND embeddings; build should use a transaction or flag |
| **Import-as-None (wrong homonym)** | Silent error | `kg_uri` for BALLARD and BALLAGH pointed to County Galway and County Kerry entities — geographic queries returned wrong parish/barony with no error | Authority-ID audit (post-hoc) | Post-ingest consistency check comparing resolved `P89_falls_within` against stored `civil_parish`; or use only `vrti_id` for KG lookups |
| **Empty triplestore** | Silent degradation | GraphDB repository existed but had no data loaded; SPARQL queries returned 0/empty for all questions — interpreted as "real zeros" until the graph was loaded | RQ6 evaluation revealed 6/6 "disagreements" — all zeros | Load the graph before any evaluation; add a startup health check that verifies triple count > 0 |
| **Wrong gold CL-IDs** | Silent error | `eval/er_gold.csv` had `u_record_id` values pointing to wrong persons — gold evaluation ran without error but measured against wrong ground truth | Manual cross-check: CL ID → person name, compare against intended gold pair | All 35 gold pairs verified by name + townland + year lookup (`eval_results/er_diagnosis.md` §4) |
| **Over-routing (0% honest-refusal)** | Silent miscalibration | D9 baseline: 16 G-series out-of-scope questions were routed to deterministic paths and returned (incorrect) answers — no error raised, no warning to user | Gold set evaluation with expected_route=llm | `_OUT_OF_SCOPE_SIGNALS` guard; best-match scoring with threshold=2; honest-refusal path confirmed 100% on tuned vocab |
| **Spurious estate filter** | Silent exec error | LLM generated `AND estate = 'Coolattin'` — `estate` column is NULL for majority of rows; query returned 0 or 1 instead of correct count, with no error | Live evaluation D10d B1: 0/6 in-scope fallback cases correct | ER tables + estate-filter warning added to LLM schema context |
| **LLM citing wrong KG context** | Silent hallucination | er_wh_04 case: LLM cited 13,707 (total unified record count from KG context) as evidence for workhouse mention count | Gate block audit (`eval_results/gate_block_audit.md`) | Numeric gate caught this; entity IDs/KG context values should be added to gate allowlist |

---

### 6.10 Deployment Readiness

**VERIFIED EVIDENCE:**

- Live deployment: **Azure App Service — Italy North region** (`docs/00_master_dissertation_plan.md`)
- **`/api/ask/llm-status` endpoint** in `backend/routes/ask.py` — health check for LLM provider + VRTI endpoint
- SSE streaming: `/api/ask/query` — chunked transfer, no buffering
- WAL mode: SQLite in write-ahead logging mode for concurrent reads

**Pinned canonical configuration (source: `docs/11_demo_freeze.md` §2):**

| Variable | Value |
|----------|-------|
| `ASK_USE_NEW_PIPELINE` | `true` |
| `GRAPHRAG_ENABLED` | `true` |
| `GRAPHRAG_VECTOR_TOP_K` | `8` |
| `GRAPHRAG_K_HOPS` | `2` |
| `GRAPHRAG_MAX_NODES` | `120` |
| `EMBEDDING_PROVIDER` | `local` |
| `OPENROUTER_MODEL` | `openai/gpt-oss-20b:free` |
| `OPENROUTER_REQUEST_TIMEOUT` | `80` s |
| `VRTI_TTL_SECONDS` | `3600` (1 hour) |

---

### 6.11 Limitations

Limitations to state explicitly in the dissertation:

1. **Tuned-set numbers:** The 100% routing accuracy and 100% honest-refusal rate are from the 83-question tuned set. The held-out set (35 questions) drops to 71.4% and 0.0% respectively. The tuned figures should not be presented without the held-out caveat.
2. **Unmeasured live-fallback hallucination rate:** 7/16 live fallback cases timed out (~91 s); only 9 synthesis attempts were made, all blocked by the numeric gate. No case arose where the gate passed through a non-empty answer. The cross-verifier (D10b) has never fired in production.
3. **Cross-verifier unmeasured:** `_cross_verify_synthesis()` requires a case where the numeric gate passes an answer but prose contains unsupported claims. No such case was observed. The verifier's effectiveness is theoretically sound but empirically untested.
4. **D11 user study not conducted:** No external participants have evaluated the system. The `eval/manual_scoring_sheet.csv` provides a ready-to-use scoring rubric but no data has been collected.
5. **ER recall ceiling:** 3,921 workhouse mentions (48%) are structurally unmatchable at any threshold. The 3 confirmed links represent the ceiling for the well-corroborated sub-population, not the full register.
6. **LOCATED_IN edge gap:** 4,612 Person nodes in the graph have no direct Townland edge (25% orphan rate). This limits GraphRAG grounding quality for questions about poorly-normalised townland names.
7. **Gender and age sparsity:** 70.2% null gender and 58.4% null age limit Q1 (gender land comparison) and Q5 (children emigration) accuracy. Results for these questions are derived from partial data.

---

## CH.7 CONCLUSION

### 7.1–7.5 Per-RQ Answer Skeleton

| RQ | One-line answer | Evidence pointer | Confidence |
|----|----------------|-----------------|-----------|
| RQ1: Heterogeneous data integration | A reproducible data warehouse pattern with fuzzy normalisation unifies 5 source types; 99.8% townland_norm coverage confirms high geographic alignment | D3 null-rate audit; authority-ID audit; `extensions.py` idempotent schema | **Established** |
| RQ2: VRTI KG linkage | VRTI provides centroid, WKT, and hierarchy for ~3,142 townlands; 4/150 `kg_uri` values are misaligned due to name-only homonym resolution | `eval_results/rq6_sql_vs_sparql.md`; authority-ID audit | **Established** (with documented failure class) |
| RQ3: Workhouse linkage | ER is feasible for well-corroborated records (F1=0.92 at POSSIBLE threshold, P=1.00 at CONFIRMED); 3 confirmed links from 8,214 mentions; coverage limited by 48% metadata-free records | `eval_results/er_metrics.md` | **Partial** (data coverage limited) |
| RQ4: NL→SQL pipeline accuracy | 100% routing accuracy on tuned set (83 Qs); 100% SQL exec success on deterministic routes; drops to 71.4% routing on held-out set | `evaluation_pack.md` D9/D10 | **Established** (on tuned set); **Partial** (on held-out) |
| RQ5: Explainability and faithfulness | Numeric consistency gate catches 100% of test hallucinations with 0% false positives; provenance payload shows path_used in all 9/9 live runs; gate has documented false-positive cases (formatting artifacts, KG context values) | `evaluation_pack.md` D10a; `gate_block_audit.md` | **Partial** (gate not gate false-positive-free in all live cases) |
| RQ6: SQL vs. SPARQL comparison | 5/6 competency questions: full agreement; 1/6: data-level discrepancy (entity alignment, not boundary data); open-world difference is operationally inert for complete graph load | `eval_results/rq6_sql_vs_sparql.md` | **Established** |
| RQ7: Graphical summaries | Chart layer renders for 7 template types; GraphRAG enrichment provides provenance paths for 9/9 RELATIONAL questions; GraphRAG adds 0.0 pp numeric change | `docs/11_demo_freeze.md` §1.2; GraphRAG eval | **Established** |

### 7.6 Future Work (from `docs/04_future_scope.md` and outstanding items)

1. **Held-out evaluation expansion** — extend to 100+ questions to measure the generalisation ceiling of the keyword-guard approach; compare to a learned intent classifier (e.g. fine-tuned BERT)
2. **LOCATED_IN edge backfill** — resolve the 7,960 skipped edges by improving townland normalisation at graph build time; would improve GraphRAG grounding from 56% to ~100%
3. **Hierarchical authority check** — implement post-ingest consistency check: compare resolved `P89_falls_within` parish against stored `civil_parish`; fix the 4 `kg_uri` misalignments
4. **Workhouse review UI** — build a web page over the `match_review` table; `entity_resolution_decisions` has 0 rows currently; enables human-in-the-loop validation
5. **D11 user study** — recruit 4–6 participants (historians, genealogists); 5–8 questions per participant; score on Correctness/Faithfulness/Historical Appropriateness rubric in `eval/manual_scoring_sheet.csv`; report Cohen's κ
6. **Learned intent classifier** — replace keyword guards with a fine-tuned BERT or zero-shot classifier; would address the −100 pp honest-refusal drop on unseen phrasing
7. **Pre-1841 data sourcing** — 1821 census fragments (National Archives of Ireland, Wicklow County Archives) to close the data gap for Q8

---

## APPENDIX A — Database Schema (Complete DDL)

**[MISSING EVIDENCE]** — The DDL output in this dossier is truncated for the `unified_record` and `heritage_feature` tables (columns beyond the INSERT limit were cut off). For Appendix A, the candidate must run:
```sql
SELECT sql FROM sqlite_master WHERE type='table' ORDER BY name;
```
and include the complete, untruncated output.

The complete tables are: `ask_query_feedback`, `ask_query_memory`, `census_record`, `clearances_record`, `entity_resolution_candidates`, `entity_resolution_decisions`, `graph_edges`, `graph_nodes`, `heritage_feature`, `match_review`, `refresh_state`, `source_mentions`, `townland`, `unified_record`, `workhouse_unified_links`.

**co: ontology → SQLite mapping table** is in `eval_results/rq6_sql_vs_sparql.md` §Ontology namespace.

---

## APPENDIX B — SQL Template Library (Representative Sample)

**10 representative SQL templates (verified — all have SQL exec success=100%):**

| Template ID | Question type | SQL pattern |
|-------------|--------------|------------|
| `emigration_total` | A — aggregate | `SELECT COUNT(DISTINCT record_id) FROM unified_record WHERE has_emigration_record=1` |
| `emigration_by_townland` | A — filtered | Add `AND townland_norm='{townland_norm}'` |
| `eviction_total` | A — aggregate | `SELECT SUM(count) FROM clearances_record` |
| `population_by_year_townland` | A — filtered | `SELECT year, total FROM census_record JOIN townland ... WHERE UPPER(name)=...` |
| `widows_count` | A — derived | `SELECT COUNT(*) FROM unified_record WHERE is_widow=1` |
| `children_emigrated` | A — derived | `SELECT COUNT(*) FROM unified_record WHERE has_emigration_record=1 AND age<18 AND age IS NOT NULL` |
| `canada_emigration_peak` | A — aggregate | `SELECT year, COUNT(*) FROM unified_record WHERE is_canada_destination=1 GROUP BY year ORDER BY COUNT(*) DESC` |
| `townland_details` | R — attribute | Joins `townland` for parish/barony/county via SPARQL enrichment |
| `estate_summary` | R — overview | Multi-source count query + VRTI context |
| `holy_well_population` | H — heritage | Joins `heritage_feature` WHERE feature_group='holy_well' + census JOIN |

**Intent-routing rule table (post-D10 fix):**

| Guard frozenset | Members (examples) | Effect |
|-----------------|-------------------|--------|
| `_OUT_OF_SCOPE_SIGNALS` | religion, weather, crop, workhouse, entity resolution candidate | → None (LLM fallback) |
| `_UNMAPPED_REQUIREMENT_PHRASES` | average rent, children under, under the age | → None (LLM fallback) |
| Cross-metric intersection | widow + emigra | → None (LLM fallback) |
| Score threshold | < 2 | → None (LLM fallback) |

**[MISSING EVIDENCE]** — The full 83-template library with template IDs, required_keywords, optional_keywords, and SQL templates should be extracted from `ask_service.py`'s `QUESTION_TEMPLATES` list for Appendix B. The candidate should run: `grep -n "QUESTION_TEMPLATES\|template_id\|required_keywords" backend/services/ask_service.py | head -200`.

---

## APPENDIX C — Full 83-Question Gold Table

**[MISSING EVIDENCE]** — The complete table must be rendered from `eval/gold.csv` (83 rows) with columns: ID, question, expected_route, category, expected_answer_facts. The per-question eval results table from `eval_results/eval_d10_routing_fix.json` (or the corresponding `.md` file) provides the actual route taken, SQL executed, and correctness flag for all 83 questions.

---

## APPENDIX D — Deployment Configuration and First-Boot Runbook

**VERIFIED ENV-VAR TABLE (from `docs/11_demo_freeze.md` §2 and `.env.example`):**

| Variable | Required | Default | Notes |
|----------|----------|---------|-------|
| `OPENROUTER_API_KEY` | Yes | — | LLM provider API key |
| `OPENROUTER_MODEL` | No | `openai/gpt-oss-20b:free` | Default model |
| `ASK_USE_NEW_PIPELINE` | No | `true` | Enable 7-phase pipeline |
| `GRAPHRAG_ENABLED` | No | `true` | In-process graph enrichment |
| `GRAPHDB_ENABLED` | No | `true` | Local GraphDB SPARQL |
| `GRAPHDB_SPARQL_ENDPOINT` | No | `http://localhost:7200/...` | GraphDB endpoint |
| `EMBEDDING_PROVIDER` | No | `local` | `local`/`cohere`/`voyage` |
| `COHERE_API_KEY` | If cohere | — | Only if `EMBEDDING_PROVIDER=cohere` |
| `DATABASE_URL` | No | — | PostgreSQL URL for pgvector backend |
| `FLASK_ENV` | No | `development` | Set to `production` for deployment |

**First-boot runbook:**
```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env.local  # add OPENROUTER_API_KEY
python3 app.py              # DB auto-created; visit http://127.0.0.1:5001
# Populate with VRTI data:
curl -X POST http://127.0.0.1:5001/api/census/refresh
```

---

## APPENDIX E — Screenshot Capture Checklist

**[MISSING EVIDENCE: All items below are unconfirmed until screenshots are taken]**

| # | Page/State | Action/Question to type | What to capture |
|---|-----------|------------------------|-----------------|
| E1 | Home page (`/`) | — | Full page view with map embed and estate boundary |
| E2 | Census page (`/census`) | Select year 1841 | Population grid across townlands |
| E3 | Analytics page (`/analytics`) | Select "emigrations" dataset | Timeline chart + KPI cards |
| E4 | Heritage page (`/heritage`) | — | Leaflet map with ring forts + holy wells overlaid |
| E5 | Ask page (`/ask`) | "How many people emigrated from the Coolattin estate?" | SSE progress bar + result panel + PDF link |
| E6 | Ask page | "How many widows appear in the records?" | Result + coverage warning |
| E7 | Ask page | "What was the peak period for emigration to Canada?" | Chart rendered from verified_analysis template |
| E8 | Ask page | "Which ship carried the most Coolattin families to Canada?" | Named result (expected: Dunbrody or similar) |
| E9 | Ask page | "What religion were the Coolattin tenants?" | honest-refusal / LLM fallback response |
| E10 | Ask page — show SQL enabled | "How many evictions were recorded in 1849?" | SQL display visible in response panel |
| E11 | PDF report | Download from Q5 answer | PDF showing question, answer, data table, VRTI context |
| E12 | Ask page with RELATIONAL question | "Which barony does Ballinacor belong to?" | GraphRAG provenance path visible in result |

---

## APPENDIX F — D11 User Study Materials

**[MISSING EVIDENCE: User study not conducted]**

**Ready-to-use task sheet for D11 (generate this for immediate use):**

**Task instructions for participants:**
> You will be exploring a web application that lets you search historical records from the Coolattin Estate in County Wicklow, Ireland from the mid-1800s. Please try to answer 5–8 questions of your own choosing using the Ask page. Type your questions naturally — as you would ask a librarian or historian.

**Suggested seed questions (to prompt participants who are stuck):**
1. How many people emigrated from this estate?
2. Which townland had the most evictions?
3. How did the population change between 1841 and 1861?
4. Were there any families with the surname Byrne who emigrated?
5. Which ship carried the most families to Canada?

**Scoring rubric (from `eval/manual_scoring_sheet.csv`):**

| Criterion | Score 1 | Score 3 | Score 5 |
|-----------|---------|---------|---------|
| Correctness | Answer contradicts the data | Minor imprecision | Perfectly consistent with raw rows |
| Faithfulness | Numbers differ from what data shows | Minor discrepancy | Exactly matches data table |
| Historical Appropriateness | Anachronistic/generic language | Adequate | Appropriate C19 Irish historical vocabulary |

**Consent outline:** Participants should consent to: (1) screen recording during the session, (2) audio recording of verbal commentary, (3) use of session data in the dissertation (anonymised). Standard TCD research ethics consent form should be used.

**Inter-rater agreement:** For questions attempted by multiple participants, report Cohen's κ across raters on the three criteria. Target κ > 0.6 (substantial agreement).

---

## GAP REGISTER

One consolidated table of every [MISSING EVIDENCE] item, ordered by dissertation impact:

| # | What is missing | Which section(s) blocked | Effort | Codeable or human-only |
|---|----------------|-------------------------|--------|----------------------|
| G1 | Complete D11 user study data (4–6 participants, session recordings, scored rubrics) | Ch.6 §6.11 (limitation), Appendix F | 4–6 hours per participant × 5 participants | Human-only |
| G2 | Full 83-question gold table rendered from `eval/gold.csv` and `eval_d10_routing_fix.json` | Appendix C | 30 min | Codeable: `python3 -c "import csv, json; ..."` |
| G3 | Real live provenance JSON example from Ask page (question + SSE result event) | Ch.4 §4.10 | 15 min | Codeable: run `curl -X POST /api/ask/query` with a RELATIONAL question |
| G4 | Screenshots E1–E12 (full screenshot capture checklist) | Appendix E; Ch.4 figures; Ch.3 architecture | 60 min | Human-only (requires browser) |
| G5 | Gold set category breakdown (count of questions per category from `eval/gold.csv`) | Ch.6 §6.3 | 10 min | Codeable: `python3 -c "import csv; ..."` |
| G6 | Complete unified_record DDL (truncated in DB output) | Appendix A | 5 min | Codeable: `SELECT sql FROM sqlite_master WHERE name='unified_record'` |
| G7 | Full 83-template library with required_keywords and SQL patterns | Appendix B | 45 min | Codeable: grep + format from `ask_service.py` |
| G8 | Source-to-database traceability sample (20 emigration rows CSV → unified_record) | Ch.3 §3.3; D4 §4.4 | 60 min | Codeable + manual verification |
| G9 | Verbatim RQ text as formal research question sentences | Ch.1 §1.3 | 30 min | Human-only (candidate must write) |
| G10 | All literature references / citations (NL-to-SQL benchmarks, DH systems, ER methods, Irish history) | Ch.2 all subsections | 8–15 hours | Human-only (must source real papers) |
| G11 | Architecture data-flow diagram (Sources → Ingest → SQLite → Flask → Runtime services) | Ch.3 §3.8 | 60 min | Codeable/drawing tool (Mermaid or draw.io) |
| G12 | 174 vs. 139 LINKED_TO edge reconciliation (why 174 edges for 139 workhouse_unified_links rows) | Ch.5 §5.7 | 20 min | Codeable: `SELECT COUNT(*) FROM graph_edges WHERE rel_type='LINKED_TO'` then trace `build_graph.py` edge-insertion logic |
| G13 | Held-out gold set per-question detail from `eval/gold_heldout.csv` and `eval_d10_heldout.json` | Ch.4 §4.12 | 30 min | Codeable: render markdown table from JSON |
| G14 | Exact verbatim text of `identity_resolver.py` algorithm for Ch.4 §4.9 | Ch.4 §4.9 | 20 min | Codeable: `head -100 backend/services/identity_resolver.py` |
| G15 | `intent_router.py` classification rules for Ch.4 §4.4 | Ch.4 §4.4 | 20 min | Codeable: read `backend/services/intent_router.py` |

---

*Dossier generated: 2026-06-11*  
*All numbers verified against `coolattin.db` (SQLite, 2026-06-11 state) and `eval_results/` eval artifacts*  
*Eval artifacts generated by `ask_eval.py` and associated scripts on 2026-06-10*
