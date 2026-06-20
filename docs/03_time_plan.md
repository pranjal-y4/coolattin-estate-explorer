# Dissertation Time Plan

**Project:** Coolattin Estate Records Explorer  
**Candidate:** Pranjal Yadav  
**Submission Deadline:** First week of August 2026 (target: 3 August 2026)  
**Current date:** 11 May 2026  
**Weeks remaining:** ~12

---

## Phases Overview

| Phase | Dates | Focus |
|---|---|---|
| 1. System hardening | 12–25 May | Fix known gaps, verify all 15 Qs, deploy stable version |
| 2. Evaluation data collection | 26 May – 22 Jun | Run all evaluations, record results |
| 3. Dissertation writing | 23 Jun – 27 Jul | All chapters drafted and reviewed |
| 4. Final polish and submission | 28 Jul – 3 Aug | Formatting, proofreading, submission |

---

## Detailed Week-by-Week Plan

---

### Week 1 — 12–18 May 2026
**Theme: System stabilisation and supervisor plan submission**

- [ ] Send the three-component plan to Prof Declan O'Sullivan and Dr Ciarán Wallace (contribution statement, evaluation strategy, time plan)
- [ ] Verify all 15 competency questions return correct answers on the live deployed site
- [ ] Document the one known gap: 1821 census data not available — confirm whether the estate survey CSVs contain any pre-1841 population figures; if so, ingest them
- [ ] Confirm that the `population_trend_1841_1861` template's warning message is visible in the Ask page response
- [ ] Fix any keyword matching issues that prevent competency questions from hitting their target templates (test each question verbatim)
- [ ] Ensure the Azure deployment is stable with environment variables set

**Deliverable:** Supervisor plan email sent; 15 Qs verified working on live site.

---

### Week 2 — 19–25 May 2026
**Theme: Feature completeness and data quality**

- [ ] Audit the `unified_record` table: document null rates for `age`, `gender`, `holding_acres`, `children_count`, `ship_name` columns — these directly affect answer quality for Qs 1, 3, 5, 10, 15
- [ ] Review `is_widow` flag derivation: spot-check 20 source rows to confirm accuracy; document the derivation logic in the dissertation methods section
- [ ] Review `is_canada_destination` flag: spot-check against raw `arrival` text for a sample of 20 records
- [ ] Confirm `family_key` and `family_size_estimate` are populated sufficiently for Q6 (eviction family size range) — report coverage percentage
- [ ] Review heritage feature data: ensure `holy_well` and `ring_fort` features are loaded and townland-normalised correctly for Qs 12–13
- [ ] Code freeze for core functionality (no new features after this point unless evaluation reveals a critical gap)

**Deliverable:** Data quality audit report (internal working document feeding dissertation methods chapter).

---

### Week 3 — 26 May – 1 June 2026
**Theme: Evaluation run — competency questions**

- [ ] Conduct formal evaluation session: run all 15 competency questions in a clean browser session, recording for each:
  - Pipeline route (template ID or LLM fallback)
  - SQL query executed
  - Answer returned
  - NL rewrite text
  - Time to answer (from SSE stage durations)
- [ ] Independently verify each answer by running the SQL directly against the SQLite DB
- [ ] Cross-check 3–5 answers against the original source CSV/Excel files
- [ ] Classify each outcome: Correct / Partially correct / Incorrect / No answer
- [ ] Record any edge cases where keyword matching routed to wrong template

**Deliverable:** Completed evaluation table (15 questions × outcome, SQL, route, latency).

---

### Week 4 — 2–8 June 2026
**Theme: Evaluation run — pipeline reliability and answer quality**

- [ ] Run 10 additional free-form questions not in the template library to test LLM fallback path
- [ ] Record: LLM SQL error rate, self-repair rate, final answer delivery rate
- [ ] Score the 15 NL rewrites on the three quality criteria (factual consistency, historical appropriateness, hedging)
- [ ] Run data completeness query set against the database — record all coverage metrics
- [ ] Conduct source-to-database traceability exercise: trace 20 emigration records from CSV to `unified_record`
- [ ] Share a live session of the Ask page with Dr Ciarán Wallace — invite feedback on 3–5 answers from his domain perspective

