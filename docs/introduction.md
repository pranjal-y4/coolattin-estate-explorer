# Chapter 1: Introduction

## 1.1 Background and Motivation

The Great Famine of 1845–1852 stands as one of the most catastrophic demographic events in modern European history. In County Wicklow, Ireland, the Coolattin Estate — a large landholding spanning 152 townlands across the baronies of Shillelagh and Ballinacor — witnessed profound social upheaval: mass emigration, systematic clearances, workhouse admissions, and wholesale displacement of tenant farming communities. The estate records produced during this period, covering the years 1827 to 1868, constitute a rare and granular primary source: annual estate surveys, emigration ledgers, eviction registers, and workhouse admission and discharge books that together document the lived experience of thousands of individuals at the intersection of famine, landlordism, and forced migration.

These records, however, exist in forms that resist easy analysis. Estate surveys enumerate tenants and acreages in tabular form. Emigration manifests name individuals against townlands and ships. Census enumerations aggregate population by decade. Workhouse books record admissions against fragmentary name and place fields. Each dataset uses inconsistent orthography for personal names and place names, records the same individuals under variant spellings across different sources, and employs administrative geographies — townland, civil parish, barony — that do not map cleanly onto one another. The result is a fragmented archive: rich in detail, but locked behind representational heterogeneity that makes integrated historical analysis extraordinarily difficult without computational assistance.

At the same time, a new class of computational tools has emerged that is directly relevant to this problem. Large Language Models (LLMs) have demonstrated the capacity to generate syntactically valid SQL from natural-language questions, reason over structured and unstructured context simultaneously, and produce coherent narrative summaries from tabular data. Semantic knowledge graphs — most notably the Virtual Record Treasury of Ireland (VRTI), which represents Irish historical records as a linked-data knowledge base using the CIDOC-CRM ontology — now expose machine-readable structured representations of precisely the kind of archival material held in the Coolattin collection. Hybrid retrieval architectures that combine term-based indexing with dense vector representations have been shown to outperform either approach alone on domain-specific information retrieval tasks.

The convergence of a historically significant, computationally tractable archival corpus with a new generation of AI-assisted query tools motivates this dissertation. If these techniques can be integrated effectively — linking heterogeneous datasets at the record level, grounding natural-language questions in verified SQL, and enriching answers with knowledge graph context — they offer the possibility of making Famine-era estate records accessible not only to specialist historians but to a far broader audience of researchers, genealogists, and members of the Irish diaspora.

## 1.2 Problem Statement

Despite the growing availability of digitised historical archives and the rapid advancement of natural-language processing technology, a significant gap persists between what archival data contains and what an ordinary user can extract from it without expert assistance. This gap has three distinct dimensions.

**Data heterogeneity.** The Coolattin Estate records are distributed across at least five dataset types — unified estate person records (13,707 rows), standard decennial census records (1841–1891), estate survey populations (1827, 1839, 1848, 1850, 1860, 1868), clearances registers (1847–1856), and workhouse admission records — each maintained in a different format, at a different granularity, with different geographic identifiers. Integrating these sources requires entity resolution at both the place level (152 townland names with variant spellings) and the person level (repeated names with ambiguous identity across sources). Naive string matching fails in the face of phonetic variants such as "Ballinacur" and "BALLINACOR", or individuals recorded as "Patt Murphy" in an estate survey and "Patrick Murphy" in a workhouse ledger.

**Query accessibility.** Structured data of this richness is conventionally queried through SQL or SPARQL, both of which require significant technical expertise to use correctly. Existing interfaces to digitised Irish archives are predominantly keyword search or faceted browse: powerful for known-item retrieval, but inadequate for analytical questions such as "Which townlands had the highest ratio of emigrations to clearances during the Famine years?" or "How did the population of Ballinacor parish change between the pre-Famine estate survey and the 1851 census?". Answering such questions currently demands either specialist database skills or a research assistant with archival expertise.

**Knowledge integration.** Even a well-formed SQL query against the local estate database cannot capture the full picture. The VRTI knowledge graph holds complementary representations of census data, place hierarchies, and heritage features that are not present in the estate records. Free-form natural-language questions about the history or geographic context of a townland — "Tell me about Kilmacoo" or "What is the heritage significance of Aghowle?" — require knowledge graph traversal rather than SQL aggregation. No existing system bridges these two retrieval modalities in a unified, user-facing query interface.

The problem, stated precisely, is as follows: **How can heterogeneous Famine-era estate records be integrated, disambiguated, and made queryable through natural language in a way that produces accurate, traceable answers grounded in verifiable primary source data?**

