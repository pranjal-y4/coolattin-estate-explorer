# 08 — Semantic Layer (`backend/services/semantic_layer.py`)

Full technical reference for the deterministic "slot-fill" query compiler —
a 1,186-line module that maps a natural-language analytical question to a
guaranteed-valid, injection-safe SQL (and, for a subset of metrics, SPARQL)
statement **without any LLM call** on the rule-based path, and with a
constrained, JSON-only LLM call on the fallback path.

This module is **Phase 2** of the Ask pipeline. It is a pure compiler
library — it has no DB connection, no HTTP client, and (per its own
docstring) never raises out of its public entry points; every function
either returns `None`/`SlotFill | None` on failure or logs and degrades
gracefully. All SQL text produced here is still re-checked by
`_sanitize_and_validate_sql` in `ask_service.py` before execution — the
semantic layer's "safe by construction" guarantee is a design property, not
a substitute for that guardrail (see §8).

**Where it runs**: per `CLAUDE.md`, this module is wired into the **legacy
pipeline** (`answer_question_stream`, active only when
`ASK_USE_NEW_PIPELINE=false`), not the default `_orchestrated_pipeline_stream`
path. This was confirmed by reading `backend/services/ask_service.py`
directly (see §9). The default pipeline (`ASK_USE_NEW_PIPELINE=true`) uses
direct LLM SQL generation instead and never imports `semantic_layer`.

---

## 1. Module public API (from the file's own docstring, lines 1–50)

| Function | Signature | Purpose |
|---|---|---|
| `try_rule_based_fill` | `(question, analysis, townland_resolution) -> SlotFill \| None` | Zero-LLM keyword/pattern matcher. Returns `None` if the question isn't analytical or no metric fits. |
| `build_slot_fill_prompt` | `(question, analysis, townland_resolution) -> str` | Builds the tight JSON-only prompt sent to the LLM for the fallback slot-fill path. |
| `parse_slot_fill` | `(raw_text, question="") -> SlotFill \| None` | Parses the LLM's JSON response into a validated `SlotFill`. Never raises. |
| `compile_sql` | `(slot_fill, clearances_col="count") -> str \| None` | Deterministic SQLite compiler. Never raises; returns `None` on failure. |
| `compile_sparql` | `(slot_fill) -> str \| None` | Equivalent SPARQL aggregate for the local GraphDB `co:` ontology. Returns `None` when no KG equivalent exists. |
| `validate_slot_fill` | `(slot_fill) -> None` | Raises `ValueError` if the fill references an undefined metric/dimension/filter. |
| `slot_fill_meta` | `(slot_fill, compiled_sql) -> dict` | Builds the provenance dict attached to `llm_meta` in the SSE result payload. |

The module docstring states the three-layer architecture explicitly:

```
① try_rule_based_fill   — keyword + entity pattern matching, zero LLM calls
② LLM slot filler       — structured JSON only (via build_slot_fill_prompt)
③ Deterministic compiler — parse_slot_fill + compile_sql = guaranteed-valid SQL
```

Adding a new metric is declared to require "only an entry in
`METRIC_REGISTRY` and optionally a keyword in `_METRIC_KEYWORDS`" — the
registries are deliberately flat Python dicts with no YAML/config-file
indirection.

---

## 2. The `SlotFill` dataclass — the exact slot-fill shape

```python
@dataclass
class SlotFill:
    metric: str                          # must be a key in METRIC_REGISTRY
    dimensions: list[str] = field(default_factory=list)
    filters: dict[str, Any] = field(default_factory=dict)
    group_mode: str = "aggregate"        # "aggregate"|"trend"|"list"|"detail"
    limit: int | None = 50
    order_by_override: str | None = None # overrides metric's default order
    confidence: float = 1.0              # 1.0 rule-based; 0.0–1.0 for LLM-filled
    source: str = "rule"                 # "rule" | "llm"
    raw_intent: str = ""                 # original question for provenance
```

This is the **only** intermediate representation between "natural-language
question" and "compiled SQL" in this module — both `try_rule_based_fill`
(rule path) and `parse_slot_fill` (LLM path) produce the same dataclass,
and `compile_sql`/`compile_sparql` consume it identically regardless of
`source`. There is no separate "LLM slot" schema and "rule slot" schema —
one shape, two producers.

Field meanings:

| Field | Type | Meaning |
|---|---|---|
| `metric` | `str` | Key into `METRIC_REGISTRY` — what to measure. |
| `dimensions` | `list[str]` | Which axes to `GROUP BY` / include in `SELECT` (e.g. `["year"]` for a trend). |
| `filters` | `dict[str, Any]` | `WHERE` predicates keyed by filter id; value shape depends on the filter (scalar, `[start, end]` pair, or `True` for boolean flags). |
| `group_mode` | `str` | One of `"aggregate"` (single row), `"trend"` (grouped by year), `"grouped"` (grouped by a non-year dimension), `"detail"` (non-aggregate attribute lookup — used only by `townland_attribute`). Note `"list"` is declared in the docstring/type comment but never actually assigned anywhere in the file — `group_mode` is only ever set to `"aggregate"`, `"trend"`, `"grouped"`, or `"detail"` by the code itself. |
| `limit` | `int \| None` | Row cap. `None` disables `LIMIT` entirely (used for true scalar aggregates). |
| `order_by_override` | `str \| None` | Escape hatch to override the metric's default `order_by`; never set by `try_rule_based_fill` or `parse_slot_fill` in this file — present for future/external callers. |
| `confidence` | `float` | `1.0` default for a freshly-constructed rule fill (actual rule-path confidence is computed separately, see §5.6); `0.0–1.0` reported by the LLM on the LLM path. |
| `source` | `str` | `"rule"` or `"llm"` — provenance tag surfaced to the SSE payload via `slot_fill_meta`. |
| `raw_intent` | `str` | The original question text, kept for logging/audit, not used by the compiler. |

---

## 3. Metric vocabulary — `METRIC_REGISTRY`

The registry defines **13 metrics** (not "thirty-plus" as literally counted
— see the note at the end of this section on how the "30+" figure is
actually reached). Each entry has this fixed shape (per the file's header
comment, lines 70–85):

```
from_clause     str  — base FROM (may include a JOIN alias block)
aggregate       str  — aggregate expression (no alias)
alias           str  — output column alias
base_where      str  — metric-inherent WHERE condition ("" if none)
dim_select      dict — dimension id → SELECT expression
dim_group_by    dict — dimension id → GROUP BY expression
filter_where    dict — filter id → WHERE SQL (use {val} placeholder)
order_by        str  — default ORDER BY for trend/grouped queries
valid_dimensions set
valid_filters   set
sparql_agg      str|None  — co: SPARQL aggregate if KG equivalent exists
keywords        list[str] — trigger substrings (lower-case)
subsumes        list[str] — template IDs this metric replaces
```

