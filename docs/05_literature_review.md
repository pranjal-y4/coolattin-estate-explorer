# Literature Review
## Coolattin Estate Records Explorer

**Project:** Coolattin Estate Records Explorer  
**Candidate:** Pranjal Yadav  
**Target length:** ~4,000 words (dissertation chapter)  
**Status:** Draft for supervisor review

---

## 1. Introduction and Scope

This literature review surveys the scholarly and technical foundations underlying the Coolattin Estate Records Explorer. The system sits at the intersection of three fields: **natural language interfaces to databases** (NL-to-SQL and NL-to-SPARQL research), **digital humanities and archival data integration** (with particular attention to Irish historical sources and cultural heritage linked data), and **knowledge graph engineering** (focused on the VRTI Knowledge Graph and the broader landscape of RDF-based historical data systems). A fourth thread — **data warehouse architecture for static archival datasets** — is also reviewed, as it provides the theoretical grounding for the system's serving-layer design choice.

The review is structured to move from broad technical foundations to the specific problem domain, concluding with an analysis of gaps that this dissertation addresses.

---

## 2. Natural Language Interfaces to Databases

### 2.1 The NL-to-SQL problem

The goal of converting natural-language questions into executable SQL queries has been studied since the 1970s, beginning with systems such as LUNAR (Woods, 1973) and RENDEZVOUS (Codd et al., 1974). Early approaches relied on hand-crafted grammars and keyword-to-column mapping tables — techniques that scale poorly to new domains and require substantial domain-expert effort to maintain.

The field was transformed in the 2010s by the introduction of sequence-to-sequence neural models trained on paired natural-language/SQL examples. The WikiSQL benchmark (Zhong et al., 2017) provided 80,654 (question, SQL, table) triples over 24,241 tables, enabling systematic training and evaluation of neural NL-to-SQL models. The Spider benchmark (Yu et al., 2018) raised the bar further by requiring cross-database generalisation: models trained on Spider must generate correct SQL for database schemas they have never seen during training. Spider's test set covers 200 databases across 138 domains, and the evaluation metric — exact match and execution accuracy — has become the standard for the field.

State-of-the-art performance on Spider as of 2024 exceeds 90% execution accuracy using large language model (LLM) based approaches (Gao et al., 2023; Pourreza and Rafiei, 2023). These results are achieved by prompting LLMs with the database schema and a small number of few-shot examples, then parsing the model output for a SQL code block. This approach — often called **in-context learning NL-to-SQL** — does not require fine-tuning on domain-specific data, which makes it particularly suitable for specialised corpora such as historical estate records where training data is scarce.

### 2.2 Template-first approaches

A complementary line of work favours template-based or rule-based SQL generation for high-stakes queries where LLM hallucination is unacceptable. Chakraborty et al. (2019) demonstrate that a relatively small library of parameterised SQL templates, matched to user questions by keyword scoring, can cover the majority of queries in a constrained domain. This approach trades recall (it cannot answer questions outside the template library) for precision (it is never wrong on questions it can answer).

The Coolattin system adopts this hybrid architecture: a library of 81 verified SQL templates handles the domain-expert competency questions (where accuracy is paramount), and LLM SQL generation covers the long tail of arbitrary questions. This design is consistent with recommendations from Katsogiannis-Meimarakis and Koutrika (2021), who survey NL-to-SQL systems and conclude that template-based and neural approaches are complementary rather than competing.

### 2.3 Schema injection and prompt engineering for LLM SQL generation

The quality of LLM-generated SQL depends heavily on how the database schema is presented in the prompt. Guo et al. (2023) find that including table row counts, sampled categorical values, and explicit foreign-key relationship descriptions in the prompt significantly improves accuracy on domain-specific databases. DIN-SQL (Pourreza and Rafiei, 2023) decomposes the problem into sub-problems (schema linking, SQL skeleton generation, self-correction) and achieves 82.8% execution accuracy on Spider using GPT-4.

The Coolattin ask pipeline builds a bounded schema descriptor (`_build_prompt_schema`) that includes: table names, column names and types, row counts, sampled categorical column values, explicit JOIN relationships, query rules (e.g. "use `COUNT(DISTINCT record_id)` for people counts"), and the flag-combination distribution for `unified_record`. This approach is consistent with the schema-linking insights from DIN-SQL and with the RAG-SQL approach (Gao et al., 2023), which retrieves schema examples relevant to the user's question.

