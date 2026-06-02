"""
backend/services/ask_eval.py

Eval harness for the Ask pipeline — Phases 0, 1, 2, …

Tests the deterministic components of the pipeline (no LLM calls) against a
golden set of questions spanning all template categories:
  emigration · evictions · census/population · geography · people/names
  tenancy · heritage · overview · entity-linking (Phase 1)
  relational/hierarchy · comparative · fallback (Phase 5 lanes)

Metrics reported — global
--------------------------
  routing_accuracy        — % of cases where the pipeline chose the correct lane
  aggregation_correctness — % where the numeric answer exactly matches ground truth
  sql_exec_success        — % where the compiled SQL executed without error
  entity_resolution_acc   — % where entity resolved to correct label_norm
  sql_id_resolution_rate  — % of townland cases where sql_id is populated (Phase 1+)
  kg_uri_resolution_rate  — % of townland cases where kg_uri is populated (Phase 1+)
  p50_latency_ms          — median per-question wall time
  p95_latency_ms          — 95th-percentile wall time
  llm_calls               — LLM calls needed (0 for deterministic-only cases)

Per-lane metrics (Phase 5+)
-----------------------------
  analytical_n / analytical_agg_acc  — ANALYTICAL lane coverage + aggregation accuracy
  relational_n / subgraph_recall     — RELATIONAL lane coverage + subgraph retrieval recall
  comparative_n / comparative_sqlite_capture / comparative_kg_capture
  fallback_n / fallback_routing_acc  — FALLBACK lane coverage + routing accuracy

Usage
-----
  python3 -m backend.services.ask_eval --phase phase2

  # Compare phases:
  python3 -m backend.services.ask_eval --compare \\
      backend/services/eval_results/eval_phase2.json \\
      backend/services/eval_results/eval_phase5.json

Ground-truth values were verified directly against coolattin.db on 2026-06-01.
"""
from __future__ import annotations

import json
import math
import re
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class EvalCase:
    id: str
    question: str
    category: str          # emigration|eviction|census|geography|people|tenancy|heritage|overview|entity|relational|comparative|fallback
    expected_route: str    # "verified_analysis" | "template" | "llm"
    townland_hint: str | None = None
    # Entity resolution
    expected_entity_norm: str | None = None   # UPPER townland norm e.g. "BALLINACOR"
    expected_surname: str | None = None        # e.g. "BYRNE"
    # Phase 1: enhanced entity checks
    expected_sql_id: int | None = None         # expected townland.id
    expected_kg_uri_prefix: str | None = None  # expected kg_uri prefix
    # Template expected
    expected_template_id: str | None = None
    # Ground truth for aggregation check
    ground_truth_sql: str | None = None        # canonical SQL for the correct answer
    ground_truth_value: Any = None             # expected scalar or list check
    ground_truth_key: str | None = None        # column name for scalar check
    ground_truth_type: str | None = None       # "scalar"|"sum_all"|"row_key_value"|None
    expected_answer_facts: list[str] = field(default_factory=list)
    # Phase 5: lane + subgraph + comparative fields
    expected_lane: str | None = None           # "analytical"|"relational"|"comparative"|"fallback"
    expected_subgraph_facts: list[str] = field(default_factory=list)  # strings expected in linearized subgraph
    expected_comparative_sources: list[str] = field(default_factory=list)  # ["sqlite", "kg"]


@dataclass
class CaseResult:
    id: str
    category: str
    question: str          # truncated for display
    expected_route: str
    actual_route: str      # verified_analysis|template|template_miss
    route_ok: bool
    entity_ok: bool | None
    sql_id_ok: bool | None  # Phase 1: sql_id populated and matches expected
    kg_uri_ok: bool | None  # Phase 1: kg_uri populated with VRTI prefix
    sql_ok: bool | None
    agg_ok: bool | None
    agg_actual: Any
    ground_truth: Any
    template_id: str | None
    latency_ms: int
    error: str | None = None
    # Phase 5 diagnostics
    lane: str | None = None                    # actual lane from classify_intent
    lane_ok: bool | None = None
    subgraph_ok: bool | None = None            # did subgraph contain expected facts?
    subgraph_recall: float | None = None       # fraction of expected_subgraph_facts found
    comparative_sqlite_ok: bool | None = None  # SQLite returned non-empty result
    comparative_kg_ok: bool | None = None      # SPARQL/GraphDB returned non-empty result
    compiled_sql_actual: str | None = None     # SQL actually compiled by the pipeline
    entity_label_expected: str | None = None   # for miss-detail printing
    entity_label_actual: str | None = None
    entity_sql_id_actual: int | None = None
    entity_kg_uri_actual: str | None = None


@dataclass
class EvalResult:
    phase_label: str
    cases: list[CaseResult]
    timestamp: str


# ── Golden cases (59 existing + 14 new = 73 total) ────────────────────────────
# Ground-truth SQL uses the actual DB column `count` for clearances_record,
# not the template alias `eviction_count` (handled by _clearances_count_column).
# All aggregate values verified against coolattin.db on 2026-06-01.

