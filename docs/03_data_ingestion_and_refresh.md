# 03 — Data Ingestion and Refresh

Technical reference for how data gets **into** `coolattin.db` and how it is
kept up to date afterwards: the three one-shot ingest jobs
(`backend/jobs/*.py`), the DB-first/KG-second retrieval strategy in
`census_service.py`, the entity-resolution/geometry-validation machinery in
`townland_service.py`, the reconciliation subsystem
(`townlands_reference.py` + the alias map + the reconciliation-gaps log),
and the forced-refresh orchestration in `refresh_service.py`. See
`01_architecture_overview.md` for the process/config layer and
`02_database_schema.md` for full column definitions of every table named
below (`townland`, `census_record`, `clearances_record`, `refresh_state`,
`townland_xref`, `field_provenance`, `match_review`) — this document does
not re-derive their schema, only how rows get written into them.

## 1. Three ingest jobs, one shared purpose

All three live in `backend/jobs/` and are one-shot CLI scripts (`python -m
backend.jobs.<name>`), each wrapping its work in
`create_app().app_context()` so `get_db_conn()` and `ActiveConfig` resolve
exactly as they would inside a live request. None of them is scheduled —
there is no cron/APScheduler in this codebase (confirmed by the stub at
`census_service._schedule_background_refresh`, §4.6). They are triggered
manually, or via the project's `/ingest` skill (see CLAUDE.md), or via the
two admin-gated HTTP refresh routes (§5).

| Job | Module | Data sources | Scope |
|---|---|---|---|
| **Full ingest** | `backend/jobs/full_ingest.py` | Estate GeoJSON + VRTI KG | Everything: `townland`, `census_record` (both estate-survey and standard years), `clearances_record` |
| **Census ingest** | `backend/jobs/census_ingest.py` | VRTI KG (CSV seed fallback) | `census_record` only, plus an Excel export |
| **Townlands ingest** | `backend/jobs/townlands_ingest.py` | VRTI KG + townlands.ie reference snapshot | `townland` only, plus a reference snapshot file and an Excel export |

`full_ingest.py` is the canonical "populate everything from scratch"
entry point and is what CLAUDE.md's `/ingest` flow and the `reset-db` skill
run. `census_ingest.py` and `townlands_ingest.py` are narrower, single-table
tools — useful for refreshing one dataset without re-walking the whole
GeoJSON, and structurally similar to (but independent of) the logic inside
`refresh_service.py` (§5) and `census_service.py` (§4).

## 2. `full_ingest.py` — the canonical ingest pipeline

### 2.1 What it builds and in what order

`run_full_ingest(dry_run: bool = False) -> dict` (full_ingest.py:69) is the
single public function. Six steps, all inside one call:

```
Step 1  Load GeoJSON features (townlands.json)
Step 2  Probe the VRTI SPARQL endpoint (online/offline decision)
Step 3  Per-townland loop:
          3a  Build base Townland from GeoJSON properties
          3b  KG enrichment overlay (if online)
          3c  Persist townland (upsert)
          3d  Estate population survey records (from GeoJSON columns)
          3e  Clearances records (from GeoJSON columns)
Step 4  Standard census 1841-1891, fetched once at county scope from KG
        (CSV-seed fallback if the KG returns zero rows)
Step 5  Bulk persist all census_record + clearances_record rows
Step 6  Update refresh_state under key "full_ingest"
```

Returns a stats dict (`townlands_processed`, `townlands_kg_enriched`,
`census_records_json`, `census_records_kg`, `census_records_csv_seed`,
`clearances_records`, `kg_errors`) that the CLI entry point prints and uses
to set the process exit code (`0` if `townlands_processed > 0`, else `1`).

### 2.2 Step 1 — GeoJSON feature loading

```python
geojson_path = ActiveConfig.STATIC_DATA_DIR / "townlands.json"
```

`STATIC_DATA_DIR` resolves to `frontend/static/data`, i.e. this is the same
6.2 MB GeoJSON the map page serves directly to the browser (see
`01_architecture_overview.md` §2.3 on why its browser cache lifetime is
24h) — ingest reads the identical file the frontend uses, so there is no
drift between "what the map shows" and "what the ingest imports." Missing
file → `log.error("full_ingest.geojson_missing | path=%s")` and an early
`return stats` with all-zero counters (not an exception — a clean no-op
failure with a non-zero exit code from the CLI).

`_load_geojson_features()` (full_ingest.py:346) wraps `json.load()` in a
`try/except (json.JSONDecodeError, OSError)`, logging and returning `[]` on
either failure rather than propagating — the ingest degrades to "zero
townlands processed" instead of crashing.

### 2.3 Step 2 — VRTI endpoint probe and graceful KG unavailability

```python
kg_online = vrti_sparql.probe_endpoint()
```

This single boolean gates every KG-dependent branch downstream (§2.4 step
3b, §2.5 step 4). If the probe fails:

```python
log.warning(
    "full_ingest.kg_offline — geometry, identifiers and standard census will "
    "be skipped.  GeoJSON data (names, area, estate populations, clearances) "
    "will still be stored."
)
```

The job does **not** abort — it still processes the full GeoJSON feature
list and persists names, Gaelic names, area, TD_ID/GUID, estate population
surveys (1827–1868), and clearances (1847–1856), all of which are entirely
GeoJSON-sourced and require no network call. Only the KG-exclusive fields —
boundary WKT, centroid, barony/civil parish, OSM/OSI/VRTI external IDs,
images/links, and the six standard census years — are skipped. This is the
graceful degradation CLAUDE.md refers to: a KG outage produces a smaller
but still-usable database rather than an ingest failure.

`refresh_state.source` records which mode ran: `"json+kg"` if the KG was
online, `"json"` if not (full_ingest.py:321).

### 2.4 Step 3 — per-townland processing

For each GeoJSON feature:

1. **Name extraction**: `raw_name = props.get("TL_ENGLISH")`. Features with
   no name are skipped (`log.debug`, continue) — the GeoJSON occasionally
   contains unnamed boundary artefacts.
2. **Canonicalisation**: `canonical = normalize_townland_name(raw_name)`
   (delegated to `townland_service`, §3.1). An `estate_name_map` dict is
   built as the loop runs, mapping both the fully-canonicalised name *and*
   the raw-name-run-through-`canonical_name()` (i.e. alias-resolved) to the
   same canonical value — this map is what Step 4 later uses to decide
   whether a KG census row belongs to an *estate* townland at all.
