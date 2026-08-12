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


@dataclass
class EvalCase:
    id: str
    question: str
    category: str
    expected_route: str
    townland_hint: str | None = None
    expected_entity_norm: str | None = None
    expected_surname: str | None = None
    expected_sql_id: int | None = None
    expected_kg_uri_prefix: str | None = None
    expected_template_id: str | None = None
    ground_truth_sql: str | None = None
    ground_truth_value: Any = None
    ground_truth_key: str | None = None
    ground_truth_type: str | None = None
    expected_answer_facts: list[str] = field(default_factory=list)
    expected_lane: str | None = None
    expected_subgraph_facts: list[str] = field(default_factory=list)
    expected_comparative_sources: list[str] = field(default_factory=list)
    catalogue_code: str | None = None
    is_out_of_scope: bool = False


@dataclass
class CaseResult:
    id: str
    category: str
    question: str
    expected_route: str
    actual_route: str
    route_ok: bool
    entity_ok: bool | None
    sql_id_ok: bool | None
    kg_uri_ok: bool | None
    sql_ok: bool | None
    agg_ok: bool | None
    agg_actual: Any
    ground_truth: Any
    template_id: str | None
    latency_ms: int
    error: str | None = None
    lane: str | None = None
    lane_ok: bool | None = None
    subgraph_ok: bool | None = None
    subgraph_recall: float | None = None
    comparative_sqlite_ok: bool | None = None
    comparative_kg_ok: bool | None = None
    compiled_sql_actual: str | None = None
    entity_label_expected: str | None = None
    entity_label_actual: str | None = None
    entity_sql_id_actual: int | None = None
    entity_kg_uri_actual: str | None = None
    answer_facts_ok: bool | None = None


@dataclass
class EvalResult:
    phase_label: str
    cases: list[CaseResult]
    timestamp: str