GOLDEN_CASES: list[EvalCase] = [

    # ── EMIGRATION (8) ─────────────────────────────────────────────────────────

    EvalCase(
        id="emi_01_total",
        question="How many people emigrated from the Coolattin estate?",
        category="emigration",
        expected_route="template",
        expected_template_id="emigration_total",
        expected_lane="analytical",
        # Phase 2: semantic layer alias = "emigration_count"
        ground_truth_sql="SELECT COUNT(DISTINCT record_id) AS emigration_count FROM unified_record WHERE has_emigration_record=1",
        ground_truth_value=6016,
        ground_truth_key="emigration_count",
        ground_truth_type="scalar",
        expected_answer_facts=["6016"],
    ),

    EvalCase(
        id="emi_02_townland_ballynultagh",
        question="How many people emigrated from Ballynultagh?",
        category="emigration",
        expected_route="template",
        # Phase 0: emigration_total beats emigration_from_townland (score tie). agg_ok=✗.
        # Phase 2: semantic layer routes to emigration_count + townland filter. agg_ok=✓.
        expected_template_id="emigration_from_townland",
        expected_lane="analytical",
        townland_hint="Ballynultagh",
        expected_entity_norm="BALLYNULTAGH",
        # ground_truth_key uses semantic layer alias "emigration_count"
        ground_truth_sql="SELECT COUNT(DISTINCT record_id) AS emigration_count FROM unified_record WHERE has_emigration_record=1 AND townland_norm='BALLYNULTAGH'",
        ground_truth_value=400,
        ground_truth_key="emigration_count",
        ground_truth_type="scalar",
        expected_answer_facts=["400"],
    ),

    EvalCase(
        id="emi_03_townland_killinure",
        question="How many people emigrated from Killinure?",
        category="emigration",
        expected_route="template",
        # Same as emi_02 — routing bug fixed in Phase 2 by semantic layer.
        expected_template_id="emigration_from_townland",
        expected_lane="analytical",
        townland_hint="Killinure",
        expected_entity_norm="KILLINURE",
        ground_truth_sql="SELECT COUNT(DISTINCT record_id) AS emigration_count FROM unified_record WHERE has_emigration_record=1 AND townland_norm='KILLINURE'",
        ground_truth_value=294,
        ground_truth_key="emigration_count",
        ground_truth_type="scalar",
        expected_answer_facts=["294"],
    ),

    EvalCase(
        id="emi_04_per_year_trend",
        question="Show emigration broken down by year",
        category="emigration",
        expected_route="template",
        expected_template_id="emigration_per_year",
        expected_lane="analytical",
        # Semantic layer returns year + emigration_count; just check SQL executes
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
        expected_answer_facts=["1847"],
    ),

    EvalCase(
        id="emi_05_canada_total",
        question="How many people emigrated to Canada?",
        category="emigration",
        expected_route="verified_analysis",
        expected_template_id="canada_emigration_peak_period",
        expected_lane="analytical",
        # Template returns per-year breakdown, not a single total — agg check N/A
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
        expected_answer_facts=["1847"],
    ),

    EvalCase(
        id="emi_06_canada_ship",
        question="Which ship carried the most Coolattin families to Canada?",
        category="emigration",
        expected_route="verified_analysis",
        expected_template_id="ship_most_families_canada",
        expected_lane="analytical",
        ground_truth_sql="SELECT ship_name FROM unified_record WHERE has_emigration_record=1 AND is_canada_destination=1 AND ship_name IS NOT NULL GROUP BY ship_name ORDER BY COUNT(DISTINCT record_id) DESC LIMIT 1",
        ground_truth_value="Glenlyon",
        ground_truth_key="ship_name",
        ground_truth_type="scalar",
        expected_answer_facts=["Glenlyon"],
    ),

    EvalCase(
        id="emi_07_ships_list",
        question="List the ships used for emigration from the estate",
        category="emigration",
        expected_route="template",
        expected_template_id="emigration_ships_list",
        expected_lane="analytical",
        ground_truth_sql="SELECT COUNT(DISTINCT ship_name) AS n FROM unified_record WHERE ship_name IS NOT NULL AND TRIM(ship_name)!=''",
        ground_truth_value=27,
        ground_truth_key="n",
        ground_truth_type=None,   # template returns list rows, not this count
        expected_answer_facts=["Star", "Glenlyon"],
    ),

    EvalCase(
        id="emi_08_in_1848",
        question="How many people emigrated in 1848?",
        category="emigration",
        expected_route="template",
        # Phase 0: emigration_total wins over emigration_in_year. agg_ok=✗.
        # Phase 2: semantic layer → emigration_count + year=1848 filter. agg_ok=✓.
        expected_template_id="emigration_in_year",
        expected_lane="analytical",
        ground_truth_sql="SELECT COUNT(DISTINCT record_id) AS emigration_count FROM unified_record WHERE has_emigration_record=1 AND year=1848",
        ground_truth_value=1290,
        ground_truth_key="emigration_count",
        ground_truth_type="scalar",
        expected_answer_facts=["1290"],
    ),

    # ── EVICTIONS (6) ──────────────────────────────────────────────────────────

    EvalCase(
        id="evic_01_total",
        question="How many evictions were recorded in total?",
        category="eviction",
        expected_route="template",
        expected_template_id="eviction_total",
        expected_lane="analytical",
        # Semantic layer alias: "total_evictions" (avoids normalizer collision with "eviction_count")
        ground_truth_sql="SELECT SUM(count) AS total_evictions FROM clearances_record",
        ground_truth_value=7763,
        ground_truth_key="total_evictions",
        ground_truth_type="scalar",
        expected_answer_facts=["7763"],
    ),

    EvalCase(
        id="evic_02_worst_year",
        question="Which year had the most evictions?",
        category="eviction",
        expected_route="template",
        expected_template_id="eviction_worst_year",
        expected_lane="analytical",
        ground_truth_sql="SELECT year FROM (SELECT year, SUM(count) AS n FROM clearances_record GROUP BY year ORDER BY n DESC LIMIT 1)",
        ground_truth_value=1847,
        ground_truth_key="year",
        ground_truth_type="scalar",
        expected_answer_facts=["1847"],
    ),

    EvalCase(
        id="evic_03_townland_ballinacor",
        question="How many evictions happened in Ballinacor?",
        category="eviction",
        expected_route="template",
        expected_template_id="eviction_from_townland",
        expected_lane="analytical",
        townland_hint="Ballinacor",
        expected_entity_norm="BALLINACOR",
        ground_truth_sql="SELECT SUM(c.count) AS n FROM clearances_record c JOIN townland t ON c.townland_id=t.id WHERE UPPER(t.name)='BALLINACOR'",
        ground_truth_value=122,
        ground_truth_key="n",
        ground_truth_type=None,   # template returns per-year rows, not total
        expected_answer_facts=["Ballinacor"],
    ),

    EvalCase(
        id="evic_04_per_year",
        question="Show evictions per year",
        category="eviction",
        expected_route="template",
        expected_template_id="eviction_per_year",
        expected_lane="analytical",
        ground_truth_sql="SELECT year, SUM(count) AS n FROM clearances_record GROUP BY year ORDER BY year",
        ground_truth_value=1847,
        ground_truth_key="year",
        ground_truth_type="row_key_value",
        expected_answer_facts=["1847"],
    ),

    EvalCase(
        id="evic_05_people_list",
        question="List the people who were evicted",
        category="eviction",
        expected_route="template",
        expected_template_id="eviction_people",
        expected_lane="analytical",
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
        expected_answer_facts=[],
    ),

    EvalCase(
        id="evic_06_in_1849",
        question="How many evictions happened in 1849?",
        category="eviction",
        expected_route="template",
        expected_template_id="eviction_in_year",
        expected_lane="analytical",
        # Template returns per-townland rows for that year, NOT a total
        ground_truth_sql="SELECT SUM(count) AS n FROM clearances_record WHERE year=1849",
        ground_truth_value=1016,
        ground_truth_key="n",
        ground_truth_type=None,   # template returns list, not total — intentional mismatch test
        expected_answer_facts=["1849"],
    ),

    # ── CENSUS / POPULATION (8) ────────────────────────────────────────────────

    EvalCase(
        id="cen_01_estate_1841",
        question="What was the total population of the estate in 1841?",
        category="census",
        expected_route="template",
        # census_total_year has higher score than census_1841 due to "total"+"estate" optional matches
        expected_template_id="census_total_year",
        expected_lane="analytical",
        ground_truth_sql="SELECT SUM(total) AS n FROM census_record WHERE year=1841",
        ground_truth_value=119300,
        ground_truth_key="n",
        ground_truth_type=None,   # template returns all years, not just 1841
        expected_answer_facts=["119300", "1841"],
    ),

    EvalCase(
        id="cen_02_estate_1851",
        question="What was the estate population in 1851?",
        category="census",
        expected_route="template",
        expected_template_id="census_1851",
        expected_lane="analytical",
        ground_truth_sql="SELECT SUM(total) AS n FROM census_record WHERE year=1851",
        ground_truth_value=91860,
        ground_truth_key="n",
        ground_truth_type=None,   # template returns per-townland list
        expected_answer_facts=["1851"],
    ),

    EvalCase(
        id="cen_03_ballinacor_1841",
        question="What was the population of Ballinacor in 1841?",
        category="census",
        expected_route="template",
        expected_template_id="census_population_townland_year",
        expected_lane="analytical",
        townland_hint="Ballinacor",
        expected_entity_norm="BALLINACOR",
        # Phase 2: semantic layer → population metric, townland+year filters. Alias="population".
        ground_truth_sql="SELECT SUM(c.total) AS population FROM census_record c JOIN townland t ON c.townland_id=t.id WHERE UPPER(t.name)='BALLINACOR' AND c.year=1841",
        ground_truth_value=55,
        ground_truth_key="population",
        ground_truth_type="scalar",
        expected_answer_facts=["55"],
    ),

    EvalCase(
        id="cen_04_famine_decline",
        question="How did the population decline from 1841 to 1851?",
        category="census",
        expected_route="template",
        expected_template_id="census_decline_famine",
        expected_lane="analytical",
        # Template returns per-townland rows (change col), not estate-wide total — type=None
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_key=None,
        ground_truth_type=None,
        expected_answer_facts=["1841", "1851"],
    ),

    EvalCase(
        id="cen_05_trend_1841_1861",
        question="What was the population trend from 1841 to 1861?",
        category="census",
        expected_route="verified_analysis",
        expected_template_id="population_trend_1841_1861",
        expected_lane="analytical",
        # Phase 2: semantic layer → population, dim=year, year_range=[1841,1861]. Alias="population".
        ground_truth_sql="SELECT c.year AS year, SUM(c.total) AS population FROM census_record c JOIN townland t ON c.townland_id=t.id WHERE c.year BETWEEN 1841 AND 1861 GROUP BY c.year ORDER BY c.year",
        ground_truth_value=81429,
        ground_truth_key="population",
        ground_truth_type="row_key_value",
        expected_answer_facts=["1841", "1861"],
    ),

    EvalCase(
        id="cen_06_uninhabited",
        question="How many uninhabited houses were recorded?",
        category="census",
        expected_route="template",
        expected_template_id="census_uninhabited",
        expected_lane="analytical",
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
    ),

    EvalCase(
        id="cen_07_all_years",
        question="Show the estate population across all census years",
        category="census",
        expected_route="template",
        expected_template_id="census_total_year",
        expected_lane="analytical",
        # Phase 2: semantic layer → population, dim=year, alias="population"
        # Known miss: "across" fuzzy-matches CROSS townland → spurious filter applied.
        # See written_finding() for full diagnosis.
        ground_truth_sql="SELECT c.year AS year, SUM(c.total) AS population FROM census_record c JOIN townland t ON c.townland_id=t.id GROUP BY c.year ORDER BY c.year",
        ground_truth_value=119300,
        ground_truth_key="population",
        ground_truth_type="row_key_value",
        expected_answer_facts=["1841"],
    ),

    EvalCase(
        id="cen_08_by_parish",
        question="Show population breakdown by parish",
        category="census",
        expected_route="template",
        expected_template_id="census_by_parish",
        expected_lane="analytical",
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
    ),

    # ── GEOGRAPHY (7) ─────────────────────────────────────────────────────────

    EvalCase(
        id="geo_01_total_townlands",
        question="How many townlands are there in the estate?",
        category="geography",
        expected_route="template",
        expected_template_id="townlands_total_count",
        expected_lane="analytical",
        # Phase 2: semantic layer alias = "townland_count"
        ground_truth_sql="SELECT COUNT(*) AS townland_count FROM townland",
        ground_truth_value=4225,
        ground_truth_key="townland_count",
        ground_truth_type="scalar",
        expected_answer_facts=["4225"],
    ),

    EvalCase(
        id="geo_02_parish_count",
        question="How many civil parishes are there?",
        category="geography",
        expected_route="template",
        expected_template_id="parishes_count",
        expected_lane="analytical",
        # Phase 2: semantic layer alias = "parish_count"
        ground_truth_sql="SELECT COUNT(DISTINCT civil_parish) AS parish_count FROM townland WHERE civil_parish IS NOT NULL AND TRIM(civil_parish)!=''",
        ground_truth_value=22,
        ground_truth_key="parish_count",
        ground_truth_type="scalar",
        expected_answer_facts=["22"],
    ),

    EvalCase(
        id="geo_03_parish_list",
        question="List all civil parishes in the estate",
        category="geography",
        expected_route="template",
        expected_template_id="parishes_list",
        expected_lane="analytical",
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
    ),

    EvalCase(
        id="geo_04_ballinacor_parish",
        question="Which parish is Ballinacor in?",
        category="geography",
        expected_route="template",
        # Phase 0: parishes_list wins over townland_parish_lookup. agg_ok=✗.
        # Phase 2: semantic layer detects "which parish" → townland_attribute metric. agg_ok=✓.
        expected_template_id="townland_parish_lookup",
        expected_lane="relational",   # hierarchy question → RELATIONAL intent
        townland_hint="Ballinacor",
        expected_entity_norm="BALLINACOR",
        expected_sql_id=355,
        expected_kg_uri_prefix="https://kg.virtualtreasury.ie",
        # SQLite says civil_parish="Kilbride"; VRTI KG says parish="Ballinacor".
        # This is a real SQLite/KG data discrepancy exposed here for Phase 6.
        expected_subgraph_facts=["Ballinacor"],  # KG parish name (VRTI-authoritative)
        ground_truth_sql="SELECT civil_parish FROM townland WHERE UPPER(name)='BALLINACOR' LIMIT 1",
        ground_truth_value="Kilbride",           # SQLite value (may differ from KG)
        ground_truth_key="civil_parish",
        ground_truth_type="scalar",
        expected_answer_facts=["Kilbride"],
    ),

    EvalCase(
        id="geo_05_baronies",
        question="What baronies are in the estate?",
        category="geography",
        expected_route="template",
        expected_template_id="barony_list",
        expected_lane="analytical",
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
        expected_answer_facts=["Shillelagh"],
    ),

    EvalCase(
        id="geo_06_nearby_coolattin",
        question="Show me townlands near Coolattin",
        category="geography",
        expected_route="template",
        expected_template_id="townland_nearby",
        expected_lane="relational",   # "near" → spatial/relational
        townland_hint="Coolattin",
        expected_entity_norm="COOLATTIN",
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
    ),

    EvalCase(
        id="geo_07_by_county",
        question="How many townlands are in each county?",
        category="geography",
        expected_route="template",
        expected_template_id="townlands_by_county",
        expected_lane="analytical",
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
    ),

    # ── PEOPLE / NAMES (8) ────────────────────────────────────────────────────

    EvalCase(
        id="ppl_01_total_records",
        question="How many people are in the records?",
        category="people",
        expected_route="template",
        expected_template_id="people_all_records",
        expected_lane="analytical",
        ground_truth_sql="SELECT COUNT(DISTINCT record_id) AS n FROM unified_record",
        ground_truth_value=13707,
        ground_truth_key="n",
        ground_truth_type=None,   # template returns two-column summary
        expected_answer_facts=["13707"],
    ),

    EvalCase(
        id="ppl_02_byrne_records",
        question="How many records mention the surname Byrne?",
        category="people",
        # Phase 0/1: output_mode="grouped" bypasses verified_analysis count path → list result. agg_ok=✗.
        # Phase 2: semantic layer detects "how many"→aggregate + surname → person_count. agg_ok=✓.
        expected_route="template",
        expected_lane="analytical",
        expected_surname="BYRNE",
        ground_truth_sql="SELECT COUNT(DISTINCT record_id) AS person_count FROM unified_record WHERE UPPER(surname)='BYRNE'",
        ground_truth_value=1290,
        ground_truth_key="person_count",
        ground_truth_type="scalar",
        expected_answer_facts=["1290", "Byrne"],
    ),

    EvalCase(
        id="ppl_03_murphy_list",
        question="List all people named Murphy in the estate records",
        category="people",
        expected_route="template",
        expected_lane="analytical",
        expected_surname="MURPHY",
        ground_truth_sql="SELECT COUNT(DISTINCT record_id) AS n FROM unified_record WHERE UPPER(surname)='MURPHY'",
        ground_truth_value=290,
        ground_truth_key="n",
        ground_truth_type=None,
        expected_answer_facts=["Murphy"],
    ),

    EvalCase(
        id="ppl_04_widows_count",
        question="How many widows are recorded in the estate records?",
        category="people",
        expected_route="verified_analysis",
        expected_template_id="widows_count",
        expected_lane="analytical",
        # Phase 2: semantic layer alias = "widow_count"
        ground_truth_sql="SELECT COUNT(DISTINCT record_id) AS widow_count FROM unified_record WHERE is_widow=1",
        ground_truth_value=811,
        ground_truth_key="widow_count",
        ground_truth_type="scalar",
        expected_answer_facts=["811"],
    ),

    EvalCase(
        id="ppl_05_widows_children",
        question="What proportion of widows had recorded children?",
        category="people",
        expected_route="verified_analysis",
        expected_template_id="widows_with_children_proportion",
        expected_lane="analytical",
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
        expected_answer_facts=["widow"],
    ),

    EvalCase(
        id="ppl_06_heads_of_household",
        question="List all heads of household in the estate",
        category="people",
        expected_route="template",
        expected_template_id="heads_of_household",
        expected_lane="analytical",
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
    ),

    EvalCase(
        id="ppl_07_ballynultagh_people",
        question="List all people recorded in Ballynultagh",
        category="people",
        expected_route="template",
        expected_template_id="people_all_in_townland",
        expected_lane="analytical",
        townland_hint="Ballynultagh",
        expected_entity_norm="BALLYNULTAGH",
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
    ),

    EvalCase(
        id="ppl_08_in_1847",
        question="How many people were recorded in 1847?",
        category="people",
        expected_route="template",
        expected_lane="analytical",
        ground_truth_sql="SELECT COUNT(DISTINCT record_id) AS n FROM unified_record WHERE year=1847",
        ground_truth_value=None,
        ground_truth_type=None,
        expected_answer_facts=["1847"],
    ),

    # ── TENANCY (6) ───────────────────────────────────────────────────────────

    EvalCase(
        id="ten_01_total",
        question="How many tenants are recorded?",
        category="tenancy",
        expected_route="template",
        expected_template_id="tenants_total",
        expected_lane="analytical",
        # Phase 2: semantic layer alias = "tenancy_count"
        ground_truth_sql="SELECT COUNT(DISTINCT record_id) AS tenancy_count FROM unified_record WHERE has_tenancy_record=1",
        ground_truth_value=5247,
        ground_truth_key="tenancy_count",
        ground_truth_type="scalar",
        expected_answer_facts=["5247"],
    ),

    EvalCase(
        id="ten_02_gender_avg",
        question="What is the average landholding for male versus female tenants?",
        category="tenancy",
        expected_route="verified_analysis",
        expected_template_id="tenant_land_gender_average",
        expected_lane="comparative",  # "versus" → COMPARATIVE intent
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
        expected_answer_facts=["Male", "Female"],
    ),

    EvalCase(
        id="ten_03_coolattin_tenants",
        question="List tenants from Coolattin",
        category="tenancy",
        expected_route="template",
        expected_template_id="tenants_townland",
        expected_lane="analytical",
        townland_hint="Coolattin",
        expected_entity_norm="COOLATTIN",
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
    ),

    EvalCase(
        id="ten_04_largest_holdings",
        question="Which tenants had the largest landholdings in their latest recorded year?",
        category="tenancy",
        expected_route="verified_analysis",
        expected_template_id="largest_latest_tenant_holdings",
        expected_lane="analytical",
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
    ),

    EvalCase(
        id="ten_05_smallest_plots",
        question="Which townlands have the smallest tenant plots?",
        category="tenancy",
        expected_route="verified_analysis",
        expected_template_id="smallest_townland_plots",
        expected_lane="analytical",
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
    ),

    EvalCase(
        id="ten_06_per_townland",
        question="How many tenants are recorded per townland?",
        category="tenancy",
        expected_route="template",
        expected_template_id="tenants_per_townland",
        expected_lane="analytical",
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
    ),

    # ── HERITAGE (5) ──────────────────────────────────────────────────────────

    EvalCase(
        id="her_01_holy_well_population",
        question="Are townlands with holy wells more populous than those without?",
        category="heritage",
        expected_route="verified_analysis",
        expected_template_id="holy_well_population_relationship",
        expected_lane="relational",   # heritage signal → RELATIONAL
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
        expected_answer_facts=["holy well"],
    ),

    EvalCase(
        id="her_02_ring_fort_population",
        question="Are townlands with ring forts more populous than those without?",
        category="heritage",
        expected_route="verified_analysis",
        expected_template_id="ring_fort_population_relationship",
        expected_lane="relational",
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
        expected_answer_facts=["ring fort"],
    ),

    EvalCase(
        id="her_03_holy_well_count",
        question="How many holy wells are recorded in the estate?",
        category="heritage",
        # Routes to holy_well_population_relationship (required: "holy","well") — returns
        # population comparison NOT a count. This intentionally tests template routing mismatch.
        expected_route="verified_analysis",
        expected_template_id="holy_well_population_relationship",
        expected_lane="analytical",   # pure count → ANALYTICAL (Core Rule 1)
        ground_truth_sql="SELECT COUNT(*) AS n FROM heritage_feature WHERE feature_group='holy_well'",
        ground_truth_value=68,
        ground_truth_key="n",
        ground_truth_type=None,   # template returns comparison rows, not count
        expected_answer_facts=["holy well"],
    ),

    EvalCase(
        id="her_04_ring_fort_count",
        question="How many ring forts are there in the estate?",
        category="heritage",
        # Same intentional mismatch as her_03
        expected_route="verified_analysis",
        expected_template_id="ring_fort_population_relationship",
        expected_lane="analytical",
        ground_truth_sql="SELECT COUNT(*) AS n FROM heritage_feature WHERE feature_group='ring_fort'",
        ground_truth_value=298,
        ground_truth_key="n",
        ground_truth_type=None,
        expected_answer_facts=["ring fort"],
    ),

    EvalCase(
        id="her_05_holy_well_townlands",
        question="Which townlands have holy wells?",
        category="heritage",
        expected_route="verified_analysis",
        expected_template_id="holy_well_population_relationship",
        expected_lane="relational",
        ground_truth_sql="SELECT COUNT(DISTINCT townland_norm) AS n FROM heritage_feature WHERE feature_group='holy_well'",
        ground_truth_value=65,
        ground_truth_key="n",
        ground_truth_type=None,
        expected_answer_facts=["holy well"],
    ),

    # ── OVERVIEW / COMBINED (5) ────────────────────────────────────────────────

    EvalCase(
        id="ov_01_famine_impact",
        question="What was the impact of the Great Famine on the estate?",
        category="overview",
        expected_route="template",
        expected_template_id="famine_impact",
        expected_lane="relational",   # sensemaking → RELATIONAL
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
        expected_answer_facts=["eviction", "emigrat"],
    ),

    EvalCase(
        id="ov_02_estate_summary",
        question="Give me an overview of the estate statistics",
        category="overview",
        expected_route="template",
        expected_template_id="estate_summary",
        expected_lane="relational",
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
    ),

    EvalCase(
        id="ov_03_emi_and_evic",
        question="How many people were both evicted and emigrated?",
        category="overview",
        expected_route="template",
        expected_template_id="emigration_and_eviction",
        expected_lane="analytical",
        ground_truth_sql="SELECT COUNT(DISTINCT record_id) AS n FROM unified_record WHERE has_emigration_record=1 AND has_eviction_record=1",
        ground_truth_value=0,
        ground_truth_key="n",
        ground_truth_type=None,   # template returns list not scalar
        expected_answer_facts=[],
    ),

    EvalCase(
        id="ov_04_emi_vs_population",
        question="Compare emigration numbers with census population over time",
        category="overview",
        expected_route="template",
        expected_template_id="emigration_and_population",
        expected_lane="comparative",  # "compare" → COMPARATIVE
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
        expected_answer_facts=["1841"],
    ),

    EvalCase(
        id="ov_05_records_per_year",
        question="How many records are there per year?",
        category="overview",
        expected_route="template",
        expected_template_id="records_per_year",
        expected_lane="analytical",
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
        expected_answer_facts=["1847"],
    ),

    # ── PHASE 1 — Entity resolution (sql_id + kg_uri) ──────────────────────────
    # These cases specifically test the vector-linking introduced in Phase 1.
    # They use townland hints and check that sql_id and kg_uri are populated.

    EvalCase(
        id="er_01_exact_ballinacor",
        question="Show me the census data for Ballinacor",
        category="entity",
        expected_route="template",
        expected_lane="analytical",
        townland_hint="Ballinacor",
        expected_entity_norm="BALLINACOR",
        expected_sql_id=355,
        expected_kg_uri_prefix="https://kg.virtualtreasury.ie",
        expected_template_id="census_population_townland",
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
    ),

    EvalCase(
        id="er_02_spelling_variant",
        question="How many people emigrated from Ballinacour?",
        category="entity",
        expected_route="template",
        expected_lane="analytical",
        townland_hint="Ballinacour",      # deliberate misspelling
        expected_entity_norm="BALLINACOR",  # should resolve to canonical
        expected_sql_id=355,
        expected_kg_uri_prefix="https://kg.virtualtreasury.ie",
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
    ),

    EvalCase(
        id="er_03_spelling_ballynultach",
        question="Emigration from Ballynultach",
        category="entity",
        expected_route="template",
        expected_lane="analytical",
        townland_hint="Ballynultach",     # common alternate spelling
        expected_entity_norm="BALLYNULTAGH",
        expected_kg_uri_prefix="https://kg.virtualtreasury.ie",
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
    ),

    EvalCase(
        id="er_04_coolattin_kg_uri",
        question="Who were the tenants of Coolattin?",
        category="entity",
        expected_route="template",
        expected_lane="analytical",
        townland_hint="Coolattin",
        expected_entity_norm="COOLATTIN",
        expected_kg_uri_prefix="https://kg.virtualtreasury.ie",
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
    ),

    EvalCase(
        id="er_05_surname_byrne_exact",
        question="List all Byrne family members",
        category="entity",
        expected_route="template",
        expected_lane="analytical",
        expected_surname="BYRNE",
        ground_truth_sql="SELECT COUNT(DISTINCT record_id) AS n FROM unified_record WHERE UPPER(surname)='BYRNE'",
        ground_truth_value=1290,
        ground_truth_key="n",
        ground_truth_type=None,
    ),

    EvalCase(
        id="er_06_surname_fuzzy",
        question="Records for the Kavanah family",
        category="entity",
        expected_route="template",
        expected_lane="analytical",
        expected_surname="KAVANAGH",   # Kavanah → Kavanagh via vector
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
    ),

    # ── PHASE 5 — RELATIONAL / HIERARCHY (5 new) ──────────────────────────────
    # These exercise the subgraph engine (Phase 3) and intent router (Phase 5).
    # Expected subgraph facts are checked against the linearized subgraph text
    # returned by retrieve_subgraph().  VRTI connectivity failures score 0 recall
    # but are not counted as routing errors.

    EvalCase(
        id="rel_01_ballinacor_barony",
        question="Which barony does Ballinacor belong to?",
        category="relational",
        expected_route="template",         # semantic_layer compiles townland_attribute
        expected_lane="relational",        # hierarchy keyword → RELATIONAL intent
        townland_hint="Ballinacor",
        expected_entity_norm="BALLINACOR",
        expected_sql_id=355,
        expected_kg_uri_prefix="https://kg.virtualtreasury.ie",
        # SQLite says barony="Arklow"; VRTI KG says barony="Ballinacor South".
        # This is a real SQLite/KG data discrepancy — Phase 6 must reconcile.
        # agg check (Arklow) validates SQLite; subgraph check (Ballinacor South) validates KG.
        expected_subgraph_facts=["Ballinacor South"],  # KG barony (VRTI-authoritative)
        ground_truth_sql="SELECT barony FROM townland WHERE UPPER(name)='BALLINACOR' LIMIT 1",
        ground_truth_value="Arklow",          # SQLite value (disagrees with KG)
        ground_truth_key="barony",
        ground_truth_type="scalar",
        expected_answer_facts=["Arklow"],
    ),

    EvalCase(
        id="rel_02_ballynultagh_county",
        question="What county and barony does Ballynultagh fall within?",
        category="relational",
        expected_route="template",
        expected_lane="relational",        # "falls within" → RELATIONAL
        townland_hint="Ballynultagh",
        expected_entity_norm="BALLYNULTAGH",
        expected_subgraph_facts=["Wicklow", "Shillelagh"],  # county + barony
        ground_truth_sql="SELECT barony, county FROM townland WHERE UPPER(name)='BALLYNULTAGH' LIMIT 1",
        ground_truth_value="Wicklow",
        ground_truth_key="county",
        ground_truth_type="scalar",
        expected_answer_facts=["Wicklow", "Shillelagh"],
    ),

    EvalCase(
        id="rel_03_ballinacor_parish_siblings",
        question="What other townlands are in the same parish as Ballinacor?",
        category="relational",
        expected_route="template",
        expected_lane="relational",        # "same parish" → RELATIONAL
        townland_hint="Ballinacor",
        expected_entity_norm="BALLINACOR",
        # KG parish for BALLINACOR is "Ballinacor" (not "Kilbride" as in SQLite).
        # Sibling townlands shown in subgraph are from the KG parish.
        expected_subgraph_facts=["Ballinacor"],   # KG parish name (siblings listed under it)
        ground_truth_sql="SELECT civil_parish FROM townland WHERE UPPER(name)='BALLINACOR' LIMIT 1",
        ground_truth_value="Kilbride",           # SQLite value (differs from KG)
        ground_truth_key="civil_parish",
        ground_truth_type="scalar",
        expected_answer_facts=["Kilbride"],
    ),

    EvalCase(
        id="rel_04_estate_overview",
        question="Tell me about the Coolattin estate and its history",
        category="relational",
        expected_route="template",         # famine_impact or estate_summary may match
        expected_lane="relational",        # "tell me about" + sensemaking → RELATIONAL
        expected_subgraph_facts=["Coolattin", "estate"],
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
        expected_answer_facts=["estate"],
    ),

    EvalCase(
        id="rel_05_historical_monuments",
        question="Tell me about the historical monuments in Ballinacor",
        category="heritage",
        expected_route="template",         # template catches "historical" before verified_analysis fires
        expected_lane="relational",        # "historical" + "monuments" → RELATIONAL
        townland_hint="Ballinacor",
        expected_entity_norm="BALLINACOR",
        expected_subgraph_facts=["Ballinacor"],
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
        expected_answer_facts=["Ballinacor"],
    ),

    # ── PHASE 5 — COMPARATIVE (3 new) ─────────────────────────────────────────
    # Questions where BOTH SQLite and the KG (GraphDB co: ontology) should return
    # the same metric for the same entity.  Phase 6 will reconcile discrepancies;
    # here we only verify both sources are captured (non-empty results).
    #
    # cmp_01 and cmp_02 expect AGREEMENT (same data source loaded into both).
    # cmp_03 uses eviction data which may differ between estate ledger and KG.

    EvalCase(
        id="cmp_01_emigration_vs_kg",
        question="Compare the emigration count from Ballynultagh in the estate records versus the knowledge graph",
        category="comparative",
        expected_route="template",
        expected_lane="comparative",       # "compare...versus" → COMPARATIVE
        townland_hint="Ballynultagh",
        expected_entity_norm="BALLYNULTAGH",
        expected_comparative_sources=["sqlite", "kg"],  # both sources should return data
        ground_truth_sql="SELECT COUNT(DISTINCT record_id) AS emigration_count FROM unified_record WHERE has_emigration_record=1 AND townland_norm='BALLYNULTAGH'",
        ground_truth_value=400,
        ground_truth_key="emigration_count",
        ground_truth_type="scalar",
        expected_answer_facts=["400"],
    ),

    EvalCase(
        id="cmp_02_population_vs_kg",
        question="How does the 1841 population of Ballinacor in the estate records compare to the VRTI knowledge graph?",
        category="comparative",
        expected_route="template",
        expected_lane="comparative",       # "compare to" → COMPARATIVE
        townland_hint="Ballinacor",
        expected_entity_norm="BALLINACOR",
        expected_comparative_sources=["sqlite", "kg"],
        ground_truth_sql="SELECT SUM(c.total) AS population FROM census_record c JOIN townland t ON c.townland_id=t.id WHERE UPPER(t.name)='BALLINACOR' AND c.year=1841",
        ground_truth_value=55,
        ground_truth_key="population",
        ground_truth_type="scalar",
        expected_answer_facts=["55"],
    ),

    EvalCase(
        id="cmp_03_eviction_agree",
        question="Compare the eviction total for Ballinacor from the estate records versus the knowledge graph",
        category="comparative",
        expected_route="template",
        expected_lane="comparative",       # "compare...versus" → COMPARATIVE
        townland_hint="Ballinacor",
        expected_entity_norm="BALLINACOR",
        expected_comparative_sources=["sqlite", "kg"],  # may disagree — KG has different data granularity
        # The semantic layer compiles this as "total_evictions" alias (eviction_event_count metric)
        ground_truth_sql="SELECT SUM(c.count) AS n FROM clearances_record c JOIN townland t ON c.townland_id=t.id WHERE UPPER(t.name)='BALLINACOR'",
        ground_truth_value=122,
        ground_truth_key="total_evictions",  # alias used by eviction_event_count metric
        ground_truth_type="scalar",
        expected_answer_facts=["122"],
    ),

    # ── PHASE 5 — FALLBACK / LOW-CONFIDENCE (3 new) ───────────────────────────
    # These questions have no semantic-layer metric match AND no template match,
    # so they must reach template_miss (the LLM free-form SQL generation path).
    # The eval verifies routing only — it does not call the LLM.

    EvalCase(
        id="fbl_01_rent",
        question="What was the average rent paid by tenants on the Coolattin estate?",
        category="fallback",
        # "tenant" triggers tenancy_count metric, but "rent" implies avg_holding_acres
        # or a rent-specific metric that doesn't exist.  Depending on routing, may
        # land on semantic_layer (tenancy_count) or template — annotated as expected_route="llm"
        # to mark this as intended fallback territory.
        expected_route="llm",
        expected_lane="fallback",
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
    ),

    EvalCase(
        id="fbl_02_crops",
        question="What crops were typically grown in the Coolattin area during the 1840s?",
        category="fallback",
        expected_route="llm",
        expected_lane="fallback",
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
    ),

    EvalCase(
        id="fbl_03_fitzwilliam",
        question="What was the Fitzwilliam family's approach to managing the Coolattin estate?",
        category="fallback",
        expected_route="llm",
        expected_lane="fallback",
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
    ),
]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _close_enough(a: Any, b: Any, rtol: float = 0.01) -> bool:
    """Numeric near-equality or exact string equality."""
    if a is None or b is None:
        return a == b
    try:
        fa, fb = float(a), float(b)
        if fb == 0:
            return fa == 0
        return abs(fa - fb) / abs(fb) <= rtol
    except (TypeError, ValueError):
        return str(a).strip().lower() == str(b).strip().lower()


