# GraphRAG and RAG System — Ask Page Pipeline

## Overview

The Ask page (`/ask`) implements a hybrid Retrieval-Augmented Generation (RAG) architecture that answers natural-language questions about the Coolattin Estate records. The system combines three retrieval strategies — a template-based SQL library, an LLM-generated SQL path, and an optional SPARQL path against the VRTI Knowledge Graph — with an LLM rewrite step that converts raw database results into readable prose. A feedback loop and query memory layer allow the system to improve over time by caching approved queries for future reuse without a repeated LLM call.

The entire pipeline is implemented in `backend/services/ask_service.py` (~1 700 lines) and streamed to the browser over Server-Sent Events (SSE).

---

## Architecture at a Glance

```
User question (natural language)
        │
        ▼
┌───────────────────────────────────────────────────────┐
│              Entity Extraction Layer                  │
│  • townland hint (fuzzy match against catalog)        │
│  • year entities  (1827–1891)                         │
│  • surname entities (case-insensitive scan)           │
└───────────────────────┬───────────────────────────────┘
                        │
                        ▼
┌───────────────────────────────────────────────────────┐
│         Path 1 — Template Matching (RAG)              │
│  100+ pre-verified SQL templates scored by keyword    │
│  match; best match instantiated with extracted        │
│  entities and executed immediately (no LLM call)      │
└───────────────────────┬───────────────────────────────┘
                        │ miss
                        ▼
┌───────────────────────────────────────────────────────┐
│         Path 2 — Query Memory Retrieval (RAG)         │
│  Semantic similarity search over previously-approved  │
│  queries; reuses approved SQL if similarity > thresh  │
└───────────────────────┬───────────────────────────────┘
                        │ miss
                        ▼
┌───────────────────────────────────────────────────────┐
│         Path 3 — LLM SQL Generation (RAG)             │
│  Annotated schema + question sent to OpenRouter/      │
│  Ollama; LLM generates SQL; safety guardrail applied  │
└───────────────────────┬───────────────────────────────┘
                        │
                        ▼
┌───────────────────────────────────────────────────────┐
│              SQLite Execution (primary DB)            │
└───────────────────────┬───────────────────────────────┘
                        │
                        ▼
┌───────────────────────────────────────────────────────┐
│    Optional — Path 4: VRTI SPARQL (GraphRAG)         │
│  LLM-generated SPARQL/PostgreSQL against the          │
│  VRTI Knowledge Graph; results merged with SQLite     │
└───────────────────────┬───────────────────────────────┘
                        │
                        ▼
┌───────────────────────────────────────────────────────┐
│    Optional — Path 5: GraphDB SPARQL (GraphRAG)      │
│  SPARQL against local Coolattin RDF repository;       │
│  comparative analysis alongside VRTI/SQLite           │
└───────────────────────┬───────────────────────────────┘
                        │
                        ▼
┌───────────────────────────────────────────────────────┐
│              LLM Answer Rewrite                       │
│  Raw rows → readable natural-language answer          │
└───────────────────────┬───────────────────────────────┘
                        │
                        ▼
                 SSE stream to browser
         (table, chart, answer, provenance, PDF link)
```

---

## RAG Path 1 — Template Matching

### What it is

Template matching is the fast, deterministic path. A library of 100+ manually verified SQL queries is stored as Python objects in `ask_service.QUESTION_TEMPLATES`. Each template covers a specific question type and carries keyword metadata that lets the system score how well the template matches any incoming question.

### Template Structure

Each template contains:

```python
{
  "id":                "emigration_by_townland_year",
  "category":          "emigration",
  "description":       "How many people emigrated from {townland} in {year}?",
  "required_keywords": ["emigrat", "townland"],   # ALL must appear
  "optional_keywords": ["family", "year"],        # boosts score
  "sql_template":      """
      SELECT COUNT(*) AS emigrations
      FROM unified_record
      WHERE townland_norm = '{townland_norm}'
        AND year = {year}
        AND role IN ('emigrant','head of family')
  """,
  "requires_townland": True,
  "requires_year":     True,
  "requires_surname":  False,
}
```

### Scoring

The scoring function `_template_match_score(template, question)` computes:

1. Convert question to lowercase.
2. Check that **all** `required_keywords` appear as substrings. If any is missing, score = 0 (template cannot match).
3. Add a fixed score for each `optional_keyword` that appears.
4. Return the total score.

All templates are scored; the one with the highest non-zero score wins. Ties are broken by specificity (longer `required_keywords` list preferred).

### Placeholder Substitution

After a template wins, the pipeline substitutes extracted entities:

| Placeholder | Source |
|---|---|
| `{townland_norm}` | `canonical_name()` applied to the extracted townland hint |
| `{year}` | First year-like integer (1827–1891) found in the question |
| `{surname}` | First capitalised token that matches a surname in the `unified_record` table |

Templates that declare `requires_townland=True` but cannot resolve a townland from the question are skipped (their score is zeroed), so they never produce a query with an empty placeholder.

### Why This is RAG

Template matching is a form of retrieval: the question is used to retrieve the most relevant pre-authored SQL from the template library, then that SQL is executed to fetch facts from the database, and the facts are used to compose the answer. The LLM is not involved in this path at all — it is deterministic and reproducible.

---

## RAG Path 2 — Query Memory

### What it is

Query memory stores SQL queries that users have previously approved (via the thumbs-up button) in the `ask_query_memory` SQLite table. When a new question arrives, the system computes a similarity score between the new question and every stored question in memory. If the best match exceeds a threshold, the approved SQL is reused directly.

### Memory Schema

```sql
CREATE TABLE ask_query_memory (
    id            INTEGER PRIMARY KEY,
    question_text TEXT,
    sql_text      TEXT,
    vrti_postgres_sql TEXT,
    feedback      TEXT,       -- 'up' | 'down'
    result_sample JSON,
    reuse_count   INTEGER DEFAULT 0,
    created_at    TEXT,
    last_reused_at TEXT
);
```

### Similarity Scoring

The matching uses a token-set ratio approach (from `rapidfuzz` if available, otherwise `difflib.SequenceMatcher`). Token-set ratio is used rather than simple edit distance because it is insensitive to word order — *"emigration per year from Ballinacor"* and *"Ballinacor emigration by year"* should score as near-identical.

### Feedback Loop

When a user submits a thumbs-up (`POST /api/ask/feedback { feedback: "up" }`), the query is written to `ask_query_memory`. Thumbs-down feedback is recorded in `ask_query_feedback` for review but does not populate the memory cache. This asymmetry means only verified-correct queries enter the retrieval pool.

---

## RAG Path 3 — LLM SQL Generation

### When it activates

If neither the template library nor query memory returns a match, the pipeline falls back to asking the LLM to generate a SQL query from scratch.

### Schema Annotation

Rather than sending the raw `CREATE TABLE` DDL, the pipeline sends a richly annotated schema description (`_ANNOTATED_SCHEMA`). This annotation:

- Describes each table's purpose in prose.
- Lists each column with its type and a short description of what values it holds.
- Provides JOIN hints (e.g., "join census_record to townland on townland_id = townland.id").
- Documents any custom SQL functions available (e.g., `distance_km(lat1, lon1, lat2, lon2)`).
- Lists the categorical values for key columns (roles, legal actions, occupations, source tags).

This annotation is cached with a TTL of 300 seconds to avoid rebuilding it on every request.

### LLM Prompt Structure

```
You are a SQL assistant for a historical records database.
Schema:
  <annotated schema>

Available townlands: [list of canonical names]
Available census years: [1841, 1851, 1861, 1871, 1881, 1891, 1827, 1839, 1848, 1850, 1860, 1868]

Question: <user question>

Return only a single valid SQLite SELECT statement. Do not include any explanation.
```

The prompt deliberately excludes INSERT/UPDATE/DELETE examples to reduce the risk of the LLM generating write statements.

### Safety Guardrail

All LLM-generated SQL is checked against `FORBIDDEN_SQL` before execution:

```python
FORBIDDEN_SQL = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|PRAGMA|TRUNCATE)\b",
    re.IGNORECASE,
)
```

If the pattern matches, the query is rejected and an error SSE event is emitted. The check applies to template SQL as well (defence in depth).

### Provider Fallback

The LLM provider is selected at startup via `ASK_LLM_PROVIDER`:

- `"openrouter"` — uses OpenRouter API (commercial, cloud-based).
- `"ollama"` — uses a local Ollama instance.
- `"auto"` — probes OpenRouter first, then Ollama, uses whichever responds.

Free OpenRouter models are tried in order if the primary model is unavailable. LLM availability is cached for 60 seconds to avoid probe overhead on every request.

---

## GraphRAG Path 4 — VRTI Knowledge Graph SPARQL

### What it is

When `ASK_GENERATE_VRTI_SQL_WITH_LLM=true`, the pipeline executes a second query against the remote VRTI Virtuoso endpoint in parallel with the SQLite query. This is the "Graph" component of GraphRAG: the retrieval step spans both a relational database and an RDF knowledge graph.

### Why a Knowledge Graph

