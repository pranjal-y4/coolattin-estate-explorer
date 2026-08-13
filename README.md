# Coolattin Estate Records Explorer

**A Traceable LLM-Assisted Analytics and Graph-Enrichment System for Nineteenth-Century Irish Estate Records**

Pranjal Yadav — MSc in Computer Science (Intelligent Systems), University of Dublin, Trinity College, 2026
Supervisors: Professor Declan O'Sullivan and Dr Ciarán Wallace

- Repository: https://github.com/pranjal-y4/coolattin-estate-explorer
- Deployment: https://coolattin-app.azurewebsites.net
- Submission tag: `v1.0-dissertation` (commit `6f5fc4a01f6a53a18adb946c87818d38f99a7942`)

---

## Overview

Historical estate records are distributed across sources that differ in structure, terminology, geographic coverage and level of detail. The nineteenth-century Coolattin Estate collection exhibits these problems: it includes estate surveys, person records, population observations, clearance data, workhouse records and geospatial sources. Inconsistent names and the absence of stable historical identifiers make cross-source analysis difficult, while natural-language access introduces a need for answers that remain connected to the underlying evidence.

This artefact is a townland-centred system for integrating, resolving and exploring those records. SQLite provides the principal relational data layer, with source values, missingness and provenance retained during integration. A deterministic entity-resolution pipeline uses bounded candidate generation and seven interpretable evidence signals to identify plausible links between workhouse mentions and estate records without altering the original datasets. Natural-language questions are handled through an LLM-assisted analytical pipeline that proposes candidate SQL, validates it against a read-only policy and executes it before constructing an answer from the returned rows. A local NetworkX property graph, selected linked data and geospatial layers provide separately identified contextual enrichment. The web interface combines search, maps, tables, visualisations and natural-language exploration for users without specialist database knowledge.

The system is **not** a conventional document-RAG application. Structured SQL results remain the principal authority for counts, comparisons and other analytical answers. Virtual Record Treasury of Ireland (VRTI) data and the locally derived property graph supply supplementary geographic or relational context; they do not replace the relational result.

## Research questions

**Overall:** Using the nineteenth-century Coolattin Estate collection as a bounded case study, to what extent can heterogeneous historical records be integrated, resolved and enriched within a reproducible data layer that enables non-technical exploration and traceable, evidence-grounded natural-language analysis?

| SRQ | Focus | Question |
| --- | --- | --- |
| SRQ1 | Data integration | How can fragmented Coolattin Estate sources be geospatially aligned and integrated into a reproducible townland-level data layer? |
| SRQ2 | Entity resolution | How can entity-resolution methods address inconsistent place and person names, source identifiers and historical spelling variation while preserving uncertainty in the resulting links? |
| SRQ3 | Traceable QA | To what extent can a natural-language Ask system produce traceable and verifiable answers grounded in structured database results? |
| SRQ4 | Enrichment | How can geospatial and knowledge-graph enrichment add contextual information about administrative geography, connected records, population patterns and historic landscape features around a place? |
| SRQ5 | Non-technical exploration | How effectively does the web interface support non-technical exploration of person records, townlands, census demographics, historic landscape features and the wider estate context? |

SRQ1 and SRQ2 establish the integrated and reconciled data foundation. SRQ3 examines traceable access to it, SRQ4 evaluates contextual enrichment, and SRQ5 assesses the complete artefact with its intended non-technical users.

## Contributions

- **C1 — A provenance-aware, townland-centred integration model for heterogeneous historical records.** Townland is the principal integration key, but source-specific names and identifiers remain available rather than being replaced by the canonical representation. Cross-source references, field-level provenance and uncertain place relationships are represented explicitly through structures such as `townland_xref`, `field_provenance` and `match_review`.
- **C2 — A traceable architecture for language-model-assisted analysis over historical data.** The pattern separates five responsibilities: natural-language interpretation, candidate query generation, controlled execution, contextual enrichment and generated explanation. It is not a new text-to-SQL algorithm; it is an evidence-oriented way of placing probabilistic generation around a structured analytical authority.
- **C3 — Evaluated design guidance for uncertainty-aware linkage, contextual enrichment and non-technical historical exploration.** Candidate relationships retain supporting, conflicting and missing evidence with a confidence classification; source observations remain unchanged; the derived graph stays subordinate to identifiable source evidence.

