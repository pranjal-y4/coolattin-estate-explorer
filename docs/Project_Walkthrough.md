# Coolattin Estate Records Explorer — Project Walkthrough

*A plain-language explanation of what was built, how it works, the decisions made, and why. Written for someone reading the code for the first time — no prior knowledge of the historical data or the codebase assumed.*

---

## What Is This Project?

The **Coolattin Estate Records Explorer** is a web application that brings together historical records from the Coolattin Estate in County Wicklow, Ireland — one of the largest landlord estates of the Famine era (mid-19th century). These records cover tenants, evictions, emigration, and census population data from roughly 1827 to 1891.

The goal was to make this data **searchable, visual, and explorable** — not just a spreadsheet. Someone researching their family history or studying the Famine can type a natural-language question, see population maps, browse tenant records, and export a formatted PDF report. All from a browser.

This was built as a **Masters Dissertation project** at Trinity College Dublin, which meant two non-negotiable constraints:
- **Reproducible** — anyone with the code can run it and get the same results
- **Transparent** — every claim must be traceable back to a known data source

Those two constraints shaped nearly every technical decision.

---

## Where the Data Comes From

Before anything else, it helps to understand the raw ingredients:

| Source | What it contains | How it is used |
|---|---|---|
| **Coolattin Estate GeoJSON** | Map boundaries for all 152 townlands; estate population surveys (1827–1868); eviction counts (1847–1856) | The backbone of the map and eviction analytics |
| **VRTI Knowledge Graph** | Standard national census data (1841–1891); townland metadata (Gaelic names, parish, barony, geographic coordinates) | Enriches local data with nationally standardised records |
| **Unified Estate CSV** | ~13,700 individual tenant records — names, dates, locations, emigration ships, destinations | Powers the "search people" feature and the LLM Q&A |
| **Workhouse Excel Register** | Pauper admission records from the local Baltinglass Workhouse | Linked to estate records via entity resolution |
| **NMS Heritage Data** | Archaeological monuments, holy wells, ring forts across Wicklow | Displayed as an overlay on the heritage map |

The key challenge was that these sources use **different naming conventions** for the same places. "Ballinacor", "Ballinacor North", and "BALLINACOR" all refer to the same townland — but a computer treats them as different. A lot of early work went into building a normalisation system that converts everything to a single canonical form (uppercase, no qualifiers) and maintains a lookup table of known spelling variants.

---

## How the Application Is Structured

Think of the application in four layers, each with a single job:

```
[User's browser]
      ↕  HTML pages, API calls, Server-Sent Events
[Routes — the front door]
      ↕  delegates work to services
[Services — the decision-making layer]
      ↕  reads and writes data via repositories
[Repositories — the data layer (all SQL lives here)]
      ↕  SQL queries with parameterised placeholders
[SQLite database]   ←→   [VRTI Knowledge Graph]   ←→   [LLM synthesis chain]
```

**Routes** are thin — they receive a request, call a service, and return a response. They contain almost no logic themselves.

**Services** contain all the decision-making — do we have fresh data? Does this question match a known template? Should we call the external knowledge graph or use what's in the database?

**Repositories** contain all the SQL queries. The rule is simple: no raw SQL outside the repository layer. This makes it trivially easy to audit every database query in one place.

**The database** is a single SQLite file (`coolattin.db`, currently ~65 MB) with 17 tables covering person records, population data, evictions, workhouse entity resolution, and the in-process knowledge graph.

---

## The Database Decision: No ORM

Most modern web projects use an **ORM** (Object-Relational Mapper) — a library that hides the SQL behind Python objects so you don't have to write queries manually. This project deliberately chose not to use one.

**Why:** For a dissertation project, every query must be readable and auditable. With an ORM you get convenient code but the actual SQL being run is hidden. With raw `sqlite3`, every query is a plain text string that anyone can read and verify. The trade-off was more boilerplate code, but full control and zero magic.

**The trade-off:** More work upfront (writing every query by hand), but full control. For a research project where you have to explain your methodology, raw SQL is the honest choice.

---

## The Core Data Flow: DB-First, KG-Second