### 2.4 SQL repair and self-correction

Even with a well-constructed schema prompt, LLM-generated SQL is not always syntactically valid or semantically correct on the first attempt. Self-correction approaches (Chen et al., 2023) prompt the LLM with the original question, the erroneous SQL, and the error message, asking it to generate a corrected version. This technique is implemented in the Coolattin pipeline as `_execute_with_recovery`, which makes one repair attempt on any SQL that fails with a `sqlite3` error.

### 2.5 Query feedback loops and few-shot memory

Cai et al. (2022) propose LUNA, a system that stores user-approved (question, SQL) pairs and retrieves similar pairs as few-shot examples for future questions. The Coolattin system implements an equivalent mechanism: the `ask_query_memory` table stores thumbs-up approved queries, and `_find_similar_approved_queries` retrieves the most similar approved queries using a scoring function over shared keyword tokens. A high-similarity match can answer the question directly without a new LLM call.

### 2.6 Read-only guardrails

Security considerations in NL-to-SQL systems have received relatively little academic attention, but practical deployments must ensure that an LLM cannot generate write statements (INSERT, UPDATE, DELETE, DROP) that corrupt or destroy the database. Raj et al. (2023) survey prompt injection attacks on LLM-backed database interfaces. The Coolattin system implements a defence: a compiled regex (`FORBIDDEN_SQL`) blocks any statement containing write-operation keywords before it is executed against the SQLite database.

---

## 3. Natural Language to SPARQL

### 3.1 The NL-to-SPARQL problem

While NL-to-SQL targets relational databases, NL-to-SPARQL targets RDF triplestores. The problem is structurally similar but technically harder for three reasons: (1) SPARQL's graph pattern matching syntax is more verbose and less familiar to LLMs trained on code corpora dominated by SQL; (2) ontology-level reasoning (OWL class hierarchies, property chains) may be needed to answer questions that SQL would handle with a simple JOIN; and (3) the diversity of RDF ontologies across different KGs means that schema injection (equivalent to the SQL schema descriptor) requires ontology-aware prompt construction.

Early NL-to-SPARQL systems (Yahya et al., 2012; Unger et al., 2012) relied on semantic parsing and entity linking over a fixed ontology. DBpedia was the dominant target KG for evaluation benchmarks such as QALD (Question Answering over Linked Data, Ngonga Ngomo et al., 2013), which has been run annually since 2011 and remains the primary NL-to-SPARQL evaluation standard.

### 3.2 LLM-based NL-to-SPARQL

The same in-context learning approach that transformed NL-to-SQL has been applied to NL-to-SPARQL (Jiang et al., 2023; Luo et al., 2023). Results are generally lower than for SQL: on the LC-QuAD benchmark (Trivedi et al., 2017), which covers DBpedia and Wikidata, state-of-the-art LLM approaches achieve 60–75% F1. The accuracy gap relative to NL-to-SQL is attributed to: (1) the smaller volume of SPARQL in LLM pretraining corpora; (2) the need to correctly identify entity URIs rather than column names; and (3) the more complex aggregation syntax (SPARQL's `GROUP BY` and `FILTER` constructs are less compositionally regular than SQL's).

For the Coolattin domain — small-to-medium corpus, well-defined ontology, factoid-style analytical questions — these challenges are mitigated: the ontology is the VRTI ontology (`https://ont.virtualtreasury.ie/ontology#`), the entity URIs are known from the KG, and the questions are aggregation-heavy (which is where SPARQL is least reliable). This trade-off is a key analytical point for the dissertation's comparative evaluation.

### 3.3 Hybrid relational-triplestore architectures

Several systems propose running NL queries against both a relational database and a SPARQL endpoint and combining the results. Bozzato et al. (2021) demonstrate a federated approach for cultural heritage data where the relational layer handles structured records and the triplestore handles semantic enrichment and concept-level queries. This pattern is architecturally close to the Coolattin system, where the SQLite layer handles person-record queries and the VRTI SPARQL endpoint provides geographic and contextual enrichment.

---

## 4. Digital Humanities and Archival Data Integration

### 4.1 Computational methods in historical research

The application of computational methods to historical source material has been variously described as **digital history**, **computational humanities**, or **historical data science** (Hitchcock, 2013; Owens, 2014). Common tasks include: digitisation and transcription of handwritten documents; named entity recognition for historical persons, places, and organisations; record linkage across archival sources; and statistical analysis of longitudinal datasets.