## Design principles

| Principle | Design consequence |
| --- | --- |
| Deterministic factual path | Numerical claims derive from executed relational queries; prose presents rather than replaces the result. |
| Authority-aware identity | Place and person identity use separate evidence models and retain uncertainty. |
| End-to-end provenance | Source observations, canonical entities, derived links and generated output remain identifiable. |
| Visible failure | Partial, unsupported and degraded states are represented explicitly. |
| Read-oriented analytical access | Public analysis is limited to controlled, read-oriented execution. |
| Layered accessibility | A readable result is shown first, with structured evidence and technical detail available for inspection. |

The project decision heuristic is to preserve supported evidence before maximising answer completeness, and to prefer correctness and traceability over presentation convenience or latency when these objectives conflict.

## Architecture

Four logical layers organised by responsibility: **presentation**, **data**, **enrichment/resolution** and **question-answering orchestration**. Dashed components in the dissertation figures are retained in the codebase for legacy execution, comparison or optional deployment, and are not part of the final default Ask path.

### Technology stack

One Python 3.12 application handles ingestion, entity resolution, graph construction, evaluation and web serving.

| Component | Version constraint | Role |
| --- | --- | --- |
| Flask + Jinja2 | Flask ≥ 3.1.2 | HTTP routing, templates, JSON and streamed responses |
| Gunicorn | ≥ 22.0 | Azure WSGI server, 2 workers × 4 threads |
| SQLite | Python `sqlite3` | Integrated records, provenance, resolution evidence, graph tables |
| pandas / NumPy / openpyxl | ≥ 2.3 / ≥ 2.3 / ≥ 3.1.5 | Tabular ingestion and validation |
| RapidFuzz / jellyfish | ≥ 3.14 / ≥ 1.0 | Fuzzy and phonetic comparison |
| Shapely / RDFLib / NetworkX | ≥ 2.1 / ≥ 6.3 / ≥ 3.6 | Geometry, RDF inspection, in-process graph traversal |
| Leaflet / Chart.js / D3 | 1.9.4 / 4.4.1 / v7 | Maps, charts, graph visualisation |

Hand-written SQL is used instead of an ORM so that executed queries remain visible in logs, evaluation outputs and the Ask interface. VRTI, GraphDB, cloud embeddings and language-model providers remain optional or externally maintained.

### Repository layout

```
app.py                     WSGI entry point (gunicorn app:app); calls the factory
backend/                   Python application package
  app.py                   Flask application factory, blueprints, security headers
  config.py                Layered configuration; BASE_DIR anchors the project root
  extensions.py            SQLite connection handling and schema bootstrap
  routes/                  HTTP blueprints (main, census, unified, map, townlands,
                           exports, ask, kg_explore)
  services/                Domain logic: Ask pipeline, GraphRAG, entity resolution,
                           embeddings, exports, census/townland/workhouse services
  repositories/            SQL access per aggregate
  integrations/            External endpoints (VRTI, GraphDB, townlands reference)
  jobs/                    Ingestion and seeding entry points
  models/                  Dataclasses shared across layers
  analytics/               Pluggable dashboard datasets, discovered at request time
frontend/                  Presentation assets served by Flask
  templates/               Jinja2 templates
  static/                  css/, js/, images/, data/ (CSV, GeoJSON, XLSX)
data/                      Seed data, RDF/SHACL, KG context, source snapshots
scripts/                   Operational CLIs (graph build, ingest, reports, validation)
tests/                     Pytest suite
eval/                      Evaluation harness and gold standards
eval_results/              Recorded evaluation runs; legacy/ holds superseded runs
docs/                      Dissertation and technical documentation
```

`backend/` is imported as a package (`from backend.config import ActiveConfig`), so the
project root must be on `sys.path`; the CLIs under `scripts/` and `eval/` insert it
themselves. Application code resolves files from `BASE_DIR` rather than by walking
parent directories, so modules can be moved without breaking path resolution.