The VRTI Knowledge Graph (VRTI = Virtual Record Treasury of Ireland) is an external research database hosted by another institution. It can be slow (500 ms–2 s per query) or occasionally unavailable.

So the system uses a **caching layer**: on the first ingest, it calls VRTI, saves the results to local SQLite, and records when that happened. On every subsequent request, it serves from the local database instantly. It only calls VRTI again when the data is stale (configurable, default 7 days in development).

If VRTI is completely unavailable, it falls back to bundled CSV seed files. The user still gets a response — just potentially older data.

```
Request comes in
    ↓
Is data in local DB? → Yes, is it fresh? → Yes → Serve from DB (< 1 ms)
                                          → No  → Serve from DB + refresh in background
                      → No → Call VRTI KG → Save to DB → Serve result
                                          (if VRTI is down → serve from seed CSV)
```

---

## The Eight Pages

### 1. Home / Map (`/`)

The landing page is a **choropleth map** — each of the 152 Coolattin townlands is drawn as a coloured polygon, with colour intensity representing population or evictions depending on which data layer is selected. Built with **Leaflet.js**.

GeoJSON and unified data are loaded in **parallel** on page load to halve the initial load time.

### 2. Census Explorer (`/census`)

A year slider moves from 1827 to 1891, watching the choropleth update as population rises and falls. Clicking a townland opens a sidebar showing the year-by-year breakdown.

This page handles two different data formats: estate surveys (1827–1868) recorded total population only, while national censuses (1841–1891) recorded males, females, inhabited houses, and uninhabited houses separately. Both formats are stored in the same `census_record` table and rendered appropriately by the `source` column.

### 3. Ask (`/ask`) — the most complex page

The Ask page lets users type a question in plain English and get a structured answer back. This is the most technically sophisticated part of the project. More on this below.

### 4. Analytics (`/analytics`)

Pre-computed dashboards showing KPIs and charts for emigrations, evictions, workhouse connections, and tenancy patterns. Built as **pluggable modules** — each dataset has its own self-contained module file in `analytics/`. Adding a new analytics dashboard means creating one new file; nothing else changes.

### 5. Heritage Map (`/heritage`)

An overlay of archaeological monuments and holy wells from the National Monuments Service open data, layered on the Wicklow base map. Users can filter by monument type and click for details.

### 6. KG Explore (`/kg-explore`)

A knowledge graph visualisation page built for the dissertation's D8 deliverable. It runs the same historical query as both a SQL query against the local database and a SPARQL query against the GraphDB ontology endpoint, showing results side-by-side with timing. A D3.js force graph shows the 152 townlands connected by their geographic hierarchy.

### 7. About (`/about`) and Info (`/info`)

Project information and technical details pages.

---

## The Ask Page: Walking Through the LLM Pipeline

This is where the most engineering effort went. The pipeline went through multiple design iterations and is now a seven-phase orchestrated system implemented in `ask_service.py` (10,192 lines).

When a user submits a question, it passes through these phases in order. Results stream back to the browser in real time via **Server-Sent Events** — the user sees each stage as it completes.

### The Core Principle: LLM Last, Not First

The most important architectural decision was that **the LLM never generates numbers**. All counts, totals, and aggregates come from deterministic SQL queries against the local SQLite database. The LLM's job is to rewrite the SQL result into readable prose.

This matters because historical data demands accuracy. A 5% LLM error rate might be acceptable in a general chatbot. It is not acceptable when someone is researching their great-great-grandmother's eviction record.

### Four Fast Lanes (Before Any LLM Call)

Before the pipeline even considers calling the LLM for SQL generation, four "fast lanes" are checked in order. The first match short-circuits everything else:

**Lane 1 — Rule-based slot-fill (0 LLM calls, < 5 ms)**
The question is matched against 14 defined metrics (emigration count, eviction count, population, tenancy count, etc.) using keyword sets. If the match is confident enough (≥ 0.80), the pipeline compiles a deterministic SQL query directly from the matched metric and filters. No LLM is called. This handles approximately 70% of all analytical questions.