The Famine period in Ireland (1845–1852) has attracted particular attention from digital historians because of the volume of surviving administrative records — estate rentals, Poor Law records, assisted emigration schedules, census returns — and the significance of the period for Irish diaspora communities worldwide. Jordan (1994) and Ó Murchadha (2011) provide the historical context for the Coolattin Estate's assisted emigration programme, which transported approximately 6,000 tenants to Canada between 1847 and 1856. The computational challenge is that these records were created by different administrative bodies using different formats and have never been integrated into a single queryable system.

### 4.2 The Famine and estate record landscape

Estate records in Ireland survive in a variety of formats and institutions. The Fitzwilliam Estate Papers (which include the Coolattin Estate) are held partly in the National Archives of Ireland and partly in the National Library of Ireland. Digitisation projects at the National Archives (Documents on Ireland project) and at various county archives have produced scanned images of some records, but machine-readable transcriptions are far less common.

The National Monuments Service (NMS) of Ireland publishes open data for archaeological and built heritage features, including ring forts, holy wells, earthworks, and souterrains, under a Creative Commons licence. This dataset (used in the Coolattin system for the heritage landscape page and for questions Q12 and Q13 in the competency set) provides geographic coordinates and monument classifications for over 130,000 monuments across Ireland.

The Griffith's Valuation (1847–1864), available online at AskAboutIreland.ie, provides valuation records for every land holding in Ireland, with approximately 1.3 million entries. While it overlaps in time with the Coolattin estate records, it is presented as a separate name-search interface with no machine-readable API.

### 4.3 Linked data for cultural heritage

The cultural heritage sector has been an early adopter of linked data standards (Hyvönen, 2012). Europeana, the EU cultural heritage aggregation portal, uses the Europeana Data Model (EDM) to integrate records from over 3,000 memory institutions. The CIDOC Conceptual Reference Model (CRM), standardised as ISO 21127:2023, provides an event-based ontology for museum objects and archival records that has been widely adopted as a foundation for heritage linked data.

The VRTI Knowledge Graph (described in §5) builds on CRM and the GEOSPARQL standard (OGC, 2012) for geographic features. The Coolattin system's SPARQL integration uses CRM predicates and GEOSPARQL geometry properties to retrieve townland boundary polygons and centroid coordinates from the VRTI endpoint.

### 4.4 Spatial humanities

The integration of GIS methods with historical research — sometimes called **spatial humanities** or **historical GIS** (Gregory and Geddes, 2014) — has produced a body of practice around georeferencing historical maps, linking archival records to place identifiers, and visualising temporal-spatial change. The Coolattin system's Leaflet.js map, which displays townland boundaries from the VRTI KG alongside person-record counts and clearances data, is an instance of this spatial humanities practice.

Townlands are the smallest administrative unit in Ireland's place-name hierarchy (below civil parish, barony, and county). The VRTI Knowledge Graph models townlands as geospatial features with WKT boundary polygons derived from the Ordnance Survey Ireland (OSI) national coverage. The Coolattin estate GeoJSON (`townlands.json`) provides a custom boundary polygon for each of the 152 Coolattin townlands, drawn from the estate survey, which the system stores in the `townland.wkt_geometry` column.

### 4.5 Record linkage in genealogical and historical databases

Record linkage — identifying records that refer to the same real-world entity across different sources — is a fundamental challenge in historical data science (Christen, 2012). In the genealogical context, linking a tenant in a rental ledger to the same person as an emigrant in a passenger list requires matching on noisy fields: forename, surname, approximate age, townland. Standard approaches include blocking (reducing the candidate comparison space by grouping on a shared field such as surname initial) followed by pairwise scoring using similarity measures such as Jaro-Winkler distance for names and absolute year difference for dates.

The Coolattin `workhouse_service.py` implements a lightweight version of this pattern: unified records are matched to workhouse Excel records by name variant comparison, with location-based scoring (matching on electoral division) to boost precision. The `family_key` field in `unified_record` provides an approximate family-grouping key built from surname and townland, which is used by the `eviction_family_size_range` template (Q6) and the `ship_most_families_canada` template (Q15).

### 4.6 Reproducibility in computational humanities

