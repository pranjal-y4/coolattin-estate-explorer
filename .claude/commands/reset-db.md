Drop and recreate the SQLite database schema, then run the full ingest. Use this when the schema needs to be reset to a clean state.

**Warning:** This deletes all data in `coolattin.db`. Only run if you intend to repopulate from the VRTI KG and seed files.

```bash
source venv/bin/activate

# Delete the existing database
rm -f coolattin.db

# Recreate schema + run full ingest
python3 -c "
from create_app import create_app
app = create_app()
with app.app_context():
    from backend.jobs.full_ingest import run_full_ingest
    run_full_ingest()
    print('Database reset and ingested')
"
```

After this completes, verify counts:
```bash
python3 -c "
from extensions import init_db, get_db_conn
from pathlib import Path
init_db(Path('coolattin.db'))
conn = get_db_conn()
for table in ['townland', 'census_record', 'clearances_record']:
    n = conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
    print(f'{table}: {n} rows')
conn.close()
"
```