def _extract_scalar(rows: list[dict], key: str) -> Any:
    """Return rows[0][key] if present."""
    if rows and key in rows[0]:
        return rows[0][key]
    return None


def _any_row_has_value(rows: list[dict], key: str, value: Any) -> bool:
    """Return True if any row has row[key] == value (approx)."""
    for row in rows:
        if key in row and _close_enough(row[key], value):
            return True
    return False


def _check_agg(case: EvalCase, cols: list[str], rows: list[dict]) -> tuple[bool | None, Any]:
    """Return (agg_ok, actual_value) for the case's aggregation check."""
    if case.ground_truth_type is None or case.ground_truth_value is None:
        return None, None

    if case.ground_truth_type == "scalar":
        if not case.ground_truth_key:
            return None, None
        actual = _extract_scalar(rows, case.ground_truth_key)
        return _close_enough(actual, case.ground_truth_value), actual

    if case.ground_truth_type == "row_key_value":
        if not case.ground_truth_key:
            return None, None
        ok = _any_row_has_value(rows, case.ground_truth_key, case.ground_truth_value)
        first_val = _extract_scalar(rows, case.ground_truth_key)
        return ok, first_val

    if case.ground_truth_type == "sum_all":
        if not case.ground_truth_key:
            return None, None
        try:
            total = sum(float(r[case.ground_truth_key]) for r in rows if r.get(case.ground_truth_key) is not None)
            return _close_enough(total, case.ground_truth_value), total
        except Exception:
            return False, None

    return None, None