3. **Base `Townland` construction** from GeoJSON properties directly:
   `name_gaelic` ← `TL_GAEILGE`, `area_sqm` ← `AREA`, `td_id` ← `TD_ID`,
   `guid` ← `GUID`, `county` ← `COUNTY_ENGLISH`, `source="json"`.
4. **KG enrichment overlay** (3b) — only if `kg_online`. Calls
   `_fetch_kg_details(vrti_sparql, raw_name, county=json_county)`
   (full_ingest.py:357), which tries `get_townland_details_by_name()` with
   the name **title-cased** first (`"KG standard"` per the code comment),
   then falls back to the raw name casing if the title-case lookup misses.
   The GeoJSON's own county (title-cased) is passed through as a
   disambiguation hint — the comment explains this exists because some
   townland names (e.g. "Ballard") exist in multiple Irish counties, and
   without a county hint the KG lookup could resolve to the wrong county's
   townland. Any exception inside the fetch is swallowed to `log.debug` and
   treated as a miss (`None`), not a hard failure — one bad KG lookup never
   aborts the whole ingest.

   When a KG match is found, **every** KG field is overlaid unconditionally
   (KG is treated as authoritative for geography): `kg_uri`, `wkt_geometry`,
   `centroid_lat/lon`, `barony`, `civil_parish`, `county` (KG wins if
   present, else GeoJSON value is kept), `osm_id`, `osi_id`, `vrti_id`,
   `images`, `links`. `name_gaelic` and `geometry_flag` are filled only if
   not already set / if the KG DTO carries a `centroid_flag`. If the KG
   returned geometry or a centroid, `townland.source` is upgraded from
   `"json"` to `"kg"` — i.e. `source` reflects "was this row's *geometry*
   KG-sourced," not merely "was a KG match found at all." A miss increments
   `stats["kg_errors"]` and logs at `debug` level (`full_ingest.kg_no_match`)
   — despite the name, this is a normal/expected outcome for townlands the
   KG doesn't carry, not necessarily an error condition worth surfacing at
   `warning`.
5. **Persist** (3c): `townland_repository.upsert(townland)`, skipped
   entirely under `--dry-run`.
6. **Estate population survey rows** (3d) — one `CensusRecord` per year in
   `ESTATE_SURVEY_YEARS = [1827, 1839, 1848, 1850, 1860, 1868]`, reading
   columns via `ESTATE_POP_COLUMNS` (note the inconsistent GeoJSON column
   name for 1839: `T_POP_1839_` with a trailing underscore, unlike the
   other five). Only `total` is populated — `male`/`female`/
   `inhabited`/`uninhabited` are explicitly `None` because the estate
   surveys never broke population down that way. `source="json"`,
   `kg_uri=None`. A missing/blank column value is skipped (no zero-filled
   row).
7. **Clearances rows** (3e) — one `ClearancesRecord` per year in
   `CLEARANCE_YEARS = range(1847, 1857)` (1847–1856 inclusive), reading
   `Clearances_{year}` columns. `source="json"` always — CLAUDE.md and
   `02_database_schema.md` both note there is no KG equivalent for
   eviction/clearance data.

### 2.5 Step 4 — standard census years (1841–1891) from the KG

This runs **once**, after the per-townland loop, not per-townland:

```python
if kg_online and estate_name_map:
    kg_census = vrti_sparql.get_census_records_for_county(county="Wicklow")
```

The code comment explains the design choice explicitly: fetching the whole
county in one SPARQL call is "more reliable than querying the KG one
townland URI at a time, and avoids loading non-estate Wicklow rows into the
local DB." Every returned row is then filtered against `estate_name_map`:

```python
match_key = canonical_name(raw_name)
canonical = estate_name_map.get(match_key)
if not canonical:
    canonical = estate_name_map.get(normalize_townland_name(raw_name))
if not canonical:
    skipped_non_estate += 1
    continue
```

Two lookup attempts are made — alias-resolved (`canonical_name`) first,
then plain-normalised (`normalize_townland_name`) as a fallback — before a
KG row is discarded as belonging to a non-estate Wicklow townland
(`skipped_non_estate` is logged but not otherwise surfaced). A
`seen_census_keys` set of `(townland_name, year)` tuples deduplicates
against rows already queued from the GeoJSON pass (defensive — in practice
the GeoJSON pass only ever produces estate-survey years, which don't
overlap the standard years, so this guard is a belt-and-braces check
against any future year overlap).

Each surviving row becomes a `CensusRecord` with `source="kg"`,
`kg_uri=dto.townland_uri`, `total = male + female` computed only if at
least one of `male`/`female` is non-null (else `total=None` — this mirrors
the `CensusRecord.__post_init__` normalisation documented in
`02_database_schema.md` §1.5, applied here explicitly rather than relying
on the dataclass default).

**CSV-seed fallback**: if the KG census fetch returns zero estate rows
(`stats["census_records_kg"] == 0`, e.g. VRTI is reachable but the county
query itself comes back empty), the job falls back to
`load_standard_census_seed_records(allowed_townlands=set(estate_name_map.values()))`
from `census_seed.py` (§2.6), logging `full_ingest.census_kg_empty_using_csv_seed`
at `warning`. This is a **narrower** fallback trigger than the KG-offline
branch in Step 2/3 — it fires even when the KG endpoint is reachable, as
long as it returns no usable rows for the estate townland set.

### 2.6 `census_seed.py` — the bundled CSV fallback

Not a CLI job — a helper module (`load_standard_census_seed_records()`)
called from three places: `full_ingest.py` (Step 4, above),
`census_ingest.py` (§2.7), and indirectly nowhere in `census_service.py`
(which has its own separate KG-or-nothing miss path, §4.5 — it does *not*
call this seed loader).

