# Evaluation Strategy

**Project:** Coolattin Estate Records Explorer  
**Candidate:** Pranjal Yadav  
**Submission:** First week of August 2026

---

## Overview

The evaluation is structured across four dimensions: **functional correctness** (does the system answer the 15 domain-expert competency questions accurately?), **pipeline reliability** (how often does the system reach an answer and by which route?), **answer quality** (are natural-language answers historically accurate and clearly expressed?), and **data completeness** (how fully do the underlying records cover the Coolattin estate corpus?).

Each dimension has specific metrics, methods, and evidence standards appropriate for an MSc dissertation.

---

## Dimension 1: Functional Correctness — Competency Question Evaluation

The primary evaluation instrument is the 15-question competency set provided by Dr Ciarán Wallace (VRTI Programme Director). These questions were specified by a domain expert as representative of the analytical needs of historians and genealogical researchers working with nineteenth-century Irish estate records.

### 1.1 The 15 Competency Questions

| # | Question | Template ID | Expected answer type |
|---|---|---|---|
| Q1 | On average, did male tenants have more land than female tenants? | `tenant_land_gender_average` | Numeric comparison with counts |
| Q2 | How many widows appear in the records? | `widows_count` | Scalar count with data provenance note |
| Q3 | What proportion of widows had children? | `widows_with_children_proportion` | Percentage with raw numerator/denominator |
| Q4 | What proportion of widows appear on the eviction records? | `widows_eviction_proportion` | Percentage with raw numerator/denominator |
| Q5 | How many children emigrated? | `children_emigrated` | Count with age-threshold definition |
| Q6 | What was the range of family sizes in the eviction records? | `eviction_family_size_range` | Min/max/avg with coverage percentage |
| Q7 | Are the most populous townlands in 1841 still the most populous in 1861? | `most_populous_1841_vs_1861` | Named townland comparison |
| Q8 | What is the overall population trend 1821–1861? | `population_trend_1841_1861` | Time series (with 1821 caveat documented) |
| Q9 | Is there a relationship between emigration townlands and population trends? | `emigration_population_townland_trend` | Per-townland table with correlation indicators |
| Q10 | What tenants had more land at the end of the record dates? | `largest_latest_tenant_holdings` | Ranked list with years and acreages |
| Q11 | Which townlands had the tenants with the smallest plots? | `smallest_townland_plots` | Ranked townland table |
| Q12 | Is there a statistical relationship between holy wells and high population? | `holy_well_population_relationship` | Group comparison (has/lacks feature) |
| Q13 | Is there a statistical relationship between ring forts and high population? | `ring_fort_population_relationship` | Group comparison (has/lacks feature) |
| Q14 | What was the peak period for emigration to Canada? | `canada_emigration_peak_period` | Year(s) with counts |
| Q15 | Which ship carried the most Coolattin families to Canada? | `ship_most_families_canada` | Named ship with family count |

### 1.2 Evaluation Method

For each question:

1. **Enter the question verbatim** into the Ask page in a clean browser session.
2. **Record the pipeline route taken**: check `query_provenance.strategy` in the SSE result — values are `rule_fill` (semantic layer rule-based), `verified_analysis` (fast-lane verified template), `slot_fill_llm` (semantic layer LLM slot-fill), `template` (embedding fast lane), `memory` (approved memory reuse), or `llm_sql` (FALLBACK free-form LLM SQL).
3. **Record the SQL query executed** (shown in the Ask page response).
4. **Independently verify the answer** by running the same SQL directly against the SQLite database using a command-line tool, and cross-checking against the source CSV/Excel files where possible.
5. **Assess the NL rewrite**: Is the rephrased answer factually consistent with the raw data? Is it appropriately hedged where data is sparse?
6. **Classify outcome**: Correct / Partially correct (right SQL, imprecise rewrite) / Incorrect / No answer.

### 1.3 Data Provenance Notes to Document

Certain questions have known data limitations that must be documented as part of the evaluation, not hidden:

- **Q8 (1821–1861 trend)**: The VRTI KG census data begins in 1841. The system correctly handles this by returning 1841–1861 data with an explicit warning. The dissertation should note whether any estate survey data for pre-1841 years exists in the source CSVs.
- **Q2–Q4 (widows)**: Widow identification is derived from widow-labelled names and notes in the source rows. The accuracy depends on the original transcription conventions.
- **Q5 (children emigrated)**: Child status is inferred from a recorded age < 18. Records without an age field are excluded and this coverage gap is reported.
- **Q6 (family size in evictions)**: Family size is estimated from linked family keys. Coverage percentage is reported in the query output.
- **Q12–Q13 (heritage correlations)**: These are descriptive comparisons (group average) not formal statistical significance tests. The dissertation should be explicit that these answer "is there a descriptive difference?" not "is the difference statistically significant at p < 0.05?".

---

## Dimension 2: Pipeline Reliability

### 2.1 Metrics