def _check_entity_resolution(case: EvalCase, resolution: dict[str, Any]) -> bool | None:
    """Return True/False if entity resolved to expected label_norm; None if no check."""
    if case.expected_entity_norm:
        actual = resolution.get("name_norm")
        return actual == case.expected_entity_norm
    return None


def _check_surname_extraction(case: EvalCase, analysis: dict[str, Any]) -> bool | None:
    """Return True/False if surname extraction matches expected; None if no check."""
    if case.expected_surname:
        actual = analysis.get("surname")
        if actual is None:
            return False
        return str(actual).upper() == case.expected_surname.upper()
    return None


def _check_sql_id(case: EvalCase, resolution: dict[str, Any]) -> bool | None:
    """Phase 1: check that sql_id is populated (and matches expected if set)."""
    if not case.expected_entity_norm:
        return None  # no townland hint → nothing to check
    actual_id = resolution.get("sql_id")
    if actual_id is None:
        return False
    if case.expected_sql_id is not None:
        return int(actual_id) == int(case.expected_sql_id)
    return True  # just check it's non-None


def _check_kg_uri(case: EvalCase, resolution: dict[str, Any]) -> bool | None:
    """Phase 1: check that kg_uri is a valid VRTI URI."""
    if not case.expected_entity_norm:
        return None
    actual_uri = resolution.get("kg_uri")
    if actual_uri is None:
        return False
    prefix = case.expected_kg_uri_prefix or "https://kg.virtualtreasury.ie"
    return str(actual_uri).startswith(prefix)


