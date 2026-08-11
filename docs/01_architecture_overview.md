# 01 — Architecture Overview

Technical reference for the Coolattin Estate Records Explorer's process
architecture: how the Flask app boots, how configuration is resolved, how the
database connection is created, and how a request travels from the WSGI
server to a JSON/HTML response. This document covers `app.py`,
`create_app.py`, `config.py`, and `extensions.py` line-by-line. Every other
document in `docs/` assumes the reader has read this one first.

## 1. Process entry point — `app.py`

```python
from create_app import create_app
app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    debug = os.environ.get("FLASK_ENV", "").strip().lower() == "development"
    app.run(host="0.0.0.0", port=port, debug=debug)
```

Two invocation paths exist, and both end up calling `create_app()` exactly
once per process:

- **Direct**: `python3 app.py` — binds to `0.0.0.0:$PORT` (default `5001`),
  Werkzeug dev server.
- **Flask CLI / WSGI**: `flask --app app run`, or a production WSGI server
  (gunicorn, Azure App Service's built-in runner) importing the module-level
  `app` object directly. In this path `if __name__ == "__main__"` never
  executes, so `app.run()` is never called — the WSGI server owns the socket
  instead.

`debug` is `False` unless `FLASK_ENV=development` is set explicitly. This is
a deliberate fail-closed default: an unset or misconfigured `FLASK_ENV` in
production never accidentally exposes the Werkzeug interactive debugger
(which allows arbitrary code execution via the browser).

`Procfile` (`web: gunicorn app:app`) and `startup.sh` / `startup.txt` are the
Azure App Service / gunicorn entry points that import this same `app` object.

## 2. Application factory — `create_app.py`

`create_app(config_class=None)` is the **only** place in the codebase where
blueprints are registered, extensions are attached, and app-wide middleware
is installed. No other module is allowed to call
`app.register_blueprint(...)` — this is enforced by convention, not code, and
is called out explicitly in `CLAUDE.md`.

### 2.1 Config resolution

```python
if config_class is None:
    config_class = ActiveConfig
```

`ActiveConfig` (from `config.py`) is resolved once at import time based on
`FLASK_ENV` (see §4). Tests pass an explicit `config_class` to override this
— see `tests/test_config_env_loading.py`.

### 2.2 Template/static folder resolution

```python
_root = Path(__file__).resolve().parent
app = Flask(__name__,
    template_folder=str(_root / "frontend" / "templates"),
    static_folder=str(_root / "frontend" / "static"))
```

Paths are resolved from `create_app.py`'s own location, not from the current
working directory. This means the app boots identically whether launched as
`python3 app.py` from the project root or as `python3 /abs/path/app.py` from
anywhere else — a requirement for gunicorn/Azure, which may `cd` into a
different working directory before exec'ing the process.

### 2.3 Flask config keys set on `app.config`

| Key | Source | Purpose |
|---|---|---|
| `SECRET_KEY` | `config_class.SECRET_KEY` | Flask session signing (sessions are not heavily used in this app, but Flask requires it to be set) |
| `DATABASE_PATH` | `config_class.DATABASE_PATH` | Absolute path to `coolattin.db`, exposed on `app.config` for introspection/testing |
| `VRTI_SPARQL_ENDPOINT` | `config_class.VRTI_SPARQL_ENDPOINT` | Passed through so routes/services can read it without re-importing `config` |
| `CENSUS_STALE_AFTER_DAYS` | `config_class.CENSUS_STALE_AFTER_DAYS` | TTL used by `census_service.py`'s DB-first/KG-second decision |
| `EXPORTS_DIR` | `config_class.EXPORTS_DIR` | Where Excel/PDF exports are written |
| `SEND_FILE_MAX_AGE_DEFAULT` | hardcoded `86400` | Browser cache lifetime (24h) for static files — deliberately long because `townlands.json` is 6.2 MB and the unified records CSV is 4.4 MB; re-fetching them on every page load would be wasteful |

A secondary check logs an error (not a hard failure) if the app is running
with `DEBUG=False` **and** still has the placeholder secret key
`"dev-secret-change-in-prod"` — a soft guardrail against deploying with
default credentials.

### 2.4 Database initialisation

```python
from extensions import init_db, ensure_schema
init_db(config_class.DATABASE_PATH)
ensure_schema()
```

Called synchronously, on every process start, before the app is returned.
`init_db` just records the path (see §5 below); `ensure_schema` runs the full
idempotent DDL script (documented in `02_database_schema.md`). This means
schema creation/migration is not a separate deploy step — it happens on
every cold start, including in test fixtures that call `create_app()`.

### 2.5 Rate limiting (`flask-limiter`)

```python
try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
    _LIMITER_AVAILABLE = True
except ImportError:
    _LIMITER_AVAILABLE = False
```

The import is wrapped in a `try/except` so a missing `flask-limiter`
dependency degrades to **no rate limiting** rather than crashing the app at
import time — logged as a warning. When available, a single `Limiter`
instance is created with:

- `key_func=get_remote_address` — limits are per-client-IP, not global or
  per-session.
- `default_limits=[]` — no blanket limit on every route; limits are applied
  surgically, only to the two LLM-backed Ask endpoints (§2.6).
- `storage_uri="memory://"` — in-process counters. This means rate-limit
  state does **not** survive a process restart and does **not** share state
  across multiple gunicorn worker processes — each worker enforces its own
  independent 30/min and 200/hour budget. This is a known limitation for
  multi-worker production deployments; a shared store (Redis) would be
  required to enforce a true global per-IP limit across workers.

The `Limiter` instance is stashed on `app.extensions["limiter"]` (or `None`
if unavailable) so it can be retrieved later without a fresh import.

### 2.6 Blueprint registration

```python
app.register_blueprint(main_bp)
app.register_blueprint(census_bp,     url_prefix="/api/census")
app.register_blueprint(unified_bp,    url_prefix="/api/unified")
app.register_blueprint(map_bp,        url_prefix="/api/map")
app.register_blueprint(townlands_bp,  url_prefix="/api/townlands")
app.register_blueprint(exports_bp,    url_prefix="/api/exports")
app.register_blueprint(ask_bp,        url_prefix="/api/ask")
app.register_blueprint(kg_explore_bp, url_prefix="/api/kg")
```

`main_bp` has no prefix — it owns the page routes (`/`, `/ask`, `/census`,
…). Every other blueprint owns one JSON API namespace. Full route inventory
is in `13_api_routes.md`.

Immediately after registration, `_apply_ask_rate_limits(app)` retroactively
attaches per-IP limits to two already-registered view functions by name:

```python
limiter.limit("30 per minute; 200 per hour")(app.view_functions["ask_api.ask_query"])
limiter.limit("60 per minute")(app.view_functions["ask_api.ask_feedback"])
```

This has to happen *after* `register_blueprint` because
`app.view_functions` is only populated once blueprints are mounted — the
limiter decorator is applied to the already-registered function object by
looking it up in that dict rather than being used as a `@limiter.limit(...)`
decorator directly on the view (which would require importing `limiter`
inside `ask.py`, creating a circular import between `create_app.py` and
the blueprint module). `ask_query` (the SSE streaming endpoint that calls
paid LLM APIs) gets the strict limit; `ask_feedback` (thumbs up/down,
cheap DB write) gets a looser one.

### 2.7 Security headers — `@app.after_request`

Every response, regardless of route, passes through `_add_security_headers`:

| Header | Value | Purpose |
|---|---|---|
| `X-Content-Type-Options` | `nosniff` | Blocks MIME-sniffing attacks |
| `X-Frame-Options` | `SAMEORIGIN` | Blocks clickjacking via iframe embedding on other origins |
| `Referrer-Policy` | `strict-origin-when-cross-origin` | Limits referrer leakage to third parties |
| `Permissions-Policy` | `camera=(), microphone=(), geolocation=()` | Disables browser features the app never uses |
| `Content-Security-Policy` | see below | Restricts script/style/font/image/frame sources |

All headers use `.setdefault(...)`, not `[...] = ...` — if a route handler
already set one of these headers explicitly, the after-request hook will
not clobber it.

CSP is only attached when `"text/html" in response.content_type` — explicitly
**not** applied to the `/api/ask/query` SSE stream or any JSON API response,
because a restrictive CSP on a non-HTML response has no browser effect but
the check itself guards against any future accidental interference with
streaming behaviour. The CSP allow-lists exactly four third-party origins:
`unpkg.com` and `cdn.jsdelivr.net` (Leaflet.js, Chart.js, D3.js loaded from
CDN — see `14_frontend_pages_and_ui.md`), `fonts.googleapis.com` /
`fonts.gstatic.com` (webfonts), and `youtube.com` / `youtube-nocookie.com`
(embedded video on the About/Info pages). `script-src` and `style-src` both
include `'unsafe-inline'`, which is a real CSP weakening — it allows inline
`<script>`/`<style>` blocks and `onclick=` handlers to execute, which is a
common but non-ideal trade-off for a vanilla-JS frontend that doesn't use a
bundler/nonce pipeline.

### 2.8 Legacy compatibility routes

`_register_legacy_routes(app)` adds two bare, un-prefixed routes directly on
`app` (not through a blueprint):

- `GET /api/centroids` → `map_service.build_centroids()`
- `GET /api/workhouse/match/<record_id>` → `workhouse_service.get_matches_for_record(record_id)`

These exist because earlier versions of the frontend called these exact
paths before the blueprint-per-namespace structure was introduced; they are
kept so no old bookmarked/cached frontend JS breaks. New code should not add
routes here — use a blueprint.

### 2.9 Cache pre-warming

```python
def _prewarm():
    with app.app_context():
        from backend.services.unified_service import _get_all_records
        _get_all_records()
threading.Thread(target=_prewarm, daemon=True).start()
```

`create_app()` returns immediately without waiting for this thread. The
unified records dataset (13,707 rows, loaded from
`unified_processed.csv` via pandas) takes on the order of several seconds to
parse; without pre-warming, the *first* real HTTP request to touch
`unified_service` would pay that cost synchronously. The background thread
starts the parse immediately at boot so it is very likely already cached by
the time the first user request arrives. Because it's a daemon thread, it
never blocks process shutdown. Because `_get_all_records()` populates a
module-level cache (`_UNIFIED_CACHE` in `unified_service.py`), this is a
pure optimisation — correctness does not depend on the thread finishing
before the first request; a request arriving mid-parse will simply run the
parse itself (guarded, not literally re-entrant-safe against a race, but
low-risk given Python's GIL and the read-mostly access pattern).

## 3. Request lifecycle summary

```
WSGI server (gunicorn/Werkzeug)
  → Flask routing → blueprint view function (backend/routes/*.py)
      → thin route handler: parse request, call one service function
          → backend/services/*.py: business logic
              → backend/repositories/*.py: SQL (or backend/integrations/*.py: external HTTP/SPARQL)
          → returns dict/dataclass
      → route handler: jsonify() or render_template()
  → @app.after_request: security headers
  → response
```

The one significant deviation from this synchronous request→response shape
is `/api/ask/query`, which returns a `text/event-stream` response that is
generated by a Python generator function yielding SSE frames as the
multi-phase pipeline executes — see `05_ask_pipeline_default.md` and
`07_ask_pipeline_safety_execution_streaming.md`. Because the response is
streamed, Flask does not buffer the whole body before sending, and the CSP
header logic in §2.7 explicitly skips non-HTML responses so it never
interferes with that stream.

## 4. Configuration — `config.py`

### 4.1 Env file loading

Before any `Config` class attribute is evaluated, `_load_local_env_files()`
runs at module import time:

```python
for env_path in (root / ".env.local", root / ".env"):
    ...
    for raw_line in env_path.read_text(...).splitlines():
        key, value = line.split("=", 1)
        if key and key not in os.environ:
            os.environ[key] = value
```

This is a **minimal hand-rolled dotenv loader** — no `python-dotenv`
dependency. Key behaviours:

- `.env.local` is read before `.env`; since both loops only set a key `if
  key not in os.environ`, `.env.local` values win over `.env` values, and
  **any value already present in the real process environment wins over
  both files**. This ordering is deliberate: it lets Azure App Service
  environment settings (which are injected as real process env vars) always
  override whatever is checked into `.env`/`.env.local` for local dev.
- Comment lines (`#`) and blank lines are skipped; quotes around values are
  stripped.
- Any `OSError` while reading a file is silently swallowed — a missing or
  unreadable env file is not a startup failure.

### 4.2 `Config` class — every tunable in one place

| Attribute | Env var | Default | Notes |
|---|---|---|---|
| `SECRET_KEY` | `SECRET_KEY` | `"dev-secret-change-in-prod"` | Flask session signing key |
| `ADMIN_API_KEY` | `ADMIN_API_KEY` | `""` | Gate for any admin-only endpoint |
| `DATABASE_PATH` | `DATABASE_PATH` | `BASE_DIR / "coolattin.db"` | Resolved via `_resolve_database_path()`, expands `~` |
| `DATABASE_URL` | `DATABASE_URL` | `sqlite:///{DATABASE_PATH}` | SQLAlchemy-style URL string kept for compatibility/tooling even though no ORM is used |
| `VRTI_SPARQL_ENDPOINT` | — (hardcoded) | `https://virtuoso.virtualtreasury.ie/sparql/` | Not overridable via env — the one external KG endpoint is fixed |
| `VRTI_REQUEST_TIMEOUT` | `VRTI_REQUEST_TIMEOUT` | `30` (seconds) | |
| `GRAPHDB_SPARQL_ENDPOINT` | `GRAPHDB_SPARQL_ENDPOINT` | `http://localhost:7200/repositories/coolattin` | Local GraphDB instance, comparative prototype |
| `GRAPHDB_ENABLED` | `GRAPHDB_ENABLED` | `true` | |
| `GRAPHDB_REQUEST_TIMEOUT` | `GRAPHDB_REQUEST_TIMEOUT` | `15` | |
| `GRAPHRAG_ENABLED` | `GRAPHRAG_ENABLED` | `true` | Toggles the in-process NetworkX graph subsystem |
| `GRAPHRAG_VECTOR_TOP_K` | `GRAPHRAG_VECTOR_TOP_K` | `8` | Dense-retrieval seed count before BFS expansion |
| `GRAPHRAG_K_HOPS` | `GRAPHRAG_K_HOPS` | `2` | BFS traversal depth |
| `GRAPHRAG_MAX_NODES` | `GRAPHRAG_MAX_NODES` | `120` | Cap on subgraph size returned to the LLM |
| `CENSUS_STALE_AFTER_DAYS` | — (hardcoded) | `7` (dev) / `1` (prod) | DB-first/KG-second TTL |
| `TOWNLAND_STALE_AFTER_DAYS` | — (hardcoded) | `30` (dev) / `7` (prod) | |
| `STATIC_DATA_DIR` | — | `frontend/static/data` | GeoJSON/CSV/Excel seed data served directly by Flask's static handler |
| `DATA_SEED_DIR` | — | `data/seed` | Non-CSV reference data (community summaries, aliases, TTL) |
| `DATA_SNAPSHOT_DIR` | — | `data/source_snapshots` | Gitignored local copies of external API responses |
| `EXPORTS_DIR` | — | `exports/` | Runtime PDF/Excel output, gitignored |
| `EMBEDDING_PROVIDER` | `EMBEDDING_PROVIDER` | `local` | One of `local` \| `cohere` \| `voyage` — see `09_retrieval_and_embeddings.md` |
| `LOG_LEVEL` | `LOG_LEVEL` | `INFO` | |

`DevelopmentConfig` and `ProductionConfig` both subclass `Config` and
override only `DEBUG`, `LOG_LEVEL` (dev) or `DEBUG`,
`CENSUS_STALE_AFTER_DAYS`, `TOWNLAND_STALE_AFTER_DAYS` (prod) — production
refreshes KG-backed data far more aggressively (1 day / 7 days) than
development (7 days / 30 days), trading more frequent VRTI calls for
fresher public-facing data.

### 4.3 Active config selection

```python
_flask_env = os.environ.get("FLASK_ENV", "").strip().lower()
ActiveConfig = config_by_name.get(_flask_env, ProductionConfig)
```

Only the literal string `"development"` selects `DevelopmentConfig`.
**Any other value, or an unset `FLASK_ENV`, resolves to `ProductionConfig`.**
This is the same fail-closed philosophy as the `debug` flag in `app.py` —
an operator who forgets to set `FLASK_ENV` on a new deployment gets the
safer, more conservative production behaviour rather than accidentally
running with `DEBUG=True` and permissive staleness windows.

## 5. Database singleton — `extensions.py`

`extensions.py` is the **only** place `sqlite3.connect()` is allowed to be
called (per `CLAUDE.md`). It exposes two functions:

```python
def init_db(db_path: Path) -> None:
    global _DB_PATH
    _DB_PATH = db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)

def get_db_conn() -> sqlite3.Connection:
    if _DB_PATH is None:
        raise RuntimeError("Database not initialised — call init_db() first")
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-65536")
    conn.execute("PRAGMA temp_store=2")
    conn.execute("PRAGMA mmap_size=268435456")
    return conn
```

`_DB_PATH` is process-global module state, set once by `create_app()` at
boot. `get_db_conn()` opens a **brand-new connection on every call** — there
is no connection pool. Every repository/service function that needs the
database calls `get_db_conn()`, does its work inside a `try/finally: conn.close()`,
and never holds a connection across a request boundary. This is a deliberate
simplicity trade-off appropriate for SQLite (which does not benefit from
connection pooling the way a client-server DB does) but means every DB touch
pays SQLite's connection-open cost, mitigated by the PRAGMAs below:

| PRAGMA | Value | Effect |
|---|---|---|
| `journal_mode` | `WAL` | Write-Ahead Logging — readers do not block writers and vice versa; this is what allows the Ask pipeline's SQL execution to run concurrently with e.g. a census refresh write without lock contention. Produces the `coolattin.db-wal` and `coolattin.db-shm` sidecar files visible in the repo root. |
| `foreign_keys` | `ON` | SQLite disables FK enforcement by default per-connection; this turns it on so `REFERENCES` constraints in the schema (e.g. `census_record.townland_id → townland.id`) are actually enforced |
| `synchronous` | `NORMAL` | Fsyncs less aggressively than `FULL` — safe under WAL mode (checkpoints still sync), trades a small durability window for write throughput |
| `cache_size` | `-65536` | Negative value = size in **KB**, so 64 MB page cache per connection |
| `temp_store` | `2` | Temporary tables/indexes (used by `ORDER BY`, `GROUP BY` on large result sets) live in memory, not on disk |
| `mmap_size` | `268435456` | 256 MB memory-mapped I/O window — lets SQLite read pages via `mmap` instead of `read()` syscalls for better cache locality |

### 5.1 Schema bootstrap — `ensure_schema()`

Called once per process, immediately after `init_db()`, from
`create_app()`. It is **idempotent and safe to call on every startup** —
every `CREATE TABLE` uses `IF NOT EXISTS`, and the one table that predates
the `entity_id` UUID column (`townland`) is migrated in place rather than
recreated. Full schema contents (all 15+ tables) are documented in
`02_database_schema.md`; this section covers only the *mechanism*.

The function is split into four phases, all inside one `try/finally: conn.close()`:

1. **Stable tables** (`census_record`, `clearances_record`, `refresh_state`)
   — created via one `executescript()` call, no migration logic because
   their shape has never changed since introduction.
2. **`townland` table** — conditionally created fresh (`_TOWNLAND_CREATE`)
   or migrated (`_apply_v2_migration`) depending on whether the table
   already exists and whether it already has an `entity_id` column. See
   §5.2.
3. **Resolution-engine support tables** (`townland_xref`, `match_review`,
   `field_provenance`, `source_mentions`, `entity_resolution_candidates`,
   `workhouse_unified_links`, `entity_resolution_decisions`, `graph_nodes`,
   `graph_edges`) — one large `executescript()` (`_SUPPORT_TABLES`), all
   `CREATE TABLE IF NOT EXISTS`.
4. **Indexes** — a list of `CREATE INDEX IF NOT EXISTS` statements executed
   individually in a loop.

Four more tables — `unified_record`, `heritage_feature`, `ask_query_memory`,
`ask_query_feedback` — are **not** created here. They are created lazily on
first use by `backend/services/ask_service.py` and `unified_service.py`
respectively, via their own `_ensure_*_schema()` helper functions that run
the same `CREATE TABLE IF NOT EXISTS` pattern. This split exists because
those four tables are logically owned by the Ask pipeline/unified-records
subsystem rather than the core townland/census schema that `extensions.py`
governs — see `02_database_schema.md` §"Lazily-created tables" and
`05_ask_pipeline_default.md`.

### 5.2 The `townland` v1→v2 migration

Early versions of the schema had a `UNIQUE` constraint on `townland.name`
and no `entity_id` UUID column. `_apply_v2_migration(conn)`:

1. `ALTER TABLE townland ADD COLUMN entity_id/qualifier/logainm_id/geometry_flag`
   for any of the four v2 columns not already present.
2. Backfills `entity_id = uuid4()` for every existing row where it is
   `NULL`, one `UPDATE` per row (not a bulk statement — acceptable given the
   townland table is only ~152–200 rows).
3. Cannot drop the old `UNIQUE(name)` constraint — SQLite does not support
   `ALTER TABLE ... DROP CONSTRAINT`. The docstring notes this is tolerated
   because townland names are unique in practice within the Coolattin
   dataset, and documents the manual rename-recreate-reinsert-drop procedure
   an operator would need to run if two same-named townlands from different
   baronies ever needed to coexist.

This is the only migration path in the codebase — there is no generic
migration framework (no Alembic). New schema changes are expected to be
added as new `ALTER TABLE ... ADD COLUMN` / `CREATE TABLE IF NOT EXISTS`
statements guarded by an `IF col NOT IN existing` check, following the same
pattern.

## 6. Directory layout cross-reference

See `CLAUDE.md` for the canonical directory tree. In terms of *layering*,
the codebase enforces (by convention, not by import-linting) a strict
dependency direction:

```
backend/routes/*        (thin — parse request, call one service, jsonify/render)
      ↓ imports
backend/services/*       (business logic, orchestration, caching)
      ↓ imports
backend/repositories/*   (SQL only — no business logic)
backend/integrations/*   (external HTTP/SPARQL clients — no business logic)
      ↓ imports
extensions.py             (get_db_conn — the only sqlite3.connect call site)
```

`backend/models/` holds plain dataclasses/typed-dicts (e.g.
`census_models.py::Townland`, `CensusFilters`) shared across the layers with
no behaviour of their own. `analytics/` is a parallel, self-contained
plugin system (see `12_analytics_modules.md`) that reads from the same
database but is not part of the routes→services→repositories chain — it is
invoked only by `backend/routes/main.py`'s analytics-dashboard route and
discovers its modules via `analytics/registry.py`.
