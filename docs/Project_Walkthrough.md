# Coolattin Estate Records Explorer — Project Walkthrough

*A plain-language explanation of what we built, how it works, the decisions we made, and why.*

---

## What Is This Project?

The **Coolattin Estate Records Explorer** is a web application that brings together historical records from the Coolattin Estate in County Wicklow, Ireland — one of the largest landlord estates of the Famine era (mid-19th century). These records cover tenants, evictions, emigration, and census population data from roughly 1827 to 1891.

The goal was to make this data **searchable, visual, and explorable** — not just a spreadsheet. Someone researching their family history or studying the Famine can type a natural-language question, see population maps, browse tenant records, and even export a formatted PDF report. All from a browser.

This was built as a **Masters Dissertation project**, which meant two non-negotiable constraints:
- It had to be **reproducible** — anyone with the code could run it and get the same results.
- It had to be **transparent** — every claim had to be traceable back to a known data source.

Those two constraints shaped nearly every technical decision made.

---

## Where the Data Comes From

Before anything else, it helps to understand the raw ingredients:

| Source | What it contains | How we use it |
|---|---|---|
| **Coolattin Estate GeoJSON** | Map boundaries for all 152 townlands, estate survey population figures (1827–1868), eviction counts (1847–1856) | The backbone of the map and the eviction analytics |
| **VRTI Knowledge Graph** | Standard census data (1841–1891), townland metadata (Gaelic names, parish, barony, geographic coordinates) | Enriches our local data with nationally standardised records |
| **Unified Estate CSV** | ~13,000 individual tenant records — names, dates, locations, emigration ships, destinations | Powers the "search people" feature and the LLM Q&A |
| **NMS Heritage Data** | Archaeological monuments, holy wells, ring forts across Wicklow | Displayed as an overlay on the heritage map |
| **Bundled seed CSVs** | Fallback copies of census data | Used when the external VRTI service is unavailable |

The key challenge was that these sources use **different naming conventions** for the same places. "Ballinacor", "Ballinacor North", and "BALLINACOR" all refer to the same townland — but a computer treats them as three different things. A lot of early work went into building a normalisation system that converts everything to a single canonical form (uppercase, no qualifiers) and maintains a lookup table of known spelling variants.

---

## How the Application Is Structured

Think of the application in four layers, each with a single job:

```
[User's browser]
      ↕  HTML pages, API calls, Server-Sent Events
[Routes — the front door]
      ↕  delegates work
[Services — the brain]
      ↕  reads/writes data
[Repositories — the data layer]
      ↕  SQL queries
[SQLite database]   ←→   [VRTI Knowledge Graph]   ←→   [LLM (OpenRouter / Ollama)]
```

**Routes** are thin — they receive a request, call a service, and return a response. They contain almost no logic themselves.

**Services** contain all the decision-making — do we have fresh data? Does this question match a known template? Should we call the external knowledge graph or use what's in the database?

**Repositories** contain all the SQL queries. The rule was simple: no raw SQL outside the repository layer. This makes it trivially easy to audit every database query in one place.

**The database** is a single SQLite file. Four tables: townlands, census records, clearances, and a "freshness tracker" that remembers when each dataset was last updated.

---

## The Database Decision: No ORM

Most modern web projects use an **ORM** (Object-Relational Mapper) — a library that hides the SQL behind Python objects so you don't have to write queries manually. We deliberately chose not to use one.

**Why:** For a dissertation project, every query had to be readable and auditable. With an ORM you get convenient code but the actual SQL being run is hidden. With raw `sqlite3`, every query is a plain text string that anyone can read and verify. The trade-off was more boilerplate code, but every query was transparent.

**The trade-off:** More work upfront (writing every query by hand), but full control and zero magic. For a production startup, you'd probably want an ORM. For a research project where you have to explain your methodology, raw SQL is the honest choice.

---

## The Core Data Flow: DB-First, KG-Second

The VRTI Knowledge Graph (VRTI = Virtual Record Treasury of Ireland) is an external research database hosted by another institution. We do not control it. It can be slow (2+ seconds per query) or occasionally unavailable.

So we built a **caching layer**: on the first request, we call VRTI, save the results to our local SQLite database, and record when we did so. On every subsequent request, we serve from the local database instantly. We only call VRTI again when the data is "stale" — older than a configurable number of days.

