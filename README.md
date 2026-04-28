# Coolattin Estate Records Explorer

An interactive web application for exploring historical records from the **Coolattin Estate** in County Wicklow, Ireland (mid-19th century). Built as a Masters Dissertation project.

The application integrates tenancy, eviction, emigration, and census data from the [Virtual Record Treasury of Ireland (VRTI)](https://virtualtreasury.ie/) Knowledge Graph into a unified interface with an interactive map, analytics dashboards, and a natural-language Q&A system.

---

## Features

- **Interactive Map** — Leaflet.js map of Coolattin townland boundaries with population and clearances overlays
- **Census Browser** — Population data 1841–1891 (from VRTI KG) plus estate survey years 1827–1868
- **Analytics Dashboards** — KPI summaries and charts for emigration, evictions, workhouse, and tenancy datasets
- **Ask (LLM Q&A)** — Natural-language questions answered via SQL + LLM rewrite with SSE streaming; PDF export of results
- **Heritage Map** — NMS monuments and holy wells overlay
- **Data Export** — Excel exports of census and townland data

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
│   ├── repositories/       # All SQL queries
│   ├── models/             # Typed dataclasses
│   ├── integrations/       # VRTI SPARQL client
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
├── scripts/                # One-off data processing scripts
├── extra_datasets/         # NMS heritage open-data CSVs
└── _archive/               # Deprecated code (reference only)
```

**Stack:** Python 3.12 · Flask · SQLite · VRTI SPARQL · OpenRouter/Ollama LLM · Vanilla JS · Leaflet.js

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

The SQLite database (`coolattin.db`) is created and schema-migrated automatically on first run. It is empty until populated by an ingest job (see below).

---

## Data ingestion

The application fetches live data from the VRTI Knowledge Graph. After first run, populate the database:

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

### Ollama (local, offline fallback)

```bash
# Install Ollama, then pull a model
ollama serve
ollama pull llama3.1:8b
```

Add to `.env.local`:

```env
ASK_LLM_PROVIDER=ollama
OLLAMA_MODEL=llama3.1:8b
```

### How the Ask pipeline works

1. 100+ SQL templates matched by keyword scoring — instant answers, no LLM needed
2. Townland names resolved: exact → fuzzy → "did you mean?" suggestions
3. If no template matches, the configured LLM generates SQL
4. All SQL is validated as read-only before execution
5. LLM rephrases the raw database answer
6. VRTI SPARQL enriches with parish context (parallel)
7. PDF report generated and downloadable

---

## Environment variables

See [`.env.example`](.env.example) for the full documented list. Key variables:

| Variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | `dev-secret-...` | Flask session secret — change in production |
| `FLASK_ENV` | `development` | `development` or `production` |
| `ASK_LLM_PROVIDER` | `auto` | `auto` / `openrouter` / `ollama` / `none` |
| `OPENROUTER_API_KEY` | — | Required for cloud LLM |
| `OPENROUTER_MODEL` | `openai/gpt-oss-20b:free` | OpenRouter model ID |
| `OLLAMA_MODEL` | — | Ollama model name |
| `VRTI_REQUEST_TIMEOUT` | `30` | SPARQL endpoint timeout (seconds) |

---

## API endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Index / map page |
| `GET` | `/census` | Census browser page |
| `GET` | `/analytics` | Analytics dashboards |
| `GET` | `/ask` | LLM Q&A page |
| `GET` | `/heritage` | Heritage monuments page |
| `POST` | `/api/ask/query` | SSE-streamed Q&A pipeline |
| `GET` | `/api/ask/llm-status` | LLM provider health check |
| `GET` | `/api/ask/townland-suggest` | Fuzzy townland suggestions |
| `GET` | `/api/ask/pdf/<name>` | Download PDF report |
| `GET` | `/api/census/` | Census data (JSON) |
| `GET` | `/api/census/refresh` | Trigger VRTI census refresh |
| `GET` | `/api/townlands/list` | Townland list |
| `GET` | `/api/map/config` | Map configuration + centroids |
| `GET` | `/api/unified/analytics` | Analytics module results |
| `GET` | `/api/exports/census` | Download latest census Excel |

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
| Coolattin Estate GeoJSON | Townland boundaries, estate survey data 1827–1868 |
| [National Monuments Service](https://www.archaeology.ie/) | Heritage monuments open data |
| `data/seed/` | Canonical townland reference and name aliases |

---

## Dissertation context

This application was developed as part of a Masters dissertation examining digital humanities approaches to Irish Famine-era estate records. The Coolattin Estate (owned by the Fitzwilliam family) is notable for its large-scale assisted emigration programme during the Famine years 1847–1856.