## 1.3 Research Questions

This dissertation is organised around four research questions, each targeting a distinct technical challenge in the integration and querying of historical estate records.

**RQ1 — Record Integration:** How can heterogeneous Famine-era estate records from multiple sources be integrated into a unified data store that preserves provenance, supports analytical querying, and resolves entity ambiguity at both the place and person level?

**RQ2 — Natural-Language Query:** To what extent can a structured, multi-phase pipeline that combines slot-fill SQL compilation with LLM-generated fallback queries produce accurate, read-safe SQL answers to natural-language questions about historical demographic data?

**RQ3 — Knowledge Graph Augmentation:** How does the integration of a linked-data knowledge graph (VRTI SPARQL) with a local relational database affect the completeness and accuracy of answers to heritage, geographic, and contextual questions that cannot be answered by SQL alone?

**RQ4 — Entity Resolution:** Can a phonetic-blocking, multi-signal scoring approach to entity resolution reliably link workhouse admission records to unified estate person records across source datasets that use inconsistent name and place representations?

## 1.4 Aims and Objectives

**Aim 1:** Construct a unified, schema-consistent relational database that integrates estate records, census data, clearances registers, and workhouse admissions for the Coolattin Estate, with explicit field-level provenance tracking and cross-source entity identifiers.

- **Objective 1.1:** Design and implement a SQLite schema comprising fifteen tables, including `unified_record`, `townland`, `townland_xref`, `census_record`, `clearances_record`, `source_mentions`, and `entity_resolution_candidates`, with WAL-mode concurrency and idempotent schema migration.
- **Objective 1.2:** Implement a townland entity resolution pipeline that assigns stable UUID entity identifiers to all 152 Coolattin townlands and maps variant names and source-specific identifiers to canonical entries via a cross-reference table.
- **Objective 1.3:** Implement a full ingest pipeline (`backend/jobs/full_ingest.py`) that populates the database from two authoritative sources — a GeoJSON estate boundary file and the VRTI SPARQL endpoint — with coordinate-swap validation and graceful fallback when the knowledge graph is unavailable.

**Aim 2:** Design and implement a multi-phase natural-language query pipeline that translates user questions into verified SQL and returns accurate, readable answers with full source attribution.

- **Objective 2.1:** Implement a slot-fill compiler (`backend/services/semantic_layer.py`) with a vocabulary of thirty-plus analytical metrics, covering emigration, eviction, census, tenancy, and heritage, that compiles deterministic, read-only SQL with zero LLM calls for high-confidence matches.
- **Objective 2.2:** Implement a hybrid retrieval index (`backend/services/embedding_index.py`) using TF-IDF with reciprocal rank fusion over a library of one-hundred-plus verified SQL templates, with a cosine-threshold fast lane that bypasses LLM generation when confidence is sufficient.
- **Objective 2.3:** Implement an intent routing layer (`backend/services/intent_router.py`) that classifies questions as ANALYTICAL, RELATIONAL, COMPARATIVE, or FALLBACK and dispatches to the appropriate pipeline branch.
- **Objective 2.4:** Implement a multi-provider LLM synthesis cascade (Claude → Grok → OpenRouter → Ollama) with rate limiting, numeric hallucination detection, and Server-Sent Event streaming for real-time progress feedback.
- **Objective 2.5:** Implement a read-only SQL safety guard that rejects any generated SQL containing INSERT, UPDATE, DELETE, DROP, or ATTACH before execution.

**Aim 3:** Integrate the VRTI knowledge graph as a complementary retrieval source alongside the local relational database, supporting geographic, heritage, and contextual queries through SPARQL traversal and in-process graph analytics.

- **Objective 3.1:** Implement a subgraph engine (`backend/services/subgraph_engine.py`) that performs k-hop BFS traversal of the in-process NetworkX property graph (49,081 nodes, 64,308 edges) and linearises the result into a context block suitable for LLM synthesis.
- **Objective 3.2:** Implement VRTI SPARQL integration (`backend/integrations/vrti_sparql.py`) for townland hierarchy, census, and heritage feature retrieval, with five-minute offline cooldown to prevent cascading failures when the external endpoint is unavailable.
- **Objective 3.3:** Implement a cross-source fusion phase that detects and presents discrepancies between SQL-derived and KG-derived counts for comparative questions.

**Aim 4:** Develop and evaluate a workhouse entity resolution subsystem that links workhouse admission records to unified estate person records using phonetic blocking and multi-signal scoring.