If VRTI is completely unavailable, we fall back to bundled CSV seed files that contain pre-collected census data. The user still gets a response — just potentially older data.

```
Request comes in
    ↓
Is data in local DB? → Yes, is it fresh? → Yes → Serve from DB instantly
                                         → No  → Serve from DB + refresh in background
                      → No → Call VRTI KG → Save to DB → Serve result
                                          (if VRTI is down → serve from seed CSV)
```

**Why this matters:** A direct VRTI call takes 500ms–2000ms. A local DB query takes under 1ms. Users get a fast, consistent experience regardless of VRTI's availability.

---

## The Five Pages

### 1. Home / Map (`/`)

The landing page is a **choropleth map** — each of the 152 Coolattin townlands is drawn as a coloured polygon, with colour intensity representing population or evictions depending on what data layer the user selects. Built with **Leaflet.js**, a well-established open-source mapping library.

The data behind the polygons comes from the estate GeoJSON file — a geographic data format that combines map geometry (the actual polygon coordinates) with property data (the historical figures attached to each area).

### 2. Census Explorer (`/census`)

A year slider lets the user move from 1827 to 1891, watching the choropleth update as population rises and falls. Clicking a townland opens a sidebar showing the year-by-year breakdown for that specific area.

This page was the first place we ran into the dual-source problem: estate surveys and national censuses were collected in different years and with different methodologies. The estate surveys (1827, 1839, etc.) recorded total population. The national censuses (1841, 1851, etc.) recorded males, females, inhabited houses, and uninhabited houses separately. We had to handle both formats in the same table and UI, and ensure the year slider correctly distinguishes between them.

### 3. Ask (`/ask`) — the most complex page

The Ask page lets users type a question in plain English — "How many families emigrated from Baltinglass?" — and get a structured answer back. This is the most technically sophisticated part of the project.

More on this below.

### 4. Analytics (`/analytics`)

Pre-computed dashboards showing KPIs and charts for emigrations, evictions, workhouse connections, and tenancy patterns. These are built as **pluggable modules** — each dataset gets its own self-contained module file. Adding a new analytics dashboard means creating one new file; nothing else needs to change.

### 5. Heritage Map (`/heritage`)

An overlay of archaeological monuments and holy wells from the National Monuments Service open data, layered on top of the Wicklow base map. Users can filter by monument type and click for details.

---

## The Ask Page: Walking Through the LLM Pipeline

This is where the most engineering effort went, and where the most interesting decisions were made.

When a user submits a question, it goes through a **multi-stage pipeline**. Critically, the results stream back to the browser in real time — the user sees each stage appear as it completes rather than waiting for everything to finish.

Here is each stage:

### Stage 1: Template Matching

Before calling any AI, we check the question against 100+ pre-written SQL templates. Each template is associated with a set of keywords. If the question scores highly enough against a template, we skip the AI entirely and run the pre-approved SQL query.

**Why:** This was the most important safety decision. LLMs are impressive, but they occasionally make mistakes — a hallucinated SQL query could return wrong numbers and be presented as historical fact. For common, high-value questions ("How many people emigrated from X?", "What was the population of Y in 1851?"), we use **verified queries** that we know are correct. This is faster and more reliable than LLM-generated SQL.

### Stage 2: Townland Resolution

If the question mentions a place name, we resolve it. Exact match first. If that fails, fuzzy matching against the canonical townland list. If still no match, we return "did you mean X, Y, or Z?" suggestions instead of proceeding with a wrong location.

### Stage 3: LLM SQL Generation (only if no template matched)

If no template matched, we send the question to an LLM (either OpenRouter in the cloud, or a local Ollama model). Critically, we also send it the **database schema, row counts, and sample values** — enough context for the LLM to write accurate SQL without having to guess column names or data formats.

**The LLM provider choice:** We support two: **OpenRouter** (cloud, requires an API key, uses free-tier models by default) and **Ollama** (runs entirely on your laptop, no API key, slower). The app auto-detects which is available. This was important for the dissertation — the project had to work offline during a presentation, and couldn't depend on an external service being available.

### Stage 4: Read-Only Guardrail

Before executing any SQL — whether from a template or LLM-generated — we check it against a pattern that blocks any write operation (INSERT, UPDATE, DELETE, ALTER). The database is historical data; nothing should be able to modify it through the Ask interface.

### Stage 5: Execute and Format

The query runs against SQLite. Results come back as a table.

