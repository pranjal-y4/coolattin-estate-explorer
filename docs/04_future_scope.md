# Future Scope and Extra Deliverables

**Project:** Coolattin Estate Records Explorer  
**Candidate:** Pranjal Yadav

This document catalogues directions for extending the project beyond the MSc dissertation scope. Items are organised by effort level and academic impact. The first section covers the comparative pipeline extension that Prof Declan O'Sullivan specifically recommended — this is the highest-priority item that straddles the boundary between "dissertation extension" and "future work".

---

## Priority 0: Recommended Extension (Supervisor-endorsed)

### 0.1 NL→SPARQL Comparative Pipeline

**What:** Implement a second query pipeline that uplifts a sample of the Coolattin data into RDF, loads it into a local triplestore (Apache Jena Fuseki), and routes natural-language questions through an LLM→SPARQL→answer path instead of LLM→SQL→answer.

**Why it matters:** Prof Declan O'Sullivan explicitly identified this as a valuable CS contribution. Running the same 15 competency questions through both pipelines and comparing results, latency, and answer quality directly strengthens the dissertation's evaluation chapter and positions it as a methodologically complete CS contribution rather than just a software build.

**Scope for dissertation:**
- Uplift: 5 townlands × census + emigration records (≈ 200 triples) into Turtle format using a Python script
- Triplestore: Apache Jena Fuseki (single JAR, runs locally, no infrastructure needed)
- Pipeline: one Python function that replaces the SQL template path with a SPARQL generation path, using the same LLM prompt structure
- Evaluation: run Qs 7, 8, 9, 14, 15 (the most SPARQL-natural questions) on both pipelines and compare

**Estimated effort:** 2–3 weeks if started in Week 6.

**Why this is achievable before submission:** The existing LLM integration in `ask_service.py` already handles prompt construction, LLM call, and response parsing. The only new component is the SPARQL schema descriptor (replacing the SQL schema descriptor) and the Fuseki endpoint call (replacing `_run_read_only_query`).

---

## Category 1: CS / Architecture Extensions

### 1.1 RML / R2RML Mapping Pipeline

**What:** Replace the hand-written Python ingest scripts with a formal RML (RDF Mapping Language) or R2RML mapping that declaratively describes the transformation from the source CSV/Excel files into RDF triples.

**Why:** RML is an emerging W3C standard for heterogeneous data-to-RDF transformation. An RML pipeline is more reproducible, inspectable, and transferable than bespoke Python scripts. Prof O'Sullivan referenced this approach as the original expected architecture.

**Academic relevance:** Publishable as a methodological contribution in the digital humanities / linked data space. Demonstrates knowledge of semantic web standards.

**Effort:** 4–6 weeks. Can reuse the existing data model as the ontology basis.

---

### 1.2 Local Triplestore as Runtime Query Engine

**What:** Replace (or run alongside) the SQLite serving layer with a local Apache Jena Fuseki or GraphDB Community triplestore as the primary query engine, with the full Coolattin dataset uplifted to RDF.

**Why:** Enables SPARQL-native queries including graph traversal (e.g., "show me all relatives of tenant X who also emigrated"), federated queries joining local data with the live VRTI endpoint, and reasoning over ontology relationships.

**Challenge:** SPARQL aggregate queries (sums, averages, group-bys) are more verbose and harder to generate with an LLM than equivalent SQL. Benchmark studies show NL→SPARQL accuracy is typically lower than NL→SQL for relational-shaped data.

**Effort:** 6–10 weeks for a complete replacement. A hybrid approach (SQLite for analytics, triplestore for graph traversal) is more achievable.

---

### 1.3 RAG-Enhanced Query Pipeline

**What:** Add a Retrieval-Augmented Generation (RAG) layer to the Ask pipeline. Before calling the LLM, retrieve the top-K most similar historical questions from a vector store of previously answered questions and include them as few-shot examples in the prompt.

**Why:** Few-shot prompting with domain-relevant examples significantly improves LLM SQL generation accuracy on specialised corpora. The existing query feedback table (`ask_query_feedback`) already stores successful question–SQL pairs that could seed the vector store.