The VRTI KG holds data that is not replicated in the local SQLite database: linked external resources, place descriptions in Irish, formal ontological relationships between townlands and their administrative hierarchies, and media assets. For questions that require semantic context beyond the tabular records — for example, questions about the heritage classification of a place or its relationship to parishes in the wider county — the KG can return answers that SQLite cannot.

### Query Generation

For KG queries, the LLM is prompted with the VRTI ontology namespace and prefix block, the relevant graph URI (`https://kg.virtualtreasury.ie/graph/present-day-places-v1`), and the question. The LLM generates either a SPARQL SELECT or a PostgreSQL query targeting the VRTI relational projection. The safety guardrail is applied to both.

### Result Merging

The SQLite result set and the KG result set are returned as separate tables in the SSE event. The frontend renders them side by side in the provenance block, labelled "SQLite (local)" and "VRTI Knowledge Graph", so the user can compare them.

---

## GraphRAG Path 5 — Local GraphDB Repository

A local GraphDB instance at `http://localhost:7200/repositories/coolattin` holds a Coolattin-specific RDF dataset built with the `co:` ontology (`https://coolattin.ie/ontology#`). This is an experimental prototype for Dissertation objective D8 — comparing a purpose-built estate RDF graph against the VRTI's general-purpose place graph.

When `GRAPHDB_ENABLED=true`, the pipeline queries GraphDB after the SQLite step. The SPARQL client in `backend/integrations/graphdb_sparql.py` returns rows as dicts with the same key names as the SQLite `Row` objects, so the frontend can render both without any format conversion.

If GraphDB is unreachable or returns an error, `query()` returns `([], [])` — the pipeline continues and the GraphDB section of the answer is simply absent.

---

## SSE Streaming Protocol

The pipeline is a Python generator function (`answer_question_stream`) that yields JSON-encoded SSE events as it completes each stage. The frontend `ask.js` opens an `EventSource` and updates the UI progressively.

### Event Types

| `type` | `stage` | Meaning |
|---|---|---|
| `progress` | `framing_query` | Entities extracted; template scoring in progress |
| `progress` | `contacting_llm` | No template match; calling LLM for SQL |
| `progress` | `querying_database` | SQL verified; executing against SQLite |
| `progress` | `querying_vrti_graph` | Executing KG query in parallel |
| `progress` | `querying_graphdb` | Executing local GraphDB query |
| `progress` | `querying_fusion` | Aligning lanes on resolved entity; detecting discrepancies |
| `progress` | `preparing_output` | Results ready; rewriting answer |
| `result` | *(final)* | Complete structured response |
| `error` | *(any)* | Pipeline error; message included |

### Event Payload (final result)

```json
{
  "type": "result",
  "answer": "In 1851, 47 people emigrated from Ballinacor...",
  "table": { "columns": [...], "rows": [[...]] },
  "chart": { "type": "line", "labels": [...], "datasets": [...] },
  "sql": "SELECT ...",
  "vrti_sql": "SELECT ...",
  "provenance": {
    "source": "template | memory | llm",
    "template_id": "emigration_by_townland_year",
    "match_score": 3,
    "memory_reuse": false,
    "llm_model": "openai/gpt-4o-mini"
  },
  "warnings": ["No data found for years 1827–1839"],
  "suggestions": ["Try asking about clearances in the same period"],
  "insights": ["Total estate emigration peaked in 1852 with 312 departures"],
  "pdf_url": "/api/ask/pdf/ask_20240601_ballinacor.pdf",
  "discrepancies": [
    {
      "metric": "emigrant count",
      "entity": "Ballinacor",
      "kg_uri": "https://kg.virtualtreasury.ie/resource/...",
      "sqlite_value": 312,
      "vrti_value": null,
      "graphdb_value": 308,
      "delta": 4,
      "likely_cause": "likely differing record scope (minor: < 5% difference)"
    }
  ],
  "fusion": {
    "discrepancy_count": 1,
    "agreement_count": 0,
    "entity_label": "Ballinacor",
    "kg_uri": "https://kg.virtualtreasury.ie/resource/...",
    "fusion_text": "SQLite records 312 emigrant count for Ballinacor; the Coolattin RDF graph (GraphDB) attributes 308 — a discrepancy of 4, likely differing record scope (minor: < 5% difference).",
    "source_provenance": {
      "sqlite": [{"source": "sqlite", "entity": "Ballinacor", "kg_uri": null}],
      "graphdb": [{"source": "graphdb", "entity": "Ballinacor", "kg_uri": "..."}],
      "vrti": []
    }
  }
}
```

---

## LLM Answer Rewrite