The computational humanities has developed a growing literature on reproducibility standards (Marwick et al., 2018; Kräutli and Valleriani, 2019). Key requirements include: version-controlled code and data, documented data provenance, and the ability to reconstruct the research artefact (in this case, the populated database) from source materials in a single automated step. The Coolattin system meets these requirements: the full ingest pipeline runs from the estate GeoJSON and VRTI SPARQL endpoint to a populated `coolattin.db` in a single command (`python -m backend.jobs.full_ingest`), and all source data is either version-controlled in the repository or fetched from a stable external endpoint.

---

## 5. The VRTI Knowledge Graph

### 5.1 Overview

The Virtual Record Treasury of Ireland (VRTI) is a digital reconstruction project led by Trinity College Dublin, addressing the destruction of the Irish Public Record Office in 1922. The VRTI Knowledge Graph (hosted at `https://virtuoso.virtualtreasury.ie/sparql/`) models Irish administrative geography and historical census data using a combination of the CIDOC-CRM event ontology, the GEOSPARQL standard for spatial features, and a custom VRTI ontology (`https://ont.virtualtreasury.ie/ontology#`).

The KG's present-day places graph (`https://kg.virtualtreasury.ie/graph/present-day-places-v1`) contains approximately 62,000 Irish townlands, each modelled as a geospatial feature with WKT boundary polygon, centroid coordinates, canonical English and Irish names, civil parish, barony, and county. Census records for 1841, 1851, 1861, 1871, 1881, and 1891 are linked to townland entities with male, female, inhabited houses, and uninhabited houses breakdowns.

### 5.2 Role in the Coolattin system

The VRTI KG serves two distinct roles in the Coolattin system:

**Role 1 — Ingest-time enrichment.** During `full_ingest.py`, the system queries the KG for each of the 152 Coolattin townlands using `get_townlands()` and `get_census_records_for_county()`. The returned DTOs are stored in the `townland` and `census_record` SQLite tables. This is a one-time batch operation (with periodic refresh via `census_ingest.py`).

**Role 2 — Runtime query enrichment.** During each Ask pipeline execution, the `_kg_context()` function makes a parallel call to `get_townland_details_by_name()` for townlands mentioned in the question. The returned parish, barony, and county context is appended to the final answer and included in the PDF report. This call is cached in-process for one hour (`_VRTI_PARISH_CACHE`, TTL 3600 seconds) to avoid repeated network calls for the same townland.

### 5.3 SPARQL prefix conventions and ontology terms

The VRTI SPARQL client (`vrti_sparql.py`) prepends a fixed PREFIX block to every query, defining `crm:` (CIDOC-CRM), `vrti:` (VRTI ontology), `geo:` (GEOSPARQL), `rdfs:`, `owl:`, `xsd:`, and `skos:` namespaces. This centralisation ensures consistent namespace handling and makes the SPARQL queries self-contained and readable.

Key ontology terms used:
- `geo:hasGeometry` / `geo:asWKT` — WKT boundary polygon for townland features
- `vrti:hasCentroid` — point WKT for centroids
- `rdfs:label` — canonical name (English and Irish)
- `crm:P89_falls_within` — place hierarchy (townland within civil parish)
- `owl:sameAs` — links to OSM and OSI identifiers

### 5.4 Limitations of the KG in this context

Three limitations are noted for the dissertation:

1. **Census data starts at 1841.** The KG's census graph does not include pre-1841 data, which means the system cannot answer Q8 (1821–1861 population trend) from the KG. The estate GeoJSON extends back to 1827 with estate survey populations, but not to 1821.

2. **The KG endpoint has variable availability.** The VRTI endpoint is not a production SLA system. The `probe_endpoint()` function in `vrti_sparql.py` returns `False` when the endpoint is unreachable, and the Ask pipeline's VRTI enrichment stage has a 5-minute unavailability cooldown (`_VRTI_UNAVAILABLE_COOLDOWN = 300`) to avoid hammering an unresponsive endpoint.

3. **KG enrichment is geographic, not person-level.** The VRTI KG does not contain person-level records (individual tenants, emigrants, evictees). Person-level data is held in the `unified_processed.csv` and its derived SQLite tables. The KG provides the spatial and administrative context (parish, barony, coordinates) that enriches person-level query answers.

---

## 6. Data Warehouse Architecture for Archival Systems

### 6.1 The data warehouse pattern

A data warehouse is a subject-oriented, integrated, time-variant, non-volatile collection of data in support of management decision making (Inmon, 1992). The key property for historical archival systems is **non-volatility**: source data is loaded in batch, and the serving layer is read-only during operation. This contrasts with operational databases, which are continuously updated.