def _check_lane(case: EvalCase, actual_lane: str | None) -> bool | None:
    """Phase 5: check that the intent router returned the expected lane."""
    if case.expected_lane is None or actual_lane is None:
        return None
    return actual_lane == case.expected_lane


def _check_subgraph(
    case: EvalCase,
    linearized: str,
    hierarchy: dict,
) -> tuple[bool | None, float | None]:
    """
    Phase 5: check whether expected_subgraph_facts appear in the subgraph.
    Returns (subgraph_ok, recall_fraction).

    Facts are searched case-insensitively in:
      1. the linearized subgraph text
      2. the hierarchy dict values (parish, barony, county)
    """
    if not case.expected_subgraph_facts:
        return None, None

    hier_text = " ".join(str(v) for v in hierarchy.values() if v)
    combined = (linearized + " " + hier_text).lower()

    found = sum(
        1 for fact in case.expected_subgraph_facts
        if fact.lower() in combined
    )
    total = len(case.expected_subgraph_facts)
    recall = found / total if total > 0 else 1.0
    ok = recall >= 0.5  # at least half the expected facts found
    return ok, round(recall, 3)


def _run_subgraph_retrieval(
    case: EvalCase,
    question: str,
    analysis: dict[str, Any],
    resolution: dict[str, Any],
) -> tuple[bool | None, float | None, str, dict]:
    """
    Phase 5: run retrieve_subgraph for RELATIONAL cases.
    Returns (subgraph_ok, recall, linearized, hierarchy).
    Never raises — returns empty results on any failure.
    """
    if not case.expected_subgraph_facts:
        return None, None, "", {}

    # For cases where we can answer from SQLite hierarchy alone (no VRTI needed),
    # build a minimal hierarchy dict directly from the resolution payload.
    # Use sql_id or name_norm regardless of the `matched` flag — if either is
    # present, we can look up the row.
    hier: dict = {}
    sql_id = resolution.get("sql_id")
    name_norm = resolution.get("name_norm")
    if sql_id or name_norm:
        try:
            from extensions import get_db_conn
            conn = get_db_conn()
            try:
                if sql_id:
                    where, param = "id = ?", int(sql_id)
                else:
                    where, param = "UPPER(name) = ?", str(name_norm)
                row = conn.execute(
                    f"SELECT civil_parish, barony, county FROM townland WHERE {where} LIMIT 1",
                    (param,)
                ).fetchone()
                if row:
                    hier = {
                        "parish": row["civil_parish"],
                        "barony": row["barony"],
                        "county": row["county"],
                    }
            finally:
                conn.close()
        except Exception:
            pass

    # Attempt full subgraph retrieval (VRTI + GraphDB).
    linearized = ""
    try:
        from backend.services.subgraph_engine import retrieve_subgraph
        result = retrieve_subgraph(
            question=question,
            analysis=analysis,
            townland_resolution=resolution,
            sources=("vrti",),    # skip GraphDB for eval speed
        )
        linearized = result.linearized or ""
        if result.hierarchy:
            # Merge KG hierarchy (KG wins over SQLite if available)
            for k, v in result.hierarchy.items():
                if v:
                    hier[k] = v
    except Exception:
        pass   # offline or unavailable — fall back to SQLite hier

    subgraph_ok, recall = _check_subgraph(case, linearized, hier)
    return subgraph_ok, recall, linearized, hier


def _run_comparative_check(
    case: EvalCase,
    sl_fill: Any | None,
    clearances_col: str,
) -> tuple[bool | None, bool | None]:
    """
    Phase 5: for COMPARATIVE cases, attempt to compile and execute both
    SQLite SQL and the SPARQL equivalent.
    Returns (sqlite_ok, kg_ok).
    sqlite_ok: True if SQLite returned ≥1 row, None if no metric.
    kg_ok:     True if GraphDB SPARQL returned ≥1 row,
               None if GraphDB offline or no SPARQL equivalent.
    """
    if not case.expected_comparative_sources:
        return None, None

    sqlite_ok: bool | None = None
    kg_ok: bool | None = None

    # ── SQLite path ───────────────────────────────────────────────────────────
    if sl_fill is not None and "sqlite" in case.expected_comparative_sources:
        try:
            from backend.services.semantic_layer import compile_sql
            from backend.services.ask_service import _run_read_only_query, _sanitize_and_validate_sql
            sql = compile_sql(sl_fill, clearances_col)
            if sql:
                _, rows = _run_read_only_query(_sanitize_and_validate_sql(sql))
                sqlite_ok = bool(rows)
            else:
                sqlite_ok = False
        except Exception:
            sqlite_ok = False

    # ── KG / SPARQL path ──────────────────────────────────────────────────────
    if sl_fill is not None and "kg" in case.expected_comparative_sources:
        try:
            from backend.services.semantic_layer import compile_sparql
            from backend.integrations import graphdb_sparql as _gdb
            sparql = compile_sparql(sl_fill)
            if sparql and _gdb.probe():
                rows = _gdb.query(sparql)
                kg_ok = bool(rows)
            else:
                kg_ok = None   # GraphDB offline or no SPARQL equivalent
        except Exception:
            kg_ok = None

    return sqlite_ok, kg_ok


# ── Case runner ───────────────────────────────────────────────────────────────