The `subsumes` field is documentation-as-code: each metric lists the IDs of
older, single-purpose hard-coded SQL templates (from the "verified
analysis" fast lane elsewhere in `ask_service.py`) that this one
generalized metric now covers. **This is where "30+" comes from** — 13
metrics × their combined `subsumes` lists enumerate roughly 30 distinct
template IDs that are now redundant/superseded, not 30 distinct metrics.

### 3.1 Full metric table

| Metric ID | Domain | `label` | `from_clause` | `aggregate` | `base_where` | Keywords | SPARQL equivalent? |
|---|---|---|---|---|---|---|---|
| `emigration_count` | Emigration | People who emigrated | `unified_record` | `COUNT(DISTINCT record_id)` | `has_emigration_record = 1` | `emigra` | Yes |
| `canada_emigration_count` | Emigration | Emigrants to Canada | `unified_record` | `COUNT(DISTINCT record_id)` | `has_emigration_record = 1 AND is_canada_destination = 1` | `canada` | No |
| `eviction_event_count` | Eviction | Eviction events (clearances ledger) | `clearances_record cr LEFT JOIN townland t ON cr.townland_id = t.id` | `SUM(cr.{eviction_col})` (col resolved at compile time) | `""` | `evict`, `clearance` | Yes |
| `evicted_person_count` | Eviction | People with eviction records | `unified_record` | `COUNT(DISTINCT record_id)` | `has_eviction_record = 1` | `evict` (disambiguated, see §5.5) | No |
| `population` | Census | Census population | `census_record c JOIN townland t ON c.townland_id = t.id` | `SUM(c.total)` | `""` | `population`, `census`, `populated`, `inhabitant` | Yes |
| `population_change` | Census | Population change between two census years | `census_record a JOIN census_record b ON a.townland_id = b.townland_id JOIN townland t ON a.townland_id = t.id` | `SUM(b.total) - SUM(a.total)` | `""` (year filters required) | `decline`, `change`, `fell`, `drop`, `loss`, `decrease` | No |
| `uninhabited_houses` | Census | Uninhabited houses in census | `census_record c JOIN townland t ON c.townland_id = t.id` | `SUM(c.uninhabited)` | `c.uninhabited > 0` | `uninhabit` | No |
| `tenancy_count` | Tenancy | Tenants recorded | `unified_record` | `COUNT(DISTINCT record_id)` | `has_tenancy_record = 1` | `tenant`, `tenancy` | No |
| `avg_holding_acres` | Tenancy | Average tenant landholding (acres) | `unified_record` | `ROUND(AVG(holding_acres), 2)` | `has_tenancy_record = 1 AND holding_acres IS NOT NULL` | `holding`, `acr`, `land` | No |
| `widow_count` | People/demographics | Widow records | `unified_record` | `COUNT(DISTINCT record_id)` | `is_widow = 1` | `widow` | No |
| `person_count` | People/demographics | People in records | `unified_record` | `COUNT(DISTINCT record_id)` | `""` | `people`, `record`, `person` | No |
| `townland_count` | Geography | Number of townlands | `townland` | `COUNT(*)` | `""` | `townland` | No |
| `parish_count` | Geography | Number of civil parishes | `townland` | `COUNT(DISTINCT civil_parish)` | `civil_parish IS NOT NULL AND TRIM(civil_parish) != ''` | `parish` | No |
| `townland_attribute` | Geography (non-aggregate) | Townland attributes (parish, barony, coordinates) | `townland` | `""` (compiler uses a fixed `SELECT *`-style projection) | `""` | `parish`, `barony`, `county`, `about`, `detail`, `where`, `located` | Yes |

Only **4 of 13 metrics** have a non-`None` `sparql_agg`: `emigration_count`,
`eviction_event_count`, `population`, `townland_attribute` (see §7).

### 3.2 `dim_select` / `dim_group_by` per metric

These two dicts are what makes a metric "groupable" — a dimension only
works for a metric if both a `SELECT` expression and (usually) a `GROUP BY`
expression are registered for it.

| Metric | Supported dimensions (`valid_dimensions`) |
|---|---|
| `emigration_count` | `year`, `townland`, `parish`, `ship`, `surname` |
| `canada_emigration_count` | `year`, `ship` |
| `eviction_event_count` | `year`, `townland`, `parish` |
| `evicted_person_count` | `year`, `townland`, `parish` |
| `population` | `year`, `townland`, `parish`, `barony` |
| `population_change` | `townland`, `parish` |
| `uninhabited_houses` | `year`, `townland` |
| `tenancy_count` | `year`, `townland`, `parish` |
| `avg_holding_acres` | `gender`, `townland`, `year` |
| `widow_count` | `townland`, `year` |
| `person_count` | `year`, `townland`, `parish`, `surname`, `role` |
| `townland_count` | `county`, `barony`, `parish` |
| `parish_count` | *(none — `set()`)* |
| `townland_attribute` | *(none — `set()`)* |

Note two subtleties visible in the raw dict literals:

- `emigration_count`'s `townland` dimension groups by
  `townland_norm, townland` (two columns) but only selects `townland` — the
  compiler emits whatever is in `dim_select` for `SELECT` and whatever is
  in `dim_group_by` for `GROUP BY` independently, so a `SELECT`-vs-`GROUP
  BY` column mismatch (grouping by a normalised column but selecting the
  raw display column) is a legal, intentional pattern here: it groups rows
  by the canonical form while displaying the human-readable form.
- `person_count`'s `surname` dimension similarly groups by
  `UPPER(surname), surname` but selects only `surname`.

### 3.3 Filter vocabulary — `filter_where` per metric and `_ALL_FILTER_KEYS`

The full closed set of filter ids recognised anywhere in the module:

```python
_ALL_FILTER_KEYS = frozenset({
    "townland", "year", "year_range", "year_a", "year_b",
    "surname", "gender", "parish", "barony", "county",
    "is_canada", "is_widow", "is_emigrant", "is_evicted", "is_tenant",
})
```

| Filter ID | Value shape | Example rendered SQL (`filter_where` template) | Used by metric(s) |
|---|---|---|---|
| `townland` | `str` (normalised uppercase name) | `townland_norm = '{val}'` or `UPPER(t.name) = '{val}'` or `UPPER(name) = '{val}'` (varies per metric depending on join alias) | emigration_count, eviction_event_count, evicted_person_count, population, uninhabited_houses, tenancy_count, avg_holding_acres, widow_count, person_count, townland_attribute, population_change (via `townland` in `valid_filters` though not directly listed in its `filter_where` dict — see caveat below) |
| `year` | `int` | `year = {val}` / `cr.year = {val}` / `c.year = {val}` | emigration_count, canada_emigration_count, eviction_event_count, population, uninhabited_houses, evicted_person_count, tenancy_count |
| `year_range` | `[start, end]` | `year BETWEEN {val[0]} AND {val[1]}` / `cr.year BETWEEN {val[0]} AND {val[1]}` / `c.year BETWEEN {val[0]} AND {val[1]}` | emigration_count, canada_emigration_count, eviction_event_count, population |
| `year_a` | `int` | `a.year = {val}` | population_change |
| `year_b` | `int` | `b.year = {val}` | population_change |
| `surname` | `str` (uppercased) | `UPPER(surname) = '{val}'` | emigration_count, person_count |
| `gender` | `"male"` \| `"female"` | `UPPER(COALESCE(gender,'')) IN ('M','MALE','F','FEMALE')` (note: this template does not actually branch on the filter value — see caveat below) | avg_holding_acres |
| `is_canada` | `True` (boolean flag filter — no `{val}` substitution) | `is_canada_destination = 1` | emigration_count |
| `is_widow` | `True` | `is_widow = 1` | person_count |
| `is_emigrant` | `True` | `has_emigration_record = 1` | person_count |
| `is_evicted` | `True` | `has_eviction_record = 1` | person_count |
| `is_tenant` | `True` | `has_tenancy_record = 1` | person_count |
| `county` | `str` | `county = '{val}'` | townland_count |
| `barony` | `str` | `barony = '{val}'` | townland_count |
| `parish` | `str` | `civil_parish = '{val}'` | townland_count |

**Caveat on `avg_holding_acres`'s `gender` filter**: its `filter_where["gender"]`
template is a fixed membership test
(`UPPER(COALESCE(gender,'')) IN ('M','MALE','F','FEMALE')`) that does **not**
reference `{val}` at all — applying this filter narrows to "gender is
recorded" but cannot select specifically male vs. female via the filter
path. Selecting a specific gender is instead done via the `gender`
**dimension** (`dim_group_by["gender"]` maps to the derived
`gender_group` column via a `CASE WHEN` expression), which is what
`try_rule_based_fill` actually uses when the question mentions
male/female — see §5, "Gender grouping for holding query."

`population_change`'s `valid_filters` set is `{"year_a", "year_b",
"townland"}`, but its `filter_where` dict (as literally written in the
file) only defines `year_a` and `year_b` — there is no `"townland"` key in
its `filter_where` dict. Because `compile_sql`'s filter-rendering loop does
`tpl = m["filter_where"].get(filt_key); if not tpl: continue`, a
`townland` filter passed to this metric is **silently dropped** at compile
time (no error, no `WHERE` clause emitted for it) rather than raising —
this is a real latent gap in the registry, not a misreading.

---

## 4. `DIMENSION_REGISTRY` — informational dimension metadata

```python
DIMENSION_REGISTRY: dict[str, dict] = {
    "year":        {"label": "Year",          "type": "integer",  "table_col": "year"},
    "townland":    {"label": "Townland",      "type": "string",   "table_col": "townland_norm"},
    "parish":      {"label": "Civil parish",  "type": "string",   "table_col": "civil_parish"},
    "barony":      {"label": "Barony",        "type": "string",   "table_col": "barony"},
    "county":      {"label": "County",        "type": "string",   "table_col": "county"},
    "gender":      {"label": "Gender",        "type": "category", "table_col": "gender"},
    "ship":        {"label": "Ship",          "type": "string",   "table_col": "ship_name"},
    "surname":     {"label": "Surname",       "type": "string",   "table_col": "surname"},
    "role":        {"label": "Role",          "type": "category", "table_col": "role"},
    "destination": {"label": "Destination",   "type": "string",   "table_col": "arrival"},
    "occupation":  {"label": "Occupation",    "type": "string",   "table_col": "occupation"},
}
```

This dict is **not consulted by `compile_sql`** — the compiler always uses
the per-metric `dim_select`/`dim_group_by` dicts inside `METRIC_REGISTRY`.
`DIMENSION_REGISTRY` exists purely for `validate_slot_fill`-adjacent
documentation and to populate the `dim_list` string interpolated into the
LLM slot-fill prompt (`build_slot_fill_prompt`, §6). Two of its 11 entries
(`destination`, `occupation`) are never referenced as a valid dimension by
any metric in `METRIC_REGISTRY` — they are declared but unused, presumably
reserved for future metrics.

---

## 5. `try_rule_based_fill` — the zero-LLM rule engine

Signature: `try_rule_based_fill(question: str, analysis: dict[str, Any], townland_resolution: dict[str, Any]) -> SlotFill | None`.

`analysis` is the dict produced by `ask_service._analyse_question()`
(Phase-1-adjacent question parsing — surname/year extraction, output mode
classification); `townland_resolution` is the dict produced by
`ask_service._resolve_townland_context()` / the identity resolver (Phase 1
entity resolution), carrying at minimum `name_norm`, `sql_id`, `kg_uri`,
`matched`, `raw_text`.

The function runs through a strict ordered gauntlet. Each stage can return
`None` early (short-circuiting all later logic):

### 5.1 Out-of-scope guard (return `None` immediately)

```python
_OUT_OF_SCOPE_SIGNALS: frozenset[str] = frozenset([
    "religion", "religious", "faith", "catholic", "protestant",
    "weather", "climate", "temperature", "rainfall",
    "crop", "farming", "agriculture", "tillage",
    "political",
    "died of", "cause of death",
    "other irish", "other estate",
    "workhouse",
    "entity resolution candidate", "confirmed match", "review required",
    "source mention",
])
```

If **any** substring appears in the lowercased question, the function
returns `None` unconditionally — before any metric keyword matching runs.
The in-code comment explains why each group exists: religion/weather/
crops/politics have no covering table at all; mortality ("died of",
"cause of death") is deliberately distinct from eviction/emigration data;
"other irish"/"other estate" rules out cross-estate comparison questions
(only Coolattin data is in the DB); and the workhouse/entity-resolution
phrases exclude the separate ER subsystem (`source_mentions`,
`entity_resolution_candidates`, `workhouse_unified_links` — none of which
any metric covers).

### 5.2 Unmapped-requirement guard (return `None` immediately)

```python
_UNMAPPED_REQUIREMENT_PHRASES: frozenset[str] = frozenset([
    "average rent", "rent owed", "rent paid",
    "under the age", "children under",
])
```

Catches questions that need a field/aggregate no metric provides (e.g.
`AVG(rent_owed)` — there is no `rent`-based metric in the registry at all
— or an age-range filter, which no metric's `filter_where` supports).

### 5.3 Cross-metric intersection guard (return `None` immediately)

```python
if "widow" in q and "emigra" in q:
    return None