## Data integration (SRQ1)

Estate GeoJSON, unified person CSV, workhouse Excel records, heritage GeoJSON and selected VRTI results are combined in one SQLite database. There is no single monolithic rebuild command: core townland and census records use explicit ingest jobs, person and heritage tables use fingerprint-controlled seeders, and workhouse linkage, graph construction and RDF uplift are separate offline operations.

Principal townland ingest stages: load estate townland GeoJSON → test VRTI availability → normalise names and create local base records → add VRTI geometry, hierarchy and identifiers → upsert the canonical townland → extract estate population and clearance observations → fetch the standard census series at county scope and restrict it to the estate catalogue → persist observations and refresh metadata.

Key handling rules:

- Estate-survey and standard census observations remain separate series. Unrecorded fields stay `NULL` — a missing observation is not an observed value of zero.
- VRTI WKT geometry is parsed, repaired via `make_valid()` then `buffer(0)`, and reduced to an interior representative point (not an arithmetic centroid, which can fall outside a concave polygon).
- Coordinates are treated as WGS84; a candidate ordering is accepted only inside a broad Ireland bounding box (51.0 ≤ φ ≤ 55.5, −11.0 ≤ λ ≤ −5.0), otherwise rejected rather than silently stored.
- Polygon overlap is measured through intersection over union.
- The person/heritage seeding fingerprint is `F(D) = schema version ‖ file mtime ‖ file size` — a change indicator, not a cryptographic content hash.

The RDF uplift is a downstream representation generated from SQLite, not an independent primary route. The verified snapshot contains **189,018 triples**: 13,707 `co:Person`, 13,707 `co:Event`, 4,225 `co:Townland`, 8,033 `co:CensusRecord`, 1,211 `co:Clearance`.

## Entity resolution (SRQ2)

Workhouse mentions are linked to estate records through symmetric normalisation, bounded candidate generation, seven-signal scoring and evidence-preserving persistence. Neither source dataset is modified.

**Normalisation:** Unicode NFKD decomposition, diacritic and combining-mark removal, quotation normalisation, restricted punctuation removal, whitespace collapse, uppercase conversion, plus bounded abbreviation and surname-prefix rules. Original transcriptions are retained.

**Blocking:** phonetic-surname and place buckets, combined as a union so candidates survive where either surname or residence changed. Each pooled record must satisfy at least one admission strategy — exact normalised name, surname + forename initial, phonetic surname (Metaphone), fuzzy full name (token-sort ≥ 82), canonical/variant place, or compatible event year (≤ 10 years apart). At most **25 candidates** are retained per mention.

**Scoring:** seven signals summed to a raw score out of 60, then normalised to [0, 1].

| Signal | Max | Support | Missing/conflict |
| --- | --- | --- | --- |
| Full name | 10 | Similarity bands 10 / 7 / 4 / 0 | Lower similarity → 0 |
| Surname | 10 | Exact 10; Metaphone 7 | No agreement → 0 |
| Forename | 10 | Similarity bands 10 / 7 / 4 / 0 | Missing → neutral 5 |
| Place | 10 | Exact canonical 10; contained variant 6 | Missing → 0; disagreement recorded as conflict |
| Birth year | 5 | Diff ≤ 3 → 5; ≤ 8 → 3 | Missing → 0; diff > 20 → impossible |
| Gender | 10 | Agreement 10 | Missing → neutral 5; disagreement is a conflict |
| Timeline | 5 | Compatible progression 5 or 3 | Implausible progression can be impossible |

Three parallel outputs are recorded: `evidence` (supporting comparisons), `conflicts` (present but incompatible values) and `missing_evidence` (fields that could not be compared).

**Asymmetric conflict override:** an impossible birth-year or timeline conflict caps the score at 0.39, forcing the no-match band. No positive signal can force a match.

**Confidence bands:** `CONFIRMED_MATCH` ≥ 0.75, `POSSIBLE_MATCH` ≥ 0.60, `WEAK_CANDIDATE` ≥ 0.40, `NO_MATCH` < 0.40. These are implementation bands, not calibrated probabilities or archival verification.