| Metric | Definition | Target |
|---|---|---|
| Template match rate | % of the 15 competency questions answered by a verified template (not LLM SQL generation) | 100% for the 15 competency questions |
| Answer delivery rate | % of questions that return any usable answer (template + LLM fallback combined) | ≥ 93% |
| LLM SQL error rate | % of LLM-generated queries that fail with a SQL error on first attempt | Documented |
| Self-repair rate | % of failed LLM SQL queries that succeed after the built-in repair step | Documented |
| Median end-to-end latency | Time from question submission to final SSE `done` event, measured per route | Template path: < 2 s; LLM path: < 15 s |

### 2.2 Measurement Method

Run the 15 competency questions (and a supplementary set of 10 free-form questions not in the template library) in a controlled session. Record:
- Which pipeline stage each question resolved at (from the SSE stage events logged in the browser console)
- Time to each stage (available from the SSE `duration_ms` fields)
- Whether any SQL repair step was triggered

Record results in a table in the dissertation's evaluation chapter.

---

## Dimension 3: Answer Quality

### 3.1 LLM Rewrite Assessment

For the 15 competency questions, assess the natural-language rewrite output on three criteria (1–5 scale):

| Criterion | Description |
|---|---|
| **Factual consistency** | Does the NL answer accurately represent the data in the table rows? No hallucination of figures not present in the results. |
| **Historical appropriateness** | Does the answer use appropriate vocabulary and framing for the nineteenth-century Irish historical context? |
| **Appropriate hedging** | Where data coverage is limited, does the answer communicate this? Does it avoid overclaiming? |

Assessment is conducted by the candidate and reviewed in dialogue with Dr Ciarán Wallace, who as domain expert can assess historical appropriateness.

### 3.2 VRTI Enrichment Quality

For questions that involve named townlands, assess whether the VRTI SPARQL enrichment (parish, barony, county) is populated and correct. Compare against the VRTI Knowledge Graph directly for a sample of 5 townlands.

---

## Dimension 4: Data Completeness

### 4.1 Record Coverage

Report the following counts from the integrated database:

| Metric | Source |
|---|---|
| Total unified records | `SELECT COUNT(DISTINCT record_id) FROM unified_record` |
| Records with emigration data | `WHERE has_emigration_record=1` |
| Records with eviction data | `WHERE has_eviction_record=1` |
| Records with tenancy data | `WHERE has_tenancy_record=1` |
| Records with known age | `WHERE age IS NOT NULL` |
| Records with known gender | `WHERE gender IS NOT NULL AND gender != ''` |
| Townlands with census data | Join `census_record` → `townland` |
| Townlands with heritage features | Join `heritage_feature` → `townland` |

### 4.2 Source-to-Database Traceability

Select one data category (e.g., emigration records) and trace a random sample of 20 source rows from the original CSV/Excel file through the ingest pipeline to the corresponding `unified_record` rows. Report: how many rows mapped cleanly, how many required normalisation, how many were dropped and why.

---

## Dimension 5: Comparative Baseline (Recommended Extension)

Prof Declan O'Sullivan has specifically recommended exploring a comparative NL→SPARQL pipeline as a CS extension of value. If time permits before submission, implement a lightweight version:

1. **Uplift a sample dataset** (e.g., 5 townlands × all years of census data + emigration counts) into RDF using a simple Python script (no RML toolchain required for a dissertation prototype).
2. **Load into a local triplestore** (Apache Jena Fuseki, which runs as a single JAR).
3. **Implement a minimal NL→SPARQL pipeline**: same LLM prompt structure as the existing NL→SQL path, with the schema replaced by the RDF ontology.
4. **Run the 15 competency questions on both pipelines** and compare:

| Criterion | NL→SQL (SQLite) | NL→SPARQL (Fuseki) |
|---|---|---|
| Answer correctness on 15 Qs | | |
| Median latency | | |
| Template match possible? | Yes | Not applicable |
| Handles cross-source joins | Natively | Requires graph traversal |
| 1821 census data accessible? | No (not in schema) | Yes (if uplifted) |
| Explainability of query | SQL is readable | SPARQL is readable |

This comparison directly addresses Prof O'Sullivan's framing and strengthens the CS contribution. If time does not permit a full implementation, a design study (architecture diagram, sample SPARQL queries, analysis of trade-offs) is sufficient for dissertation purposes.

---

## Evaluation Timeline

| Week | Activity |
|---|---|
| Week 3 (26 May – 1 Jun) | Run all 15 competency questions, record results, verify SQL against source data |
| Week 4 (2–8 Jun) | Pipeline reliability measurement (template hit rate, latency), answer quality scoring |
| Week 5 (9–15 Jun) | Data completeness audit, source-to-database traceability sample |
| Week 6 (16–22 Jun) | (If pursuing) Begin NL→SPARQL prototype for comparative evaluation |
| Week 9 (7–13 Jul) | Write evaluation chapter based on collected data |

---

## Evidence to Include in Dissertation

- Table of 15 competency questions with outcome classification and pipeline route
- The verified SQL for each question (can be included as an appendix)
- Pipeline reliability statistics table
- Data completeness table
- Sample PDF export output (screenshot or appendix)
- If comparative: side-by-side NL→SQL vs NL→SPARQL results table
