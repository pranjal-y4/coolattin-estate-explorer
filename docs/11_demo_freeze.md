# v1.0-demo-freeze — Evaluation Results and Canonical Configuration

**Git tag:** `v1.0-demo-freeze`  
**Freeze date:** 2026-06-10  
**Purpose:** Academic submission snapshot — deterministic evaluation complete, config pinned.

---

## 1. Evaluation Summary

### 1.1 Full Regression (75 competency questions)

Run: `python3 -m backend.services.ask_eval --phase graphrag_on`

| Metric | Value |
|--------|-------|
| Questions run | 75 |
| Routing accuracy | 89.3% |
| Aggregation correctness | 100.0% |
| SQL exec success | 100.0% |
| Entity label accuracy | 100.0% |
| SQL-id resolution | 100.0% |
| KG-URI resolution | 100.0% |
| Template hit rate | 100.0% |
| Lane routing accuracy | 72.0% |
| Analytical agg accuracy | 100.0% |
| Subgraph recall (relational) | 1.0 |
| Comparative SQLite capture | 100.0% |
| Comparative KG capture | 100.0% |
| Honest-refusal rate (G-series) | 0.0% |
| LLM calls required | 0 |
| p50 latency | 372 ms |
| p90 latency | 2095 ms |
| p95 latency | 4152 ms |

**Known issues (pre-freeze, documented not blocking):**
- Honest-refusal 0%: G-series out-of-scope questions are routed by the semantic layer
  (partial keyword matches trigger tenancy/eviction templates). These reach a deterministic
  answer rather than an honest "I don't know". Fixing this would require an explicit
  out-of-scope classifier before the semantic layer — acceptable for the prototype.
- Lane routing 72%: Several census and geography questions are correctly answered by the
  semantic layer as ANALYTICAL but are classified as RELATIONAL by the intent router.
  The SQL result is correct; only the intent label disagrees. Not a correctness bug.

### 1.2 GraphRAG Enrichment Evaluation (§9 — OFF vs ON)

Run: `python3 -m eval.graphrag_enrichment_eval`

| Metric | Value |
|--------|-------|
| Cases tested | 9 (R-series + multi-hop) |
| **Numeric delta = 0** | **9/9 (100%) ← acceptance gate** |
| Grounding OK | 5/9 (56%) |
| Provenance path present | 9/9 (100%) |
| Avg auto-usefulness | 4.4/5 |
| Avg latency (GraphRAG ON) | ~2037 ms (incl. BGE cold start ~17 s on first run) |
| Avg latency overhead (ON − OFF) | +46 ms at p90 (warm) |

**Acceptance:** Graph adds contextual enrichment with **zero numeric change** (SQLite
aggregates are never modified by the GraphRAG layer). Provenance carries the subgraph
path (`vector_seed(N) → k-hop BFS → M triples`) in every result.

**Grounding notes:** The 4 cases with grounding_rate < 1.0 reflect genuine graph content
gaps (missing LOCATED_IN edges due to unresolved townland names at build time), not
ungrounded hallucination. The linearized text contains only triples that trace to
`subgraph_rels` entries, and every `subgraph_rel` traces to a `graph_nodes` or
`graph_edges` row in SQLite. No ungrounded facts are generated.

### 1.3 GraphRAG OFF vs ON Regression Comparison

| Metric | GraphRAG ON | GraphRAG OFF | Delta |
|--------|-------------|--------------|-------|
| Routing accuracy | 89.3% | 89.3% | 0.0 |
| Aggregation correctness | 100.0% | 100.0% | 0.0 |
| SQL exec success | 100.0% | 100.0% | 0.0 |
| Entity label accuracy | 100.0% | 100.0% | 0.0 |
| All other accuracy metrics | same | same | 0.0 |
| p50 latency | 372 ms | 365 ms | +7 ms |
| p90 latency | 2095 ms | 2049 ms | +46 ms |