**Deliverable:** Complete evaluation dataset; supervisor feedback received.

---

### Week 5 — 9–15 June 2026
**Theme: Dissertation writing — Introduction and Literature Review**

- [ ] Write dissertation Introduction chapter (~1,500 words):
  - Research problem and motivation
  - Coolattin Estate historical context
  - System overview and contribution summary
  - Dissertation structure
- [ ] Begin Literature Review (~2,500 words):
  - NL-to-SQL systems (WikiSQL, Spider benchmarks; RAG-SQL approaches)
  - Digital humanities and archival data integration (linked data for cultural heritage)
  - Irish historical data systems (VRTI, Griffith's Valuation, Landed Estates DB)
  - Knowledge graph enrichment patterns in DH applications
- [ ] Decide whether to pursue the NL→SPARQL comparative prototype (Declan's recommendation); if yes, begin setup of Apache Jena Fuseki this week

**Deliverable:** Introduction draft; Literature Review 50% complete.

---

### Week 6 — 16–22 June 2026
**Theme: Dissertation writing — Literature Review (finish) + Background**

- [ ] Complete Literature Review (remaining 50%)
- [ ] Write Background / Context chapter (~1,000 words):
  - The Coolattin Estate: historical and archival context
  - Source data provenance (estate ledgers, VRTI KG, NMS open data)
  - Why SQLite + data warehouse is appropriate for static historical records
- [ ] (If pursuing comparative) Implement minimal NL→SPARQL prototype: uplift sample dataset to RDF, load into Fuseki, test 3–5 queries
- [ ] Supervisor check-in: share Introduction and Literature Review draft

**Deliverable:** Literature Review complete; Background chapter drafted.

---

### Week 7 — 23–29 June 2026
**Theme: Dissertation writing — System Design and Architecture**

- [ ] Write System Design chapter (~2,500 words):
  - Overall architecture diagram (data sources → ingest pipeline → SQLite → Flask → frontend)
  - Data integration pipeline: source formats, normalisation, derived fields
  - Unified record schema design decisions
  - Analytics module architecture
  - Ask pipeline: four fast lanes (rule-fill / verified template / memory / embedding) → intent classification → semantic layer SQL (ANALYTICAL) / subgraph engine (RELATIONAL) / FALLBACK → read-only guardrail → SQL execution → VRTI + GraphDB enrichment → Phase 6 fusion → Phase 7 LLM rewrite → SSE streaming → PDF export
  - Frontend architecture: Leaflet maps, SSE handling, analytics rendering
- [ ] Include architecture diagram (can be drawn in draw.io or Mermaid and exported)
- [ ] Describe the data warehouse design decision and justify it (Declan's point: static data, batch refresh, appropriate for this use case)

**Deliverable:** System Design chapter complete.

---

### Week 8 — 30 June – 6 July 2026
**Theme: Dissertation writing — Implementation / Methodology**

- [ ] Write Implementation chapter (~2,000 words):
  - Data ingestion and normalisation in detail (ingest pipeline, fuzzy matching, derived fields)
  - Template library design: how the 83 verified SQL templates were constructed and validated
  - LLM integration: prompt design, schema injection, query memory, repair loop
  - VRTI SPARQL enrichment integration
  - PDF export implementation (hand-written PDF 1.4)
  - Deployment (Azure App Service, environment variable configuration)
- [ ] Include code excerpts for key components (template scoring function, VRTI enrichment call, SSE streaming loop)
- [ ] (If pursuing comparative) Write NL→SPARQL implementation section

**Deliverable:** Implementation chapter complete.

---

### Week 9 — 7–13 July 2026
**Theme: Dissertation writing — Evaluation chapter**

- [ ] Write Evaluation chapter (~2,500 words):
  - Evaluation methodology overview
  - Dimension 1: Competency question results table (all 15 Qs) with analysis
  - Dimension 2: Pipeline reliability statistics
  - Dimension 3: Answer quality scoring and discussion
  - Dimension 4: Data completeness audit
  - (If applicable) Dimension 5: Comparative NL→SQL vs NL→SPARQL results and discussion
  - Limitations and threats to validity
- [ ] Write Discussion section (~1,000 words):
  - What worked well and why
  - Where the data limits the answer quality (1821 gap, sparse age/gender fields)
  - Implications for digital humanities research infrastructure
  - Comparison with existing Irish historical systems

**Deliverable:** Evaluation and Discussion chapters complete.

---

### Week 10 — 14–20 July 2026
**Theme: Dissertation writing — Conclusion, Abstract, References**

- [ ] Write Conclusion chapter (~1,000 words):
  - Summary of contributions (CS and DH)
  - Key findings from evaluation
  - Reflection on the data warehouse approach vs triplestore approach
  - Pointer to future work (reference `04_future_scope.md`)
- [ ] Write Abstract (~300 words)
- [ ] Compile full reference list (aim for 30–50 references; include NL-to-SQL benchmarks, DH linked data papers, Irish historical sources)
- [ ] Assemble appendices:
  - A: Database schema (full DDL)
  - B: The 83 verified SQL template library (or a representative sample)
  - C: Full competency question evaluation table
  - D: Sample PDF export output

**Deliverable:** Full dissertation draft complete.

---

### Week 11 — 21–27 July 2026
**Theme: Review and revision**

- [ ] Share full draft with supervisors — request feedback specifically on:
  - Evaluation chapter (Declan: CS rigour)
  - Historical framing and competency question analysis (Ciarán: DH accuracy)
- [ ] Self-review: check every factual claim about the system against the actual code
- [ ] Review all figures and tables for consistency (column names match what the system actually outputs)
- [ ] Proofread for clarity, grammar, and academic register
- [ ] Confirm word count is within programme requirements

**Deliverable:** Revised dissertation ready for final formatting.

---

### Week 12 — 28 July – 3 August 2026
**Theme: Final polish and submission**

- [ ] Apply any final supervisor feedback
- [ ] Final proofreading pass
- [ ] Format to TCD dissertation template (title page, declaration, table of contents, page numbers)
- [ ] Export final PDF
- [ ] Submit via TCD online submission system by 3 August 2026
- [ ] Tag the Git commit corresponding to the submitted version: `git tag v1.0-dissertation-submission`

**Deliverable:** Dissertation submitted. 🎓

---

## Demo Day Preparation (parallel track)

Assuming the demo is in late July or early August, allocate approximately 4 hours within Week 11 to:

- [ ] Prepare a 10–15 minute live demonstration script covering:
  1. Home page / map — estate overview, spatial data
  2. Census page — population charts
  3. Analytics page — emigration and eviction KPIs
  4. Heritage page — landscape features
  5. Ask page — run 3–4 competency questions live (Q1, Q2, Q14, Q15 are visually impactful)
  6. PDF export
- [ ] Test the live Azure deployment immediately before the demo
- [ ] Prepare a fallback: screenshots or a recorded video in case of network issues

---

## Risk Register

| Risk | Likelihood | Mitigation |
|---|---|---|
| Azure deployment instability before demo | Medium | Keep local version runnable; record demo video backup |
| VRTI SPARQL endpoint unavailable | Medium | Ask page degrades gracefully — VRTI enrichment is parallel and non-blocking |
| Supervisor feedback requires major changes in Week 11 | Low | Share early drafts in Weeks 6 and 10 to catch issues early |
| 1821 census data not available anywhere | High (expected) | Already handled: document the limitation explicitly, do not hide it |
| LLM API (OpenRouter) key expires or rate-limits | Low | Ollama local fallback is already implemented |
| Word count overrun | Medium | Write chapters to targets; cut from implementation details first |