Example: "How many emigrants left Aghowle in 1852?" → metric: `emigration_count`, filters: `townland=AGHOWLE LOWER, year=1852` → SQL compiled directly.

**Lane 2 — Verified template match (confidence = 1.0)**
81 pre-written SQL templates cover common research questions. If the question matches one of the 15 templates in `VERIFIED_ANALYSIS_TEMPLATE_IDS` (by required keyword scoring), the pre-written SQL is used directly. 7 of these 15 templates also emit a Chart.js visualisation spec.

**Lane 3 — Direct memory reuse**
When a user gives a thumbs-up on an answer, the question→SQL pair is saved to `ask_query_memory`. Future similar questions (measured by token-sort-ratio + cosine similarity) reuse the stored SQL directly. Over time, the system builds a validated query library without any manual curation.

**Lane 4 — Embedding template retrieval**
TF-IDF unigram+bigram cosine similarity over all templates, merged with dense embeddings (BGE/Voyage/Cohere) via Reciprocal Rank Fusion (RRF). If the top hit scores above the threshold (0.68) and required keywords match, the template SQL is used directly.

In the 75-question formal evaluation, all 75 questions were answered via the fast lanes or the semantic layer — the LLM was never called for SQL generation.

### Phase 5: Intent Classification

If no fast lane fires, `intent_router.py` classifies the question into one of four routes:

- **ANALYTICAL** — aggregate counts, trends, statistics → goes to the semantic layer
- **RELATIONAL** — hierarchy, adjacency, parish membership, heritage descriptions → goes to the subgraph engine
- **COMPARATIVE** — cross-source comparisons ("compare X vs Y") → fan-out to both SQL and knowledge graph
- **FALLBACK** — everything else → LLM-generated SQL

This routing step means the LLM is never called for SQL on a question that can be answered deterministically.

### The Semantic Layer

For ANALYTICAL questions, `semantic_layer.py` attempts to map the question to a validated **slot-fill struct** using keyword matching (no LLM). If successful, it compiles a deterministic SQL query and an equivalent SPARQL query — both guaranteed valid because they are assembled from a typed vocabulary of metrics, dimensions, and filters, not generated free-form.

If rule-based filling fails, the LLM is prompted to return only a JSON slot-fill (not raw SQL); the compiler then turns that into SQL. This separation means the LLM never writes SQL directly in the semantic layer path.

### The Subgraph Engine and GraphRAG

For RELATIONAL questions, `subgraph_engine.py` traverses two knowledge graphs:

1. **VRTI SPARQL** — queries the external knowledge graph for the townland's parish hierarchy, neighbouring townlands, and external links (logainm.ie, OSM)
2. **GraphDB** — queries the local GraphDB instance running the Coolattin (`co:`) ontology for entity neighbourhoods
3. **In-process GraphRAG** (`graphrag.py`) — traverses a NetworkX property graph built from the unified records (49,081 nodes, 64,308 edges). Questions are embedded using BGE-large-en-v1.5 (or Voyage AI on Azure) to find the most relevant starting nodes, then BFS traversal expands 2 hops outward. The resulting subgraph is linearised into a compact text block for the synthesis LLM.

**Key rule:** the KG and GraphRAG provide **qualitative context** only. Count and aggregate answers always come from the SQL path. The linearised subgraph is passed to the LLM to *read* context — not to answer numerical questions. The 75-question evaluation confirmed that enabling GraphRAG produced zero numeric changes in answers (purely additive enrichment) with only +46 ms latency overhead at p90.

### Identity Resolution

`identity_resolver.py` runs after SQL execution for questions involving person names. It uses a three-layer model:

- **Mention** — one immutable row per name occurrence in a source record
- **Person** — an inferred individual linked to one or more Mentions via a `SAME_AS` relationship with a confidence score
- **Factoid** — a reified claim (mention, property, value, source) that preserves contradictory records without hard-merging

Scoring uses phonetic blocking (Metaphone on surname) then Jaro-Winkler name similarity + geographic proximity + temporal plausibility + family co-occurrence.

This lets the answer say **"3 distinct individuals called John Murphy"** instead of silently collapsing them into one.

### Multi-Model LLM Synthesis Chain

