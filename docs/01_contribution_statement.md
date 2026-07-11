# Dissertation Contribution Statement

**Project:** Coolattin Estate Records Explorer  
**Candidate:** Pranjal Yadav  
**Programme:** MSc Computer Science (Interactive Digital Media / Data Science)  
**Institution:** Trinity College Dublin  
**Supervisors:** Dr Ciarán Wallace, Prof Declan O'Sullivan  
**Submission:** First week of August 2026

---

## 1. Research Problem

Nineteenth-century Irish estate records — tenant rentals, assisted emigration lists, eviction ledgers, and population censuses — exist across multiple archival formats and institutions. These sources have never been computationally integrated into a single queryable system that a historian or descendant researcher can interrogate in natural language without knowledge of SQL, SPARQL, or any technical query language. The result is that rich comparative analyses (for example: did emigration rates correlate with population decline across townlands? which ships carried the most families to Canada? were widows disproportionately evicted?) remain either inaccessible to non-technical researchers or require bespoke programming effort for each question.

This dissertation addresses that gap for one of the largest and best-documented assisted-emigration estates in nineteenth-century Ireland: the Coolattin Estate, County Wicklow.

---

## 2. Computer Science Contribution

### 2.1 Hybrid Data Warehouse Architecture for Heterogeneous Historical Sources

The core technical contribution is the design and implementation of a reproducible data integration pipeline that unifies five structurally dissimilar source types into a single relational serving layer:

| Source | Format | Role |
|---|---|---|
| Coolattin estate tenant/emigration/eviction ledgers | CSV / Excel | Primary records |
| VRTI Knowledge Graph | RDF via SPARQL endpoint | Townland metadata, census data, geographic identifiers |
| Coolattin estate boundary and survey data | GeoJSON | Spatial alignment |
| National Monuments Service (NMS) open data | CSV / GeoJSON | Heritage landscape features |
| Townlands.ie reference | JSON API | Canonical place-name resolution |

The integration pipeline (implemented in `backend/jobs/` and `backend/integrations/`) applies fuzzy place-name normalisation, canonical townland resolution, and derived-field inference (widow identification, family key construction, Canada destination detection, family size estimation) to produce a unified SQLite database with five tables: `townland`, `census_record`, `clearances_record`, `unified_record`, and `heritage_feature`. This approach is explicitly a **data warehouse pattern**: sources are uplifted once into the serving layer, and all runtime queries run against the local database. This is appropriate given that the source data is historically static.

### 2.2 Orchestrated Natural Language to SQL/SPARQL Pipeline

The Ask page implements a seven-phase orchestrated pipeline with intent-first routing, designed for the specific constraints of historical record datasets:

1. **Entity resolution (Phase 1)** — Place names are resolved to canonical townland IDs and KG URIs using exact match, normalised match, and fuzzy match (rapidfuzz, threshold 80). Person references are disambiguated via a three-layer Mention/Person/Factoid identity model (Jaro-Winkler + Metaphone phonetic blocking + geographic/temporal co-occurrence).
2. **Four fast lanes** — Before intent routing, four short-circuit paths are checked in priority order: (a) rule-based slot-fill with confidence ≥ 0.80 (0 LLM calls); (b) verified SQL template match; (c) approved memory reuse (token_sort_ratio + cosine ≥ 0.55); (d) TF-IDF + RRF embedding retrieval (cosine ≥ 0.68). Any hit bypasses the remaining pipeline.
3. **Intent classification (Phase 5)** — `classify_intent()` assigns questions to ANALYTICAL, RELATIONAL, COMPARATIVE, or FALLBACK using keyword-priority rules. Core Rule 1 prevents heritage/sensemaking keywords from mis-routing count queries to the graph path.
4. **Semantic layer — ANALYTICAL lane (Phase 2)** — A 14-metric registry maps analytical questions to a `SlotFill` struct (metric + dimensions + filters + confidence). Rule-based fill (confidence ≥ 0.80) or LLM slot-fill (confidence ≥ 0.70) feeds a deterministic SQL compiler — never free-form LLM SQL. The same SlotFill also compiles equivalent SPARQL for GraphDB comparison (RQ6).
5. **Subgraph engine — RELATIONAL lane (Phase 3)** — Multi-hop VRTI SPARQL (place hierarchy, siblings, external links) + GraphDB k=2 neighbourhood expansion supplies qualitative context for geography/heritage questions. Counts always come from SQL.
6. **Read-only SQL guardrail** — FORBIDDEN_SQL regex blocks all write operations (INSERT/UPDATE/DELETE/DROP/…); verified before every execution.
7. **Multi-source synthesis (Phases 6–7)** — SQL, VRTI, and GraphDB results are fused with discrepancy detection; the LLM rewrites aggregated data into historically-contextualised prose with provenance annotation. PDF export (hand-written PDF 1.4, no library) generated per query.