**Sparse-record ceiling:** a record with perfect name evidence but no place, age or timeline data reaches at most 35/60 ≈ 0.583 and cannot enter the possible-match band. This protects precision at the cost of recall in the sparse sheet.

Persistence tables: `source_mentions`, `entity_resolution_candidates`, `workhouse_unified_links`, `entity_resolution_decisions`.

## Ask pipeline (SRQ3)

The submitted code retains two implementations. The **default orchestrated path** (`ASK_USE_NEW_PIPELINE=true`) uses a fixed direct sequence with LLM-generated candidate SQL and local NetworkX GraphRAG. The **retained routed path** uses intent routing across deterministic rules, templates and query memory with remote graph operations; earlier evaluation artefacts describe that configuration.

Default pipeline stages:

1. Resolve place and person references → entities and warnings
2. Generate candidate SQLite SQL → SQL and provider metadata
3. Retrieve local graph context where possible → bounded neighbourhood
4. Validate, repair and execute SQL → columns, rows and provenance
5. Retrieve optional VRTI context → hierarchy and external metadata
6. Assemble structured and contextual evidence → evidence payload
7. Build deterministic answer and guarded synthesis → final response

**SQL guard.** Rejects empty output, multiple statements, and anything not beginning with `SELECT` or `WITH`. Blocked keywords: `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `CREATE`, `ATTACH`, `DETACH`, `PRAGMA`, `REINDEX`, `VACUUM`, `TRUNCATE`, `REPLACE`. The guard is deliberately conservative — textual blocking also rejects the valid `REPLACE()` function and may reject a blocked word inside a quoted string. It is an application-level textual safeguard, not AST parsing or a formal proof of query safety.

**Bounded repair layers:** syntax/safety rejection → one corrected candidate; semantic gap → semantically corrected query; SQLite runtime error → one repaired query. If all repairs fail, heuristic SQL is used *only* when `ASK_ALLOW_HEURISTIC_FALLBACK=true`. The default is `false`, so unresolved failure produces a diagnostic row rather than a fabricated count.

**GraphRAG.** Materialised graph tables load into a NetworkX `MultiDiGraph` once per process. Exact canonical seeding is preferred (e.g. `townland:AGHOWLE LOWER`); otherwise a 1,024-dimensional question vector is ranked against normalised node vectors by exact matrix multiplication (not HNSW). The seed is expanded bidirectionally to at most two hops and pruned to at most 120 nodes and 200 edges before linearisation.

**Numerical evidence gate.** Three-or-more-digit numeric tokens in generated prose must be a subset of the permitted tokens from the question and evidence payload. Failure triggers stricter regeneration, provider fallback, or rejection of the rewrite — in which case the deterministic answer is shown. This is visible degradation, not silent substitution.

**Provider cascade:** Claude → Grok → OpenRouter → Ollama, with failover on rate limit, timeout, transport error, empty output or numerical-gate rejection.

**Streaming.** The server emits SSE-style progress, terminal result and terminal error messages. The browser consumes the POST response through `fetch()` and a `ReadableStream`, because the standard `EventSource` API does not support a POST body. Streaming is stage-based rather than token-based.

## Enrichment (SRQ4)

Four context dimensions, produced by separate mechanisms and kept distinct from the executed SQLite rows:

| Context | Mechanism |
| --- | --- |
| Administrative geography | Live SPARQL against the VRTI Virtuoso endpoint |
| Connected records | Local NetworkX traversal (default) or live VRTI/GraphDB SPARQL (retained path) |
| Population patterns | Bound-parameter SQL over `census_record` |
| Historic landscape | Normalised townland-name equality in Ask; browser-side point-in-polygon and Haversine on the Historic Landscape page |

The VRTI client uses a 30-second timeout with a five-minute process-local cooldown after failure (not shared between Gunicorn workers). Haversine distance uses R = 6371 km and, where a townland is represented by a centroid, is point-to-point rather than minimum distance to the polygon boundary. The Ask and browser heritage association methods can disagree; the system keeps them separate and does not reconcile disagreement in a dedicated quality table.

Response fields separate factual from contextual evidence: `columns` / `rows` / `row_count` (primary), versus `kg_context`, `graphrag_context`, `subgraph_context`, `discrepancies` / `fusion`.

## Web interface (SRQ5)

Server-rendered Jinja2 templates with page-specific JavaScript controllers. Ordinary pages consume JSON; Ask consumes a streamed POST response.

| Route | Template | Role |
| --- | --- | --- |
| `/` | `index.html` | Map, surname and townland filtering, family groups, record details |
| `/census` | `census.html` | Population choropleth, year selection, townland detail |
| `/heritage` | `heritage.html` | Historic-landscape containment and proximity exploration |
| `/ask` | `ask.html` | Natural-language questions, streamed progress, traceable results |
| `/about` | `about.html` | Project and source context |
| `/info` | `info.html` | Narrative estate and clearance information |
| `/kg-explore`, `/explore-knowledge` | `kg_explore.html` | D3 relationship exploration |

The Ask result panel implements layered traceability in seven sections: answer and warnings → SQLite result → graph context → explainability (tables, filters, scope, strategy, sources) → generated queries → technical details → feedback.

## Evaluation

Automated evaluations were run on 3 August 2026 against the authoritative root-level `coolattin.db`.

### SRQ1 — Data integration

| Measure | Expected | Observed | Coverage |
| --- | --- | --- | --- |
| Unified estate records | 13,707 | 13,707 | 100% |
| Workhouse source mentions | 8,214 | 8,214 | 100% |
| Estate townlands processed | 152 | 152 | 100% |
| Townlands with geometry or centroid | 152 | 151 | 99.3% |
| Townlands with VRTI identifier | 152 | 132 | 86.8% |
| Townlands with OSM identifier | 152 | 132 | 86.8% |
| Townlands with OSI identifier | 152 | 83 | 54.6% |

A read-only ingestion dry run reproduced `unified_record` and `source_mentions` exactly, but the live VRTI census query returned no rows: `census_record` would have fallen from 8,033 preserved rows to 1,462, and `clearances_record` differed by +20. A destructive rebuild was therefore deliberately not performed.

### SRQ2 — Entity resolution

35 curated reference pairs; 4 labelled `UNCERTAIN` and excluded, leaving 31 scored.

| Measure | Result |
| --- | --- |
| True positives / False positives | 13 / 6 |
| False negatives / True negatives | 0 / 12 |
| Precision | 0.684 |
| Recall | 1.000 |
| F1-score | 0.813 |

Blocking retained 13/13 known positives (pairs completeness 1.000) with a reduction ratio of 0.998. Recurring false-positive pattern: shared surname, townland and compatible temporal evidence with insufficient forename or gender evidence to reject.

Place disambiguation preserved `COOLATTIN`, `COOLATTIN PARK` and `DEERPARK ED COOLATTIN` as distinct canonical entities, and seven distinct `BALLINACOR` names. Authority-identifier consistency was 146/150 (97.3%).

### SRQ3 — Traceable question answering

30-question single-pass benchmark across 13 categories; 18 questions were scalar-scorable.

| Outcome | Count | Percentage |
| --- | --- | --- |
| Numerically correct | 15 | 83.3% |
| Incorrect | 2 | 11.1% |
| Partially correct comparison | 1 | 5.6% |

All 30 runs retained the SQL-generation or diagnostic strategy, the generated/executed/diagnostic SQL, a deterministic answer and end-to-end elapsed time.

Measured failures: `evic_01_total` counted 4,108 person records with an eviction flag rather than summing the 7,763 clearance observations; `geo_01_total_townlands` counted 516 distinct `townland_norm` text values instead of the 152 canonical estate townlands; `cmp_01_emigration_vs_kg` produced different SQL across two runs. One benchmark case also described `clearances_record` as knowledge-graph evidence, showing that trace presence and trace correctness require separate evaluation.

Three questions deliberately requested unrepresented information. One returned an appropriate no-data response; the crop and religion questions returned genuine but semantically tangential records — substitution rather than fabrication.

Latency: min 6.32 s, p50 17.45 s, mean 21.27 s, p90 42.62 s, p95 43.56 s, max 72.57 s. Long-tail latency was associated with regeneration and provider fallback rather than SQLite execution.

### SRQ4 — Enrichment coverage

| Context type | Available | Represented | Coverage | Relation |
| --- | --- | --- | --- | --- |
| Administrative geography | 152 townlands | 133 | 87.5% | `WITHIN` |
| Workhouse relationships | 5,134 links | 140 | 2.7% | `LINKED_TO` (stale graph) |
| Census observations | 8,033 | 8,033 | 100% | `HAS_OBSERVATION` |
| Clearance observations | 1,211 | 1,211 | 100% | `HAS_OBSERVATION` |
| Person-to-place | 13,707 persons | 9,095 | 66.4% | `LOCATED_IN` |
| Landscape features | 366 | 0 | 0% | Not represented |

The workhouse figure exposed a freshness problem: the graph had been built against an earlier entity-resolution state.

### SRQ5 — Pilot usability

Seven consenting adult participants (3 historical researchers/academics, 2 archivists/heritage professionals, 2 technical). 18 custom five-point statements, 126 valid item responses, overall mean **4.00/5**. Not the standard SUS, so no SUS score is reported.

Highest: information sufficient for historical exploration 4.57; map/geospatial view 4.43; satisfaction 4.29; found relevant information 4.29; interface easy to understand 4.29. Lowest: Ask helped explore the archive 3.57; Ask answer understandable 3.14; Ask made the source of the answer clear 3.14.

### Silent-degradation cases

The evaluation's methodological finding is that successful execution is necessary but insufficient. Five components executed successfully while representing incomplete or misleading states: VRTI census retrieval returning zero rows, stale graph freshness, Ask semantic substitution, provenance mislabelling and provider fallback latency.

## Scope and assumptions

- **Geographic/temporal.** The principal Coolattin Estate, County Wicklow. Townland is the primary geographic unit. The 1842 survey was associated with 153 townlands. Estate population observations: 1827, 1839, 1848, 1850, 1860, 1868. Clearances: 1847–1856. Standard census: 1841, 1851, 1861, 1871, 1881, 1891. No values are interpolated for missing years.
- **Evidential.** The unified estate file contains 13,707 person-level source records — source-derived mentions, not necessarily 13,707 distinct individuals. Absence from the data is not proof of non-existence; missing numerical values are not interpreted as zero.
- **Identity.** `CONFIRMED_MATCH` and similar labels are algorithmic, not historical proof. Person nodes in the local graph represent record entries, not necessarily unique individuals.
- **Analytical.** Ask is not a general question-answering system for Irish history. Public-facing interfaces are read-only with respect to the historical corpus.
- **Enrichment.** The local property graph is derived from the integrated project data, so agreement between it and SQLite is not independent corroboration. Heritage proximity is a spatial association, not a documented historical relationship.
- **Reproducibility.** Strongest for the preserved local snapshot, deterministic transformations, stored SQL and recorded result rows. LLM output varies across providers and versions; live VRTI enrichment varies with endpoint availability. The Python environment is documented but not hash-pinned, and no complete repeated clean-ingestion experiment was performed.

## Reproduction

The authoritative database is stored via Git LFS. There is no single command that rebuilds every artefact.

1. Clone, `git checkout v1.0-dissertation`, `git lfs pull` to materialise `coolattin.db`, verify expected inputs
2. Create a Python 3.12 environment and install the documented local requirements
3. Provide non-secret configuration (database path, Flask settings, optional services)
4. Create or migrate the core SQLite schema
5. Run the explicit townland, census and clearance ingestion
6. Seed the unified person and heritage tables
7. Run the workhouse entity-resolution process where links are required
8. Rebuild the local graph from the intended relational snapshot
9. Regenerate the Turtle file where RDF or GraphDB comparison is required
10. Start Flask and verify ordinary pages and the Ask route
11. Execute the relevant evaluation inputs using the recorded configuration

A successful schema initialisation does not establish that source tables, links or graph artefacts have been populated.

### Submitted artefact state

| Item | Value |
| --- | --- |
| Submission commit | `6f5fc4a01f6a53a18adb946c87818d38f99a7942` |
| Tag | `v1.0-dissertation` |
| Database | `coolattin.db` (repository root, Git LFS) |
| Checksum | `e719a158bec8fe51b1160ed9370140579b3c64405ac34ca1465ecc49b1d765ea` |
| Size | 188,264,448 bytes (179.54 MiB) |
| Last data write | 10 August 2026, 08:40:17 IST; WAL checkpointed 11 August 2026, 14:51:38 IST |
| Graph snapshot | Built 7 August 2026, 09:43:16 — 49,081 nodes, 69,302 edges, 3,501 communities, `BUILD CLEAN` |
| Ask evaluation run | 3 August 2026, 23:48 IST; `ASK_USE_NEW_PIPELINE=true`; Anthropic `claude-sonnet-4-6`; GraphRAG enabled |

The submitted database post-dates the evaluation run, and the graph was rebuilt between them. The preserved evaluation JSON and console trace are the primary records of that run; re-executing the harness against the submitted database may legitimately produce different results.

### Critical runtime controls

| Control | Default | Effect |
| --- | --- | --- |
| `ASK_USE_NEW_PIPELINE` | `true` | Selects the final orchestrated Ask path over the retained routed architecture |
| `ASK_ALLOW_HEURISTIC_FALLBACK` | `false` | Prevents keyword-derived emergency SQL unless explicitly enabled |
| `GRAPHRAG_ENABLED` | `true` | Enables local NetworkX retrieval when a suitable seed is available |
| `GRAPHDB_ENABLED` | `true` | Makes GraphDB available to retained paths; does not activate numerical fusion in the direct route |
| `ASK_LLM_PROVIDER` | `auto` | Selects or prioritises the candidate-SQL provider |

Configuration precedence: process environment → `.env.local` → `.env` → code defaults. Azure Application Settings therefore override local files. Secrets live in App Service settings, not committed files.

## Deployment

A single Azure App Service application served by Gunicorn (2 workers × 4 threads). Flask, SQLite and the local NetworkX graph run inside the App Service process; VRTI, GraphDB and cloud model providers are accessed over the network. Deployment authenticates through OpenID Connect, selects the trimmed Azure requirements, packages the repository and deploys a ZIP; dependency installation is performed by Azure Oryx. The workflow does not provision a co-located GraphDB, pgvector service or Ollama process. Enabling a feature flag does not provision the corresponding external service.

## Evaluation evidence

| SRQ | Primary repository evidence |
| --- | --- |
| SRQ1 | `eval_plan/scripts/rq1_data_integration.py`; `eval_plan/evidence/RQ1_raw_output.txt` |
| SRQ2 | `eval/er_gold.csv`; `eval_plan/scripts/rq2_entity_resolution.py`; `eval_plan/evidence/RQ2_raw_output.txt`; `eval_results/authority_id_consistency.md` |
| SRQ3 | `eval_plan/scripts/rq3_full30.py`; `eval_plan/evidence/RQ3_full30_raw_output.json`; `eval_plan/evidence/RQ3_full30_console_output.txt`; `eval_plan/evidence/RQ3_pilot_raw_output.json` |
| SRQ4 | `eval_plan/scripts/rq4_enrichment.py`; `eval_plan/evidence/RQ4_raw_output.txt` |
| SRQ5 | Anonymised aggregate ratings and qualitative themes (participant-level raw data excluded from the public repository) |

Credentials, API keys, local secrets and participant-level raw response data are excluded from the public repository and dissertation package.

## Ethics

Ethical approval was obtained; participant information, consent and eligibility gates, the pilot procedure and the questionnaire are preserved in Appendix D of the dissertation. Generative-AI tools were used in a limited and supervised capacity for language and clarity, and as an exploratory aid; all outputs were critically reviewed and independently verified by the author.

## Limitations

The artefact does not remove uncertainty from the historical material or replace archival interpretation. It provides an inspectable computational environment through which the available evidence, generated queries and candidate relationships can be examined. Findings are bounded by the Coolattin case study and are not presented as universally validated solutions for historical archives.