The final stage assembles SQL results, KG enrichment, and GraphRAG context into a readable answer. The synthesis LLM is invoked via a four-provider chain:

1. **Claude** (Anthropic) — first priority, highest quality
2. **Grok** (xAI) — second priority
3. **OpenRouter** — third priority (cloud, OpenAI-compatible API)
4. **Ollama** (local) — fully offline fallback for demos without internet

Each provider is tried in order. Failure at any stage silently falls to the next. If the entire chain fails, the raw SQL result is returned with a note.

The synthesis LLM is instructed to never introduce numbers not present in the SQL rows. If it does, the raw result is used instead — a validation step that prevents hallucinated facts from reaching the user.

### What the User Sees

All phases stream back as Server-Sent Events. The user sees intent routing, retrieval, SQL execution, KG query, synthesis — each appearing progressively. Each stage is labelled with a strategy tag (`rule_fill`, `verified_analysis`, `memory_reuse`, `slot_fill_llm`, `subgraph`, etc.) so the user always knows exactly how the answer was produced.

---

## The Workhouse Entity Resolution Subsystem

Separate from the Ask pipeline is a dedicated entity-resolution subsystem that links workhouse admission records to unified estate records. It is deliberately separate because the two tasks are fundamentally different:

- **Ask pipeline:** retrieve semantically relevant context for natural-language questions
- **Workhouse ER:** produce explicit, reviewable candidate identity links with transparent evidence scores

**How it works:**

1. Load the workhouse pauper register (Excel, ~500 rows)
2. Normalise each name: unicode → uppercase → remove editorial annotations → expand abbreviations (JNO→JOHN, WM→WILLIAM) → Metaphone phonetic encoding
3. For each normalised mention, generate up to 25 candidate matches from the unified records using blocking strategies (exact name, phonetic surname, place+name)
4. Score each candidate on 7 signals over a 60-point scale (name similarity, surname, forename, townland, birth-year, gender, timeline)
5. Assign confidence bands: CONFIRMED_MATCH (≥ 0.75) / POSSIBLE_MATCH (0.50–0.74) / WEAK_CANDIDATE (< 0.50)
6. Persist all candidates and scores to four SQLite tables for review

**Result:** 140 confirmed links between workhouse records and estate records. Each link includes supporting evidence, missing evidence, and conflicting evidence fields — no silent merges, full audit trail.

**Why this matters:** The Coolattin records include many common Irish surnames (Murphy, Ryan, Brien). Without entity resolution, a query for "John Murphy" conflates dozens of distinct individuals. This subsystem demonstrates a principled approach to record linkage: transparent scoring, reproducible normalisation, no black-box ML model.

---

## The Knowledge Graph Exploration Page

The `/kg-explore` page was built specifically for the dissertation's D8 deliverable (RQ6: SQL vs SPARQL comparison). It runs the same query as both a SQL query against the local SQLite database and a SPARQL query against the local GraphDB instance, shows results side-by-side with timing, and renders a D3 force graph of the 152 townlands connected by their geographic hierarchy.

The `semantic_layer.compile_sparql(slot_fill)` function generates SPARQL from the same `SlotFill` struct that produces SQL — making the comparison precisely equivalent, not an approximation.

---

## Technologies Used and Why

| Technology | What it does | Why chosen |
|---|---|---|
| **Flask (Python)** | Web framework | Lightweight, transparent, no magic. Easy to reason about. |
| **SQLite** | Local database | Single file, zero setup, fully portable. An examiner can open it directly. |
| **VRTI SPARQL** | External knowledge graph queries | Authoritative source for nationally standardised Irish historical data. |
| **Claude / Grok / OpenRouter / Ollama** | LLM for synthesis | Multi-provider chain ensures the demo works online AND offline. |
| **NetworkX** | In-process property graph | No external graph server needed; loads from SQLite at startup. |
| **BGE-large-en-v1.5 / Voyage AI** | Dense embeddings | 1024-dim vectors for GraphRAG vector seed and template retrieval. |
| **Leaflet.js** | Interactive maps | Open source, no API key required. |
| **D3.js** | Force-directed graph | Flexible SVG graph rendering for the KG explore page. |
| **Chart.js** | Bar/line charts | Simple, well-documented, no build step. |
| **rapidfuzz** | Fuzzy string matching | Fast and accurate for townland name normalisation and ER scoring. |
| **jellyfish** | Phonetic algorithms | Metaphone encoding for phonetic blocking in workhouse ER. |
| **openpyxl** | Excel generation | Pure Python, no external dependencies. |
| **Vanilla JavaScript** | Frontend behaviour | Deliberately chosen over React/Vue — 8 pages don't need a framework. |
| **Jinja2** | HTML templating | Built into Flask. Keeps HTML logic in templates, not JavaScript. |