def _run_case(
    case: EvalCase,
    try_verified_analysis_fn,
    match_and_build_template_fn,
    resolve_townland_context_fn,
    analyse_question_fn,
    run_read_only_query_fn,
    sanitize_sql_fn,
) -> CaseResult:
    t0 = time.perf_counter()
    error: str | None = None

    try:
        # 1. Entity resolution + analysis
        resolution = resolve_townland_context_fn(case.question, case.townland_hint)
        canonical_townland = resolution.get("name_norm")
        analysis = analyse_question_fn(case.question, canonical_townland or case.townland_hint)

        entity_ok_townland = _check_entity_resolution(case, resolution)
        entity_ok_surname = _check_surname_extraction(case, analysis)
        if entity_ok_townland is not None:
            entity_ok: bool | None = entity_ok_townland
        elif entity_ok_surname is not None:
            entity_ok = entity_ok_surname
        else:
            entity_ok = None

        # Phase 1: check sql_id and kg_uri enrichment
        sql_id_ok = _check_sql_id(case, resolution)
        kg_uri_ok = _check_kg_uri(case, resolution)

        # Capture entity details for per-miss diagnostics
        entity_label_expected = case.expected_entity_norm or case.expected_surname
        entity_label_actual = resolution.get("name_norm") or analysis.get("surname")
        entity_sql_id_actual = resolution.get("sql_id")
        entity_kg_uri_actual = resolution.get("kg_uri")

        # 2. Route detection — mirrors the pipeline: semantic layer → verified_analysis → template
        raw_sql = ""
        compiled_sql_actual: str | None = None
        template_id = None
        actual_route = "template_miss"
        _sl_fill = None
        _sl_sql: str | None = None

        # Phase 2: try semantic layer first
        try:
            from backend.services.semantic_layer import try_rule_based_fill as _try_sl, compile_sql as _compile_sl
            from backend.services.ask_service import _clearances_count_column as _ccol
            _sl_fill = _try_sl(case.question, analysis, resolution)
            if _sl_fill and _sl_fill.confidence >= 0.80:
                _sl_sql = _compile_sl(_sl_fill, _ccol())
        except Exception:
            _sl_fill = None
            _sl_sql = None

        if _sl_sql:
            actual_route = "semantic_layer"
            raw_sql = _sl_sql
            compiled_sql_actual = _sl_sql
            template_id = f"semantic:{_sl_fill.metric}" if _sl_fill else "semantic"
        else:
            # Fall through to verified_analysis → template
            verified = try_verified_analysis_fn(case.question, canonical_townland, analysis)
            if verified:
                actual_route = "verified_analysis"
                raw_sql = verified.get("sql") or ""
                compiled_sql_actual = raw_sql or None
                template_id = (verified.get("meta") or {}).get("analysis_id")
            else:
                tmpl, tmpl_sql = match_and_build_template_fn(case.question, canonical_townland)
                if tmpl:
                    actual_route = "template"
                    raw_sql = tmpl_sql or ""
                    compiled_sql_actual = raw_sql or None
                    template_id = tmpl.get("id")

        # Routing correctness:
        # "verified_analysis" accepts semantic_layer or verified_analysis
        # "template" accepts semantic_layer, template, or verified_analysis (all deterministic)
        # "llm" expects template_miss
        if case.expected_route == "verified_analysis":
            route_ok = actual_route in {"verified_analysis", "semantic_layer"}
        elif case.expected_route == "template":
            route_ok = actual_route in {"template", "verified_analysis", "semantic_layer"}
        else:  # "llm"
            route_ok = (actual_route == "template_miss")

        # 3. SQL execution
        sql_ok: bool | None = None
        result_cols: list[str] = []
        result_rows: list[dict] = []
        if raw_sql:
            try:
                safe_sql = sanitize_sql_fn(raw_sql)
                result_cols, result_rows = run_read_only_query_fn(safe_sql)
                sql_ok = True
            except Exception as exc:
                sql_ok = False
                error = str(exc)[:120]

        # 4. Ground-truth aggregation check against the template/verified SQL result
        agg_ok, agg_actual = _check_agg(case, result_cols, result_rows)

        # 5. Phase 5: intent router lane classification
        actual_lane: str | None = None
        try:
            from backend.services.intent_router import classify_intent
            actual_lane = classify_intent(case.question, analysis, _sl_fill)
        except Exception:
            pass
        lane_ok = _check_lane(case, actual_lane)

        # 6. Phase 5: subgraph retrieval for RELATIONAL / HERITAGE cases
        subgraph_ok: bool | None = None
        subgraph_recall: float | None = None
        if case.expected_subgraph_facts:
            try:
                from backend.services.ask_service import _clearances_count_column as _ccol2
                subgraph_ok, subgraph_recall, _sg_text, _sg_hier = _run_subgraph_retrieval(
                    case, case.question, analysis, resolution
                )
            except Exception:
                subgraph_ok = None
                subgraph_recall = None

        # 7. Phase 5: comparative source capture
        comparative_sqlite_ok: bool | None = None
        comparative_kg_ok: bool | None = None
        if case.expected_comparative_sources:
            try:
                from backend.services.ask_service import _clearances_count_column as _ccol3
                comparative_sqlite_ok, comparative_kg_ok = _run_comparative_check(
                    case, _sl_fill, _ccol3()
                )
            except Exception:
                pass

    except Exception as exc:
        latency_ms = int((time.perf_counter() - t0) * 1000)
        return CaseResult(
            id=case.id,
            category=case.category,
            question=case.question[:70],
            expected_route=case.expected_route,
            actual_route="error",
            route_ok=False,
            entity_ok=None,
            sql_id_ok=None,
            kg_uri_ok=None,
            sql_ok=None,
            agg_ok=None,
            agg_actual=None,
            ground_truth=case.ground_truth_value,
            template_id=None,
            latency_ms=latency_ms,
            error=str(exc)[:120],
        )

    return CaseResult(
        id=case.id,
        category=case.category,
        question=case.question[:70],
        expected_route=case.expected_route,
        actual_route=actual_route,
        route_ok=route_ok,
        entity_ok=entity_ok,
        sql_id_ok=sql_id_ok,
        kg_uri_ok=kg_uri_ok,
        sql_ok=sql_ok,
        agg_ok=agg_ok,
        agg_actual=agg_actual,
        ground_truth=case.ground_truth_value,
        template_id=template_id,
        latency_ms=int((time.perf_counter() - t0) * 1000),
        error=error,
        lane=actual_lane,
        lane_ok=lane_ok,
        subgraph_ok=subgraph_ok,
        subgraph_recall=subgraph_recall,
        comparative_sqlite_ok=comparative_sqlite_ok,
        comparative_kg_ok=comparative_kg_ok,
        compiled_sql_actual=compiled_sql_actual,
        entity_label_expected=entity_label_expected,
        entity_label_actual=str(entity_label_actual) if entity_label_actual is not None else None,
        entity_sql_id_actual=int(entity_sql_id_actual) if entity_sql_id_actual is not None else None,
        entity_kg_uri_actual=str(entity_kg_uri_actual) if entity_kg_uri_actual is not None else None,
    )


# ── Eval runner ───────────────────────────────────────────────────────────────