After SQL results are obtained, the pipeline calls the LLM a second time to convert the raw table into a natural-language answer. The prompt is:

```
You are a historian's assistant summarising archival query results.
Question: <user question>
Data:
  <first 20 rows as a markdown table>

Write a single paragraph answer in plain English. Do not invent facts not present in the data.
```

This rewrite step is what distinguishes RAG from pure SQL search: the retrieved facts are composed into a coherent prose answer that a historian or student can read directly without interpreting raw rows.

---

## PDF Report Generation

If the user requests a PDF (or if the result exceeds a row threshold), the pipeline calls `_generate_pdf_report()`. This function writes a hand-crafted PDF 1.4 file — no third-party library dependency — containing:

- Question, date, and provenance header.
- Natural-language answer paragraph.
- Data table (truncated at 50 rows with row count noted).
- Chart image (if present), rendered as an embedded PNG.
- SQL query used (in monospace font).
- Warnings and related suggestions.

The file is saved to `exports/ask/<name>.pdf` and served via `GET /api/ask/pdf/<name>`.

---

## Query Template Categories

The template library covers seven analytical domains:

| Category | Example questions |
|---|---|
| **Emigration** | Total emigrants by year; emigrants per townland; emigrants by ship name; departure port breakdown; Canada vs US destinations |
| **Evictions / Clearances** | Evictions per year; worst eviction years; evictions per townland; total evicted persons |
| **Census / Population** | Population change 1841–1851; uninhabited houses in a year; parish-level aggregation; most/least populous townlands |
| **Geography** | Townlands in a given civil parish; nearest townlands to a location; barony listing |
| **People / Names** | All persons in a townland; persons by surname; heads of household; widows with children |
| **Tenancy / Holdings** | Average holding size; land distribution by size bracket; tenants per townland |
| **Heritage** | Holy wells near populated townlands; ring fort prevalence vs population density |

---

## Verified Analysis Templates

A subset of templates are marked as "verified" — they have been manually checked for statistical correctness and cover cross-table analytical questions:

- `tenant_land_gender_average` — average holding size by gender
- `widows_with_children_proportion` — percentage of widows with dependants
- `eviction_family_size_range` — distribution of evicted family sizes
- `population_trend_1841_1861` — Famine-era population change
- `emigration_population_townland_trend` — correlation of emigration and population decline
- `holy_well_population_relationship` — population density around heritage features
- `ring_fort_population_relationship` — ring fort prevalence vs. population patterns

These templates are guaranteed to return meaningful results without LLM involvement, making them suitable for export, citation, and dissertation tables.

---

## Caching Architecture

To reduce latency and API costs, the pipeline maintains six in-process caches:

| Cache | TTL | Contents |
|---|---|---|
| `_VRTI_PARISH_CACHE` | 3 600 s | Parish names from VRTI |
| `_VRTI_STATUS_CACHE` | 300 s (unavailable) | Whether VRTI endpoint is reachable |
| `_OPENROUTER_STATUS_CACHE` | 60 s | OpenRouter availability and model |
| `_OLLAMA_MODEL_CACHE` | 120 s | Available Ollama models |
| `_PROMPT_SCHEMA_CACHE` | 300 s | Annotated schema for LLM prompts |
| `_QUERY_MEMORY_CACHE` | 60 s | Approved queries from DB |
| `_TOWNLAND_CATALOG_CACHE` | in-process | All canonical townland names |

All caches are module-level dicts with an expiry timestamp. They are not shared across workers; if the application runs with multiple Flask workers, each worker maintains its own cache.

---

## Relationship Between RAG and GraphRAG

The distinction in this system is:

- **RAG** (Paths 1–3) retrieves facts from the **relational SQLite database** using SQL. The database is flat and tabular; joins are explicit; the schema is fixed.

- **GraphRAG** (Paths 4–5) retrieves facts from **RDF knowledge graphs** using SPARQL. The KG represents places, people, and events as a graph of typed relationships; queries can traverse the hierarchy (townland → parish → barony → county) in a single SPARQL triple pattern rather than a multi-table JOIN. The KG also exposes links to external resources (OSM, OSI, heritage databases) that have no equivalent in the relational schema.

In practice, GraphRAG is activated when:
1. The question requires semantic hierarchy traversal (e.g., "all townlands in the barony of Ballinacor").
2. The question asks for heritage classification or external provenance data.
3. The user explicitly requests KG enrichment alongside the SQL answer.

For most day-to-day historical questions — population counts, emigration tallies, eviction lists — the RAG (SQLite) path is faster and more reliable, because the local database is a curated subset of the KG optimised for these query patterns.