### The "No Frontend Framework" Decision

Every modern web project is expected to use React, Vue, or similar. This project chose not to, for three reasons:

1. **Complexity vs. benefit:** 8 pages with independent functionality. A framework adds a build step, dependency manager, and component architecture for no real gain.
2. **Transparency:** Vanilla JavaScript is readable by anyone. You don't need to know React's lifecycle hooks to understand `ask.js`.
3. **SSE streaming:** The `EventSource` API for Server-Sent Events works cleanly in plain JavaScript. A framework would add an abstraction layer without adding value.

The trade-off: the JavaScript files are more imperative and repetitive than component-based code. Some map utilities are duplicated between pages. This was a known, accepted trade-off.

---

## Key Architectural Decisions and Trade-offs

### 1. SQLite vs a Database Server

**Decision:** SQLite.

**Why:** Zero setup. Reproducible — the database is a single file that can be handed to an examiner, copied for backup, or opened directly. No PostgreSQL instance needed to run the application.

**Trade-off:** SQLite doesn't support concurrent writes well. For a production application with many simultaneous users who might trigger ingest jobs, it would be a bottleneck. For a dissertation demo with a handful of concurrent users, it's completely fine. WAL mode handles concurrent reads during any write window.

### 2. Deterministic First, LLM Last

**Decision:** Four fast lanes before any LLM SQL call.

**Why:** Historical data demands accuracy. An aggregate count in an emigration record must be exactly right. The pipeline checks rule-based slot-fill, pre-written templates, approved memory, and embedding retrieval before ever touching the LLM for SQL generation. In the 75-question evaluation, **zero LLM SQL calls were needed** — all questions were answered deterministically.

**Trade-off:** The deterministic layers require upfront effort to specify metrics and templates. But each compiled query is auditable. The LLM is only invoked for the FALLBACK lane — questions that don't match any known pattern.

### 3. Multi-Provider LLM Chain

**Decision:** Claude → Grok → OpenRouter → Ollama, in priority order.

**Why:** Academic demos happen offline. API services go down. The research must be demonstrable without a live internet connection. The four-provider chain ensures the synthesis LLM always produces an answer — even with no API keys, the local Ollama fallback works.

**Trade-off:** Local LLMs (Ollama) are slower and less capable than cloud models. For answer synthesis from structured SQL results, even a smaller model works well with good context.

### 4. Hand-Written PDF vs a Library

**Decision:** Hand-written PDF 1.4 format.

**Why:** PDF is ultimately a structured text format. Writing it by hand avoids a dependency on `reportlab`, `fpdf`, or `weasyprint` that could break or change between environments. The hand-written generator supports exactly the features needed (text, tables, basic layout).

**Trade-off:** Limited to features explicitly implemented. Cannot handle complex layouts. For this use case, that was sufficient.

### 5. In-Process GraphRAG vs External Graph Server

**Decision:** NetworkX graph loaded from SQLite at startup. No Neo4j, no GraphDB for the property graph.

**Why:** Zero infrastructure dependencies. The graph is rebuilt from the same SQLite data that powers everything else. It loads at startup and stays in process memory. No external server to configure, monitor, or connect to.

**Trade-off:** ~17 s cold start on first request (BGE model loading). Subsequent requests hit the in-memory graph in < 1 ms. The Voyage AI path on Azure has no cold start because there's no model to load.

---

## What Was Learned Along the Way

