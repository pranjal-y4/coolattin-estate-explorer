"""
backend/services/semantic_layer.py

Phase 2 — Semantic layer + deterministic query compiler.

Public API
----------
try_rule_based_fill(question, analysis, townland_resolution) -> SlotFill | None
    Attempts to map the question to a slot-fill without any LLM call.
    Returns None if the question is not an analytical/aggregate query.

build_slot_fill_prompt(question, analysis, townland_resolution) -> str
    Returns the tight slot-filling prompt to send to the LLM.
    The LLM MUST return JSON {metric, dimensions, filters, group_mode, confidence}.
    It NEVER writes SQL.

parse_slot_fill(json_text) -> SlotFill | None
    Parses LLM JSON response into a validated SlotFill.

compile_sql(slot_fill, clearances_col) -> str
    Deterministic SQLite compiler.  Never raises; returns None on failure.

compile_sparql(slot_fill) -> str | None
    Equivalent SPARQL aggregate for the local GraphDB co: ontology.
    Returns None when no KG equivalent exists.

validate_slot_fill(slot_fill) -> None
    Raises ValueError if the fill references undefined metrics/dimensions/filters.

Architecture
------------
Questions flow through three layers (in order):

  ① try_rule_based_fill   — keyword + entity pattern matching, zero LLM calls
  ② LLM slot filler       — structured JSON only (via build_slot_fill_prompt)
  ③ Deterministic compiler — parse_slot_fill + compile_sql = guaranteed-valid SQL

Every compiled query goes through _sanitize_and_validate_sql in ask_service before
execution — the FORBIDDEN_SQL guardrail is never bypassed.

Metric / Dimension / Filter vocabulary
---------------------------------------
METRICS  — what to measure (scalar or grouped aggregate)
DIMENSIONS — how to slice / GROUP BY
FILTERS  — WHERE conditions (from resolved entities and extracted values)

All three are declared below as plain dicts (no YAML dependency).  Adding a
new metric requires only an entry in METRIC_REGISTRY and optionally a keyword
in _METRIC_KEYWORDS.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# ── SQL escape helper (mirrors ask_service._sql_escape) ──────────────────────
def _esc(value: str) -> str:
    """Escape single-quote in a value embedded in a SQL string literal."""
    return str(value).replace("'", "''")


# ─────────────────────────────────────────────────────────────────────────────
# Metric registry
# ─────────────────────────────────────────────────────────────────────────────
#
# Each metric entry:
#   from_clause     str  — base FROM (may include a JOIN alias block)
#   aggregate       str  — aggregate expression (no alias)
#   alias           str  — output column alias
#   base_where      str  — metric-inherent WHERE condition ("" if none)
#   dim_select      dict — dimension id → SELECT expression
#   dim_group_by    dict — dimension id → GROUP BY expression
#   filter_where    dict — filter id → WHERE SQL (use {val} placeholder)
#   order_by        str  — default ORDER BY for trend/grouped queries
#   valid_dimensions set
#   valid_filters   set
#   sparql_agg      str|None  — co: SPARQL aggregate if KG equivalent exists
#   keywords        list[str] — trigger substrings (lower-case)
#   subsumes        list[str] — template IDs this metric replaces

METRIC_REGISTRY: dict[str, dict] = {

    # ── Emigration ────────────────────────────────────────────────────────────

    "emigration_count": {
        "label": "People who emigrated",
        "from_clause": "unified_record",
        "aggregate": "COUNT(DISTINCT record_id)",
        "alias": "emigration_count",
        "base_where": "has_emigration_record = 1",
        "dim_select": {
            "year":     "year",
            "townland": "townland",
            "parish":   "parish",
            "ship":     "ship_name",
            "surname":  "surname",
        },
        "dim_group_by": {
            "year":     "year",
            "townland": "townland_norm, townland",
            "parish":   "parish",
            "ship":     "ship_name",
            "surname":  "UPPER(surname), surname",
        },
        "filter_where": {
            "townland":   "townland_norm = '{val}'",
            "year":       "year = {val}",
            "year_range": "year BETWEEN {val[0]} AND {val[1]}",
            "surname":    "UPPER(surname) = '{val}'",
            "is_canada":  "is_canada_destination = 1",
        },
        "order_by": "year",
        "valid_dimensions": {"year", "townland", "parish", "ship", "surname"},
        "valid_filters": {"townland", "year", "year_range", "surname", "is_canada"},
        "sparql_agg": (
            "SELECT (COUNT(DISTINCT ?record) AS ?emigration_count) "
            "WHERE { ?record a co:EstatePerson ; co:hasEmigrationRecord true . {filters} }"
        ),
        "keywords": ["emigra"],
        "subsumes": [
            "emigration_total", "emigration_from_townland", "emigration_per_year",
            "emigration_in_year", "emigration_townland_year", "emigration_by_ship",
            "emigration_by_parish",
        ],
    },

    "canada_emigration_count": {
        "label": "Emigrants to Canada",
        "from_clause": "unified_record",
        "aggregate": "COUNT(DISTINCT record_id)",
        "alias": "canada_emigration_count",
        "base_where": "has_emigration_record = 1 AND is_canada_destination = 1",
        "dim_select": {
            "year":  "year",
            "ship":  "ship_name",
        },
        "dim_group_by": {
            "year":  "year",
            "ship":  "ship_name",
        },
        "filter_where": {
            "year":       "year = {val}",
            "year_range": "year BETWEEN {val[0]} AND {val[1]}",
        },
        "order_by": "canada_emigration_count DESC, year",
        "valid_dimensions": {"year", "ship"},
        "valid_filters": {"year", "year_range"},
        "sparql_agg": None,
        "keywords": ["canada"],
        "subsumes": ["canada_emigration_peak_period", "ship_most_families_canada"],
    },

    # ── Evictions ─────────────────────────────────────────────────────────────

    "eviction_event_count": {
        "label": "Eviction events (from clearances ledger)",
        "from_clause": (
            "clearances_record cr "
            "LEFT JOIN townland t ON cr.townland_id = t.id"
        ),
        "aggregate": "SUM(cr.{eviction_col})",   # {eviction_col} resolved at compile time
        # Use total_evictions (not eviction_count) to avoid collision with the
        # _normalise_schema_compat_sql substitution that rewrites "eviction_count"
        # to the live column name ("count").
        "alias": "total_evictions",
        "base_where": "",
        "dim_select": {
            "year":     "cr.year AS year",
            "townland": "t.name AS townland",
            "parish":   "t.civil_parish AS parish",
        },
        "dim_group_by": {
            "year":     "cr.year",
            "townland": "t.id, t.name",
            "parish":   "t.civil_parish",
        },
        "filter_where": {
            "townland": "UPPER(t.name) = '{val}'",
            "year":     "cr.year = {val}",
            "year_range": "cr.year BETWEEN {val[0]} AND {val[1]}",
        },
        "order_by": "eviction_count DESC",
        "valid_dimensions": {"year", "townland", "parish"},
        "valid_filters": {"townland", "year", "year_range"},
        "sparql_agg": (
            "SELECT (SUM(?count) AS ?eviction_count) "
            "WHERE { ?ev a co:Clearance ; co:year ?year ; co:count ?count . {filters} }"
        ),
        "keywords": ["evict", "clearance"],
        "subsumes": [
            "eviction_total", "eviction_from_townland", "eviction_per_year",
            "eviction_in_year", "eviction_worst_year", "eviction_per_townland",
            "clearances_total_records",
        ],
    },

    "evicted_person_count": {
        "label": "People with eviction records",
        "from_clause": "unified_record",
        "aggregate": "COUNT(DISTINCT record_id)",
        "alias": "evicted_person_count",
        "base_where": "has_eviction_record = 1",
        "dim_select": {
            "year":     "year",
            "townland": "townland",
            "parish":   "parish",
        },
        "dim_group_by": {
            "year":     "year",
            "townland": "townland_norm, townland",
            "parish":   "parish",
        },
        "filter_where": {
            "townland": "townland_norm = '{val}'",
            "year":     "year = {val}",
        },
        "order_by": "year",
        "valid_dimensions": {"year", "townland", "parish"},
        "valid_filters": {"townland", "year"},
        "sparql_agg": None,
        "keywords": ["evict"],
        "subsumes": ["eviction_people", "eviction_people_townland"],
    },

    # ── Census / Population ───────────────────────────────────────────────────

    "population": {
        "label": "Census population",
        "from_clause": "census_record c JOIN townland t ON c.townland_id = t.id",
        "aggregate": "SUM(c.total)",
        "alias": "population",
        "base_where": "",
        "dim_select": {
            "year":     "c.year AS year",
            "townland": "t.name AS townland",
            "parish":   "t.civil_parish AS parish",
            "barony":   "t.barony AS barony",
        },
        "dim_group_by": {
            "year":     "c.year",
            "townland": "t.id, t.name",
            "parish":   "t.civil_parish",
            "barony":   "t.barony",
        },
        "filter_where": {
            "townland":   "UPPER(t.name) = '{val}'",
            "year":       "c.year = {val}",
            "year_range": "c.year BETWEEN {val[0]} AND {val[1]}",
        },
        "order_by": "c.year",
        "valid_dimensions": {"year", "townland", "parish", "barony"},
        "valid_filters": {"townland", "year", "year_range"},
        "sparql_agg": (
            "SELECT ?year (SUM(?total) AS ?population) "
            "WHERE { ?census a co:CensusRecord ; co:year ?year ; co:totalPopulation ?total . {filters} } "
            "GROUP BY ?year ORDER BY ?year"
        ),
        "keywords": ["population", "census", "populated", "inhabitant"],
        "subsumes": [
            "census_total_year", "census_population_townland_year",
            "census_population_townland", "census_all_years_trend",
            "census_decline_famine", "census_by_parish",
            "census_1841", "census_1851", "population_trend_1841_1861",
        ],
    },

    "population_change": {
        "label": "Population change between two census years",
        "from_clause": (
            "census_record a "
            "JOIN census_record b ON a.townland_id = b.townland_id "
            "JOIN townland t ON a.townland_id = t.id"
        ),
        "aggregate": "SUM(b.total) - SUM(a.total)",
        "alias": "population_change",
        "base_where": "",   # year filters required
        "dim_select": {
            "townland": "t.name AS townland",
            "parish":   "t.civil_parish AS parish",
        },
        "dim_group_by": {
            "townland": "t.id, t.name",
            "parish":   "t.civil_parish",
        },
        "filter_where": {
            "year_a": "a.year = {val}",
            "year_b": "b.year = {val}",
        },
        "order_by": "population_change ASC",
        "valid_dimensions": {"townland", "parish"},
        "valid_filters": {"year_a", "year_b", "townland"},
        "sparql_agg": None,
        "keywords": ["decline", "change", "fell", "drop", "loss", "decrease"],
        "subsumes": ["census_decline_famine"],
    },

    "uninhabited_houses": {
        "label": "Uninhabited houses in census",
        "from_clause": "census_record c JOIN townland t ON c.townland_id = t.id",
        "aggregate": "SUM(c.uninhabited)",
        "alias": "uninhabited_houses",
        "base_where": "c.uninhabited > 0",
        "dim_select": {
            "year":     "c.year AS year",
            "townland": "t.name AS townland",
        },
        "dim_group_by": {
            "year":     "c.year",
            "townland": "t.id, t.name",
        },
        "filter_where": {
            "year":     "c.year = {val}",
            "townland": "UPPER(t.name) = '{val}'",
        },
        "order_by": "uninhabited_houses DESC",
        "valid_dimensions": {"year", "townland"},
        "valid_filters": {"year", "townland"},
        "sparql_agg": None,
        "keywords": ["uninhabit"],
        "subsumes": ["census_uninhabited", "census_houses"],
    },

    # ── Tenancy ───────────────────────────────────────────────────────────────

    "tenancy_count": {
        "label": "Tenants recorded",
        "from_clause": "unified_record",
        "aggregate": "COUNT(DISTINCT record_id)",
        "alias": "tenancy_count",
        "base_where": "has_tenancy_record = 1",
        "dim_select": {
            "year":     "year",
            "townland": "townland",
            "parish":   "parish",
        },
        "dim_group_by": {
            "year":     "year",
            "townland": "townland_norm, townland",
            "parish":   "parish",
        },
        "filter_where": {
            "townland": "townland_norm = '{val}'",
            "year":     "year = {val}",
        },
        "order_by": "tenancy_count DESC",
        "valid_dimensions": {"year", "townland", "parish"},
        "valid_filters": {"townland", "year"},
        "sparql_agg": None,
        "keywords": ["tenant", "tenancy"],
        "subsumes": ["tenants_total", "tenants_per_townland"],
    },

    "avg_holding_acres": {
        "label": "Average tenant landholding in acres",
        "from_clause": "unified_record",
        "aggregate": "ROUND(AVG(holding_acres), 2)",
        "alias": "avg_holding_acres",
        "base_where": "has_tenancy_record = 1 AND holding_acres IS NOT NULL",
        "dim_select": {
            "gender":   "CASE WHEN UPPER(gender) IN ('M','MALE') THEN 'Male' WHEN UPPER(gender) IN ('F','FEMALE') THEN 'Female' ELSE 'Unknown' END AS gender_group",
            "townland": "townland",
            "year":     "year",
        },
        "dim_group_by": {
            "gender":   "gender_group",
            "townland": "townland_norm, townland",
            "year":     "year",
        },
        "filter_where": {
            "townland": "townland_norm = '{val}'",
            "gender":   "UPPER(COALESCE(gender,'')) IN ('M','MALE','F','FEMALE')",
        },
        "order_by": "avg_holding_acres DESC",
        "valid_dimensions": {"gender", "townland", "year"},
        "valid_filters": {"townland", "gender"},
        "sparql_agg": None,
        "keywords": ["holding", "acr", "land"],
        "subsumes": ["tenant_land_gender_average", "largest_latest_tenant_holdings"],
    },

    # ── People / Demographics ─────────────────────────────────────────────────

    "widow_count": {
        "label": "Widow records",
        "from_clause": "unified_record",
        "aggregate": "COUNT(DISTINCT record_id)",
        "alias": "widow_count",
        "base_where": "is_widow = 1",
        "dim_select": {
            "townland": "townland",
            "year":     "year",
        },
        "dim_group_by": {
            "townland": "townland_norm, townland",
            "year":     "year",
        },
        "filter_where": {
            "townland": "townland_norm = '{val}'",
            "year":     "year = {val}",
        },
        "order_by": "widow_count DESC",
        "valid_dimensions": {"townland", "year"},
        "valid_filters": {"townland", "year"},
        "sparql_agg": None,
        "keywords": ["widow"],
        "subsumes": [
            "widows_count", "widows_with_children_proportion",
            "widows_eviction_proportion",
        ],
    },

    "person_count": {
        "label": "People in records",
        "from_clause": "unified_record",
        "aggregate": "COUNT(DISTINCT record_id)",
        "alias": "person_count",
        "base_where": "",
        "dim_select": {
            "year":     "year",
            "townland": "townland",
            "parish":   "parish",
            "surname":  "surname",
            "role":     "role",
        },
        "dim_group_by": {
            "year":     "year",
            "townland": "townland_norm, townland",
            "parish":   "parish",
            "surname":  "UPPER(surname), surname",
            "role":     "role",
        },
        "filter_where": {
            "townland":    "townland_norm = '{val}'",
            "year":        "year = {val}",
            "surname":     "UPPER(surname) = '{val}'",
            "is_widow":    "is_widow = 1",
            "is_emigrant": "has_emigration_record = 1",
            "is_evicted":  "has_eviction_record = 1",
            "is_tenant":   "has_tenancy_record = 1",
        },
        "order_by": "person_count DESC",
        "valid_dimensions": {"year", "townland", "parish", "surname", "role"},
        "valid_filters": {
            "townland", "year", "surname",
            "is_widow", "is_emigrant", "is_evicted", "is_tenant",
        },
        "sparql_agg": None,
        "keywords": ["people", "record", "person"],
        "subsumes": ["people_all_records", "records_overview", "records_per_year"],
    },

    # ── Geography ─────────────────────────────────────────────────────────────

    "townland_count": {
        "label": "Number of townlands",
        "from_clause": "townland",
        "aggregate": "COUNT(*)",
        "alias": "townland_count",
        "base_where": "",
        "dim_select": {
            "county":  "county",
            "barony":  "barony",
            "parish":  "civil_parish AS parish",
        },
        "dim_group_by": {
            "county":  "county",
            "barony":  "barony",
            "parish":  "civil_parish",
        },
        "filter_where": {
            "county":  "county = '{val}'",
            "barony":  "barony = '{val}'",
            "parish":  "civil_parish = '{val}'",
        },
        "order_by": "townland_count DESC",
        "valid_dimensions": {"county", "barony", "parish"},
        "valid_filters": {"county", "barony", "parish"},
        "sparql_agg": None,
        "keywords": ["townland"],
        "subsumes": ["townlands_total_count", "townlands_by_county", "barony_list"],
    },

    "parish_count": {
        "label": "Number of civil parishes",
        "from_clause": "townland",
        "aggregate": "COUNT(DISTINCT civil_parish)",
        "alias": "parish_count",
        "base_where": "civil_parish IS NOT NULL AND TRIM(civil_parish) != ''",
        "dim_select": {},
        "dim_group_by": {},
        "filter_where": {},
        "order_by": "",
        "valid_dimensions": set(),
        "valid_filters": set(),
        "sparql_agg": None,
        "keywords": ["parish"],
        "subsumes": ["parishes_count"],
    },

    # ── Townland attribute lookup (not an aggregate — returns a detail row) ───

    "townland_attribute": {
        "label": "Townland attributes (parish, barony, coordinates)",
        "from_clause": "townland",
        "aggregate": "",   # not an aggregate; compiler uses SELECT *
        "alias": "",
        "base_where": "",
        "dim_select": {},
        "dim_group_by": {},
        "filter_where": {
            "townland": "UPPER(name) = '{val}'",
        },
        "order_by": "",
        "valid_dimensions": set(),
        "valid_filters": {"townland"},
        "sparql_agg": (
            "SELECT ?name ?parish ?barony ?county "
            "WHERE { ?t a co:Townland ; rdfs:label ?name ; co:civilParish ?parish ; "
            "co:barony ?barony ; co:county ?county . {filters} }"
        ),
        "keywords": ["parish", "barony", "county", "about", "detail", "where", "located"],
        "subsumes": ["townland_parish_lookup", "townland_details"],
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Dimension registry (informational — for validation and routing)
# ─────────────────────────────────────────────────────────────────────────────

DIMENSION_REGISTRY: dict[str, dict] = {
    "year":     {"label": "Year",          "type": "integer",  "table_col": "year"},
    "townland": {"label": "Townland",      "type": "string",   "table_col": "townland_norm"},
    "parish":   {"label": "Civil parish",  "type": "string",   "table_col": "civil_parish"},
    "barony":   {"label": "Barony",        "type": "string",   "table_col": "barony"},
    "county":   {"label": "County",        "type": "string",   "table_col": "county"},
    "gender":   {"label": "Gender",        "type": "category", "table_col": "gender"},
    "ship":     {"label": "Ship",          "type": "string",   "table_col": "ship_name"},
    "surname":  {"label": "Surname",       "type": "string",   "table_col": "surname"},
    "role":     {"label": "Role",          "type": "category", "table_col": "role"},
    "destination": {"label": "Destination", "type": "string",  "table_col": "arrival"},
    "occupation":  {"label": "Occupation",  "type": "string",  "table_col": "occupation"},
}

# Valid filter keys across all metrics
_ALL_FILTER_KEYS = frozenset({
    "townland", "year", "year_range", "year_a", "year_b",
    "surname", "gender", "parish", "barony", "county",
    "is_canada", "is_widow", "is_emigrant", "is_evicted", "is_tenant",
})


# ─────────────────────────────────────────────────────────────────────────────
# SlotFill dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SlotFill:
    """
    The output of the slot-filler (rule-based or LLM).
    Passed verbatim into compile_sql / compile_sparql.
    """
    metric: str                          # must be a key in METRIC_REGISTRY
    dimensions: list[str] = field(default_factory=list)
    filters: dict[str, Any] = field(default_factory=dict)
    group_mode: str = "aggregate"        # "aggregate"|"trend"|"list"|"detail"
    limit: int | None = 50
    order_by_override: str | None = None # overrides metric's default order
    confidence: float = 1.0              # 1.0 rule-based; 0.0–1.0 for LLM-filled
    source: str = "rule"                 # "rule" | "llm"
    raw_intent: str = ""                 # original question for provenance


# ─────────────────────────────────────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────────────────────────────────────

def validate_slot_fill(sf: SlotFill) -> None:
    """
    Raise ValueError if the slot fill references undefined metrics, dimensions,
    or filters.  Called before compile_sql to guarantee the compiler never
    receives invalid input.
    """
    if sf.metric not in METRIC_REGISTRY:
        raise ValueError(f"Unknown metric '{sf.metric}'. Valid: {sorted(METRIC_REGISTRY)}")

    m = METRIC_REGISTRY[sf.metric]
    for dim in sf.dimensions:
        if dim not in m["valid_dimensions"]:
            raise ValueError(
                f"Metric '{sf.metric}' does not support dimension '{dim}'. "
                f"Valid: {sorted(m['valid_dimensions'])}"
            )
    for filt in sf.filters:
        if filt not in m["valid_filters"]:
            raise ValueError(
                f"Metric '{sf.metric}' does not support filter '{filt}'. "
                f"Valid: {sorted(m['valid_filters'])}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# SQL compiler
# ─────────────────────────────────────────────────────────────────────────────

def compile_sql(sf: SlotFill, clearances_col: str = "count") -> str | None:
    """
    Deterministic SQLite compiler.

    Takes a validated SlotFill and returns a complete, read-only SELECT/WITH
    statement.  Returns None if compilation fails (caller should fall back).

    clearances_col is the runtime-detected column name in clearances_record
    (either "count" or "eviction_count" depending on schema version).
    """
    try:
        validate_slot_fill(sf)
        m = METRIC_REGISTRY[sf.metric]

        # ── Attribute lookup (non-aggregate) ──────────────────────────────
        if sf.metric == "townland_attribute":
            return _compile_attribute_lookup(sf, m)

        # ── SELECT clause ──────────────────────────────────────────────────
        select_parts: list[str] = []
        for dim in sf.dimensions:
            expr = m["dim_select"].get(dim, dim)
            select_parts.append(expr)

        agg_expr = m["aggregate"].replace("{eviction_col}", clearances_col)
        alias = m["alias"]
        select_parts.append(f"{agg_expr} AS {alias}")
        select_sql = ", ".join(select_parts)

        # ── FROM clause ────────────────────────────────────────────────────
        from_sql = m["from_clause"]

        # ── WHERE clause ───────────────────────────────────────────────────
        where_parts: list[str] = []
        if m["base_where"]:
            where_parts.append(m["base_where"])

        for filt_key, filt_val in sf.filters.items():
            tpl = m["filter_where"].get(filt_key)
            if not tpl:
                continue
            where_parts.append(_render_filter(tpl, filt_val))

        where_sql = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""

        # ── GROUP BY ───────────────────────────────────────────────────────
        group_parts: list[str] = []
        for dim in sf.dimensions:
            gb = m["dim_group_by"].get(dim)
            if gb:
                group_parts.append(gb)
        group_sql = (" GROUP BY " + ", ".join(group_parts)) if group_parts else ""

        # ── ORDER BY ───────────────────────────────────────────────────────
        order = sf.order_by_override or (m["order_by"] if sf.dimensions else "")
        order_sql = (f" ORDER BY {order}") if order else ""

        # ── LIMIT ─────────────────────────────────────────────────────────
        # No LIMIT for simple scalar aggregates (0 or 1 dimensions)
        limit_n = sf.limit
        if not sf.dimensions:
            limit_n = None
        limit_sql = (f" LIMIT {int(limit_n)}") if limit_n else ""

        sql = f"SELECT {select_sql} FROM {from_sql}{where_sql}{group_sql}{order_sql}{limit_sql}"
        return sql

    except Exception as exc:
        log.warning("semantic_layer.compile_sql_failed metric=%s error=%s", sf.metric, exc)
        return None


def _render_filter(template: str, val: Any) -> str:
    """Replace {val} or {val[0]}/{val[1]} in a filter template."""
    if isinstance(val, (list, tuple)) and "{val[0]}" in template:
        return template.replace("{val[0]}", str(val[0])).replace("{val[1]}", str(val[1]))
    if isinstance(val, str):
        return template.replace("{val}", _esc(val))
    return template.replace("{val}", str(val))


def _compile_attribute_lookup(sf: SlotFill, m: dict) -> str | None:
    """Compile a non-aggregate townland attribute SELECT."""
    townland = sf.filters.get("townland")
    if not townland:
        return None
    return (
        "SELECT name, civil_parish, barony, county, centroid_lat, centroid_lon "
        f"FROM townland WHERE UPPER(name) = '{_esc(str(townland))}'"
    )


# ─────────────────────────────────────────────────────────────────────────────
# SPARQL compiler (local GraphDB co: ontology)
# ─────────────────────────────────────────────────────────────────────────────

def compile_sparql(sf: SlotFill) -> str | None:
    """
    Compile a SPARQL aggregate for the local co: ontology.
    Returns None when no KG equivalent exists or GraphDB is not available.
    The returned string is a bare SPARQL SELECT (PREFIX declarations are
    added by graphdb_sparql.query).
    """
    try:
        m = METRIC_REGISTRY.get(sf.metric)
        if not m or not m.get("sparql_agg"):
            return None

        sparql_tpl: str = m["sparql_agg"]

        # Build filter triples
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


# ─────────────────────────────────────────────────────────────────────────────
# Rule-based slot filler (deterministic fast lane — zero LLM calls)
# ─────────────────────────────────────────────────────────────────────────────

# Keyword → metric candidates (ordered by specificity)
_METRIC_KEYWORDS: list[tuple[str, str]] = [
    # (substring to match in lowercased question, metric_id)
    # Most specific first to avoid false positives
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

# Words that trigger "how many" aggregate mode
_COUNT_WORDS = frozenset(["how many", "count", "total", "number of"])

# Words that trigger GROUP BY year (trend)
_TREND_WORDS = frozenset([
    "per year", "by year", "each year", "over time", "trend",
    "yearly", "annual", "year by year",
])

# Words that trigger GROUP BY townland
_BY_TOWNLAND_WORDS = frozenset(["per townland", "by townland", "each townland"])

# Words that signal a geography/attribute question (not aggregate)
_ATTRIBUTE_WORDS = frozenset([
    "which parish", "what parish", "which barony", "what barony",
    "which county", "what county", "located in", "where is", "belong to",
    "same parish", "fall within",
])


def try_rule_based_fill(
    question: str,
    analysis: dict[str, Any],
    townland_resolution: dict[str, Any],
) -> SlotFill | None:
    """
    Attempt to map the question to a SlotFill without any LLM call.

    Returns None if:
    - The question is not analytical (it's a list/people/geography detail query)
    - No metric can be identified with high confidence

    Uses resolved entity from townland_resolution (sql_id, name_norm) and
    the question analysis dict from _analyse_question in ask_service.
    """
    q = (question or "").lower()

    # ── Geography attribute lookup ─────────────────────────────────────────
    townland_norm = townland_resolution.get("name_norm")

    # Fix 2: suppress townland filter when the resolved entity is used as an
    # estate-scope qualifier (e.g. "the Coolattin estate") rather than naming a
    # specific townland.  Guard: "from Ballinacor in the Coolattin estate" still
    # filters to BALLINACOR because raw_text="Ballinacor" ≠ "Coolattin estate".
    _raw_text = townland_resolution.get("raw_text") or ""
    if _raw_text and re.search(
        r"\b" + re.escape(_raw_text) + r"\s+estate\b",
        question,
        re.IGNORECASE,
    ):
        townland_norm = None

    if any(w in q for w in _ATTRIBUTE_WORDS) and townland_norm:
        return SlotFill(
            metric="townland_attribute",
            filters={"townland": townland_norm},
            group_mode="detail",
            limit=None,
            confidence=0.92,
            source="rule",
            raw_intent=question,
        )

    # ── Detect metric ──────────────────────────────────────────────────────
    metric_id: str | None = None
    for keyword, candidate in _METRIC_KEYWORDS:
        if keyword in q:
            # For eviction: disambiguate event-count vs person-count
            if candidate == "eviction_event_count":
                if any(w in q for w in ["people", "person", "who", "names", "list"]):
                    candidate = "evicted_person_count"
            metric_id = candidate
            break

    # Special rule: "how many townlands" → townland_count
    if not metric_id and "townland" in q and any(w in q for w in ["how many", "count", "total", "number"]):
        metric_id = "townland_count"

    # Special rule: "how many parishes" → parish_count
    if not metric_id and "parish" in q and any(w in q for w in ["how many", "count", "total"]):
        metric_id = "parish_count"

    # Special rule: "how many people"/"how many records" with surname → person_count
    if not metric_id:
        surname = analysis.get("surname")
        wants_count = analysis.get("output_mode") in {"count", "grouped"} or any(w in q for w in _COUNT_WORDS)
        if surname and wants_count:
            metric_id = "person_count"

    if not metric_id:
        return None

    m = METRIC_REGISTRY[metric_id]

    # ── Detect filters ─────────────────────────────────────────────────────
    filters: dict[str, Any] = {}

    # Townland filter from Phase 1 resolution
    if townland_norm and "townland" in m["valid_filters"]:
        filters["townland"] = townland_norm

    # Year filter
    year = analysis.get("year")
    if year and "year" in m["valid_filters"]:
        filters["year"] = int(year)

    # Year-range filter for known Famine years
    if any(w in q for w in ["1841", "1851", "famine", "decline"]):
        if "1861" in q and "1841" in q:
            filters.pop("year", None)
            if "year_range" in m["valid_filters"]:
                filters["year_range"] = [1841, 1861]
        elif "1851" in q and "1841" in q:
            filters.pop("year", None)
            if metric_id == "population_change":
                filters["year_a"] = 1841
                filters["year_b"] = 1851
            elif "year_range" in m["valid_filters"]:
                filters["year_range"] = [1841, 1851]

    # Surname filter
    surname = analysis.get("surname")
    if surname and "surname" in m["valid_filters"]:
        filters["surname"] = str(surname).upper()

    # Gender filter
    gender = _detect_gender(q)
    if gender and "gender" in m["valid_filters"]:
        filters["gender"] = gender

    # is_canada
    if ("canada" in q) and "is_canada" in m["valid_filters"] and metric_id == "emigration_count":
        filters["is_canada"] = True

    # ── Detect dimensions ──────────────────────────────────────────────────
    dimensions: list[str] = []

    # Trend dimension — "per year", "by year", "which year had the most", etc.
    _year_dim_triggers = _TREND_WORDS | {
        "which year", "what year", "what years",
        "all years", "all census", "each census", "over the years",
    }
    if any(w in q for w in _year_dim_triggers) and "year" in m["valid_dimensions"]:
        if "year" not in filters:   # don't group if year is already a filter
            dimensions.append("year")

    # By-townland dimension
    if any(w in q for w in _BY_TOWNLAND_WORDS) and "townland" in m["valid_dimensions"]:
        dimensions.append("townland")

    # Gender grouping for holding query
    if metric_id == "avg_holding_acres" and any(
        w in q for w in ["male", "female", "gender", "men", "women"]
    ) and "gender" in m["valid_dimensions"]:
        if "gender" not in dimensions:
            dimensions.append("gender")
        if "gender" in filters:
            del filters["gender"]   # don't filter AND group by same field

    # Parish grouping
    if any(w in q for w in ["by parish", "per parish", "each parish"]) and "parish" in m["valid_dimensions"]:
        dimensions.append("parish")

    # For ship queries
    if metric_id in ("emigration_count", "canada_emigration_count") and "ship" in q and "ship" in m["valid_dimensions"]:
        dimensions.append("ship")

    # ── Determine group_mode and limit ─────────────────────────────────────
    group_mode = "aggregate"
    limit: int | None = 50

    if dimensions:
        group_mode = "trend" if "year" in dimensions else "grouped"
    elif not filters or len(filters) == 1 and "townland" in filters:
        limit = None   # simple scalar — no LIMIT

    # For "worst year" / "highest" queries, limit to top rows
    if any(w in q for w in ["worst", "most", "highest", "peak", "top", "largest", "smallest"]):
        limit = 10

    # ── Confidence scoring ────────────────────────────────────────────────
    # Start at 1.0; reduce if multiple metrics could match
    confidence = 1.0
    matching_keywords = sum(1 for kw, _ in _METRIC_KEYWORDS if kw in q)
    if matching_keywords > 1:
        confidence = max(0.82, confidence - 0.06 * (matching_keywords - 1))
    if not filters and not dimensions:
        confidence = min(confidence, 0.90)  # global unfiltered queries are less certain

    return SlotFill(
        metric=metric_id,
        dimensions=dimensions,
        filters=filters,
        group_mode=group_mode,
        limit=limit,
        confidence=confidence,
        source="rule",
        raw_intent=question,
    )


def _detect_gender(q: str) -> str | None:
    if any(w in q for w in ["female", "women", "woman"]):
        return "female"
    if any(w in q for w in ["male", "men", "man"]):
        return "male"
    return None


# ─────────────────────────────────────────────────────────────────────────────
# LLM slot-fill prompt builder
# ─────────────────────────────────────────────────────────────────────────────

def build_slot_fill_prompt(
    question: str,
    analysis: dict[str, Any],
    townland_resolution: dict[str, Any],
) -> str:
    """
    Build the prompt sent to the LLM for slot filling.
    The LLM returns ONLY JSON — no SQL, no prose.
    """
    townland_hint = (
        f"Resolved townland: {townland_resolution['name_norm']} "
        f"(sql_id={townland_resolution.get('sql_id')}, "
        f"kg_uri={townland_resolution.get('kg_uri', 'N/A')})"
        if townland_resolution.get("matched")
        else "No townland resolved."
    )
    year = analysis.get("year") or "none"
    surname = analysis.get("surname") or "none"

    metric_list = "\n".join(
        f"  {mid}: {m['label']}" for mid, m in METRIC_REGISTRY.items()
    )
    dim_list = ", ".join(sorted(DIMENSION_REGISTRY.keys()))
    filter_list = ", ".join(sorted(_ALL_FILTER_KEYS))

    return f"""You are a slot filler for a historical estate records query system.
Extract query intent as JSON. Do NOT write SQL. Do NOT guess metric names.

Question: {question}
{townland_hint}
Extracted year: {year}  |  Extracted surname: {surname}

Available metrics (use EXACT ids):
{metric_list}

Available dimensions: {dim_list}
Available filters: {filter_list}

Return ONLY this JSON (no markdown, no explanation):
{{
  "metric": "<metric_id or null if not analytical>",
  "dimensions": ["<dim1>", ...],
  "filters": {{
    "townland": "<UPPER_NORM or null>",
    "year": <integer or null>,
    "year_range": [<start>, <end>] or null,
    "surname": "<UPPER or null>",
    "gender": "<male|female or null>",
    "is_canada": <true|false or null>,
    "is_widow": <true|false or null>
  }},
  "group_mode": "aggregate|trend|grouped|detail",
  "limit": <integer or null>,
  "confidence": <0.0-1.0>
}}

Rules:
- metric MUST be one of the listed ids (or null for non-analytical questions like lists or details)
- dimensions and filters must be valid for the chosen metric
- confidence < 0.75 means uncertain — the system will fall back to template matching
- For "per year" / "trend" questions: add "year" to dimensions
- For "by townland" questions: add "townland" to dimensions
- Omit null values from the filters dict""".strip()


def parse_slot_fill(raw_text: str, question: str = "") -> SlotFill | None:
    """
    Parse an LLM JSON response into a SlotFill.
    Returns None if the response is invalid or metric is null/unknown.
    Never raises.
    """
    try:
        # Strip markdown fences
        text = re.sub(r"```(?:json)?\s*", "", raw_text, flags=re.IGNORECASE).strip()
        text = re.sub(r"```\s*$", "", text).strip()
        data = json.loads(text)
    except Exception as exc:
        log.debug("semantic_layer.parse_slot_fill json_failed error=%s raw=%r", exc, raw_text[:100])
        return None

    metric = data.get("metric")
    if not metric or metric not in METRIC_REGISTRY:
        return None

    confidence = float(data.get("confidence", 0.5))
    if confidence < 0.6:
        return None

    raw_filters = data.get("filters") or {}
    filters: dict[str, Any] = {
        k: v for k, v in raw_filters.items() if v is not None
    }

    sf = SlotFill(
        metric=metric,
        dimensions=list(data.get("dimensions") or []),
        filters=filters,
        group_mode=str(data.get("group_mode") or "aggregate"),
        limit=data.get("limit"),
        confidence=confidence,
        source="llm",
        raw_intent=question,
    )

    try:
        validate_slot_fill(sf)
    except ValueError as exc:
        log.debug("semantic_layer.parse_slot_fill invalid slot_fill=%s", exc)
        return None

    return sf


# ─────────────────────────────────────────────────────────────────────────────
# Provenance metadata for the SSE result payload
# ─────────────────────────────────────────────────────────────────────────────

def slot_fill_meta(sf: SlotFill, compiled_sql: str) -> dict[str, Any]:
    """Return provenance dict added to llm_meta in the SSE result."""
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