```

A literal, hard-coded special case: "How many widows emigrated?" needs
`is_widow=1 AND has_emigration_record=1` simultaneously, but `widow_count`
has no emigration filter and `emigration_count` has no widow filter, so
neither metric alone can answer it — the guard forces a fall-through to
the LLM path rather than silently answering with the wrong subset.

### 5.4 Geography attribute lookup (early return, before metric scoring)

```python
_ATTRIBUTE_WORDS = frozenset([
    "which parish", "what parish", "which barony", "what barony",
    "which county", "what county", "located in", "where is", "belong to",
    "same parish", "fall within",
])
```

If any of these phrases is present **and** a townland was resolved in
Phase 1 (`townland_resolution.get("name_norm")`), the function returns a
`townland_attribute` `SlotFill` directly with `confidence=0.92`,
`group_mode="detail"`, `limit=None`. This bypasses the metric-keyword
scorer entirely.

Before this check runs, there's an estate-scope disambiguation guard
(labelled "Fix 2" in the source): if the resolved townland's `raw_text`
is immediately followed by the word "estate" in the original question
(regex `\b<raw_text>\s+estate\b`, case-insensitive) — e.g. "…the Coolattin
estate" — the resolved townland is treated as an estate-scope qualifier,
not a real townland filter, and `townland_norm` is set to `None` for the
rest of the function. The in-code example clarifies the boundary: "from
Ballinacor in the Coolattin estate" still filters to `BALLINACOR` because
the resolver's `raw_text` is `"Ballinacor"`, not `"Coolattin estate"`, so
the regex doesn't match on that phrase.

### 5.5 Metric detection — best-match keyword scoring

```python
_METRIC_KEYWORDS: list[tuple[str, str]] = [
    ("canada",    "canada_emigration_count"),
    ("widow",     "widow_count"),
    ("uninhabit", "uninhabited_houses"),
    ("clearance", "eviction_event_count"),
    ("emigra",    "emigration_count"),
    ("evict",     "eviction_event_count"),
    ("decline",   "population_change"),
    ("population_change", "population_change"),
    ("census",    "population"),
    ("population","population"),
    ("inhabited", "uninhabited_houses"),
    ("holding",   "avg_holding_acres"),
    ("acreage",   "avg_holding_acres"),
    ("tenant",    "tenancy_count"),
    ("tenancy",   "tenancy_count"),
]
```

This is a **list of (substring, metric_id) pairs, ordered by specificity**
(the header comment says "most specific first to avoid false positives").
Matching is **not** first-hit-wins. Instead:

1. Every `(keyword, candidate)` pair whose keyword appears in the
   lowercased question increments a per-candidate score in
   `_metric_scores: dict[str, int]`.
2. Before scoring, `eviction_event_count` is conditionally remapped to
   `evicted_person_count` if the question also contains any of
   `["people", "person", "who", "names", "list"]` — this disambiguates
   "how many evictions" (event count) from "who was evicted" (person
   count) at the point of scoring, not after.
3. The candidate(s) with the **highest total score** win; ties are broken
   by iterating `_METRIC_KEYWORDS` in its declared order again and picking
   the first candidate whose score equals the max — i.e. declaration order
   is the tie-break priority, matching the "most specific first" ordering
   intent.

This best-match design is explicitly a fix for a documented prior defect:
`eval_results/eval_baseline_post_migration.md`-era eval notes (surfaced in
`ask_eval.py`'s report generator, around its `llm_routed_wrong` section)
record that a naive first-match scorer let an incidental keyword like
"tenant" in an otherwise out-of-scope question force a wrong deterministic
route. The current best-match scorer, plus the out-of-scope/unmapped-
requirement guards in §5.1–§5.2, are the fix.

After the keyword scorer, three more special-case rules run **only if no
metric_id has been found yet** (`if not metric_id:`), in this order:

1. `"townland" in q` + any of `["how many", "count", "total", "number"]` → `townland_count`
2. `"parish" in q` + any of `["how many", "count", "total"]` → `parish_count`
3. `analysis.get("surname")` is truthy **and** (`analysis.get("output_mode") in {"count", "grouped"}` **or** any of `_COUNT_WORDS` = `{"how many", "count", "total", "number of"}` appears) → `person_count`

If still no `metric_id`, the function returns `None`.

### 5.6 Filter extraction (once a metric is chosen)

Applied in this order, each gated on the filter being in the chosen
metric's `valid_filters`:

- **`townland`** — from the Phase-1-resolved `townland_norm` (post the
  estate-scope correction in §5.4).
- **`year`** — from `analysis.get("year")`, cast to `int`.
- **Famine year-range special case**: if any of `["1841", "1851", "famine", "decline"]` appears —
  - if both `"1861"` and `"1841"` are literally in the question text: pops any `year` filter and sets `year_range = [1841, 1861]` (if the metric supports `year_range`).
  - elif both `"1851"` and `"1841"` are in the text: pops `year`; for `population_change` specifically sets `year_a=1841, year_b=1851`; otherwise sets `year_range=[1841, 1851]` if supported.
- **`surname`** — from `analysis.get("surname")`, upper-cased.
- **`gender`** — via helper `_detect_gender(q)` (returns `"female"` if any of `female/women/woman` present, `"male"` if any of `male/men/man` present, else `None`).
- **`is_canada`** — set to `True` only when `"canada" in q` **and** the chosen metric is specifically `emigration_count` (canada_emigration_count doesn't need the flag — it's baked into `base_where` already).

### 5.7 Dimension extraction

- **Year/trend dimension**: triggered by `_TREND_WORDS` (`per year`, `by
  year`, `each year`, `over time`, `trend`, `yearly`, `annual`, `year by
  year`) unioned with `{"which year", "what year", "what years", "all
  years", "all census", "each census", "over the years"}`. Only added if
  `"year"` is in the metric's `valid_dimensions` **and** `year` is not
  already present as a filter (avoids grouping by the same field you just
  pinned to a single value).
- **Townland dimension**: triggered by `_BY_TOWNLAND_WORDS` = `{"per
  townland", "by townland", "each townland"}`.
- **Gender dimension** (only for `avg_holding_acres`): triggered by any of
  `["male", "female", "gender", "men", "women"]`; if added, any `gender`
  filter already set is deleted (`del filters["gender"]`) so the query
  doesn't simultaneously filter to one gender and group by gender.
- **Parish dimension**: triggered by `["by parish", "per parish", "each
  parish"]`.
- **Ship dimension**: only for `emigration_count`/`canada_emigration_count`,
  triggered by the literal substring `"ship"`.

### 5.8 `group_mode` and `limit` determination

```python
group_mode = "aggregate"
limit: int | None = 50

if dimensions:
    group_mode = "trend" if "year" in dimensions else "grouped"
elif not filters or len(filters) == 1 and "townland" in filters:
    limit = None   # simple scalar — no LIMIT

if any(w in q for w in ["worst", "most", "highest", "peak", "top", "largest", "smallest"]):
    limit = 10
```

So: any dimension present → `"trend"` (if year) or `"grouped"` (otherwise).
No dimensions and either no filters at all, or exactly one filter that is
`townland` → treated as a pure scalar aggregate, `limit=None` (no `LIMIT`
clause emitted at all — see §6). Superlative language ("worst", "most",
"highest", "peak", "top", "largest", "smallest") always caps the result to
`limit=10` regardless of the above, since these are top-N ranking queries.

### 5.9 Confidence scoring — the exact rule behind the 0.80 threshold

```python
confidence = 1.0
_competing_metrics = len(_metric_scores)
if _competing_metrics > 1:
    confidence = max(0.82, confidence - 0.06 * (_competing_metrics - 1))
if not filters and not dimensions:
    confidence = min(confidence, 0.90)
```

Starting confidence is `1.0`. It is penalised in two independent ways:

1. **Metric competition penalty**: if more than one distinct metric
   scored at least one keyword hit (`_competing_metrics = len(_metric_scores)`,
   counting *distinct candidate metrics*, not total keyword hits), confidence
   drops by `0.06` per extra competing metric beyond the first, floored at
   `0.82`. E.g. 2 competing metrics → `1.0 - 0.06 = 0.94`; 4 competing
   metrics → `1.0 - 0.18 = 0.82` (the floor — the formula would go to
   `0.76` but `max(0.82, …)` clamps it).
2. **Unfiltered/ungrouped penalty**: if the resulting `SlotFill` has
   neither filters nor dimensions (a fully global, unscoped aggregate),
   confidence is capped at `0.90` regardless of the competition penalty.

**Consequence for the 0.80 confidence threshold used downstream**
(`ask_service.py`'s `_sl_confidence_threshold = 0.80`, see §9): because the
competition penalty floors at `0.82` and the unfiltered-query cap is
`0.90`, a rule-based fill that reaches this point (i.e. passed all the
guards in §5.1–§5.3 and found a `metric_id`) can *never* itself fall below
the `0.80` threshold from these two penalties alone — the lowest value
either penalty can produce is `0.82`. In practice this means: **once
`try_rule_based_fill` returns a non-`None` `SlotFill`, it is essentially
always accepted by the `0.80` gate in the caller.** The `0.80` threshold's
real effect is therefore almost entirely a **binary** gate — "did the rule
engine produce a fill at all" — rather than a graded confidence cutoff
within the rule path; the graded low-confidence behaviour is reserved for
the *LLM* slot-fill path (§6), whose `parse_slot_fill` rejects anything
under `0.6`, and whose caller in `ask_service.py` separately requires
`>= 0.70` before compiling (see §9).

### 5.10 What happens below the threshold — confirmed from `ask_service.py`

Reading the call site directly (`backend/services/ask_service.py`, inside
the legacy `answer_question_stream` function, `~line 3738`):

```python
_sl_confidence_threshold = 0.80
if (
    _semantic_slot_fill is not None
    and _semantic_slot_fill.confidence >= _sl_confidence_threshold
    and not force_llm
):
    ... compile and route via semantic layer ...
else:
    _semantic_routed = False
```

If `try_rule_based_fill` returns `None`, or returns a `SlotFill` whose
`confidence` is below `0.80` (which, per §5.9, essentially never happens
for a successfully-produced rule fill — it is far more common for the
function to have returned `None` outright), the pipeline does **not** fail
outright. It falls through the ordered fast-lane chain in
`answer_question_stream`: Phase 4 template embedding match → verified
(curated) SQL templates → direct approved-memory reuse → the intent
router (`ANALYTICAL`/`RELATIONAL`/`COMPARATIVE`/`FALLBACK`). Only on the
`ANALYTICAL` route does the pipeline give the semantic layer a *second*
chance via the **LLM-assisted slot-fill path** (§6) before finally falling
back to fully free-form LLM SQL generation. This fall-through chain is
documented in full in `CLAUDE.md`'s "Legacy pipeline" section and is
reproduced accurately by this description.

---

## 6. The LLM-assisted slot-fill path

This is present in the file and is genuinely used (per `ask_service.py`,
only on the `ANALYTICAL` intent route, and only when the rule-based fill
either failed or produced low confidence — see §9). It differs from the
rule-based path in that it makes exactly one LLM call and constrains the
LLM to emit **JSON only, never SQL**.

### 6.1 `build_slot_fill_prompt`

Builds a prompt string with:

- The raw question.
- A `townland_hint` line: if `townland_resolution.get("matched")` is
  truthy, `f"Resolved townland: {name_norm} (sql_id=..., kg_uri=...)"`;
  otherwise `"No townland resolved."`.
- Extracted `year` / `surname` from `analysis` (or the literal string
  `"none"` if absent).
- A newline-joined list of every metric id and its `label` from
  `METRIC_REGISTRY` (`f"  {mid}: {m['label']}"` per line) — this is the
  entire metric vocabulary handed to the LLM verbatim, so it can never
  hallucinate a metric name that doesn't already exist in the registry
  (the parser also double-checks this, see §6.3).
- A comma-joined sorted list of all `DIMENSION_REGISTRY` keys.
- A comma-joined sorted list of `_ALL_FILTER_KEYS`.
- A fixed JSON schema template the LLM must emit exactly:

```json
{
  "metric": "<metric_id or null if not analytical>",
  "dimensions": ["<dim1>", ...],
  "filters": {
    "townland": "<UPPER_NORM or null>",
    "year": <integer or null>,
    "year_range": [<start>, <end>] or null,
    "surname": "<UPPER or null>",
    "gender": "<male|female or null>",
    "is_canada": <true|false or null>,
    "is_widow": <true|false or null>
  },
  "group_mode": "aggregate|trend|grouped|detail",
  "limit": <integer or null>,
  "confidence": <0.0-1.0>
}
```

Plus five explicit rules appended to the prompt text, verbatim:
metric must be a listed id or `null`; dimensions/filters must be valid for
the chosen metric; **"confidence < 0.75 means uncertain — the system will
fall back to template matching"** (this is a prompt-level instruction to
the LLM, distinct from the actual code-level `0.6` rejection threshold in
`parse_slot_fill` and the `0.70` acceptance threshold in the
`ask_service.py` caller — see the discrepancy note in §6.4); trend/by-
townland instructions map directly onto `dimensions`; null values should
be omitted from `filters`.

Note the JSON schema only lists `townland, year, year_range, surname,
gender, is_canada, is_widow` as example filter keys — it omits `year_a`,
`year_b`, `county`, `barony`, `parish`, `is_emigrant`, `is_evicted`,
`is_tenant` from the example object (though `filter_list` in the prose
above it does enumerate the full `_ALL_FILTER_KEYS` set), so the LLM is
told the full valid vocabulary in prose but only shown a subset in the
example skeleton.

### 6.2 `parse_slot_fill`

```python
def parse_slot_fill(raw_text: str, question: str = "") -> SlotFill | None:
```

1. Strips Markdown code fences (`` ```json `` / `` ``` ``) via regex before
   attempting `json.loads`.
2. Returns `None` (never raises) on any JSON parse failure, logged at
   `log.debug`.
3. Returns `None` if `metric` is missing, falsy, or not a key in
   `METRIC_REGISTRY` — this is the hard guarantee against a hallucinated
   metric name reaching the compiler.
4. Reads `confidence` (`float(data.get("confidence", 0.5))`); **returns
   `None` if `confidence < 0.6`** — this is the actual code-level rejection
   threshold, distinct from the `0.75` figure mentioned in the prompt text
   sent to the LLM.
5. Drops any `null`-valued filter keys from the LLM's `filters` object
   (`{k: v for k, v in raw_filters.items() if v is not None}`).
6. Constructs a `SlotFill` with `source="llm"`.
7. Runs it through `validate_slot_fill` — if that raises `ValueError`
   (unknown dimension/filter for the chosen metric), the exception is
   caught, logged at `log.debug`, and `None` is returned instead of
   propagating.

### 6.3 Guarantee against hallucinated slots

Because `parse_slot_fill` checks `metric in METRIC_REGISTRY` before
constructing the `SlotFill`, and then unconditionally calls
`validate_slot_fill` (which itself re-checks metric membership plus every
dimension against `valid_dimensions` and every filter against
`valid_filters`), **no `SlotFill` object with an unknown metric,
dimension, or filter key can ever reach `compile_sql`** regardless of
whether it originated from the rule engine or the LLM — the LLM path adds
exactly one more layer of validation (JSON parsing) on top of the same
`validate_slot_fill` gate the rule path also implicitly satisfies by
construction (the rule engine only ever assigns known metric/dimension/
filter ids from its own hard-coded keyword tables).

### 6.4 Threshold discrepancy worth flagging

There are **three different confidence numbers** associated with the LLM
slot-fill path, each doing a different job:

| Value | Where | Effect |
|---|---|---|
| `0.75` | Prompt text shown to the LLM (`build_slot_fill_prompt`) | Advisory instruction to the LLM about what confidence it should self-report as "uncertain" — has no code-level enforcement in this module. |
| `0.6` | `parse_slot_fill`, hard-coded | Any parsed `SlotFill` with `confidence < 0.6` is discarded (`return None`) before it can reach the compiler. |
| `0.70` | `ask_service.py` caller, hard-coded (`if _sf_parsed and _sf_parsed.confidence >= 0.70:`) | Even a `SlotFill` that survives `parse_slot_fill`'s `0.6` floor is only actually compiled and used if its confidence is `>= 0.70` at the call site. |

So the effective acceptance floor for an LLM-produced slot fill is `0.70`
(the stricter of the two enforced checks), while `0.6` and `0.75` are a
looser internal filter and an unenforced prompt hint respectively.

---

## 7. The SQL compiler — `compile_sql`

```python
def compile_sql(sf: SlotFill, clearances_col: str = "count") -> str | None:
```

Deterministic template assembly, in this fixed order, never using string
formatting on anything except values that pass through `_render_filter`
(itself always calling `_esc`, see §8):

1. **`validate_slot_fill(sf)`** — raises internally if invalid; caught by
   the surrounding `try/except`, which logs at `log.warning` and returns
   `None`. Compilation therefore never raises to the caller.
2. **Non-aggregate branch**: if `sf.metric == "townland_attribute"`,
   delegates to `_compile_attribute_lookup` and returns immediately (see
   below) — this metric bypasses the aggregate-query builder entirely.
3. **`SELECT` clause**: for each `dim` in `sf.dimensions`, look up
   `m["dim_select"].get(dim, dim)` (falls back to the bare dimension name
   if somehow not registered) and append it; then append the metric's
   `aggregate` expression (with `{eviction_col}` substituted for the
   runtime-detected clearances column name) aliased as `m["alias"]`.
   Joined with `", "`.
4. **`FROM` clause**: literally `m["from_clause"]`.
5. **`WHERE` clause**: starts with `m["base_where"]` if non-empty, then
   for each `(filt_key, filt_val)` in `sf.filters`, looks up the template
   in `m["filter_where"]` (skips silently — `continue` — if not found for
   that metric, e.g. the `population_change` + `townland` gap noted in
   §3.3) and renders it via `_render_filter`. All parts joined with
   `" AND "`; the whole clause omitted if no parts exist.
6. **`GROUP BY`**: for each `dim` in `sf.dimensions`, looks up
   `m["dim_group_by"].get(dim)`; only appended if truthy. Omitted entirely
   if no dimensions produced a group expression.
7. **`ORDER BY`**: `sf.order_by_override` if set, else `m["order_by"]` —
   but **only if `sf.dimensions` is non-empty** (a pure scalar aggregate
   gets no `ORDER BY` at all, since there's nothing to order).
8. **`LIMIT`**: `sf.limit`, except forced to `None` when
   `sf.dimensions` is empty (a scalar aggregate never gets a `LIMIT`
   clause, since it returns exactly one row by construction — this
   overrides whatever `sf.limit` was set to, including the rule engine's
   default of `50`).
9. Final assembly:
   `f"SELECT {select_sql} FROM {from_sql}{where_sql}{group_sql}{order_sql}{limit_sql}"`.

### `_render_filter` — value interpolation

```python
def _render_filter(template: str, val: Any) -> str:
    if isinstance(val, (list, tuple)) and "{val[0]}" in template:
        return template.replace("{val[0]}", str(val[0])).replace("{val[1]}", str(val[1]))
    if isinstance(val, str):
        return template.replace("{val}", _esc(val))
    return template.replace("{val}", str(val))
```

Three branches: a `[start, end]` pair for range templates (int values,
`str()`-cast, not escaped — because year values are always ints by this
point, never free text); a string value, escaped via `_esc` before
substitution; anything else (ints, bools already baked into the template
text as in `is_canada`) cast to `str()` directly. Note boolean filter
templates like `is_canada_destination = 1` don't actually reference
`{val}` at all — the filter's presence in `sf.filters` is what matters,
not its value; `_render_filter` is called on them but has nothing to
replace, so it's effectively a no-op pass-through in that case.

### `_compile_attribute_lookup`

```python
def _compile_attribute_lookup(sf: SlotFill, m: dict) -> str | None:
    townland = sf.filters.get("townland")
    if not townland:
        return None
    return (
        "SELECT name, civil_parish, barony, county, centroid_lat, centroid_lon "
        f"FROM townland WHERE UPPER(name) = '{_esc(str(townland))}'"
    )
```

A completely separate, fixed-shape query — not built from
`dim_select`/`aggregate`/`group_by` at all (the `townland_attribute`
registry entry's `aggregate`, `dim_select`, `dim_group_by`, `order_by` are
all empty and unused for this branch). Returns `None` if no `townland`
filter is present, since the metric has nothing else to select on.

### 7.1 Traced worked example

Given the question **"How many people emigrated from Ballinacor by
year?"**, assume Phase 1 resolves `townland_resolution = {"name_norm":
"BALLINACOR", "matched": True, "raw_text": "Ballinacor", ...}` and
`analysis = {"year": None, "surname": None, "output_mode": "count"}`.

Tracing `try_rule_based_fill`:

- No out-of-scope/unmapped-requirement/widow-emigration guard hits.
- Not an attribute-lookup phrase.
- `_METRIC_KEYWORDS` scoring: `"emigra"` matches → `emigration_count`
  scores `1`. No other keyword matches. `_metric_scores = {"emigration_count": 1}`,
  `metric_id = "emigration_count"`.
- Filters: `townland_norm = "BALLINACOR"` is in `valid_filters` →
  `filters = {"townland": "BALLINACOR"}`. No year filter (none extracted).
  No famine-year phrase. No surname. No gender. No `"canada"` substring.
- Dimensions: `"by year"` is in `_TREND_WORDS` and `"year"` is not already
  a filter → `dimensions = ["year"]`.
- `group_mode`: dimensions present and `"year"` in them → `"trend"`.
- `limit`: stays `50` (dimensions present, so the scalar-limit branch is
  skipped; no superlative word present, so no `limit=10` override).
- Confidence: `_competing_metrics = 1` → no competition penalty. Filters
  and dimensions are both non-empty → no unfiltered-query cap. Final
  `confidence = 1.0`.

Resulting `SlotFill`:

```python
SlotFill(
    metric="emigration_count",
    dimensions=["year"],
    filters={"townland": "BALLINACOR"},
    group_mode="trend",
    limit=50,
    confidence=1.0,
    source="rule",
    raw_intent="How many people emigrated from Ballinacor by year?",
)
```

`1.0 >= 0.80`, so `ask_service.py` accepts this fill and calls
`compile_sql(sf, clearances_col)`. Tracing `compile_sql` against the
`emigration_count` registry entry:

- `SELECT`: dimension `year` → `dim_select["year"] = "year"`; then
  aggregate `COUNT(DISTINCT record_id) AS emigration_count`.
  → `"year, COUNT(DISTINCT record_id) AS emigration_count"`
- `FROM`: `"unified_record"`
- `WHERE`: `base_where = "has_emigration_record = 1"`, plus the
  `townland` filter template `"townland_norm = '{val}'"` rendered with
  `_esc("BALLINACOR")` → `"townland_norm = 'BALLINACOR'"`.
  → `" WHERE has_emigration_record = 1 AND townland_norm = 'BALLINACOR'"`
- `GROUP BY`: dimension `year` → `dim_group_by["year"] = "year"`.
  → `" GROUP BY year"`
- `ORDER BY`: dimensions non-empty → `m["order_by"] = "year"`.
  → `" ORDER BY year"`
- `LIMIT`: dimensions non-empty, so `sf.limit` (`50`) is kept.
  → `" LIMIT 50"`

**Final compiled SQL:**

```sql
SELECT year, COUNT(DISTINCT record_id) AS emigration_count
FROM unified_record
WHERE has_emigration_record = 1 AND townland_norm = 'BALLINACOR'
GROUP BY year
ORDER BY year
LIMIT 50
```

This trace is derived directly from the code paths in this file (not
copied from a docstring example — the file contains no literal SQL
examples in comments), cross-checked line-by-line against `compile_sql`
and `try_rule_based_fill`.

---

## 8. Injection safety — how "safe by construction" actually holds

The module produces read-only, injection-safe SQL by construction through
a combination of four properties, all verifiable directly in the code:

1. **No raw string concatenation of user text into SQL structure.** Every
   `WHERE` fragment comes from a fixed template string already declared in
   `METRIC_REGISTRY` (e.g. `"townland_norm = '{val}'"`). User-derived
   *values* are only ever substituted into the single `{val}` /
   `{val[0]}` / `{val[1]}` placeholder positions inside those templates,
   never into the SQL keyword/clause skeleton itself — there is no path by
   which extracted question text can add a new clause, subquery, or
   statement terminator.
2. **All string values are escaped via `_esc`** before substitution:
   `_esc(value) = str(value).replace("'", "''")` — the standard SQL
   single-quote doubling escape, applied inside `_render_filter`
   (`if isinstance(val, str): return template.replace("{val}", _esc(val))`)
   and again explicitly inside `_compile_attribute_lookup`
   (`_esc(str(townland))`). Since every value is wrapped in single quotes
   in its owning template (`'{val}'`), doubling embedded quotes is
   sufficient to prevent breaking out of the string literal.
3. **Non-string values are constrained by type before reaching SQL.**
   Year filters are cast with `int(year)` in `try_rule_based_fill` before
   being stored in `sf.filters`; the LLM path's `parse_slot_fill` reads
   `year`/`year_range` straight from parsed JSON, so those are native
   Python `int`/list types from `json.loads`, not attacker-controlled
   strings — `_render_filter`'s fallback branch (`template.replace("{val}",
   str(val))`) only ever receives already-typed ints/floats/bools for
   these positions, not raw text.
4. **The metric/dimension/filter vocabulary is a closed, fixed set** —
   `validate_slot_fill` is invoked unconditionally at the top of
   `compile_sql` (and separately inside `parse_slot_fill` for the LLM
   path) and raises `ValueError` for anything not already declared in
   `METRIC_REGISTRY`/`valid_dimensions`/`valid_filters`. Since the
   *shape* of every emitted query (which columns, which tables, which
   `WHERE` templates) is entirely determined by `sf.metric` — a value
   drawn from a fixed, hard-coded dict of 13 keys — there is no way for
   either the rule engine or the LLM to cause the compiler to reference a
   table, column, or SQL construct that isn't already one of the ~13
   pre-authored templates.
5. **Every SELECT statement, no writes.** There is no code path in this
   file that emits `INSERT`/`UPDATE`/`DELETE`/`DROP`/`ATTACH`/`PRAGMA` or
   any DDL — the compiler's output format string is hard-coded as
   `f"SELECT {select_sql} FROM {from_sql}{where_sql}{group_sql}{order_sql}{limit_sql}"`
   (and the attribute-lookup branch is a hard-coded `SELECT` literal too).
   There is no template anywhere in `METRIC_REGISTRY` whose SQL fragment
   contains a semicolon or a second statement, so statement-stacking is
   not possible via this compiler either.

This module's docstring itself is explicit that this is defense-in-depth,
not the sole guarantee: "Every compiled query goes through
`_sanitize_and_validate_sql` in `ask_service` before execution — the
`FORBIDDEN_SQL` guardrail is never bypassed." So the actual safety
guarantee in production is two-layered: (a) this compiler cannot construct
anything other than a `SELECT` from a fixed template set with escaped
values, and (b) `ask_service._sanitize_and_validate_sql` independently
re-validates every SQL string — from *any* source, not just this module —
before it reaches SQLite.

---

## 9. SPARQL compilation — verifying the `CLAUDE.md` claim

`CLAUDE.md` describes this module as "a slot-fill compiler → deterministic
SQL **+ SPARQL** (no LLM)". This is **accurate, not stale** — `compile_sparql`
is a real, functioning code path, not a vestigial stub:

```python
def compile_sparql(sf: SlotFill) -> str | None:
    try:
        m = METRIC_REGISTRY.get(sf.metric)
        if not m or not m.get("sparql_agg"):
            return None
        sparql_tpl: str = m["sparql_agg"]
        filter_triples: list[str] = []
        if "townland" in sf.filters:
            norm = _esc(str(sf.filters["townland"]))
            filter_triples.append(f'FILTER(UCASE(STR(?name)) = "{norm}")')
        if "year" in sf.filters:
            filter_triples.append(f'FILTER(?year = {sf.filters["year"]})')
        if "year_a" in sf.filters:
            filter_triples.append(f'FILTER(?year_a = {sf.filters["year_a"]})')
        if "year_b" in sf.filters:
            filter_triples.append(f'FILTER(?year_b = {sf.filters["year_b"]})')
        if "year_range" in sf.filters:
            yr = sf.filters["year_range"]
            filter_triples.append(f"FILTER(?year >= {yr[0]} && ?year <= {yr[1]})")
        filters_block = " . ".join(filter_triples)
        sparql = sparql_tpl.replace("{filters}", filters_block)
        return sparql
    except Exception as exc:
        log.debug("semantic_layer.compile_sparql_failed metric=%s error=%s", sf.metric, exc)
        return None
```

Key facts:

- It targets the **local GraphDB `co:` ontology**, not VRTI — the
  docstring says "Equivalent SPARQL aggregate for the local GraphDB `co:`
  ontology." This is a separate integration from `backend/integrations/
  vrti_sparql.py`; the relevant client is `backend/integrations/
  graphdb_sparql.py`.
- Only **4 of the 13 metrics** have a `sparql_agg` template:
  `emigration_count`, `eviction_event_count`, `population`,
  `townland_attribute` (verified by grepping `METRIC_REGISTRY` for
  non-`None` `sparql_agg` values — every other metric's entry has
  `"sparql_agg": None`). For any other metric, `compile_sparql` returns
  `None` immediately.
- The four `sparql_agg` templates use `co:EstatePerson`, `co:Clearance`,
  `co:CensusRecord`, `co:Townland` as subject types and properties like
  `co:hasEmigrationRecord`, `co:year`, `co:count`, `co:totalPopulation`,
  `co:civilParish`, `co:barony`, `co:county` — matching the same `co:`
  ontology vocabulary as `graphdb_sparql.py`.
- The `PREFIX` declarations are **not** included in the returned string —
  the docstring notes "PREFIX declarations are added by
  `graphdb_sparql.query`" — so `compile_sparql`'s output is a bare
  `SELECT ... WHERE { ... }` fragment, not a complete, executable SPARQL
  document on its own.
- **However, this path is not invoked anywhere in `ask_service.py`.**
  Grepping `backend/services/ask_service.py` for `compile_sparql` (the
  function is imported alongside `try_rule_based_fill`/`compile_sql` at
  the `from backend.services.semantic_layer import (...)` block inside the
  legacy pipeline) shows it is imported but never actually called in the
  file's control flow that was read for this document — only
  `try_rule_based_fill` and `compile_sql` are invoked in the routing logic
  that ultimately sets `sql`/`llm_meta`. The only other call sites are in
  `backend/services/ask_eval.py` (an eval harness that imports and calls
  `compile_sparql` directly for offline SQL-vs-SPARQL comparison — see
  `eval_results/rq6_sql_vs_sparql.md`, which exists specifically to
  compare this pathway's output against SQL). **Conclusion: `compile_sparql`
  is real, tested-in-isolation code, not stale documentation, but it is
  not wired into the live Ask-page request path** — it is exercised only
  by the offline evaluation harness (research question 6 in the
  dissertation's eval suite), not by any production route handler.

---

## 10. Call site in `ask_service.py` — confirmed integration details

Reading `backend/services/ask_service.py` directly (not inferred) confirms
the following, all inside the **legacy** `answer_question_stream` function
(active only when `ASK_USE_NEW_PIPELINE=false`; the default orchestrated
pipeline, `_orchestrated_pipeline_stream`, starting at a different line in
the same file, does not import or call `semantic_layer` at all):

```python
_sl_confidence_threshold = 0.80
if (
    _semantic_slot_fill is not None
    and _semantic_slot_fill.confidence >= _sl_confidence_threshold
    and not force_llm
):
    _compiled = _compile_semantic_sql(_semantic_slot_fill, _clearances_count_column())
    if _compiled:
        sql = _compiled
        llm_meta = _slot_fill_meta(_semantic_slot_fill, sql)
        ...
        query_provenance.update({"strategy": "semantic_layer"})
        _semantic_routed = True
    else:
        _semantic_routed = False
else:
    _semantic_routed = False
```

`_semantic_slot_fill` itself is computed earlier via
`_try_rule_based_fill(clean_q, analysis, townland_resolution)`, wrapped in
a `try/except` that swallows any import or runtime error and leaves
`_semantic_slot_fill = None` on failure (belt-and-braces around the
module's own internal `try/except`s).

If `_semantic_routed` is `False`, the pipeline proceeds through: Phase 4
template embedding fast lane → verified (curated) SQL → direct approved-
memory reuse → else the intent router runs
(`intent_router.classify_intent`). **Only on the `ANALYTICAL` route**, and
**only if `_semantic_slot_fill is not None`** (i.e. the rule engine did
produce *some* fill, just below the `0.80` bar, or — more commonly per
§5.9 — it returned `None` and this branch is skipped), does the LLM slot-
fill path run:

```python
if _intent_route == "analytical" and _semantic_slot_fill is not None:
    _sf_prompt = _build_slot_fill_prompt(clean_q, analysis, townland_resolution)
    _sf_raw, _sf_meta = _llm_generate(_sf_prompt, purpose="slot_fill", max_tokens=256, temperature=0.0)
    _sf_parsed = _parse_slot_fill(_sf_raw, clean_q)
    if _sf_parsed and _sf_parsed.confidence >= 0.70:
        _llm_slot_sql = _compile_semantic_sql(_sf_parsed, _clearances_count_column())
        ...
        query_provenance["strategy"] = "semantic_layer_llm"
```

Note the LLM call here uses `temperature=0.0` and a tight `max_tokens=256`
cap — consistent with the module's stated goal of constraining the LLM to
compact, deterministic JSON rather than free-form generation. If
`_llm_slot_sql` is still unset after this block (LLM slot-fill unavailable,
low confidence, or compile failure), the pipeline falls through further to
free-form LLM SQL generation for the `RELATIONAL`/`COMPARATIVE`/`FALLBACK`
routes and any remaining `ANALYTICAL` misses.

`slot_fill_meta` provides the provenance dict merged into the SSE
`llm_meta` payload:

```python
def slot_fill_meta(sf: SlotFill, compiled_sql: str) -> dict[str, Any]:
    return {
        "provider": "semantic_layer",
        "model": "rule_compiler" if sf.source == "rule" else "llm_slot_fill",
        "mode": "semantic_layer",
        "metric": sf.metric,
        "dimensions": sf.dimensions,
        "filters": sf.filters,
        "group_mode": sf.group_mode,
        "confidence": sf.confidence,
        "compiled_sql_len": len(compiled_sql),
    }
```

`query_provenance["strategy"]` is set to `"semantic_layer"` for the
rule-based route and `"semantic_layer_llm"` for the LLM-slot-fill route —
these two string constants are what downstream eval tooling
(`ask_eval.py`) keys off to classify routing.

---

## 11. Test and eval usage

There is **no dedicated unit-test file** for `semantic_layer.py` — a
search of `tests/` (`tests/test_ask_pgvector.py`,
`test_ask_pipeline_flags.py`, `test_config_env_loading.py`,
`test_graphdb_sparql.py`, `test_llm_status.py`,
`test_local_embeddings.py`, `test_numeric_gate.py`,
`test_same_parish_fast_path.py`, `test_townland_resolution.py`,
`test_workhouse_entity_resolution.py`) found no file importing
`semantic_layer` — none of the existing `tests/test_*.py` files exercise
`try_rule_based_fill`, `compile_sql`, `compile_sparql`, or
`validate_slot_fill` directly.

All coverage of this module instead comes from `backend/services/ask_eval.py`,
the dissertation's evaluation harness, which imports it directly in
several places:

- **`ask_eval.py:2337`** — imports `compile_sql` for direct compiler
  exercise in the eval harness.
- **`ask_eval.py:2351`** — imports `compile_sparql` (RQ6 SQL-vs-SPARQL
  comparison; see `eval_results/rq6_sql_vs_sparql.md`).
- **`ask_eval.py:2414–2424`** — the harness's own routing simulator:
  imports `try_rule_based_fill` and `compile_sql`, calls
  `try_rule_based_fill(case.question, analysis, resolution)`, and — **mirroring the exact
  `0.80` threshold used in production** — only treats the fill as
  routed via `"semantic_layer"` if `_sl_fill.confidence >= 0.80`. If not,
  it falls through the harness's own simulated chain (verified_analysis →
  template → `template_miss`), matching the real pipeline's fallback
  order.
- **Route-correctness scoring** (`ask_eval.py:2445–2451`): the harness
  treats `semantic_layer` as an acceptable actual route for eval cases
  whose `expected_route` is either `"verified_analysis"` or `"template"`
  (since all three are considered equally "deterministic, non-LLM"
  outcomes for scoring purposes) — only cases with `expected_route ==
  "llm"` require the actual route to be exactly `"template_miss"`.
- **Per-route execution-accuracy metric**: `exec_acc_semantic` is computed
  and reported per eval run (`ask_eval.py:2718`, `3078`, `3424–3426`).

### 11.1 Measured results (from `eval_results/eval_baseline_post_migration.md`)

This is the most recent full baseline eval run found (`baseline_post_migration`,
75 questions: 70 pre-migration cases + 5 new "G-series" out-of-scope
probes):

| Metric | Value |
|---|---|
| Routing accuracy (overall) | 89.3% |
| SQL exec success, `semantic_layer (deterministic)` route | 100.0% |
| SQL exec success, `template` route | 100.0% |
| SQL exec success, `verified_analysis` route | 100.0% |
| Aggregation correctness (overall) | 100.0% |
| LLM calls required | 0 (in this run) |

The per-case table in that report shows every `emi_*`/`evic_*`/`cen_*` case
(emigration, eviction, census questions) resolving to `actual="semantic_layer"`
against an `expected="template"` (or `"verified"`) label — confirming that
in practice the semantic layer's rule-based path (§5) has **superseded**
the older hard-coded template/verified-analysis SQL for the large majority
of analytical question categories it covers, exactly as the `subsumes`
lists in `METRIC_REGISTRY` (§3) declare it is designed to do.

### 11.2 Historical eval finding that shaped the current guards

`ask_eval.py`'s own report-generation code (around its `llm_routed_wrong`
section, ~line 3505–3525) documents, as commentary embedded in the eval
report template, the root cause of an earlier over-routing defect: a
naive first-match keyword scorer let an incidental keyword (e.g. "tenant")
in an otherwise out-of-scope or fallback-expected question force an
incorrect deterministic route, and recommended the fix be "a stricter
confidence threshold in `semantic_layer.try_rule_based_fill` — only accept
the fill when ≥2 keywords match the target metric, or when the question
explicitly names the metric's primary entity." The version of
`try_rule_based_fill` present in the file today does **not** implement the
literal "≥2 keywords" rule as worded, but implements the closely-related
best-match competitive scoring (§5.5) plus the explicit `_OUT_OF_SCOPE_SIGNALS`
and `_UNMAPPED_REQUIREMENT_PHRASES` guards (§5.1–§5.2) and the
widow+emigration intersection guard (§5.3) — all of which post-date, and
directly address, this documented eval finding.