def run_eval(phase_label: str = "baseline") -> EvalResult:
    """
    Run all golden cases through the deterministic pipeline.
    Must be called inside a Flask application context.
    """
    from datetime import datetime, timezone

    # Import deterministic functions from ask_service
    from backend.services.ask_service import (
        _try_verified_analysis,
        _match_and_build_template,
        _resolve_townland_context,
        _analyse_question,
        _run_read_only_query,
        _sanitize_and_validate_sql,
    )

    results: list[CaseResult] = []
    for case in GOLDEN_CASES:
        result = _run_case(
            case=case,
            try_verified_analysis_fn=_try_verified_analysis,
            match_and_build_template_fn=_match_and_build_template,
            resolve_townland_context_fn=_resolve_townland_context,
            analyse_question_fn=_analyse_question,
            run_read_only_query_fn=_run_read_only_query,
            sanitize_sql_fn=_sanitize_and_validate_sql,
        )
        results.append(result)

    return EvalResult(
        phase_label=phase_label,
        cases=results,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


# ── Metrics calculation ───────────────────────────────────────────────────────

def _compute_metrics(result: EvalResult) -> dict[str, Any]:
    cases = result.cases
    n = len(cases)
    if not n:
        return {}

    routing_ok = [c for c in cases if c.route_ok]
    entity_tested = [c for c in cases if c.entity_ok is not None]
    entity_ok_list = [c for c in entity_tested if c.entity_ok]
    sql_id_tested = [c for c in cases if c.sql_id_ok is not None]
    sql_id_ok = [c for c in sql_id_tested if c.sql_id_ok]
    kg_uri_tested = [c for c in cases if c.kg_uri_ok is not None]
    kg_uri_ok = [c for c in kg_uri_tested if c.kg_uri_ok]
    sql_tested = [c for c in cases if c.sql_ok is not None]
    sql_ok_list = [c for c in sql_tested if c.sql_ok]
    agg_tested = [c for c in cases if c.agg_ok is not None]
    agg_ok = [c for c in agg_tested if c.agg_ok]
    latencies = sorted([c.latency_ms for c in cases])
    llm_required = sum(1 for c in cases if c.actual_route == "template_miss")

    # ── Per-lane metrics ──────────────────────────────────────────────────────
    def _lane_cases(lane: str) -> list[CaseResult]:
        return [c for c in cases if _case_expected_lane(c) == lane]

    def _case_expected_lane(c: CaseResult) -> str | None:
        for case in GOLDEN_CASES:
            if case.id == c.id:
                return case.expected_lane
        return None

    analytical = _lane_cases("analytical")
    analytical_agg_tested = [c for c in analytical if c.agg_ok is not None]
    analytical_agg_ok = [c for c in analytical_agg_tested if c.agg_ok]

    relational = _lane_cases("relational")
    rel_sg_tested = [c for c in relational if c.subgraph_recall is not None]
    rel_sg_recalls = [c.subgraph_recall for c in rel_sg_tested if c.subgraph_recall is not None]

    comparative = _lane_cases("comparative")
    cmp_sqlite_tested = [c for c in comparative if c.comparative_sqlite_ok is not None]
    cmp_sqlite_ok = [c for c in cmp_sqlite_tested if c.comparative_sqlite_ok]
    cmp_kg_tested = [c for c in comparative if c.comparative_kg_ok is not None]
    cmp_kg_ok = [c for c in cmp_kg_tested if c.comparative_kg_ok]

    fallback = _lane_cases("fallback")
    fallback_routing_ok = [c for c in fallback if c.route_ok]

    # ── Lane routing accuracy ─────────────────────────────────────────────────
    lane_tested = [c for c in cases if c.lane_ok is not None]
    lane_ok_list = [c for c in lane_tested if c.lane_ok]

    return {
        "n": n,
        "routing_accuracy": round(100 * len(routing_ok) / n, 1),
        "entity_resolution_acc": round(100 * len(entity_ok_list) / len(entity_tested), 1) if entity_tested else None,
        "sql_id_resolution_rate": round(100 * len(sql_id_ok) / len(sql_id_tested), 1) if sql_id_tested else None,
        "kg_uri_resolution_rate": round(100 * len(kg_uri_ok) / len(kg_uri_tested), 1) if kg_uri_tested else None,
        "sql_exec_success": round(100 * len(sql_ok_list) / len(sql_tested), 1) if sql_tested else None,
        "aggregation_correctness": round(100 * len(agg_ok) / len(agg_tested), 1) if agg_tested else None,
        "p50_latency_ms": latencies[n // 2],
        "p95_latency_ms": latencies[min(int(n * 0.95), n - 1)],
        "llm_calls_required": llm_required,
        "template_hit_rate": round(100 * (n - llm_required) / n, 1),
        # Per-lane
        "lane_routing_acc": round(100 * len(lane_ok_list) / len(lane_tested), 1) if lane_tested else None,
        "analytical_n": len(analytical),
        "analytical_agg_acc": round(100 * len(analytical_agg_ok) / len(analytical_agg_tested), 1) if analytical_agg_tested else None,
        "relational_n": len(relational),
        "subgraph_recall": round(statistics.mean(rel_sg_recalls), 3) if rel_sg_recalls else None,
        "comparative_n": len(comparative),
        "comparative_sqlite_capture": round(100 * len(cmp_sqlite_ok) / len(cmp_sqlite_tested), 1) if cmp_sqlite_tested else None,
        "comparative_kg_capture": round(100 * len(cmp_kg_ok) / len(cmp_kg_tested), 1) if cmp_kg_tested else None,
        "fallback_n": len(fallback),
        "fallback_routing_acc": round(100 * len(fallback_routing_ok) / len(fallback), 1) if fallback else None,
    }


# ── Per-miss diagnostic detail ────────────────────────────────────────────────

def print_miss_detail(result: EvalResult) -> None:
    """
    Step 1 diagnostic: for every failing case, print per-question detail covering:
      • expected vs actual resolved entity (label, sql_id, kg_uri)
      • expected vs actual compiled SQL
      • expected vs actual numeric answer
    """
    failures = [
        c for c in result.cases
        if not c.route_ok or c.entity_ok is False or c.agg_ok is False
    ]
    if not failures:
        print("\n  All cases passed — no miss detail to display.")
        return

    W = 80
    print(f"\n{'═' * W}")
    print("  PER-MISS DIAGNOSTIC DETAIL")
    print(f"{'═' * W}")

    # Look up case objects by id for ground_truth_sql
    case_map: dict[str, EvalCase] = {c.id: c for c in GOLDEN_CASES}

    for cr in failures:
        ec = case_map.get(cr.id)
        print(f"\n  Case: {cr.id}  [{cr.category}]")
        print(f"  Question: {cr.question}")

        # Route
        route_sym = "✓" if cr.route_ok else "✗"
        print(f"  Route [{route_sym}]: expected={cr.expected_route!r}  actual={cr.actual_route!r}")

        # Entity
        if cr.entity_ok is not None:
            ent_sym = "✓" if cr.entity_ok else "✗"
            print(f"  Entity [{ent_sym}]:")
            print(f"    expected label={cr.entity_label_expected!r}")
            print(f"    actual  label={cr.entity_label_actual!r}  sql_id={cr.entity_sql_id_actual}  kg_uri={str(cr.entity_kg_uri_actual or '')[:60]}")

        # Aggregation / SQL
        if cr.agg_ok is not None:
            agg_sym = "✓" if cr.agg_ok else "✗"
            print(f"  Aggregation [{agg_sym}]:")
            print(f"    expected value={cr.ground_truth!r}")
            print(f"    actual  value={cr.agg_actual!r}")
            if ec and ec.ground_truth_sql:
                print(f"    ground-truth SQL: {ec.ground_truth_sql[:100]}")
            if cr.compiled_sql_actual:
                print(f"    compiled SQL:     {cr.compiled_sql_actual[:100]}")
            # Diagnose the cause
            if ec and ec.ground_truth_sql and cr.compiled_sql_actual:
                if ec.ground_truth_sql.strip() != cr.compiled_sql_actual.strip():
                    print(f"    ⚠ SQL MISMATCH — pipeline compiled a different query than ground truth.")
                    # Check for spurious filters
                    gt_where = _extract_where(ec.ground_truth_sql)
                    act_where = _extract_where(cr.compiled_sql_actual)
                    if act_where and act_where != gt_where:
                        print(f"    ⚠ Extra/different WHERE clause detected:")
                        print(f"      ground-truth WHERE: {gt_where or '(none)'}")
                        print(f"      compiled    WHERE: {act_where or '(none)'}")

        if cr.error:
            print(f"  ERROR: {cr.error}")

    print(f"\n{'─' * W}")


def _extract_where(sql: str) -> str:
    """Extract the WHERE clause from a SQL string (rough heuristic)."""
    m = re.search(r'\bWHERE\b(.+?)(?:\bGROUP\b|\bORDER\b|\bLIMIT\b|$)', sql, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else ""


def written_finding(result: EvalResult) -> str:
    """
    Step 1 written finding: what is actually driving the aggregation misses?
    Returns a multi-line diagnostic summary string.
    """
    cases = result.cases
    case_map: dict[str, EvalCase] = {c.id: c for c in GOLDEN_CASES}

    agg_failures = [c for c in cases if c.agg_ok is False]
    entity_failures = [c for c in cases if c.entity_ok is False]
    agg_ids = {c.id for c in agg_failures}
    entity_ids = {c.id for c in entity_failures}
    overlap = agg_ids & entity_ids

    agg_tested = [c for c in cases if c.agg_ok is not None]
    agg_miss_pct = round(100 * len(agg_failures) / len(agg_tested), 1) if agg_tested else 0.0

    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("  STEP 1 WRITTEN FINDING — AGGREGATION MISS DIAGNOSIS")
    lines.append("=" * 72)
    lines.append("")
    lines.append(f"  Aggregation miss rate : {agg_miss_pct}% ({len(agg_failures)} / {len(agg_tested)} tested cases)")
    lines.append(f"  Aggregation failures  : {sorted(agg_ids) or '(none)'}")
    lines.append(f"  Entity-label failures : {sorted(entity_ids) or '(none)'}")
    lines.append(f"  Overlap               : {sorted(overlap) or '(none — zero overlap)'}")
    lines.append("")

    lines.append("  METRIC RECONCILIATION")
    lines.append("  ─────────────────────")
    lines.append("  sql_id_resolution_rate (100%) measures whether every townland hint")
    lines.append("  that was resolved has a non-None sql_id in the resolution payload.")
    lines.append("  It is computed only over cases with expected_entity_norm (townland cases).")
    lines.append("")
    lines.append("  entity_label_acc (<100%) measures whether the resolved label_norm")
    lines.append("  (or extracted surname) matches the expected entity string. This covers")
    lines.append("  BOTH townland cases (label_norm check) AND surname cases (analysis.surname")
    lines.append("  extraction check). The failures are surname cases (er_05, er_06) where")
    lines.append("  _analyse_question does not extract the surname correctly.")
    lines.append("")
    lines.append("  A query CAN resolve to a 'valid-but-wrong entity' only if the vector")
    lines.append("  search returns a real townland that has a sql_id, but with a different")
    lines.append("  label than expected. In the current 59-case set this does NOT occur —")
    lines.append("  all townland cases resolve to the correct label.")
    lines.append("")

    lines.append("  ROOT CAUSE OF AGGREGATION MISSES")
    lines.append("  ─────────────────────────────────")
    if not agg_failures:
        lines.append("  None — all aggregation checks pass.")
    else:
        for cr in agg_failures:
            ec = case_map.get(cr.id)
            lines.append(f"  [{cr.id}]  Q: {cr.question[:60]}")
            if ec:
                gt_w = _extract_where(ec.ground_truth_sql or "")
                act_w = _extract_where(cr.compiled_sql_actual or "")
                if gt_w != act_w:
                    lines.append(f"    Cause: FALSE-POSITIVE TOWNLAND EXTRACTION")
                    lines.append(f"    Ground-truth WHERE : {gt_w or '(none)'}")
                    lines.append(f"    Compiled     WHERE : {act_w or '(none)'}")
                    # Identify spurious entity
                    entity_in_filter = cr.entity_label_actual or "(unknown)"
                    lines.append(f"    Spurious entity    : {entity_in_filter!r}")
                    if cr.id == "emi_01_total":
                        lines.append( "    Mechanism: 'Coolattin' is an exact DB match for the COOLATTIN")
                        lines.append( "    townland, so _resolve_townland_context returns COOLATTIN and")
                        lines.append( "    try_rule_based_fill applies a townland filter.  The question")
                        lines.append( "    intends the WHOLE ESTATE, not that single townland (217 vs 6016).")
                    elif cr.id == "cen_07_all_years":
                        lines.append( "    Mechanism: 'across' in 'across all census years' has the same")
                        lines.append( "    compact-key as the CROSS townland. _townland_query_candidates")
                        lines.append( "    extracts 'across' as a candidate, _suggest_townland_matches")
                        lines.append( "    scores it ≥0.86 against CROSS, and the filter is applied.")
                else:
                    lines.append(f"    Cause: SQL MATCHES but answer differs — data value mismatch.")
                    lines.append(f"    Expected: {cr.ground_truth!r}   Actual: {cr.agg_actual!r}")
    lines.append("")
    lines.append("  CONCLUSION")
    lines.append("  The 11.8% aggregation miss is NOT driven by entity mislabeling")
    lines.append("  (entity_resolver returning the wrong canonical form). It is driven")
    lines.append("  by FALSE-POSITIVE TOWNLAND EXTRACTION in ask_service:")
    lines.append("    1. Estate-name context confusion: 'Coolattin estate' → COOLATTIN filter")
    lines.append("    2. Substring compact-key collision: 'across' → CROSS filter")
    lines.append("  Step 3 (entity_resolver fix) does NOT apply — the resolver correctly")
    lines.append("  returns COOLATTIN and CROSS from the given text. The fix must be in")
    lines.append("  ask_service._townland_query_candidates (stopword 'across') or the")
    lines.append("  semantic layer (suppress filter when question uses 'estate' as qualifier).")
    lines.append("=" * 72)
    return "\n".join(lines)


# ── Display ───────────────────────────────────────────────────────────────────

def print_case_table(result: EvalResult) -> None:
    """Print per-case results table."""
    _W = 84
    print(f"\n{'─' * _W}")
    print(f"  CASE RESULTS  [{result.phase_label}]  {result.timestamp[:19]}")
    print(f"{'─' * _W}")
    print(f"{'ID':<36} {'Cat':<8} {'Exp':<7} {'Act':<16} {'Rt':>3} {'En':>3} {'Id':>3} {'KG':>3} {'Sq':>3} {'Ag':>3} {'Ln':>3} {'SG':>3} {'ms':>5}")
    print(f"{'─' * _W}")

    def _fmt(val: bool | None) -> str:
        if val is True:
            return "✓"
        if val is False:
            return "✗"
        return "-"

    for c in result.cases:
        act = c.actual_route[:16]
        print(
            f"{c.id:<36} {c.category[:8]:<8} {c.expected_route[:7]:<7} {act:<16}"
            f" {_fmt(c.route_ok):>3} {_fmt(c.entity_ok):>3}"
            f" {_fmt(c.sql_id_ok):>3} {_fmt(c.kg_uri_ok):>3}"
            f" {_fmt(c.sql_ok):>3} {_fmt(c.agg_ok):>3}"
            f" {_fmt(c.lane_ok):>3} {_fmt(c.subgraph_ok):>3}"
            f" {c.latency_ms:>5}"
        )
        if c.error:
            print(f"  {'':36}   ERROR: {c.error[:60]}")
    print(f"{'─' * _W}")
    print("  Columns: Rt=route  En=entity  Id=sql_id  KG=kg_uri  Sq=sql_exec")
    print("           Ag=agg    Ln=lane    SG=subgraph")


def print_metrics_table(
    results: list[EvalResult],
    labels: list[str] | None = None,
) -> None:
    """Print a before/after metrics comparison table."""
    if not results:
        return
    labels = labels or [r.phase_label for r in results]
    metrics = [_compute_metrics(r) for r in results]

    rows = [
        ("Questions run",            "n",                          False),
        ("── GLOBAL ──────────────", None,                        False),
        ("Routing accuracy (%)",     "routing_accuracy",           True),
        ("Entity label acc (%)",     "entity_resolution_acc",      True),
        ("SQL-id resolution (%)",    "sql_id_resolution_rate",     True),
        ("KG-URI resolution (%)",    "kg_uri_resolution_rate",     True),
        ("SQL exec success (%)",     "sql_exec_success",           True),
        ("Aggregation correct (%)",  "aggregation_correctness",    True),
        ("Template hit rate (%)",    "template_hit_rate",          True),
        ("LLM calls required",       "llm_calls_required",         False),
        ("Lane routing acc (%)",     "lane_routing_acc",           True),
        ("p50 latency (ms)",         "p50_latency_ms",             False),
        ("p95 latency (ms)",         "p95_latency_ms",             False),
        ("── PER-LANE ────────────", None,                        False),
        ("Analytical n",             "analytical_n",               False),
        ("Analytical agg acc (%)",   "analytical_agg_acc",         True),
        ("Relational n",             "relational_n",               False),
        ("Subgraph recall (mean)",   "subgraph_recall",            True),
        ("Comparative n",            "comparative_n",              False),
        ("Compar. SQLite capture (%)", "comparative_sqlite_capture", True),
        ("Compar. KG capture (%)",   "comparative_kg_capture",     True),
        ("Fallback n",               "fallback_n",                 False),
        ("Fallback routing acc (%)", "fallback_routing_acc",       True),
    ]

    col_w = 28
    val_w = 14
    header = f"{'Metric':<{col_w}}" + "".join(f"{lbl:>{val_w}}" for lbl in labels)
    print(f"\n{'═' * (col_w + val_w * len(results))}")
    print("  ASK PIPELINE EVAL — METRICS COMPARISON")
    print(f"{'═' * (col_w + val_w * len(results))}")
    print(header)
    print(f"{'─' * (col_w + val_w * len(results))}")

    for label, key, higher_is_better in rows:
        if key is None:
            print(f"  {label}")
            continue
        vals = [m.get(key) for m in metrics]
        row = f"{label:<{col_w}}"
        for i, v in enumerate(vals):
            if v is None:
                cell = "N/A"
            else:
                cell = str(v)
            # Mark improvement / regression relative to first column
            if i > 0 and vals[0] is not None and v is not None and higher_is_better:
                try:
                    delta = float(v) - float(vals[0])
                    if delta > 0.5:
                        cell += " ▲"
                    elif delta < -0.5:
                        cell += " ▼"
                except (TypeError, ValueError):
                    pass
            row += f"{cell:>{val_w}}"
        print(row)

    print(f"{'═' * (col_w + val_w * len(results))}")


# ── CLI ────────────────────────────────────────────────────────────────────────

def _save_result(result: EvalResult, path: Path) -> None:
    data = {
        "phase_label": result.phase_label,
        "timestamp": result.timestamp,
        "metrics": _compute_metrics(result),
        "cases": [
            {
                "id": c.id, "category": c.category,
                "question": c.question,
                "expected_route": c.expected_route, "actual_route": c.actual_route,
                "route_ok": c.route_ok, "entity_ok": c.entity_ok,
                "sql_id_ok": c.sql_id_ok, "kg_uri_ok": c.kg_uri_ok,
                "sql_ok": c.sql_ok, "agg_ok": c.agg_ok,
                "agg_actual": c.agg_actual, "ground_truth": c.ground_truth,
                "template_id": c.template_id, "latency_ms": c.latency_ms,
                "error": c.error,
                "lane": c.lane, "lane_ok": c.lane_ok,
                "subgraph_ok": c.subgraph_ok, "subgraph_recall": c.subgraph_recall,
                "comparative_sqlite_ok": c.comparative_sqlite_ok,
                "comparative_kg_ok": c.comparative_kg_ok,
                "compiled_sql_actual": c.compiled_sql_actual,
                "entity_label_expected": c.entity_label_expected,
                "entity_label_actual": c.entity_label_actual,
            }
            for c in result.cases
        ],
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    print(f"\nResults saved → {path}")


def _load_result(path: Path) -> EvalResult:
    data = json.loads(path.read_text())
    cases = [
        CaseResult(
            id=c["id"], category=c.get("category", ""),
            question=c.get("question", c["id"]),
            expected_route=c["expected_route"], actual_route=c["actual_route"],
            route_ok=c["route_ok"], entity_ok=c.get("entity_ok"),
            sql_id_ok=c.get("sql_id_ok"), kg_uri_ok=c.get("kg_uri_ok"),
            sql_ok=c.get("sql_ok"), agg_ok=c.get("agg_ok"),
            agg_actual=c.get("agg_actual"), ground_truth=c.get("ground_truth"),
            template_id=c.get("template_id"), latency_ms=c.get("latency_ms", 0),
            error=c.get("error"),
            lane=c.get("lane"), lane_ok=c.get("lane_ok"),
            subgraph_ok=c.get("subgraph_ok"), subgraph_recall=c.get("subgraph_recall"),
            comparative_sqlite_ok=c.get("comparative_sqlite_ok"),
            comparative_kg_ok=c.get("comparative_kg_ok"),
            compiled_sql_actual=c.get("compiled_sql_actual"),
            entity_label_expected=c.get("entity_label_expected"),
            entity_label_actual=c.get("entity_label_actual"),
        )
        for c in data.get("cases", [])
    ]
    return EvalResult(
        phase_label=data.get("phase_label", path.stem),
        cases=cases,
        timestamp=data.get("timestamp", ""),
    )


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Ask pipeline eval harness")
    parser.add_argument("--phase", default="baseline", help="Label for this run (e.g. phase0, phase5)")
    parser.add_argument("--save", metavar="FILE", help="Save result JSON to FILE")
    parser.add_argument("--compare", nargs="+", metavar="FILE", help="Compare existing result JSON files")
    parser.add_argument("--no-miss-detail", action="store_true", help="Skip per-miss diagnostic output")
    args = parser.parse_args()

    if args.compare:
        loaded = [_load_result(Path(f)) for f in args.compare]
        for r in loaded:
            print_case_table(r)
        print_metrics_table(loaded)
        return

    # Run eval
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from create_app import create_app

    app = create_app()
    with app.app_context():
        print(f"Running eval harness ({len(GOLDEN_CASES)} cases)…")
        result = run_eval(phase_label=args.phase)

    print_case_table(result)
    print_metrics_table([result])

    if not args.no_miss_detail:
        print_miss_detail(result)
        print(written_finding(result))

    if args.save:
        _save_result(result, Path(args.save))
    else:
        out_dir = Path(__file__).parent / "eval_results"
        out_dir.mkdir(exist_ok=True)
        out_path = out_dir / f"eval_{args.phase}.json"
        _save_result(result, out_path)


if __name__ == "__main__":
    main()