**Data normalisation is the hardest problem.** Before any feature could be built, all sources had to agree on the same place names, date formats, and record identifiers. The townland alias system — a lookup table mapping dozens of spelling variants to canonical names — was built incrementally as mismatches were discovered. This took more time than any other single task.

**Graceful degradation has to be designed in from the start.** Every external dependency — VRTI, the LLM providers, the Excel generator, GraphDB — was given a fallback before any feature was considered done. When VRTI is down, the app serves from the database. When the LLM is down, the template system still answers questions. When GraphDB is unavailable, the SQL result stands alone. Designing for failure first meant nothing was blocked on external services.

**Streaming makes slow operations feel fast.** The Ask pipeline takes 3–30 seconds end-to-end depending on the route. Streaming the results stage-by-stage means the user sees the SQL query and raw results within 1–2 seconds, and watches the synthesis answer appear in real time. The same wait behind a spinner would feel much worse.

**Transparency beats cleverness for research software.** Every query, every data source, every decision point in the pipeline is visible — in the UI, in the code, or in the streaming log. When an examiner asks "but where does this number come from?", the answer is always "click here and you can see the exact SQL query that produced it." The `query_provenance.strategy` field in every SSE result tells the user exactly which lane produced the answer.

**The external knowledge graph adds real value but needs careful integration.** VRTI contains authoritative national-level data that estate records alone don't have — standardised townland names, geographic boundaries, links to other datasets. But it's an external service with its own availability and response-time constraints. The DB-first caching layer was the architectural decision that made it practical to depend on.

---

## How It All Comes Together: A Full Example

A researcher visits the site and asks: *"What happened to the population of Kilcommon between 1841 and 1861?"*

1. **Browser** sends `POST /api/ask/query` to the Flask backend.
2. **Pre-flight** — townland resolution finds "KILCOMMON" (exact match); question analysis extracts `primary_intent: population`, `output_mode: trend`, `scope: townland`, `year_from: 1841`, `year_to: 1861`.
3. **Fast Lane 1** — rule-based slot-fill: metric `population_change` keywords match; confidence 0.95 ≥ 0.80. SQL compiled from the 14-metric registry. **Zero LLM calls.**
4. **SQL query** runs against the local SQLite database (census_record JOIN townland). Returns population figures for 1841, 1851, and 1861 for Kilcommon.
5. **SSE events stream** — each pipeline stage emits a progress event (`schema_sql → framing_query → querying_database → querying_vrti_graph → synthesizing_answer`). The browser renders a live progress bar.
6. **VRTI enrichment** — parallel call adds that Kilcommon is in the civil parish of Kilcommon, barony of Shillelagh.
7. **GraphRAG** — vector seed finds "KILCOMMON" and "POPULATION" nodes; 2-hop BFS retrieves 23 related nodes (household families, census years, townland geography). Linearised text passed to synthesis.
8. **LLM synthesis** — Claude writes: "The population of Kilcommon fell from 412 in 1841 to 198 in 1851 — a decline of 52% during the Famine decade — and had recovered only slightly to 231 by 1861."
9. **Chart spec** — a line chart is assembled with year labels and population values.
10. **PDF export** — the full report is packaged (question + SQL + table + VRTI context) and available for download.

The researcher saw the first results within 1 second, and the full answer within 8 seconds. Every number is traceable. The SQL query is displayed. The data sources are credited. The `query_provenance.strategy` field shows "rule_fill" — the answer came from a deterministic keyword match, not an LLM guess.

---

## Summary

This project is, at its core, a **data integration and research tool**. The interesting engineering is in the joins: joining estate records with national census data with a knowledge graph with an in-process property graph with an LLM synthesis chain, while keeping everything transparent, reproducible, and honest about where every number came from.

The technical stack was chosen not for novelty but for fitness: SQLite because it's portable, Flask because it's transparent, vanilla JS because it's readable, raw SQL because it's auditable, hand-written PDF because it's dependency-free. The LLM was added carefully — useful for answer synthesis and qualitative context, but never trusted with number generation.

The result is an application that a historian can use without knowing what SQL is, but where every answer they get is backed by a query a developer can read and verify.