### Stage 6: LLM Rewrite

The raw database result ("27") gets sent back to the LLM to be rephrased as a natural language answer ("27 people from Ballinacor emigrated between 1847 and 1852, primarily to North America."). If the LLM is unavailable, the raw table is shown directly — the page degrades gracefully.

### Stage 7: VRTI Enrichment

In parallel, we call the VRTI Knowledge Graph to fetch parish and administrative context for any townlands mentioned in the result. This adds historical background that isn't in our local database.

### Stage 8: Chart Suggestion and PDF Export

If the results are numerical and multi-row, the pipeline automatically suggests a chart type (bar, line, scatter). A PDF report is generated with the question, SQL, results, and LLM answer.

### What the User Sees

All of this streams back to the browser as it happens. The user sees the template match appear, then the SQL query, then the results table, then the LLM answer — each appearing within a second or two of the previous. This streaming approach (called **Server-Sent Events**) was a deliberate choice over showing a spinner for 8 seconds — it makes the system feel responsive and also makes the pipeline transparent.

---

## The Feedback Loop

Every answer on the Ask page has a thumbs up / thumbs down button. When a user marks an answer as correct, that question-SQL pair is saved in a "query memory" table. Future similar questions are matched against this memory before even reaching the template system. Over time, the system learns from confirmed good answers.

---

## The Knowledge Graph Exploration Page

One page was built specifically for the dissertation's technical evaluation: `/explore-knowledge`. It runs the same query as both a SQL query against our local database and as a SPARQL query against the VRTI Knowledge Graph, shows the results side-by-side with timing information, and can ask the LLM to explain any discrepancies.

**Why:** The dissertation argument was that combining a local relational database with an external knowledge graph gave better results than either alone. This page is the evidence — it demonstrates the two systems returning complementary data about the same records.

---

## Technologies Used and Why

| Technology | What it does | Why we chose it |
|---|---|---|
| **Flask (Python)** | Web framework — handles HTTP requests, routes, templates | Lightweight, easy to reason about, excellent for APIs. No magic. |
| **SQLite** | Local database | Single file, zero setup, fully portable. Perfect for a dissertation project. |
| **VRTI SPARQL** | External knowledge graph queries | The authoritative source for nationally standardised Irish historical data. |
| **OpenRouter / Ollama** | LLM for natural language Q&A | OpenRouter for cloud convenience, Ollama for offline fallback. |
| **Leaflet.js** | Interactive maps | Open source, well-documented, no API key required. |
| **Chart.js** | Charts and graphs | Simple, well-documented, no build step required. |
| **Vanilla JavaScript** | Frontend behaviour | Deliberately chose not to use React/Vue. The app has a handful of pages — a framework would add more complexity than it solved. |
| **Jinja2** | HTML templating | Built into Flask. Keeps HTML logic in templates rather than JavaScript. |
| **openpyxl** | Excel file generation | Pure Python, no external dependencies. |
| **rapidfuzz** | Fuzzy string matching for townland names | Fast and accurate. Used for the "did you mean?" suggestions. |

### The "no framework" frontend decision

Every modern web project is expected to use React, Vue, or similar. We chose not to. The reasons:

1. **Complexity vs. benefit**: The app has six pages. A JavaScript framework would add a build step, a dependency manager, component architecture, and hundreds of kilobytes of library code for what are fundamentally static pages with some interactivity.
2. **Transparency**: Vanilla JavaScript is readable by anyone. You don't need to know React's lifecycle hooks to understand `ask.js`.
3. **SSE streaming**: Server-Sent Events — the mechanism used to stream Ask results — work cleanly with plain `EventSource` in JavaScript. A framework would have added an abstraction layer without adding value.

**The trade-off:** The JavaScript files are more imperative and repetitive than component-based code would be. `census.js` and `ask.js` share some map utilities that could have been abstracted. This was a known, accepted trade-off.

---

## Key Architectural Decisions and Trade-offs

### 1. Single file database (SQLite) vs. a proper database server

**Decision:** SQLite.

**Why:** Zero setup. A researcher cloning the repository can run the app immediately without installing PostgreSQL or MySQL. The database is a single file that can be checked for integrity, copied for backup, or handed to an examiner.

**Trade-off:** SQLite doesn't support concurrent writes well. If this were a production application with many simultaneous users, it would be a problem. For a dissertation demo with a handful of concurrent users, it's completely fine.