It reads a bundled CSV — `_resolve_seed_path()` tries
`unified_census.csv` first, then `wicklow-census-data.csv`, both under
`STATIC_DATA_DIR` — and for each `(townland, year)` combination present in
the row set, **sums** `{year} Male`, `{year} Female`, `{year} Inhabited`,
`{year} Uninhabited` columns across all matching CSV rows for that
townland/year (a `defaultdict(_empty_bucket)` accumulator keyed on
`(canonical_townland, year)`), tracking a `seen_*` boolean per field so a
field that never appeared in any row for that key stays `None` rather than
being reported as `0`. `allowed_townlands` (a canonical-name whitelist) and
`years` are both optional filters — `full_ingest.py` passes
`allowed_townlands` (restrict to estate townlands only);
`census_ingest.py` passes `years=[year] if year else None` (restrict to the
CLI's `--year` argument, if given). Every record produced this way carries
`source="csv_seed"`.

### 2.7 `census_ingest.py` — standalone census-only refresh

Six steps, structurally similar to `full_ingest.py`'s census-only slice but
independently implemented (does not call into `full_ingest.py` or
`census_service.py`):

1. `vrti_sparql.probe_endpoint()` — **hard fail** on unreachable, unlike
   `full_ingest.py`'s graceful degrade: `log.error(...)` and `return 0`
   immediately. The module docstring's all-caps banner
   (`RUN THIS TO POPULATE THE DATABASE FROM THE KNOWLEDGE GRAPH`) signals
   this job's whole purpose is a KG-backed refresh, so an unreachable
   endpoint is treated as nothing-to-do rather than something to degrade
   around.
2. `vrti_sparql.get_census_records_for_county(county="Wicklow", year=year)`
   — `year` is the optional CLI `--year` argument; omitted means all years.
3. If the KG call returns rows, they're normalised through
   `canonical_name()` (alias-resolved, unlike `full_ingest.py`'s Step 4
   which tries alias-resolved *then* falls back to non-alias-resolved) into
   `CensusRecord`s with `source="kg_refresh"`. If zero rows come back,
   falls through to `census_seed.load_standard_census_seed_records()` with
   `source="csv_seed"` — note this job's CSV fallback is **not**
   restricted by `allowed_townlands`, so it can seed non-estate Wicklow
   townlands too (unlike `full_ingest.py`'s Step 4 fallback).
4. `census_repository.upsert_many(records)`.
5. `export_service.export_census(records, filters)` — writes an Excel file
   under `exports/census/`; every run of this job produces an export,
   unlike `census_service.get_census_data()`'s cache-hit path which never
   exports.
6. `refresh_state_repository.upsert(dataset_key, source=..., record_count=...,
   export_file=...)` — `dataset_key` is `f"{DATASET_KEY_PREFIX}_{year}"` if a
   year was given, else just `DATASET_KEY_PREFIX` (`"wicklow_census"`) —
   the same key-derivation scheme `CensusFilters.dataset_key()` uses, so
   this job's writes are visible to `census_service`'s staleness checks for
   the equivalent filter scope.

### 2.8 `townlands_ingest.py` — standalone townland-only refresh

Fetches **all** available townlands from the KG with no county restriction
(`vrti_sparql.get_townlands(county=None, limit=5000)`) — broader than
`full_ingest.py`, which only ever touches the 152 estate townlands named in
the GeoJSON. Each KG DTO becomes a minimal `Townland(name=normalize_townland_name(dto.name),
name_gaelic=..., kg_uri=dto.uri, wkt_geometry=dto.wkt_geometry, source="kg")`
— notably **no** GeoJSON-derived fields (area, TD_ID, GUID, estate
population, clearances) are ever set by this job, since it never touches
the GeoJSON at all. If the KG returns nothing, the job logs a pointer to
run `full_ingest.py` instead and returns `0` — it does not have its own CSV
fallback.

Step 3 calls `reconcile_with_reference(townlands)` (§3.6) to backfill
barony/civil_parish/electoral_division/gaelic_name from the townlands.ie
reference snapshot, then persists via `townland_repository.upsert_many()`.

Step 5 is unique to this job: it **writes** a fresh reference snapshot to
`data/seed/wicklow_townlands_reference.json` — the very file
`townlands_reference.py` (§3.6) reads back on every subsequent
reconciliation call. This creates a bootstrapping relationship: the first
run of `townlands_ingest.py` (or a run after the snapshot file has been
deleted) reconciles against an **empty** reference (logged as
`townland_service.reconcile — reference empty, skipping enrichment`) and
then immediately regenerates the snapshot from what it just fetched, so
subsequent runs (and subsequent `reconcile_with_reference()` calls from
other code paths, e.g. `refresh_service.trigger_townlands_refresh()`, §5.2)
have real reference data to enrich against.

Step 6 exports to Excel (`export_service.export_townlands`), wrapped in its
own `try/except` — an export failure is logged as a warning and does not
fail the job (`export_path` stays `None`).

Step 7 updates `refresh_state` under the fixed key `"wicklow_townlands"`
with `source="kg_refresh"`.

## 3. `townland_service.py` — normalisation, entity resolution, geometry

This module's own docstring lists five responsibilities; each is covered
below with the exact functions, algorithms, and numeric thresholds found in
the code.

### 3.1 Name normalisation pipeline — `normalize_townland_name()`

`normalize_townland_name(name: str) -> str` (townland_service.py:60), the
single canonicalisation function every ingest path routes names through
(directly, or via `canonical_name()` which adds alias resolution on top).
Exact pipeline, in order:

1. Unicode **NFC** normalisation (`unicodedata.normalize("NFC", name)`) —
   guards against names arriving as NFD-decomposed Unicode (e.g. combining
   diacritics as separate codepoints) producing spurious duplicate rows for
   what is visually the same name.
2. Smart-quote folding: `’`/`‘` → ASCII `'`.
3. Strip + collapse internal whitespace (`" ".join(s.split())`).
4. Strip a leading `"Townland of "` / `"townland of "` prefix via regex.
5. Strip **type** qualifiers inside parentheses — `_TYPE_QUALIFIER_RE`
   matches `(civil parish)`, `(electoral division)`, `(barony)`, `(county)`,
   `(townland)` case-insensitively and removes them entirely, including the
   parentheses.
6. Strip any remaining bare parenthesis characters while **keeping** their
   content — this is the step that preserves locational qualifiers, e.g.
   `"Ballinacor (Upper)"` → `"Ballinacor Upper"` after this step (the type
   regex in step 5 does not match `"Upper"` since it isn't one of the five
   type words).
7. Remove punctuation other than hyphens and apostrophes
   (`re.sub(r"[^\w\s\-']", "", s)`).
8. Uppercase.

The module-level constant `_LOCATIONAL_QUALIFIERS = frozenset({"UPPER",
"LOWER", "EAST", "WEST", "NORTH", "SOUTH", "BEG", "MORE"})` is what keeps
`BALLINACOR UPPER` and `BALLINACOR LOWER` as two **distinct** canonical
names rather than being collapsed — the docstring calls this out
explicitly as a deliberate outcome, not a bug.

`extract_qualifier(raw: str) -> Optional[str]` (townland_service.py:93) is
a separate, narrower function used when a caller wants the qualifier
*split out* rather than folded into the name: it regex-matches a
parenthetical suffix, and returns the qualifier only if its first word is
in `_LOCATIONAL_QUALIFIERS` — a type qualifier like `(Civil Parish)`
correctly returns `None` here since `"CIVIL"` isn't in the set.

### 3.2 Alias resolution — `resolve_alias()` / `canonical_name()`

```python
def canonical_name(raw: str) -> str:
    return resolve_alias(normalize_townland_name(raw))
```

`resolve_alias()` looks the already-normalised name up in a module-level
`_ALIAS_MAP` dict, lazily loaded once per process by
`_ensure_alias_map_loaded()` (townland_service.py:671) from
`data/seed/townland_aliases.json` (`ActiveConfig.DATA_SEED_DIR`). The seed
file is a flat JSON object of `{variant_spelling: canonical_form}` pairs —
e.g. `"Ballinacur": "BALLINACOR"`, `"Colattin": "COOLATTIN"`, `"Cill
Mhantain": "KILMANTAN"` (the last being a Gaelic-form variant mapped to its
English canonical form). Both keys and values are re-run through
`normalize_townland_name()` when the map is built, so the map's own
entries are stored in fully-normalised form and any key starting with `_`
is skipped as a metadata/comment entry. A missing or unparseable file
degrades to an empty map with a `log.warning`, not a crash.

`canonical_name()` is the function used throughout the ingest jobs and
`census_service.py` wherever a *fully resolved* name (normalised **and**
alias-mapped) is needed, vs. `normalize_townland_name()` alone where only
normalisation is wanted (e.g. as a first-pass key before trying the
alias-resolved form as a second attempt, as in `full_ingest.py` Step 4,
§2.5).

### 3.3 Geometry validation — `validate_and_clean_geometry()`

`validate_and_clean_geometry(wkt: Optional[str]) -> GeomResult`
(townland_service.py:134) is Shapely-backed and follows the docstring's
four checks exactly:

1. **Parse**: `shapely.wkt.loads(wkt)`. A parse failure produces
   `flags=["wkt_parse_error"]` and an all-`None`/`valid=False` result — no
   exception propagates.
2. **Validity + repair**: if `not geom.is_valid` (ring self-intersections,
   unclosed rings, etc. — flagged `"geometry_invalid"`), two repair
   attempts are tried in order:
   - `shapely.validation.make_valid(geom)` → if the result validates,
     accepted, flagged `"geometry_repaired_make_valid"`.
   - else `geom.buffer(0)` (the classic Shapely idiom for coercing a
     self-intersecting polygon back to valid) → if that validates,
     accepted, flagged `"geometry_repaired_buffer"`.
   - If neither repair produces a valid geometry, flagged
     `"geometry_unrecoverable"` and the function returns early with
     `valid=False` (original WKT preserved unmodified, no centroid
     computed).
3. **Centroid via `representative_point()`**, not `.centroid` — the
   docstring/comment explains why: `representative_point()` is
   *guaranteed* to lie within the polygon (unlike the arithmetic centroid,
   which can fall outside a concave or multi-part polygon). The function
   still separately computes `geom.centroid` and checks
   `centroid.within(geom)`, flagging `"geometric_centroid_outside_polygon"`
   if not — this is a diagnostic-only check; the arithmetic centroid itself
   is never stored, only used to raise the flag.
4. **Within-polygon assertion** on the representative point itself: if
   `not rep.within(geom)` (should not normally happen given the
   guarantee, but checked defensively), flags
   `"representative_point_outside_polygon"` and logs at `warning`.

Returns a `GeomResult(wkt, centroid_lat, centroid_lon, flags, valid)`
dataclass. `clean_wkt` is only the *repaired* geometry's WKT if a repair
flag fired (`any("repaired" in f for f in flags)`) — otherwise the
original input WKT string is passed through unchanged, avoiding
Shapely's own WKT serialisation (with its different float formatting)
touching geometries that were already valid. If the `shapely` package
itself isn't installed, the whole function short-circuits immediately to
`flags=["shapely_unavailable"]`, `valid=False`, WKT passed through
unmodified — geometry validation is an optional enhancement, not a hard
ingest dependency.

A parallel, simpler function — `build_centroids_from_geojson()`
(townland_service.py:557) — computes centroids for **all** features in a
GeoJSON file at once (used by `map_service.build_centroids()`, exposed at
the legacy `GET /api/centroids` route per `01_architecture_overview.md`
§2.8). It also prefers `representative_point()` when Shapely is available,
but falls back to a plain arithmetic ring-average (`sum(lat)/len`,
`sum(lon)/len` over the first ring's points) if Shapely is not installed —
a cruder approximation than the `GeomResult` repair path, since it never
attempts to validate or repair the polygon first.

### 3.4 Entity resolution — feature scoring

`score_pair(a: dict, b: dict) -> MatchFeatures` (townland_service.py:269)
computes a `MatchFeatures` vector for a *candidate pair* of townland record
dicts (each dict needs at minimum a `name` key). Six signals:

| Signal | Computation | Weight in `.score` |
|---|---|---|
| `external_id_match` | any shared non-null value across `osm_id`, `osi_id`, `vrti_id`, `kg_uri`, `logainm_id` | decisive — short-circuits `.score` to `0.99` |
| `jaro_winkler` | `rapidfuzz.distance.JaroWinkler.normalized_similarity()` on the two names run through `normalize_townland_name()` | 0.35 |
| `gaelic_similarity` | same Jaro-Winkler function on uppercased/stripped `name_gaelic` fields | 0.10 |
| `same_civil_parish` | exact string match on uppercased `civil_parish` | 0.15 (boolean) |
| `same_barony` | exact string match on uppercased `barony` | 0.10 (boolean) |
| `area_ratio` | `min(area_a, area_b) / max(area_a, area_b)` (both must be > 0) | 0.10 |
| `polygon_iou` | Shapely intersection-area / union-area of the two WKT polygons (`_polygon_iou`, townland_service.py:320) | 0.20 |

`.score` is a `@property`: if `external_id_match` is `True` it returns
`0.99` unconditionally (bypassing the weighted sum entirely); otherwise the
weighted sum above (weights sum to 1.00 across the five non-ID signals).

`.has_corroboration` is a separate `@property`, deliberately independent
of `.score`, that the docstring insists on: *"Name similarity must never
be the sole basis for a merge."* It is `True` only when at least one of:
`external_id_match`, `polygon_iou >= 0.8`, or
`(same_civil_parish AND same_barony AND area_ratio >= 0.90)`.

### 3.5 Entity resolution — three-band decision

`decide_match(features: MatchFeatures) -> str` (townland_service.py:335)
returns one of `"merge" | "review" | "reject"`, via four checked paths in
this exact order:

1. **External ID** — `external_id_match` → always `"merge"`, unconditionally.
2. **Low-score reject** — `score < MATCH_THRESHOLD_LOW (0.40)` → `"reject"`.
3. **General high-confidence path** — `score >= MATCH_THRESHOLD_HIGH (0.85)`
   **and** `has_corroboration` → `"merge"`.
4. **Explicit geometric corroboration** — `polygon_iou >= 0.8` **and**
   `jaro_winkler >= 0.80` → `"merge"` (called out in the docstring as
   necessary because IoU alone doesn't push `.score` past 0.85 without
   other signals also being high — this path exists to catch strong
   geometric matches that the weighted score formula alone would place in
   the review band).
5. **Explicit administrative corroboration** — `same_civil_parish` **and**
   `same_barony` **and** `area_ratio >= 0.90` **and**
   `jaro_winkler >= 0.90` → `"merge"` (same rationale — strong
   administrative-hierarchy + name evidence that the linear score formula
   alone might not reach 0.85 with).
6. **Default** — anything not rejected or merged falls to `"review"` — the
   ambiguous middle band that gets queued into `match_review`
   (`02_database_schema.md` §1.3) for human adjudication via
   `match_review_repository.enqueue()`.

`MATCH_THRESHOLD_HIGH = 0.85`, `MATCH_THRESHOLD_LOW = 0.40` are the two
module-level constants (townland_service.py:49–50) — everything in
`[0.40, 0.85)` that doesn't hit one of the explicit corroboration paths
lands in `"review"`.

### 3.6 Entity resolution — candidate blocking

Running `score_pair()` on every possible pair of townland records is
quadratic and wasteful when most pairs obviously don't match.
`build_candidate_blocks(records: list[dict]) -> dict[str, list[dict]]`
(townland_service.py:396) groups records by a cheap **blocking key** first
— `_block_key()` (townland_service.py:383) returns
`f"{county_prefix}:{name_prefix}"`, where `county_prefix` is the first 4
characters of the uppercased county (or `"UNKN"` if absent) and
`name_prefix` is the first 3 characters of the *normalised* name. Only
blocks with **≥ 2** records are returned — singleton blocks have no
possible candidate pair and are dropped. Pairwise scoring (`score_pair` +
`decide_match`) is then only run within each surviving block, not across
the full record set.

### 3.7 Transitive closure and canonical selection

`transitive_closure(pairs: list[tuple]) -> list[list]`
(townland_service.py:411) is a standard union-find (disjoint-set) over
`(id_a, id_b)` merge-decided pairs, with path compression in `_find()`. It
returns the connected components — clusters of IDs that were transitively
linked (if A merges with B, and B merges with C, all three end up in one
cluster even if A and C were never directly compared).

`pick_canonical(cluster_records: list[dict]) -> dict`
(townland_service.py:440) then picks **one** representative row per
cluster, by `max()` over a `(source_rank, populated_field_count)` tuple
where `source_rank` is `{"kg": 2, "json": 1}.get(source, 0)` — i.e. KG-sourced
records outrank JSON-sourced records outrank anything else, and ties are
broken by whichever record has the most non-null/non-empty/non-`[]`
fields populated.

### 3.8 Reconciliation with the townlands.ie reference

`reconcile_with_reference(townlands: list[Townland]) -> list[Townland]`
(townland_service.py:623) is the function both `townlands_ingest.py`
(§2.8) and `refresh_service.trigger_townlands_refresh()` (§5.2) call to
backfill administrative-hierarchy fields that the KG's `get_townlands()`
bulk call doesn't itself return. It loads
`backend/integrations/townlands_reference.py::load_wicklow_reference()`
(§3.9) and builds a name-indexed lookup
(`build_name_index()`, keyed by `normalize_townland_name(ref.name)`).

For each incoming `Townland`, it looks up by normalised name and, **only
where the incoming field is not already set** (`t.barony or ref.barony`,
etc. — reference data never overwrites an existing value), backfills
`barony`, `civil_parish`, `electoral_division`, and — only if
`t.name_gaelic` is falsy — `name_gaelic`. Any townland with no reference
match is appended to a `gaps` list, logged individually at `debug`
(`townland_service.reconcile_gap`), and the whole batch is written out via
`_write_reconciliation_gaps(gaps)` (§3.10) if non-empty.

If the reference itself is empty (nothing loaded — e.g. first-ever run
before `townlands_ingest.py` has produced a snapshot), the function logs a
warning and returns the input list completely unmodified — reconciliation
is a pure enhancement step, never a hard requirement.

### 3.9 `townlands_reference.py` — the reconciliation authority

A read-only integration client, **not** a live scraper. Its own docstring
states the design rationale plainly: `townlands.ie` (the reconciliation
source of truth for Wicklow barony/civil-parish/electoral-division
context) has no public JSON API, scraping on every request would be
"unreliable and rude to the host," and the canonical townland list changes
rarely — so the module only ever reads a local seed file,
`data/seed/wicklow_townlands_reference.json`, and is re-populated only by
manually re-running `townlands_ingest.py`.

`load_wicklow_reference()` returns `[]` with a `log.warning` pointing at
the ingest command if the seed file doesn't exist yet, and `[]` with a
`log.error` if the file exists but fails to parse
(`json.JSONDecodeError`/`OSError`) — both are non-fatal, degrade-to-empty
outcomes, consistent with every other "external data missing" branch in
this codebase. `build_name_index()` builds the
`normalized_name → TownlandReference` dict that `reconcile_with_reference()`
(§3.8) consumes.

### 3.10 Reconciliation gaps — two different writers of the same file

`data/source_snapshots/reconciliation_gaps.csv` is written by **two
independent code paths** that use different schemas for the same file —
worth documenting precisely since they can produce inconsistent-looking
rows if both have run at different times against the same file:

- **`townland_service._write_reconciliation_gaps()`** (townland_service.py:691),
  called from `reconcile_with_reference()` (§3.8). This is a full
  **overwrite** (`open(path, "w")`) of a single-column CSV —
  header `townland_name`, one name per line, no dedup against prior
  content (each call replaces the file's contents entirely with whatever
  gap list was just produced).
- **`scripts/build_graph.py::write_reconciliation_gaps()`** (not part of
  the ingest jobs covered elsewhere in this document — it's a separate
  offline GraphRAG-graph-building script, see `10_knowledge_graph_retrieval.md`
  or equivalent). This one **appends** (`open(path, "a")`), deduplicates
  against existing `townland_name` values already in the file, and uses a
  five-column schema: `townland_name, has_parish, has_barony, has_county,
  detected_at` — querying `SELECT name, civil_parish, barony, county FROM
  townland WHERE civil_parish IS NULL OR barony IS NULL OR county IS NULL`
  directly against the live DB rather than working from an in-memory list
  of just-reconciled `Townland` objects.

At the time of writing, the file on disk
(`data/source_snapshots/reconciliation_gaps.csv`) reflects the five-column
`build_graph.py` schema, not the one-column `townland_service.py` schema —
meaning the most recent writer of this file was the graph-build script, not
a `reconcile_with_reference()` call. Since the file is gitignored (under
`DATA_SNAPSHOT_DIR`, per CLAUDE.md's directory layout notes on
`data/source_snapshots/`), its exact contents are run-dependent and not
meant to be treated as a stable artifact — it exists purely as an operator
review queue ("which townlands are missing hierarchy fields, go look these
up manually"), not as anything the app reads back at runtime.

### 3.11 Data quality reporting and reviewer decisions

`generate_quality_report()` (townland_service.py:461) is a thin wrapper
around `match_review_repository.quality_summary()`, returning
pending/confirmed/rejected `match_review` counts, geometry-flag counts, and
same-name-different-geometry collision counts — used by an (unspecified
elsewhere in this document) data-quality dashboard. Any exception is
caught and returned as `{"error": str(exc)}` rather than propagating.

`record_reviewer_decision(match_id, decision, note="")`
(townland_service.py:481) delegates straight to
`match_review_repository.apply_decision()`. Per that function's own
behaviour (documented in `02_database_schema.md` §1.3): a `"confirmed"`
decision triggers `_link_confirmed_pair()`, which merges the two
townlands' identifiers into a shared `entity_id` and — per this module's
docstring — additionally creates a `townland_xref` entry, which has the
effect of seeding *future* ingest runs with the now-confirmed match (so a
human-reviewed merge decision persists and doesn't need to be re-derived by
the scorer on every subsequent ingest).

### 3.12 DB-first townland retrieval

`get_wicklow_townlands()` (townland_service.py:502) — the function behind
`GET /api/townlands/wicklow` — is a simple three-branch flow, structurally
the ancestor pattern that `census_service.get_census_data()` (§4) expands
on considerably: check `townland_repository.count()`; if `> 0`, check
`refresh_state_repository.get("full_ingest", stale_after_days=
ActiveConfig.TOWNLAND_STALE_AFTER_DAYS)` purely to decide the
`cache_status` value returned in the response envelope (`"hit"` vs.
`"stale_refresh"`) — **staleness here never triggers an automatic refetch**,
it only changes the reported metadata; the caller (frontend) would have to
separately call the refresh endpoint (§5). If the DB has zero townland
rows, returns an empty `data` list with `cache_status="miss"` and an
explicit `hint` field pointing at `python -m coolattin.jobs.full_ingest` —
this function **never** silently triggers a live KG call itself, unlike
`census_service`.

## 4. `census_service.py` — DB-first / KG-second decision flow

The module docstring states this file's role bluntly: *"This is the only
module that decides WHEN the KG is called"* and *"The word 'KG' appears
exactly once in this file: in the cache miss branch"* (a self-imposed
constraint the code honours — grepping the file confirms `vrti_sparql` is
imported only inside `_ingest_from_kg_or_seed`, §4.5). Routes never see
whether data came from the KG or the DB — only `meta.source` in the JSON
envelope communicates that outward.

### 4.1 `get_census_data(filters: CensusFilters) -> CensusResponse` — the main decision function

Exact flow, step by step (census_service.py:53):

1. `dataset_key = filters.dataset_key()` — derived from the filter scope
   (e.g. a request for just year 1851 and a request for all years produce
   different keys, so they're tracked as independently-stale datasets in
   `refresh_state`, per `02_database_schema.md` §1.7).
2. `records = census_repository.find(filters)` — always queries the DB
   first, unconditionally.
3. `state = refresh_state_repository.get(dataset_key,
   stale_after_days=ActiveConfig.CENSUS_STALE_AFTER_DAYS)` — `7` days in
   dev, `1` day in prod (`01_architecture_overview.md` §4.2).
4. **DB hit + fresh** (`records` non-empty **and** (`state is None` **or**
   `not state.is_stale`)) → serve immediately, `cache_status="hit"`,
   `source="database"`. The `state is None` branch is deliberately
   permissive: the inline comment explains that some environments may have
   valid persisted rows even though `refresh_state` was never backfilled
   for that exact dataset key — treating "no refresh-state row at all" as
   equivalent to "fresh" avoids unnecessarily falling through to a live KG
   call just because the bookkeeping table wasn't populated for that
   specific filter combination.
5. **DB hit + stale** (`records` non-empty **and** `state` present **and**
   `state.is_stale`) → serve the existing (stale) rows immediately,
   `cache_status="stale_refresh"`, and call
   `_schedule_background_refresh(filters)` (§4.6) — which is presently a
   **no-op stub** that only logs. The user-facing behaviour today is
   therefore: stale data is served with a `stale_refresh` status flag, but
   nothing actually refreshes it in the background until an operator wires
   the stub to a real task queue.
6. **DB miss** (`records` empty) → `_ingest_from_kg_or_seed(filters)`
   (§4.5) is called — this is the one and only branch that talks to the
   KG. Two possible outcomes:
   - Still empty after the KG attempt → return `data=[]`,
     `cache_status="miss"`, `source` set to whatever
     `_ingest_from_kg_or_seed` reported (`"kg_error"` or `"kg_empty"`).
   - Records returned → persist via `census_repository.upsert_many()`,
     attempt an Excel export (`export_service.export_census`, failure
     logged as `warning` and `export_path` set to `None`, never fatal),
     update `refresh_state` for `dataset_key`, then **re-read** from the DB
     (`cr.find(filters)`) rather than returning the just-fetched KG DTOs
     directly — ensuring the response always reflects exactly what's
     persisted (e.g. picking up any `INSERT OR REPLACE` merge behaviour
     from `upsert_many`). `cache_status="miss"` even on this success path
     (the word describes the *initial* cache state, not the outcome).

### 4.2 `get_census_summary()` and `get_available_townlands()`

Both call `_ensure_census_seeded()` (§4.4) first — a check-only,
non-fetching guard — then read exclusively from the DB
(`census_repository.get_summary()` / `get_townland_names()`). Neither ever
calls the KG; both always report `source="database"`,
`cache_status="hit"`. The module docstring's summary table calls this out:
"Always served from local DB — no KG call for aggregates."

### 4.3 `get_townland_detail(townland_name)` — per-townland KG live-enrich

Structurally different from `get_census_data()`: it does **not** follow
the staleness-gated DB-first/KG-second pattern. Instead:

1. `_ensure_census_seeded()` guard.
2. `census_repository.find_townland_detail(townland_name)` — if `None`
   (no census rows for this townland at all), a minimal stub dict is built
   in its place rather than returning early, specifically so KG enrichment
   can still populate it — the comment names concrete examples:
   `BALLINGLEN`, `MOTABOWER` (absent from the CSV entirely), `LYBAGH`
   (present but all-null rows).
3. **Every call unconditionally attempts a live KG lookup** —
   `vrti_sparql.get_townland_details_by_name(kg_name, county="Wicklow")`
   — wrapped in `try/except Exception` (any failure logged at `debug` and
   silently ignored, `kg_found` stays `False`). There is no staleness
   check gating this call; `get_townland_detail` always tries the KG live,
   every time it's invoked. A code comment explains a specific casing
   nuance: the townland name is passed through **as-is**, not
   `.title()`-cased, because `vrti_sparql` does `LCASE()` SPARQL-side
   matching, and `.title()`-casing an ALL-CAPS GeoJSON name with lowercase
   connector words (e.g. `"COOLBAWN or COOLBALLINTAGGART"`) would mangle
   it into `"Coolbawn Or Coolballintaggart"` and fail to match.
4. On a KG hit: `townland_repository.save_kg_cache(townland_name, kg_dto)`
   persists the KG fields onto the `townland` row for offline caching, and
   the in-memory `detail` dict is overlaid with `uri`, `kg_uri`, `county`,
   `centroid_lat/lon`, `boundary_wkt`, `images`, `links`, `osm_id`,
   `osi_id`, `vrti_id`, `kg_civil_parish`, `kg_barony`, and conditionally
   `gaelic_name` — always treated as authoritative when present.
5. **DB-cache fallback for `kg_found`**: even if the live KG call fails or
   misses, `kg_found` is retroactively set `True` if the `detail` dict
   already carries any of `kg_uri`, `centroid_lat`, `county`, or `barony` —
   i.e. fields that could only have gotten there via a *previous* call's
   `save_kg_cache()` write. This lets the function report "found" (and
   thus `source="kg_enriched"`) using stale-but-persisted KG data even when
   the live endpoint is currently unreachable.
6. Final response is empty (`data=[]`, `cache_status="miss"`) **only** if
   there are no census rows **and** no KG data (live or cached) at all —
   otherwise `source` is `"kg_enriched"` if `kg_found` else `"database"`,
   always `cache_status="hit"`, `record_count=1`.

### 4.4 `_ensure_census_seeded()` — a check, not a seeder

Despite the name, this function **never seeds anything**. It only checks
`census_repository.count_records() == 0` and, if so, logs a `warning`
pointing at `python -m coolattin.jobs.full_ingest` — consistent with the
"never silently seed" philosophy stated explicitly elsewhere in this
codebase (`townland_service.get_wicklow_townlands()`, §3.12, uses the same
pattern).

### 4.5 `_ingest_from_kg_or_seed()` — the one KG call site

```python
kg_dtos = vrti_sparql.get_census_records_for_county(
    county=None,   # deliberately unrestricted
    year=filters.year,
)
```

The `county=None` choice is explained in an inline comment: some Wicklow
townlands lack a complete Townland→Parish→Barony→County hierarchy chain in
the KG itself, so a strict SPARQL-side county filter would silently drop
otherwise-valid records; restricting to estate-relevant townlands is
instead left to the **frontend** (`census.js`, via a `validTownlandNames`
filter built from `townlands.json`) rather than being enforced at ingest
time here. This is a different filtering strategy from `full_ingest.py`'s
Step 4 (§2.5), which *does* filter server-side against `estate_name_map`
before persisting — i.e. `census_service`'s live KG path can end up
persisting non-estate Wicklow townland census rows into the DB, whereas
`full_ingest.py`'s path cannot.

Three outcomes:
- **Exception** (network error, malformed SPARQL response, etc.) → caught,
  logged at `warning` (`census_service.kg_unreachable`), returns
  `([], "kg_error")`.
- **Empty result** (endpoint fine, zero rows) → logged at `warning`
  pointing at `full_ingest`, returns `([], "kg_empty")`. Note: unlike
  `full_ingest.py`'s and `census_ingest.py`'s own KG-empty branches, this
  function does **not** fall back to the bundled CSV seed
  (`census_seed.py`) — a KG miss here simply produces zero records, and
  the caller (`get_census_data`) reports `cache_status="miss"`,
  `source="kg_empty"` back to the API consumer with nothing persisted.
- **Success** → each DTO becomes a `CensusRecord` via `canonical_name()`
  (alias-resolved) on the townland name, `source="kg"`,
  `kg_uri=dto.townland_uri`, `total` computed conditionally as in the
  other ingest paths. Returns `(records, "kg_refresh")`.

### 4.6 `_schedule_background_refresh()` — an intentional stub

```python
def _schedule_background_refresh(filters: CensusFilters) -> None:
    """
    REVIEWER NOTE: This is intentionally a stub.
    To activate background refreshes, implement one of:
      - Flask-APScheduler: add a scheduled job calling force_refresh()
      - Celery task: enqueue census_ingest.run_census_ingest()
      - Simple threading: threading.Thread(target=force_refresh, daemon=True).start()
    """
    log.info("census_service.refresh_scheduled | key=%s (stub — wire to task queue to activate)", ...)
```

Only ever called from the "DB hit + stale" branch of `get_census_data()`
(§4.1 step 5). Its docstring is explicit that the stale-serving path is
still correct/non-blocking behaviour on its own — the stub simply means
staleness is currently surfaced to the caller via `cache_status` rather
than being silently and automatically corrected.

### 4.7 `force_refresh(filters=None) -> dict`

The function `refresh_service.trigger_census_refresh()` (§5.1) delegates
to. Ignores TTL/staleness entirely — always calls
`_ingest_from_kg_or_seed(filters, force=True)` (the `force` flag is
accepted by the signature but not actually read inside the function body;
the KG call happens unconditionally regardless of that flag's value since
there is no staleness check in this code path to begin with — `force=True`
exists purely as a documentation/intent marker at the call site). Persists,
exports, updates `refresh_state`, and returns a status dict:
`{"status": "refreshed", "record_count": N, "source": ..., "export_file": ...}`.

## 5. `refresh_service.py` — forced-refresh orchestration

This module is the thin orchestration layer sitting directly behind the
two admin-gated POST refresh routes. Both entry points return **status
dicts** consumed directly by their route handlers as the JSON response
body, with HTTP `202 Accepted` (not `200`) — signalling "refresh has been
performed synchronously but is being reported as an accepted
background-style operation."

### 5.1 `trigger_census_refresh(year=None)`

Called from `POST /api/census/refresh` (`backend/routes/census.py`, gated
by `@_require_admin`, §5.3). One line of real logic: builds a
`CensusFilters(year=year)` and delegates straight to
`census_service.force_refresh(filters)` (§4.7).

### 5.2 `trigger_townlands_refresh()`

Called from `POST /api/townlands/refresh` and its backward-compatible
alias `POST /api/townlands/wicklow/refresh` (`backend/routes/townlands.py`
— **not** wrapped in `@_require_admin`, unlike the census refresh route;
see §5.3). Its own inline implementation, independent of both
`townlands_ingest.py` and `census_service.py`:

1. `vrti_sparql.get_townlands(county=None, limit=5000)` — same
   unrestricted-county, 5000-row-limit call `townlands_ingest.py` makes. No
   endpoint probe first (unlike `townlands_ingest.py`'s Step 1) — a KG
   failure here would surface as an exception from `get_townlands()`
   itself rather than a pre-emptive `probe_endpoint()` check.
2. Empty result → `log.warning("refresh_service.townlands_kg_empty")`,
   returns `{"status": "no_data", "record_count": 0, "source": "kg"}`
   (**no** `202`-implying "refreshed" status — the route still returns
   `202` regardless, since the route layer doesn't branch on the dict's
   `status` field).
3. Each DTO → minimal `Townland(name=normalize_townland_name(dto.name),
   name_gaelic=..., kg_uri=dto.uri, wkt_geometry=dto.wkt_geometry,
   source="kg")` — identical construction to `townlands_ingest.py`'s Step
   2 (§2.8).
4. `reconcile_with_reference(townlands)` (§3.8) — backfills
   barony/parish/electoral-division from the townlands.ie snapshot, same
   as the standalone job.
5. `townland_repository.upsert_many(townlands)`.
6. `export_service.export_townlands(townlands)`, wrapped in
   `try/except`, non-fatal on failure.
7. `refresh_state_repository.upsert("wicklow_townlands", source="kg_refresh",
   record_count=count, export_file=export_path)`.
8. Returns `{"status": "refreshed", "record_count": count,
   "source": "kg_refresh", "export_file": export_path}`.

This is functionally near-identical to `townlands_ingest.py` minus the
endpoint probe (step 1 there) and minus the reference-snapshot-writing step
(step 5 there, §2.8) — `refresh_service` never regenerates
`wicklow_townlands_reference.json`, it only *reads* the existing snapshot
via `reconcile_with_reference()`. This means the HTTP refresh path depends
on `townlands_ingest.py` having been run at least once beforehand for
reconciliation to have any reference data to enrich against; if it never
has been, `reconcile_with_reference()` degrades gracefully (§3.8) and the
refresh still succeeds, just without barony/parish backfill.

### 5.3 Admin gating asymmetry

`backend/routes/census.py::_require_admin` (census.py:29) wraps
`POST /api/census/refresh` and `POST /api/census/export/regenerate`: it
checks `ActiveConfig.ADMIN_API_KEY` is non-empty (if unset, the endpoint
returns `403` unconditionally — *"Admin operations are disabled"* — never
reachable on a deployment that hasn't explicitly configured a key), then
compares an `X-Admin-Key` header or `admin_key` query param against it,
returning `403 Forbidden` on any mismatch or absence.

`backend/routes/townlands.py::refresh_townlands` (townlands.py:47) carries
**no** equivalent decorator — `POST /api/townlands/refresh` and
`/api/townlands/wicklow/refresh` are reachable by any caller with no admin
key requirement at all. This is a real asymmetry between the two refresh
surfaces in the current codebase, not a documented intentional design
choice — worth flagging for anyone hardening the admin surface later.

## 6. Cross-cutting conventions observed across this subsystem

- **Logging style**: every log call in this subsystem follows the
  `module.event_name | key=value key2=value2` convention (e.g.
  `full_ingest.kg_offline`, `census_service.cache_hit | key=%s count=%d`,
  `townland_service.alias_map_loaded | entries=%d`) — `log =
  logging.getLogger(__name__)` per file, never `print()`, matching
  CLAUDE.md's stated code convention. `log.error` is reserved for
  conditions that abort the current operation (missing GeoJSON, KG
  unreachable in `census_ingest.py`); `log.warning` for degraded-but-continuing
  paths (KG offline in `full_ingest.py`, KG empty, export failures,
  reconciliation reference missing); `log.info` for normal
  progress/completion milestones; `log.debug` for per-row detail that would
  be too noisy at `info` (individual KG lookup misses, individual
  reconciliation gaps).
- **Idempotency**: every persistence call in this subsystem is an
  upsert (`townland_repository.upsert`/`upsert_many`,
  `census_repository.upsert_many`, `clearances_repository.upsert_many`),
  keyed on `UNIQUE` constraints documented in `02_database_schema.md`
  (`townland_xref(source, source_record_id)`,
  `census_record(townland_id, year)`,
  `clearances_record(townland_id, year)`,
  `field_provenance(entity_id, field_name)`) — re-running any of the three
  ingest jobs, or hitting either refresh endpoint, any number of times
  never produces duplicate rows, only updates.
- **Timeouts**: no ingest-job-level HTTP timeout logic exists in the files
  covered by this document — timeout behaviour for SPARQL calls
  (`VRTI_REQUEST_TIMEOUT = 30` seconds, per `01_architecture_overview.md`
  §4.2) lives inside `backend/integrations/vrti_sparql.py`, outside this
  document's scope. Every KG call site in the jobs/services covered here
  treats a raised exception from the SPARQL client uniformly — caught,
  logged, degraded to an empty-result / offline branch, never left to
  propagate to the CLI or HTTP layer as an unhandled 500.
- **No retry logic**: none of `full_ingest.py`, `census_ingest.py`,
  `townlands_ingest.py`, `census_service.py`, or `refresh_service.py`
  retries a failed KG call. A single failure (exception or empty result)
  is immediately treated as final for that invocation — the retry
  strategy, such as it is, is "the operator re-runs the job/endpoint
  later."