- **Objective 4.1:** Implement a four-stage pipeline (mention normalisation → phonetic blocking → candidate scoring → persistence) in `backend/services/workhouse_entity_resolution.py` that operates without LLM involvement.
- **Objective 4.2:** Design a seven-signal scoring model incorporating name similarity (Jaro-Winkler), place similarity, temporal plausibility, gender match, occupation overlap, family size consistency, and household co-occurrence.
- **Objective 4.3:** Persist resolution outcomes in `entity_resolution_candidates` with four confidence labels (CONFIRMED_MATCH ≥ 0.85, POSSIBLE_MATCH 0.70–0.84, WEAK_CANDIDATE 0.50–0.69, NO_MATCH < 0.50) and expose results through the `/api/unified/records` endpoint.

**Aim 5:** Deliver a production-quality web application with interactive map, analytics dashboard, knowledge graph explorer, and secure API, demonstrating the practical utility of the integrated system for historical research.

- **Objective 5.1:** Implement eight distinct user-facing pages (home, census explorer, Ask Q&A, analytics, heritage map, knowledge graph explorer, historical info, about) with responsive Leaflet.js maps, Chart.js analytics, and D3.js force-graph visualisation.
- **Objective 5.2:** Implement per-IP rate limiting (30 requests/minute on the LLM endpoint), Content Security Policy headers, SPARQL injection prevention, and read-only SQL enforcement to secure the public-facing API.
- **Objective 5.3:** Implement a pluggable analytics module architecture (`analytics/base.py`, `analytics/registry.py`) supporting auto-discovery of dataset-specific KPI and chart modules at runtime.

## 1.5 Contributions

This dissertation makes the following original contributions:

**C1 — Integrated Coolattin Historical Database.** A unified SQLite database integrating five distinct historical datasets — estate person records, estate survey populations, standard census enumerations, clearances registers, and workhouse admissions — for 152 townlands of the Coolattin Estate, County Wicklow, 1827–1868. The database enforces field-level provenance through a survivorship table (`field_provenance`) and cross-source entity linkage through a cross-reference table (`townland_xref`), enabling analysts to trace every data point to its source.

**C2 — Orchestrated Multi-Phase Ask Pipeline.** A seven-phase natural-language query pipeline that sequentially applies entity resolution, fast-lane template retrieval, intent routing, deterministic SQL compilation, knowledge graph augmentation, cross-source fusion, and multi-provider LLM synthesis to answer historical demographic questions. The pipeline produces read-safe, traceable SQL with zero LLM calls for high-confidence questions (fast-lane threshold 0.68) and falls back gracefully through a four-provider cascade (Claude, Grok, OpenRouter, Ollama) when deterministic generation is insufficient.

**C3 — Slot-Fill SQL Compiler for Historical Analytics.** A domain-specific slot-fill compiler (`semantic_layer.py`) with a vocabulary of thirty-plus analytical metrics, nine dimension types, and fifteen filter predicates tailored to Famine-era estate data. The compiler produces guaranteed-valid, read-only SQLite SQL from structured slot-fill JSON without any probabilistic generation, eliminating hallucination risk for the analytical query path. The metric vocabulary covers emigration, eviction, census population change, tenancy acreage, widow proportions, and heritage feature co-occurrence.

**C4 — Hybrid Retrieval Index with Fast-Lane Short-Circuit.** A TF-IDF plus reciprocal rank fusion retrieval system over a library of one-hundred-plus verified SQL templates, coupled with a cosine-threshold fast lane that bypasses all LLM calls when a sufficiently similar verified query is found. This architecture guarantees that for well-represented question types the system operates in deterministic mode with sub-second response time, while retaining full LLM generality for novel questions.

**C5 — Workhouse Entity Resolution Subsystem.** A fully automated, LLM-free entity resolution pipeline linking workhouse admission records (`source_mentions`) to unified estate person records (`unified_record`) using phonetic blocking (Metaphone) and a seven-signal scoring model. The pipeline has produced 140 confirmed matches (score ≥ 0.85) from a workhouse dataset of approximately one thousand admissions, with all results persisted in the relational database for human review and audit.

**C6 — In-Process GraphRAG over Historical Knowledge Graph.** An in-process property graph built from the VRTI knowledge graph, instantiated in NetworkX with 49,081 nodes and 64,308 directed edges, with precomputed community embeddings (BAAI/bge-large-en-v1.5, 768 dimensions). The graph supports exact townland seeding, k-hop BFS subgraph extraction, community-summary linearisation, and place hierarchy traversal, enabling relational and contextual questions to be answered without real-time SPARQL round-trips.