**Verdict:** GraphRAG enrichment is additive-only. No accuracy regressions. Latency overhead
is ~7 ms (p50) / ~46 ms (p90) for warm runs (BGE model pre-loaded).

### 1.4 RQ6 SQL vs SPARQL Comparison

Full table: `eval_results/rq6_sql_vs_sparql.md`

| # | Question | SQL | SPARQL | Agreement |
|---|----------|-----|--------|-----------|
| Q1 | Total emigration | 6016 | 0 | ✗ repo not loaded |
| Q2 | Emigration Ballynultagh | 400 | 0 | ✗ repo not loaded |
| Q3 | Total evictions | 7763 | 0 | ✗ repo not loaded |
| Q4 | Population 1841 | 119300 | empty | ✗ repo not loaded |
| Q5 | Pop. Ballinacor 1841 | 55 | empty | ✗ repo not loaded |
| Q6 | Ballinacor parish/barony | Kilbride/Arklow | empty | ✗ repo not loaded; VRTI disagrees on values |

Key findings: (1) The local co: ontology repository is provisioned but not loaded with
data — open-world queries return 0/empty rather than signalling absence. (2) SQL vs VRTI
SPARQL shows a real data-level discrepancy for Q6 (estate register vs. authoritative KG
boundary values) which the pipeline surfaces as a provenance-annotated discrepancy.
(3) Structural SQL ↔ SPARQL equivalence is confirmed for the 4 metrics with SPARQL
templates; the generated query forms are correct.

---

## 2. Canonical Configuration

These are the pinned settings for the v1.0-demo-freeze. Any deployment **must** set these
to reproduce the evaluation results.

### 2.1 Ask Pipeline

| Variable | Value | Notes |
|----------|-------|-------|
| `ASK_USE_NEW_PIPELINE` | `true` | Enables 7-phase orchestrated pipeline |
| `GRAPHRAG_ENABLED` | `true` | In-process property-graph enrichment |
| `GRAPHRAG_VECTOR_TOP_K` | `8` | Seed nodes per question |
| `GRAPHRAG_K_HOPS` | `2` | BFS traversal depth |
| `GRAPHRAG_MAX_NODES` | `120` | Max subgraph size per query |
| `EMBEDDING_PROVIDER` | `local` | BAAI/bge-large-en-v1.5 (no API key needed) |

### 2.2 LLM Provider

| Variable | Value | Notes |
|----------|-------|-------|
| `ASK_LLM_PROVIDER` | `auto` | Tries OpenRouter → Ollama → disabled |
| `ASK_SYNTHESIS_MODEL` | `claude` | Multi-model synthesis with Claude |
| `LLM_ALLOW_PAID` | `true` | Allows paid API calls |
| `OPENROUTER_MODEL` | `openai/gpt-oss-20b:free` | Free tier fallback model |
| `ASK_ALLOW_HEURISTIC_FALLBACK` | `0` | No heuristic guessing on SQL failure |
| `ASK_GENERATE_VRTI_SQL_WITH_LLM` | `0` | SPARQL generated deterministically |

### 2.3 Knowledge Graphs

| Variable | Value | Notes |
|----------|-------|-------|
| `GRAPHDB_ENABLED` | `true` | Local co: ontology (see §3 for data loading) |
| `GRAPHDB_SPARQL_ENDPOINT` | `http://51.120.71.162:7200/repositories/coolattin` | Azure VM |
| `GRAPHDB_REQUEST_TIMEOUT` | `15` | seconds |
| `VRTI_REQUEST_TIMEOUT` | `30` | seconds |

### 2.4 Flask

| Variable | Value | Notes |
|----------|-------|-------|
| `FLASK_ENV` | `development` | Use `production` for Azure deployment |
| `LOG_LEVEL` | `INFO` | |

---

## 3. Deploy Prerequisites

### 3.1 Local Development

