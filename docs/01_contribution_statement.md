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

### 2.2 Multi-Stage Natural Language to SQL Pipeline

The Ask page implements a novel multi-stage query pipeline designed for the specific constraints of historical record datasets:

1. **Template-first matching** — A library of 83 verified SQL templates is scored against the user's question using weighted keyword matching. Templates cover domain-expert competency questions and return exact, deterministic answers without involving an LLM call.
2. **Townland resolution** — Place names in the question are resolved via exact match, normalised match, and fuzzy match with "did you mean?" suggestions, using the canonical townland catalogue.
3. **LLM SQL generation** — If no template matches, the pipeline sends bounded schema context, sampled categorical values, approved query memory, and the user question to an LLM (OpenRouter / Ollama fallback) to generate a read-only SQL query.
4. **Read-only SQL guardrail** — All LLM-generated SQL is validated to block any write statement before execution.
5. **LLM answer rewrite** — The raw database result is rephrased into a natural-language answer contextualised to the historical domain.
6. **VRTI SPARQL enrichment** — A parallel call to the VRTI SPARQL endpoint retrieves parish, barony, and county context for townlands mentioned in the answer, which is appended to the response.
7. **PDF report export** — A hand-written PDF 1.4 report (no library dependency) is generated for every answered question, including the SQL query, data table, and VRTI context.

Pipeline stages stream progress to the browser via Server-Sent Events (SSE), giving the user live feedback during multi-second LLM operations.

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

> This dissertation contributes (1) a reproducible data warehouse architecture integrating heterogeneous Irish historical archival sources with the VRTI Knowledge Graph; (2) a multi-stage NL→SQL pipeline with template-first matching, LLM fallback, read-only guardrail, and VRTI SPARQL enrichment, evaluated against a 15-question domain-expert competency set; and (3) the first publicly accessible integrated computational interface for the Coolattin Estate records, enabling historical and genealogical research that was previously inaccessible without bespoke programming.