All pipeline stages stream SSE progress events to the browser. The `query_provenance.strategy` field in the result reports which path fired: `rule_fill | verified_analysis | slot_fill_llm | template | memory | llm_sql`.

### 2.3 Pluggable Analytics Module Architecture

A registry-based analytics module system (`analytics/`) allows independent analytical views (emigrations, evictions, tenancies, townland geography, workhouse, unified overview) to be added without modifying the routing or rendering layer. Each module implements a typed protocol (`AnalyticsModule`) returning KPIs and chart data, which the frontend renders dynamically.

### 2.4 Evaluation Against a Domain-Expert Competency Question Set

The dissertation provides a systematic evaluation of the pipeline against 15 competency questions specified by Dr Ciarán Wallace (VRTI Programme Director). This constitutes a domain-expert acceptance test suite — a methodologically rigorous way to evaluate an NL-to-database system for a specific historical corpus, analogous to established NL-to-SQL benchmark practices (Spider, WikiSQL) but applied to a real archival domain.

---

## 3. Digital Humanities Contribution

### 3.1 First Integrated Computational Interface for the Coolattin Estate Records

The Coolattin Estate records have been digitised and partially published as archival documents, but no prior system has integrated tenancy, emigration, eviction, census, and heritage landscape data into a single searchable interface. This dissertation produces a publicly deployable web application that makes these records accessible to:

- Genealogical researchers tracing Irish ancestors
- Historians studying the mid-nineteenth century Irish Famine and its aftermath
- Digital humanities researchers interested in estate records as linked data

### 3.2 Spatial and Heritage Landscape Integration

By aligning the estate records with NMS heritage feature data (holy wells, ring forts, earthworks) and VRTI geographic identifiers, the system enables a class of questions that crosses the boundary between social history and landscape history: whether settlement patterns associated with specific monument types correlate with demographic trends across the Famine period.

### 3.3 Reproducible Archival Research Infrastructure

All data sources, ingestion scripts, schema definitions, and query templates are version-controlled and documented. The system is designed so that a researcher can reproduce the full database from source data in a single command, which meets emerging standards for reproducibility in computational humanities research.

---

## 4. Positioning Relative to Prior Work

| System | Coverage | Query interface | Integration depth |
|---|---|---|---|
| VRTI Knowledge Graph | All-Ireland townlands and census | SPARQL (technical users) | KG-native |
| Landed Estates Database (NUI Galway) | Multiple Irish estates | Free-text search only | Single source |
| Griffith's Valuation Online | Valuation records only | Name search | Single source |
| **This dissertation** | Coolattin (tenancy + emigration + eviction + census + heritage) | Natural language | Multi-source integration |

The key distinction from existing systems is the combination of multi-source integration, natural-language querying, and domain-expert validated competency answers in a single reproducible application.

---

## 5. Summary Statement (for supervisor communication)

> This dissertation contributes (1) a reproducible data warehouse architecture integrating heterogeneous Irish historical archival sources with the VRTI Knowledge Graph; (2) a seven-phase orchestrated NL→SQL/SPARQL pipeline with intent-first routing, a 14-metric semantic layer, subgraph-engine KG traversal, multi-source fusion, and read-only SQL guardrail, evaluated against a 15-question domain-expert competency set; (3) a place-first workhouse entity resolution pipeline using phonetic blocking, 7-signal scored matching (60-point scale), and persisted confidence bands; and (4) the first publicly accessible integrated computational interface for the Coolattin Estate records, enabling historical and genealogical research that was previously inaccessible without bespoke programming.