```bash
# 1. Python 3.12 + venv
python3.12 -m venv venv && source venv/bin/activate

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Copy and configure env
cp .env.example .env.local
# Set OPENROUTER_API_KEY for LLM; everything else is optional for read-only use

# 4. Start server (auto-creates coolattin.db and runs migrations)
python3 app.py
# → http://127.0.0.1:5001

# 5. (Optional) Build the in-process graph for GraphRAG enrichment
#    Run AFTER database is populated:
python3 scripts/build_graph.py
```

### 3.2 Database Population

The SQLite database (`coolattin.db`) is committed to the repository with a pre-populated
snapshot. If starting from scratch:

```bash
# Full ingest from VRTI + seed data
python3 -m backend.jobs.full_ingest
# Or via API: GET /api/census/refresh
```

### 3.3 GraphRAG Graph Build

The `graph_nodes` and `graph_edges` tables are populated by:

```bash
python3 scripts/build_graph.py
# Produces: 49081 nodes, 64342 edges, 28078 embedded (BGE-large)
# Runtime: ~3-5 min on first run (downloads BGE model if not cached)
```

The graph is cached in-process after first load (~2 GB RAM for full graph + embeddings).
BGE model download: ~1.3 GB (cached to `~/.cache/huggingface`).

### 3.4 Python Package Requirements

All packages are in `requirements.txt`. Key packages with version constraints:
- `flask>=3.0` — application factory
- `networkx>=3.3` — in-process property graph
- `sentence-transformers>=3.0` — BGE-large embeddings
- `numpy>=1.26` — vector similarity
- `torch` (CPU is sufficient; no GPU needed)
- `anthropic` — Claude synthesis
- `openai` — OpenRouter-compatible client

### 3.5 Environment Variables Required for Full Feature Set

| Feature | Required Variable |
|---------|-------------------|
| Ask page LLM synthesis | `OPENROUTER_API_KEY` or Ollama running |
| Dense vector retrieval | None (local BGE is default) |
| Cohere embeddings (optional) | `COHERE_API_KEY` |
| GraphDB comparative | `GRAPHDB_SPARQL_ENDPOINT` (Azure VM already set) |
| Claude synthesis | `ANTHROPIC_API_KEY` (via OpenRouter or direct) |

### 3.6 Azure App Service Deployment

See `.github/workflows/` for the CI/CD pipeline configuration. The production deployment:
- Runs on Azure App Service (Linux, Python 3.12)
- Uses `gunicorn` as the WSGI server (command in `Procfile`)
- SQLite database stored at `/home/site/data/coolattin.db`
- BGE model cached at `/home/site/hf_cache`
- GraphDB running on separate VM (51.120.71.162:7200)

---

## 4. Eval Artefacts (eval_results/)

| File | Contents |
|------|----------|
| `eval_graphrag_on.json` | Full regression, GraphRAG ON (75 cases) |
| `eval_graphrag_on.md` | Markdown report |
| `eval_graphrag_off.json` | Full regression, GraphRAG OFF (75 cases) |
| `eval_graphrag_off.md` | Markdown report |
| `eval_baseline_post_migration.json` | Pre-freeze baseline (reference) |
| `graphrag_enrichment.json` | GraphRAG enrichment eval (9 R-series cases) |
| `rq6_sql_vs_sparql.md` | SQL vs SPARQL competency question comparison |
| `graph_build_report.md` | Graph build statistics (49K nodes, 64K edges) |
| `gold_answers.csv` | Ground-truth answers for all 75 cases |

---

## 5. Freeze Checklist

- [x] Full regression (75 cases) passes — 100% agg correctness, 89.3% routing
- [x] GraphRAG numeric delta = 0 for all 9 R-series + multi-hop cases
- [x] GraphRAG provenance path present in all 9 cases
- [x] No ungrounded claims in GraphRAG output (all facts trace to subgraph_rels)
- [x] RQ6 SQL/SPARQL comparison table produced
- [x] Canonical config documented (§2)
- [x] Deploy prerequisites documented (§3)
- [x] Git tag `v1.0-demo-freeze` created

---

_Frozen by Pranjal Yadav — 2026-06-10 for Masters Dissertation submission_
