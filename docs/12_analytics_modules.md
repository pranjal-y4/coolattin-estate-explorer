# 12 — Analytics Modules

Technical reference for the `/analytics` dashboard system: the `AnalyticsModule`
contract, the auto-discovery registry, and the six dataset modules that
currently exist in `analytics/`. Verified against the code as of this
checkout (all line/behaviour claims below were confirmed by reading the
source and, where noted, by executing `discover_modules()` +
`module.compute()` directly).

## 1. Package contents

```
analytics/
├── __init__.py       # re-exports discover_modules, list_datasets, safe_compute
├── base.py            # KPI / Chart / AnalyticsResult dataclasses + AnalyticsModule Protocol
├── registry.py         # discover_modules(), list_datasets()
├── utils.py            # safe_compute() — the ONLY helper in this file
├── emigrations.py      # dataset_id="emigration"
├── evictions.py        # dataset_id="evictions"
├── tenancies.py        # dataset_id="tenancies"
├── townland_geo.py     # dataset_id="townlands_geo"
├── unified.py           # dataset_id="unified"
└── workhouse.py         # 0 bytes — empty file, no MODULE object, NOT registered
```

Six `.py` files look like dataset modules, but only **five** are actually
picked up by the registry at runtime — `workhouse.py` is a completely empty
file (confirmed with `wc -c` → 0 bytes), so it defines no `MODULE` and is
silently skipped by `discover_modules()` (see §3). There is currently no
workhouse-linkage analytics module despite `workhouse_unified_links` /
`entity_resolution_*` tables existing in the schema.

## 2. `analytics/base.py` — the module contract

```python
@dataclass
class KPI:
    label: str
    value: str
    hint: str = ""

@dataclass
class Chart:
    chart_id: str
    title: str
    type: str  # "bar" | "line" | "doughnut"
    data: Dict[str, Any]
    options: Dict[str, Any] | None = None

@dataclass
class AnalyticsResult:
    dataset_id: str
    dataset_name: str
    description: str
    kpis: List[KPI]
    charts: List[Chart]
    notes: List[str]

class AnalyticsModule(Protocol):
    dataset_id: str
    dataset_name: str
    description: str
    def compute(self) -> AnalyticsResult: ...
```

- **`KPI`** — a single stat tile. `value` is always a pre-formatted `str`
  (comma-separated thousands, percentages baked in as text, etc.) — there is
  no numeric type on the wire, formatting is the module author's
  responsibility. `hint` is optional sub-text under the tile, defaults to
  `""`.
- **`Chart`** — `type` is a free-text string but by convention (and by what
  `frontend/templates/analytics.html` + Chart.js actually support) it is one
  of `"bar"`, `"line"`, `"doughnut"`. `data` and `options` are **verbatim
  Chart.js constructor arguments** — see §7 for why this matters to the
  frontend.
- **`AnalyticsResult`** — the full payload for one dataset: KPI tiles +
  charts + free-text `notes` (rendered as a bullet list, used by every
  module to report which CSV columns were auto-detected, or to explain
  why a particular chart was skipped).
- **`AnalyticsModule`** — a `typing.Protocol`, **not** an `abc.ABC` and not
  decorated `@runtime_checkable`. This means:
  - It is a structural-typing contract for static type checkers only.
  - `isinstance(x, AnalyticsModule)` would raise at runtime if attempted
    (no `__instancehook__`) — and indeed nothing in this codebase attempts
    it. `registry.py` enforces the contract with plain `hasattr()` checks
    instead (§3), so a class only needs `dataset_id`, `dataset_name` are
    *not* actually checked individually — only `dataset_id` and `compute`
    are checked; `dataset_name`/`description` are read with `getattr(...,
    default)` wherever consumed (e.g. `utils.safe_compute`), so a module
    missing them will not error at discovery time, only degrade gracefully
    at render time.
- To be a valid module: a file in `analytics/` must expose a module-level
  object named exactly `MODULE` that has a `dataset_id` attribute and a
  zero-argument `compute()` method returning an `AnalyticsResult`. Every
  existing module satisfies this by instantiating its class at the bottom
  of the file, e.g. `MODULE = EmigrationAnalytics()`.

