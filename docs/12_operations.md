# Operations Runbook

Practical commands for running, ingesting and checking the application locally. All commands assume the repository root as the working directory.

## 1. Environment

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env.local
```

`.env.local` holds local secrets and overrides. Configuration precedence is: process environment → `.env.local` → `.env` → code defaults.

## 2. Run the development server

```bash
source venv/bin/activate && python3 app.py
```

The server listens on <http://127.0.0.1:5001> in debug mode.

| Route | Page |
| --- | --- |
| `/` | Interactive map — townland boundaries, surname and record filtering |
| `/census` | Census browser (1841–1891 standard, 1827–1868 estate) |
| `/analytics` | KPI dashboards (emigration, eviction, workhouse) |
| `/ask` | Natural-language Q&A (requires LLM configuration in `.env.local`) |
| `/heritage` | NMS heritage monument overlay |
| `/kg-explore` | Knowledge-graph explorer |
| `/about`, `/info` | Project information |

Health checks:

```bash
curl http://127.0.0.1:5001/api/ask/llm-status
curl http://127.0.0.1:5001/api/townlands
```

## 3. Full ingest

Populates the database from the VRTI Knowledge Graph and the estate seed data. The schema is created and migrated automatically on first run.

```bash
source venv/bin/activate
python3 -c "
from backend.app import create_app
app = create_app()
with app.app_context():
    from backend.jobs.full_ingest import run_full_ingest
    run_full_ingest()
    print('Ingest complete')
"
```

Verify the row counts afterwards:

```bash
python3 -c "
from backend.extensions import init_db, get_db_conn
from pathlib import Path
init_db(Path('coolattin.db'))
conn = get_db_conn()
for table in ['townland', 'census_record', 'clearances_record']:
    print(table, conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0])
conn.close()
"
```

If the VRTI endpoint times out, raise `VRTI_REQUEST_TIMEOUT` in `.env.local`.

## 4. Reset the database

**Destructive.** This deletes all data in `coolattin.db`; only run when you intend to repopulate from the VRTI KG and the seed files.

```bash
source venv/bin/activate
rm -f coolattin.db
```

Then run the full ingest from section 3. A successful schema initialisation does not establish that source tables, links or graph artefacts have been populated — always confirm the counts.

## 5. Syntax check

```bash
source venv/bin/activate

find . -name "*.py" \
  -not -path "./venv/*" \
  -not -path "./__pycache__/*" \
  | sort \
  | xargs -I{} sh -c 'python3 -m py_compile "{}" || echo "FAIL: {}"'

python3 -c "from backend import config, extensions, app; print('imports OK')"
```

## 6. Common failures

| Symptom | Likely cause | Action |
| --- | --- | --- |
| `/api/ask/llm-status` reports `"available": false` | Missing or invalid provider key | Add `ANTHROPIC_API_KEY` / `OPENROUTER_API_KEY` to `.env.local` |
| Ask answers lack administrative context | VRTI SPARQL timed out and entered its cooldown | Check network access; raise `VRTI_REQUEST_TIMEOUT` |
| PDF download 404s | `exports/ask/` missing | `mkdir -p exports/ask` |
| Townland not recognised | Name absent from the database or alias list | Check `data/seed/townland_aliases.json` |
| Ask returns a diagnostic row instead of a count | SQL repair layers exhausted; heuristic fallback disabled | Expected default behaviour (`ASK_ALLOW_HEURISTIC_FALLBACK=false`) |