**Implementation:** Use a lightweight local embedding model (e.g., `sentence-transformers/all-MiniLM-L6-v2`) and a local vector store (SQLite-VSS or Chroma). No external API dependency.

**Effort:** 2–3 weeks.

---

### 1.4 Formal NL-to-SQL Benchmark Evaluation

**What:** Evaluate the Ask pipeline against a subset of the Spider or WikiSQL NL-to-SQL benchmark datasets to establish a baseline accuracy figure that can be compared with the state of the art.

**Why:** The 15 competency questions are domain-expert validated but are a small, in-distribution sample. A standard benchmark evaluation gives the dissertation a comparative figure that places the system in the broader NL-to-SQL literature.

**Challenge:** The Spider benchmark uses a different schema to Coolattin, so the comparison would be on the LLM SQL generation component only (not the full pipeline including templates).

**Effort:** 1–2 weeks.

---

### 1.5 Multi-Model LLM Evaluation

**What:** Run the 15 competency questions through multiple LLMs (GPT-4o, Claude Sonnet, Mistral-7B via Ollama, Llama-3-8B) and compare SQL generation accuracy, answer quality, and latency.

**Why:** LLM choice is a significant variable in NL-to-SQL system performance. A multi-model comparison is a publishable result and a strong CS dissertation chapter.

**Effort:** 1–2 weeks (mostly prompt engineering; pipeline infrastructure already supports multiple providers).

---

## Category 2: Data and Coverage Extensions

### 2.1 Pre-1841 Population Data Integration

**What:** Source and ingest pre-1841 population estimates for Coolattin townlands. Candidates include the 1821 Irish Census fragments, the 1831 Census, and any estate survey records from the Fitzwilliam Estate papers.

**Why:** Dr Ciarán Wallace's question 8 specifically asks about the 1821–1861 trend. The current system honestly reports that data begins in 1841. Adding pre-1841 data would make the answer complete.

**Source:** National Archives of Ireland; Wicklow County Archives; the Fitzwilliam Estate Papers in the National Library of Ireland.

**Effort:** Data sourcing (archival access) is the bottleneck; ingest is straightforward once data is in CSV.

---

### 2.2 Additional Irish Estate Datasets

**What:** Extend the integration to include records from neighbouring estates in County Wicklow (e.g., the Fitzwilliam Estates, which overlap with Coolattin geographically) or from the Landed Estates Court records for the same area.

**Why:** Enables cross-estate comparisons of eviction rates, emigration patterns, and tenant demographics.

**Effort:** Depends on data availability and format; the ingest pipeline is designed to be extended with additional source adaptors.

---

### 2.3 Ship Voyage and Passenger Manifest Data

**What:** Ingest standardised passenger manifest data from ships named in the Coolattin emigration records (e.g., from the Library and Archives Canada or the National Archives) to cross-reference family groups and verify arrival dates.

**Why:** Allows verification and enrichment of questions 14 and 15 (Canada emigration peak, most family-carrying ship). It also enables new questions: "Which passengers from the Dunbrody are also in the eviction records?"

**Effort:** 3–4 weeks if manifest data is available in machine-readable form.

---

## Category 3: Digital Humanities / Research Features

### 3.1 Genealogical Record Linking

**What:** Implement a record linkage algorithm that attempts to connect the same individual across different record types: a tenant in the rental ledger, an emigrant in the emigration list, and a head of household in the census.

**Why:** This is a core problem in genealogical research. Record linkage across archival sources is both a significant technical challenge (fuzzy name matching, birth year inference, household grouping) and a direct user need.

**Implementation approach:** Blocking by townland + surname, followed by a scoring function over forename similarity, year proximity, and household composition. Can be implemented as an offline preprocessing step that adds a `linked_record_group` identifier to `unified_record`.

**Effort:** 4–6 weeks. Record linkage quality must be evaluated — precision and recall on a hand-labelled sample.

---

### 3.2 Family Network Visualisation

**What:** Add a graph visualisation (using D3.js or Cytoscape.js) showing family connections within the Coolattin records: households grouped by surname and townland, with edges indicating shared emigration voyages, co-tenancy, or family key links.

**Why:** Graph visualisation makes the social structure of the estate visible in a way that tables cannot. It is immediately compelling for genealogical researchers.

