# 13 — API Routes

Complete HTTP endpoint reference for every Flask route in the application:
8 blueprints plus two un-prefixed legacy routes registered directly on `app`.
Blueprint registration, `url_prefix` assignment, and rate-limit wiring are
covered in `01_architecture_overview.md` §2.6 — this document does not repeat
that mechanism, only the resulting per-route contract.

Internal pipeline logic that lives *behind* a route (the Ask pipeline's SQL
generation/synthesis, the semantic layer, census/townland/unified service
internals, analytics module computation, GraphRAG traversal) is documented
in the sibling docs referenced inline (`03_*`, `05_*`, `06_*`, `07_*`,
`09_*`, `10_*`, `12_*`). Each entry below documents only what a client sends
and receives — method, path, params, request/response JSON shape, and status
codes — grounded in what the route handler itself does before/after
delegating to a service.

## Blueprint → prefix map

| Blueprint (file) | `url_prefix` | Registered name |
|---|---|---|
| `main.py` | *(none)* | `main` |
| `census.py` | `/api/census` | `census_api` |
| `unified.py` | `/api/unified` | `unified_api` |
| `map_config.py` | `/api/map` | `map_api` |
| `townlands.py` | `/api/townlands` | `townlands_api` |
| `exports.py` | `/api/exports` | `exports_api` |
| `ask.py` | `/api/ask` | `ask_api` |
| `kg_explore.py` | `/api/kg` | `kg_explore` |
| *(legacy, no blueprint)* | — | registered directly on `app` in `create_app.py::_register_legacy_routes` |

**Rate limiting**: confirmed accurate against current code — only two view
functions carry a `flask-limiter` decorator, both applied retroactively by
name in `create_app.py` (see `01_architecture_overview.md` §2.6):

| View function | Limit |
|---|---|
| `ask_api.ask_query` (`POST /api/ask/query`) | 30/minute; 200/hour, per client IP |
| `ask_api.ask_feedback` (`POST /api/ask/feedback`) | 60/minute, per client IP |

No other route in any blueprint carries a rate limit. Limits are in-memory
(`storage_uri="memory://"`) and per-process — not shared across gunicorn
workers.

**Admin gating**: only two routes require `ADMIN_API_KEY`, both in
`census.py`, via the local `_require_admin` decorator:

- `POST /api/census/refresh`
- `POST /api/census/export/regenerate`

`_require_admin` checks `X-Admin-Key` header first, then `?admin_key=`
query param. If `ActiveConfig.ADMIN_API_KEY` is unset/empty, the endpoint
returns `403` unconditionally ("Admin operations are disabled") regardless
of what the caller supplies — there is no way to reach these routes without
an operator explicitly setting the env var. If a key *is* configured but the
caller's value doesn't match, `403 {"error": "Forbidden"}`.

No other blueprint uses `ADMIN_API_KEY` or any auth gate — every other route
listed below is open to any caller (subject only to the two Ask rate limits
above).

---

## 1. `main.py` — page routes (no prefix)

All routes are `GET`, return `render_template(...)`, and require no query
params (one exception: `/analytics`). None touch the database directly —
the analytics route delegates to `analytics/registry.py`.

| Method | Path | Template | Notes |
|---|---|---|---|
| GET | `/` | `index.html` | Home / landing page |
| GET | `/about` | `about.html` | |
| GET | `/analytics` | `analytics.html` | See below |
| GET | `/census` | `census.html` | Census Explorer page shell |
| GET | `/info` | `info.html` | Estate & Famine Clearances info page |
| GET | `/ask` | `ask.html` | Ask the Archive page shell |
| GET | `/heritage` | `heritage.html` | |
| GET | `/kg-explore` | `kg_explore.html` | Same view function registered under two paths |
| GET | `/explore-knowledge` | `kg_explore.html` | Alias of `/kg-explore` (`@bp.get` applied twice to `explore_knowledge`) |

### `GET /analytics`

Query params:

| Param | Type | Default | Meaning |
|---|---|---|---|
| `d` | str | `""` | `dataset_id` to select; falls back to the first discovered module if omitted or unknown |

Behaviour in the route handler itself (not delegated — this logic lives in
`main.py`):
1. Calls `analytics.registry.discover_modules()`. On exception, logs a
   warning and continues with `modules = {}` (page still renders, just with
   no datasets).
2. Resolves `current` = `modules.get(d)` or the first module in
   `module_list` if `d` was empty/unmatched.
3. If a `current` module was resolved, calls `current.compute()`. On
   exception, logs `analytics compute failed for <dataset_id>` and sets
   `error = str(e)` — the page still renders (with `error` shown), no `500`.
4. Template vars passed: `datasets` (list of `(dataset_id, dataset_name)`
   tuples for every discovered module), `current_dataset_id`, `result`
   (the `AnalyticsModule.compute()` output or `None`), `error` (`None` or a
   string).

Always returns `200` — this route never returns a non-2xx status; failures
degrade to an `error` string rendered in the template. Full analytics module
contract (`AnalyticsModule`, `KPI`, `Chart` protocols) is in
`12_analytics_modules.md`.

---

## 2. `census.py` — `/api/census/*`

```
GET  /api/census/                     — paginated census records
GET  /api/census/records              — alias for the above (backward-compat)
GET  /api/census/townlands            — townland names with census data
GET  /api/census/summary              — aggregate stats by year
GET  /api/census/townland             — single townland, all years
POST /api/census/refresh              — force KG re-ingestion   [admin]
GET  /api/census/export/latest        — most recent export info
POST /api/census/export/regenerate    — regenerate Excel from DB [admin]
```

All non-admin responses follow the envelope documented in the module
docstring:

```json
{ "data": [...], "meta": { "source": "...", "cache_status": "...", "generated_at": "...", "record_count": 0, "export_file": null } }
```

`meta` is always the serialised form of `CensusMeta` (`backend/models/census_models.py`):
`source` ∈ `{database, kg_refresh, csv_seed}`, `cache_status` ∈ `{hit, miss, stale_refresh}`.

### `GET /api/census/` (and alias `GET /api/census/records`)

Query params (parsed into a `CensusFilters` dataclass in the route handler):