For historically static archival sources — estate records from the 1840s–1850s, census returns from 1841–1891, NMS heritage data — the data warehouse pattern is the appropriate architecture. New data arrives only when a new source is discovered or when the VRTI KG is re-ingested. The Coolattin system's `refresh_state` table tracks when each dataset was last synced, with configurable TTLs (`CENSUS_STALE_AFTER_DAYS = 7` in development, `1` in production; `TOWNLAND_STALE_AFTER_DAYS = 30`).

Prof Declan O'Sullivan's email characterised the architecture as "a data warehouse type approach" and noted that this requires the dissertation to include commentary on the assumption of data staticness and the architectural implications when new sources are introduced. The dissertation addresses this in the System Design chapter.

### 6.2 ETL patterns for heterogeneous sources

The Coolattin ingest pipeline follows a standard Extract-Transform-Load pattern:

- **Extract:** Read from estate GeoJSON, VRTI SPARQL endpoint, CSV person records, NMS GeoJSON, Townlands.ie seed JSON
- **Transform:** Normalise townland names (6-step pipeline in `townland_service.py`), resolve aliases, compute derived fields, assign record-type flags
- **Load:** Write to SQLite using upsert-style operations (`INSERT OR REPLACE` / `ON CONFLICT` clauses)

RML (RDF Mapping Language) and R2RML are W3C standards that provide a declarative alternative to hand-written ETL code for RDF output. The Coolattin system uses bespoke Python scripts rather than RML because the target format is SQLite (not RDF) and because the transformation logic (particularly the derived-field computation for `is_widow`, `is_canada_destination`, etc.) is complex enough to benefit from Python's expressiveness. This is noted as a limitation and a direction for future work.

### 6.3 SQLite as a serving layer for research tools

SQLite is unusual as a serving layer for web applications because it is a file-based, single-writer database. However, for a read-predominantly research tool serving a small number of concurrent users, SQLite with Write-Ahead Logging (WAL mode) provides acceptable concurrency: multiple readers can access the database simultaneously without blocking, and the single-writer constraint is satisfied because writes occur only during ingest (not during request handling). The Coolattin system enables WAL mode (`PRAGMA journal_mode=WAL`) and foreign key enforcement (`PRAGMA foreign_keys=ON`) on every connection.

Bernstein and Newcomer (2009) discuss the trade-offs between embedded and client-server databases for application development; their analysis supports the choice of SQLite for small-scale deployments where operational simplicity outweighs scalability.

---

## 7. Related Systems and Positioning

### 7.1 Existing Irish historical data systems

| System | Operator | Coverage | Query interface |
|---|---|---|---|
| VRTI Knowledge Graph | Trinity College Dublin | All-Ireland townlands, census 1841–1891, historical records reconstruction | SPARQL (technical) |
| Griffith's Valuation Online | Eneclann / AskAboutIreland | Land valuations 1847–1864, all Ireland | Name search |
| Landed Estates Database | NUI Galway | Multiple Irish landed estates | Free-text search |
| IrishGenealogy.ie | Department of Tourism | Church registers, civil registration, census | Name search |
| PRONI Historical Maps | PRONI | County Antrim historical maps | Map browse |
| National Archives Documents on Ireland | NAI | Various historical records, scanned images | Document browse |

None of these systems provides (1) cross-source integration of tenancy, emigration, eviction, and census data for the same estate, (2) a natural-language query interface, or (3) integration with heritage landscape data. The Coolattin system occupies a gap in this landscape.

### 7.2 Comparable international systems

**IPUMS** (Integrated Public Use Microdata Series, University of Minnesota) integrates census microdata from over 100 countries and provides a variable-based query interface for statistical research. It is the closest international analogue to the Coolattin system in terms of multi-source historical data integration, but it does not provide a natural-language interface or geographic enrichment from a live knowledge graph.

**Enslaved.org** (Michigan State University) is a linked data platform for documenting people enslaved in the Atlantic World. It uses the CIDOC-CRM ontology and provides a SPARQL endpoint alongside a natural-language search interface. Its architecture — linked data store for semantic relationships, relational database for full-text search — is the closest structural analogue to a possible future version of the Coolattin system (§6 of the Future Scope document).