## 3. `analytics/registry.py` — discovery mechanism

**This is filesystem glob + `importlib`-based auto-discovery, not a
decorator registry and not an explicit hand-maintained list.** It matches
the CLAUDE.md claim ("add a new analytics module by creating a class... and
registering it") loosely — no explicit registration call is required beyond
naming the sentinel `MODULE`, so in practice adding a new file *is*
sufficient, contradicting the word "registering" taken literally.

```python
def discover_modules() -> Dict[str, AnalyticsModule]:
    modules = {}
    analytics_dir = Path(__file__).parent
    package_name = __package__  # comment says "coolattin.analytics" — STALE, see below

    for py_file in analytics_dir.glob("*.py"):
        name = py_file.stem
        if name in {"__init__", "base", "registry", "utils"}:
            continue
        mod = importlib.import_module(f"{package_name}.{name}")
        if not hasattr(mod, "MODULE"):
            continue
        module_obj = getattr(mod, "MODULE")
        if not hasattr(module_obj, "dataset_id") or not hasattr(module_obj, "compute"):
            continue
        did = module_obj.dataset_id
        if did in modules:
            raise RuntimeError(f"Duplicate dataset_id='{did}' detected. ...")
        modules[did] = module_obj
    return modules
```

Exact mechanics:

1. **Discovery scope**: `Path(__file__).parent.glob("*.py")` — every `.py`
   file directly inside `analytics/` (non-recursive; no subpackages are
   scanned).
2. **Denylist, not allowlist**: any file whose stem is *not* in
   `{"__init__", "base", "registry", "utils"}` is imported and probed. This
   means **any new file dropped into `analytics/` is automatically
   imported on the next call to `discover_modules()`** — genuine
   auto-discovery, confirmed live: `workhouse.py` (0 bytes) is imported
   every call (it doesn't error, an empty file is a valid empty module) but
   contributes nothing because it has no `MODULE` attribute.
3. **Import** is via `importlib.import_module(f"{package_name}.{name}")`
   where `package_name = __package__`. The inline comment claims this
   resolves to `"coolattin.analytics"` — **verified stale**: at runtime
   `analytics.registry.__package__` is `"analytics"` (the package really is
   a top-level `analytics/` directory next to `backend/`, not nested under
   a `coolattin` package). The code still works because the comment is just
   wrong, not the logic.
4. **Sentinel contract check**: `hasattr(mod, "MODULE")` — if a file has no
   module-level `MODULE` object it is silently skipped (no error, no log).
   This is how `workhouse.py` (empty) and any future non-dataset utility
   file quietly opts out.
5. **Duck-typed contract check**: `hasattr(module_obj, "dataset_id")` and
   `hasattr(module_obj, "compute")` — only these two attributes are
   verified; `dataset_name`/`description` are not checked at discovery
   time.
6. **Uniqueness**: `dataset_id` collisions across files raise
   `RuntimeError` at discovery time (fails the whole `/analytics` page,
   caught by the route's `try/except`, see §5) — the error message names
   both colliding module classes.
7. **`list_datasets(modules)`** — takes the dict returned by
   `discover_modules()` and returns `[(dataset_id, dataset_name), ...]`
   sorted case-insensitively by `dataset_name`. This is only used for
   building the dataset-picker pill list; the underlying dict from
   `discover_modules()` is unordered (dict insertion order = glob
   iteration order, which is filesystem-dependent and not sorted).

**Live discovery result on this checkout** (`discover_modules().keys()`):

```
['unified', 'evictions', 'tenancies', 'emigration', 'townlands_geo']
```

Five modules register successfully at import time (their classes and
`MODULE` singletons are well-formed); `workhouse` never appears in this
list. Whether each one's `compute()` actually *succeeds* is a separate
question — see §8, none currently do.

## 4. `analytics/utils.py` — shared helpers

This file contains **exactly one function**, `safe_compute`:

```python
def safe_compute(module: AnalyticsModule) -> Tuple[Optional[AnalyticsResult], Optional[str]]:
    try:
        return module.compute(), None
    except Exception:
        err = traceback.format_exc()
        placeholder = AnalyticsResult(
            dataset_id=getattr(module, "dataset_id", "unknown"),
            dataset_name=getattr(module, "dataset_name", "Unknown Dataset"),
            description=getattr(module, "description", ""),
            kpis=[KPI("Status", "Error", "This dataset analytics failed to compute")],
            charts=[],
            notes=["Fix the dataset module or CSV schema. See traceback below."],
        )
        return placeholder, err
```

It wraps `module.compute()` in a try/except, returning a placeholder
`AnalyticsResult` (a single "Status: Error" KPI, no charts) plus the full
traceback string on failure, so a broken dataset module degrades to an
error card instead of crashing the whole `/analytics` page.

**This function is exported from `analytics/__init__.py`
(`__all__ = ["discover_modules", "list_datasets", "safe_compute"]`) but is
never imported or called anywhere in `backend/`** (`grep -rn
"safe_compute" --include="*.py"` across the repo only finds its own
definition and the `__init__.py` re-export). `backend/routes/main.py`'s
`/analytics` route (§5) reimplements the same try/except pattern inline
instead of calling `safe_compute`, with a materially different degraded
result: it sets `result=None` and passes a separate `error=str(e)` string
to the template, rather than `utils.safe_compute`'s placeholder
`AnalyticsResult` with an "Error" KPI. **Net effect: `safe_compute` is dead
code** — a plausible target for future consolidation, but currently
unused.

**There are no other shared helpers.** In particular, there is no shared
column-detection or SQL utility in `utils.py`. The `_pick_col(df,
candidates)` fuzzy-column-matching function is instead **copy-pasted
verbatim** into three separate files (`emigrations.py`, `evictions.py`,
`tenancies.py`) — same 10-line implementation in each, not imported from a
common location. Likewise `find_data_file(filename)` is duplicated verbatim
between `tenancies.py` and `unified.py`. This contradicts an assumption
that "shared helpers" for formatting/SQL/date-bucketing live centrally —
they don't; each dataset module is self-contained and duplicates its own
copy of these utilities.

## 5. Route integration — `backend/routes/main.py`

```python
@bp.get("/analytics")
def analytics():
    from analytics.registry import discover_modules

    dataset_id = request.args.get("d", "")
    modules: dict = {}
    try:
        modules = discover_modules()
    except Exception as e:
        log.warning("analytics discover_modules failed: %s", e)

    module_list = list(modules.values())
    current = modules.get(dataset_id) if dataset_id else None
    if current is None and module_list:
        current = module_list[0]          # default: first module in glob order

    result = None
    error = None
    if current:
        try:
            result = current.compute()
        except Exception as e:
            log.exception("analytics compute failed for %s", current.dataset_id)
            error = str(e)

    datasets = [(m.dataset_id, m.dataset_name) for m in module_list]
    current_dataset_id = current.dataset_id if current else None

    return render_template(
        "analytics.html", title="Analytics",
        datasets=datasets, current_dataset_id=current_dataset_id,
        result=result, error=error,
    )
```

Key points:

- **One dataset rendered per request.** The `?d=<dataset_id>` query string
  parameter picks which module's `compute()` runs; only that module's
  `AnalyticsResult` is computed and sent to the template — the other four
  modules are *not* computed on this request (they only get computed when
  their pill is clicked, generating a fresh page load with a different
  `d=`).
- **Default dataset** when `d` is missing/unknown: `module_list[0]`, i.e.
  whatever module happens to be first in `dict(modules).values())` —
  itself the glob-then-import order from `discover_modules()`, which is
  filesystem-dependent (currently `unified`, per the live discovery order
  in §3), **not** the alphabetically-sorted order used by `list_datasets`
  for the picker UI. So the default landing dataset and the first pill in
  the list are not guaranteed to be the same one (they happen to differ
  here: alphabetically "Emigration" sorts first for the picker, but
  `unified` is what actually renders by default).
- **Error handling is duplicated, not delegated**: both `discover_modules()`
  failures (e.g. a duplicate `dataset_id` `RuntimeError`) and
  `current.compute()` failures are caught locally with their own
  `try/except`, not via `analytics.utils.safe_compute` (§4). A
  `discover_modules()` failure results in an empty dataset picker and no
  `result`/`error` shown at all (only a server-side `log.warning`); a
  `compute()` failure sets template variable `error` to `str(e)` (message
  only, not the full traceback — contrast with `safe_compute`, which would
  have surfaced the full traceback via `notes`/placeholder, but that path
  is unused).
- **Payload assembled for the template**: `datasets` (all `(id, name)`
  pairs for the pill bar), `current_dataset_id` (highlights the active
  pill), `result` (the `AnalyticsResult` or `None`), `error` (string or
  `None`).

## 6. Frontend rendering — `analytics.html` does the Chart.js work, not `analytics.js`

`frontend/static/js/analytics.js` is a 13-line file:

```js
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("canvas[id]").forEach(canvas => {
    const configEl = document.getElementById(canvas.id + "-config");
    if (!configEl) return;
    try {
      const config = JSON.parse(configEl.textContent);
      new Chart(canvas, config);
    } catch (e) {
      console.warn("Chart init failed for", canvas.id, e);
    }
  });
});
```

**Finding relevant to the frontend documentation agent**: all of the actual
"which chart type, which data, which options" decision-making happens
server-side in Python (§1's dataset modules construct `Chart.type` /
`Chart.data` / `Chart.options` as literal Chart.js config shapes already).
`frontend/templates/analytics.html` embeds that config verbatim as JSON
inside a `<script type="application/json" id="{{ c.chart_id }}-config">`
block per chart:

```html
<canvas id="{{ c.chart_id }}" height="140"></canvas>
<script type="application/json" id="{{ c.chart_id }}-config">
  { "type": "{{ c.type }}", "data": {{ c.data | tojson }}, "options": {{ (c.options or {}) | tojson }} }
</script>
```

`analytics.js` does nothing but find every `<canvas id>` on the page,
parse its sibling `-config` JSON blob, and hand it straight to
`new Chart(canvas, config)` (global `Chart` from the Chart.js UMD bundle
loaded via CDN `<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/...">`
in `analytics.html`, not bundled locally). There is no chart-shaping,
aggregation, or presentation logic in JS at all — it is a pure
config-passthrough. The template also renders the KPI grid
(`result.kpis`), the dataset picker pills (`datasets` + `current_dataset_id`
building `<a>` links with `url_for('main.analytics', d=did)`), the notes
list, and the error panel — all server-rendered Jinja2, no client-side
fetch/AJAX involved in populating the page.

## 7. Performance / caching

**No caching anywhere in this subsystem.** Every `GET /analytics?d=...`
request:

1. Re-runs `discover_modules()` — re-globs `analytics/*.py` and calls
   `importlib.import_module()` for every non-excluded file. Because Python
   caches imported modules in `sys.modules`, files already imported in this
   process are not re-executed from disk (this is standard Python import
   caching, not anything this codebase built) — but the glob, the
   `hasattr` probing, and the dict rebuild happen fresh every request
   regardless.
2. Calls `current.compute()` fresh — which for every existing module means
   re-reading and re-parsing the entire source CSV/JSON file from disk
   with `pandas.read_csv` / `json.loads` and recomputing every KPI/chart
   from scratch, on every page view. There is no `functools.lru_cache`, no
   in-memory result cache, no ETag/last-modified check, nothing analogous
   to `refresh_state` (used elsewhere in the app for census/clearances
   freshness tracking) applied to analytics.

## 8. IMPORTANT — none of the five registered modules currently compute successfully

Executed directly against this checkout:

```
$ python3 -c "from analytics.registry import discover_modules; ..."
unified        -> FAILED: FileNotFoundError Missing file: .../Coolattin-app/data/unified_processed.csv
evictions      -> FAILED: FileNotFoundError Missing file: .../Dissertation/data/evictions_records.csv
tenancies      -> FAILED: FileNotFoundError Missing file: .../Coolattin-app/data/tenancies.csv
emigration     -> FAILED: FileNotFoundError Missing file: .../Dissertation/data/emigrations_records.csv
townlands_geo  -> FAILED: FileNotFoundError Missing file: .../Dissertation/data/townlands.json
```

Root cause is a `Path.parents[N]` off-by-one in every module's `DATA_PATH`
construction:

- `emigrations.py`, `evictions.py`, `townland_geo.py` compute `DATA_PATH =
  Path(__file__).resolve().parents[2] / "data" / "<file>"`. For a file at
  `analytics/<name>.py`, `parents[0]` is `analytics/`, `parents[1]` is the
  `Coolattin-app/` repo root, and `parents[2]` is **one directory above the
  repo root** (`.../Dissertation/`, outside the git repository entirely).
  These three modules therefore look for their CSV/JSON in a `data/`
  sibling of the whole `Coolattin-app` checkout, which does not exist.
- `tenancies.py` and `unified.py` use a slightly more defensive
  `find_data_file()` that tries four candidates (`project_root/data/`,
  `package_root/data/`, `project_root/`, `package_root/`) where
  `project_root = parents[2]` (same over-shoot as above) and `package_root
  = parents[1]` (the actual repo root, `Coolattin-app/`). None of the four
  candidates match where the real seed data actually lives —
  `frontend/static/data/unified_processed.csv` /
  `frontend/static/data/townlands.json` per `CLAUDE.md`'s documented
  `frontend/static/data/` layout — so both still fall through to
  `FileNotFoundError`.
- The repo's actual `data/` directory (`Coolattin-app/data/`) contains only
  `coolattin.db`, `coolattin.shacl.ttl`, `coolattin_sample.ttl`,
  `entity_index.pkl`, `kg_context.yaml`, `seed/`, `source_snapshots/` — no
  `tenancies.csv`, `unified_processed.csv`, `emigrations_records.csv`,
  `evictions_records.csv`, or `townlands.json` exist there, confirming
  these modules are reading from a CSV-file-based dataset that predates
  (or was never migrated to) the current SQLite-backed
  (`unified_record`/`census_record`/`clearances_record`) data model
  described elsewhere in this codebase.

**Practical consequence**: visiting `/analytics` on this checkout renders a
KPI-less, chart-less "Dataset Error" panel (per the route's `error =
str(e)` path, §5) for every dataset in the picker. The analytics dashboard
is present in the codebase and structurally wired end-to-end (registry →
route → template → Chart.js), but is not currently functional against this
repository's actual data files. This is a data-plumbing bug (stale
`DATA_PATH` resolution logic pointing at a legacy/never-populated `data/`
CSV convention), not a design flaw in the plugin architecture itself.

## 9. Per-module reference

Every module shares the same `_pick_col(df, candidates)` pattern (except
`townland_geo.py`, which parses GeoJSON, not tabular data): it does a
case-insensitive exact match against `candidates` first, then falls back to
a case-insensitive substring match, returning the first matching real
column name in the DataFrame, or `None`. All numeric aggregation columns
default to a synthetic `_count` column of all `1`s when no explicit count
column is found (`df["_count"] = 1`), i.e. the module falls back to
row-counting.

### 9.1 `EmigrationAnalytics` (`analytics/emigrations.py`, `dataset_id="emigration"`)

- **Source**: `<parents[2]>/data/emigrations_records.csv` (currently
  missing — see §8). `dataset_name="Emigration"`.
- **Column detection**: `year_col` from `["year","date","departure_year","emigration_year"]`;
  `dest_col` from `["destination","destination_country","country","to"]`;
  `count_col` from `["count","records","num","n"]` (else synthetic `_count`).

| Item | Type | Computation | Output shape |
|---|---|---|---|
| Total Records | KPI | `int(pd.to_numeric(df[count_col], errors="coerce").fillna(0).sum())`, formatted `f"{total:,}"` | string, e.g. `"1,234"` |
| Detected Year Column | KPI | Literal name of `year_col` or `"None"` | string |
| Detected Destination Column | KPI | Literal name of `dest_col` or `"None"` | string |
| Detected Count Column | KPI | Literal name of `count_col` (always populated, real or synthetic `_count`) | string |
| Emigration Over Time (`chart_id="emigYear"`) | Chart, `line` | Only if `year_col` found: coerce to numeric year, `groupby("_year")[count_col].sum()`, sorted ascending, **tail(35)** if more than 35 distinct years | `labels`=year strings, one dataset `"Emigration"` (float values), `tension:0.25`, `options.scales.y.beginAtZero=True` |
| Top Destinations (`chart_id="emigDest"`) | Chart, `bar` | Only if `dest_col` found: `groupby(dest_col)[count_col].sum().sort_values(ascending=False).head(10)` | `labels`=destination names, one dataset `"Records"`, legend hidden, y beginAtZero |

- **Notes emitted**: missing-year-column and missing-destination-column
  warnings (conditionally), plus an always-present note pointing at
  `/data/emigrations_records.csv` as the claimed source.

### 9.2 `EvictionsAnalytics` (`analytics/evictions.py`, `dataset_id="evictions"`)

- **Source**: `<parents[2]>/data/evictions_records.csv` (missing — §8).
  `dataset_name="Evictions"`.
- **Column detection**: `townland_col` from `["townland","town","place","location"]`;
  `year_col` from `["year","date","eviction_year"]`; `count_col` from
  `["count","records","num","n"]` (else synthetic `_count`).

| Item | Type | Computation | Output shape |
|---|---|---|---|
| Total Records | KPI | Same pattern as emigrations: numeric-coerced sum of `count_col`, comma-formatted | string |
| Detected Townland Column | KPI | Literal name or `"None"` | string |
| Detected Year Column | KPI | Literal name or `"None"` | string |
| Detected Count Column | KPI | Literal name | string |
| Evictions by Decade (`chart_id="evictDecade"`) | Chart, `line` | Only if `year_col` found: numeric year → **`_decade = (year // 10) * 10`** → `groupby("_decade")[count_col].sum()` sorted ascending | `labels`=`"{decade}s"` (e.g. `"1840s"`), one dataset `"Evictions"`, `tension:0.25` |
| Top Townlands (Evictions) (`chart_id="evictTownlands"`) | Chart, `bar` | Only if `townland_col` found: `groupby(townland_col)[count_col].sum().sort_values(ascending=False).head(10)` | `labels`=townland names, one dataset `"Evictions"`, legend hidden |

- Unlike emigrations, this module buckets by **decade**, not raw year — the
  only module in the set that does decade bucketing.
- **Notes emitted**: conditional missing-column warnings only (no
  always-present source-path note, unlike emigrations/tenancies/unified).

### 9.3 `TenanciesAnalytics` (`analytics/tenancies.py`, `dataset_id="tenancies"`)

- **Source**: `find_data_file("tenancies.csv")` — 4-candidate fallback (see
  §8), currently unresolved, falls back to
  `<Coolattin-app>/data/tenancies.csv` (missing). `dataset_name="Tenancies"`.
- **Column detection**: `townland_col` from `["townland","town","place","location"]`;
  `year_col` from `["year","date","start_year","from","lease_year"]`;
  `surname_col` from `["surname","last_name","family","tenant_surname","name"]`;
  `count_col` from `["count","records","num","n"]` (else synthetic `_count`).

| Item | Type | Computation | Output shape |
|---|---|---|---|
| Total Tenancy Records | KPI | Numeric-coerced sum of `count_col`, comma-formatted | string |
| Detected Townland Column | KPI | Literal or `"None"` | string |
| Detected Year Column | KPI | Literal or `"None"` | string |
| Detected Surname Column | KPI | Literal or `"None"` | string |
| Top Townlands (Tenancies) (`chart_id="tenTopTownlands"`) | Chart, `bar` | Only if `townland_col`: `groupby(townland_col)[count_col].sum().sort_values(ascending=False).head(10)` | `labels`=townlands, dataset `"Tenancies"` |
| Tenancies Over Time (`chart_id="tenYearTrend"`) | Chart, `line` | Only if `year_col`: numeric year → `groupby("_year")[count_col].sum()` sorted, **tail(35)** cap | `labels`=year strings, dataset `"Tenancies"`, `tension:0.25` |
| Top Family Names (Surnames) (`chart_id="tenTopNames"`) | Chart, `bar` | Only if `surname_col` AND resulting series non-empty: takes `df[surname_col]` as string, strips whitespace, **if the value contains a space, keeps only the last whitespace-delimited token** (`x.split()[-1] if " " in x else x`) as a naive "surname from full name" heuristic, drops empties, `value_counts().head(10)` | `labels`=surname strings, dataset `"Occurrences"` |

- Note the surname-chart's `else` branch is attached to the `if surname_col:`
  check, not to the inner `if len(top_names):` — i.e. the "no surname
  column" note fires correctly, but if a surname column exists yet yields
  zero non-empty values after cleaning, **no chart is appended and no note
  is emitted either** (silent no-op) — worth knowing if debugging why a
  tenancies page shows fewer than 3 charts despite having a `surname`-like
  column.
- **Notes emitted**: conditional per-missing-column warnings, plus an
  always-present `f"Loaded from: {DATA_PATH}"` note (misleading when the
  file doesn't actually exist — this note prints the *resolved-but-missing*
  path, not confirmation of a successful load).

### 9.4 `TownlandsGeoAnalytics` (`analytics/townland_geo.py`, `dataset_id="townlands_geo"`)

- **Source**: `<parents[2]>/data/townlands.json` (missing — §8, though the
  real file exists at `frontend/static/data/townlands.json`).
  `dataset_name="Townlands (GeoJSON)"`. The only module that parses GeoJSON
  directly (`json.loads`) instead of `pandas.read_csv`.
- **No `_pick_col` here** — this module inspects GeoJSON `Feature.properties`
  keys directly rather than tabular columns.

| Item | Type | Computation | Output shape |
|---|---|---|---|
| Townland Features | KPI | `len(geo.get("features", []))` | string, comma-formatted |
| Property Keys | KPI | `len(prop_keys)` where `prop_keys = collections.Counter()` incremented once per property key seen across all features (i.e. count of **distinct** property field names across the whole file) | string |
| Map Ready | KPI | `"Yes" if total else "No"` (i.e. non-zero feature count) | string |
| Top Property | KPI | `prop_keys.most_common(1)[0][0]` — the property key name that appears in the most features — or `"None"` if no features | string |

- **No charts at all.** `charts: list[Chart] = []` is initialised and never
  appended to — this is the only module of the six that produces zero
  charts, KPI-tiles only.
- **Notes emitted**: a static tip about including a `townland_id` join key
  in GeoJSON properties for map integration, plus a listing of the top 10
  most common property keys (`", ".join(...)`) or `"No properties
  detected."` if empty.

### 9.5 `UnifiedAnalytics` (`analytics/unified.py`, `dataset_id="unified"`)

- **Source**: `find_data_file("unified_processed.csv")` — same 4-candidate
  fallback logic as tenancies.py, currently unresolved (§8; real file is at
  `frontend/static/data/unified_processed.csv`, not one of the four
  candidates checked). `dataset_name="Unified Database"`.
- **No fuzzy column detection** — this module assumes fixed, exact column
  names (`surname`, `forename`, `townland`, `year`, `estate`, `gender`) with
  no `_pick_col` fallback; a differently-named CSV would raise a
  `KeyError` rather than degrading gracefully like the other four modules.

| Item | Type | Computation | Output shape |
|---|---|---|---|
| Total Records | KPI | `len(df)` | string, comma-formatted |
| Unique Surnames | KPI | `df['surname'].nunique()` | string, comma-formatted |
| Unique Townlands | KPI | `df['townland'].nunique()` | string, comma-formatted |
| Records with Year | KPI | `df['year'].notna().sum()` plus `%` of total, e.g. `"8,432 (62%)"` | string |
| Records with Estate | KPI | `df['estate'].notna().sum()` plus `%` of total | string |
| Records Over Time (`chart_id="unifiedYearTrend"`) | Chart, `line` | If `year` column present: numeric-coerce, drop NaN, `value_counts().sort_index()` (i.e. **count of records per exact year**, not summed against a count column — this module has no `count_col` concept) | `labels`=year strings, dataset `"Records"` (ints), `tension:0.25` |
| Top Family Names (`chart_id="unifiedTopSurnames"`) | Chart, `bar` | `df['surname'].dropna().value_counts().head(15)` (top 15, more than the other modules' top-10) | `labels`=surnames, dataset `"Occurrences"` (ints) |
| Top Townlands (`chart_id="unifiedTopTownlands"`) | Chart, `bar` | `df['townland'].dropna().value_counts().head(15)` | `labels`=townlands, dataset `"Records"` (ints) |
| Records by Estate (`chart_id="unifiedEstates"`) | Chart, `doughnut` | `df['estate'].dropna().value_counts()`, **top 10 sliced via `.index.tolist()[:10]`** | `labels`=estate names (≤10), single dataset with `data` list only (no `label` key), `options.plugins.legend.position="right"` |
| Gender Distribution (`chart_id="unifiedGender"`) | Chart, `doughnut` | If `gender` column present: `df['gender'].dropna().value_counts()` (all distinct values, no top-N cap) | `labels`=gender values, single dataset `data` only, legend on right |

- This is the only module producing **doughnut** charts, and the only one
  producing **5 charts** (max of any module) when all optional columns are
  present.
- **Notes emitted**: always two static notes — the resolved `DATA_PATH` and
  a fixed string `"Preprocessed and cleaned from unified database"` — no
  conditional missing-column notes (contrast with the other four modules,
  which explicitly warn when a column isn't found; this module just skips
  the chart silently if the literal column name isn't in `df.columns`).

### 9.6 `workhouse.py` — not a module

`analytics/workhouse.py` is a 0-byte file. It has no class, no `MODULE`
object, no KPIs, no charts. `discover_modules()` imports it successfully
(an empty `.py` file is valid Python — it defines an empty module) but
skips it at the `hasattr(mod, "MODULE")` check, so it never appears in
`modules`, `list_datasets()`, or the dataset picker. There is currently
**no dashboard for workhouse entity-resolution data** (`source_mentions`,
`entity_resolution_candidates`, `workhouse_unified_links`,
`entity_resolution_decisions`) despite the filename suggesting one was
planned.

## 10. Summary table — all KPI/chart output across the package

| Module | dataset_id | KPIs (count) | Charts (count) | Chart types used | Currently computes successfully? |
|---|---|---|---|---|---|
| `EmigrationAnalytics` | `emigration` | 4 | up to 2 | line, bar | No — `FileNotFoundError` (§8) |
| `EvictionsAnalytics` | `evictions` | 4 | up to 2 | line, bar | No — `FileNotFoundError` |
| `TenanciesAnalytics` | `tenancies` | 4 | up to 3 | bar, line, bar | No — `FileNotFoundError` |
| `TownlandsGeoAnalytics` | `townlands_geo` | 4 | 0 (always) | — | No — `FileNotFoundError` |
| `UnifiedAnalytics` | `unified` | 5 | up to 5 | line, bar, bar, doughnut, doughnut | No — `FileNotFoundError` |
| `workhouse.py` | — (not registered) | — | — | — | Not a module at all |

## 11. If fixing the data-path bug (not done as part of this documentation task)

Should this be revisited: the minimal fix for the three `parents[2]`-based
modules (`emigrations.py`, `evictions.py`, `townland_geo.py`) is changing
`parents[2]` to `parents[1]` to land on the actual `Coolattin-app/` repo
root, then either populating `Coolattin-app/data/<file>` with the expected
CSVs/JSON or repointing `DATA_PATH` at
`frontend/static/data/<equivalent-file>` to reuse the already-seeded
`unified_processed.csv` / `townlands.json`. `tenancies.py` has no known
source file anywhere in the repo (`unified_processed.csv` doesn't carry a
`tenancies.csv`-shaped schema) and would need a real data source identified
before it could work at all.