### 2. Verified SQL templates vs. full LLM generation

**Decision:** Templates first, LLM second.

**Why:** Historical data demands accuracy. A 5% LLM error rate is acceptable in a chatbot. It is not acceptable when someone is researching their great-great-grandmother's eviction record.

**Trade-off:** Writing 100+ templates is tedious upfront work. But each template is a tested, verified query that will always return the right answer. The LLM is only invoked when no template matches — roughly 30% of questions.

### 3. Hand-written PDF generation vs. a library

**Decision:** Hand-written PDF 1.4 format.

**Why:** PDF libraries add external dependencies. The dissertation had to be fully self-contained. A PDF is ultimately a structured text file; writing it by hand avoids a dependency that could break or change.

**Trade-off:** The hand-written generator only supports the features we implemented (text, tables, basic formatting). It cannot handle complex layouts. For this use case, that was sufficient.

### 4. Local LLM fallback (Ollama)

**Decision:** Support both cloud and local LLMs.

**Why:** Academic presentations happen offline. API services go down. The research must be demonstrable without a live internet connection.

**Trade-off:** Local LLMs are slower and often less capable than cloud models. But for SQL generation from a constrained schema, even a smaller model works well with good context.

---

## What Was Learned Along the Way

**Data normalisation is the hardest problem.** Before any feature could be built, we had to get data from multiple sources to agree on the same place names, date formats, and record identifiers. This took more time than any other single task. The townland alias system — a lookup table mapping dozens of spelling variants to canonical names — was built incrementally as mismatches were discovered.

**Graceful degradation has to be designed in from the start.** Every external dependency — VRTI, the LLM, the Excel generator — was given a fallback before any feature was considered "done." When VRTI is down, the app serves from the database. When the LLM is down, the template system still answers 70% of questions. Designing for failure first meant nothing was blocked on external services.

**Streaming makes slow operations feel fast.** The Ask pipeline takes 5–10 seconds end-to-end. Streaming the results stage-by-stage means the user sees the SQL query and raw results within 1–2 seconds, and watches the LLM answer appear in real time. The same 8 seconds behind a spinner would feel much worse.

**Transparency beats cleverness for research software.** Every query, every data source, every decision point in the pipeline is visible — in the UI, in the code, or in the streaming log. When an examiner asks "but where does this number come from?", the answer is always "click here and you can see the exact SQL query that produced it."

**The external knowledge graph adds real value but needs careful integration.** VRTI contains authoritative national-level data that the estate records alone don't have — standardised townland names, geographic boundaries, links to other datasets. But it's an external service with its own availability and response-time constraints. The DB-first caching layer was the architectural decision that made it practical to depend on.

---

## How It All Comes Together: A Full Example

A researcher visits the site and asks: *"What happened to the population of Kilcommon between 1841 and 1861?"*

1. **Browser** sends the question to the Flask backend.
2. **Template matching** finds a match: `population_change_range` — a pre-written SQL query for population comparison across years.
3. **Townland resolution** finds "KILCOMMON" in the canonical list. Exact match.
4. **SQL query** runs against the local SQLite database. Returns population figures for 1841, 1851, and 1861.
5. **Result streams back** — the SQL and raw numbers appear in the browser immediately.
6. **LLM rewrite** — "The population of Kilcommon fell from 412 in 1841 to 198 in 1851 — a decline of 52% during the Famine decade — and had recovered only slightly to 231 by 1861."
7. **VRTI enrichment** — adds that Kilcommon is in the civil parish of Kilcommon, barony of Shillelagh.
8. **Chart suggestion** — a line chart is generated showing the decline.
9. **PDF export** — the full report is packaged and available for download.

The researcher saw the first results within 1 second, and the full answer within 8 seconds. Every number is traceable. The SQL query is displayed. The data sources are credited.

---

## Summary

This project is, at its core, a **data integration and research tool**. The interesting engineering is in the joins: joining estate records with national census data with a knowledge graph with an LLM, while keeping everything transparent, reproducible, and honest about where every number came from.

The technical stack was chosen not for novelty but for fitness: SQLite because it's portable, Flask because it's transparent, vanilla JS because it's readable, raw SQL because it's auditable. The LLM was added carefully — useful for question-answering, but never trusted without a guardrail.

The result is an application that a historian can use without knowing what SQL is, but where every answer they get is backed by a query a developer can read and verify.