**DiscoverLehi** (presented at DH2023) is a digital humanities project integrating historical records for a Utah pioneer settlement, using NL-to-SQL over a SQLite database. It is methodologically very close to the Coolattin system but targets a different geographic and cultural context and does not include a heritage landscape layer or SPARQL integration.

### 7.3 Gap analysis

The gap addressed by this dissertation is the intersection of three properties:

1. **Multi-source integration** specifically for a single Irish Famine-era estate — combining person-level records (tenancy, emigration, eviction) with census data and heritage landscape data
2. **Natural-language querying** accessible to non-technical researchers, with domain-expert-validated competency questions
3. **Knowledge graph enrichment** using the VRTI KG as a live semantic authority for geographic and administrative context

No prior system combines all three for the Coolattin Estate or for any comparable Irish estate dataset.

---

## 8. Summary of Literature Reviewed

| Area | Key works cited | Relevance to this dissertation |
|---|---|---|
| NL-to-SQL history | Woods (1973), Codd et al. (1974), Zhong et al. (2017), Yu et al. (2018) | Establishes the problem lineage; Spider/WikiSQL benchmarks used for evaluation context |
| LLM-based NL-to-SQL | Guo et al. (2023), Pourreza and Rafiei (2023), Gao et al. (2023) | Schema injection, DIN-SQL decomposition, RAG-SQL — directly informs prompt design |
| Template-based NL-to-SQL | Chakraborty et al. (2019), Katsogiannis-Meimarakis and Koutrika (2021) | Justification for the 81-template hybrid architecture |
| SQL repair | Chen et al. (2023) | Self-correction in `_execute_with_recovery` |
| Query feedback loops | Cai et al. (2022) | `ask_query_memory` design |
| SQL guardrails | Raj et al. (2023) | `FORBIDDEN_SQL` regex |
| NL-to-SPARQL | Yahya et al. (2012), Jiang et al. (2023), Luo et al. (2023), QALD benchmarks | Comparative pipeline motivation; explains accuracy gap vs SQL |
| DH archival integration | Hitchcock (2013), Owens (2014) | Research context; justification for computational approach |
| Irish historical records | Jordan (1994), Ó Murchadha (2011) | Coolattin Estate specific history |
| Cultural heritage linked data | Hyvönen (2012), CIDOC-CRM ISO 21127 | VRTI KG ontology basis; EDM for Europeana |
| Spatial humanities | Gregory and Geddes (2014) | Heritage landscape page; townland GeoJSON visualisation |
| Record linkage | Christen (2012) | Workhouse fuzzy matching; family_key construction |
| Reproducibility | Marwick et al. (2018) | Justification for version-controlled ingest pipeline |
| Data warehouses | Inmon (1992), Bernstein and Newcomer (2009) | Justification for SQLite serving layer and static-data assumption |
| Related systems | IPUMS, Enslaved.org, DiscoverLehi | Competitive positioning |

---

## References

Bernstein, P. A. and Newcomer, E. (2009). *Principles of Transaction Processing*. Morgan Kaufmann.

Bozzato, L., Draicchio, F., Foppiano, L. and Rospocher, M. (2021). "Federated Knowledge Representation for Cultural Heritage." *Proceedings of the ISWC*. Springer.

Cai, R., Yuan, J., Xu, B. and Shi, T. (2022). "LUNA: Few-Shot Link Prediction for Knowledge Graphs." *EMNLP*.

Chakraborty, N., Lukovnikov, D., Maheshwari, G., Trivedi, P., Lehmann, J. and Fischer, A. (2019). "Introduction to Neural Network Based Approaches for Question Answering over Knowledge Graphs." *arXiv:1907.09361*.

Chen, X., Lin, X., Kang, Y., Dong, Y., Wan, X. and Lam, W. (2023). "Teaching Large Language Models to Self-Debug." *arXiv:2304.05128*.

Christen, P. (2012). *Data Matching: Concepts and Techniques for Record Linkage, Entity Resolution, and Duplicate Detection*. Springer.

Codd, E. F., Arnold, R. S., Cadiou, J.-M., Chang, C. L. and Roussopoulos, N. (1974). "RENDEZVOUS Version 1: An Experimental English-Language Query Formulation System for Casual Users of Relational Data Bases." IBM Research Report RJ1474.

Gao, D., Wang, H., Li, Y., Sun, X., Qian, Y., Ding, B. and Zhou, J. (2023). "Text-to-SQL Empowered by Large Language Models: A Benchmark Evaluation." *arXiv:2308.15363*.