| Param | Type | Default | Notes |
|---|---|---|---|
| `year` | int | — | One of 1841/1851/1861/1871/1881/1891 (not validated in the route — passed through) |
| `townland` | str | — | Partial, case-insensitive match |
| `barony` | str | — | |
| `page` | int | `1` | Clamped to `max(1, page)` in the route |
| `limit` | int | `100` | Clamped to `min(limit, 2000)` in the route (docstring says max 500, code enforces 2000) |

Delegates to `census_service.get_census_data(filters)` (DB-first/KG-second —
see `03_data_ingestion_and_refresh.md`). Response: `200` with the envelope
above; `data` is a list of `CensusRecord.to_dict()` shapes (`townland`,
`year`, `male`, `female`, `total`, `inhabited`, `uninhabited`, `source`,
`last_synced_at`).

`GET /api/census/records` is a **literal alias** — the view function body is
`return get_census()`, i.e. it calls the other view function directly rather
than duplicating logic. Kept for old frontend JS that predates the
blueprint-per-namespace refactor (per the module docstring: "old app.py used
`/api/census/records`").

### `GET /api/census/townlands`

No query params. Delegates to `census_service.get_available_townlands()`.
Returns `200` with the standard envelope; `data` is a list of townland
names that have at least one census record.

### `GET /api/census/summary`

Query params: `year` (int, optional — restricts the aggregate to one year;
omitted means all years).

Delegates to `census_service.get_census_summary(year=year)`. The route
handler **flattens** the summary: `result.data[0]` (a dict) is spread
directly into the top-level JSON response alongside `meta`, rather than
being nested under a `data` key:

```json
{ "<summary fields...>": "...", "meta": { ... } }
```

This is called out in the route's own comment as "for backward
compatibility." If `result.data` is empty, the summary fields are simply
omitted (`summary = {}`).

### `GET /api/census/townland`

Query params: `name` (str, **required**).

Validation in the route handler itself:
- Missing/blank `name` → `400 {"error": "name parameter is required"}`.
- `census_service.get_townland_detail(...)` returns no rows → `404 {"error": "Townland '<name>' not found"}`.
- Success → `200 {"data": <single CensusRecord-shaped dict spanning all years>, "meta": {...}}`.

Note: `result.data[0]` is returned (not the whole list) — i.e. the service
presumably packs the full multi-year detail into one dict entry.

### `POST /api/census/refresh` — admin only

Body (JSON, optional): `{"year": <int>}`. If omitted, also checks
`?year=` query string as a fallback (`body.get("year") or request.args.get("year", type=int)`).

Delegates to `refresh_service.trigger_census_refresh(year=year)`, which
**ignores TTL and always queries the KG** (per docstring). Returns
`202 <result dict from trigger_census_refresh>` (accepted — not `200`,
signalling the refresh happens synchronously within the request but the
semantic is "work accepted"). Requires `ADMIN_API_KEY` (see §"Admin gating"
above) — unauthenticated/misconfigured callers get `403` before the service
is ever invoked.

### `GET /api/census/export/latest`

No params. Delegates to `export_service.get_latest_census_export()` — pure
read of `refresh_state`, no KG call, no export regeneration. Returns `200`
with (see §7 export_service detail below):

```json
{ "export_file": "...", "exists": true, "generated_at": "...", "record_count": 0, "source": "..." }
```

or, if no export has ever been produced:

```json
{ "export_file": null, "message": "No export available yet. Run a refresh first." }
```

### `POST /api/census/export/regenerate` — admin only

Query param: `year` (int, optional — restrict regeneration to one year).
Delegates to `export_service.regenerate_from_db(year=year)`, which reads
`census_repository.find(filters)` (DB only, **no KG call**) and writes a
fresh `.xlsx`. Returns `201 {"export_file": "<path>", "status": "regenerated"}`.

---

## 3. `unified.py` — `/api/unified/*`

```
GET /api/unified/records             — search the 13,707-row estate database
GET /api/unified/stats               — record counts / field coverage
GET /api/unified/townlands           — list of townlands in unified_record
GET /api/unified/surnames            — list of surnames
GET /api/unified/surname-suggest     — autocomplete surnames (scoped to townland)
GET /api/unified/surnames-all        — autocomplete surnames (whole dataset)
GET /api/unified/workhouse-by-townland — workhouse mentions linked/unlinked for a townland
```

(`GET /api/centroids` and `GET /api/workhouse/match/<record_id>`, listed in
this file's own module docstring, are **not** defined here — they are the
two legacy routes registered directly on `app` in `create_app.py`; see §9.)

None of these routes are wrapped in `jsonify({"data": ..., "meta": ...})` —
unlike `census.py`, most return bare JSON (a list or a plain dict), matching
`unified_service`'s return shapes directly.

### `GET /api/unified/records`

Query params (all optional, `str.strip()`'d in the route):

| Param | Type | Default |
|---|---|---|
| `surname` | str | `""` |
| `forename` | str | `""` |
| `townland` | str | `""` |
| `year` | str | `""` |
| `estate` | str | `""` |
| `limit` | int | `0` (no limit) |

Delegates to `unified_service.search_records(...)`, returning a list of
record dicts. **The route then augments every record in place** before
returning — this is logic that lives in the route handler itself, not the
service:

1. If `workhouse_entity_resolution.has_persisted_links()` is `True`, builds
   a `resolution_map` via `get_resolution_map([record_id, ...])` for every
   `record_id` in the result set (a single batched lookup, not per-record).
2. For each record `r`, sets 8 additional keys from the resolution map
   (falling back to empty defaults if the record has no resolution entry):

   | Key added | Type | Source |
   |---|---|---|
   | `linked_workhouse_records` | list | `resolution["linked_workhouse_records"]` |
   | `possible_workhouse_matches` | list | `resolution["possible_workhouse_matches"]` |
   | `please_check_records` | list | `resolution["please_check_records"]` or falls back to `possible` |
   | `identity_is_ambiguous` | bool | `resolution["identity_is_ambiguous"]` |
   | `identity_disambiguation_note` | str\|null | `resolution["identity_disambiguation_note"]` |
   | `supporting_evidence` | list | `resolution["supporting_evidence"]` |
   | `conflicting_evidence` | list | `resolution["conflicting_evidence"]` |
   | `has_workhouse_record` | bool | `bool(linked or possible)` |
   | `workhouse_record_count` | int | `len(linked) + len(possible)` |

   A code comment explicitly notes *why* this reads from persisted DB links
   rather than the legacy in-memory fuzzy index: the O(n×m)
   `SequenceMatcher` index times out gunicorn workers at 13k-record scale.
3. If the `workhouse_entity_resolution` import/lookup raises for any reason,
   the whole augmentation step is swallowed (bare `except Exception: pass`)
   and every record simply keeps whatever defaults were set before the
   `try` — i.e. this endpoint never `500`s due to entity-resolution being
   unavailable.

Response: `200 [ {...record + 9 keys...}, ... ]` — a bare JSON array, not an
envelope object.

### `GET /api/unified/stats`

No params. `200 <dict from unified_service.get_stats()>` — record counts
and field coverage stats (internal shape owned by `unified_service`, out of
scope here per the assignment).

### `GET /api/unified/townlands`

No params. `200 <list from unified_service.get_townland_list()>`.

### `GET /api/unified/surnames`

No params. `200 <list from unified_service.get_surname_list()>`.

### `GET /api/unified/surname-suggest`

Query params: `q` (str, `""` default), `townland` (str, `""` default —
scopes suggestions to one townland). `200 <list from unified_service.suggest_surnames(q=q, townland=townland)>`.

### `GET /api/unified/surnames-all`

Query params: `q` (str, `""` default). Identical to `surname-suggest` but
always calls `suggest_surnames(q=q, townland="")` — i.e. unscoped,
dataset-wide autocomplete. Route docstring: "all surnames across the entire
dataset (not scoped to a townland)."

### `GET /api/unified/workhouse-by-townland`

Query param: `townland` (str, **required** — the route does not use the
`@require` pattern, it inline-checks).

- Missing/blank `townland` → `200 {"records": [], "linked": [], "unlinked": [], "error": "townland required"}`
  (note: **still `200`**, not `400` — the error is communicated in-band).

Otherwise this route runs **raw SQL directly inside the route handler**
(the one place in this file that deviates from the "routes are thin, SQL
stays in repositories" convention stated in `CLAUDE.md`) via
`extensions.get_db_conn()`:

1. Normalises `townland` to uppercase with `'` escaped for SQL
   (`townland.upper().replace("'", "''")`) — used in a `LIKE`/`=` clause,
   not a parameter, for the uppercase transform (the actual bind param
   passed to `.execute()` is `tn`, so this is parameterised despite the
   manual escaping).
2. `linked_rows` — joins `source_mentions` → `entity_resolution_candidates`
   → `unified_record` where `UPPER(ur.townland_norm) = ?` and
   `erc.label IN ('CONFIRMED_MATCH', 'POSSIBLE_MATCH')`, ordered by
   `erc.score DESC`, capped at 30 rows. Columns returned: `mention_id,
   raw_name, wh_forename, wh_surname, event_year, raw_place,
   normalised_place, age, source_table, match_score, match_label,
   record_id, estate_forename, estate_surname, townland, estate_year,
   role, has_emigration_record, has_eviction_record, has_tenancy_record`.
3. `unlinked_rows` — `source_mentions` where
   `UPPER(COALESCE(normalised_place, raw_place, '')) LIKE '%<tn>%'`,
   ordered by `event_year`, capped at 20 rows. Columns: `mention_id,
   raw_name, forename, surname, event_year, raw_place, normalised_place,
   age, source_table`.
4. Response: `200 {"townland": "<original input>", "linked": [...], "unlinked": [...]}`.
5. Any exception during the query → **still `200`**,
   `{"townland": townland, "linked": [], "unlinked": [], "error": str(exc)}`
   — this route never returns a 4xx/5xx status.
6. `conn.close()` in a `finally` block regardless of outcome.

---

## 4. `map_config.py` — `/api/map/*`

```
GET /api/map/layers      — basemap tile layer definitions
GET /api/map/centroids   — townland centroid lat/lon (see also legacy /api/centroids, §9)
```

Both routes take no params and delegate entirely to `map_service.py`.

### `GET /api/map/layers`

Delegates to `map_service.get_layer_config()`. `200` response shape:

```json
{
  "layers": [ { "id": "...", "label": "...", "tile_url": "...", "attribution": "...", "max_zoom": 19, "description": "..." }, ... ],
  "overlays": [ { ...same shape, "is_overlay": true... } ],
  "default": "standard"
}
```

`get_layer_config()` filters `MAP_LAYERS` (see below) into `layers` (every
entry *without* `is_overlay: true`) and `overlays` (every entry *with* it)
— overlays are meant to be composited on top of a base layer by the
frontend, not selected standalone.

**`MAP_LAYERS` registry** (module-level dict in `map_service.py` — the
single source of truth; per the module docstring, adding an entry here is
the *only* step needed to add a new basemap, no frontend changes required):

| id | label | tile_url | attribution (abbreviated) | max_zoom | overlay? |
|---|---|---|---|---|---|
| `standard` | Standard | `https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png` | © OpenStreetMap contributors | 19 | no *(default)* |
| `satellite` | Satellite | `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}` | © Esri, i-cubed, USDA, USGS, AEX, GeoEye, Getmapping, Aerogrid, IGN, IGP, UPR-EGP, GIS User Community | 19 | no |
| `terrain` | Terrain | `https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png` | © OpenStreetMap contributors, SRTM / © OpenTopoMap | 17 | no |
| `labels_overlay` | Satellite + Labels | `https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}` | Labels © Esri | 19 | **yes** |

`DEFAULT_LAYER = "standard"`.

**3D/terrain note** (from the module docstring, verbatim intent): true 3D
perspective rendering (Cesium-style) would require Mapbox GL JS or
CesiumJS and is out of scope for the current Leaflet stack. OpenTopoMap
(the `terrain` entry) is described as "the closest practical terrain
equivalent within Leaflet." The `id: "terrain"` value is explicitly
**reserved** for a future Mapbox GL migration path — i.e. if Mapbox GL is
introduced later, it is expected to take over the `terrain` id rather than
add a new one.

### `GET /api/map/centroids`

Delegates to `map_service.build_centroids()`, which itself delegates to
`townland_service.build_centroids_from_geojson(geojson_path)` — reading
`ActiveConfig.STATIC_DATA_DIR / "townlands.json"` by default. Response:
`200 { "<TOWNLAND NAME>": [lat, lon], ... }` — a flat dict, not a list.
This is functionally identical to the legacy `GET /api/centroids` route
(§9) — both call the exact same `map_service.build_centroids()` function,
just reached via different URL paths (one blueprint-scoped, one legacy
top-level).

---

## 5. `townlands.py` — `/api/townlands/*`

```
GET  /api/townlands                  (and /api/townlands/)  — all available townlands
GET  /api/townlands/wicklow          — Wicklow townlands (compat alias)
POST /api/townlands/refresh          (and /wicklow/refresh) — force KG refresh
```

No admin gating on any route here (contrast with `census.py`'s refresh
route, which is admin-gated).

### `GET /api/townlands` and `GET /api/townlands/`

Both paths map to the same view function (`@bp.get("")` and `@bp.get("/")`
stacked on one function). Query param: `county` (str, optional).

Notable: **the `county` filter is currently a no-op.** Both branches of the
route's `if county: ... else: ...` call the exact same
`townland_service.get_wicklow_townlands()` function — the route has a
`# TODO: extend service for all-county fetch` comment on the `else` branch,
confirming this is known, unfinished behaviour, not a bug introduced by
this doc's analysis. In practice, `GET /api/townlands?county=Anything` and
`GET /api/townlands` return identical data today (Wicklow-only), regardless
of the `county` value supplied.

`200 <result dict from get_wicklow_townlands()>` (DB-first/KG-second — see
`03_data_ingestion_and_refresh.md`).

### `GET /api/townlands/wicklow`

No params. Explicit backward-compat alias — identical body to the above:
`200 <result dict from get_wicklow_townlands()>`.

### `POST /api/townlands/refresh` and `POST /api/townlands/wicklow/refresh`

Both paths map to the same view function (`refresh_townlands`, two
`@bp.post` decorators stacked). No body/params consumed. Delegates to
`refresh_service.trigger_townlands_refresh()`, which the route docstring
notes "fetches all available townlands (not restricted to Wicklow)" —
i.e. unlike the `GET` routes, the refresh is *not* Wicklow-scoped.
`202 <result dict from trigger_townlands_refresh()>`.

---

## 6. `exports.py` — `/api/exports/*`

```
GET /api/exports/census/latest    — latest census export info (duplicate of /api/census/export/latest)
GET /api/exports/census/download  — download the export .xlsx file
```

### `GET /api/exports/census/latest`

Identical body to `census.py`'s `GET /api/census/export/latest` — both call
`export_service.get_latest_census_export()` directly and `jsonify()` the
result verbatim. Two URL paths, one underlying function, no behavioural
difference.

### `GET /api/exports/census/download`

No query params. Route logic:

1. Calls `export_service.get_latest_census_export()`.
2. If `info["export_file"]` is falsy → `abort(404, description="No export available. Run a refresh first.")`.
3. Builds a `pathlib.Path` from the stored path; if it doesn't exist on disk
   → `abort(404, description="Export file not found on disk.")` (distinct
   message from step 2 — lets a client distinguish "never exported" from
   "exported but the file has since been deleted/moved").
4. Otherwise `send_file(path, as_attachment=True)` — streams the `.xlsx`
   with `Content-Disposition: attachment`, filename taken from the path.

Both `404`s use Flask's `abort()` with a `description`, which renders as
Flask's default HTML error page (not JSON) — this route does not return a
JSON error body on failure, unlike most other routes in the app.

### Export file naming and generation (`export_service.py`)

Two file-producing functions exist; both are read in full for this doc.

**Filename patterns** (from the module docstring and confirmed in code):

| Function | Pattern | Example |
|---|---|---|
| `export_census(records, scope, extra_meta=None)` | `census_wicklow{_all\|_<year>}_{YYYYMMDD_HHMMSS}.xlsx` | `census_wicklow_all_20260727_143210.xlsx`, `census_wicklow_1841_20260727_143210.xlsx` |
| `export_townlands(townlands, extra_meta=None)` | `townlands_wicklow_{YYYYMMDD_HHMMSS}.xlsx` | `townlands_wicklow_20260727_143210.xlsx` |

`year_part` in `export_census` is derived from `scope.year` (dataclass
attribute) **or** `scope["year"]` (dict) — the function accepts either a
`CensusFilters` instance or a plain dict as `scope`, checking
`hasattr(scope, "year")` first, then re-checking `isinstance(scope, dict)`
second (so a dict always overrides even if it also happens to have
attribute-style access — in practice these two branches are mutually
exclusive given Python's type system).

Both write into `ActiveConfig.EXPORTS_DIR / "census"` or
`ActiveConfig.EXPORTS_DIR / "townlands"` respectively (directories created
with `mkdir(parents=True, exist_ok=True)` before writing).

**When exports ARE generated** (per module docstring, and confirmed by call
sites): immediately after a successful KG ingestion (triggered from
`census_service`/ingest jobs), and on explicit
`POST /api/census/export/regenerate` (reads DB only, no KG call).

**When exports are NOT generated**: normal page loads (`GET .../latest`
just reads the path recorded in `refresh_state`), and any response served
from the DB cache without a fresh KG round-trip.

**Every export gets a second "Export Metadata" sheet.** Confirmed field
lists as written in code:

`export_census` metadata sheet rows: `Generated At (UTC)`, `Source Endpoint`
(`backend.integrations.vrti_sparql.SPARQL_ENDPOINT`), `Query Scope` (stringified
`vars(scope)` or `str(scope)`), `Record Count`, `Export File`, `Application`
(`"Coolattin Lineage — Digital Estate Archive"`), `Census Years Covered`
(`"1841, 1851, 1861, 1871, 1881, 1891"`), `County` (`"Wicklow, Ireland"`),
plus any caller-supplied `extra_meta` dict entries appended after.

`export_townlands` metadata sheet rows: `Generated At (UTC)`, `Record
Count`, `County`, `Application`, plus any `extra_meta` entries.

**Sheet 1 columns**:
- `export_census` → "Census Records" sheet: `Townland, Year, Male, Female,
  Total, Inhabited Houses, Uninhabited Houses, Source, KG URI`. Header row
  styled bold white text on a `#0F172A` fill, centered. Column widths
  auto-sized to `min(max_content_len + 4, 50)`.
- `export_townlands` → "Wicklow Townlands" sheet: `Name, Gaelic Name,
  Barony, Civil Parish, Electoral Division, Placename Theme, Description,
  KG URI, Source`. Same header styling.

**`get_latest_census_export()`** — pure read, no side effects. Reads
`refresh_state_repository.get("wicklow_census")`. If no state row or no
`export_file` recorded:
`{"export_file": None, "message": "No export available yet. Run a refresh first."}`.
Otherwise:
`{"export_file": "<path>", "exists": <bool, checked live on disk>, "generated_at": "<from refresh_state>", "record_count": <int>, "source": "<str>"}`.

**`regenerate_from_db(year=None)`** — builds `CensusFilters(year=year,
limit=10000)`, calls `census_repository.find(filters)` (DB read only, no
KG call whatsoever), then calls `export_census(...)` with
`extra_meta={"Export Type": "regenerated_from_db"}` — this is how a
regenerated file's metadata sheet is distinguishable from a fresh
KG-ingestion export. Returns the new file's path as a string (the route
wraps this in `{"export_file": path, "status": "regenerated"}`, `201`).

---

## 7. `ask.py` — `/api/ask/*`

```
POST /api/ask/query             — SSE-streamed Q&A pipeline               [rate-limited 30/min, 200/hr]
POST /api/ask/feedback          — thumbs up/down + query memory write     [rate-limited 60/min]
GET  /api/ask/llm-status        — LLM provider health/config check
GET  /api/ask/ollama-status     — alias of llm-status (backward-compat)
GET  /api/ask/townland-suggest  — fuzzy townland autocomplete
GET  /api/ask/townland-catalog  — full Wicklow townland list for client-side filtering
GET  /api/ask/estate-overview   — geographic + estate KPI dump for the All-Townlands panel
GET  /api/ask/pdf/<filename>    — download a generated PDF report
```

Internal pipeline behaviour behind `/query` (7-phase orchestration, SQL
generation, GraphRAG, LLM synthesis cascade) is documented in
`05_ask_pipeline_default.md`, `06_*`, `07_*` — this section covers only the
HTTP contract of the route itself.

### `POST /api/ask/query`

Request body (JSON):

| Key | Type | Required | Notes |
|---|---|---|---|
| `question` | str | yes | Sanitized via `_sanitize_input(raw, max_len=600)` — strips Unicode control characters (category `C*`) except `\n`/`\t`, then `.strip()`s and truncates to 600 chars |
| `townland_hint` (or `townland`) | str | no | Same sanitizer, `max_len=120`; either key accepted, `townland_hint` checked first |
| `show_sql` (or `debug_sql`) | bool | no | Either key truthy → `include_sql=True`, echoes generated SQL in the final result event |
| `force_llm` | bool | no | Forces LLM-path generation, bypassing any fast-lane/cache reuse (pipeline-internal semantics — see `05_*`) |

Validation in the route: if `question` is empty after sanitization →
`400 {"error": "question is required"}` — this check happens **before**
the SSE stream is opened, so a validation failure is a normal buffered JSON
response, not a stream.

Audit logging (route-level, not pipeline-level): `log.info("ask_api.query
ip=%s q_len=%d townland_hint=%s", ip, len(question), bool(townland_hint))`
— logs client IP (from `X-Forwarded-For` first entry, else
`request.remote_addr`), the *length* of the question (not its content), and
whether a townland hint was supplied. Explicitly does not log question text,
per the inline comment ("for abuse detection").

**Response**: `Response(generate(), content_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})`.
`X-Accel-Buffering: no` explicitly disables reverse-proxy (nginx/gunicorn)
response buffering so SSE frames reach the client as they're yielded, not
batched. `generate()` is wrapped in `@stream_with_context` so it can access
the Flask request context while iterating (needed since the generator
function is called after the route handler has technically "returned").

**SSE frame shapes** (all lines are the standard `data: <json>\n\n` SSE
format; three `type` values are emitted, confirmed against
`answer_question_stream`'s own docstring in `ask_service.py`):

```
{"type": "progress", "stage": "...", "status": "started"|"completed", "label": "...", "detail": "...", "duration_ms": N}
{"type": "result", ...full payload...}
{"type": "error", "message": "..."}
```

If the generator raises an uncaught exception mid-stream, the route's own
`try/except` around the `yield from` loop catches it, logs
`ask_api.stream_failed` via `log.exception`, and yields one final
`{"type": "error", "message": "<str(exc)>"}` frame — the HTTP response
itself still completes with a `200` status (SSE responses cannot change
status mid-stream; the `type: error` frame is the only signal a JS client
gets that something failed after streaming has started).

**`result` frame payload** — confirmed keys from the final `payload` dict
built in `answer_question_stream`/`_orchestrated_pipeline_stream` (full
semantics of each field belong to `05_*`–`07_*`; listed here only to
document the wire contract):

`question, answer, actual_answer, llm_rephrased_answer, columns, rows,
row_count, llm, llm_rewrite, vrti_query_generation, townland_context,
townland_resolution, entity_resolution, kg_context, availability,
related_insights, chart, query_provenance, suggestions,
structured_output, pdf_url, warnings, source_tables, graph_comparison,
discrepancies, fusion (nested: discrepancy_count, agreement_count,
entity_label, kg_uri, fusion_text, source_provenance), subgraph_context
(nested, nullable), graphrag_context (nested, nullable)`.

`sql` is present **only** if the request set `show_sql`/`debug_sql` truthy
(`include_sql=True`). `pdf_url`, when non-null, is of the form
`/api/ask/pdf/<generated-filename>.pdf` — the download route in this same
blueprint (see below).

### `POST /api/ask/feedback`

Request body (JSON):

| Key | Type | Required |
|---|---|---|
| `question` | str | yes |
| `feedback` | str | yes — must be `"up"` or `"down"` (case/whitespace normalised) |
| `townland_hint` | str | no |
| `sql_text` | str | no |
| `vrti_postgres_sql` | str | no |
| `note` | str | no |
| `result_row_count` | int | no (default `0`) |
| `availability_state` | str | no |
| `llm_meta` | dict | no (default `{}`) |
| `reused_memory_id` | int | no |
| `sample_answer` | str | no |
| `summary_json` | dict | no (default `{}`) |

Validation in the route:
- Empty `question` → `400 {"error": "question is required"}`.
- `feedback` not in `{"up", "down"}` → `400 {"error": "feedback must be 'up' or 'down'"}`.

Delegates to `ask_service.record_query_feedback(...)`. A `ValueError` raised
by the service (belt-and-suspenders re-validation of `feedback`) is caught
in the route and converted to `400 {"error": str(exc)}`. On success,
`200` with:

```json
{ "ok": true, "feedback": "up"|"down", "stored_in_memory": true|false, "memory_id": <int|null> }
```

`stored_in_memory`/`memory_id` reflect whether an "up" vote with valid SQL
was written or updated in `ask_query_memory` (used for fast-lane reuse in
the legacy pipeline — see `CLAUDE.md`'s Ask pipeline section).

### `GET /api/ask/llm-status` and `GET /api/ask/ollama-status`

No params. Both call `ask_service.check_llm_status()` verbatim and return
the same status code logic: `200` if `status["available"]` else `503`.
`ollama-status` is a pure backward-compat alias (route docstring: "backward-
compatible status alias") — same function body, different path, kept for
old frontend JS from before multi-provider support (Claude/Grok/OpenRouter)
was added.

Response shape varies by configured provider (`ASK_LLM_PROVIDER` env var),
but always includes at minimum `available` (bool), `provider` (str),
`configured_provider` (str), and a `hint` (str) explaining the state; when
a specific provider is configured it also includes `active_model`.

### `GET /api/ask/townland-suggest`

Query param: `q` (str, `""` default). If empty, returns suggestions as an
empty list without calling the service. Otherwise delegates to
`ask_service.suggest_townlands(query, limit=8)`.

```json
{ "query": "<q>", "suggestions": [ { "name": "...", "name_norm": "...", "civil_parish": "...", "barony": "...", "county": "...", "centroid_lat": ..., "centroid_lon": ..., "local_record_count": 0, "score": 0.0 }, ... ] }
```

`suggest_townlands` is a thin public wrapper around
`_suggest_townland_matches(query, limit=limit, min_score=0.55)` — fuzzy
`difflib.SequenceMatcher` scoring against the townland catalog, restricted
implicitly to whatever `_townland_catalog()` returns (all counties, not
Wicklow-scoped — contrast with `townland-catalog` below).

### `GET /api/ask/townland-catalog`

No params. Reads `ask_service._townland_catalog()` and filters it in the
**route handler itself** to `county == "wicklow"` (case-insensitive),
projecting only three fields per item: `name`, `civil_parish`,
`name_gaelic`. `200 [ {...}, ... ]` — a bare array.

Sets `resp.headers["Cache-Control"] = "public, max-age=300"` explicitly in
the route (a 5-minute browser/proxy cache) — the only route in the entire
app observed to set an explicit cache header this way (contrast with the
app-wide `SEND_FILE_MAX_AGE_DEFAULT=86400` for static files, documented in
`01_architecture_overview.md` §2.3).

### `GET /api/ask/estate-overview`

No params. This entire endpoint is implemented **inline in the route
handler** — it opens its own `get_db_conn()` and runs ~15 raw SQL queries
directly (not delegated to any service module), used to power the "All
Townlands" panel on the Ask page. County is hardcoded to `UPPER(county)='WICKLOW'`.

Queries computed (all against `townland`, `census_record`, `unified_record`
joined as needed): `townland_count`, `parish_count`, `barony_count`,
`gaelic_name_count`, `townlands_with_coords`, `townlands_with_area`,
`total_area_sqkm`, `largest_townland` (`{name, area_sqkm}` or `null`),
`smallest_townland` (same shape, smallest non-zero area), `baronies` (list
of names), `top_parishes_by_townlands` (top 5, `{parish, townland_count}`),
`pop_1841`, `pop_1851`, `pop_decline_pct` (computed as
`round((pop_1841 - pop_1851) / pop_1841 * 100, 1)`, `null` if `pop_1841` is
falsy), `top_pop_1841` (top 5 townlands, `{name, population}`),
`top_parish_pop_1841` (top 5 parishes aggregated, `{parish, population}`),
`total_records`, `emigrant_count`, `eviction_count`, `tenant_count`,
`canada_count` (`is_canada_destination=1`), `year_min`, `year_max`,
`top_surnames` (top 10, `{surname, count}`), `top_baronies_by_records`
(top 5, `{barony, count}`).

Full response is a single flat JSON object with all ~24 keys above at the
top level. On any exception: `log.exception("estate-overview failed")` then
`500 {"error": str(exc)}`. This is the only route among those documented
here that both (a) contains substantial raw SQL directly in a route
handler and (b) returns a `500` with the raw exception string in the body —
both deviations from the `CLAUDE.md` convention that "SQL stays in
repositories" and thin route handlers.

### `GET /api/ask/pdf/<path:filename>`

Path param: `filename` (str) — accepts any path segment (`<path:...>`
converter) but is immediately reduced to its basename:
`safe_name = Path(filename).name`, which strips any directory traversal
components (`../`, absolute paths, etc.) before use — the one explicit
path-traversal guard in the routes layer.

Validation:
- `safe_name` must end in `.pdf` (case-insensitive) → else
  `400 abort(description="Only PDF files may be downloaded from this endpoint.")`.
- Resolved path is `ActiveConfig.EXPORTS_DIR / "ask" / safe_name` — must
  exist → else `404 abort(description="Report not found.")`.
- Otherwise `send_file(pdf_path, as_attachment=True, download_name=safe_name, mimetype="application/pdf")`.

Both aborts render Flask's default (HTML) error page, not JSON, consistent
with `exports.py`'s download route.

---

## 8. `kg_explore.py` — `/api/kg/*`

Backs the KG Explore page's D3.js force-graph visualisation and the SQL-vs-
SPARQL comparison tool. Full graph-traversal/community logic lives in
`10_knowledge_graph_retrieval.md`; this section documents only the HTTP
contract, grounded in `kg_service.py`'s top-level function signatures and
return dicts (read only far enough to confirm response shapes, not the
internal traversal algorithm).

```
GET  /api/kg/graph               — graph topology (nodes + edges) for D3
GET  /api/kg/scenarios           — canned SQL vs SPARQL comparison scenarios
POST /api/kg/compare             — run one scenario or custom SQL/SPARQL
POST /api/kg/explain-mismatch    — LLM explanation of SQL/SPARQL row-count mismatch
GET  /api/kg/graphdb-status      — live GraphDB SPARQL endpoint connectivity probe
GET  /api/kg/townland/<name>     — person records for one townland (drill-down)
GET  /api/kg/townland-rich/<name> — enriched townland detail (DB + VRTI + LLM narrative)
GET  /api/kg/rdf-status          — local rdflib graph (coolattin.ttl) health check
```

### `GET /api/kg/graph`

Query param: `limit` (str, parsed to int, default `"600"`; clamped to
`min(int(limit), 1000)`; any parse failure silently falls back to `600`).

Delegates to `kg_service.build_graph(limit=limit)`. **Note**: `limit` is
accepted by the route and passed through, but `build_graph`'s own
parameter is annotated `# noqa: ARG001` (i.e. explicitly marked unused) —
the function returns a **cached, fixed geographic hierarchy** built once
from the `townland` table (County → Barony → CivilParish → Townland,
Wicklow only) regardless of the `limit` value requested. The cache is
process-global (`_GRAPH_CACHE`, guarded by `_GRAPH_CACHE_LOCK`) and only
rebuilt via the separate `reset_graph_cache()` function (not exposed as a
route).

Response shape: `200 {"nodes": [...], "edges": [...], "meta": {...}}`.

- `nodes`: one entry per County/Barony/CivilParish/Townland, each
  `{"id": "<county_X|barony_X|parish_X|t_X>", "type": "County"|"Barony"|"CivilParish"|"Townland", "label": "...", "color": "#hex", "size": N, ...type-specific extra fields}`.
  Townland nodes additionally carry `name_gaelic, civil_parish, barony,
  county, electoral_division, placename_theme, centroid_lat, centroid_lon,
  kg_uri, record_count` (a live `COUNT(DISTINCT record_id)` from
  `unified_record` matched by `townland_norm`); node `size` is
  `min(8 + record_count // 40, 14)`.
- `edges`: `{"source": "<parent id>", "target": "<child id>", "label": "contains", "type": "county_barony"|"barony_parish"|"parish_townland"}` — a strict containment hierarchy, no cross-links.
- `meta`: `{"node_count", "edge_count", "county_count", "barony_count", "parish_count", "townland_count", "with_gaelic", "source": "geographic_hierarchy"}`.

### `GET /api/kg/scenarios`

No params. `200 {"scenarios": COMPARISON_SCENARIOS}` — a fixed, hand-authored
list of dicts, each with (at minimum) `id`, `label`, `description`, `sql`,
`sparql` keys (e.g. the `emigration_count_by_townland` scenario compares a
`GROUP BY townland` SQL query against an equivalent SPARQL query over the
`co:` ontology, with the description explicitly noting the SQL is written
to filter `NULL`/empty townlands to match SPARQL's closed-triple-pattern
semantics).

### `POST /api/kg/compare`

Request body (JSON): `id` (str, optional — scenario id to run),
`custom_sql` (str, optional), `custom_sparql` (str, optional).

Route logic:
1. If `id` matches an entry in `COMPARISON_SCENARIOS`, that scenario is
   loaded.
2. `sql_text` = `custom_sql` if provided, else the scenario's `sql`.
   `sparql_text` = `custom_sparql` if provided, else the scenario's
   `sparql`. (Custom query text always takes precedence over the scenario's
   canned query, even when a scenario `id` is also supplied.)
3. If neither `sql_text` nor `sparql_text` resolved to anything →
   `400 {"error": "Provide either an id or custom_sql/custom_sparql."}`.
4. If `sql_text` present: `kg_service.run_sql(sql_text, max_rows=500)`,
   timed with `time.perf_counter()`. `run_sql` only permits `SELECT`
   statements (checks `sql.strip().upper().startswith("SELECT")`, returns
   `([], [], "Only SELECT queries are permitted.")` otherwise — this is a
   read-only guard local to `kg_service.py`, separate from the Ask
   pipeline's `_sanitize_and_validate_sql`).
5. If `sparql_text` present: `kg_service.run_sparql(sparql_text)` against
   the in-process `rdflib` graph (prefixes for `co:`, `ex:`, `schema:`,
   `xsd:`, `rdf:`, `rdfs:` are auto-prepended). Rows are capped to 500 for
   display even though `total_row_count` reports the true count.

Response (`200`):

```json
{
  "sql": { "query": "...", "columns": [...], "rows": [...], "row_count": N, "capped": bool, "error": null, "duration_ms": N },
  "sparql": { "query": "...", "columns": [...], "rows": [...], "row_count": N, "total_row_count": N, "capped": bool, "error": null, "duration_ms": N },
  "scenario": { "id": "...", "label": "...", "description": "..." }
}
```

`sql`/`sparql`/`scenario` keys are present only if the corresponding input
was resolved (e.g. a request with only `custom_sql` omits the `sparql` key
entirely, not just leaves it null). Query errors (`sql_err`/`sparql_err`)
are surfaced in-band as the `error` field within each sub-object — the
route itself does not turn a query execution error into a non-200 HTTP
status.

### `POST /api/kg/explain-mismatch`

Request body (JSON): `sql_query`, `sparql_query` (str), `sql_rows`,
`sparql_rows` (list of dicts — sample rows, not full result sets),
`sql_row_count`, `sparql_row_count` (int).

Validation: if both `sql_query` and `sparql_query` are empty →
`400 {"error": "No queries provided."}`.

Delegates to `kg_service.explain_mismatch(...)`, which calls an LLM
(OpenRouter, gated on `OPENROUTER_API_KEY`) to produce a structured
markdown explanation of *why* the SQL and SPARQL result sets differ in row
count (closed-world vs open-world semantics, `NULL` handling differences,
etc.). Response (`200` always — the route does not vary status on LLM
failure):

```json
{ "analysis": "<markdown>"|null, "reasons": ["...", ...], "model_used": "<model id>"|null, "error": null|"<error string>" }
```

If `OPENROUTER_API_KEY` is unset: `{"analysis": null, "reasons": [], "model_used": null, "error": "LLM not configured — OPENROUTER_API_KEY is not set."}`.
If the LLM call itself fails: `{"analysis": null, "reasons": [], "model_used": null, "error": "LLM call failed — check server logs for details."}`.

### `GET /api/kg/graphdb-status`

No params. Live-probes the GraphDB SPARQL endpoint via
`backend.integrations.graphdb_sparql.probe()`; if reachable, also fetches
`triple_count()`. `200`:

```json
{ "enabled": true, "endpoint": "http://localhost:7200/repositories/coolattin", "available": true|false, "triple_count": N, "data_loaded": true|false }
```

`enabled`/`endpoint` come straight from `ActiveConfig.GRAPHDB_ENABLED` /
`GRAPHDB_SPARQL_ENDPOINT` (see `01_architecture_overview.md` §4.2).
`triple_count` is `-1` if the endpoint isn't `available`. `data_loaded` is
`triple_count > 0`.

### `GET /api/kg/townland/<path:name>`

Path param: `name` (str, free text via `<path:...>` converter, allowing
slashes/spaces in townland names). No query params — `limit` is fixed at
the service's default of `50` (the route does not expose a way to override
it). Delegates to `kg_service.get_townland_persons(name)`. `200`:

```json
{ "townland": "<name>", "total": N, "persons": [ { "name": "<forename surname, or 'Unknown'>", "year": N, "occupation": "...", "event_type": "emigration"|"eviction"|"tenancy" } ... up to 50 ] }
```

`event_type` is derived by the service as a simple priority chain:
emigration if `has_emigration_record`, else eviction if
`has_eviction_record`, else tenancy — i.e. a record with multiple flags
set only reports the highest-priority one here (this is a real
information-loss point in the response, called out because it affects
what the frontend can display, not because it's a bug this doc is
diagnosing).

### `GET /api/kg/townland-rich/<path:name>`

Path param: `name` (str, free text). No query params. Delegates to
`kg_service.get_townland_rich_detail(name)`, which combines a local DB
read (townland reference row + census trend + clearances), a live VRTI
SPARQL call, an LLM-generated SPARQL query rewrite, and an LLM-generated
narrative — all in one request (this is the heaviest single GET endpoint in
the app; multiple outbound network calls happen synchronously within the
request). `200` response shape (confirmed as the literal return dict in
`kg_service.py`):

```json
{
  "townland_name": "<name>",
  "db_data": { "townland": {...}|absent, "census": [...], "...": "..." },
  "vrti_data": { "...": "..." },
  "generated_sparql": "<sparql text>"|null,
  "sparql_error": "..."|null,
  "sparql_results": { "...": "..." },
  "narrative": "<markdown>"|null,
  "narrative_error": "..."|null,
  "context_used": "<str>"
}
```

This route has no explicit error-status handling visible at the route
layer — failures inside `get_townland_rich_detail` (VRTI unreachable, LLM
unavailable) are represented as `null`/error-string fields within the
`200` payload rather than a non-2xx status, consistent with the rest of
this blueprint's "errors are in-band" pattern.

### `GET /api/kg/rdf-status`

No params. Reads `kg_service._ttl_path()` (the on-disk path to
`coolattin.ttl`, the RDF uplift file — see `CLAUDE.md`'s `data/seed/`
listing) and checks existence/size, then calls `_load_rdf_graph()` to get
a live `rdflib.Graph` (or `None` if the file is missing/unparseable).
`200`:

```json
{ "file_present": true|false, "file_path": "<abs path>", "file_size_mb": 0.0, "triple_count": N, "available": true|false }
```

`triple_count` is `-1` if the graph failed to load (`g is None`); `size_mb`
is `0` when the file is absent (computed only when `file_present`).

---

## 9. Legacy compatibility routes (no blueprint)

Registered directly on the `Flask` app object by
`create_app.py::_register_legacy_routes(app)`, **after** all blueprints are
mounted (see `01_architecture_overview.md` §2.8 for why this ordering
matters and why these exist at all — kept so old cached/bookmarked
frontend requests don't 404).

| Method | Path | Delegates to | Duplicate of |
|---|---|---|---|
| GET | `/api/centroids` | `map_service.build_centroids()` | `GET /api/map/centroids` (§4) — identical function call, different path |
| GET | `/api/workhouse/match/<record_id>` | `workhouse_service.get_matches_for_record(record_id)` | *(no blueprint equivalent — this is the only route for this functionality)* |

### `GET /api/workhouse/match/<record_id>`

Path param: `record_id` (str). `200 <dict>`.
`workhouse_service.get_matches_for_record` itself:
1. Prefers persisted entity-resolution links — if
   `workhouse_entity_resolution.has_persisted_links()` is `True`, delegates
   to `workhouse_entity_resolution.get_matches_for_record(record_id)` and
   returns that shape directly (persisted-link schema, out of scope here —
   see `CLAUDE.md`'s workhouse entity resolution subsystem description).
2. Otherwise falls back to the legacy in-memory fuzzy index
   (`get_match_index()`), returning
   `{"record_id": "<id>", "count": N, "matches": [...]}`.
3. Any exception while checking for persisted links is caught at `log.debug`
   level and silently falls through to the legacy path (i.e. this route
   never surfaces a resolution-layer failure as an HTTP error — it always
   degrades to the older, always-available fuzzy index).

New code must not add routes here — per `01_architecture_overview.md` §2.8,
this function is a closed set kept only for backward compatibility; any new
endpoint should go through a blueprint in `backend/routes/`.
