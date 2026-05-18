# Coolattin Estate Records Explorer

An interactive web application for exploring historical records from the **Coolattin Estate** in County Wicklow, Ireland (mid-19th century). Built as a Masters Dissertation project.

The application integrates tenancy, eviction, emigration, and census data from the [Virtual Record Treasury of Ireland (VRTI)](https://virtualtreasury.ie/) Knowledge Graph into a unified interface with an interactive map, analytics dashboards, and a natural-language Q&A system.

---

## Features

- **Interactive Map** — Leaflet.js map of Coolattin townland boundaries with population and clearances overlays
- **Census Browser** — Population data 1841–1891 (from VRTI KG) plus estate survey years 1827–1868
- **Analytics Dashboards** — KPI summaries and charts for emigration, evictions, workhouse, and tenancy datasets
- **Ask (LLM Q&A)** — Natural-language questions answered via verified SQL analyses, schema-aware LLM SQL generation, approved query memory, feedback loops, charts, and PDF export
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

1. High-risk statistical questions use verified analysis SQL first, so known research questions are answered from fixed data-backed queries.
2. Townland names are resolved exact → fuzzy → suggestion with explicit provenance.
3. If no verified analysis matches, the configured LLM generates SQL from live schema, row counts, sampled categories, and approved past query patterns.
4. All SQL is validated as read-only, then rechecked semantically and repaired if SQLite rejects it; if no validated query can be produced, the system returns a clear rephrase message instead of guessing.
5. If a good query is confirmed with thumbs up, it is stored in approved query memory and can be reused later for similar questions.
6. The final answer is built from actual SQL results, related insights are computed from local data, and the LLM rewrite is rejected if it introduces unsupported numbers.
7. Grouped or statistical results can be rendered as a simple chart, and PDF export remains available.

---

## Environment variables

See [`.env.example`](.env.example) for the full documented list. Key variables:

| Variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | `dev-secret-...` | Flask session secret — change in production |
| `FLASK_ENV` | `development` | `development` or `production` |
| `DATABASE_PATH` | `coolattin.db` in repo root | Optional explicit SQLite file path; set this on Azure to a persistent path like `/home/site/data/coolattin.db` |
| `ASK_LLM_PROVIDER` | `auto` | `auto` / `openrouter` / `ollama` / `none` |
| `OPENROUTER_API_KEY` | — | Required for cloud LLM |
| `OPENROUTER_MODEL` | `openai/gpt-oss-20b:free` | OpenRouter model ID |
| `ASK_ALLOW_HEURISTIC_FALLBACK` | `0` | Leave at `0` to fail safely with a clear message instead of guessing with heuristic SQL |
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
| `POST` | `/api/ask/feedback` | Save thumbs up/down feedback and approved query memory |
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

## Deploy to Azure App Service

This app can run on **Azure App Service (Linux)**. The current architecture uses **SQLite**, so the safest production setup is:

- one App Service instance only
- a persistent SQLite path such as `/home/site/data/coolattin.db`
- `Always On` enabled if you use a paid plan

If you later need scale-out or multi-instance hosting, move the app database to PostgreSQL instead of SQLite.

### Files already prepared for Azure

- `requirements.txt` now includes `gunicorn`
- `startup.txt` contains the Gunicorn startup command used by App Service
- `DATABASE_PATH` can be provided through environment variables

### From scratch with Azure CLI

1. Install Azure CLI and sign in:

```bash
az login
az account set --subscription "<your-subscription-name-or-id>"
```

2. Choose names:

```bash
RESOURCE_GROUP="coolattin-rg"
PLAN_NAME="coolattin-plan"
APP_NAME="coolattin-archive-app"
LOCATION="westeurope"
RUNTIME="PYTHON:3.12"
```

3. Create the resource group:

```bash
az group create --name "$RESOURCE_GROUP" --location "$LOCATION"
```

4. Create a Linux App Service plan.

`B1` is a practical minimum because it supports `Always On`. Free tiers are fine for experiments, but less reliable for LLM-backed requests.

```bash
az appservice plan create \
  --name "$PLAN_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  --sku B1 \
  --is-linux
```

5. Create the web app:

```bash
az webapp create \
  --resource-group "$RESOURCE_GROUP" \
  --plan "$PLAN_NAME" \
  --name "$APP_NAME" \
  --runtime "$RUNTIME"
```

6. Configure the startup command:

```bash
az webapp config set \
  --resource-group "$RESOURCE_GROUP" \
  --name "$APP_NAME" \
  --startup-file startup.txt
```

7. Configure required app settings:

```bash
az webapp config appsettings set \
  --resource-group "$RESOURCE_GROUP" \
  --name "$APP_NAME" \
  --settings \
    SCM_DO_BUILD_DURING_DEPLOYMENT=1 \
    FLASK_ENV=production \
    SECRET_KEY="<strong-random-secret>" \
    ASK_LLM_PROVIDER=openrouter \
    OPENROUTER_API_KEY="<your-openrouter-key>" \
    OPENROUTER_MODEL="openai/gpt-oss-20b:free" \
    ASK_ALLOW_HEURISTIC_FALLBACK=0 \
    OPENROUTER_SITE_URL="https://$APP_NAME.azurewebsites.net" \
    OPENROUTER_APP_TITLE="Coolattin Archive Ask" \
    DATABASE_PATH="/home/site/data/coolattin.db"
```

8. Turn on Always On for paid plans:

```bash
az webapp config set \
  --resource-group "$RESOURCE_GROUP" \
  --name "$APP_NAME" \
  --always-on true
```

9. Build a deploy zip from your project root.

Do not include `venv`, `.venv`, `.git`, or large local-only folders.

```bash
zip -r coolattin-app.zip . -x "venv/*" ".venv/*" ".git/*" ".github/*" "__pycache__/*" "*.pyc" ".env.local" "coolattin-app.zip"
```

10. Deploy the zip package:

```bash
az webapp deploy \
  --resource-group "$RESOURCE_GROUP" \
  --name "$APP_NAME" \
  --src-path coolattin-app.zip
```

11. Open the site:

```bash
echo "https://$APP_NAME.azurewebsites.net"
```

### First-time production data setup

After the first deployment, you need to populate the SQLite database on Azure. You can do that by opening the SSH console in App Service and running the ingest job:

```bash
python3 -c "
from create_app import create_app
app = create_app()
with app.app_context():
    from backend.jobs.full_ingest import run_full_ingest
    run_full_ingest()
"
```

### Logs and troubleshooting

Stream logs with:

```bash
az webapp log tail \
  --resource-group "$RESOURCE_GROUP" \
  --name "$APP_NAME"
```

If the app starts but dependencies are missing, confirm that:

- `SCM_DO_BUILD_DURING_DEPLOYMENT=1` is set
- `requirements.txt` includes every runtime dependency
- `startup.txt` is configured as the startup file
- `DATABASE_PATH` points to `/home/site/...` rather than the repo root

### Optional: GitHub Actions continuous deployment

Once the app exists, you can wire GitHub Actions to it:

```bash
az webapp deployment github-actions add \
  --repo "<github-user>/<github-repo>" \
  --resource-group "$RESOURCE_GROUP" \
  --branch main \
  --name "$APP_NAME" \
  --login-with-github
```

That creates a workflow in `.github/workflows/` and adds the publish profile secret to the repo.

---

## Dissertation context

This application was developed as part of a Masters dissertation examining digital humanities approaches to Irish Famine-era estate records. The Coolattin Estate (owned by the Fitzwilliam family) is notable for its large-scale assisted emigration programme during the Famine years 1847–1856.