**Effort:** 3–4 weeks.

---

### 3.3 Irish Language (Gaeilge) Interface

**What:** Add Irish-language translations of the interface labels, page text, and suggested questions. The Ask page could optionally accept questions in Irish, with a translation step before the pipeline.

**Why:** The VRTI project is explicitly bilingual (Irish and English). An Irish-language interface aligns the project with its institutional context and broadens accessibility for Irish-speaking users.

**Effort:** 2–3 weeks for interface translation (straightforward). Irish-language question input requires an Irish-to-English translation step before the NL→SQL pipeline; this can be implemented via the LLM with a translation prompt prefix.

---

### 3.4 IIIF Document Viewer

**What:** If scanned images of the original estate ledger pages are available from the National Archives or the National Library of Ireland, embed a IIIF viewer (Universal Viewer or Mirador) to display the source document alongside the structured data record.

**Why:** Showing the original handwritten document alongside the structured data record significantly increases academic credibility and research utility. Users can verify the transcription against the source.

**Effort:** 2–3 weeks if IIIF manifests are available from the holding institution; 6+ weeks if images need to be scanned and manifested.

---

### 3.5 Oral History and Community Annotation Layer

**What:** Allow authenticated users (family researchers, local historians) to add annotations, corrections, or contextual notes to individual records. Annotations are stored separately and displayed alongside the structured data.

**Why:** Community-sourced knowledge is a recognised methodology in digital public history. Descendants of Coolattin tenants may have knowledge (photographs, family stories, local place-name variations) that enriches the structured records.

**Effort:** 4–6 weeks (authentication, annotation schema, moderation workflow).

---

## Category 4: Infrastructure and Deployment

### 4.1 Production Database Migration (PostgreSQL)

**What:** Replace the SQLite serving layer with a PostgreSQL instance (e.g., Azure Database for PostgreSQL) for multi-user concurrent access, proper full-text search, and PostGIS spatial query support.

**Why:** SQLite is appropriate for a single-user research tool and a dissertation prototype, but a production-grade public-facing system serving multiple concurrent users would benefit from PostgreSQL's concurrency model and spatial extension.

**Effort:** 2–3 weeks (schema migration is straightforward; the `repositories/` layer isolates all SQL queries).

---

### 4.2 REST API and OpenAPI Specification

**What:** Expose the core data queries as a documented REST API with an OpenAPI specification, so that other researchers can programmatically access Coolattin data without using the web UI.

**Why:** Research infrastructure should be reusable. A documented API allows other DH projects to integrate Coolattin data.

**Effort:** 2–3 weeks.

---

### 4.3 Scheduled Data Refresh and Monitoring

**What:** Implement a scheduled refresh job (e.g., nightly via Azure Container Jobs) that re-queries the VRTI SPARQL endpoint for any updates and updates the local serving layer. Add monitoring (uptime check, answer delivery rate tracking) for the live deployment.

**Why:** The VRTI Knowledge Graph is actively maintained and updated. A refresh schedule keeps the serving layer current without manual intervention.

**Effort:** 1–2 weeks.

---

## Priority Matrix

| Item | CS Impact | DH Impact | Effort | For dissertation? |
|---|---|---|---|---|
| 0.1 NL→SPARQL comparative | High | Medium | Medium | **Yes — highest priority** |
| 1.3 RAG-enhanced pipeline | High | Low | Low | Possible if time allows |
| 1.5 Multi-model LLM eval | High | Low | Low | Possible if time allows |
| 2.1 Pre-1841 population data | Low | High | Medium | Data sourcing may block |
| 3.1 Genealogical record linking | High | High | High | Post-dissertation |
| 3.2 Family network visualisation | Medium | High | Medium | Post-dissertation |
| 1.1 RML/R2RML mapping | High | Medium | High | Post-dissertation |
| 1.2 Triplestore as runtime engine | High | Medium | Very high | Post-dissertation |
| 3.4 IIIF document viewer | Low | Very high | Medium | Depends on image availability |
| 4.1 PostgreSQL migration | Medium | Low | Medium | Post-dissertation |
| 3.3 Irish language interface | Low | High | Medium | Post-dissertation |
| 3.5 Community annotation | Low | High | High | Post-dissertation |