**C7 — Open-Access Web Application for Famine-Era Research.** A fully deployed, publicly accessible web application providing eight interactive research interfaces: a choropleth census map, a natural-language Q&A interface with streaming progress, an analytics dashboard with pluggable dataset modules, a heritage monument map, a knowledge graph force-graph explorer, and a downloadable PDF report generator. The application is secured with per-IP rate limiting, Content Security Policy, and read-only SQL enforcement, making it suitable for open public access.

## 1.6 Scope and Assumptions

**Geographic scope.** The system is scoped to the Coolattin Estate in County Wicklow, Ireland, comprising 152 townlands within the baronies of Shillelagh and Ballinacor. While the VRTI knowledge graph covers all of Ireland and the system architecture is not inherently limited to a single estate, all data ingestion, entity resolution, and query templates are calibrated for the Coolattin dataset. Extension to other estates or counties would require re-ingestion and template calibration but no architectural changes.

**Temporal scope.** The estate records span 1827 to 1868, with the most analytically significant period being the Famine years 1847–1856, for which both clearances registers and emigration ledgers are available. Standard census records extend to 1891 via VRTI. No records after 1891 are included.

**Data completeness.** The unified estate person records (13,707 rows) are treated as the primary dataset. These records are derived from estate surveys and emigration manifests and are known to be incomplete: not all tenants appear in all survey years, emigration records are subject to transcription loss, and workhouse admissions represent only those who entered the Shillelagh workhouse. The system does not impute missing data; all analytical outputs reflect what is recorded, not what occurred.

**Entity resolution coverage.** The workhouse entity resolution subsystem operates over the subset of workhouse admissions (approximately one thousand records) that can be matched against the unified estate database. Records from workhouses outside the Coolattin catchment area are not included. Resolution is deterministic and rule-based; the system does not use probabilistic generative models for entity linking.

**LLM dependency.** The Ask pipeline operates in a degraded but functional mode when no LLM provider is available: fast-lane template hits and slot-fill-compiled SQL continue to function. The synthesis phase (Phase 7) requires at least one configured provider from the cascade (Claude, Grok, OpenRouter, Ollama). All API keys are treated as environment variables and are not committed to the repository.

**Knowledge graph availability.** The VRTI SPARQL endpoint (`https://virtuoso.virtualtreasury.ie/sparql/`) is an external dependency maintained by the Virtual Record Treasury of Ireland project. The system implements a five-minute offline cooldown and graceful degradation when the endpoint is unavailable, but KG-enriched answers require live connectivity. The in-process GraphRAG graph (built from a point-in-time snapshot of the KG) remains available offline.

**Read-only data access.** The public-facing web application provides read-only access to all data. No user-facing interface permits modification of the underlying estate records. Feedback submissions (thumbs-up/thumbs-down on Ask answers) are stored in a separate feedback table and do not alter source records.

**Academic reproducibility.** The application is designed for academic submission reproducibility. The SQLite database (`coolattin.db`) is pre-populated at submission time via the full ingest pipeline. The schema is created idempotently at startup. No external database server is required; all production data is contained in a single file.

## 1.7 Research Question Traceability

The following matrix maps each research question to the system components, chapters, and evaluation evidence that address it.

| Research Question | Primary System Components | Addressed In |
|---|---|---|
| **RQ1** — Record integration and entity disambiguation | `extensions.py` (schema DDL); `backend/jobs/full_ingest.py`; `backend/services/workhouse_entity_resolution.py`; `townland_xref`, `field_provenance` tables | Chapter 3 (Design), Chapter 4 (Implementation) |
| **RQ2** — Natural-language to SQL accuracy and safety | `backend/services/ask_service.py` (Phases 0–7); `backend/services/semantic_layer.py`; `backend/services/embedding_index.py`; `backend/services/intent_router.py` | Chapter 3, Chapter 4, Chapter 5 (Evaluation) |
| **RQ3** — Knowledge graph augmentation of relational answers | `backend/integrations/vrti_sparql.py`; `backend/services/subgraph_engine.py`; `backend/services/graphrag.py`; `graph_nodes`, `graph_edges` tables | Chapter 3, Chapter 4, Chapter 5 |
| **RQ4** — Workhouse-to-estate entity resolution | `backend/services/workhouse_entity_resolution.py`; `backend/services/entity_resolution/` (candidates, normalise, scoring); `source_mentions`, `entity_resolution_candidates`, `workhouse_unified_links` tables | Chapter 4, Chapter 5 |

**RQ1** is primarily addressed through the database schema design (fifteen tables, WAL mode, idempotent migration), the full ingest pipeline that reconciles GeoJSON estate data with VRTI knowledge graph data under coordinate-swap validation, and the townland entity resolution that assigns stable UUIDs across sources. The workhouse entity resolution subsystem addresses the person-level dimension of RQ1.

