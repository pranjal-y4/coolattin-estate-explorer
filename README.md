# Coolattin Estate Records Explorer

youtube - 
app - 
do let me know what you think about this - 

An interactive web application for exploring historical records from the **Coolattin Estate** in County Wicklow, Ireland (mid-19th century). Built as a Masters Dissertation project.

The application integrates tenancy, eviction, emigration, and census data from the [Virtual Record Treasury of Ireland (VRTI)](https://virtualtreasury.ie/) Knowledge Graph into a unified interface with an interactive map, analytics dashboards, and a natural-language Q&A system backed by an LLM.

**Live deployment:** `coolattin-app.azurewebsites.net` (Azure App Service, Italy North) · **Academic freeze:** `v1.0-demo-freeze` (2026-06-10)

---

## Features

- **Interactive Map** — Leaflet.js map of Coolattin townland boundaries with population and clearances overlays; workhouse match cards on record markers
- **Census Browser** — Population data 1841–1891 (from VRTI KG) plus estate survey years 1827–1868
- **Analytics Dashboards** — KPI summaries and charts for emigration, evictions, workhouse, and tenancy datasets
- **Ask (LLM Q&A)** — Natural-language questions answered via four fast lanes (rule-fill, verified templates, query memory, embedding retrieval), a 7-phase orchestrated pipeline, GraphRAG enrichment, multi-model synthesis, and PDF export
- **KG Explore** — D3.js force graph of the 152-townland property graph; SQL-vs-SPARQL comparison scenarios
- **Heritage Map** — NMS monuments and holy wells overlay
- **Data Export** — Excel exports of census and townland data
- **Workhouse Entity Resolution** — Phonetic blocking + 7-signal scored matching of workhouse admission records to unified estate records (140 confirmed links)

---

## Architecture

```
Coolattin-app/
├── app.py                  # Entry point
├── create_app.py           # Flask application factory
├── config.py               # Centralised configuration
├── extensions.py           # DB singleton (sqlite3)
│
├── backend/
│   ├── routes/             # Flask blueprints (one per URL prefix)
│   ├── services/           # Business logic
│   │   ├── ask_service.py  #   Orchestrated 7-phase pipeline + SSE streaming
│   │   ├── graphrag.py     #   In-process property graph (49K nodes, 64K edges)
│   │   ├── kg_service.py   #   Knowledge graph service layer
│   │   ├── intent_router.py #  Intent classification (ANALYTICAL/RELATIONAL/…)
│   │   ├── semantic_layer.py # Deterministic SQL + SPARQL compiler
│   │   ├── subgraph_engine.py # KG traversal (VRTI + GraphDB)
│   │   ├── embedding_index.py # Hybrid TF-IDF + dense retrieval (RRF)
│   │   ├── identity_resolver.py # Three-layer identity model
│   │   └── workhouse_entity_resolution.py # Workhouse ER pipeline
│   ├── repositories/       # All SQL queries
│   ├── models/             # Typed dataclasses
│   ├── integrations/       # VRTI SPARQL + GraphDB SPARQL clients
│   └── jobs/               # Data ingestion jobs
│
├── analytics/              # Pluggable analytics modules (KPI + chart data)
│
├── frontend/
│   ├── templates/          # Jinja2 HTML (one per page)
│   └── static/
│       ├── css/            # Stylesheet
│       ├── js/             # Vanilla JS (one file per page)
│       ├── data/           # Static GeoJSON, CSV, seed JSON
│       └── images/
│
├── data/seed/              # Canonical reference data (townland aliases, etc.)
├── scripts/                # One-off data processing scripts (incl. build_graph.py)
├── extra_datasets/         # NMS heritage open-data CSVs
└── _archive/               # Deprecated code (reference only)
```

**Stack:** Python 3.12 · Flask · SQLite · NetworkX (in-process graph) · VRTI SPARQL · GraphDB SPARQL · OpenRouter/Claude/Ollama LLM · BAAI/bge-large-en-v1.5 · Vanilla JS · Leaflet.js · D3.js

---

## Quick start

### Prerequisites

- Python 3.12+
- (Optional) [Ollama](https://ollama.ai) for local offline LLM

### Installation

```bash
# 1. Clone
git clone <repo-url>
cd Coolattin-app

# 2. Create virtual environment
python3 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate

# 3. Install Python dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env.local
# Edit .env.local — add your OPENROUTER_API_KEY for the Ask page

# 5. Run the server
python3 app.py
```

Open [http://127.0.0.1:5001](http://127.0.0.1:5001).

The SQLite database (`coolattin.db`) is created and schema-migrated automatically on first run. It is pre-populated in the repository snapshot.

---

## Data ingestion

The application fetches live data from the VRTI Knowledge Graph. To re-populate from scratch:

```bash
python3 -c "
from create_app import create_app
app = create_app()
with app.app_context():
    from backend.jobs.full_ingest import run_full_ingest
    run_full_ingest()
"
```

Or trigger a refresh via the API while the server is running:

```
GET /api/census/refresh
```

### GraphRAG graph build

The Ask pipeline uses an in-process property graph for GraphRAG enrichment. Build it once after populating the database:

```bash
python3 scripts/build_graph.py
# Produces: 49,081 nodes · 64,342 edges · 28,078 BGE-embedded
# Runtime: ~3–5 min (downloads BGE model ~1.3 GB on first run)
```

The graph is stored in `graph_nodes` and `graph_edges` SQLite tables and loaded into a NetworkX graph in-process at startup.

---

## LLM / Ask page setup

The Ask page at `/ask` answers natural-language questions about the historical data.

### OpenRouter (recommended — free tier available)

1. Get a free API key at [openrouter.ai](https://openrouter.ai)
2. Add to `.env.local`:

```env
ASK_LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-...
OPENROUTER_MODEL=openai/gpt-oss-20b:free
```

### Claude (highest-quality synthesis)

```env
ANTHROPIC_API_KEY=sk-ant-...
ASK_SYNTHESIS_MODEL=claude
LLM_ALLOW_PAID=true
```

### Ollama (local, offline fallback)

```bash
ollama serve
ollama pull llama3.1:8b
```

```env
ASK_LLM_PROVIDER=ollama
OLLAMA_MODEL=llama3.1:8b
```

### How the Ask pipeline works

The orchestrated pipeline (`ASK_USE_NEW_PIPELINE=true`, default) runs seven phases plus an in-process GraphRAG enrichment layer:

**Pre-flight (no LLM, < 5 ms):**

1. **Townland resolution** — fuzzy-matches the question to a canonical townland name using rapidfuzz (threshold 80) and the alias catalogue.
2. **Question analysis** — extracts year, surname, radius; classifies `primary_intent`, `output_mode`, `scope` via regex; no LLM.

**Four fast lanes (first match short-circuits all routing):**

| Lane | Mechanism | Threshold |
|---|---|---|
| Rule-fill | 22-metric keyword match → deterministic SQL | confidence ≥ 0.80 |
| Verified template | 83 pre-verified SQL templates scored by required/optional keywords | template in verified set |
| Memory reuse | Approved question→SQL pairs from thumbs-up feedback | token_sort_ratio + cosine ≥ 0.55 |
| Embedding fast lane | TF-IDF + RRF over templates + memory | cosine ≥ 0.68 AND all required keywords present |

**Seven pipeline phases:**

1. **Identity resolution** (`identity_resolver.py`) — resolves townland + person mentions to surrogate IDs; Jaro-Winkler + Metaphone phonetic blocking + geo/temporal scoring.
2. **Semantic layer** (`semantic_layer.py`) — 22-metric slot-fill compiler; deterministic SQL + equivalent SPARQL (no LLM on fast path).
3. **Subgraph engine** (`subgraph_engine.py`) — VRTI multi-hop SPARQL + GraphDB k=2 neighbourhood for RELATIONAL/HERITAGE questions.
4. **Embedding retrieval** (`embedding_index.py`) — TF-IDF unigram+bigram cosine + RRF across templates, memory, and corpus chunks.
5. **Intent routing** (`intent_router.py`) — classifies as ANALYTICAL / RELATIONAL / COMPARATIVE / FALLBACK (priority order).
6. **Fusion** — cross-source discrepancy detection (SQLite vs GraphDB numeric results).
7. **Multi-model synthesis** — LLM chain **Claude → Grok → OpenRouter → Ollama**; aggregates SQL + KG results into provenance-annotated answer with optional PDF export.

**GraphRAG enrichment (parallel, non-blocking):**

After SQL execution, the in-process property graph (`graphrag.py`) retrieves a k-hop subgraph around the resolved entity using BGE-large vector seeding. The linearised subgraph provides qualitative place/people context to the synthesis LLM. Graph enrichment never modifies SQL aggregates — it is additive only (validated: numeric delta = 0 across all 9 R-series evaluation cases).

All SQL (template or LLM-generated) is validated as read-only before execution. Approved answers are stored in query memory and reused on semantically similar future questions. Grouped or statistical results can be rendered as a chart.

**Workhouse entity resolution** is a separate subsystem (`workhouse_entity_resolution.py` + `entity_resolution/` package) that links workhouse admission records to unified estate records using deterministic normalisation, phonetic blocking, and 7-signal scored confidence bands (CONFIRMED ≥ 0.75 / POSSIBLE ≥ 0.50 / WEAK < 0.50). It does not use the Ask pipeline, pgvector, or the LLM.

---

## Environment variables

See [`.env.example`](.env.example) for the full documented list. Key variables:

| Variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | `dev-secret-...` | Flask session secret — change in production |
| `FLASK_ENV` | `development` | `development` or `production` |
| `DATABASE_PATH` | `coolattin.db` in repo root | SQLite file path; set to `/home/site/data/coolattin.db` on Azure |
| `ASK_LLM_PROVIDER` | `auto` | `auto` / `openrouter` / `ollama` / `none` |
| `OPENROUTER_API_KEY` | — | Required for cloud LLM |
| `OPENROUTER_MODEL` | `openai/gpt-oss-20b:free` | OpenRouter model ID |
| `ANTHROPIC_API_KEY` | — | Required for Claude synthesis (`ASK_SYNTHESIS_MODEL=claude`) |
| `GROK_API_KEY` | — | Grok (xAI) API key — second in multi-model synthesis chain |
| `ASK_SYNTHESIS_MODEL` | `claude` | Synthesis model: `claude` / `openrouter` / `ollama` |
| `LLM_ALLOW_PAID` | `false` | Allow paid API calls (`true` required for Claude/Grok) |
| `ASK_ALLOW_HEURISTIC_FALLBACK` | `0` | `0` = fail safely; `1` = allow heuristic SQL guessing |
| `OLLAMA_MODEL` | — | Ollama model name |
| `VRTI_REQUEST_TIMEOUT` | `30` | SPARQL endpoint timeout (seconds) |
| `ASK_USE_NEW_PIPELINE` | `true` | `true` = orchestrated 7-phase pipeline; `false` = legacy path |
| `EMBEDDING_PROVIDER` | `local` | `local` (BAAI/bge-large-en-v1.5) / `cohere` / `voyage` |
| `COHERE_API_KEY` | — | Required when `EMBEDDING_PROVIDER=cohere` |
| `VOYAGE_API_KEY` | — | Required when `EMBEDDING_PROVIDER=voyage` (recommended on Azure) |
| `VOYAGE_MODEL` | `voyage-3` | Voyage AI model name |
| `ADMIN_API_KEY` | — | Protects admin endpoints (`POST /api/census/refresh`); set in production |
| `DATABASE_URL` | — | PostgreSQL connection string; enables pgvector backend for Ask retrieval |
| `GRAPHDB_ENABLED` | `true` | Query local GraphDB alongside SQLite and VRTI |
| `GRAPHDB_SPARQL_ENDPOINT` | `http://localhost:7200/...` | GraphDB SPARQL endpoint |
| `GRAPHDB_REQUEST_TIMEOUT` | `15` | GraphDB query timeout (seconds) |
| `GRAPHRAG_ENABLED` | `true` | In-process property-graph enrichment |
| `GRAPHRAG_VECTOR_TOP_K` | `8` | Seed nodes per question (BGE vector search) |
| `GRAPHRAG_K_HOPS` | `2` | BFS traversal depth from seed nodes |
| `GRAPHRAG_MAX_NODES` | `120` | Max subgraph size per query |

---

## API endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Index / map page |
| `GET` | `/census` | Census browser page |
| `GET` | `/analytics` | Analytics dashboards |
| `GET` | `/ask` | LLM Q&A page |
| `GET` | `/heritage` | Heritage monuments page |
| `GET` | `/kg-explore` | KG explore: force graph + SQL-vs-SPARQL comparison |
| `POST` | `/api/ask/query` | SSE-streamed Q&A pipeline |
| `GET` | `/api/ask/llm-status` | LLM provider health check |
| `POST` | `/api/ask/feedback` | Save thumbs up/down feedback and approved query memory |
| `GET` | `/api/ask/townland-suggest` | Fuzzy townland suggestions |
| `GET` | `/api/ask/pdf/<name>` | Download PDF report |
| `GET` | `/api/census/` | Census data (JSON) |
| `GET` | `/api/census/refresh` | Trigger VRTI census refresh |
| `GET` | `/api/townlands/list` | Townland list |
| `GET` | `/api/map/config` | Map configuration + centroids |
| `GET` | `/api/unified/analytics` | Analytics module results |
| `GET` | `/api/unified/records` | Search unified person records |
| `GET` | `/api/exports/census` | Download latest census Excel |
| `GET` | `/api/kg/graph` | D3 force-graph data (152 townland nodes) |
| `GET` | `/api/kg/scenarios` | SQL-vs-SPARQL comparison scenarios |
| `POST` | `/api/kg/compare` | Execute SQL + SPARQL and return side-by-side results |

---

## Evaluation results (v1.0-demo-freeze)

Formal evaluation run on 2026-06-10 against 75 competency questions:

| Metric | Value |
|---|---|
| Routing accuracy | 89.3% |
| Aggregation correctness | 100.0% |
| SQL execution success | 100.0% |
| Template hit rate | 100.0% |
| LLM calls required | 0 (all answers deterministic) |
| p50 latency | 372 ms |
| p90 latency | 2,095 ms |
| GraphRAG numeric delta | 0/9 (enrichment never modifies aggregates) |

Full results: [`docs/11_demo_freeze.md`](docs/11_demo_freeze.md) · [`eval_results/`](eval_results/)

---

## Claude Code

This project includes [Claude Code](https://claude.ai/code) configuration in `.claude/`:

| File | Purpose |
|---|---|
| `.claude/settings.json` | Permissions + post-edit Python syntax hook |
| `.claude/agents/data-ingest.md` | Subagent: run data ingestion pipeline |
| `.claude/agents/analytics-qa.md` | Subagent: verify analytics output |
| `.claude/agents/llm-debug.md` | Subagent: debug Ask/LLM pipeline |
| `.claude/commands/ingest.md` | `/ingest` — populate database from VRTI |
| `.claude/commands/serve.md` | `/serve` — start dev server |
| `.claude/commands/reset-db.md` | `/reset-db` — wipe and re-ingest |
| `.claude/commands/check.md` | `/check` — syntax check all Python files |
| `CLAUDE.md` | Project context for AI-assisted development |

---

## Data sources

| Source | Description |
|---|---|
| [VRTI Knowledge Graph](https://virtualtreasury.ie/) | Townland metadata, census records 1841–1891 |
| Coolattin Estate GeoJSON | 152 townland boundaries with estate survey data 1827–1868 |
| `unified_processed.csv` | 13,707 person-level records (tenants, emigrants, evictees) |
| [National Monuments Service](https://www.archaeology.ie/) | Heritage monuments open data (ring forts, holy wells) |
| Workhouse admission records | Fuzzy-linked via ER pipeline (140 confirmed links) |
| `data/seed/` | Canonical townland reference, name aliases, community summaries |

---

## Deploy to Azure App Service

This app runs on **Azure App Service (Linux)** at `coolattin-app.azurewebsites.net` (resource group `coolattin-rg2`, Italy North). It uses **SQLite** with WAL mode — one App Service instance with a persistent `DATABASE_PATH`.

### CI/CD (push to main → auto-deploy)

Every push to `main` triggers `.github/workflows/azure-deploy.yml`:

1. Logs in to Azure via OIDC (managed identity `coolattin-gh-identity`, secrets `AZURE_CLIENT_ID` / `AZURE_TENANT_ID` / `AZURE_SUBSCRIPTION_ID`)
2. Replaces `requirements.txt` with `requirements-azure.txt` (strips `psycopg` and heavy ML packages not used on Azure)
3. Zips the repo (excluding venv, docs, tests, source snapshots, eval results)
4. Deploys via `az webapp deploy --type zip`; Azure Oryx builds the venv on the target machine
5. Enforces the gunicorn startup command via `az webapp config set`

The startup command runs **2 workers × 4 gthread threads** on the `$PORT` that Azure assigns:

```
gunicorn --bind=0.0.0.0:$PORT --timeout 600 --workers 2 --worker-class gthread --threads 4 app:app
```

### Files prepared for Azure

| File | Purpose |
|---|---|
| `requirements-azure.txt` | Azure-safe deps (no `torch`, `sentence-transformers`, `psycopg`) |
| `Procfile` | Oryx auto-detection fallback for gunicorn command |
| `startup.sh` | Lazy `pip install` on first boot if antenv is absent; subsequent starts reuse antenv |
| `.webappignore` | Excludes secrets, venv, docs, tests from `az webapp up` uploads |
| `.github/workflows/azure-deploy.yml` | Active CI/CD pipeline (OIDC + Oryx zip deploy) |
| `.github/workflows/main_coolattin-archive.yml` | Legacy workflow (superseded; left for reference) |

### Required Azure App Service settings

Set these in the Azure portal or via `az webapp config appsettings set`:

```bash
az webapp config appsettings set \
  --resource-group coolattin-rg2 \
  --name coolattin-app \
  --settings \
    SCM_DO_BUILD_DURING_DEPLOYMENT=true \
    FLASK_ENV=production \
    SECRET_KEY="<strong-random-secret>" \
    ASK_LLM_PROVIDER=auto \
    OPENROUTER_API_KEY="<your-openrouter-key>" \
    OPENROUTER_MODEL="openai/gpt-oss-20b:free" \
    ANTHROPIC_API_KEY="<your-anthropic-key>" \
    ASK_SYNTHESIS_MODEL=claude \
    LLM_ALLOW_PAID=true \
    EMBEDDING_PROVIDER=voyage \
    VOYAGE_API_KEY="<your-voyage-key>" \
    GROK_API_KEY="<your-grok-key>" \
    ADMIN_API_KEY="<strong-admin-secret>" \
    GRAPHDB_ENABLED=true \
    GRAPHDB_SPARQL_ENDPOINT="http://51.120.71.162:7200/repositories/coolattin" \
    GRAPHDB_REQUEST_TIMEOUT=15 \
    GRAPHRAG_ENABLED=true \
    GRAPHRAG_VECTOR_TOP_K=8 \
    GRAPHRAG_K_HOPS=2 \
    GRAPHRAG_MAX_NODES=120 \
    DATABASE_PATH="/home/site/wwwroot/coolattin.db"
```

> **Note on embeddings:** `torch` and `sentence-transformers` (~2 GB) are excluded from the Azure build to avoid pip timeout/OOM. Set `EMBEDDING_PROVIDER=voyage` (or `cohere`) and supply the corresponding API key. The local BAAI/bge model is available only when running locally.

### Logs and troubleshooting

```bash
az webapp log tail --resource-group coolattin-rg2 --name coolattin-app
```

Common failures:
- **`gunicorn: command not found`** — the startup command was reset. The CI workflow re-enforces it on each deploy via `az webapp config set`.
- **SSE `No final result received`** — usually a single-worker deadlock. The startup command must use `--worker-class gthread --threads 4` to allow concurrent SSE connections.
- **Missing packages** — confirm `SCM_DO_BUILD_DURING_DEPLOYMENT=true` is set so Oryx builds from `requirements.txt` on the target machine.
- **BGE model absent** — expected on Azure; set `EMBEDDING_PROVIDER=voyage` instead.

---

## Dissertation context

This application was developed as part of a Masters dissertation examining digital humanities approaches to Irish Famine-era estate records at **Trinity College Dublin** (MSc Computer Science — Interactive Digital Media).

**Candidate:** Pranjal Yadav · **Supervisors:** Dr Ciarán Wallace (VRTI) · Prof Declan O'Sullivan (CS) · **Submission:** 3 August 2026

The Coolattin Estate (owned by the Fitzwilliam family) is notable for its large-scale assisted emigration programme during the Famine years 1847–1856. This system is the first computationally integrated interface for the Coolattin records, bringing tenancy, emigration, eviction, census, and heritage landscape data into a unified natural-language-queryable interface.