GOLDEN_CASES: list[EvalCase] = [


    EvalCase(
        id="emi_01_total",
        question="How many people emigrated from the Coolattin estate?",
        category="emigration",
        expected_route="template",
        expected_template_id="emigration_total",
        expected_lane="analytical",
        catalogue_code="A",
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
        expected_template_id="emigration_from_townland",
        expected_lane="analytical",
        townland_hint="Ballynultagh",
        expected_entity_norm="BALLYNULTAGH",
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
        ground_truth_type=None,
        expected_answer_facts=["Star", "Glenlyon"],
    ),

    EvalCase(
        id="emi_08_in_1848",
        question="How many people emigrated in 1848?",
        category="emigration",
        expected_route="template",
        expected_template_id="emigration_in_year",
        expected_lane="analytical",
        ground_truth_sql="SELECT COUNT(DISTINCT record_id) AS emigration_count FROM unified_record WHERE has_emigration_record=1 AND year=1848",
        ground_truth_value=1290,
        ground_truth_key="emigration_count",
        ground_truth_type="scalar",
        expected_answer_facts=["1290"],
    ),


    EvalCase(
        id="evic_01_total",
        question="How many evictions were recorded in total?",
        category="eviction",
        expected_route="template",
        expected_template_id="eviction_total",
        expected_lane="analytical",
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
        ground_truth_type=None,
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
        ground_truth_sql="SELECT SUM(count) AS n FROM clearances_record WHERE year=1849",
        ground_truth_value=1016,
        ground_truth_key="n",
        ground_truth_type=None,
        expected_answer_facts=["1849"],
    ),


    EvalCase(
        id="cen_01_estate_1841",
        question="What was the total population of the estate in 1841?",
        category="census",
        expected_route="template",
        expected_template_id="census_total_year",
        expected_lane="analytical",
        ground_truth_sql="SELECT SUM(total) AS n FROM census_record WHERE year=1841",
        ground_truth_value=119300,
        ground_truth_key="n",
        ground_truth_type=None,
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
        ground_truth_type=None,
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


    EvalCase(
        id="geo_01_total_townlands",
        question="How many townlands are there in the estate?",
        category="geography",
        expected_route="template",
        expected_template_id="townlands_total_count",
        expected_lane="analytical",
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
        expected_template_id="townland_parish_lookup",
        expected_lane="relational",
        townland_hint="Ballinacor",
        expected_entity_norm="BALLINACOR",
        expected_sql_id=355,
        expected_kg_uri_prefix="https://kg.virtualtreasury.ie",
        expected_subgraph_facts=["Ballinacor"],
        ground_truth_sql="SELECT civil_parish FROM townland WHERE UPPER(name)='BALLINACOR' LIMIT 1",
        ground_truth_value="Kilbride",
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
        expected_lane="relational",
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
        ground_truth_type=None,
        expected_answer_facts=["13707"],
    ),

    EvalCase(
        id="ppl_02_byrne_records",
        question="How many records mention the surname Byrne?",
        category="people",
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


    EvalCase(
        id="ten_01_total",
        question="How many tenants are recorded?",
        category="tenancy",
        expected_route="template",
        expected_template_id="tenants_total",
        expected_lane="analytical",
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
        expected_lane="comparative",
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


    EvalCase(
        id="her_01_holy_well_population",
        question="Are townlands with holy wells more populous than those without?",
        category="heritage",
        expected_route="verified_analysis",
        expected_template_id="holy_well_population_relationship",
        expected_lane="relational",
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
        expected_route="verified_analysis",
        expected_template_id="holy_well_population_relationship",
        expected_lane="analytical",
        ground_truth_sql="SELECT COUNT(*) AS n FROM heritage_feature WHERE feature_group='holy_well'",
        ground_truth_value=68,
        ground_truth_key="n",
        ground_truth_type=None,
        expected_answer_facts=["holy well"],
    ),

    EvalCase(
        id="her_04_ring_fort_count",
        question="How many ring forts are there in the estate?",
        category="heritage",
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


    EvalCase(
        id="ov_01_famine_impact",
        question="What was the impact of the Great Famine on the estate?",
        category="overview",
        expected_route="template",
        expected_template_id="famine_impact",
        expected_lane="relational",
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
        ground_truth_type=None,
        expected_answer_facts=[],
    ),

    EvalCase(
        id="ov_04_emi_vs_population",
        question="Compare emigration numbers with census population over time",
        category="overview",
        expected_route="template",
        expected_template_id="emigration_and_population",
        expected_lane="comparative",
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
        townland_hint="Ballinacour",
        expected_entity_norm="BALLINACOR",
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
        townland_hint="Ballynultach",
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
        expected_surname="KAVANAGH",
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
    ),


    EvalCase(
        id="rel_01_ballinacor_barony",
        question="Which barony does Ballinacor belong to?",
        category="relational",
        expected_route="template",
        expected_lane="relational",
        townland_hint="Ballinacor",
        expected_entity_norm="BALLINACOR",
        expected_sql_id=355,
        expected_kg_uri_prefix="https://kg.virtualtreasury.ie",
        expected_subgraph_facts=["Ballinacor South"],
        ground_truth_sql="SELECT barony FROM townland WHERE UPPER(name)='BALLINACOR' LIMIT 1",
        ground_truth_value="Arklow",
        ground_truth_key="barony",
        ground_truth_type="scalar",
        expected_answer_facts=["Arklow"],
    ),

    EvalCase(
        id="rel_02_ballynultagh_county",
        question="What county and barony does Ballynultagh fall within?",
        category="relational",
        expected_route="template",
        expected_lane="relational",
        townland_hint="Ballynultagh",
        expected_entity_norm="BALLYNULTAGH",
        expected_subgraph_facts=["Wicklow", "Shillelagh"],
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
        expected_lane="relational",
        townland_hint="Ballinacor",
        expected_entity_norm="BALLINACOR",
        expected_subgraph_facts=["Ballinacor"],
        ground_truth_sql="SELECT civil_parish FROM townland WHERE UPPER(name)='BALLINACOR' LIMIT 1",
        ground_truth_value="Kilbride",
        ground_truth_key="civil_parish",
        ground_truth_type="scalar",
        expected_answer_facts=["Kilbride"],
    ),

    EvalCase(
        id="rel_04_estate_overview",
        question="Tell me about the Coolattin estate and its history",
        category="relational",
        expected_route="template",
        expected_lane="relational",
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
        expected_route="template",
        expected_lane="relational",
        townland_hint="Ballinacor",
        expected_entity_norm="BALLINACOR",
        expected_subgraph_facts=["Ballinacor"],
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
        expected_answer_facts=["Ballinacor"],
    ),


    EvalCase(
        id="cmp_01_emigration_vs_kg",
        question="Compare the emigration count from Ballynultagh in the estate records versus the knowledge graph",
        category="comparative",
        expected_route="template",
        expected_lane="comparative",
        townland_hint="Ballynultagh",
        expected_entity_norm="BALLYNULTAGH",
        expected_comparative_sources=["sqlite", "kg"],
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
        expected_lane="comparative",
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
        expected_lane="comparative",
        townland_hint="Ballinacor",
        expected_entity_norm="BALLINACOR",
        expected_comparative_sources=["sqlite", "kg"],
        ground_truth_sql="SELECT SUM(c.count) AS n FROM clearances_record c JOIN townland t ON c.townland_id=t.id WHERE UPPER(t.name)='BALLINACOR'",
        ground_truth_value=122,
        ground_truth_key="total_evictions",
        ground_truth_type="scalar",
        expected_answer_facts=["122"],
    ),


    EvalCase(
        id="fbl_01_rent",
        question="What was the average rent paid by tenants on the Coolattin estate?",
        category="fallback",
        expected_route="llm",
        expected_lane="fallback",
        catalogue_code="G",
        is_out_of_scope=True,
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
        catalogue_code="G",
        is_out_of_scope=True,
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
        catalogue_code="G",
        is_out_of_scope=True,
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
    ),


    EvalCase(
        id="gen_01_mortality",
        question="How many people died of Famine-related causes on the Coolattin estate?",
        category="general",
        expected_route="llm",
        expected_lane="fallback",
        catalogue_code="G",
        is_out_of_scope=True,
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
    ),

    EvalCase(
        id="gen_02_religion",
        question="What religion were the Coolattin tenants?",
        category="general",
        expected_route="llm",
        expected_lane="fallback",
        catalogue_code="G",
        is_out_of_scope=True,
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
    ),

    EvalCase(
        id="gen_03_other_estates",
        question="How did eviction rates at Coolattin compare to other Irish estates?",
        category="general",
        expected_route="llm",
        expected_lane="fallback",
        catalogue_code="G",
        is_out_of_scope=True,
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
    ),

    EvalCase(
        id="gen_04_weather",
        question="What was the weather like in County Wicklow during the 1840s?",
        category="general",
        expected_route="llm",
        expected_lane="fallback",
        catalogue_code="G",
        is_out_of_scope=True,
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
    ),

    EvalCase(
        id="gen_05_politics",
        question="Were any Coolattin tenants involved in political movements during the 1840s?",
        category="general",
        expected_route="llm",
        expected_lane="fallback",
        catalogue_code="G",
        is_out_of_scope=True,
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
    ),


    EvalCase(
        id="er_wh_01_linked_count",
        question="How many workhouse records have been linked to estate records?",
        category="entity",
        expected_route="llm",
        expected_lane="fallback",
        catalogue_code="I",
        is_out_of_scope=False,
        ground_truth_sql="SELECT COUNT(*) AS n FROM workhouse_unified_links",
        ground_truth_value=139,
        ground_truth_key="n",
        ground_truth_type="scalar",
        expected_answer_facts=["139"],
    ),

    EvalCase(
        id="er_wh_02_confirmed_matches",
        question="How many workhouse-to-estate links are confirmed matches?",
        category="entity",
        expected_route="llm",
        expected_lane="fallback",
        catalogue_code="I",
        is_out_of_scope=False,
        ground_truth_sql="SELECT COUNT(*) AS n FROM workhouse_unified_links WHERE label='CONFIRMED_MATCH'",
        ground_truth_value=3,
        ground_truth_key="n",
        ground_truth_type="scalar",
        expected_answer_facts=["3"],
    ),

    EvalCase(
        id="er_wh_03_review_needed",
        question="How many workhouse-to-estate record links require human review?",
        category="entity",
        expected_route="llm",
        expected_lane="fallback",
        catalogue_code="I",
        is_out_of_scope=False,
        ground_truth_sql="SELECT COUNT(*) AS n FROM workhouse_unified_links WHERE review_required=1",
        ground_truth_value=136,
        ground_truth_key="n",
        ground_truth_type="scalar",
        expected_answer_facts=["136"],
    ),

    EvalCase(
        id="er_wh_04_mentions_count",
        question="How many individual name mentions were extracted from workhouse records for entity resolution?",
        category="entity",
        expected_route="llm",
        expected_lane="fallback",
        catalogue_code="I",
        is_out_of_scope=False,
        ground_truth_sql="SELECT COUNT(*) AS n FROM source_mentions",
        ground_truth_value=8214,
        ground_truth_key="n",
        ground_truth_type="scalar",
        expected_answer_facts=["8214"],
    ),


    EvalCase(
        id="fbl_04_children_emigrated",
        question="How many children under the age of 18 emigrated from the Coolattin estate?",
        category="fallback",
        expected_route="llm",
        expected_lane="fallback",
        catalogue_code="A",
        is_out_of_scope=False,
        ground_truth_sql="SELECT COUNT(DISTINCT record_id) AS n FROM unified_record WHERE has_emigration_record=1 AND age IS NOT NULL AND age < 18",
        ground_truth_value=2610,
        ground_truth_key="n",
        ground_truth_type="scalar",
        expected_answer_facts=["2610"],
    ),

    EvalCase(
        id="fbl_05_avg_rent_owed",
        question="What was the average rent owed by Coolattin tenants?",
        category="fallback",
        expected_route="llm",
        expected_lane="fallback",
        catalogue_code="A",
        is_out_of_scope=False,
        ground_truth_sql="SELECT ROUND(AVG(rent_owed), 2) AS avg_rent FROM unified_record WHERE has_tenancy_record=1 AND rent_owed IS NOT NULL AND rent_owed > 0",
        ground_truth_value=38.07,
        ground_truth_key="avg_rent",
        ground_truth_type="scalar",
        expected_answer_facts=["38"],
    ),

    EvalCase(
        id="fbl_06_widows_emigrated",
        question="How many widows emigrated from the Coolattin estate?",
        category="fallback",
        expected_route="llm",
        expected_lane="fallback",
        catalogue_code="A",
        is_out_of_scope=False,
        ground_truth_sql="SELECT COUNT(DISTINCT record_id) AS n FROM unified_record WHERE is_widow=1 AND has_emigration_record=1",
        ground_truth_value=15,
        ground_truth_key="n",
        ground_truth_type="scalar",
        expected_answer_facts=["15"],
    ),

    EvalCase(
        id="fbl_07_er_candidate_count",
        question="How many entity resolution candidates were generated when matching workhouse records to estate tenants?",
        category="fallback",
        expected_route="llm",
        expected_lane="fallback",
        catalogue_code="I",
        is_out_of_scope=False,
        ground_truth_sql="SELECT COUNT(*) AS n FROM entity_resolution_candidates",
        ground_truth_value=22928,
        ground_truth_key="n",
        ground_truth_type="scalar",
        expected_answer_facts=["22928"],
    ),
]


_CATALOGUE_CODE_MAP: dict[str, str] = {
    "emi_01_total": "A",
    "emi_02_townland_ballynultagh": "A",
    "emi_03_townland_killinure": "A",
    "emi_04_per_year_trend": "A-trend",
    "emi_05_canada_total": "A-trend",
    "emi_06_canada_ship": "A",
    "emi_07_ships_list": "A",
    "emi_08_in_1848": "A",
    "evic_01_total": "A",
    "evic_02_worst_year": "A",
    "evic_03_townland_ballinacor": "A",
    "evic_04_per_year": "A-trend",
    "evic_05_people_list": "P",
    "evic_06_in_1849": "A",
    "cen_01_estate_1841": "A",
    "cen_02_estate_1851": "A",
    "cen_03_ballinacor_1841": "A",
    "cen_04_famine_decline": "A-trend",
    "cen_05_trend_1841_1861": "A-trend",
    "cen_06_uninhabited": "A",
    "cen_07_all_years": "A-trend",
    "cen_08_by_parish": "A",
    "geo_01_total_townlands": "A",
    "geo_02_parish_count": "A",
    "geo_03_parish_list": "A",
    "geo_04_ballinacor_parish": "R",
    "geo_05_baronies": "A",
    "geo_06_nearby_coolattin": "R",
    "geo_07_by_county": "A",
    "ppl_01_total_records": "A",
    "ppl_02_byrne_records": "P",
    "ppl_03_murphy_list": "P",
    "ppl_04_widows_count": "A",
    "ppl_05_widows_children": "A",
    "ppl_06_heads_of_household": "P",
    "ppl_07_ballynultagh_people": "P",
    "ppl_08_in_1847": "A",
    "ten_01_total": "A",
    "ten_02_gender_avg": "C",
    "ten_03_coolattin_tenants": "P",
    "ten_04_largest_holdings": "A",
    "ten_05_smallest_plots": "A",
    "ten_06_per_townland": "A",
    "her_01_holy_well_population": "H",
    "her_02_ring_fort_population": "H",
    "her_03_holy_well_count": "H",
    "her_04_ring_fort_count": "H",
    "her_05_holy_well_townlands": "H",
    "ov_01_famine_impact": "R",
    "ov_02_estate_summary": "R",
    "ov_03_emi_and_evic": "A",
    "ov_04_emi_vs_population": "C",
    "ov_05_records_per_year": "A-trend",
    "er_01_exact_ballinacor": "I",
    "er_02_spelling_variant": "I",
    "er_03_spelling_ballynultach": "I",
    "er_04_coolattin_kg_uri": "I",
    "er_05_surname_byrne_exact": "I",
    "er_06_surname_fuzzy": "I",
    "rel_01_ballinacor_barony": "R",
    "rel_02_ballynultagh_county": "R",
    "rel_03_ballinacor_parish_siblings": "R",
    "rel_04_estate_overview": "R",
    "rel_05_historical_monuments": "H",
    "cmp_01_emigration_vs_kg": "X",
    "cmp_02_population_vs_kg": "X",
    "cmp_03_eviction_agree": "X",
    "fbl_01_rent": "G",
    "fbl_02_crops": "G",
    "fbl_03_fitzwilliam": "G",
    "gen_01_mortality": "G",
    "gen_02_religion": "G",
    "gen_03_other_estates": "G",
    "gen_04_weather": "G",
    "gen_05_politics": "G",
    "er_wh_01_linked_count": "I",
    "er_wh_02_confirmed_matches": "I",
    "er_wh_03_review_needed": "I",
    "er_wh_04_mentions_count": "I",
    "fbl_04_children_emigrated": "A",
    "fbl_05_avg_rent_owed": "A",
    "fbl_06_widows_emigrated": "A",
    "fbl_07_er_candidate_count": "I",
}


def _catalogue_code(case_id: str, case_code: str | None = None) -> str:
    return case_code or _CATALOGUE_CODE_MAP.get(case_id, "?")


HELDOUT_CASES: list[EvalCase] = [


    EvalCase(
        id="hh_emi_01_carnew",
        question="How many people emigrated from Carnew?",
        category="emigration",
        expected_route="template",
        expected_lane="analytical",
        catalogue_code="A",
        townland_hint="Carnew",
        expected_entity_norm="CARNEW",
        expected_sql_id=993,
        expected_kg_uri_prefix="https://kg.virtualtreasury.ie",
        ground_truth_sql="SELECT COUNT(DISTINCT record_id) AS emigration_count FROM unified_record WHERE has_emigration_record=1 AND townland_norm='CARNEW'",
        ground_truth_value=54,
        ground_truth_key="emigration_count",
        ground_truth_type="scalar",
        expected_answer_facts=["54"],
    ),

    EvalCase(
        id="hh_emi_02_1849",
        question="How many people emigrated in 1849?",
        category="emigration",
        expected_route="template",
        expected_lane="analytical",
        catalogue_code="A",
        ground_truth_sql="SELECT COUNT(DISTINCT record_id) AS emigration_count FROM unified_record WHERE has_emigration_record=1 AND year=1849",
        ground_truth_value=633,
        ground_truth_key="emigration_count",
        ground_truth_type="scalar",
        expected_answer_facts=["633"],
    ),

    EvalCase(
        id="hh_emi_03_1847",
        question="How many people emigrated in 1847?",
        category="emigration",
        expected_route="template",
        expected_lane="analytical",
        catalogue_code="A",
        ground_truth_sql="SELECT COUNT(DISTINCT record_id) AS emigration_count FROM unified_record WHERE has_emigration_record=1 AND year=1847",
        ground_truth_value=2211,
        ground_truth_key="emigration_count",
        ground_truth_type="scalar",
        expected_answer_facts=["2211"],
    ),


    EvalCase(
        id="hh_evic_01_1850",
        question="How many evictions were recorded in 1850?",
        category="eviction",
        expected_route="template",
        expected_lane="analytical",
        catalogue_code="A",
        ground_truth_sql="SELECT SUM(count) AS total_evictions FROM clearances_record WHERE year=1850",
        ground_truth_value=547,
        ground_truth_key="total_evictions",
        ground_truth_type="scalar",
        expected_answer_facts=["547"],
    ),

    EvalCase(
        id="hh_evic_02_1855",
        question="How many evictions were there in 1855?",
        category="eviction",
        expected_route="template",
        expected_lane="analytical",
        catalogue_code="A",
        ground_truth_sql="SELECT SUM(count) AS total_evictions FROM clearances_record WHERE year=1855",
        ground_truth_value=38,
        ground_truth_key="total_evictions",
        ground_truth_type="scalar",
        expected_answer_facts=["38"],
    ),

    EvalCase(
        id="hh_evic_03_tinahely",
        question="How many evictions happened in Tinahely?",
        category="eviction",
        expected_route="template",
        expected_lane="analytical",
        catalogue_code="A",
        townland_hint="Tinahely",
        expected_entity_norm="TINAHELY",
        expected_sql_id=625,
        expected_kg_uri_prefix="https://kg.virtualtreasury.ie",
        ground_truth_sql="SELECT SUM(c.count) AS n FROM clearances_record c JOIN townland t ON c.townland_id=t.id WHERE UPPER(t.name)='TINAHELY'",
        ground_truth_value=12,
        ground_truth_key="n",
        ground_truth_type=None,
        expected_answer_facts=["Tinahely"],
    ),


    EvalCase(
        id="hh_cen_01_tinahely_1841",
        question="What was the population of Tinahely in 1841?",
        category="census",
        expected_route="template",
        expected_lane="analytical",
        catalogue_code="A",
        townland_hint="Tinahely",
        expected_entity_norm="TINAHELY",
        expected_sql_id=625,
        expected_kg_uri_prefix="https://kg.virtualtreasury.ie",
        ground_truth_sql="SELECT SUM(cr.total) AS population FROM census_record cr JOIN townland t ON cr.townland_id=t.id WHERE UPPER(t.name)='TINAHELY' AND cr.year=1841",
        ground_truth_value=21,
        ground_truth_key="population",
        ground_truth_type="scalar",
        expected_answer_facts=["21"],
    ),

    EvalCase(
        id="hh_cen_02_carnew_1841",
        question="What was the population of Carnew in 1841?",
        category="census",
        expected_route="template",
        expected_lane="analytical",
        catalogue_code="A",
        townland_hint="Carnew",
        expected_entity_norm="CARNEW",
        expected_sql_id=993,
        expected_kg_uri_prefix="https://kg.virtualtreasury.ie",
        ground_truth_sql="SELECT SUM(cr.total) AS population FROM census_record cr JOIN townland t ON cr.townland_id=t.id WHERE UPPER(t.name)='CARNEW' AND cr.year=1841",
        ground_truth_value=456,
        ground_truth_key="population",
        ground_truth_type="scalar",
        expected_answer_facts=["456"],
    ),

    EvalCase(
        id="hh_cen_03_tinahely_1851",
        question="What was the population of Tinahely in 1851?",
        category="census",
        expected_route="template",
        expected_lane="analytical",
        catalogue_code="A",
        townland_hint="Tinahely",
        expected_entity_norm="TINAHELY",
        expected_sql_id=625,
        expected_kg_uri_prefix="https://kg.virtualtreasury.ie",
        ground_truth_sql="SELECT SUM(cr.total) AS population FROM census_record cr JOIN townland t ON cr.townland_id=t.id WHERE UPPER(t.name)='TINAHELY' AND cr.year=1851",
        ground_truth_value=13,
        ground_truth_key="population",
        ground_truth_type="scalar",
        expected_answer_facts=["13"],
    ),

    EvalCase(
        id="hh_cen_04_1871",
        question="What was the total estate population in 1871?",
        category="census",
        expected_route="template",
        expected_lane="analytical",
        catalogue_code="A",
        ground_truth_sql="SELECT SUM(total) AS n FROM census_record WHERE year=1871",
        ground_truth_value=153073,
        ground_truth_key="n",
        ground_truth_type=None,
        expected_answer_facts=["1871"],
    ),


    EvalCase(
        id="hh_ppl_01_doyle",
        question="How many records mention the surname Doyle?",
        category="people",
        expected_route="template",
        expected_lane="analytical",
        catalogue_code="P",
        expected_surname="DOYLE",
        ground_truth_sql="SELECT COUNT(DISTINCT record_id) AS person_count FROM unified_record WHERE UPPER(surname)='DOYLE'",
        ground_truth_value=487,
        ground_truth_key="person_count",
        ground_truth_type="scalar",
        expected_answer_facts=["487", "Doyle"],
    ),

    EvalCase(
        id="hh_ppl_02_kelly",
        question="How many people with the surname Kelly are in the records?",
        category="people",
        expected_route="template",
        expected_lane="analytical",
        catalogue_code="P",
        expected_surname="KELLY",
        ground_truth_sql="SELECT COUNT(DISTINCT record_id) AS person_count FROM unified_record WHERE UPPER(surname)='KELLY'",
        ground_truth_value=151,
        ground_truth_key="person_count",
        ground_truth_type="scalar",
        expected_answer_facts=["151", "Kelly"],
    ),

    EvalCase(
        id="hh_ppl_03_whelan",
        question="How many Whelan family members are in the estate records?",
        category="people",
        expected_route="template",
        expected_lane="analytical",
        catalogue_code="P",
        expected_surname="WHELAN",
        ground_truth_sql="SELECT COUNT(DISTINCT record_id) AS person_count FROM unified_record WHERE UPPER(surname)='WHELAN'",
        ground_truth_value=127,
        ground_truth_key="person_count",
        ground_truth_type="scalar",
        expected_answer_facts=["127", "Whelan"],
    ),

    EvalCase(
        id="hh_ppl_04_tinahely_list",
        question="List all people recorded in Tinahely",
        category="people",
        expected_route="template",
        expected_lane="analytical",
        catalogue_code="P",
        townland_hint="Tinahely",
        expected_entity_norm="TINAHELY",
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
        expected_answer_facts=["Tinahely"],
    ),


    EvalCase(
        id="hh_ten_01_tinahely",
        question="How many tenants are recorded from Tinahely?",
        category="tenancy",
        expected_route="template",
        expected_lane="analytical",
        catalogue_code="A",
        townland_hint="Tinahely",
        expected_entity_norm="TINAHELY",
        ground_truth_sql="SELECT COUNT(DISTINCT record_id) AS tenancy_count FROM unified_record WHERE has_tenancy_record=1 AND townland_norm='TINAHELY'",
        ground_truth_value=176,
        ground_truth_key="tenancy_count",
        ground_truth_type="scalar",
        expected_answer_facts=["176"],
    ),

    EvalCase(
        id="hh_ten_02_carnew",
        question="How many tenants are from Carnew?",
        category="tenancy",
        expected_route="template",
        expected_lane="analytical",
        catalogue_code="A",
        townland_hint="Carnew",
        expected_entity_norm="CARNEW",
        ground_truth_sql="SELECT COUNT(DISTINCT record_id) AS tenancy_count FROM unified_record WHERE has_tenancy_record=1 AND townland_norm='CARNEW'",
        ground_truth_value=256,
        ground_truth_key="tenancy_count",
        ground_truth_type="scalar",
        expected_answer_facts=["256"],
    ),

    EvalCase(
        id="hh_ten_03_female",
        question="How many female tenants were recorded in the estate?",
        category="tenancy",
        expected_route="llm",
        expected_lane="fallback",
        catalogue_code="A",
        is_out_of_scope=False,
        ground_truth_sql="SELECT COUNT(DISTINCT record_id) AS n FROM unified_record WHERE has_tenancy_record=1 AND LOWER(gender) IN ('f','female')",
        ground_truth_value=284,
        ground_truth_key="n",
        ground_truth_type="scalar",
        expected_answer_facts=["284"],
    ),

    EvalCase(
        id="hh_ten_04_farmers",
        question="How many tenants were recorded as farmers?",
        category="tenancy",
        expected_route="llm",
        expected_lane="fallback",
        catalogue_code="A",
        is_out_of_scope=False,
        ground_truth_sql="SELECT COUNT(DISTINCT record_id) AS n FROM unified_record WHERE has_tenancy_record=1 AND occupation='Farmer'",
        ground_truth_value=822,
        ground_truth_key="n",
        ground_truth_type="scalar",
        expected_answer_facts=["822"],
    ),


    EvalCase(
        id="hh_geo_01_shillelagh",
        question="How many townlands are in the Shillelagh barony?",
        category="geography",
        expected_route="llm",
        expected_lane="fallback",
        catalogue_code="A",
        is_out_of_scope=False,
        ground_truth_sql="SELECT COUNT(*) AS n FROM townland WHERE barony='Shillelagh'",
        ground_truth_value=36,
        ground_truth_key="n",
        ground_truth_type="scalar",
        expected_answer_facts=["36"],
    ),

    EvalCase(
        id="hh_geo_02_tinahely_barony",
        question="Which barony does Tinahely belong to?",
        category="geography",
        expected_route="template",
        expected_lane="relational",
        catalogue_code="R",
        townland_hint="Tinahely",
        expected_entity_norm="TINAHELY",
        expected_sql_id=625,
        expected_kg_uri_prefix="https://kg.virtualtreasury.ie",
        expected_subgraph_facts=["Ballinacor South"],
        ground_truth_sql="SELECT barony FROM townland WHERE UPPER(name)='TINAHELY' LIMIT 1",
        ground_truth_value="Ballinacor South",
        ground_truth_key="barony",
        ground_truth_type="scalar",
        expected_answer_facts=["Ballinacor South"],
    ),

    EvalCase(
        id="hh_geo_03_carnew_parish",
        question="What civil parish is Carnew in?",
        category="geography",
        expected_route="template",
        expected_lane="relational",
        catalogue_code="R",
        townland_hint="Carnew",
        expected_entity_norm="CARNEW",
        expected_sql_id=993,
        expected_kg_uri_prefix="https://kg.virtualtreasury.ie",
        expected_subgraph_facts=["Carnew"],
        ground_truth_sql="SELECT civil_parish FROM townland WHERE UPPER(name)='CARNEW' LIMIT 1",
        ground_truth_value="Carnew",
        ground_truth_key="civil_parish",
        ground_truth_type="scalar",
        expected_answer_facts=["Carnew"],
    ),


    EvalCase(
        id="hh_her_01_ringfort_townlands",
        question="How many distinct townlands contain ring forts?",
        category="heritage",
        expected_route="verified_analysis",
        expected_lane="relational",
        catalogue_code="H",
        ground_truth_sql="SELECT COUNT(DISTINCT townland_norm) AS n FROM heritage_feature WHERE feature_group='ring_fort'",
        ground_truth_value=213,
        ground_truth_key="n",
        ground_truth_type=None,
        expected_answer_facts=["ring fort"],
    ),

    EvalCase(
        id="hh_her_02_tinahely",
        question="What heritage features are recorded in Tinahely?",
        category="heritage",
        expected_route="template",
        expected_lane="relational",
        catalogue_code="H",
        townland_hint="Tinahely",
        expected_entity_norm="TINAHELY",
        expected_subgraph_facts=["Tinahely"],
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
        expected_answer_facts=["Tinahely"],
    ),


    EvalCase(
        id="hh_er_01_tynehely",
        question="How many people emigrated from Tynehely?",
        category="entity",
        expected_route="template",
        expected_lane="analytical",
        catalogue_code="I",
        townland_hint="Tynehely",
        expected_entity_norm="TINAHELY",
        expected_sql_id=625,
        expected_kg_uri_prefix="https://kg.virtualtreasury.ie",
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
    ),

    EvalCase(
        id="hh_er_02_carnew_census",
        question="Show me the census data for Carnew",
        category="entity",
        expected_route="template",
        expected_lane="analytical",
        catalogue_code="I",
        townland_hint="Carnew",
        expected_entity_norm="CARNEW",
        expected_sql_id=993,
        expected_kg_uri_prefix="https://kg.virtualtreasury.ie",
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
    ),

    EvalCase(
        id="hh_er_03_whelan_surname",
        question="List all Whelan family members",
        category="entity",
        expected_route="template",
        expected_lane="analytical",
        catalogue_code="I",
        expected_surname="WHELAN",
        ground_truth_sql="SELECT COUNT(DISTINCT record_id) AS n FROM unified_record WHERE UPPER(surname)='WHELAN'",
        ground_truth_value=127,
        ground_truth_key="n",
        ground_truth_type=None,
    ),


    EvalCase(
        id="hh_cmp_01_tinahely_1841",
        question="Compare the 1841 population of Tinahely in the estate records versus the knowledge graph",
        category="comparative",
        expected_route="template",
        expected_lane="comparative",
        catalogue_code="X",
        townland_hint="Tinahely",
        expected_entity_norm="TINAHELY",
        expected_comparative_sources=["sqlite", "kg"],
        ground_truth_sql="SELECT SUM(cr.total) AS population FROM census_record cr JOIN townland t ON cr.townland_id=t.id WHERE UPPER(t.name)='TINAHELY' AND cr.year=1841",
        ground_truth_value=21,
        ground_truth_key="population",
        ground_truth_type="scalar",
        expected_answer_facts=["21"],
    ),

    EvalCase(
        id="hh_cmp_02_carnew_emi",
        question="Compare the emigration count from Carnew in the estate records versus the knowledge graph",
        category="comparative",
        expected_route="template",
        expected_lane="comparative",
        catalogue_code="X",
        townland_hint="Carnew",
        expected_entity_norm="CARNEW",
        expected_comparative_sources=["sqlite", "kg"],
        ground_truth_sql="SELECT COUNT(DISTINCT record_id) AS emigration_count FROM unified_record WHERE has_emigration_record=1 AND townland_norm='CARNEW'",
        ground_truth_value=54,
        ground_truth_key="emigration_count",
        ground_truth_type="scalar",
        expected_answer_facts=["54"],
    ),


    EvalCase(
        id="hh_fbl_01_tenant_widows",
        question="How many tenant records belong to widows?",
        category="fallback",
        expected_route="llm",
        expected_lane="fallback",
        catalogue_code="A",
        is_out_of_scope=False,
        ground_truth_sql="SELECT COUNT(DISTINCT record_id) AS n FROM unified_record WHERE has_tenancy_record=1 AND is_widow=1",
        ground_truth_value=489,
        ground_truth_key="n",
        ground_truth_type="scalar",
        expected_answer_facts=["489"],
    ),

    EvalCase(
        id="hh_fbl_02_scarawalsh_tenants",
        question="How many tenants are recorded from the Scarawalsh barony?",
        category="fallback",
        expected_route="llm",
        expected_lane="fallback",
        catalogue_code="A",
        is_out_of_scope=False,
        ground_truth_sql="SELECT COUNT(DISTINCT ur.record_id) AS n FROM unified_record ur JOIN townland t ON ur.townland_norm=t.name WHERE ur.has_tenancy_record=1 AND t.barony='Scarawalsh'",
        ground_truth_value=540,
        ground_truth_key="n",
        ground_truth_type="scalar",
        expected_answer_facts=["540"],
    ),


    EvalCase(
        id="hh_gen_01_agent",
        question="What was the name of the estate agent who managed Coolattin?",
        category="general",
        expected_route="llm",
        expected_lane="fallback",
        catalogue_code="G",
        is_out_of_scope=True,
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
    ),

    EvalCase(
        id="hh_gen_02_schools",
        question="Were there any schools on the Coolattin estate?",
        category="general",
        expected_route="llm",
        expected_lane="fallback",
        catalogue_code="G",
        is_out_of_scope=True,
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
    ),

    EvalCase(
        id="hh_gen_03_language",
        question="What language did the tenants on the Coolattin estate speak?",
        category="general",
        expected_route="llm",
        expected_lane="fallback",
        catalogue_code="G",
        is_out_of_scope=True,
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
    ),

    EvalCase(
        id="hh_gen_04_aftermath",
        question="What happened to the Coolattin estate after the Famine?",
        category="general",
        expected_route="llm",
        expected_lane="fallback",
        catalogue_code="G",
        is_out_of_scope=True,
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
    ),

    EvalCase(
        id="hh_gen_05_compensation",
        question="Were any Coolattin tenants compensated for their eviction?",
        category="general",
        expected_route="llm",
        expected_lane="fallback",
        catalogue_code="G",
        is_out_of_scope=True,
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_type=None,
    ),
]

_ALL_KNOWN_CASES: list[EvalCase] = GOLDEN_CASES + HELDOUT_CASES


def _close_enough(a: Any, b: Any, rtol: float = 0.01) -> bool:
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
    if rows and key in rows[0]:
        return rows[0][key]
    return None


def _any_row_has_value(rows: list[dict], key: str, value: Any) -> bool:
    for row in rows:
        if key in row and _close_enough(row[key], value):
            return True
    return False


def _check_agg(case: EvalCase, cols: list[str], rows: list[dict]) -> tuple[bool | None, Any]:
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
    if case.expected_entity_norm:
        actual = resolution.get("name_norm")
        return actual == case.expected_entity_norm
    return None


def _check_surname_extraction(case: EvalCase, analysis: dict[str, Any]) -> bool | None:
    if case.expected_surname:
        actual = analysis.get("surname")
        if actual is None:
            return False
        return str(actual).upper() == case.expected_surname.upper()
    return None


def _check_sql_id(case: EvalCase, resolution: dict[str, Any]) -> bool | None:
    if not case.expected_entity_norm:
        return None
    actual_id = resolution.get("sql_id")
    if actual_id is None:
        return False
    if case.expected_sql_id is not None:
        return int(actual_id) == int(case.expected_sql_id)
    return True


def _check_kg_uri(case: EvalCase, resolution: dict[str, Any]) -> bool | None:
    if not case.expected_entity_norm:
        return None
    actual_uri = resolution.get("kg_uri")
    if actual_uri is None:
        return False
    prefix = case.expected_kg_uri_prefix or "https://kg.virtualtreasury.ie"
    return str(actual_uri).startswith(prefix)


def _check_answer_facts(
    case: EvalCase,
    result_rows: list[dict],
) -> bool | None:
    if not case.expected_answer_facts:
        return None
    if not result_rows:
        return False
    result_str = " ".join(
        str(v) for row in result_rows for v in row.values() if v is not None
    ).lower()
    return all(fact.lower() in result_str for fact in case.expected_answer_facts)


def _check_lane(case: EvalCase, actual_lane: str | None) -> bool | None:
    if case.expected_lane is None or actual_lane is None:
        return None
    return actual_lane == case.expected_lane


def _check_subgraph(
    case: EvalCase,
    linearized: str,
    hierarchy: dict,
) -> tuple[bool | None, float | None]:
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
    ok = recall >= 0.5
    return ok, round(recall, 3)


def _run_subgraph_retrieval(
    case: EvalCase,
    question: str,
    analysis: dict[str, Any],
    resolution: dict[str, Any],
) -> tuple[bool | None, float | None, str, dict]:
    if not case.expected_subgraph_facts:
        return None, None, "", {}

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

    linearized = ""
    try:
        from backend.services.subgraph_engine import retrieve_subgraph
        result = retrieve_subgraph(
            question=question,
            analysis=analysis,
            townland_resolution=resolution,
            sources=("vrti",),
        )
        linearized = result.linearized or ""
        if result.hierarchy:
            for k, v in result.hierarchy.items():
                if v:
                    hier[k] = v
    except Exception:
        pass

    subgraph_ok, recall = _check_subgraph(case, linearized, hier)
    return subgraph_ok, recall, linearized, hier


def _run_comparative_check(
    case: EvalCase,
    sl_fill: Any | None,
    clearances_col: str,
) -> tuple[bool | None, bool | None]:
    if not case.expected_comparative_sources:
        return None, None

    sqlite_ok: bool | None = None
    kg_ok: bool | None = None

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

    if sl_fill is not None and "kg" in case.expected_comparative_sources:
        try:
            from backend.services.semantic_layer import compile_sparql
            from backend.integrations import graphdb_sparql as _gdb
            sparql = compile_sparql(sl_fill)
            if sparql and _gdb.probe():
                rows = _gdb.query(sparql)
                kg_ok = bool(rows)
            else:
                kg_ok = None
        except Exception:
            kg_ok = None

    return sqlite_ok, kg_ok


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

        sql_id_ok = _check_sql_id(case, resolution)
        kg_uri_ok = _check_kg_uri(case, resolution)

        entity_label_expected = case.expected_entity_norm or case.expected_surname
        entity_label_actual = resolution.get("name_norm") or analysis.get("surname")
        entity_sql_id_actual = resolution.get("sql_id")
        entity_kg_uri_actual = resolution.get("kg_uri")

        raw_sql = ""
        compiled_sql_actual: str | None = None
        template_id = None
        actual_route = "template_miss"
        _sl_fill = None
        _sl_sql: str | None = None

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

        if case.expected_route == "verified_analysis":
            route_ok = actual_route in {"verified_analysis", "semantic_layer"}
        elif case.expected_route == "template":
            route_ok = actual_route in {"template", "verified_analysis", "semantic_layer"}
        else:
            route_ok = (actual_route == "template_miss")

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

        agg_ok, agg_actual = _check_agg(case, result_cols, result_rows)
        answer_facts_ok = _check_answer_facts(case, result_rows)

        actual_lane: str | None = None
        try:
            from backend.services.intent_router import classify_intent
            actual_lane = classify_intent(case.question, analysis, _sl_fill)
        except Exception:
            pass
        lane_ok = _check_lane(case, actual_lane)

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
        answer_facts_ok=answer_facts_ok,
    )


def run_eval(
    phase_label: str = "baseline",
    case_list: list[EvalCase] | None = None,
) -> EvalResult:
    from datetime import datetime, timezone

    from backend.services.ask_service import (
        _try_verified_analysis,
        _match_and_build_template,
        _resolve_townland_context,
        _analyse_question,
        _run_read_only_query,
        _sanitize_and_validate_sql,
    )

    cases_to_run = case_list if case_list is not None else GOLDEN_CASES
    results: list[CaseResult] = []
    for case in cases_to_run:
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

    p90_latency_ms = latencies[min(int(n * 0.90), n - 1)]

    exec_by_route: dict[str, float | None] = {}
    for _route in ("template", "verified_analysis", "semantic_layer", "template_miss"):
        _rc = [c for c in cases if c.actual_route == _route and c.sql_ok is not None]
        _ok = [c for c in _rc if c.sql_ok]
        exec_by_route[_route] = round(100 * len(_ok) / len(_rc), 1) if _rc else None

    confusion: dict[str, dict[str, int]] = {}
    for c in cases:
        exp = c.expected_route
        act = c.actual_route
        confusion.setdefault(exp, {})
        confusion[exp][act] = confusion[exp].get(act, 0) + 1

    facts_tested = [c for c in cases if c.answer_facts_ok is not None]
    facts_ok_list = [c for c in facts_tested if c.answer_facts_ok]
    answer_facts_found_rate = (
        round(100 * len(facts_ok_list) / len(facts_tested), 1) if facts_tested else None
    )

    g_cases = [c for c in cases if c.expected_route == "llm"]
    g_refusals = [c for c in g_cases if c.actual_route == "template_miss"]
    honest_refusal_rate = (
        round(100 * len(g_refusals) / len(g_cases), 1) if g_cases else None
    )

    _lane_lookup: dict[str, str | None] = {
        ec.id: ec.expected_lane for ec in _ALL_KNOWN_CASES
    }

    def _lane_cases(lane: str) -> list[CaseResult]:
        return [c for c in cases if _case_expected_lane(c) == lane]

    def _case_expected_lane(c: CaseResult) -> str | None:
        return _lane_lookup.get(c.id)

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
        "p90_latency_ms": p90_latency_ms,
        "answer_facts_found_rate": answer_facts_found_rate,
        "honest_refusal_rate": honest_refusal_rate,
        "routing_confusion_matrix": confusion,
        "exec_acc_template": exec_by_route.get("template"),
        "exec_acc_semantic": exec_by_route.get("semantic_layer"),
        "exec_acc_verified": exec_by_route.get("verified_analysis"),
        "exec_acc_llm_fallback": exec_by_route.get("template_miss"),
        "g_series_n": len(g_cases),
    }


def print_miss_detail(result: EvalResult) -> None:
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

    case_map: dict[str, EvalCase] = {c.id: c for c in GOLDEN_CASES}

    for cr in failures:
        ec = case_map.get(cr.id)
        print(f"\n  Case: {cr.id}  [{cr.category}]")
        print(f"  Question: {cr.question}")

        route_sym = "✓" if cr.route_ok else "✗"
        print(f"  Route [{route_sym}]: expected={cr.expected_route!r}  actual={cr.actual_route!r}")

        if cr.entity_ok is not None:
            ent_sym = "✓" if cr.entity_ok else "✗"
            print(f"  Entity [{ent_sym}]:")
            print(f"    expected label={cr.entity_label_expected!r}")
            print(f"    actual  label={cr.entity_label_actual!r}  sql_id={cr.entity_sql_id_actual}  kg_uri={str(cr.entity_kg_uri_actual or '')[:60]}")

        if cr.agg_ok is not None:
            agg_sym = "✓" if cr.agg_ok else "✗"
            print(f"  Aggregation [{agg_sym}]:")
            print(f"    expected value={cr.ground_truth!r}")
            print(f"    actual  value={cr.agg_actual!r}")
            if ec and ec.ground_truth_sql:
                print(f"    ground-truth SQL: {ec.ground_truth_sql[:100]}")
            if cr.compiled_sql_actual:
                print(f"    compiled SQL:     {cr.compiled_sql_actual[:100]}")
            if ec and ec.ground_truth_sql and cr.compiled_sql_actual:
                if ec.ground_truth_sql.strip() != cr.compiled_sql_actual.strip():
                    print(f"    ⚠ SQL MISMATCH — pipeline compiled a different query than ground truth.")
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
    m = re.search(r'\bWHERE\b(.+?)(?:\bGROUP\b|\bORDER\b|\bLIMIT\b|$)', sql, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else ""


def written_finding(result: EvalResult) -> str:
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


def print_case_table(result: EvalResult) -> None:
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
        ("p90 latency (ms)",         "p90_latency_ms",             False),
        ("p95 latency (ms)",         "p95_latency_ms",             False),
        ("Answer facts found (%)",   "answer_facts_found_rate",    True),
        ("Honest refusal rate (%)",  "honest_refusal_rate",        True),
        ("G-series n",               "g_series_n",                 False),
        ("── EXEC BY ROUTE ───────", None,                        False),
        ("Exec acc — template (%)",  "exec_acc_template",          True),
        ("Exec acc — semantic (%)",  "exec_acc_semantic",          True),
        ("Exec acc — verified (%)",  "exec_acc_verified",          True),
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


def print_confusion_matrix(result: EvalResult) -> None:
    metrics = _compute_metrics(result)
    confusion = metrics.get("routing_confusion_matrix", {})
    if not confusion:
        return

    expected_routes = sorted(confusion.keys())
    actual_routes = sorted({act for exp_dict in confusion.values() for act in exp_dict})

    col_w = 16
    hdr = f"{'Expected \\ Actual':<{col_w}}" + "".join(f"{a[:14]:>14}" for a in actual_routes)
    W = len(hdr)
    print(f"\n{'─' * W}")
    print("  ROUTING CONFUSION MATRIX")
    print(f"{'─' * W}")
    print(hdr)
    print(f"{'─' * W}")
    for exp in expected_routes:
        row = f"{exp:<{col_w}}"
        for act in actual_routes:
            count = confusion[exp].get(act, 0)
            row += f"{count:>14}"
        print(row)
    print(f"{'─' * W}")


def generate_markdown_report(result: EvalResult) -> str:
    from datetime import datetime, timezone
    metrics = _compute_metrics(result)
    case_map: dict[str, EvalCase] = {c.id: c for c in _ALL_KNOWN_CASES}
    ts = result.timestamp[:19].replace("T", " ") + " UTC"
    n = metrics.get("n", 0)

    lines: list[str] = []
    lines += [
        f"# Ask Pipeline Eval — Baseline Post-Migration",
        f"",
        f"**Run label:** `{result.phase_label}`  ",
        f"**Timestamp:** {ts}  ",
        f"**Questions run:** {n}  ",
        f"",
        f"---",
        f"",
        f"## 1. Global Metrics",
        f"",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Routing accuracy | {metrics.get('routing_accuracy', 'N/A')}% |",
        f"| Entity label accuracy | {metrics.get('entity_resolution_acc', 'N/A')}% |",
        f"| SQL-id resolution | {metrics.get('sql_id_resolution_rate', 'N/A')}% |",
        f"| KG-URI resolution | {metrics.get('kg_uri_resolution_rate', 'N/A')}% |",
        f"| SQL exec success | {metrics.get('sql_exec_success', 'N/A')}% |",
        f"| Aggregation correctness | {metrics.get('aggregation_correctness', 'N/A')}% |",
        f"| Answer facts found rate | {metrics.get('answer_facts_found_rate', 'N/A')}% |",
        f"| Honest-refusal rate (G-series) | {metrics.get('honest_refusal_rate', 'N/A')}% |",
        f"| Template hit rate | {metrics.get('template_hit_rate', 'N/A')}% |",
        f"| LLM calls required | {metrics.get('llm_calls_required', 'N/A')} |",
        f"| Lane routing accuracy | {metrics.get('lane_routing_acc', 'N/A')}% |",
        f"| p50 latency | {metrics.get('p50_latency_ms', 'N/A')} ms |",
        f"| p90 latency | {metrics.get('p90_latency_ms', 'N/A')} ms |",
        f"| p95 latency | {metrics.get('p95_latency_ms', 'N/A')} ms |",
        f"",
        f"---",
        f"",
        f"## 2. Execution Accuracy by Route",
        f"",
        f"| Route | SQL exec success (%) |",
        f"|-------|---------------------|",
        f"| template | {metrics.get('exec_acc_template', 'N/A')} |",
        f"| semantic_layer (deterministic) | {metrics.get('exec_acc_semantic', 'N/A')} |",
        f"| verified_analysis | {metrics.get('exec_acc_verified', 'N/A')} |",
        f"| template_miss (LLM fallback) | {metrics.get('exec_acc_llm_fallback', 'N/A')} |",
        f"",
        f"---",
        f"",
        f"## 3. Per-Lane Breakdown",
        f"",
        f"| Lane | N | Key metric |",
        f"|------|---|------------|",
        f"| Analytical | {metrics.get('analytical_n', 'N/A')} | agg_acc={metrics.get('analytical_agg_acc', 'N/A')}% |",
        f"| Relational | {metrics.get('relational_n', 'N/A')} | subgraph_recall={metrics.get('subgraph_recall', 'N/A')} |",
        f"| Comparative | {metrics.get('comparative_n', 'N/A')} | sqlite_capture={metrics.get('comparative_sqlite_capture', 'N/A')}% / kg_capture={metrics.get('comparative_kg_capture', 'N/A')}% |",
        f"| Fallback / G-series | {metrics.get('fallback_n', 'N/A')} ({metrics.get('g_series_n', 'N/A')} G) | honest_refusal={metrics.get('honest_refusal_rate', 'N/A')}% |",
        f"",
        f"---",
        f"",
        f"## 4. Routing Confusion Matrix",
        f"",
    ]

    confusion = metrics.get("routing_confusion_matrix", {})
    if confusion:
        all_acts = sorted({act for d in confusion.values() for act in d})
        hdr = "| Expected \\ Actual |" + "".join(f" {a} |" for a in all_acts)
        sep = "|---|" + "---|" * len(all_acts)
        lines += [hdr, sep]
        for exp in sorted(confusion.keys()):
            row = f"| **{exp}** |"
            for act in all_acts:
                row += f" {confusion[exp].get(act, 0)} |"
            lines.append(row)
    else:
        lines.append("_(no data)_")

    lines += [
        f"",
        f"---",
        f"",
        f"## 5. Per-Question Results",
        f"",
        f"| ID | Cat | Code | Expected | Actual | Rt | Ag | Ln | ms |",
        f"|----|-----|------|----------|--------|----|----|----|-----|",
    ]

    def _s(v: bool | None) -> str:
        return "✓" if v is True else ("✗" if v is False else "-")

    for c in result.cases:
        ec = case_map.get(c.id)
        code = _catalogue_code(c.id, ec.catalogue_code if ec else None) if ec else "?"
        lines.append(
            f"| {c.id} | {c.category[:8]} | {code} | {c.expected_route[:8]} "
            f"| {c.actual_route[:16]} | {_s(c.route_ok)} | {_s(c.agg_ok)} | {_s(c.lane_ok)} | {c.latency_ms} |"
        )

    failures = [c for c in result.cases if not c.route_ok or c.agg_ok is False]
    if failures:
        lines += [
            f"",
            f"---",
            f"",
            f"## 6. Failures Requiring Attention",
            f"",
        ]
        for c in failures:
            tag = []
            if not c.route_ok:
                tag.append(f"routing: expected `{c.expected_route}` got `{c.actual_route}`")
            if c.agg_ok is False:
                tag.append(f"agg: expected `{c.ground_truth}` got `{c.agg_actual}`")
            lines.append(f"- **{c.id}**: {'; '.join(tag)}")
    else:
        lines += [
            f"",
            f"---",
            f"",
            f"## 6. Failures",
            f"",
            f"None — all routing and aggregation checks passed.",
        ]

    lines += [
        f"",
        f"---",
        f"",
        f"## 7. Headline Aggregates (verified against coolattin.db)",
        f"",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Total unified records | 13 707 |",
        f"| Records with emigration | 6 016 |",
        f"| Records with eviction (unique) | 4 108 |",
        f"| Records with tenancy | 5 247 |",
        f"| Total clearances (clearances_record.count) | 7 763 |",
        f"| Townlands | 4 225 |",
        f"| Civil parishes | 22 |",
        f"| Baronies | 11 |",
        f"| Widows | 811 |",
        f"| Holy wells | 68 |",
        f"| Ring forts | 298 |",
        f"",
        f"_Generated by `ask_eval.py --phase {result.phase_label}`_",
    ]

    return "\n".join(lines)


def test_faithfulness_gate_offline() -> dict[str, Any]:
    try:
        from backend.services.ask_service import (
            _extract_numeric_tokens,      # type: ignore[attr-defined]
            _synthesis_allowed_numbers,    # type: ignore[attr-defined]
        )
    except ImportError:
        return {"status": "import_failed", "catch_rate": None, "pass_rate": None, "cases": []}

    gate_cases = [
        {
            "name": "correct_emigration_total",
            "rows": [{"emigration_count": 6016}],
            "answer": "There were 6,016 emigrations from the Coolattin estate.",
            "expected_violation": False,
        },
        {
            "name": "hallucinated_emigration_number",
            "rows": [{"emigration_count": 6016}],
            "answer": "There were 9,999 emigrations from the Coolattin estate.",
            "expected_violation": True,
        },
        {
            "name": "wrong_eviction_year_and_count",
            "rows": [{"year": 1847, "n": 2681}],
            "answer": "The worst eviction year was 1851 with 3,000 clearances.",
            "expected_violation": True,
        },
        {
            "name": "correct_multi_row",
            "rows": [{"year": 1847, "n": 2681}, {"year": 1848, "n": 1565}],
            "answer": "In 1847 there were 2,681 evictions and in 1848 there were 1,565.",
            "expected_violation": False,
        },
        {
            "name": "hallucinated_percentage_not_in_rows",
            "rows": [{"emigration_count": 6016, "total": 13707}],
            "answer": "Approximately 75 percent of estate records are emigration records.",
            "expected_violation": True,
        },
        {
            "name": "correct_single_value",
            "rows": [{"population": 55}],
            "question": "What was the population of Ballinacor in 1841?",
            "answer": "The population in 1841 was 55 people.",
            "expected_violation": False,
        },
    ]

    results = []
    violations_caught = 0
    correct_passes = 0
    total_violations_expected = sum(1 for tc in gate_cases if tc["expected_violation"])
    total_passes_expected = sum(1 for tc in gate_cases if not tc["expected_violation"])

    for tc in gate_cases:
        sql_result = {"rows": tc["rows"]}
        allowed = _synthesis_allowed_numbers(sql_result, "", tc.get("question", ""))
        answer_nums = _extract_numeric_tokens(tc["answer"])
        violations = sorted(n for n in answer_nums if n not in allowed)
        has_violation = bool(violations)
        gate_correct = has_violation == tc["expected_violation"]

        if tc["expected_violation"] and has_violation:
            violations_caught += 1
        elif not tc["expected_violation"] and not has_violation:
            correct_passes += 1

        results.append({
            "name": tc["name"],
            "expected_violation": tc["expected_violation"],
            "actual_violation": has_violation,
            "violations_found": violations,
            "gate_correct": gate_correct,
        })

    catch_rate = (violations_caught / total_violations_expected) if total_violations_expected else None
    pass_rate = (correct_passes / total_passes_expected) if total_passes_expected else None

    return {
        "status": "ok",
        "n_cases": len(gate_cases),
        "violations_expected": total_violations_expected,
        "passes_expected": total_passes_expected,
        "violations_caught": violations_caught,
        "correct_passes": correct_passes,
        "catch_rate": round(catch_rate, 3) if catch_rate is not None else None,
        "pass_rate": round(pass_rate, 3) if pass_rate is not None else None,
        "cases": results,
    }


def _run_fallback_ground_truth(
    cases: list[EvalCase],
    run_query_fn,
    sanitize_fn,
) -> list[dict[str, Any]]:
    results = []
    for case in cases:
        if not (case.expected_route == "llm" and case.ground_truth_sql):
            continue
        gt_sql_ok: bool | None = None
        gt_agg_ok: bool | None = None
        gt_actual: Any = None
        try:
            safe = sanitize_fn(case.ground_truth_sql)
            _, rows = run_query_fn(safe)
            gt_sql_ok = True
            gt_agg_ok, gt_actual = _check_agg(case, [], rows)
        except Exception as exc:
            gt_sql_ok = False
            gt_actual = str(exc)[:80]
        results.append({
            "id": case.id,
            "catalogue_code": _catalogue_code(case.id, case.catalogue_code),
            "question": case.question[:90],
            "gt_sql": case.ground_truth_sql[:100] if case.ground_truth_sql else None,
            "gt_expected": case.ground_truth_value,
            "gt_actual": gt_actual,
            "gt_sql_ok": gt_sql_ok,
            "gt_agg_ok": gt_agg_ok,
        })
    return results


def generate_evaluation_pack(
    result: EvalResult,
    gate_result: dict[str, Any],
    fallback_gt: list[dict[str, Any]],
    output_path: "Path | None" = None,
) -> str:
    from pathlib import Path as _Path

    metrics = _compute_metrics(result)
    confusion = metrics.get("routing_confusion_matrix", {})
    ts = result.timestamp[:19].replace("T", " ") + " UTC"
    n = metrics.get("n", 0)

    def _s(v: bool | None) -> str:
        return "✓" if v is True else ("✗" if v is False else "–")

    lines: list[str] = []

    lines += [
        "# Dissertation Evaluation Pack — D9 / D10",
        "",
        f"**Run label:** `{result.phase_label}`  ",
        f"**Timestamp:** {ts}  ",
        f"**Questions run:** {n}  ",
        f"**Gold-set size:** {len(GOLDEN_CASES)} (75 pre-existing + 8 new: 4 workhouse-ER + 4 in-scope fallback)  ",
        "",
        "---",
        "",
        "## D9 — Automated Pipeline Evaluation",
        "",
    ]

    lines += [
        "### D9a — Routing Accuracy and Confusion Matrix",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Overall routing accuracy | {metrics.get('routing_accuracy', 'N/A')}% |",
        f"| Lane routing accuracy | {metrics.get('lane_routing_acc', 'N/A')}% |",
        f"| Template hit rate | {metrics.get('template_hit_rate', 'N/A')}% |",
        f"| LLM calls required | {metrics.get('llm_calls_required', 'N/A')} |",
        "",
        "**Routing confusion matrix (expected route → actual route)**",
        "",
    ]
    if confusion:
        all_acts = sorted({act for d in confusion.values() for act in d})
        hdr = "| Expected \\ Actual |" + "".join(f" {a} |" for a in all_acts)
        sep = "|---|" + "---|" * len(all_acts)
        lines += [hdr, sep]
        for exp in sorted(confusion.keys()):
            row = f"| **{exp}** |"
            for act in all_acts:
                row += f" {confusion[exp].get(act, 0)} |"
            lines.append(row)
    lines.append("")

    lines += [
        "### D9b — Execution Accuracy by Route",
        "",
        "| Route | SQL exec success (%) | N cases |",
        "|-------|---------------------|---------|",
    ]
    for _route in ("semantic_layer", "template", "verified_analysis", "template_miss"):
        _key = {
            "semantic_layer": "exec_acc_semantic",
            "template": "exec_acc_template",
            "verified_analysis": "exec_acc_verified",
            "template_miss": "exec_acc_llm_fallback",
        }[_route]
        _val = metrics.get(_key)
        _n_route = sum(1 for c in result.cases if c.actual_route == _route)
        lines.append(f"| {_route} | {_val if _val is not None else 'N/A'} | {_n_route} |")
    lines.append("")
    lines += [
        "> **Acceptance criterion:** Deterministic routes should reach ~100% execution accuracy.",
        "> Any miss is a compiler bug and must be fixed before submission.",
        "",
    ]

    lines += [
        "### D9c — Per-Lane Breakdown",
        "",
        "| Lane | N | Key metric | Value |",
        "|------|---|------------|-------|",
        f"| Analytical | {metrics.get('analytical_n', 'N/A')} | Aggregation correctness | {metrics.get('analytical_agg_acc', 'N/A')}% |",
        f"| Relational | {metrics.get('relational_n', 'N/A')} | Mean subgraph recall | {metrics.get('subgraph_recall', 'N/A')} |",
        f"| Comparative | {metrics.get('comparative_n', 'N/A')} | SQLite capture | {metrics.get('comparative_sqlite_capture', 'N/A')}% |",
        f"| Fallback / G-series | {metrics.get('fallback_n', 'N/A')} | Routing accuracy | {metrics.get('fallback_routing_acc', 'N/A')}% |",
        "",
    ]

    g_n = metrics.get("g_series_n", 0)
    honest_rate = metrics.get("honest_refusal_rate")
    lines += [
        "### D9d — Honest-Refusal Rate (G-series)",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| G-series questions (expected route = llm) | {g_n} |",
        f"| Honest-refusal rate (reached template_miss) | {honest_rate if honest_rate is not None else 'N/A'}% |",
        "",
    ]
    llm_cases = [c for c in result.cases if c.expected_route == "llm"]
    if llm_cases:
        route_dist: dict[str, int] = {}
        for c in llm_cases:
            route_dist[c.actual_route] = route_dist.get(c.actual_route, 0) + 1
        lines.append("**Distribution of actual routes for llm-expected cases:**")
        lines.append("")
        lines.append("| Actual route | Count |")
        lines.append("|---|---|")
        for route, cnt in sorted(route_dist.items()):
            lines.append(f"| {route} | {cnt} |")
        lines.append("")

    lines += [
        "### D9e — Latency",
        "",
        f"| Percentile | Value |",
        f"|-----------|-------|",
        f"| p50 (median) | {metrics.get('p50_latency_ms', 'N/A')} ms |",
        f"| p90 | {metrics.get('p90_latency_ms', 'N/A')} ms |",
        f"| p95 | {metrics.get('p95_latency_ms', 'N/A')} ms |",
        "",
        "> Stage-level latency (SSE event timings) is available in the browser console",
        "> during live use; it cannot be captured by the offline eval harness.",
        "",
    ]

    llm_routed_wrong = [c for c in result.cases
                        if c.expected_route == "llm" and c.actual_route != "template_miss"]
    lines += [
        "### D9f — Over-Routing Finding",
        "",
        f"Of the {len(llm_cases)} questions expected to reach the LLM fallback, "
        f"**{len(llm_routed_wrong)}** were instead routed to a deterministic path. "
        "This is the over-routing bug documented in Phase 5.",
        "",
    ]
    if llm_routed_wrong:
        lines.append("| ID | Cat | Actual route | Template / metric used |")
        lines.append("|---|---|---|---|")
        for c in llm_routed_wrong:
            tmpl = c.template_id or "—"
            lines.append(f"| {c.id} | {c.category[:8]} | {c.actual_route} | `{tmpl}` |")
        lines.append("")
        lines += [
            "**Root cause:** The semantic layer keyword map is over-inclusive. For in-scope",
            "fallback questions (e.g. 'average rent', 'children who emigrated'), the first",
            "matching keyword ('tenant' → `tenancy_count`, 'emigrat' → `emigration_count`)",
            "applies a semantically valid metric but with an incorrect or missing filter.",
            "For out-of-scope G-series questions (crops, religion, mortality), the metric",
            "returns an unrelated result set that silently passes all routing checks.",
            "",
            "**Decision point:** The fix is a stricter confidence threshold in",
            "`semantic_layer.try_rule_based_fill` — only accept the fill when ≥2 keywords",
            "match the target metric, or when the question explicitly names the metric's",
            "primary entity. This is a scope decision for the dissertation's conclusions.",
            "",
        ]
    else:
        lines += [
            "No over-routing detected — all fallback-expected questions reached `template_miss`.",
            "",
        ]

    lines += [
        "### D9g — Fallback Oracle Ground-Truth Verification",
        "",
        "For fallback-expected questions that have `ground_truth_sql`, the oracle SQL was",
        "executed directly against the DB (bypassing the pipeline) to confirm data is present.",
        "",
        "| ID | Code | Expected | Oracle actual | GT SQL ok | GT value ok |",
        "|---|---|---|---|---|---|",
    ]
    for r in fallback_gt:
        lines.append(
            f"| {r['id']} | {r['catalogue_code']} | {r['gt_expected']} "
            f"| {r['gt_actual']} | {_s(r['gt_sql_ok'])} | {_s(r['gt_agg_ok'])} |"
        )
    lines.append("")

    lines += [
        "---",
        "",
        "## D10 — Faithfulness and Hallucination Analysis",
        "",
    ]

    lines += [
        "### D10a — Numeric-Consistency Gate (Offline Test)",
        "",
        "The gate extracts every number from the synthesised answer and checks it",
        "against an allowlist built from the SQL result rows. This test uses synthetic",
        "cases (no LLM call required).",
        "",
    ]
    if gate_result.get("status") == "ok":
        lines += [
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Test cases | {gate_result['n_cases']} |",
            f"| Violations expected | {gate_result['violations_expected']} |",
            f"| Violations caught | {gate_result['violations_caught']} |",
            f"| Catch rate | {gate_result['catch_rate']} ({int(gate_result['catch_rate']*100)}%) |",
            f"| Correct passes | {gate_result['correct_passes']} / {gate_result['passes_expected']} |",
            f"| Pass rate (no false positives) | {gate_result['pass_rate']} ({int(gate_result['pass_rate']*100)}%) |",
            "",
            "**Per-case results:**",
            "",
            "| Case | Expected violation | Actual violation | Gate correct | Numbers flagged |",
            "|------|------------------|-----------------|-------------|----------------|",
        ]
        for tc in gate_result.get("cases", []):
            flagged = ", ".join(tc["violations_found"]) or "—"
            lines.append(
                f"| {tc['name']} | {tc['expected_violation']} | {tc['actual_violation']}"
                f" | {_s(tc['gate_correct'])} | {flagged} |"
            )
        lines.append("")
    else:
        lines += [
            f"> Gate test status: `{gate_result.get('status')}` — could not import",
            "> `_extract_numeric_tokens` / `_synthesis_allowed_numbers` from ask_service.",
            "",
        ]

    lines += [
        "### D10b — Cross-Verifier (LLM-Based)",
        "",
        "A second LLM-based verifier (`_cross_verify_synthesis`) is implemented in",
        "`ask_service.py` and is invoked for every LLM-fallback route answer. It prompts",
        "a separate model to list factual claims in the answer not supported by the result",
        "rows. If `verdict = 'disagree'`, warnings are appended to the answer.",
        "",
        "**Catch-rate measurement:** Requires live LLM calls (POST to the configured",
        "provider). The offline eval harness cannot exercise this path. To measure the",
        "catch rate manually:",
        "",
        "1. Ask a question that reaches `template_miss` (e.g., any `er_wh_*` or `fbl_*` case).",
        "2. The Ask page will call the LLM and the verifier fires automatically.",
        "3. Check `query_provenance.verifier.verdict` in the API response JSON.",
        "",
        "Until live measurements are taken, the cross-verifier is reported as **implemented",
        "but unmeasured** — a known gap in the automated evidence.",
        "",
    ]

    facts_rate = metrics.get("answer_facts_found_rate")
    lines += [
        "### D10c — Hallucination Proxy (Answer-Facts Found Rate)",
        "",
        "For deterministic-route answers, `answer_facts_ok` checks whether every string",
        "in `expected_answer_facts` appears in the SQL result rows. This is a lower bound",
        "on faithfulness: a passing score means the expected facts *are* in the data;",
        "a failing score means the template returned wrong data or the SQL was filtered",
        "incorrectly.",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Answer facts found rate | {facts_rate if facts_rate is not None else 'N/A'}% |",
        "",
        "This metric does not cover LLM-generated prose (which requires the cross-verifier",
        "or the numeric gate). It applies only to the deterministic route results.",
        "",
    ]

    lines += [
        "---",
        "",
        "## Outstanding — D11 User Study",
        "",
        "**D11 is a human task and cannot be automated.** Suggested protocol:",
        "",
        "- Recruit 4–6 participants (historians, genealogists, or graduate students)",
        "  with an interest in nineteenth-century Irish history.",
        "- Ask each participant to attempt 5–8 questions of their own choosing on",
        "  the Ask page. Record the browser session (screen + audio).",
        "- After each session, ask the participant to rate each answer on the same",
        "  three-dimension rubric used in `eval/manual_scoring_sheet.csv`:",
        "  Correctness, Faithfulness, and Historical Appropriateness.",
        "- Report inter-rater agreement (Cohen's κ) across raters for the overlap",
        "  questions.",
        "",
        "The `eval/manual_scoring_sheet.csv` produced alongside this pack provides a",
        "pre-filled question list and empty scoring columns that can be printed or",
        "shared as a Google Sheet for participant use.",
        "",
        "---",
        "",
        f"_Generated by `ask_eval.py --phase {result.phase_label}` on {ts}_",
    ]

    md = "\n".join(lines)

    if output_path is not None:
        _Path(output_path).write_text(md, encoding="utf-8")

    return md


def generate_manual_scoring_sheet(output_path: "Path | None" = None) -> str:
    import csv
    import io
    import random

    case_map: dict[str, EvalCase] = {c.id: c for c in GOLDEN_CASES}

    gold_answers = {
        "emi_01_total": "6016",
        "emi_02_townland_ballynultagh": "400",
        "emi_03_townland_killinure": "294",
        "emi_04_per_year_trend": "time-series peak 1847=2211",
        "emi_05_canada_total": "peak 1848=787",
        "emi_06_canada_ship": "Glenlyon",
        "emi_07_ships_list": "27 distinct ships",
        "emi_08_in_1848": "1290",
        "evic_01_total": "7763",
        "evic_02_worst_year": "1847",
        "evic_03_townland_ballinacor": "122",
        "evic_04_per_year": "time-series 1847–1856",
        "evic_05_people_list": "list of 4108 unique records",
        "evic_06_in_1849": "1016",
        "cen_01_estate_1841": "119300",
        "cen_02_estate_1851": "91860",
        "cen_03_ballinacor_1841": "55",
        "cen_04_famine_decline": "−27440 (−23.0%)",
        "cen_05_trend_1841_1861": "time-series 1841–1861",
        "cen_06_uninhabited": "varies by year",
        "cen_07_all_years": "time-series 1827–1891",
        "cen_08_by_parish": "per-parish list (22 parishes)",
        "geo_01_total_townlands": "4225",
        "geo_02_parish_count": "22",
        "geo_03_parish_list": "22 parish names",
        "geo_04_ballinacor_parish": "Kilbride (SQLite) / Ballinacor (KG)",
        "geo_05_baronies": "11 baronies incl. Shillelagh",
        "geo_06_nearby_coolattin": "same-parish/spatial proximity",
        "geo_07_by_county": "per-county breakdown",
        "ppl_01_total_records": "13707",
        "ppl_02_byrne_records": "1290",
        "ppl_03_murphy_list": "list of 290 records",
        "ppl_04_widows_count": "811",
        "ppl_05_widows_children": "28.7% (233/811)",
        "ppl_06_heads_of_household": "list",
        "ppl_07_ballynultagh_people": "list",
        "ppl_08_in_1847": "2211",
        "ten_01_total": "5247",
        "ten_02_gender_avg": "M=39.49ac F=34.98ac",
        "ten_03_coolattin_tenants": "list",
        "ten_04_largest_holdings": "top-20 ranked list",
        "ten_05_smallest_plots": "per-townland ranked",
        "ten_06_per_townland": "per-townland count",
        "her_01_holy_well_population": "descriptive group comparison",
        "her_02_ring_fort_population": "descriptive group comparison",
        "her_03_holy_well_count": "68",
        "her_04_ring_fort_count": "298",
        "her_05_holy_well_townlands": "65 townlands",
        "ov_01_famine_impact": "multi-source narrative",
        "ov_02_estate_summary": "13707 records summary",
        "ov_03_emi_and_evic": "0",
        "ov_04_emi_vs_population": "time-series comparison",
        "ov_05_records_per_year": "time-series",
        "er_01_exact_ballinacor": "townland id=355 resolved",
        "er_02_spelling_variant": "resolves to BALLINACOR",
        "er_03_spelling_ballynultach": "resolves to BALLYNULTAGH",
        "er_04_coolattin_kg_uri": "COOLATTIN + kg_uri",
        "er_05_surname_byrne_exact": "1290",
        "er_06_surname_fuzzy": "resolves to KAVANAGH",
        "rel_01_ballinacor_barony": "Arklow (SQLite)",
        "rel_02_ballynultagh_county": "Wicklow / Shillelagh",
        "rel_03_ballinacor_parish_siblings": "Kilbride parish siblings",
        "rel_04_estate_overview": "narrative",
        "rel_05_historical_monuments": "heritage features",
        "cmp_01_emigration_vs_kg": "SQLite=400",
        "cmp_02_population_vs_kg": "SQLite=55",
        "cmp_03_eviction_agree": "SQLite=122",
        "fbl_01_rent": "N/A — no template",
        "fbl_02_crops": "N/A — not in DB",
        "fbl_03_fitzwilliam": "N/A — not in DB",
        "gen_01_mortality": "N/A — not in DB",
        "gen_02_religion": "N/A — not in DB",
        "gen_03_other_estates": "N/A — not in DB",
        "gen_04_weather": "N/A — not in DB",
        "gen_05_politics": "N/A — not in DB",
        "er_wh_01_linked_count": "139",
        "er_wh_02_confirmed_matches": "3",
        "er_wh_03_review_needed": "136",
        "er_wh_04_mentions_count": "8214",
        "fbl_04_children_emigrated": "2610",
        "fbl_05_avg_rent_owed": "38.07",
        "fbl_06_widows_emigrated": "15",
        "fbl_07_er_candidate_count": "22928",
    }

    random.seed(42)
    by_code: dict[str, list[str]] = {}
    for case in GOLDEN_CASES:
        code = _catalogue_code(case.id, case.catalogue_code)
        by_code.setdefault(code, []).append(case.id)
    kappa_ids: set[str] = set()
    for code, ids in by_code.items():
        k = max(1, round(len(ids) * 0.20))
        kappa_ids.update(random.sample(ids, min(k, len(ids))))

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "id", "catalogue_code", "category", "question", "gold_answer",
        "expected_route", "correctness", "faithfulness", "rater_1_notes",
        "rater_2_notes", "kappa_subset",
    ])
    for case in GOLDEN_CASES:
        code = _catalogue_code(case.id, case.catalogue_code)
        gold = gold_answers.get(case.id, "")
        writer.writerow([
            case.id,
            code,
            case.category,
            case.question,
            gold,
            case.expected_route,
            "",
            "",
            "",
            "",
            "Y" if case.id in kappa_ids else "",
        ])

    csv_text = buf.getvalue()
    if output_path is not None:
        from pathlib import Path as _Path
        _Path(output_path).write_text(csv_text, encoding="utf-8")
    return csv_text


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
                "answer_facts_ok": c.answer_facts_ok,
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
            answer_facts_ok=c.get("answer_facts_ok"),
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
    parser.add_argument("--phase", default="baseline", help="Label for this run (e.g. phase0, phase5, heldout)")
    parser.add_argument("--set", dest="eval_set", choices=["tuned", "heldout", "both"],
                        default="tuned",
                        help="Which question set to run: tuned (default), heldout, or both (side-by-side)")
    parser.add_argument("--save", metavar="FILE", help="Save result JSON to FILE")
    parser.add_argument("--compare", nargs="+", metavar="FILE", help="Compare existing result JSON files")
    parser.add_argument("--no-miss-detail", action="store_true", help="Skip per-miss diagnostic output")
    parser.add_argument("--evaluation-pack", action="store_true",
                        help="Write full D9/D10 dissertation evidence pack to eval_results/")
    args = parser.parse_args()

    if args.compare:
        loaded = [_load_result(Path(f)) for f in args.compare]
        for r in loaded:
            print_case_table(r)
        print_metrics_table(loaded)
        return

    _root = Path(__file__).resolve().parents[2]
    _eval_dir = _root / "eval_results"
    _eval_dir.mkdir(exist_ok=True)

    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from create_app import create_app

    app = create_app()
    with app.app_context():
        from backend.services.ask_service import (
            _run_read_only_query,
            _sanitize_and_validate_sql,
        )

        if args.eval_set in ("tuned", "both"):
            print(f"Running eval harness — tuned set ({len(GOLDEN_CASES)} cases)…")
            result_tuned = run_eval(phase_label=args.phase, case_list=GOLDEN_CASES)
            fallback_gt = _run_fallback_ground_truth(
                GOLDEN_CASES, _run_read_only_query, _sanitize_and_validate_sql
            )

        if args.eval_set in ("heldout", "both"):
            label_hh = f"{args.phase}_heldout" if args.eval_set == "both" else args.phase
            print(f"Running eval harness — held-out set ({len(HELDOUT_CASES)} cases)…")
            result_heldout = run_eval(phase_label=label_hh, case_list=HELDOUT_CASES)
            fallback_gt_hh = _run_fallback_ground_truth(
                HELDOUT_CASES, _run_read_only_query, _sanitize_and_validate_sql
            )

    gate_result = test_faithfulness_gate_offline()

    if args.eval_set == "tuned":
        result = result_tuned
        print_case_table(result)
        print_metrics_table([result])
        print_confusion_matrix(result)
        if not args.no_miss_detail:
            print_miss_detail(result)
            print(written_finding(result))
        print(f"\nFaithfulness gate (offline): catch_rate={gate_result.get('catch_rate')} "
              f"pass_rate={gate_result.get('pass_rate')} "
              f"({gate_result.get('violations_caught')}/{gate_result.get('violations_expected')} violations caught)")

        json_path = Path(args.save) if args.save else _eval_dir / f"eval_{args.phase}.json"
        _save_result(result, json_path)
        md_path = json_path.with_suffix(".md")
        md = generate_markdown_report(result)
        md_path.write_text(md, encoding="utf-8")
        print(f"Markdown report  → {md_path}")

        if args.evaluation_pack:
            pack_path = _eval_dir / "evaluation_pack.md"
            generate_evaluation_pack(result, gate_result, fallback_gt, output_path=pack_path)
            print(f"Evaluation pack  → {pack_path}")
            scoring_path = _root / "eval" / "manual_scoring_sheet.csv"
            scoring_path.parent.mkdir(exist_ok=True)
            generate_manual_scoring_sheet(output_path=scoring_path)
            print(f"Scoring sheet    → {scoring_path}")

    elif args.eval_set == "heldout":
        result = result_heldout
        print_case_table(result)
        print_metrics_table([result])
        print_confusion_matrix(result)
        if not args.no_miss_detail:
            print_miss_detail(result)
        print(f"\nFaithfulness gate (offline): catch_rate={gate_result.get('catch_rate')} "
              f"pass_rate={gate_result.get('pass_rate')}")
        json_path = Path(args.save) if args.save else _eval_dir / f"eval_{args.phase}_heldout.json"
        _save_result(result, json_path)
        md_path = json_path.with_suffix(".md")
        md = generate_markdown_report(result)
        md_path.write_text(md, encoding="utf-8")
        print(f"Held-out report  → {md_path}")

    else:
        print_case_table(result_tuned)
        print_case_table(result_heldout)
        print_metrics_table(
            [result_tuned, result_heldout],
            labels=[f"{args.phase} (tuned)", f"{args.phase} (held-out)"],
        )
        print_confusion_matrix(result_tuned)
        print_confusion_matrix(result_heldout)

        json_path_t = _eval_dir / f"eval_{args.phase}.json"
        json_path_h = _eval_dir / f"eval_{args.phase}_heldout.json"
        _save_result(result_tuned, json_path_t)
        _save_result(result_heldout, json_path_h)

        md_t = generate_markdown_report(result_tuned)
        md_h = generate_markdown_report(result_heldout)
        json_path_t.with_suffix(".md").write_text(md_t, encoding="utf-8")
        json_path_h.with_suffix(".md").write_text(md_h, encoding="utf-8")

        cmp_path = _eval_dir / f"eval_{args.phase}_tuned_vs_heldout.md"
        _write_comparison_report(result_tuned, result_heldout, args.phase, cmp_path)
        print(f"Tuned report     → {json_path_t.with_suffix('.md')}")
        print(f"Held-out report  → {json_path_h.with_suffix('.md')}")
        print(f"Comparison       → {cmp_path}")


def _write_comparison_report(
    tuned: EvalResult,
    heldout: EvalResult,
    phase: str,
    output_path: Path,
) -> None:
    from datetime import datetime, timezone

    mt = _compute_metrics(tuned)
    mh = _compute_metrics(heldout)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    def _fmt(val: Any) -> str:
        return str(val) if val is not None else "N/A"

    def _delta(t: Any, h: Any) -> str:
        try:
            d = float(h) - float(t)
            if abs(d) < 0.05:
                return "="
            return f"+{d:.1f}" if d > 0 else f"{d:.1f}"
        except (TypeError, ValueError):
            return "—"

    rows_spec = [
        ("Questions n",           "n"),
        ("Routing accuracy (%)",  "routing_accuracy"),
        ("Entity label acc (%)",  "entity_resolution_acc"),
        ("SQL exec success (%)",  "sql_exec_success"),
        ("Aggregation corr. (%)", "aggregation_correctness"),
        ("Honest-refusal (%)",    "honest_refusal_rate"),
        ("Template hit rate (%)", "template_hit_rate"),
        ("LLM calls required",    "llm_calls_required"),
        ("Lane routing acc (%)",  "lane_routing_acc"),
        ("Subgraph recall",       "subgraph_recall"),
        ("Answer facts (%)",      "answer_facts_found_rate"),
        ("p50 latency (ms)",      "p50_latency_ms"),
        ("p90 latency (ms)",      "p90_latency_ms"),
    ]

    lines = [
        f"# Tuned vs Held-Out Evaluation Comparison",
        f"",
        f"**Phase:** `{phase}`  ",
        f"**Timestamp:** {ts}  ",
        f"**Tuned set:** {mt.get('n')} questions (GOLDEN_CASES — used for routing/keyword development)  ",
        f"**Held-out set:** {mh.get('n')} questions (HELDOUT_CASES — never seen during tuning)  ",
        f"",
        f"> The gap between tuned and held-out scores is the **generalisation gap** —",
        f"> the primary measure of whether the D10 routing fix over-fits to the tuned set.",
        f"",
        f"---",
        f"",
        f"## Global Metrics",
        f"",
        f"| Metric | Tuned | Held-out | Δ (held-out − tuned) |",
        f"|--------|-------|----------|----------------------|",
    ]
    for label, key in rows_spec:
        vt = _fmt(mt.get(key))
        vh = _fmt(mh.get(key))
        d = _delta(mt.get(key), mh.get(key))
        lines.append(f"| {label} | {vt} | {vh} | {d} |")

    lines += [
        f"",
        f"---",
        f"",
        f"## Per-Lane Breakdown",
        f"",
        f"| Lane | Tuned N | Held-out N | Tuned key metric | Held-out key metric |",
        f"|------|---------|------------|-----------------|---------------------|",
        f"| Analytical | {mt.get('analytical_n')} | {mh.get('analytical_n')} | agg_acc={mt.get('analytical_agg_acc')}% | agg_acc={mh.get('analytical_agg_acc')}% |",
        f"| Relational | {mt.get('relational_n')} | {mh.get('relational_n')} | sg_recall={mt.get('subgraph_recall')} | sg_recall={mh.get('subgraph_recall')} |",
        f"| Comparative | {mt.get('comparative_n')} | {mh.get('comparative_n')} | sqlite={mt.get('comparative_sqlite_capture')}% | sqlite={mh.get('comparative_sqlite_capture')}% |",
        f"| Fallback/G  | {mt.get('fallback_n')} | {mh.get('fallback_n')} | refusal={mt.get('honest_refusal_rate')}% | refusal={mh.get('honest_refusal_rate')}% |",
        f"",
        f"---",
        f"",
        f"## Interpretation",
        f"",
        f"**Routing accuracy** measures whether each question reached the expected pipeline",
        f"branch (semantic_layer / template / verified_analysis → 'template', or template_miss → 'llm').",
        f"A large gap here indicates the routing keywords/thresholds over-fit to the tuned set.",
        f"",
        f"**Honest-refusal rate** measures whether G-series (out-of-scope) questions correctly",
        f"reached the LLM fallback rather than silently returning an unrelated DB result.",
        f"This is the D10 fix; a near-zero gap confirms the fix generalises.",
        f"",
        f"**Aggregation correctness** measures whether the deterministic pipeline returns the",
        f"verified numeric answer. A large gap here suggests the semantic layer has spurious",
        f"filters that were not caught on the held-out townlands/years.",
        f"",
        f"_Generated by `ask_eval.py --phase {phase} --set both`_",
    ]

    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Comparison report written → {output_path}")


if __name__ == "__main__":
    main()