**RQ2** is addressed through the layered Ask pipeline architecture: the slot-fill compiler eliminates hallucination risk for analytical questions by producing deterministic SQL from a typed metric vocabulary; the template fast lane reduces LLM dependency for well-represented question types; and the read-only SQL safety guard prevents data modification regardless of what any LLM generates. Evaluation in Chapter 5 measures pipeline accuracy over a set of benchmark questions spanning all intent categories.

**RQ3** is addressed through three complementary mechanisms: real-time VRTI SPARQL queries that retrieve townland hierarchy and census data not present in the local database; an in-process NetworkX graph supporting k-hop BFS subgraph extraction over 49,081 nodes; and a cross-source fusion phase that surfaces discrepancies between SQL and KG-derived counts. Evaluation assesses whether KG augmentation improves answer completeness on contextual and heritage questions relative to SQL-only responses.

**RQ4** is addressed through the phonetic-blocking, seven-signal scoring pipeline that operates without any LLM involvement. The confidence-band labelling (CONFIRMED_MATCH, POSSIBLE_MATCH, WEAK_CANDIDATE, NO_MATCH) provides a transparent audit trail, and all decisions are persisted in the relational database for human review. The 140 confirmed matches achieved at submission time form the evaluation sample for precision and recall estimation in Chapter 5.

## 1.8 Dissertation Structure

The remainder of this dissertation is organised as follows.

**Chapter 2 — Literature Review** surveys the relevant prior work across four domains: historical record digitisation and linked data (with particular attention to the VRTI project and the CIDOC-CRM ontology); natural-language interfaces to databases (NL2SQL, text-to-SQL benchmarks, and the limitations of LLM-based SQL generation on domain-specific schemas); entity resolution in historical records (blocking strategies, scoring models, and evaluation on genealogical data); and hybrid retrieval architectures combining sparse and dense representations (BM25, dense passage retrieval, reciprocal rank fusion, and retrieval-augmented generation).

**Chapter 3 — System Design** presents the overall architecture of the Coolattin Estate Records Explorer, motivating key design decisions: the choice of SQLite over a client-server database; the application factory pattern and blueprint separation; the layered Ask pipeline architecture with its four dispatch paths; the GraphRAG approach to in-process knowledge graph analytics; and the pluggable analytics module protocol. Subsections detail the database schema design, the slot-fill metric vocabulary, the entity resolution scoring model, and the security architecture (rate limiting, CSP, read-only SQL enforcement).

**Chapter 4 — Implementation** documents the implementation of each major system component in the order of data flow: the full ingest pipeline (GeoJSON + VRTI → SQLite); the townland entity resolution (UUID assignment, cross-reference, field provenance); the workhouse entity resolution pipeline (normalisation → phonetic blocking → scoring → persistence); the Ask pipeline from question intake through SSE streaming to PDF export; the in-process GraphRAG graph construction (scripts/build_graph.py, 49,081 nodes, 64,308 edges); and the frontend (Leaflet.js choropleth, Chart.js analytics, D3.js force graph, vanilla-JS SSE consumer).

**Chapter 5 — Evaluation** assesses the system against each research question. For RQ1, the evaluation reports data coverage metrics (record counts, field completeness, provenance attribution). For RQ2, it presents pipeline accuracy on a benchmark of one hundred natural-language questions spanning all intent categories, measured against ground-truth SQL executed by the author. For RQ3, it compares answer completeness on contextual and heritage questions with and without KG augmentation. For RQ4, it reports precision and recall of the workhouse entity resolution pipeline on a manually labelled sample of one hundred candidate pairs. The chapter also discusses failure modes: LLM hallucination in the FALLBACK path, VRTI endpoint downtime, and phonetic collision errors in entity resolution.

**Chapter 6 — Discussion** reflects on the findings in relation to the research questions and the broader literature. It discusses the trade-off between deterministic and probabilistic query generation, the practical limitations of SPARQL-based KG enrichment over an external endpoint, the representational gap between estate records and workhouse admissions that limits entity resolution recall, and the ethical considerations of making personal historical records publicly searchable.

**Chapter 7 — Conclusion** summarises the contributions of the dissertation, revisits the research questions with reference to the evaluation findings, and identifies directions for future work: extension to other Irish estates and counties, integration of additional VRTI datasets (transportation records, land valuation), improvement of the entity resolution scoring model with supervised learning over the human-reviewed candidate pairs, and deployment of a persistent vector index (pgvector or a dedicated ANN library) to replace the in-memory TF-IDF index for production scale.