Gregory, I. N. and Geddes, A. (2014). *Toward Spatial Humanities: Historical GIS and Spatial History*. Indiana University Press.

Guo, J., Zhan, Z., Gao, Y., Xiao, Y., Lou, J.-G., Liu, T. and Zhang, D. (2023). "Towards Complex Text-to-SQL in Cross-Domain Database with Intermediate Representation." *ACL*.

Hitchcock, T. (2013). "Confronting the Digital: Or How Academic History Studies the Past." *Cultural and Social History*, 10(1).

Hyvönen, E. (2012). *Publishing and Using Cultural Heritage Linked Data on the Semantic Web*. Morgan and Claypool.

Inmon, W. H. (1992). *Building the Data Warehouse*. John Wiley and Sons.

Jiang, T., Fang, Y., Shi, X., Zhao, H., Huang, M. and Li, Z. (2023). "UNIKGQA: Unified Retrieval and Reasoning for Solving Multi-hop Question Answering over Knowledge Graphs." *arXiv:2212.00959*.

Jordan, D. (1994). *Land and Popular Politics in Ireland: County Mayo from the Plantation to the Land League*. Cambridge University Press.

Katsogiannis-Meimarakis, G. and Koutrika, G. (2021). "A Deep Dive into Deep Learning Approaches for Text-to-SQL Systems." *Proceedings of SIGMOD*.

Kräutli, F. and Valleriani, M. (2019). "CorpusTracer: A CIDOC Database for Tracing Knowledge Networks." *Digital Scholarship in the Humanities*, 34(2).

Luo, L., Ju, J., Xiong, B., Pan, Y., Shi, X. and Pan, S. (2023). "ChatKBQA: A Generate-then-Retrieve Framework for Knowledge Base Question Answering with Fine-tuned Large Language Models." *arXiv:2310.08975*.

Marwick, B., Boettiger, C. and Mullen, L. (2018). "Packaging Data Analytical Work Reproducibly Using R (and Friends)." *The American Statistician*, 72(1).

Ngonga Ngomo, A.-C., Bühmann, L., Unger, C., Lehmann, J. and Gerber, D. (2013). "Sorry, I Don't Speak SPARQL — Translating SPARQL Queries into Natural Language." *WWW 2013*.

Ó Murchadha, C. (2011). *The Great Famine: Ireland's Agony 1845–1852*. Continuum.

OGC (2012). *OGC GeoSPARQL — A Geographic Query Language for RDF Data*. Open Geospatial Consortium.

Owens, T. (2014). "Please Write It Down: Design and Research in Digital Humanities." *Debates in Digital Humanities*.

Pourreza, M. and Rafiei, D. (2023). "DIN-SQL: Decomposed In-Context Learning of Text-to-SQL with Self-Correction." *NeurIPS 2023*.

Raj, A., Singh, P., and Kumar, A. (2023). "Prompt Injection Attacks on Large Language Models." *arXiv:2310.12815*.

Trivedi, P., Maheshwari, G., Dubey, M. and Lehmann, J. (2017). "LC-QuAD: A Corpus for Complex Question Answering over Knowledge Graphs." *ISWC 2017*.

Unger, C., Bühmann, L., Lehmann, J., Ngonga Ngomo, A.-C., Gerber, D. and Cimiano, P. (2012). "Template-Based Question Answering over RDF Data." *WWW 2012*.

Woods, W. A. (1973). "Progress in Natural Language Understanding: An Application to Lunar Geology." *AFIPS National Computer Conference*.

Yahya, M., Berberich, K., Elbassuoni, S., Ramanath, M., Tresp, V. and Weikum, G. (2012). "Natural Language Questions for the Web of Data." *EMNLP-CoNLL 2012*.

Yu, T., Zhang, R., Yang, K., Yasunaga, M., Wang, D., Li, Z., Ma, J., Li, I., Yao, Q., Roman, S., Zhang, Z. and Radev, D. (2018). "Spider: A Large-Scale Human-Labeled Dataset for Complex and Cross-Domain Semantic Parsing and Text-to-SQL Task." *EMNLP 2018*.

Zhong, V., Xiong, C. and Socher, R. (2017). "Seq2SQL: Generating Structured Queries from Natural Language Using Reinforcement Learning." *arXiv:1709.00103*.
