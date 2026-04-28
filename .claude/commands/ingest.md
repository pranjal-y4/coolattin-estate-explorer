Run the full data ingestion pipeline to populate the database from the VRTI Knowledge Graph and the estate GeoJSON seed data.

Steps:
1. Verify the venv is active and Flask can import correctly.
2. Run the full ingest job inside an app context:

```bash
source venv/bin/activate
python3 -c "
from create_app import create_app
app = create_app()
with app.app_context():
    from backend.jobs.full_ingest import run_full_ingest
    run_full_ingest()
    print('Ingest complete')
"
```

3. After ingestion, verify row counts:

```bash
python3 -c "
from extensions import init_db, get_db_conn
from pathlib import Path
init_db(Path('coolattin.db'))
conn = get_db_conn()
print('Townlands:', conn.execute('SELECT COUNT(*) FROM townland').fetchone()[0])
print('Census records:', conn.execute('SELECT COUNT(*) FROM census_record').fetchone()[0])
print('Clearances:', conn.execute('SELECT COUNT(*) FROM clearances_record').fetchone()[0])
conn.close()
"
```

4. Report the counts and any errors encountered. If the VRTI endpoint timed out, suggest increasing `VRTI_REQUEST_TIMEOUT` in `.env.local`.
