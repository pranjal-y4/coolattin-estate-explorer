"""
backend/services/ask_service.py

Natural-language Q&A over local SQLite using LLM-generated SQL.

Design
------
- Verified SQL templates handle high-risk research questions where statistical accuracy matters most.
- The configured LLM generates read-only SQL only after receiving live schema, sampled categories, and approved query memory.
- Approved thumbs-up feedback can store trusted SQL for future reuse.
- Any failed or semantically invalid SQL is repaired or rejected before results are shown.
- The configured LLM rewrites the verified data answer for readability.
- VRTI parish data is cached in-process (TTL 1 h).
- SSE streaming: each pipeline stage emits a progress event as it starts and completes.
- Read-only SQL guardrails before execution.
- PDF export of all relevant entries.
"""
from __future__ import annotations

import concurrent.futures
import csv
import difflib
import json
import logging
import math
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

import requests
try:
    from rapidfuzz import fuzz
except Exception:  # pragma: no cover - optional fallback for environments without rapidfuzz
    fuzz = None

from config import ActiveConfig
from extensions import get_db_conn

log = logging.getLogger(__name__)

# ── LLM providers ─────────────────────────────────────────────────────────────
ASK_LLM_PROVIDER = os.environ.get("ASK_LLM_PROVIDER", "auto").strip().lower()

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
OPENROUTER_BASE_URL = os.environ.get(
    "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
).rstrip("/")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "openai/gpt-oss-20b:free").strip()
OPENROUTER_CONNECT_TIMEOUT = int(os.environ.get("OPENROUTER_CONNECT_TIMEOUT", "10"))
OPENROUTER_REQUEST_TIMEOUT = int(os.environ.get("OPENROUTER_REQUEST_TIMEOUT", "80"))
OPENROUTER_STATUS_TIMEOUT = float(os.environ.get("OPENROUTER_STATUS_TIMEOUT", "5"))
OPENROUTER_STATUS_CACHE_TTL = max(5, int(os.environ.get("OPENROUTER_STATUS_CACHE_TTL", "60")))
OPENROUTER_MAX_RETRIES = max(1, int(os.environ.get("OPENROUTER_MAX_RETRIES", "2")))
OPENROUTER_SITE_URL = os.environ.get("OPENROUTER_SITE_URL", "http://127.0.0.1:5001").strip()
OPENROUTER_APP_TITLE = os.environ.get("OPENROUTER_APP_TITLE", "Coolattin Archive Ask").strip()
ASK_GENERATE_VRTI_SQL_WITH_LLM = os.environ.get(
    "ASK_GENERATE_VRTI_SQL_WITH_LLM", ""
).strip().lower() in {"1", "true", "yes", "on"}
ASK_ALLOW_HEURISTIC_FALLBACK = os.environ.get(
    "ASK_ALLOW_HEURISTIC_FALLBACK", ""
).strip().lower() in {"1", "true", "yes", "on"}

# When false, skip paid-API calls (Anthropic, Grok) and fall back to free OpenRouter models.
LLM_ALLOW_PAID = os.environ.get("LLM_ALLOW_PAID", "true").strip().lower() not in {"0", "false", "no", "off"}

# Feature flag: routed architecture (entity_resolver → intent_router → semantic/subgraph).
# Default TRUE so the newer orchestrated pipeline is the standard runtime path.
# Set ASK_USE_NEW_PIPELINE=false to force the legacy pipeline only. The old pipeline
# remains both the explicit flag-off behaviour and the FALLBACK lane inside the
# new orchestrator.
ASK_USE_NEW_PIPELINE = os.environ.get(
    "ASK_USE_NEW_PIPELINE", "true"
).strip().lower() in {"1", "true", "yes", "on"}

# ── Claude (Anthropic) — Part D ───────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6").strip()
ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"
ANTHROPIC_API_VERSION = "2023-06-01"

# ── Grok (xAI) — Part D ───────────────────────────────────────────────────────
GROK_API_KEY = os.environ.get("GROK_API_KEY", "").strip()
GROK_MODEL = os.environ.get("GROK_MODEL", "grok-3-mini").strip()
GROK_BASE_URL = os.environ.get("GROK_BASE_URL", "https://api.x.ai/v1").rstrip("/")

# Which model handles final answer synthesis: "claude" | "grok" | "openrouter" | "ollama"
ASK_SYNTHESIS_MODEL = os.environ.get("ASK_SYNTHESIS_MODEL", "claude").strip().lower()

_OPENROUTER_FREE_MODELS = [
    "openai/gpt-oss-20b:free",
    "openai/gpt-oss-120b:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "meta-llama/llama-3.2-3b-instruct:free",
    "google/gemma-3-27b-it:free",
    "google/gemma-3-12b-it:free",
    "google/gemma-3-4b-it:free",
    "google/gemma-3n-e4b-it:free",
    "google/gemma-3n-e2b-it:free",
    "qwen/qwen3-next-80b-a3b-instruct:free",
    "qwen/qwen3-coder:free",
    "z-ai/glm-4.5-air:free",
    "minimax/minimax-m2.5:free",
    "nvidia/nemotron-3-nano-30b-a3b:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "liquid/lfm-2.5-1.2b-instruct:free",
    "liquid/lfm-2.5-1.2b-thinking:free",
    "nousresearch/hermes-3-llama-3.1-405b:free",
    "arcee-ai/trinity-large-preview:free",
    "cognitivecomputations/dolphin-mistral-24b-venice-edition:free",
]

# ── Ollama fallback ───────────────────────────────────────────────────────────
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "").strip()
OLLAMA_REQUEST_TIMEOUT = int(os.environ.get("OLLAMA_REQUEST_TIMEOUT", "180"))
OLLAMA_CONNECT_TIMEOUT = int(os.environ.get("OLLAMA_CONNECT_TIMEOUT", "8"))
OLLAMA_MAX_RETRIES = max(1, int(os.environ.get("OLLAMA_MAX_RETRIES", "2")))
OLLAMA_MODEL_CACHE_TTL = max(5, int(os.environ.get("OLLAMA_MODEL_CACHE_TTL", "120")))
OLLAMA_KEEP_ALIVE = os.environ.get("OLLAMA_KEEP_ALIVE", "10m")

# ── CSV seed ──────────────────────────────────────────────────────────────────
UNIFIED_SEED_KEY = "ask_unified_seed"
UNIFIED_CSV_PATH = ActiveConfig.STATIC_DATA_DIR / "unified_processed.csv"
UNIFIED_SEED_SCHEMA_VERSION = "v2"
HERITAGE_SEED_KEY = "ask_heritage_seed"
HOLYWELLS_GEOJSON_PATH = ActiveConfig.STATIC_DATA_DIR / "holywells_wicklow.geojson"
ASI_GEOJSON_PATH = ActiveConfig.STATIC_DATA_DIR / "asi_wicklow.geojson"
HERITAGE_SEED_SCHEMA_VERSION = "v1"

# ── SQL safety ────────────────────────────────────────────────────────────────
FORBIDDEN_SQL = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|DETACH|PRAGMA|REINDEX|VACUUM|TRUNCATE|REPLACE)\b",
    flags=re.IGNORECASE,
)

# ── VRTI parish cache ─────────────────────────────────────────────────────────
_vrti_cache_lock: threading.Lock = threading.Lock()
_VRTI_PARISH_CACHE: dict[str, Any] = {}
_VRTI_CACHE_TTL = 3600  # seconds
_VRTI_STATUS_CACHE: dict[str, Any] = {"down_until": 0.0}
_VRTI_UNAVAILABLE_COOLDOWN = 300  # seconds

# ── Ollama cache ─────────────────────────────────────────────────────────────
_openrouter_status_cache_lock: threading.Lock = threading.Lock()
_OPENROUTER_STATUS_CACHE: dict[str, Any] = {
    "expires_at": 0.0,
    "status": None,
}

_ollama_cache_lock: threading.Lock = threading.Lock()
_OLLAMA_MODEL_CACHE: dict[str, Any] = {
    "expires_at": 0.0,
    "models": [],
    "resolved_model": None,
}

# ── Schema compatibility cache ───────────────────────────────────────────────
_schema_cache_lock: threading.Lock = threading.Lock()
_SCHEMA_COMPAT_CACHE: dict[str, Any] = {"clearances_count_column": None}

_prompt_schema_cache_lock: threading.Lock = threading.Lock()
_PROMPT_SCHEMA_CACHE: dict[str, Any] = {"expires_at": 0.0, "value": None}
_PROMPT_SCHEMA_CACHE_TTL = 300

_query_memory_cache_lock: threading.Lock = threading.Lock()
_QUERY_MEMORY_CACHE: dict[str, Any] = {"expires_at": 0.0, "rows": []}
_QUERY_MEMORY_CACHE_TTL = 60

QUERY_MEMORY_SCHEMA_VERSION = "v1"
QUERY_MEMORY_TABLE = "ask_query_memory"
QUERY_FEEDBACK_TABLE = "ask_query_feedback"

VERIFIED_ANALYSIS_TEMPLATE_IDS: set[str] = {
    "tenant_land_gender_average",
    "widows_with_children_proportion",
    "widows_eviction_proportion",
    "widows_count",
    "children_emigrated",
    "eviction_family_size_range",
    "most_populous_1841_vs_1861",
    "population_trend_1841_1861",
    "emigration_population_townland_trend",
    "largest_latest_tenant_holdings",
    "smallest_townland_plots",
    "holy_well_population_relationship",
    "ring_fort_population_relationship",
    "canada_emigration_peak_period",
    "ship_most_families_canada",
}

VERIFIED_ANALYSIS_CHART_HINTS: dict[str, str] = {
    "tenant_land_gender_average": "bar",
    "most_populous_1841_vs_1861": "bar",
    "population_trend_1841_1861": "line",
    "holy_well_population_relationship": "bar",
    "ring_fort_population_relationship": "bar",
    "canada_emigration_peak_period": "line",
    "smallest_townland_plots": "bar",
}

_PROMPT_CATEGORY_COLUMNS: dict[str, list[str]] = {
    "unified_record": [
        "month", "role", "legal_action", "estate", "parish", "gender",
        "occupation", "is_widow", "is_canada_destination",
    ],
    "townland": ["civil_parish", "barony", "county", "electoral_division", "placename_theme", "source"],
    "census_record": ["year", "source"],
    "clearances_record": ["year", "source"],
    "heritage_feature": ["feature_group", "source_dataset", "monument_class"],
}

# ── Townland catalog cache ───────────────────────────────────────────────────
_townland_catalog_lock: threading.Lock = threading.Lock()
_TOWNLAND_CATALOG_CACHE: dict[str, Any] = {
    "loaded_at": 0.0,
    "items": [],
}

_TOWNLAND_STOPWORDS = {
    "a", "about", "across", "an", "and", "are", "around", "as", "at", "be", "been", "between",
    "by", "can", "census", "count", "did", "do", "does", "emigrated",
    "emigration", "evicted", "eviction", "family", "for", "from", "give",
    "happened", "has", "have", "how", "i", "in", "info", "is", "it", "km",
    "land", "list", "many", "me", "near", "nearby", "of", "on", "parish",
    "people", "person", "place", "population", "record", "records", "show",
    "tell", "tenancy", "tenant", "the", "there", "this", "to", "total",
    "townland", "was", "were", "what", "which", "who", "with", "within",
    "year", "years",
}

# ── Annotated schema for LLM prompt ──────────────────────────────────────────
_ANNOTATED_SCHEMA = """
Table: unified_record  — family/people records (emigration, eviction, tenancy)
  record_id TEXT             — unique id per record; use COUNT(DISTINCT record_id) to count people
  year INTEGER               — event year (1827–1868)
  month TEXT                 — month when known
  surname TEXT               — family surname
  forename TEXT              — given name
  canonical_name TEXT        — preferred display name
  townland TEXT              — raw townland name
  townland_norm TEXT         — UPPERCASE normalised townland for WHERE filters e.g. 'BALLINACOR'
  parish TEXT                — civil parish
  estate TEXT                — estate (usually 'Coolattin')
  role TEXT                  — e.g. 'Head of Household'
  gender TEXT                — gender when recorded
  age INTEGER                — age when recorded
  occupation TEXT            — occupation when recorded
  acres REAL                 — holding size from source rows
  acres_irish REAL           — Irish acres when present
  acres_english REAL         — English acres when present
  holding_acres REAL         — derived best-available holding size for land queries
  sons INTEGER               — recorded sons in household
  daughters INTEGER          — recorded daughters in household
  children_count INTEGER     — derived sons + daughters
  family_size_estimate INTEGER — derived estimate from household count fields
  is_widow INTEGER           — derived flag from widow-labelled names/notes
  is_canada_destination INT  — derived flag from arrival text mentioning Quebec / St Andrews / Grosse Isle / Canada
  family_key TEXT            — family grouping key when supplied in CSV
  ship_name TEXT             — ship for emigration
  departure TEXT             — departure place + date
  arrival TEXT               — arrival place + date
  household_list TEXT        — household members
  has_emigration_record INT  — 1=emigrated  0=not
  has_eviction_record INT    — 1=evicted    0=not
  has_tenancy_record INT     — 1=tenant     0=not
  JOIN: UPPER(townland.name) = unified_record.townland_norm

Table: townland  — local townland reference table used by the app
  id INTEGER
  name TEXT              — canonical name e.g. 'Ballinacor'
  name_gaelic TEXT       — Irish name
  civil_parish TEXT      — e.g. 'Knockrath', 'Moyacomb'
  barony TEXT            — e.g. 'Shillelagh'
  county TEXT            — e.g. 'Wicklow'
  electoral_division TEXT
  placename_theme TEXT
  description TEXT
  centroid_lat REAL      — latitude ~52.x
  centroid_lon REAL      — longitude ~-6.x
  kg_uri TEXT

Table: census_record  — census data per townland per year
  townland_id INTEGER    — FK → townland.id
  year INTEGER           — 1841 1851 1861 1871 1881 1891
  male INTEGER           — male population
  female INTEGER         — female population
  total INTEGER          — total population
  inhabited INTEGER      — inhabited houses
  uninhabited INTEGER    — uninhabited houses

Table: clearances_record  — eviction counts per townland per year
  townland_id INTEGER    — FK → townland.id
  year INTEGER           — 1847–1856
  count INTEGER          — eviction count; this live database uses count as the metric column

Table: heritage_feature  — seeded from local heritage GeoJSON for Ask comparisons
  townland_norm TEXT     — normalised townland name used for joins to townland
  feature_group TEXT     — e.g. 'holy_well', 'ring_fort'
  monument_class TEXT    — source class label such as 'Ritual site - holy well'
  source_dataset TEXT    — 'holywells' or 'asi'

Table: source_mentions  — one row per name extracted from workhouse admissions/discharge records
  id INTEGER PRIMARY KEY
  source_table TEXT          — source dataset (e.g. 'do_workhouse' = Dunlavin/Shillelagh Union)
  source_record_id TEXT      — UNIQUE identifier of the workhouse source record
  raw_name TEXT              — name as it appears in the workhouse register
  normalised_name TEXT       — cleaned/normalised version
  forename TEXT
  surname TEXT
  raw_place TEXT
  normalised_place TEXT
  event_year INTEGER
  age INTEGER
  NOTE: Use this table (not unified_record) to count workhouse name mentions or workhouse records.
        Example: SELECT COUNT(*) FROM source_mentions  -- total workhouse name extractions

Table: entity_resolution_candidates  — scored candidate links produced during workhouse→estate matching
  id INTEGER PRIMARY KEY
  mention_id INTEGER         — FK → source_mentions.id
  candidate_record_id TEXT   — FK → unified_record.record_id
  score REAL                 — similarity score 0–1
  label TEXT                 — 'CONFIRMED_MATCH' | 'POSSIBLE_MATCH' | 'REJECTED'
  review_required INTEGER    — 1=needs human review
  NOTE: Use this table to count entity resolution candidates.
        Example: SELECT COUNT(*) FROM entity_resolution_candidates

Table: workhouse_unified_links  — final accepted workhouse→estate record links (after review)
  id INTEGER PRIMARY KEY
  mention_id INTEGER         — FK → source_mentions.id
  unified_record_id TEXT     — FK → unified_record.record_id
  score REAL
  label TEXT                 — 'CONFIRMED_MATCH' | 'POSSIBLE_MATCH' | 'REJECTED'
  review_required INTEGER    — 1=needs human review

IMPORTANT — estate filter: The `estate` column in unified_record is almost always NULL.
  All records in the database belong to the Coolattin estate implicitly.
  NEVER use `estate = 'Coolattin'` (or any estate filter) as a WHERE clause — it returns 0 rows.
  Omit the estate column from all filters entirely.

IMPORTANT — townland scope: `townland_norm = 'COOLATTIN'` refers to ONE specific townland
  named Coolattin (a small area within the estate). It is NOT the whole estate.
  For estate-wide queries (e.g. "all emigrants from the Coolattin estate") omit the townland
  filter entirely — do not add any townland_norm or townland condition.

Function: distance_km(lat1, lon1, lat2, lon2) → REAL  (great-circle km)
  Radius query example:
    WITH base AS (SELECT centroid_lat lat,centroid_lon lon FROM townland WHERE UPPER(name)='X' LIMIT 1)
    SELECT t.name FROM townland t,base b WHERE distance_km(t.centroid_lat,t.centroid_lon,b.lat,b.lon)<=20
""".strip()

_VRTI_PG_SCHEMA = """
Table: vrti_townland — townland records from VRTI KG
  name TEXT, name_gaelic TEXT, civil_parish TEXT, barony TEXT, county TEXT,
  centroid_lat DOUBLE PRECISION, centroid_lon DOUBLE PRECISION, kg_uri TEXT

Table: vrti_census — census data from VRTI KG
  townland_name TEXT, census_year INTEGER, male INTEGER, female INTEGER,
  total INTEGER, inhabited INTEGER, uninhabited INTEGER
""".strip()


# ─────────────────────────────────────────────────────────────────────────────
# 100-question template library
# ─────────────────────────────────────────────────────────────────────────────
# Each template:
#   required_keywords — ALL must appear in the lowercased question
#   optional_keywords — boost the score when present
#   sql_template      — SQL with optional {townland_norm}, {year}, {surname} placeholders
#   requires_townland / requires_year / requires_surname — skip if entity unavailable

QUESTION_TEMPLATES: list[dict[str, Any]] = [

    # ── EMIGRATION ────────────────────────────────────────────────────────────

    {"id": "emigration_total",
     "category": "emigration", "description": "Total emigrated people",
     "required_keywords": ["emigra"],
     "optional_keywords": ["total", "how many", "count", "overall", "all"],
     "sql_template": "SELECT COUNT(DISTINCT record_id) AS total_emigrated_people FROM unified_record WHERE has_emigration_record = 1"},

    {"id": "emigration_from_townland",
     "category": "emigration", "description": "Emigrants from a specific townland",
     "required_keywords": ["emigra"],
     "optional_keywords": ["from", "this townland", "townland"],
     "sql_template": "SELECT COUNT(DISTINCT record_id) AS emigrated_people FROM unified_record WHERE has_emigration_record = 1 AND townland_norm = '{townland_norm}'",
     "requires_townland": True},

    {"id": "emigration_20km",
     "category": "emigration", "description": "Emigrants within 20km radius of townland",
     "required_keywords": ["emigra"],
     "optional_keywords": ["20km", "20 km", "around", "radius", "nearby"],
     "sql_template": """WITH base AS (SELECT centroid_lat lat,centroid_lon lon FROM townland WHERE UPPER(name)='{townland_norm}' LIMIT 1),nearby AS (SELECT t.name FROM townland t,base b WHERE t.centroid_lat IS NOT NULL AND distance_km(t.centroid_lat,t.centroid_lon,b.lat,b.lon)<=20.0) SELECT COUNT(DISTINCT u.record_id) AS emigrated_within_20km FROM unified_record u JOIN nearby n ON u.townland_norm=UPPER(n.name) WHERE u.has_emigration_record=1""",
     "requires_townland": True},

    {"id": "emigration_per_year",
     "category": "emigration", "description": "Emigration count broken down by year",
     "required_keywords": ["emigra", "year"],
     "optional_keywords": ["per year", "by year", "each year", "trend", "over time", "breakdown"],
     "sql_template": "SELECT year, COUNT(DISTINCT record_id) AS emigrated_people FROM unified_record WHERE has_emigration_record = 1 AND year IS NOT NULL GROUP BY year ORDER BY year"},

    {"id": "emigration_per_townland",
     "category": "emigration", "description": "Emigration count broken down by townland",
     "required_keywords": ["emigra", "townland"],
     "optional_keywords": ["per townland", "by townland", "each townland", "most", "which"],
     "sql_template": "SELECT townland, COUNT(DISTINCT record_id) AS emigrated_people FROM unified_record WHERE has_emigration_record=1 GROUP BY townland ORDER BY emigrated_people DESC LIMIT 50"},

    {"id": "emigration_in_year",
     "category": "emigration", "description": "Emigrants in a specific year",
     "required_keywords": ["emigra"],
     "optional_keywords": ["in", "during", "year"],
     "sql_template": "SELECT COUNT(DISTINCT record_id) AS emigrated_in_{year} FROM unified_record WHERE has_emigration_record=1 AND year={year}",
     "requires_year": True},

    {"id": "list_emigrants_all",
     "category": "emigration", "description": "List all emigrated people",
     "required_keywords": ["emigra"],
     "optional_keywords": ["list", "who", "which people", "what people", "all", "show", "names"],
     "sql_template": "SELECT DISTINCT COALESCE(NULLIF(TRIM(canonical_name),''),TRIM(COALESCE(forename,'')||' '||COALESCE(surname,''))) AS person_name,surname,forename,townland,parish,year,departure,arrival,ship_name FROM unified_record WHERE has_emigration_record=1 ORDER BY person_name LIMIT 200"},

    {"id": "list_emigrants_townland",
     "category": "emigration", "description": "List emigrants from a specific townland",
     "required_keywords": ["emigra"],
     "optional_keywords": ["list", "who", "names", "people", "from this townland", "show"],
     "sql_template": "SELECT DISTINCT COALESCE(NULLIF(TRIM(canonical_name),''),TRIM(COALESCE(forename,'')||' '||COALESCE(surname,''))) AS person_name,surname,forename,townland,year,departure,arrival,ship_name FROM unified_record WHERE has_emigration_record=1 AND townland_norm='{townland_norm}' ORDER BY person_name LIMIT 200",
     "requires_townland": True},

    {"id": "emigration_by_ship",
     "category": "emigration", "description": "Emigrants grouped by ship",
     "required_keywords": ["emigra", "ship"],
     "optional_keywords": ["vessel", "boat", "sailed", "which ship", "by ship"],
     "sql_template": "SELECT ship_name, COUNT(DISTINCT record_id) AS passengers FROM unified_record WHERE has_emigration_record=1 AND ship_name IS NOT NULL GROUP BY ship_name ORDER BY passengers DESC LIMIT 50"},

    {"id": "emigration_ships_list",
     "category": "emigration", "description": "Ships used for emigration",
     "required_keywords": ["ship"],
     "optional_keywords": ["emigra", "vessel", "list", "names", "sailed"],
     "sql_template": "SELECT DISTINCT ship_name, COUNT(DISTINCT record_id) AS passengers FROM unified_record WHERE ship_name IS NOT NULL GROUP BY ship_name ORDER BY passengers DESC LIMIT 100"},

    {"id": "emigration_departure",
     "category": "emigration", "description": "Departure places for emigrants",
     "required_keywords": ["depart"],
     "optional_keywords": ["emigra", "from", "port", "left", "where"],
     "sql_template": "SELECT departure, COUNT(DISTINCT record_id) AS people FROM unified_record WHERE departure IS NOT NULL AND has_emigration_record=1 GROUP BY departure ORDER BY people DESC LIMIT 50"},

    {"id": "emigration_arrival",
     "category": "emigration", "description": "Arrival destinations for emigrants",
     "required_keywords": ["arriv"],
     "optional_keywords": ["emigra", "destination", "went to", "sailed to"],
     "sql_template": "SELECT arrival, COUNT(DISTINCT record_id) AS people FROM unified_record WHERE arrival IS NOT NULL AND has_emigration_record=1 GROUP BY arrival ORDER BY people DESC LIMIT 50"},

    {"id": "emigration_surname",
     "category": "emigration", "description": "Emigrants with a specific surname",
     "required_keywords": ["emigra"],
     "optional_keywords": ["surname", "family", "named", "name"],
     "sql_template": "SELECT DISTINCT COALESCE(NULLIF(TRIM(canonical_name),''),TRIM(COALESCE(forename,'')||' '||COALESCE(surname,''))) AS person_name,townland,parish,year,departure,arrival FROM unified_record WHERE has_emigration_record=1 AND UPPER(surname)='{surname}' ORDER BY year LIMIT 200",
     "requires_surname": True},

    {"id": "emigration_households",
     "category": "emigration", "description": "Households that emigrated",
     "required_keywords": ["emigra"],
     "optional_keywords": ["household", "family", "families"],
     "sql_template": "SELECT COALESCE(NULLIF(TRIM(canonical_name),''),TRIM(COALESCE(forename,'')||' '||COALESCE(surname,''))) AS head,household_list,townland,year,ship_name FROM unified_record WHERE has_emigration_record=1 AND household_list IS NOT NULL ORDER BY year LIMIT 200"},

    {"id": "emigration_by_parish",
     "category": "emigration", "description": "Emigration count by parish",
     "required_keywords": ["emigra", "parish"],
     "optional_keywords": ["per parish", "by parish", "each parish", "breakdown"],
     "sql_template": "SELECT parish, COUNT(DISTINCT record_id) AS emigrated FROM unified_record WHERE has_emigration_record=1 AND parish IS NOT NULL GROUP BY parish ORDER BY emigrated DESC LIMIT 50"},

    {"id": "emigration_and_eviction",
     "category": "emigration", "description": "People who were both evicted and emigrated",
     "required_keywords": ["emigra", "evict"],
     "optional_keywords": ["both", "also", "and"],
     "sql_template": "SELECT DISTINCT COALESCE(NULLIF(TRIM(canonical_name),''),TRIM(COALESCE(forename,'')||' '||COALESCE(surname,''))) AS person_name,townland,year FROM unified_record WHERE has_emigration_record=1 AND has_eviction_record=1 ORDER BY year LIMIT 200"},

    {"id": "emigration_townland_year",
     "category": "emigration", "description": "Emigrants from townland in a specific year",
     "required_keywords": ["emigra"],
     "optional_keywords": ["from", "in", "year", "townland"],
     "sql_template": "SELECT COUNT(DISTINCT record_id) AS emigrated FROM unified_record WHERE has_emigration_record=1 AND townland_norm='{townland_norm}' AND year={year}",
     "requires_townland": True, "requires_year": True},

    # ── EVICTION / CLEARANCES ─────────────────────────────────────────────────

    {"id": "eviction_total",
     "category": "eviction", "description": "Total evictions recorded",
     "required_keywords": ["evict"],
     "optional_keywords": ["total", "how many", "count", "all", "overall"],
     "sql_template": "SELECT SUM(eviction_count) AS total_evictions FROM clearances_record"},

    {"id": "eviction_from_townland",
     "category": "eviction", "description": "Evictions from a specific townland",
     "required_keywords": ["evict"],
     "optional_keywords": ["from", "townland", "this"],
     "sql_template": "SELECT cr.year, cr.eviction_count FROM clearances_record cr JOIN townland t ON cr.townland_id=t.id WHERE UPPER(t.name)='{townland_norm}' ORDER BY cr.year",
     "requires_townland": True},

    {"id": "eviction_in_year",
     "category": "eviction", "description": "Evictions in a specific year",
     "required_keywords": ["evict"],
     "optional_keywords": ["in", "during"],
     "sql_template": "SELECT t.name AS townland, cr.eviction_count FROM clearances_record cr JOIN townland t ON cr.townland_id=t.id WHERE cr.year={year} ORDER BY cr.eviction_count DESC LIMIT 50",
     "requires_year": True},

    {"id": "eviction_per_year",
     "category": "eviction", "description": "Evictions per year (trend)",
     "required_keywords": ["evict"],
     "optional_keywords": ["per year", "by year", "year", "trend", "over time", "each year"],
     "sql_template": "SELECT year, SUM(eviction_count) AS total_evictions FROM clearances_record GROUP BY year ORDER BY year"},

    {"id": "eviction_worst_year",
     "category": "eviction", "description": "Year with most evictions",
     "required_keywords": ["evict"],
     "optional_keywords": ["worst", "most", "highest", "peak", "bad"],
     "sql_template": "SELECT year, SUM(eviction_count) AS total_evictions FROM clearances_record GROUP BY year ORDER BY total_evictions DESC LIMIT 5"},

    {"id": "eviction_per_townland",
     "category": "eviction", "description": "Evictions per townland (worst townlands)",
     "required_keywords": ["evict", "townland"],
     "optional_keywords": ["per townland", "by townland", "most", "which", "highest"],
     "sql_template": "SELECT t.name AS townland, SUM(cr.eviction_count) AS total_evictions FROM clearances_record cr JOIN townland t ON cr.townland_id=t.id GROUP BY t.name ORDER BY total_evictions DESC LIMIT 20"},

    {"id": "eviction_people",
     "category": "eviction", "description": "People with eviction records",
     "required_keywords": ["evict"],
     "optional_keywords": ["people", "list", "who", "names", "show"],
     "sql_template": "SELECT DISTINCT COALESCE(NULLIF(TRIM(canonical_name),''),TRIM(COALESCE(forename,'')||' '||COALESCE(surname,''))) AS person_name,townland,parish,year FROM unified_record WHERE has_eviction_record=1 ORDER BY year LIMIT 200"},

    {"id": "eviction_people_townland",
     "category": "eviction", "description": "People evicted from a townland",
     "required_keywords": ["evict"],
     "optional_keywords": ["people", "who", "names", "from", "townland"],
     "sql_template": "SELECT DISTINCT COALESCE(NULLIF(TRIM(canonical_name),''),TRIM(COALESCE(forename,'')||' '||COALESCE(surname,''))) AS person_name,townland,year FROM unified_record WHERE has_eviction_record=1 AND townland_norm='{townland_norm}' ORDER BY year LIMIT 200",
     "requires_townland": True},

    {"id": "clearances_total_records",
     "category": "eviction", "description": "Total clearance records in database",
     "required_keywords": ["clearance"],
     "optional_keywords": ["total", "how many", "count"],
     "sql_template": "SELECT COUNT(*) AS total_clearance_records, SUM(eviction_count) AS total_evictions FROM clearances_record"},

    # ── CENSUS / POPULATION ───────────────────────────────────────────────────

    {"id": "census_population_townland_year",
     "category": "census", "description": "Population of a townland in a specific year",
     "required_keywords": ["population"],
     "optional_keywords": ["townland", "in", "year", "people", "how many"],
     "sql_template": "SELECT c.year, c.male, c.female, c.total, c.inhabited, c.uninhabited FROM census_record c JOIN townland t ON c.townland_id=t.id WHERE UPPER(t.name)='{townland_norm}' AND c.year={year}",
     "requires_townland": True, "requires_year": True},

    {"id": "census_population_townland",
     "category": "census", "description": "Population history of a townland",
     "required_keywords": ["population"],
     "optional_keywords": ["townland", "history", "over time", "all years", "change"],
     "sql_template": "SELECT c.year, c.male, c.female, c.total FROM census_record c JOIN townland t ON c.townland_id=t.id WHERE UPPER(t.name)='{townland_norm}' ORDER BY c.year",
     "requires_townland": True},

    {"id": "census_total_year",
     "category": "census", "description": "Total estate population in a census year",
     "required_keywords": ["population"],
     "optional_keywords": ["total", "all", "estate", "wicklow", "overall"],
     "sql_template": "SELECT year, SUM(total) AS estate_population, SUM(male) AS total_male, SUM(female) AS total_female FROM census_record GROUP BY year ORDER BY year"},

    {"id": "census_decline_famine",
     "category": "census", "description": "Population decline during the Famine (1841–1851)",
     "required_keywords": ["population"],
     "optional_keywords": ["decline", "famine", "1841", "1851", "drop", "loss", "fell", "change"],
     "sql_template": "SELECT t.name AS townland, a.total AS pop_1841, b.total AS pop_1851, (b.total-a.total) AS change, ROUND(100.0*(b.total-a.total)/a.total,1) AS pct_change FROM census_record a JOIN census_record b ON a.townland_id=b.townland_id JOIN townland t ON a.townland_id=t.id WHERE a.year=1841 AND b.year=1851 ORDER BY change ASC LIMIT 30"},

    {"id": "census_largest_townland",
     "category": "census", "description": "Largest townlands by population in a year",
     "required_keywords": ["population", "largest"],
     "optional_keywords": ["biggest", "most people", "highest", "most populous"],
     "sql_template": "SELECT t.name AS townland, c.total AS population, c.year FROM census_record c JOIN townland t ON c.townland_id=t.id WHERE c.year={year} ORDER BY c.total DESC LIMIT 20",
     "requires_year": True},

    {"id": "census_largest_any_year",
     "category": "census", "description": "Largest townlands (most recent census year)",
     "required_keywords": ["largest"],
     "optional_keywords": ["townland", "population", "most", "biggest"],
     "sql_template": "SELECT t.name AS townland, c.year, c.total AS population FROM census_record c JOIN townland t ON c.townland_id=t.id ORDER BY c.total DESC LIMIT 20"},

    {"id": "census_uninhabited",
     "category": "census", "description": "Uninhabited houses per townland",
     "required_keywords": ["uninhabit"],
     "optional_keywords": ["houses", "empty", "abandoned", "vacant"],
     "sql_template": "SELECT t.name AS townland, c.year, c.uninhabited, c.inhabited FROM census_record c JOIN townland t ON c.townland_id=t.id WHERE c.uninhabited > 0 ORDER BY c.uninhabited DESC LIMIT 50"},

    {"id": "census_houses",
     "category": "census", "description": "Inhabited and uninhabited houses by year",
     "required_keywords": ["house"],
     "optional_keywords": ["inhabited", "uninhabited", "buildings", "dwellings", "census"],
     "sql_template": "SELECT year, SUM(inhabited) AS inhabited_houses, SUM(uninhabited) AS uninhabited_houses FROM census_record GROUP BY year ORDER BY year"},

    {"id": "census_all_years_trend",
     "category": "census", "description": "Estate population trend across all census years",
     "required_keywords": ["population", "census"],
     "optional_keywords": ["trend", "all years", "over time", "history", "change"],
     "sql_template": "SELECT year, SUM(total) AS estate_total, SUM(male) AS male_total, SUM(female) AS female_total FROM census_record GROUP BY year ORDER BY year"},

    {"id": "census_by_parish",
     "category": "census", "description": "Census population aggregated by parish",
     "required_keywords": ["population", "parish"],
     "optional_keywords": ["by parish", "per parish", "each parish"],
     "sql_template": "SELECT t.civil_parish AS parish, c.year, SUM(c.total) AS total_population FROM census_record c JOIN townland t ON c.townland_id=t.id WHERE t.civil_parish IS NOT NULL GROUP BY t.civil_parish, c.year ORDER BY c.year, total_population DESC LIMIT 100"},

    {"id": "census_1841",
     "category": "census", "description": "Population in 1841",
     "required_keywords": ["1841"],
     "optional_keywords": ["population", "census", "people", "how many"],
     "sql_template": "SELECT t.name AS townland, c.total AS population_1841, c.male, c.female FROM census_record c JOIN townland t ON c.townland_id=t.id WHERE c.year=1841 ORDER BY c.total DESC LIMIT 50"},

    {"id": "census_1851",
     "category": "census", "description": "Population in 1851 (post-Famine)",
     "required_keywords": ["1851"],
     "optional_keywords": ["population", "census", "people", "famine"],
     "sql_template": "SELECT t.name AS townland, c.total AS population_1851, c.male, c.female FROM census_record c JOIN townland t ON c.townland_id=t.id WHERE c.year=1851 ORDER BY c.total DESC LIMIT 50"},

    # ── TOWNLAND / GEOGRAPHY ──────────────────────────────────────────────────

    {"id": "townlands_total_count",
     "category": "geography", "description": "Total number of townlands in the estate",
     "required_keywords": ["townland"],
     "optional_keywords": ["how many", "total", "count", "estate", "all"],
     "sql_template": "SELECT COUNT(*) AS total_townlands FROM townland"},

    {"id": "parishes_count",
     "category": "geography", "description": "Number of distinct civil parishes",
     "required_keywords": ["parish"],
     "optional_keywords": ["how many", "count", "total", "distinct", "different"],
     "sql_template": "SELECT COUNT(DISTINCT civil_parish) AS parish_count FROM townland WHERE civil_parish IS NOT NULL AND TRIM(civil_parish)!=''"},

    {"id": "parishes_list",
     "category": "geography", "description": "List all civil parishes",
     "required_keywords": ["parish"],
     "optional_keywords": ["list", "all", "which", "names", "show"],
     "sql_template": "SELECT DISTINCT civil_parish, COUNT(*) AS townland_count FROM townland WHERE civil_parish IS NOT NULL GROUP BY civil_parish ORDER BY civil_parish"},

    {"id": "parishes_count_and_people",
     "category": "geography", "description": "Parish count plus all people in a townland",
     "required_keywords": ["parish", "people"],
     "optional_keywords": ["how many", "townland", "all", "list", "what"],
     "sql_template": "SELECT civil_parish, COUNT(*) AS townlands FROM townland WHERE civil_parish IS NOT NULL GROUP BY civil_parish ORDER BY civil_parish"},

    {"id": "townland_parish_lookup",
     "category": "geography", "description": "Which parish a townland belongs to",
     "required_keywords": ["parish"],
     "optional_keywords": ["which", "belong", "in", "townland"],
     "sql_template": "SELECT name, civil_parish, barony, county FROM townland WHERE UPPER(name)='{townland_norm}'",
     "requires_townland": True},

    {"id": "townlands_in_parish",
     "category": "geography", "description": "Townlands in the same parish as a given townland",
     "required_keywords": ["townland", "parish"],
     "optional_keywords": ["in", "within", "list", "which", "all", "same parish", "other"],
     "sql_template": "SELECT name, civil_parish, barony, county FROM townland WHERE civil_parish=(SELECT civil_parish FROM townland WHERE UPPER(name)='{townland_norm}' LIMIT 1) AND UPPER(name)!='{townland_norm}' ORDER BY name LIMIT 200",
     "requires_townland": True},

    {"id": "townlands_by_county",
     "category": "geography", "description": "Townlands grouped by county",
     "required_keywords": ["townland", "county"],
     "optional_keywords": ["by county", "each county", "which county", "list"],
     "sql_template": "SELECT county, COUNT(*) AS townland_count FROM townland WHERE county IS NOT NULL GROUP BY county ORDER BY townland_count DESC"},

    {"id": "townland_details",
     "category": "geography", "description": "Full details of a specific townland",
     "required_keywords": [],
     "optional_keywords": ["details", "about", "information", "info", "townland", "monument", "historical"],
     "sql_template": "SELECT name, name_gaelic, civil_parish, barony, county, centroid_lat, centroid_lon FROM townland WHERE UPPER(name)='{townland_norm}'",
     "requires_townland": True},

    {"id": "townland_nearby",
     "category": "geography", "description": "Townlands within 20km",
     "required_keywords": [],
     "optional_keywords": ["nearby", "near", "around", "within", "20km", "close", "radius"],
     "sql_template": "WITH base AS (SELECT centroid_lat lat,centroid_lon lon FROM townland WHERE UPPER(name)='{townland_norm}' LIMIT 1) SELECT t.name,t.civil_parish,t.county,ROUND(distance_km(t.centroid_lat,t.centroid_lon,base.lat,base.lon),1) AS dist_km FROM townland t,base WHERE t.centroid_lat IS NOT NULL AND distance_km(t.centroid_lat,t.centroid_lon,base.lat,base.lon)<=20 ORDER BY dist_km LIMIT 30",
     "requires_townland": True},

    {"id": "barony_list",
     "category": "geography", "description": "All baronies in the estate",
     "required_keywords": ["baron"],
     "optional_keywords": ["list", "all", "how many", "which"],
     "sql_template": "SELECT DISTINCT barony, COUNT(*) AS townland_count FROM townland WHERE barony IS NOT NULL GROUP BY barony ORDER BY barony"},

    # ── PEOPLE / NAMES ────────────────────────────────────────────────────────

    {"id": "people_all_in_townland",
     "category": "people", "description": "All people recorded in a townland",
     "required_keywords": ["people"],
     "optional_keywords": ["all", "who", "list", "names", "in", "townland", "this"],
     "sql_template": "SELECT DISTINCT COALESCE(NULLIF(TRIM(canonical_name),''),TRIM(COALESCE(forename,'')||' '||COALESCE(surname,''))) AS person_name,surname,forename,year,role,has_emigration_record,has_eviction_record,has_tenancy_record FROM unified_record WHERE townland_norm='{townland_norm}' ORDER BY person_name LIMIT 200",
     "requires_townland": True},

    {"id": "people_surname_search",
     "category": "people", "description": "People with a specific surname",
     "required_keywords": ["surname"],
     "optional_keywords": ["people", "who", "list", "family", "all", "named"],
     "sql_template": "SELECT DISTINCT COALESCE(NULLIF(TRIM(canonical_name),''),TRIM(COALESCE(forename,'')||' '||COALESCE(surname,''))) AS person_name,townland,parish,year,has_emigration_record,has_eviction_record FROM unified_record WHERE UPPER(surname)='{surname}' ORDER BY person_name LIMIT 200",
     "requires_surname": True},

    {"id": "people_all_records",
     "category": "people", "description": "All people in the database",
     "required_keywords": ["people"],
     "optional_keywords": ["all", "every", "list", "database", "records", "total", "everyone"],
     "sql_template": "SELECT COUNT(DISTINCT record_id) AS total_people, COUNT(DISTINCT UPPER(surname)) AS distinct_surnames FROM unified_record"},

    {"id": "people_in_year",
     "category": "people", "description": "People recorded in a specific year",
     "required_keywords": ["people"],
     "optional_keywords": ["in", "year", "during", "recorded"],
     "sql_template": "SELECT DISTINCT COALESCE(NULLIF(TRIM(canonical_name),''),TRIM(COALESCE(forename,'')||' '||COALESCE(surname,''))) AS person_name,townland,role,has_emigration_record,has_eviction_record FROM unified_record WHERE year={year} ORDER BY person_name LIMIT 200",
     "requires_year": True},

    {"id": "surnames_list",
     "category": "people", "description": "Distinct surnames in the records",
     "required_keywords": ["surname"],
     "optional_keywords": ["all", "list", "distinct", "different", "what", "which"],
     "sql_template": "SELECT UPPER(surname) AS surname, COUNT(DISTINCT record_id) AS occurrences FROM unified_record WHERE surname IS NOT NULL GROUP BY UPPER(surname) ORDER BY occurrences DESC LIMIT 100"},

    {"id": "people_by_role",
     "category": "people", "description": "People grouped by their role",
     "required_keywords": ["role"],
     "optional_keywords": ["people", "list", "all", "by role", "head", "household"],
     "sql_template": "SELECT role, COUNT(DISTINCT record_id) AS people_count FROM unified_record WHERE role IS NOT NULL GROUP BY role ORDER BY people_count DESC LIMIT 30"},

    {"id": "heads_of_household",
     "category": "people", "description": "Heads of household",
     "required_keywords": ["head"],
     "optional_keywords": ["household", "family", "list", "all"],
     "sql_template": "SELECT DISTINCT COALESCE(NULLIF(TRIM(canonical_name),''),TRIM(COALESCE(forename,'')||' '||COALESCE(surname,''))) AS person_name,townland,year FROM unified_record WHERE LOWER(role) LIKE '%head%' ORDER BY person_name LIMIT 200"},

    {"id": "people_townland_year",
     "category": "people", "description": "People in a townland in a specific year",
     "required_keywords": ["people"],
     "optional_keywords": ["townland", "year", "in", "during"],
     "sql_template": "SELECT DISTINCT COALESCE(NULLIF(TRIM(canonical_name),''),TRIM(COALESCE(forename,'')||' '||COALESCE(surname,''))) AS person_name,role,has_emigration_record,has_eviction_record FROM unified_record WHERE townland_norm='{townland_norm}' AND year={year} ORDER BY person_name LIMIT 200",
     "requires_townland": True, "requires_year": True},

    # ── TENANCY ───────────────────────────────────────────────────────────────

    {"id": "tenants_total",
     "category": "tenancy", "description": "Total tenants in the records",
     "required_keywords": ["tenant"],
     "optional_keywords": ["total", "how many", "count", "all"],
     "sql_template": "SELECT COUNT(DISTINCT record_id) AS total_tenants FROM unified_record WHERE has_tenancy_record=1"},

    {"id": "tenants_list",
     "category": "tenancy", "description": "List all tenants",
     "required_keywords": ["tenant"],
     "optional_keywords": ["list", "who", "names", "all", "show"],
     "sql_template": "SELECT DISTINCT COALESCE(NULLIF(TRIM(canonical_name),''),TRIM(COALESCE(forename,'')||' '||COALESCE(surname,''))) AS person_name,townland,parish,year FROM unified_record WHERE has_tenancy_record=1 ORDER BY person_name LIMIT 200"},

    {"id": "tenants_townland",
     "category": "tenancy", "description": "Tenants in a specific townland",
     "required_keywords": ["tenant"],
     "optional_keywords": ["townland", "from", "in", "list"],
     "sql_template": "SELECT DISTINCT COALESCE(NULLIF(TRIM(canonical_name),''),TRIM(COALESCE(forename,'')||' '||COALESCE(surname,''))) AS person_name,year FROM unified_record WHERE has_tenancy_record=1 AND townland_norm='{townland_norm}' ORDER BY person_name LIMIT 200",
     "requires_townland": True},

    {"id": "tenants_per_townland",
     "category": "tenancy", "description": "Tenant count per townland",
     "required_keywords": ["tenant", "townland"],
     "optional_keywords": ["per", "by", "each", "breakdown", "count"],
     "sql_template": "SELECT townland, COUNT(DISTINCT record_id) AS tenant_count FROM unified_record WHERE has_tenancy_record=1 GROUP BY townland ORDER BY tenant_count DESC LIMIT 50"},

    # ── RECORDS OVERVIEW ──────────────────────────────────────────────────────

    {"id": "records_overview",
     "category": "overview", "description": "Overview of all record types in the database",
     "required_keywords": ["record"],
     "optional_keywords": ["total", "how many", "overview", "all", "types", "count"],
     "sql_template": "SELECT COUNT(DISTINCT record_id) AS total_records, SUM(has_emigration_record) AS emigration_records, SUM(has_eviction_record) AS eviction_records, SUM(has_tenancy_record) AS tenancy_records FROM unified_record"},

    {"id": "records_per_year",
     "category": "overview", "description": "Records per year",
     "required_keywords": ["record", "year"],
     "optional_keywords": ["per year", "by year", "each year", "count", "total"],
     "sql_template": "SELECT year, COUNT(DISTINCT record_id) AS records FROM unified_record WHERE year IS NOT NULL GROUP BY year ORDER BY year"},

    {"id": "records_per_townland",
     "category": "overview", "description": "Records per townland",
     "required_keywords": ["record", "townland"],
     "optional_keywords": ["per", "by", "each", "count", "how many"],
     "sql_template": "SELECT townland, COUNT(DISTINCT record_id) AS records FROM unified_record WHERE townland IS NOT NULL GROUP BY townland ORDER BY records DESC LIMIT 50"},

    # ── COMBINED QUESTIONS ────────────────────────────────────────────────────

    {"id": "famine_impact",
     "category": "overview", "description": "Famine impact: evictions, emigration and population decline",
     "required_keywords": ["famine"],
     "optional_keywords": ["impact", "effect", "great", "potato"],
     "sql_template": "SELECT 'Total evictions' AS metric, CAST(SUM(eviction_count) AS TEXT) AS value FROM clearances_record UNION ALL SELECT 'Total emigrated', CAST(COUNT(DISTINCT record_id) AS TEXT) FROM unified_record WHERE has_emigration_record=1"},

    {"id": "emigration_and_population",
     "category": "overview", "description": "Compare emigration numbers with census population",
     "required_keywords": ["emigra", "population"],
     "optional_keywords": ["compare", "versus", "vs", "census", "relation"],
     "sql_template": "SELECT c.year, SUM(c.total) AS census_population, (SELECT COUNT(DISTINCT record_id) FROM unified_record WHERE has_emigration_record=1 AND year<=c.year) AS cumulative_emigrants FROM census_record c GROUP BY c.year ORDER BY c.year"},

    {"id": "tenant_land_gender_average",
     "category": "tenancy", "description": "Average landholding for male and female tenants",
     "required_keywords": ["tenant", "land"],
     "optional_keywords": ["average", "male", "female", "acres", "mean"],
     "sql_template": "SELECT CASE WHEN UPPER(gender) IN ('M','MALE') THEN 'Male' WHEN UPPER(gender) IN ('F','FEMALE') THEN 'Female' ELSE 'Unknown' END AS gender_group, ROUND(AVG(holding_acres),2) AS average_holding_acres, COUNT(DISTINCT record_id) AS tenant_records FROM unified_record WHERE has_tenancy_record=1 AND holding_acres IS NOT NULL AND UPPER(COALESCE(gender,'')) IN ('M','MALE','F','FEMALE') GROUP BY gender_group ORDER BY gender_group"},

    {"id": "widows_with_children_proportion",
     "category": "people", "description": "Proportion of widows with recorded children",
     "required_keywords": ["widow"],
     "optional_keywords": ["proportion", "children", "child", "how many", "percent", "percentage"],
     "sql_template": "SELECT COUNT(DISTINCT record_id) AS widow_records, COUNT(DISTINCT CASE WHEN children_count > 0 THEN record_id END) AS widows_with_children, ROUND(100.0 * COUNT(DISTINCT CASE WHEN children_count > 0 THEN record_id END) / NULLIF(COUNT(DISTINCT record_id),0), 1) AS pct_widows_with_children FROM unified_record WHERE is_widow=1",
     "warning": "Widows are identified from widow-labelled names or notes in the source rows, and children are counted from recorded sons + daughters fields."},

    {"id": "widows_eviction_proportion",
     "category": "people", "description": "Proportion of widows appearing on eviction records",
     "required_keywords": ["widow"],
     "optional_keywords": ["proportion", "eviction", "evicted", "percent", "percentage"],
     "sql_template": "SELECT COUNT(DISTINCT record_id) AS widow_records, COUNT(DISTINCT CASE WHEN has_eviction_record=1 THEN record_id END) AS widows_on_eviction_records, ROUND(100.0 * COUNT(DISTINCT CASE WHEN has_eviction_record=1 THEN record_id END) / NULLIF(COUNT(DISTINCT record_id),0), 1) AS pct_widows_on_eviction_records FROM unified_record WHERE is_widow=1",
     "warning": "Widows are identified from widow-labelled names or notes in the source rows."},

    {"id": "widows_count",
     "category": "people", "description": "Count widows in the records",
     "required_keywords": ["widow"],
     "optional_keywords": ["how many", "count", "appear", "records", "total"],
     "sql_template": "SELECT COUNT(DISTINCT record_id) AS widow_records FROM unified_record WHERE is_widow=1",
     "warning": "Widows are identified from widow-labelled names or notes in the source rows."},

    {"id": "children_emigrated",
     "category": "emigration", "description": "Count child emigrants based on recorded age",
     "required_keywords": ["emigra"],
     "optional_keywords": ["children", "child", "under 18", "age", "how many"],
     "sql_template": "SELECT COUNT(DISTINCT record_id) AS child_emigrant_records, COUNT(DISTINCT CASE WHEN age IS NOT NULL THEN record_id END) AS emigrant_records_with_known_age FROM unified_record WHERE has_emigration_record=1 AND age IS NOT NULL AND age < 18",
     "warning": "Child emigrants are counted here as emigration records with a recorded age under 18."},

    {"id": "eviction_family_size_range",
     "category": "eviction", "description": "Range of estimated family sizes in eviction records",
     "required_keywords": ["family", "size"],
     "optional_keywords": ["range", "eviction", "evicted", "records", "household"],
     "sql_template": "WITH eviction_families AS (SELECT DISTINCT family_key FROM unified_record WHERE has_eviction_record=1 AND family_key IS NOT NULL AND TRIM(family_key) <> '' AND INSTR(family_key,'|') > 0 AND TRIM(SUBSTR(family_key, INSTR(family_key,'|') + 1)) <> ''), family_sizes AS (SELECT family_key, MAX(family_size_estimate) AS family_size_estimate FROM unified_record WHERE family_key IS NOT NULL AND TRIM(family_key) <> '' AND INSTR(family_key,'|') > 0 AND TRIM(SUBSTR(family_key, INSTR(family_key,'|') + 1)) <> '' AND family_size_estimate IS NOT NULL GROUP BY family_key), matched AS (SELECT e.family_key, f.family_size_estimate FROM eviction_families e JOIN family_sizes f ON f.family_key = e.family_key) SELECT (SELECT COUNT(*) FROM eviction_families) AS eviction_families_with_keys, COUNT(*) AS matched_eviction_families, ROUND(100.0 * COUNT(*) / NULLIF((SELECT COUNT(*) FROM eviction_families),0), 1) AS pct_eviction_families_with_estimated_size, MIN(family_size_estimate) AS smallest_estimated_family, MAX(family_size_estimate) AS largest_estimated_family, ROUND(AVG(family_size_estimate),2) AS average_estimated_family_size FROM matched",
     "warning": "Eviction rows rarely include household counts directly, so this uses linked family keys where an estimated family size exists elsewhere in the database."},

    {"id": "most_populous_1841_vs_1861",
     "category": "census", "description": "Compare the most populous townlands in 1841 and 1861",
     "required_keywords": ["1841", "1861"],
     "optional_keywords": ["populous", "most populous", "still", "census"],
     "sql_template": "WITH pop_1841 AS (SELECT t.name AS townland, c.total AS population FROM census_record c JOIN townland t ON c.townland_id=t.id WHERE c.year=1841 ORDER BY c.total DESC LIMIT 1), pop_1861 AS (SELECT t.name AS townland, c.total AS population FROM census_record c JOIN townland t ON c.townland_id=t.id WHERE c.year=1861 ORDER BY c.total DESC LIMIT 1) SELECT pop_1841.townland AS most_populous_1841, pop_1841.population AS population_1841, pop_1861.townland AS most_populous_1861, pop_1861.population AS population_1861, CASE WHEN pop_1841.townland = pop_1861.townland THEN 'yes' ELSE 'no' END AS same_townland FROM pop_1841 CROSS JOIN pop_1861"},

    {"id": "population_trend_1841_1861",
     "category": "census", "description": "Estate-wide population trend for available early census years",
     "required_keywords": ["population", "trend"],
     "optional_keywords": ["estate", "overall", "1841", "1851", "1861", "1821"],
     "sql_template": "SELECT year, SUM(total) AS estate_population FROM census_record WHERE year IN (1841,1851,1861) GROUP BY year ORDER BY year",
     "warning": "The Ask census table begins in 1841, so this trend uses 1841, 1851, and 1861 rather than 1821."},

    {"id": "emigration_population_townland_trend",
     "category": "overview", "description": "Top emigration townlands compared with 1841 to 1861 population change",
     "required_keywords": ["emigra", "population", "townland"],
     "optional_keywords": ["trend", "relationship", "relation", "1841", "1861", "most"],
     "sql_template": "WITH emigrants AS (SELECT townland_norm, townland, COUNT(DISTINCT record_id) AS emigrated_people FROM unified_record WHERE has_emigration_record=1 AND townland_norm IS NOT NULL GROUP BY townland_norm, townland), pop_1841 AS (SELECT t.name AS townland, c.total AS pop_1841 FROM census_record c JOIN townland t ON c.townland_id=t.id WHERE c.year=1841), pop_1861 AS (SELECT t.name AS townland, c.total AS pop_1861 FROM census_record c JOIN townland t ON c.townland_id=t.id WHERE c.year=1861) SELECT e.townland, e.emigrated_people, p41.pop_1841, p61.pop_1861, (p61.pop_1861 - p41.pop_1841) AS population_change, ROUND(100.0 * (p61.pop_1861 - p41.pop_1841) / NULLIF(p41.pop_1841,0), 1) AS pct_population_change FROM emigrants e LEFT JOIN pop_1841 p41 ON UPPER(p41.townland)=e.townland_norm LEFT JOIN pop_1861 p61 ON UPPER(p61.townland)=e.townland_norm ORDER BY e.emigrated_people DESC LIMIT 30",
     "warning": "Population change is based on 1841 to 1861 because the Ask census table does not include 1821."},

    {"id": "largest_latest_tenant_holdings",
     "category": "tenancy", "description": "Tenants with the largest holdings in their latest recorded year",
     "required_keywords": ["tenant", "land"],
     "optional_keywords": ["latest", "end", "final", "record dates", "largest", "more"],
     "sql_template": "WITH tenancy AS (SELECT COALESCE(NULLIF(TRIM(canonical_name),''), NULLIF(TRIM(COALESCE(forename,'') || ' ' || COALESCE(surname,'')), ''), NULLIF(TRIM(COALESCE(chief_tenant_forename,'') || ' ' || COALESCE(chief_tenant_surname,'')), '')) AS person_name, townland, year, holding_acres FROM unified_record WHERE has_tenancy_record=1 AND holding_acres IS NOT NULL), latest AS (SELECT person_name, MAX(year) AS latest_year FROM tenancy WHERE person_name IS NOT NULL GROUP BY person_name) SELECT t.person_name, t.townland, t.year AS latest_year, ROUND(t.holding_acres,2) AS holding_acres FROM tenancy t JOIN latest l ON t.person_name=l.person_name AND t.year=l.latest_year ORDER BY t.holding_acres DESC, t.person_name LIMIT 50"},

    {"id": "smallest_townland_plots",
     "category": "tenancy", "description": "Townlands with the smallest tenant plots",
     "required_keywords": ["townland", "smallest"],
     "optional_keywords": ["plot", "plots", "land", "tenant", "acres"],
     "sql_template": "SELECT townland, ROUND(MIN(holding_acres),2) AS smallest_plot_acres, ROUND(AVG(holding_acres),2) AS average_plot_acres, COUNT(DISTINCT record_id) AS tenancy_records FROM unified_record WHERE has_tenancy_record=1 AND holding_acres IS NOT NULL GROUP BY townland_norm, townland ORDER BY smallest_plot_acres ASC, average_plot_acres ASC LIMIT 30"},

    {"id": "holy_well_population_relationship",
     "category": "heritage", "description": "Compare census populations for townlands with and without holy wells",
     "required_keywords": ["holy", "well"],
     "optional_keywords": ["population", "relationship", "statistical", "high", "figures"],
     "sql_template": "WITH holy AS (SELECT DISTINCT townland_norm FROM heritage_feature WHERE feature_group='holy_well' AND townland_norm IS NOT NULL), census AS (SELECT t.name AS townland, c.year, c.total FROM census_record c JOIN townland t ON c.townland_id=t.id) SELECT CASE WHEN UPPER(census.townland) IN (SELECT townland_norm FROM holy) THEN 'Has holy well' ELSE 'No holy well' END AS holy_well_group, COUNT(*) AS census_rows, ROUND(AVG(census.total),2) AS average_population, MAX(census.total) AS max_population FROM census GROUP BY holy_well_group ORDER BY average_population DESC",
     "warning": "This compares average recorded census populations for townlands with and without holy wells; it is a descriptive comparison rather than a formal significance test."},

    {"id": "ring_fort_population_relationship",
     "category": "heritage", "description": "Compare census populations for townlands with and without ring forts",
     "required_keywords": ["ring"],
     "optional_keywords": ["fort", "ring fort", "ringfort", "population", "relationship", "statistical", "high"],
     "sql_template": "WITH ringfort AS (SELECT DISTINCT townland_norm FROM heritage_feature WHERE feature_group='ring_fort' AND townland_norm IS NOT NULL), census AS (SELECT t.name AS townland, c.year, c.total FROM census_record c JOIN townland t ON c.townland_id=t.id) SELECT CASE WHEN UPPER(census.townland) IN (SELECT townland_norm FROM ringfort) THEN 'Has ring fort' ELSE 'No ring fort' END AS ring_fort_group, COUNT(*) AS census_rows, ROUND(AVG(census.total),2) AS average_population, MAX(census.total) AS max_population FROM census GROUP BY ring_fort_group ORDER BY average_population DESC",
     "warning": "This compares average recorded census populations for townlands with and without ring forts; it is a descriptive comparison rather than a formal significance test."},

    {"id": "canada_emigration_peak_period",
     "category": "emigration", "description": "Peak years for emigration to Canada",
     "required_keywords": ["emigra", "canada"],
     "optional_keywords": ["peak", "period", "year", "when"],
     "sql_template": "SELECT year, COUNT(DISTINCT record_id) AS canada_emigrant_records FROM unified_record WHERE has_emigration_record=1 AND is_canada_destination=1 AND year IS NOT NULL GROUP BY year ORDER BY canada_emigrant_records DESC, year",
     "warning": "Canada-focused emigration is identified from arrival text such as Quebec, St Andrews, Grosse Isle, or Canada."},

    {"id": "ship_most_families_canada",
     "category": "emigration", "description": "Ship carrying the most Coolattin families to Canada",
     "required_keywords": ["ship", "canada"],
     "optional_keywords": ["family", "families", "most", "carried", "coolattin"],
     "sql_template": "SELECT ship_name, COUNT(DISTINCT COALESCE(NULLIF(TRIM(family_key),''), UPPER(COALESCE(surname,'')) || '|' || UPPER(COALESCE(townland_norm,'')))) AS family_count, COUNT(DISTINCT record_id) AS emigrant_records FROM unified_record WHERE has_emigration_record=1 AND is_canada_destination=1 AND ship_name IS NOT NULL AND TRIM(ship_name) <> '' GROUP BY ship_name ORDER BY family_count DESC, emigrant_records DESC, ship_name LIMIT 20",
     "warning": "Canada-focused emigration is identified from arrival text, and family counts use family_key when available or a surname+townland fallback."},

    {"id": "estate_summary",
     "category": "overview", "description": "Full estate summary statistics",
     "required_keywords": ["estate"],
     "optional_keywords": ["summary", "overview", "about", "statistics", "data"],
     "sql_template": "SELECT (SELECT COUNT(*) FROM townland) AS townlands, (SELECT COUNT(DISTINCT civil_parish) FROM townland) AS parishes, (SELECT COUNT(DISTINCT record_id) FROM unified_record) AS total_records, (SELECT COUNT(DISTINCT record_id) FROM unified_record WHERE has_emigration_record=1) AS emigrated, (SELECT COUNT(DISTINCT record_id) FROM unified_record WHERE has_eviction_record=1) AS evicted, (SELECT COUNT(DISTINCT record_id) FROM unified_record WHERE has_tenancy_record=1) AS tenants"},
]


def _extract_year(question: str) -> int | None:
    m = re.search(r"\b(18[0-9]{2}|19[0-2][0-9])\b", question)
    return int(m.group(1)) if m else None


def _extract_radius_km(question: str) -> int | None:
    q = (question or "").lower()
    m = re.search(r"\b(\d{1,3})\s*km\b", q)
    if m:
        return int(m.group(1))
    if any(x in q for x in ["around", "nearby", "radius", "within"]) and "townland" in q:
        return 20
    return None


def _extract_surname(question: str) -> str | None:
    # "{Surname} family" must come before "family {word}" so "Byrne family members"
    # captures "Byrne" rather than letting the next pattern capture "members".
    patterns = [
        r"\bsurname[s]?\s+(?:of\s+|is\s+)?['\"]?(\w+)['\"]?",
        r"\b([A-Za-z][A-Za-z'-]{2,})\s+family\b",
        r"\bfamily\s+(?:name\s+)?['\"]?(\w+)['\"]?",
        r"\b(?:about|for|on)\s+([A-Za-z][A-Za-z'-]{2,})\s+(?:family|surname|people|records)\b",
        r"\bnamed?\s+['\"]?(\w+)['\"]?",
        r"\bby\s+the\s+name\s+(?:of\s+)?['\"]?(\w+)['\"]?",
    ]
    for p in patterns:
        m = re.search(p, question, re.I)
        if m:
            candidate = m.group(1).upper()
            # reject common words that aren't surnames
            if candidate not in {"THE", "A", "AN", "THIS", "THAT", "THEIR", "ALL", "HOW", "MANY", "MEMBERS", "NAME"}:
                return candidate
    return None


def _analyse_question(question: str, townland_hint: str | None) -> dict[str, Any]:
    q = (question or "").lower()
    year = _extract_year(question)
    surname = _extract_surname(question)
    # Fix 3b: canonicalize extracted surname via entity_resolver so fuzzy spellings
    # (e.g. "Kavanah") resolve to their DB canonical form ("KAVANAGH").
    if surname:
        try:
            from backend.services.entity_resolver import resolve_entity as _re_s
            _sr = _re_s(surname, entity_type="surname")
            if _sr.label_norm and _sr.confidence >= 0.70:
                surname = _sr.label_norm
        except Exception:
            pass
    hint = _norm_townland(townland_hint)
    radius_km = _extract_radius_km(question)

    asks_population = any(x in q for x in ["population", "census", "inhabited", "uninhabited"])
    asks_emigration = "emigra" in q
    asks_eviction = "evict" in q or "clearance" in q
    asks_tenancy = "tenant" in q or "tenancy" in q
    asks_people = bool(surname) or any(x in q for x in ["people", "person", "persons", "who", "names", "family", "families", "records"])
    asks_parish = "parish" in q
    asks_barony = "barony" in q
    asks_county = "county" in q
    mentions_townland = any(x in q for x in ["townland", "from this", "in this", "this place"])

    if any(x in q for x in ["by year", "per year", "yearly", "each year", "over time", "trend"]):
        group_by = "year"
    elif any(x in q for x in ["by parish", "per parish", "each parish"]):
        group_by = "parish"
    elif any(x in q for x in ["by townland", "per townland", "each townland"]):
        group_by = "townland"
    elif any(x in q for x in ["by surname", "per surname", "family name", "surname"]):
        group_by = "surname"
    elif any(x in q for x in ["by ship", "per ship"]):
        group_by = "ship_name"
    else:
        group_by = None

    wants_list = any(
        x in q
        for x in [
            "list",
            "show",
            "who",
            "which people",
            "which townlands",
            "what townlands",
            "what other townlands",
            "names",
            "what all",
        ]
    )
    wants_count = any(x in q for x in ["how many", "count", "total", "number of"])

    if group_by:
        output_mode = "grouped"
    elif wants_list:
        output_mode = "list"
    elif wants_count:
        output_mode = "count"
    else:
        output_mode = "detail"

    if asks_population:
        primary_intent = "population"
    elif asks_eviction:
        primary_intent = "eviction"
    elif asks_tenancy:
        primary_intent = "tenancy"
    elif asks_emigration:
        primary_intent = "emigration"
    elif asks_people:
        primary_intent = "people"
    elif asks_parish or asks_barony or asks_county or mentions_townland:
        primary_intent = "geography"
    else:
        primary_intent = "overview"

    secondary_intents: list[str] = []
    if asks_people and primary_intent != "people":
        secondary_intents.append("people")
    if asks_parish and primary_intent != "geography":
        secondary_intents.append("parish")

    if radius_km and hint:
        scope = "radius"
    elif hint and (mentions_townland or primary_intent in {"people", "population", "emigration", "eviction", "tenancy"}):
        scope = "townland"
    else:
        scope = "global"

    if primary_intent == "population":
        preferred_tables = ["census_record", "townland"]
    elif primary_intent == "eviction":
        preferred_tables = ["clearances_record", "townland"]
    elif primary_intent in {"people", "emigration", "tenancy"}:
        preferred_tables = ["unified_record"]
    elif primary_intent == "geography":
        preferred_tables = ["townland"]
    else:
        preferred_tables = ["unified_record", "townland", "census_record"]

    return {
        "primary_intent": primary_intent,
        "secondary_intents": secondary_intents,
        "output_mode": output_mode,
        "group_by": group_by,
        "year": year,
        "surname": surname,
        "townland_norm": hint,
        "scope": scope,
        "radius_km": radius_km,
        "preferred_tables": preferred_tables,
        "asks_people": asks_people,
        "asks_parish": asks_parish,
        "asks_population": asks_population,
        "asks_emigration": asks_emigration,
        "asks_eviction": asks_eviction,
        "asks_tenancy": asks_tenancy,
    }


def _same_parish_sql(question: str, analysis: dict[str, Any], townland_hint: str | None) -> str | None:
    """
    Deterministic SQL for "same parish as <townland>" questions.

    This bypasses the heavier relational enrichment path for questions that can
    be answered directly from SQLite's townland hierarchy.
    """
    q = (question or "").lower()
    hint = _norm_townland(townland_hint) or _norm_townland(analysis.get("townland_norm"))
    if not hint:
        return None
    if "same parish" not in q and "in the parish as" not in q:
        return None

    safe_hint = _sql_escape(hint)
    parish_subquery = (
        "SELECT civil_parish FROM townland "
        f"WHERE UPPER(name)='{safe_hint}' LIMIT 1"
    )

    if analysis.get("output_mode") in {"count", "aggregate"}:
        return f"""
SELECT COUNT(*) AS same_parish_townland_count
FROM townland
WHERE civil_parish = ({parish_subquery})
  AND UPPER(name) != '{safe_hint}'
""".strip()

    return f"""
SELECT name, civil_parish, barony, county
FROM townland
WHERE civil_parish = ({parish_subquery})
  AND UPPER(name) != '{safe_hint}'
ORDER BY name
LIMIT 200
""".strip()


def _analysis_prompt_block(analysis: dict[str, Any]) -> str:
    secondary = ", ".join(analysis.get("secondary_intents") or []) or "none"
    tables = ", ".join(analysis.get("preferred_tables") or [])
    lines = [
        "APP-INTERPRETED QUESTION PLAN:",
        f"- primary_intent: {analysis.get('primary_intent')}",
        f"- secondary_intents: {secondary}",
        f"- output_mode: {analysis.get('output_mode')}",
        f"- group_by: {analysis.get('group_by') or 'none'}",
        f"- scope: {analysis.get('scope')}",
        f"- townland_norm: {analysis.get('townland_norm') or 'none'}",
        f"- year: {analysis.get('year') or 'none'}",
        f"- surname: {analysis.get('surname') or 'none'}",
        f"- radius_km: {analysis.get('radius_km') or 'none'}",
        f"- preferred_tables: {tables}",
    ]
    return "\n".join(lines)


def _live_sqlite_schema_prompt_block() -> str:
    now = time.time()
    with _prompt_schema_cache_lock:
        cached = _PROMPT_SCHEMA_CACHE.get("value")
        if cached and float(_PROMPT_SCHEMA_CACHE.get("expires_at") or 0.0) > now:
            return str(cached)

    clear_col = _clearances_count_column()
    conn = get_db_conn()
    try:
        payload: dict[str, Any] = {
            "actual_clearances_metric_column": clear_col,
            "tables": {},
            "relationships": [
                "unified_record.townland_norm joins to UPPER(townland.name)",
                "census_record.townland_id joins to townland.id",
                f"clearances_record.townland_id joins to townland.id and uses {clear_col} as the eviction metric column",
            ],
            "query_rules": [
                "Use COUNT(DISTINCT unified_record.record_id) for people counts.",
                "Use census_record for population, inhabited houses, and uninhabited houses.",
                "Use unified_record for person lists, surnames, ships, departures, arrivals, roles, tenancy, emigration, and eviction person flags.",
                "Use unified_record.holding_acres for landholding analysis when acreage is requested.",
                "Use unified_record.is_widow, unified_record.children_count, and unified_record.family_size_estimate for widow/children/family-size questions when available.",
                "Use unified_record.is_canada_destination for Canada-focused emigration questions when available.",
                "Use heritage_feature for holy-well and ring-fort comparisons by townland.",
                "For townland filters, prefer unified_record.townland_norm='NAME' or UPPER(townland.name)='NAME'.",
                "For radius queries, use distance_km() with townland centroids.",
            ],
        }

        for table in ("unified_record", "townland", "census_record", "clearances_record", "heritage_feature"):
            pragma_rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
            if not pragma_rows:
                continue
            column_names = [str(row["name"]) for row in pragma_rows]
            payload["tables"][table] = {
                "row_count": conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"],
                "columns": [
                    f"{row['name']} {row['type'] or 'TEXT'}" + (" PRIMARY KEY" if row["pk"] else "")
                    for row in pragma_rows
                ],
            }

            category_examples = _prompt_category_examples(conn, table, column_names)
            if category_examples:
                payload["tables"][table]["categorical_examples"] = category_examples

            if table == "unified_record":
                flag_rows = conn.execute(
                    """
                    SELECT has_emigration_record, has_eviction_record, has_tenancy_record, COUNT(*) AS row_count
                    FROM unified_record
                    GROUP BY has_emigration_record, has_eviction_record, has_tenancy_record
                    ORDER BY row_count DESC
                    LIMIT 6
                    """
                ).fetchall()
                payload["tables"][table]["flag_combinations"] = [
                    (
                        f"emigration={row['has_emigration_record']}, "
                        f"eviction={row['has_eviction_record']}, "
                        f"tenancy={row['has_tenancy_record']} (rows={row['row_count']})"
                    )
                    for row in flag_rows
                ]
            elif table == "census_record":
                year_rows = conn.execute(
                    """
                    SELECT year, COUNT(*) AS townland_rows, SUM(total) AS estate_population
                    FROM census_record
                    GROUP BY year
                    ORDER BY year
                    LIMIT 12
                    """
                ).fetchall()
                payload["tables"][table]["year_summary"] = [
                    f"{row['year']} (rows={row['townland_rows']}, population={row['estate_population']})"
                    for row in year_rows
                ]
            elif table == "clearances_record":
                year_rows = conn.execute(
                    f"""
                    SELECT year, SUM({clear_col}) AS event_count
                    FROM clearances_record
                    GROUP BY year
                    ORDER BY year
                    LIMIT 20
                    """
                ).fetchall()
                payload["tables"][table]["year_summary"] = [
                    f"{row['year']} (events={row['event_count']})"
                    for row in year_rows
                ]
            elif table == "heritage_feature":
                group_rows = conn.execute(
                    """
                    SELECT feature_group, COUNT(*) AS feature_count, COUNT(DISTINCT townland_norm) AS townland_count
                    FROM heritage_feature
                    GROUP BY feature_group
                    ORDER BY feature_count DESC
                    """
                ).fetchall()
                payload["tables"][table]["group_summary"] = [
                    f"{row['feature_group']} (features={row['feature_count']}, townlands={row['townland_count']})"
                    for row in group_rows
                ]

        block = json.dumps(payload, ensure_ascii=False, default=str, indent=2)
    except Exception as exc:
        log.warning("ask_service.live_sqlite_schema_prompt_failed error=%s", exc)
        block = "{}"
    finally:
        conn.close()

    with _prompt_schema_cache_lock:
        _PROMPT_SCHEMA_CACHE["expires_at"] = now + _PROMPT_SCHEMA_CACHE_TTL
        _PROMPT_SCHEMA_CACHE["value"] = block
    return block


def _prompt_category_examples(
    conn,
    table: str,
    column_names: list[str],
    limit: int = 6,
) -> dict[str, list[str]]:
    available = set(column_names)
    out: dict[str, list[str]] = {}
    for column in _PROMPT_CATEGORY_COLUMNS.get(table, []):
        if column not in available:
            continue
        rows = conn.execute(
            f"""
            SELECT CAST({column} AS TEXT) AS value, COUNT(*) AS row_count
            FROM {table}
            WHERE {column} IS NOT NULL
              AND TRIM(CAST({column} AS TEXT)) <> ''
            GROUP BY {column}
            ORDER BY row_count DESC, value
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        examples = [
            f"{row['value']} (rows={row['row_count']})"
            for row in rows
            if row["value"] is not None
        ]
        if examples:
            out[column] = examples
    return out


def _database_profile_prompt_block() -> str:
    try:
        profile = _database_profile_context()
        compact = {
            "townland_count": profile.get("townland_count"),
            "parish_count": profile.get("parish_count"),
            "people_record_count": profile.get("people_record_count"),
            "emigrated_people": profile.get("emigrated_people"),
            "evicted_people": profile.get("evicted_people"),
            "tenant_people": profile.get("tenant_people"),
            "townlands_with_holy_wells": profile.get("townlands_with_holy_wells"),
            "townlands_with_ring_forts": profile.get("townlands_with_ring_forts"),
            "record_year_range": [profile.get("first_record_year"), profile.get("last_record_year")],
            "clearance_events": profile.get("clearance_events"),
            "top_townlands_by_people_records": profile.get("top_townlands_by_people_records", [])[:6],
            "top_surnames": profile.get("top_surnames", [])[:6],
        }
        return json.dumps(compact, ensure_ascii=False, default=str, indent=2)
    except Exception as exc:
        log.warning("ask_service.database_profile_prompt_failed error=%s", exc)
        return "{}"


def _match_and_build_template(
    question: str,
    canonical_townland: str | None,
) -> tuple[dict | None, str | None]:
    """
    Score every template against the question.
    Returns (template_dict, built_sql) for the best match, or (None, None).

    Minimum score threshold is 3 (= one required keyword × 2 + at least one
    optional match).  This prevents false positives where a single ambiguous
    substring (e.g. "ring" inside "during") matches a highly specific template.
    """
    q = question.lower()
    year = _extract_year(question)
    surname = _extract_surname(question)

    # ── Out-of-scope exclusions ────────────────────────────────────────────
    # Topics with no template coverage: return early so these questions reach
    # the LLM-fallback path rather than a semantically wrong template.
    _excluded_phrases = (
        "workhouse",
        "died of",
        "religion", "religious",
        "political",
        "approach",
        "average rent", "rent owed", "rent paid",
        "under the age",
        "children under",
        "other irish", "other estate",
        "weather", "climate",
        "crop", "farming",
        "entity resolution candidate",
    )
    if any(phrase in q for phrase in _excluded_phrases):
        return None, None
    # Widows-who-emigrated intersection: no template covers both conditions.
    if "widow" in q and "emigra" in q:
        return None, None

    best_tmpl: dict | None = None
    best_score: int = -1

    for tmpl in QUESTION_TEMPLATES:
        if tmpl.get("requires_townland") and not canonical_townland:
            continue
        if tmpl.get("requires_year") and not year:
            continue
        if tmpl.get("requires_surname") and not surname:
            continue

        required = tmpl.get("required_keywords", [])
        if required and not all(kw in q for kw in required):
            continue

        optional = tmpl.get("optional_keywords", [])
        score = len(required) * 2 + sum(1 for kw in optional if kw in q)

        if score > best_score:
            best_score = score
            best_tmpl = tmpl

    # Require score ≥ 2: at least one required keyword (2 pts), or a template
    # with no required keywords that matched at least two optional terms.
    # The out-of-scope exclusion guards above are the primary false-positive
    # defence; the score threshold catches residual substring coincidences
    # (e.g. "in" inside a proper noun, "estate" in a narrative question).
    if not best_tmpl or best_score < 2:
        return None, None

    # Substitute placeholders
    sql = best_tmpl["sql_template"]
    if "{townland_norm}" in sql:
        sql = sql.replace("{townland_norm}", _sql_escape(canonical_townland or ""))
    if "{year}" in sql:
        sql = sql.replace("{year}", str(year))
    if "{surname}" in sql:
        sql = sql.replace("{surname}", _sql_escape(surname or ""))

    return best_tmpl, sql


def _match_and_build_template_by_id(
    template_id: str,
    question: str,
    canonical_townland: str | None,
) -> tuple[dict | None, str | None]:
    """Build SQL for a specific template ID; returns (None, None) if entity requirements unmet."""
    year = _extract_year(question)
    surname = _extract_surname(question)
    for tmpl in QUESTION_TEMPLATES:
        if tmpl.get("id") != template_id:
            continue
        if tmpl.get("requires_townland") and not canonical_townland:
            return None, None
        if tmpl.get("requires_year") and not year:
            return None, None
        if tmpl.get("requires_surname") and not surname:
            return None, None
        sql = tmpl["sql_template"]
        if "{townland_norm}" in sql:
            sql = sql.replace("{townland_norm}", _sql_escape(canonical_townland or ""))
        if "{year}" in sql:
            sql = sql.replace("{year}", str(year))
        if "{surname}" in sql:
            sql = sql.replace("{surname}", _sql_escape(surname or ""))
        return tmpl, sql
    return None, None


def _phase4_retrieve(
    question: str,
    canonical_townland: str | None,
    approved_memory: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """
    Phase 4 hybrid retrieval: dense TF-IDF cosine + sparse keyword overlap → RRF.

    Returns:
      (template_fast_lane, embedding_ranked_memory)

    template_fast_lane: dict with keys 'template', 'sql', 'cosine_score',
      'rrf_score', 'template_id' when a high-confidence template hit exists;
      None otherwise.

    embedding_ranked_memory: approved_memory rows re-ranked by embedding
      similarity, with 'match_score' set for downstream _approved_query_examples_block.
    """
    try:
        from backend.services.embedding_index import (
            get_index,
            TEMPLATE_FAST_LANE_THRESHOLD,
        )
        try:
            from backend.services.semantic_layer import METRIC_REGISTRY as _metrics
        except Exception:
            _metrics: dict[str, Any] = {}

        hits = get_index().retrieve(
            question,
            top_k=12,
            templates=QUESTION_TEMPLATES,
            metrics=_metrics,
            memory_rows=approved_memory,
        )

        # ── Template fast lane ────────────────────────────────────────────────
        template_fast_lane: dict[str, Any] | None = None
        q_lower = question.lower()
        for hit in hits:
            if hit.source not in ("template", "metric"):
                continue
            if hit.cosine_score < TEMPLATE_FAST_LANE_THRESHOLD:
                continue
            # required_keywords is the HARD gate — must all be present
            if hit.required_keywords and not all(kw in q_lower for kw in hit.required_keywords):
                continue
            if hit.source == "template":
                tmpl, tmpl_sql = _match_and_build_template_by_id(hit.key, question, canonical_townland)
                if tmpl and tmpl_sql:
                    template_fast_lane = {
                        "template": tmpl,
                        "sql": tmpl_sql,
                        "template_id": hit.key,
                        "cosine_score": hit.cosine_score,
                        "rrf_score": hit.rrf_score,
                        "description": tmpl.get("description"),
                    }
                    break

        # ── Re-rank memory rows by embedding similarity ───────────────────────
        memory_scores: dict[str, tuple[float, float]] = {
            hit.key: (hit.cosine_score, hit.rrf_score)
            for hit in hits
            if hit.source == "memory"
        }
        ranked: list[dict[str, Any]] = []
        unranked: list[dict[str, Any]] = []
        for row in approved_memory:
            rid = str(row.get("id") or "")
            if rid in memory_scores:
                cos, rrf = memory_scores[rid]
                item = dict(row)
                item["match_score"] = round(cos * 100.0, 2)
                item["_p4_rrf"] = rrf
                ranked.append(item)
            else:
                unranked.append(dict(row))
        ranked.sort(key=lambda r: r.get("_p4_rrf", 0.0), reverse=True)

        return template_fast_lane, ranked + unranked

    except Exception as exc:
        log.debug("ask_service.phase4_retrieve_failed error=%s", exc)
        return None, approved_memory


# ─────────────────────────────────────────────────────────────────────────────
# Verified analysis layer
# ─────────────────────────────────────────────────────────────────────────────

def _try_verified_analysis(
    question: str,
    canonical_townland: str | None,
    analysis: dict[str, Any],
) -> dict[str, Any] | None:
    q_lo = (question or "").lower()
    # Guard: topics with no verified-analysis coverage must not be forced onto
    # a curated SQL path.  Return None so the question falls through to
    # template matching (which has its own exclusions) or the LLM fallback.
    _va_excluded = (
        "workhouse", "died of", "religion", "political",
        "approach",
        "average rent", "rent owed", "rent paid",
        "other irish", "other estate", "weather", "climate",
        "crop", "farming", "under the age", "children under",
    )
    if any(phrase in q_lo for phrase in _va_excluded):
        return None
    if "widow" in q_lo and "emigra" in q_lo:
        return None

    surname = analysis.get("surname")
    if surname and analysis.get("primary_intent") == "people":
        if analysis.get("output_mode") == "count":
            sql = f"SELECT COUNT(DISTINCT record_id) AS matching_people FROM unified_record WHERE UPPER(surname)='{_sql_escape(str(surname))}'"
            return {
                "sql": sql,
                "meta": {
                    "provider": "verified_analysis",
                    "model": "curated_sql",
                    "mode": "verified_analysis",
                    "analysis_id": "people_named_surname_count",
                    "description": "Verified surname count query",
                },
                "chart_hint": None,
            }
        if analysis.get("output_mode") == "list":
            sql = (
                "SELECT DISTINCT "
                "COALESCE(NULLIF(TRIM(canonical_name),''),TRIM(COALESCE(forename,'')||' '||COALESCE(surname,''))) AS person_name,"
                "surname,forename,townland,parish,year,has_emigration_record,has_eviction_record,has_tenancy_record "
                f"FROM unified_record WHERE UPPER(surname)='{_sql_escape(str(surname))}' "
                "ORDER BY year, person_name LIMIT 200"
            )
            return {
                "sql": sql,
                "meta": {
                    "provider": "verified_analysis",
                    "model": "curated_sql",
                    "mode": "verified_analysis",
                    "analysis_id": "people_named_surname_list",
                    "description": "Verified surname list query",
                },
                "chart_hint": None,
            }

    tmpl, tmpl_sql = _match_and_build_template(question, canonical_townland)
    if tmpl and tmpl_sql and tmpl.get("id") in VERIFIED_ANALYSIS_TEMPLATE_IDS:
        template_id = str(tmpl.get("id"))
        return {
            "sql": tmpl_sql,
            "meta": {
                "provider": "verified_analysis",
                "model": "curated_sql",
                "mode": "verified_analysis",
                "analysis_id": template_id,
                "description": tmpl.get("description"),
            },
            "chart_hint": VERIFIED_ANALYSIS_CHART_HINTS.get(template_id),
        }
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Approved query memory + feedback
# ─────────────────────────────────────────────────────────────────────────────

def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _question_signature(question: str) -> str:
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]{1,}", (question or "").lower())
    stopwords = {
        "the", "a", "an", "of", "for", "to", "in", "on", "at", "by", "from",
        "this", "that", "these", "those", "is", "are", "was", "were", "be",
        "do", "does", "did", "what", "which", "who", "how", "many", "much",
        "there", "any", "all", "with", "and", "or", "than", "then", "into",
        "about", "around", "within", "across", "over", "under", "show", "list",
        "tell", "me", "please", "records", "record",
    }
    cleaned = [token for token in tokens if token not in stopwords]
    if not cleaned:
        cleaned = tokens[:8]
    return " ".join(cleaned[:18]).strip()


def _ensure_query_memory_schema() -> None:
    conn = get_db_conn()
    try:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {QUERY_MEMORY_TABLE} (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              question_text TEXT NOT NULL,
              question_signature TEXT NOT NULL,
              townland_norm TEXT,
              analysis_json TEXT,
              sql_text TEXT NOT NULL,
              vrti_postgres_sql TEXT,
              sample_answer TEXT,
              summary_json TEXT,
              source_mode TEXT,
              llm_provider TEXT,
              llm_model TEXT,
              approved_count INTEGER NOT NULL DEFAULT 0,
              rejected_count INTEGER NOT NULL DEFAULT 0,
              reuse_count INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              last_approved_at TEXT,
              last_used_at TEXT,
              feedback_note TEXT
            )
            """
        )
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {QUERY_FEEDBACK_TABLE} (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              question_text TEXT NOT NULL,
              question_signature TEXT NOT NULL,
              townland_hint TEXT,
              townland_norm TEXT,
              sql_text TEXT,
              vrti_postgres_sql TEXT,
              feedback TEXT NOT NULL,
              note TEXT,
              result_row_count INTEGER,
              availability_state TEXT,
              llm_provider TEXT,
              llm_model TEXT,
              llm_mode TEXT,
              reused_memory_id INTEGER,
              created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{QUERY_MEMORY_TABLE}_signature ON {QUERY_MEMORY_TABLE}(question_signature)"
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{QUERY_MEMORY_TABLE}_townland ON {QUERY_MEMORY_TABLE}(townland_norm)"
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{QUERY_FEEDBACK_TABLE}_signature ON {QUERY_FEEDBACK_TABLE}(question_signature)"
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{QUERY_FEEDBACK_TABLE}_created_at ON {QUERY_FEEDBACK_TABLE}(created_at)"
        )
        conn.commit()
    finally:
        conn.close()


def _clear_query_memory_cache() -> None:
    with _query_memory_cache_lock:
        _QUERY_MEMORY_CACHE["expires_at"] = 0.0
        _QUERY_MEMORY_CACHE["rows"] = []


def _load_approved_query_memory() -> list[dict[str, Any]]:
    now = time.time()
    with _query_memory_cache_lock:
        cached_rows = _QUERY_MEMORY_CACHE.get("rows") or []
        if cached_rows and now < float(_QUERY_MEMORY_CACHE.get("expires_at") or 0):
            return [dict(row) for row in cached_rows]

    _ensure_query_memory_schema()
    conn = get_db_conn()
    try:
        rows = [
            dict(row) for row in conn.execute(
                f"""
                SELECT *
                FROM {QUERY_MEMORY_TABLE}
                WHERE approved_count > rejected_count
                ORDER BY approved_count DESC, updated_at DESC, id DESC
                LIMIT 250
                """
            ).fetchall()
        ]
    finally:
        conn.close()

    with _query_memory_cache_lock:
        _QUERY_MEMORY_CACHE["rows"] = [dict(row) for row in rows]
        _QUERY_MEMORY_CACHE["expires_at"] = time.time() + _QUERY_MEMORY_CACHE_TTL
    return rows


def _memory_similarity_score(
    question: str,
    analysis: dict[str, Any],
    townland_norm: str | None,
    candidate: dict[str, Any],
) -> float:
    source = _question_signature(question)
    target = candidate.get("question_signature") or _question_signature(candidate.get("question_text") or "")
    if fuzz is not None:
        base = float(fuzz.token_sort_ratio(source, target))
    else:
        base = difflib.SequenceMatcher(None, source, target).ratio() * 100.0

    candidate_analysis: dict[str, Any] = {}
    try:
        candidate_analysis = json.loads(candidate.get("analysis_json") or "{}")
    except Exception:
        candidate_analysis = {}

    score = base
    if candidate_analysis.get("primary_intent") == analysis.get("primary_intent"):
        score += 8
    if candidate_analysis.get("output_mode") == analysis.get("output_mode"):
        score += 5
    if candidate_analysis.get("group_by") == analysis.get("group_by"):
        score += 3
    if candidate_analysis.get("year") and candidate_analysis.get("year") == analysis.get("year"):
        score += 4

    candidate_townland = _norm_townland(candidate.get("townland_norm"))
    if townland_norm and candidate_townland:
        if townland_norm == candidate_townland:
            score += 10
        else:
            score -= 16
    elif townland_norm or candidate_townland:
        score -= 4

    approvals = int(candidate.get("approved_count") or 0)
    rejections = int(candidate.get("rejected_count") or 0)
    score += min(6.0, approvals * 1.5)
    score -= min(8.0, rejections * 2.0)
    return round(max(0.0, min(score, 100.0)), 2)


def _find_similar_approved_queries(
    question: str,
    analysis: dict[str, Any],
    townland_norm: str | None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for row in _load_approved_query_memory():
        score = _memory_similarity_score(question, analysis, townland_norm, row)
        if score < 55:
            continue
        item = dict(row)
        item["match_score"] = score
        matches.append(item)
    matches.sort(
        key=lambda row: (
            float(row.get("match_score") or 0.0),
            int(row.get("approved_count") or 0),
            -int(row.get("rejected_count") or 0),
            row.get("updated_at") or "",
        ),
        reverse=True,
    )
    return matches[:limit]


def _can_reuse_memory_directly(
    question: str,
    analysis: dict[str, Any],
    townland_norm: str | None,
    match: dict[str, Any] | None,
) -> bool:
    if not match:
        return False
    score = float(match.get("match_score") or 0.0)
    if score < 92:
        return False
    candidate_townland = _norm_townland(match.get("townland_norm"))
    if townland_norm and candidate_townland and townland_norm != candidate_townland:
        return False
    candidate_analysis: dict[str, Any] = {}
    try:
        candidate_analysis = json.loads(match.get("analysis_json") or "{}")
    except Exception:
        candidate_analysis = {}
    return (
        candidate_analysis.get("primary_intent") == analysis.get("primary_intent")
        and candidate_analysis.get("output_mode") == analysis.get("output_mode")
    )


def _approved_query_examples_block(matches: list[dict[str, Any]]) -> str:
    if not matches:
        return "No previously approved queries matched this question closely."
    lines = ["Approved user-validated SQL examples for similar questions:"]
    for row in matches[:3]:
        lines.extend([
            f"- similarity_score: {row.get('match_score')}",
            f"  question: {row.get('question_text')}",
            f"  townland_scope: {row.get('townland_norm') or 'global'}",
            f"  approvals: {row.get('approved_count') or 0}",
            "  SQL:",
            *[f"    {line}" for line in str(row.get("sql_text") or "").splitlines()],
        ])
    lines.append("Reuse the structure only if it truly matches the new question.")
    return "\n".join(lines)


def _mark_query_memory_used(memory_id: int | None) -> None:
    if not memory_id:
        return
    _ensure_query_memory_schema()
    conn = get_db_conn()
    try:
        conn.execute(
            f"""
            UPDATE {QUERY_MEMORY_TABLE}
            SET reuse_count = COALESCE(reuse_count, 0) + 1,
                last_used_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (_utcnow_iso(), _utcnow_iso(), memory_id),
        )
        conn.commit()
    finally:
        conn.close()
    _clear_query_memory_cache()


def record_query_feedback(
    *,
    question: str,
    townland_hint: str | None,
    sql_text: str | None,
    vrti_postgres_sql: str | None,
    feedback: str,
    note: str | None,
    result_row_count: int,
    availability_state: str | None,
    llm_meta: dict[str, Any] | None,
    reused_memory_id: int | None,
    sample_answer: str | None,
    summary_json: dict[str, Any] | None,
) -> dict[str, Any]:
    feedback_value = (feedback or "").strip().lower()
    if feedback_value not in {"up", "down"}:
        raise ValueError("feedback must be 'up' or 'down'.")

    _ensure_query_memory_schema()
    analysis = _analyse_question(question, townland_hint)
    question_signature = _question_signature(question)
    townland_norm = _norm_townland(townland_hint)
    now = _utcnow_iso()
    clean_sql = _sanitize_and_validate_sql(sql_text) if sql_text else None
    llm_meta = llm_meta or {}

    conn = get_db_conn()
    try:
        conn.execute(
            f"""
            INSERT INTO {QUERY_FEEDBACK_TABLE} (
              question_text, question_signature, townland_hint, townland_norm,
              sql_text, vrti_postgres_sql, feedback, note, result_row_count,
              availability_state, llm_provider, llm_model, llm_mode,
              reused_memory_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                question,
                question_signature,
                townland_hint,
                townland_norm,
                clean_sql,
                vrti_postgres_sql,
                feedback_value,
                (note or "").strip() or None,
                int(result_row_count or 0),
                availability_state,
                llm_meta.get("provider"),
                llm_meta.get("model"),
                llm_meta.get("mode"),
                reused_memory_id,
                now,
            ),
        )

        stored_in_memory = False
        memory_id = reused_memory_id
        if feedback_value == "up" and clean_sql:
            if memory_id:
                conn.execute(
                    f"""
                    UPDATE {QUERY_MEMORY_TABLE}
                    SET approved_count = COALESCE(approved_count, 0) + 1,
                        feedback_note = COALESCE(?, feedback_note),
                        sample_answer = COALESCE(?, sample_answer),
                        summary_json = COALESCE(?, summary_json),
                        source_mode = ?,
                        llm_provider = ?,
                        llm_model = ?,
                        sql_text = ?,
                        vrti_postgres_sql = COALESCE(?, vrti_postgres_sql),
                        last_approved_at = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        (note or "").strip() or None,
                        sample_answer,
                        json.dumps(summary_json or {}, ensure_ascii=True),
                        llm_meta.get("mode"),
                        llm_meta.get("provider"),
                        llm_meta.get("model"),
                        clean_sql,
                        vrti_postgres_sql,
                        now,
                        now,
                        memory_id,
                    ),
                )
            else:
                existing = conn.execute(
                    f"""
                    SELECT id
                    FROM {QUERY_MEMORY_TABLE}
                    WHERE question_signature = ? AND COALESCE(townland_norm, '') = COALESCE(?, '')
                    ORDER BY approved_count DESC, id DESC
                    LIMIT 1
                    """,
                    (question_signature, townland_norm),
                ).fetchone()
                if existing:
                    memory_id = int(existing["id"])
                    conn.execute(
                        f"""
                        UPDATE {QUERY_MEMORY_TABLE}
                        SET approved_count = COALESCE(approved_count, 0) + 1,
                            question_text = ?,
                            analysis_json = ?,
                            sql_text = ?,
                            vrti_postgres_sql = ?,
                            feedback_note = COALESCE(?, feedback_note),
                            sample_answer = COALESCE(?, sample_answer),
                            summary_json = COALESCE(?, summary_json),
                            source_mode = ?,
                            llm_provider = ?,
                            llm_model = ?,
                            last_approved_at = ?,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            question,
                            json.dumps(analysis, ensure_ascii=True),
                            clean_sql,
                            vrti_postgres_sql,
                            (note or "").strip() or None,
                            sample_answer,
                            json.dumps(summary_json or {}, ensure_ascii=True),
                            llm_meta.get("mode"),
                            llm_meta.get("provider"),
                            llm_meta.get("model"),
                            now,
                            now,
                            memory_id,
                        ),
                    )
                else:
                    cur = conn.execute(
                        f"""
                        INSERT INTO {QUERY_MEMORY_TABLE} (
                          question_text, question_signature, townland_norm, analysis_json,
                          sql_text, vrti_postgres_sql, sample_answer, summary_json,
                          source_mode, llm_provider, llm_model,
                          approved_count, rejected_count, reuse_count,
                          created_at, updated_at, last_approved_at, feedback_note
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, 0, ?, ?, ?, ?)
                        """,
                        (
                            question,
                            question_signature,
                            townland_norm,
                            json.dumps(analysis, ensure_ascii=True),
                            clean_sql,
                            vrti_postgres_sql,
                            sample_answer,
                            json.dumps(summary_json or {}, ensure_ascii=True),
                            llm_meta.get("mode"),
                            llm_meta.get("provider"),
                            llm_meta.get("model"),
                            now,
                            now,
                            now,
                            (note or "").strip() or None,
                        ),
                    )
                    memory_id = int(cur.lastrowid or 0)
            stored_in_memory = bool(memory_id)
        elif feedback_value == "down" and reused_memory_id:
            conn.execute(
                f"""
                UPDATE {QUERY_MEMORY_TABLE}
                SET rejected_count = COALESCE(rejected_count, 0) + 1,
                    updated_at = ?
                WHERE id = ?
                """,
                (now, reused_memory_id),
            )

        conn.commit()
    finally:
        conn.close()

    _clear_query_memory_cache()
    try:
        from backend.services.embedding_index import get_index as _get_embed_index
        _get_embed_index().invalidate_memory()
    except Exception:
        pass
    return {
        "ok": True,
        "feedback": feedback_value,
        "stored_in_memory": stored_in_memory,
        "memory_id": memory_id,
    }


def _memory_matches_for_display(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in matches[:5]:
        out.append({
            "id": row.get("id"),
            "question_text": row.get("question_text"),
            "townland_norm": row.get("townland_norm"),
            "match_score": row.get("match_score"),
            "approved_count": row.get("approved_count"),
            "rejected_count": row.get("rejected_count"),
            "source_mode": row.get("source_mode"),
        })
    return out


# ─────────────────────────────────────────────────────────────────────────────
# SSE streaming entry point
# ─────────────────────────────────────────────────────────────────────────────

def _sse(type_: str, **kw: Any) -> str:
    return f"data: {json.dumps({'type': type_, **kw})}\n\n"


def _extract_tables(sql: str) -> list[str]:
    """Return deduplicated table names referenced in a SQL query."""
    raw = re.findall(r'(?:FROM|JOIN)\s+([a-z_][a-z_0-9]*)', sql, re.IGNORECASE)
    return list(dict.fromkeys(t.lower() for t in raw))


def answer_question(
    question: str,
    townland_hint: str | None = None,
    include_sql: bool = False,
    force_llm: bool = False,
) -> dict[str, Any]:
    """
    Compatibility wrapper for non-stream consumers.
    Executes the streaming pipeline and returns the final result payload.
    """
    final_payload: dict[str, Any] | None = None
    for raw in answer_question_stream(
        question=question,
        townland_hint=townland_hint,
        include_sql=include_sql,
        force_llm=force_llm,
    ):
        if not raw.startswith("data:"):
            continue
        try:
            evt = json.loads(raw[5:].strip())
        except Exception:
            continue
        if evt.get("type") == "error":
            raise RuntimeError(evt.get("message") or "Ask pipeline failed.")
        if evt.get("type") == "result":
            final_payload = evt

    if not final_payload:
        raise RuntimeError("No final result produced by Ask pipeline.")
    final_payload.pop("type", None)
    return final_payload


def _orchestrated_pipeline_stream(
    question: str,
    townland_hint: str | None,
    include_sql: bool,
    force_llm: bool,
) -> Generator[str, None, None]:
    """
    New routed pipeline — activated by ASK_USE_NEW_PIPELINE=true.

    Orchestrator flow (per STEP 2 spec):
      1. Phase 1 — entity_resolver (via _resolve_townland_context) runs ONCE; the
         resolved sql_id + kg_uri are shared by every downstream lane.
      2. Phase 5 — classify_intent → ANALYTICAL / RELATIONAL / COMPARATIVE / FALLBACK.
      3. Dispatch:
           ANALYTICAL  → Phase 2 semantic_layer: rule-based slot-fill first (0 LLM calls),
                         then LLM slot-fill if confidence < threshold.  SQL comes from
                         the deterministic compiler — never from free-form LLM generation.
           RELATIONAL  → Phase 3 subgraph retrieval for qualitative context, then
           /HERITAGE      FALLBACK SQL generation for any counts (Core Rule 1: counts
                         always come from SQL, never from the subgraph).
           COMPARATIVE → ANALYTICAL SQL + RELATIONAL subgraph side-by-side.
                         Full reconciliation is Phase 6 (TODO marker in provenance).
           FALLBACK    → old pipeline (verified_analysis → phase4_embedding →
                         approved_memory → LLM free-form SQL).
      4. ANY lane error → graceful fallback to old pipeline SQL generation.
      5. Stages 2-end — safety check, DB execution, VRTI, GraphDB, fusion, rewrite, PDF,
         final result SSE — identical to the old pipeline; call the same helpers.

    New SSE stages vs old pipeline:
      classifying_intent — routing decision (always emitted)
      slot_filling       — LLM slot-fill attempt (ANALYTICAL lane only)

    New provenance fields in result payload:
      query_provenance.route   — intent_route label
      query_provenance.lane    — which dispatch path executed
    """
    # ── Setup ─────────────────────────────────────────────────────────────────
    try:
        _ensure_unified_table_seeded()
        _ensure_heritage_feature_seeded()
        _ensure_query_memory_schema()
    except Exception as exc:
        yield _sse("error", message=f"Database not ready: {exc}")
        return

    # ── Phase 1: Entity resolution ────────────────────────────────────────────
    # entity_resolver is called exactly once, inside _townland_resolution_payload,
    # and enriches the returned dict with sql_id + kg_uri so every downstream lane
    # references the same resolved entity without re-deriving it.
    yield _sse(
        "progress", stage="resolving_identity", status="started",
        label="Resolving Entities", detail="Identifying entities in the question…",
    )
    townland_resolution = _resolve_townland_context(question, townland_hint)
    canonical_townland = townland_resolution.get("name_norm")
    analysis = _analyse_question(question, canonical_townland or townland_hint)
    warnings: list[str] = []
    if townland_resolution.get("warning"):
        warnings.append(str(townland_resolution["warning"]))
    warnings.extend(_question_data_coverage_warnings(question))

    # Part A: Person identity resolution for surname questions
    _person_identity_result: dict[str, Any] = {}
    if analysis.get("surname"):
        try:
            from backend.services.identity_resolver import resolve_person_identity as _rpi
            _pir = _rpi(analysis["surname"], townland_norm=canonical_townland)
            _person_identity_result = {
                "raw_name": _pir.raw_name,
                "total_mentions": _pir.total_mentions,
                "is_ambiguous": _pir.is_ambiguous,
                "disambiguation_note": _pir.disambiguation_note,
                "person_candidates": [
                    {
                        "person_id": c.person_id,
                        "display_name": c.display_name,
                        "confidence": c.confidence,
                        "supporting_record_count": len(c.supporting_mention_ids),
                        "may_be_confused_with": c.may_be_confused_with,
                        "townland_norm": c.townland_norm,
                        "year_range": list(c.year_range) if c.year_range else None,
                    }
                    for c in _pir.person_candidates
                ],
            }
            if _pir.is_ambiguous and _pir.disambiguation_note:
                warnings.append(_pir.disambiguation_note)
        except Exception as _pir_exc:
            log.debug("orchestrated_pipeline.person_identity_failed error=%s", _pir_exc)

    yield _sse(
        "progress", stage="resolving_identity", status="completed",
        label="Resolving Entities",
        detail=(
            f"Townland: {canonical_townland or 'not found'}"
            + (
                f" · Person: {_person_identity_result.get('total_mentions', 0)} mention(s)"
                + (" [ambiguous]" if _person_identity_result.get("is_ambiguous") else "")
                if _person_identity_result else ""
            )
        ),
    )

    # ── Phase 5: Intent classification ───────────────────────────────────────
    yield _sse(
        "progress", stage="classifying_intent", status="started",
        label="Routing Question", detail="Classifying question intent…",
    )
    _semantic_slot_fill = None
    try:
        from backend.services.semantic_layer import try_rule_based_fill as _try_sl_fill
        _semantic_slot_fill = _try_sl_fill(question, analysis, townland_resolution)
    except Exception as _sl_init_exc:
        log.debug("orchestrated_pipeline.sl_init_failed error=%s", _sl_init_exc)

    intent_route = "fallback"
    try:
        from backend.services.intent_router import (
            classify_intent as _classify_intent,
            ANALYTICAL as _ANALYTICAL,
            RELATIONAL as _RELATIONAL,
            COMPARATIVE as _COMPARATIVE,
        )
        intent_route = _classify_intent(question, analysis, _semantic_slot_fill)
    except Exception as _ir_exc:
        log.warning("orchestrated_pipeline.intent_router_failed error=%s", _ir_exc)
        _ANALYTICAL = "analytical"
        _RELATIONAL = "relational"
        _COMPARATIVE = "comparative"

    yield _sse(
        "progress", stage="classifying_intent", status="completed",
        label="Routing Question", detail=f"Route: {intent_route}",
    )

    # ── SQL generation (stage 1) ──────────────────────────────────────────────
    t0 = time.perf_counter()
    sql: str = ""
    llm_meta: dict[str, Any] = {}
    vrti_postgres_sql: str = ""
    vrti_query_meta: dict[str, Any] = {}
    chart_hint: str | None = None
    _phase3_result = None
    _graphrag_result = None  # In-process GraphRAG enrichment (flow.md §5)
    _chunk_context: list[dict[str, Any]] = []   # Part C — set later
    # approved_matches needed by _execute_with_recovery and memory marking
    approved_matches: list[dict[str, Any]] = []
    direct_memory_match: dict[str, Any] | None = None
    query_provenance: dict[str, Any] = {
        "used_approved_memory": False,
        "reused_memory_id": None,
        "direct_memory_reuse": False,
        "execution_mode": "executed_as_generated",
        "strategy": "new_pipeline",
        "route": intent_route,
        "approved_query_candidates": [],
        "new_pipeline": True,
    }

    yield _sse(
        "progress", stage="contacting_llm", status="started",
        label="Building Query", detail=f"Route: {intent_route} — preparing…",
    )

    _routed_sql_ok = False

    # ── ANALYTICAL lane (Phase 2: semantic_layer → deterministic SQL) ──────────
    if intent_route == _ANALYTICAL and not force_llm:
        try:
            from backend.services.semantic_layer import (
                compile_sql as _compile_sl,
                slot_fill_meta as _slot_fill_meta,
                build_slot_fill_prompt as _build_slot_fill_prompt,
                parse_slot_fill as _parse_slot_fill,
            )
            sf = _semantic_slot_fill
            if sf and sf.confidence >= 0.80:
                _compiled = _compile_sl(sf, _clearances_count_column())
                if _compiled:
                    sql = _compiled
                    llm_meta = _slot_fill_meta(sf, sql)
                    query_provenance.update({"strategy": "semantic_layer_rule", "lane": "analytical_rule"})
                    _routed_sql_ok = True
                    ms = int((time.perf_counter() - t0) * 1000)
                    yield _sse(
                        "progress", stage="contacting_llm", status="completed",
                        label="Building Query",
                        detail=f"Semantic rule [{sf.metric}] confidence={sf.confidence:.2f}",
                        duration_ms=ms,
                    )

            if not _routed_sql_ok:
                # LLM slot-fill fallback within ANALYTICAL lane
                yield _sse(
                    "progress", stage="slot_filling", status="started",
                    label="Slot Filling", detail="LLM slot-fill for analytical query…",
                )
                try:
                    _sf_prompt = _build_slot_fill_prompt(question, analysis, townland_resolution)
                    _sf_raw, _sf_meta = _llm_generate(
                        _sf_prompt, purpose="slot_fill", max_tokens=256, temperature=0.0
                    )
                    _sf_parsed = _parse_slot_fill(_sf_raw, question)
                    if _sf_parsed and _sf_parsed.confidence >= 0.70:
                        _compiled2 = _compile_sl(_sf_parsed, _clearances_count_column())
                        if _compiled2:
                            sql = _compiled2
                            llm_meta = _slot_fill_meta(_sf_parsed, sql)
                            llm_meta["llm_provider"] = _sf_meta.get("provider")
                            llm_meta["llm_model"] = _sf_meta.get("model")
                            query_provenance.update({"strategy": "semantic_layer_llm", "lane": "analytical_llm"})
                            _routed_sql_ok = True
                            ms = int((time.perf_counter() - t0) * 1000)
                            yield _sse(
                                "progress", stage="slot_filling", status="completed",
                                label="Slot Filling",
                                detail=f"LLM slot-fill [{_sf_parsed.metric}] confidence={_sf_parsed.confidence:.2f}",
                                duration_ms=ms,
                            )
                        else:
                            yield _sse("progress", stage="slot_filling", status="completed",
                                       label="Slot Filling", detail="Compile failed — falling back")
                    else:
                        yield _sse("progress", stage="slot_filling", status="completed",
                                   label="Slot Filling", detail="Low confidence — falling back to old pipeline")
                except Exception as _sf_exc:
                    log.debug("orchestrated_pipeline.llm_slot_fill_failed error=%s", _sf_exc)
                    yield _sse("progress", stage="slot_filling", status="completed",
                               label="Slot Filling", detail=f"Slot-fill error — falling back")
        except Exception as _al_exc:
            log.warning("orchestrated_pipeline.analytical_lane_failed error=%s", _al_exc)

    # ── RELATIONAL / COMPARATIVE — Phase 3 subgraph retrieval ─────────────────
    # Core Rule 1: subgraph provides qualitative context only; counts still come from SQL.
    if intent_route in (_RELATIONAL, _COMPARATIVE):
        try:
            from backend.services.subgraph_engine import retrieve_subgraph as _retrieve_subgraph
            _sg_t0 = time.perf_counter()
            yield _sse(
                "progress", stage="querying_subgraph", status="started",
                label="Subgraph Retrieval",
                detail="Expanding knowledge graph neighbourhood for relational context…",
            )
            _phase3_result = _retrieve_subgraph(
                question, analysis, townland_resolution, sources=("vrti", "graphdb")
            )
            _sg_ms = int((time.perf_counter() - _sg_t0) * 1000)
            _sg_srcs = ", ".join(_phase3_result.sources_used) if _phase3_result.sources_used else "none"
            _sg_nodes = (
                len(_phase3_result.triples_vrti) + len(_phase3_result.triples_graphdb)
            )
            yield _sse(
                "progress", stage="querying_subgraph", status="completed",
                label="Subgraph Retrieval",
                detail=(
                    f"{_sg_nodes} triples · sources: {_sg_srcs} · "
                    f"type: {_phase3_result.question_type}"
                    + (", pruned" if _phase3_result.pruned else "")
                ),
                duration_ms=_sg_ms,
            )
            query_provenance["subgraph_node_count"] = _sg_nodes
            query_provenance["subgraph_sources"] = _phase3_result.sources_used
            if intent_route == _COMPARATIVE:
                # TODO: Phase 6 — full metric reconciliation between SQLite and KG.
                # For now return SQL result + subgraph context side-by-side.
                query_provenance["phase6_todo"] = (
                    "Full cross-source reconciliation pending Phase 6 implementation."
                )
        except Exception as _sg_exc:
            log.warning("orchestrated_pipeline.subgraph_failed error=%s", _sg_exc)

    # ── In-process GraphRAG enrichment (flow.md §5) ───────────────────────────
    # Runs for RELATIONAL, COMPARATIVE, and FALLBACK intents.
    # Graph results are corroboration only; counts still come from SQL.
    # Graceful degradation: if graph not built, skip with a note.
    if intent_route in (_RELATIONAL, _COMPARATIVE, "fallback"):
        try:
            from backend.services.graphrag import (
                is_available as _graphrag_available,
                retrieve_subgraph as _graphrag_retrieve,
            )
            if _graphrag_available():
                _gr_t0 = time.perf_counter()
                yield _sse(
                    "progress", stage="querying_graphrag", status="started",
                    label="GraphRAG",
                    detail="Vector seed + k-hop traversal over in-process property graph…",
                )
                _entity_hints: dict[str, Any] = {}
                if canonical_townland:
                    _entity_hints["canonical_townland"] = canonical_townland
                if analysis.get("surname"):
                    _entity_hints["surname"] = analysis["surname"]
                _graphrag_result = _graphrag_retrieve(
                    question,
                    intent=intent_route or "relational",
                    entity_hints=_entity_hints,
                )
                _gr_ms = int((time.perf_counter() - _gr_t0) * 1000)
                _gr_seed_n = len(_graphrag_result.seed_nodes)
                _gr_rel_n = len(_graphrag_result.subgraph_rels)
                yield _sse(
                    "progress", stage="querying_graphrag", status="completed",
                    label="GraphRAG",
                    detail=(
                        f"{_gr_seed_n} seed nodes · {_gr_rel_n} triples · "
                        f"k={_graphrag_result.k_hops}"
                        + (", pruned" if _graphrag_result.pruned else "")
                        + (f" · {len(_graphrag_result.community_summaries)} communities" if _graphrag_result.community_summaries else "")
                    ),
                    duration_ms=_gr_ms,
                )
                query_provenance["graphrag"] = {
                    "available": True,
                    "seed_count": _gr_seed_n,
                    "triple_count": _gr_rel_n,
                    "k_hops": _graphrag_result.k_hops,
                }
        except Exception as _gr_exc:
            log.warning("orchestrated_pipeline.graphrag_failed error=%s", _gr_exc)
            query_provenance["graphrag"] = {"available": False, "error": str(_gr_exc)}

    # ── Part C: Vector recall over verbalised chunks (RELATIONAL / COMPARATIVE / FALLBACK) ──
    _chunk_context: list[dict[str, Any]] = []
    if intent_route in (_RELATIONAL, _COMPARATIVE, "fallback"):
        try:
            yield _sse(
                "progress", stage="retrieving_vectors", status="started",
                label="Vector Recall", detail="Embedding question and querying dense retrieval backend…",
            )
            _vt0 = time.perf_counter()
            from backend.services.embedding_index import retrieve_chunks_with_meta as _retrieve_chunks
            _chunk_context, _chunk_meta = _retrieve_chunks(question, top_k=6)
            _vms = int((time.perf_counter() - _vt0) * 1000)
            query_provenance["vector_retrieval"] = dict(_chunk_meta)
            yield _sse(
                "progress", stage="retrieving_vectors", status="completed",
                label="Vector Recall",
                detail=(
                    f"dense={_chunk_meta.get('dense_backend')} "
                    f"status={_chunk_meta.get('dense_status')} "
                    f"chunks={_chunk_meta.get('dense_count')}"
                ),
                duration_ms=_vms,
            )
            yield _sse(
                "progress", stage="retrieving_sparse", status="started",
                label="Sparse Recall", detail="Scoring keyword overlap over retrieval chunks…",
            )
            yield _sse(
                "progress", stage="retrieving_sparse", status="completed",
                label="Sparse Recall",
                detail=(
                    f"{_chunk_meta.get('sparse_count', 0)} keyword-ranked chunk(s) "
                    f"via {_chunk_meta.get('sparse_backend', 'keyword_overlap')}"
                ),
                duration_ms=_vms,
            )
            yield _sse(
                "progress", stage="fusing_results", status="started",
                label="Fusing Results", detail="Combining dense and sparse rankings with RRF…",
            )
            yield _sse(
                "progress", stage="fusing_results", status="completed",
                label="Fusing Results",
                detail=f"{_chunk_meta.get('fused_count', len(_chunk_context))} fused chunk(s) returned",
                duration_ms=_vms,
            )
            # Rerank: Claude-based relevance pass when candidates > 3
            if len(_chunk_context) > 3 and ANTHROPIC_API_KEY:
                try:
                    yield _sse(
                        "progress", stage="reranking", status="started",
                        label="Reranking", detail="Claude relevance pass on retrieved chunks…",
                    )
                    _rr_t0 = time.perf_counter()
                    _chunk_texts = [c.get("text", "") for c in _chunk_context[:6]]
                    _rerank_prompt = (
                        f"Given the question: '{question}'\n\n"
                        "Score each chunk 0–10 for relevance. Return a JSON array of integers, "
                        "one per chunk, in the same order.\n\n"
                        + "\n\n".join(f"CHUNK {i}: {t[:400]}" for i, t in enumerate(_chunk_texts))
                    )
                    _rr_text, _ = _llm_generate_claude(
                        system_prompt="You are a relevance judge for a historical archive retrieval system.",
                        user_content=_rerank_prompt,
                        max_tokens=80,
                        temperature=0.0,
                    )
                    # Parse scores and re-order
                    import re as _re
                    _scores = [int(x) for x in _re.findall(r"\d+", _rr_text)]
                    if len(_scores) == len(_chunk_context):
                        _ranked = sorted(zip(_scores, _chunk_context), key=lambda x: x[0], reverse=True)
                        _chunk_context = [c for _, c in _ranked]
                    _rr_ms = int((time.perf_counter() - _rr_t0) * 1000)
                    yield _sse(
                        "progress", stage="reranking", status="completed",
                        label="Reranking", detail=f"Reranked {len(_chunk_context)} chunks",
                        duration_ms=_rr_ms,
                    )
                except Exception as _rr_exc:
                    log.debug("orchestrated_pipeline.rerank_failed error=%s", _rr_exc)
        except Exception as _vc_exc:
            log.debug("orchestrated_pipeline.vector_recall_failed error=%s", _vc_exc)

    # ── FALLBACK — old pipeline SQL paths (also used by RELATIONAL/COMPARATIVE for counts) ──
    # Activates when: (a) route=FALLBACK, (b) ANALYTICAL lane didn't produce SQL,
    # (c) RELATIONAL/COMPARATIVE always need SQL for numeric data.
    _need_fallback_sql = not _routed_sql_ok
    if _need_fallback_sql:
        _raw_memory = _load_approved_query_memory()
        approved_matches = _find_similar_approved_queries(question, analysis, canonical_townland)
        direct_memory_match = (
            approved_matches[0]
            if _can_reuse_memory_directly(
                question, analysis, canonical_townland,
                approved_matches[0] if approved_matches else None,
            )
            else None
        )
        _p4_template, _p4_memory = _phase4_retrieve(question, canonical_townland, _raw_memory)
        query_provenance["approved_query_candidates"] = _memory_matches_for_display(approved_matches)

        verified = _try_verified_analysis(question, canonical_townland, analysis)
        if verified and not force_llm:
            sql = str(verified.get("sql") or "")
            llm_meta = dict(verified.get("meta") or {})
            chart_hint = verified.get("chart_hint")
            query_provenance.update({
                "strategy": f"{query_provenance.get('strategy','new_pipeline')}+verified_analysis",
                "lane": "fallback_verified_analysis",
            })
            ms = int((time.perf_counter() - t0) * 1000)
            yield _sse(
                "progress", stage="contacting_llm", status="completed",
                label="Building Query",
                detail=f"Verified analysis: {llm_meta.get('analysis_id')}",
                duration_ms=ms,
            )
        elif _p4_template and not force_llm:
            _p4_tid = _p4_template["template_id"]
            sql = str(_p4_template["sql"])
            llm_meta = {
                "provider": "phase4_embedding", "model": "tfidf_rrf",
                "mode": "template_fast_lane", "analysis_id": _p4_tid,
                "description": _p4_template.get("description"),
                "cosine_score": _p4_template.get("cosine_score"),
                "rrf_score": _p4_template.get("rrf_score"),
            }
            chart_hint = VERIFIED_ANALYSIS_CHART_HINTS.get(_p4_tid)
            query_provenance.update({
                "strategy": "fallback_phase4_template", "lane": "fallback_p4",
                "p4_template_id": _p4_tid,
            })
            ms = int((time.perf_counter() - t0) * 1000)
            yield _sse(
                "progress", stage="contacting_llm", status="completed",
                label="Building Query",
                detail=f"Phase 4 fast lane: {_p4_tid} cosine={_p4_template.get('cosine_score', 0):.3f}",
                duration_ms=ms,
            )
        elif direct_memory_match and not force_llm:
            sql = str(direct_memory_match.get("sql_text") or "")
            llm_meta = {
                "provider": "query_memory", "model": "approved_sql",
                "mode": "approved_memory_reuse",
                "memory_id": direct_memory_match.get("id"),
                "memory_similarity": direct_memory_match.get("match_score"),
                "description": "Reused previously approved SQL for a similar question",
            }
            query_provenance.update({
                "used_approved_memory": True,
                "reused_memory_id": direct_memory_match.get("id"),
                "direct_memory_reuse": True,
                "strategy": "fallback_approved_memory",
                "lane": "fallback_memory",
            })
            ms = int((time.perf_counter() - t0) * 1000)
            yield _sse(
                "progress", stage="contacting_llm", status="completed",
                label="Building Query",
                detail=f"Approved memory reuse (similarity {direct_memory_match.get('match_score')})",
                duration_ms=ms,
            )
        else:
            try:
                _few_shot = _p4_memory if _p4_memory else approved_matches
                sql, llm_meta = _generate_sql(
                    question, _ANNOTATED_SCHEMA, canonical_townland,
                    analysis=analysis, approved_examples=_few_shot,
                )
                vrti_postgres_sql, vrti_query_meta = _generate_vrti_postgres_query(
                    question, canonical_townland
                )
                query_provenance.update({
                    "strategy": "fallback_llm_sql",
                    "lane": "fallback_llm",
                })
                ms = int((time.perf_counter() - t0) * 1000)
                yield _sse(
                    "progress", stage="contacting_llm", status="completed",
                    label="Building Query",
                    detail=(
                        f"LLM SQL [{llm_meta.get('mode')}] | "
                        f"VRTI: {vrti_query_meta.get('mode')} | "
                        f"model: {llm_meta.get('model')}"
                    ),
                    duration_ms=ms,
                )
            except Exception as exc:
                ms = int((time.perf_counter() - t0) * 1000)
                if ASK_ALLOW_HEURISTIC_FALLBACK:
                    sql = _fallback_sql(question, canonical_townland)
                    llm_meta = {
                        "provider": "local_fallback", "model": "rule_template",
                        "mode": "fallback_rule",
                    }
                    query_provenance["strategy"] = "emergency_fallback"
                else:
                    sql = _diagnostic_message_sql(
                        "I could not build a validated SQL query for this question. "
                        "Please rephrase with a clearer townland, year, surname, ship, "
                        "record type, or measure."
                    )
                    llm_meta = {
                        "provider": "validation_guard", "model": "validated_sql_only",
                        "mode": "no_validated_sql", "error": str(exc),
                    }
                    query_provenance["strategy"] = "validated_sql_unavailable"
                vrti_postgres_sql = _fallback_vrti_postgres_sql(question, canonical_townland)
                vrti_query_meta = {
                    "provider": "local_fallback", "model": "rule_template", "mode": "fallback_rule",
                }
                yield _sse(
                    "progress", stage="contacting_llm", status="completed",
                    label="Building Query",
                    detail=f"LLM unavailable ({exc}) — fallback SQL used",
                    duration_ms=ms,
                )
    else:
        # Routed SQL produced by ANALYTICAL lane — fill in VRTI defaults
        vrti_postgres_sql = vrti_postgres_sql or _fallback_vrti_postgres_sql(question, canonical_townland)
        vrti_query_meta = vrti_query_meta or {
            "provider": "new_pipeline", "model": intent_route, "mode": "routed",
        }

    # ── Stage 2 — Framing Query (FORBIDDEN_SQL guardrail) ────────────────────
    t0 = time.perf_counter()
    yield _sse("progress", stage="framing_query", status="started",
               label="Framing Query", detail="Validating SQL for safety…")
    try:
        safe_sql = _sanitize_and_validate_sql(sql)
    except ValueError:
        if ASK_ALLOW_HEURISTIC_FALLBACK:
            safe_sql = _sanitize_and_validate_sql(_fallback_sql(question, canonical_townland))
        else:
            safe_sql = _sanitize_and_validate_sql(_diagnostic_message_sql(
                "I could not validate a safe SQL query for this question. "
                "Please rephrase with a clearer entity, year, townland, surname, or measure."
            ))
            llm_meta = {
                "provider": "validation_guard", "model": "validated_sql_only",
                "mode": "no_validated_sql", "error": "sql_validation_failed",
            }
            query_provenance["strategy"] = "validated_sql_unavailable"
    ms = int((time.perf_counter() - t0) * 1000)
    yield _sse("progress", stage="framing_query", status="completed",
               label="Framing Query", detail="Read-only query validated", duration_ms=ms)

    # ── Stage 3 — Querying Database ───────────────────────────────────────────
    t0 = time.perf_counter()
    yield _sse("progress", stage="querying_database", status="started",
               label="Querying SQLite", detail="Running SQL against local SQLite database…")
    safe_sql, columns, rows, query_warning, execution_meta = _execute_with_recovery(
        question=question, townland_hint=canonical_townland, sql=safe_sql,
        approved_examples=approved_matches,
    )
    if query_warning:
        warnings.append(query_warning)
    if execution_meta:
        query_provenance["execution_mode"] = execution_meta.get("mode") or "recovered"
        if execution_meta.get("mode") == "fallback_rule":
            warnings.append(
                "The system had to use an emergency local heuristic because the "
                "generated SQL could not be executed safely."
            )
            query_provenance["strategy"] = "emergency_fallback"
        elif execution_meta.get("mode") == "no_validated_sql":
            warnings.append(
                "No validated SQL query could be produced safely, so the system "
                "returned guidance instead of guessing."
            )
            query_provenance["strategy"] = "validated_sql_unavailable"
        else:
            warnings.append("The system repaired the generated SQL after an execution error.")
        if llm_meta.get("mode") != "approved_memory_reuse":
            llm_meta = execution_meta
    if direct_memory_match:
        _mark_query_memory_used(int(direct_memory_match.get("id") or 0))
    sql_execution_ms = int((time.perf_counter() - t0) * 1000)
    yield _sse(
        "progress", stage="querying_database", status="completed",
        label="Querying SQLite",
        detail=f"{len(rows)} row{'s' if len(rows)!=1 else ''} returned · {sql_execution_ms} ms",
        duration_ms=sql_execution_ms,
    )

    # ── Stage 4 — Querying VRTI Graph ─────────────────────────────────────────
    t0 = time.perf_counter()
    yield _sse("progress", stage="querying_vrti_graph", status="started",
               label="Querying VRTI Graph",
               detail="Fetching townland + parish data from VRTI Knowledge Graph…")
    kg_context, kg_warnings = _kg_context(question, canonical_townland, force=True)
    vrti_columns, vrti_rows = _kg_context_to_table(kg_context)
    warnings.extend(kg_warnings)
    ms = int((time.perf_counter() - t0) * 1000)
    parish_count = (kg_context or {}).get("parish_count")
    _vrti_detail = f"{len(vrti_rows)} townland(s) enriched"
    if parish_count:
        _vrti_detail += f" | {parish_count} Wicklow parishes"
    yield _sse("progress", stage="querying_vrti_graph", status="completed",
               label="Querying VRTI Graph", detail=_vrti_detail, duration_ms=ms)

    # ── Phase 3 subgraph context injection ────────────────────────────────────
    # Inject linearized subgraph into kg_context so the LLM rewrite can synthesise
    # both qualitative KG context and quantitative SQL results.
    if _phase3_result and _phase3_result.linearized:
        if kg_context is None:
            kg_context = {}
        kg_context["subgraph_linearized"] = _phase3_result.linearized
        if intent_route == _COMPARATIVE:
            kg_context["phase6_fusion_note"] = (
                "This is a comparative question. SQLite estate records provide counts "
                "and statistics; the VRTI/GraphDB subgraph provides qualitative and "
                "relational context. Synthesise both in your answer."
            )

    # ── In-process GraphRAG context injection (flow.md §5) ────────────────────
    # Property-graph subgraph supplements the RDF subgraph (additive, not replacing).
    if _graphrag_result and _graphrag_result.available and _graphrag_result.linearized:
        if kg_context is None:
            kg_context = {}
        existing = kg_context.get("subgraph_linearized", "")
        graphrag_block = "\n\n### Property-graph context\n" + _graphrag_result.linearized
        kg_context["subgraph_linearized"] = (existing + graphrag_block).strip()
        if intent_route == _COMPARATIVE:
            kg_context["phase6_fusion_note"] = (
                "This is a comparative question. "
                "SQLite (relational counts) and RDF/GraphDB (SPARQL — open-world) "
                "are the two comparison paradigms; the in-process property graph "
                "provides additional qualitative context and corroboration. "
                "SQL owns the authoritative counts. Surface any discrepancies explicitly."
            )

    # ── Stage 4.5 — Querying GraphDB (non-fatal) ─────────────────────────────
    from backend.integrations import graphdb_sparql as _gdb
    graph_comparison: dict[str, Any] = {
        "sparql_query": "", "sql_query": safe_sql,
        "columns": [], "rows": [], "row_count": 0,
        "graphdb_available": False, "triple_count": -1,
        "data_loaded": False, "error": None, "setup_hint": None,
        "timing": {"sql_ms": sql_execution_ms, "sparql_gen_ms": 0, "graphdb_ms": 0},
        "mismatch_explanation": None,
    }
    if ActiveConfig.GRAPHDB_ENABLED:
        _gdb_stage_t0 = time.perf_counter()
        yield _sse("progress", stage="querying_graphdb", status="started",
                   label="Querying GraphDB", detail="Generating SPARQL query via LLM…")
        _sparql_t0 = time.perf_counter()
        sparql_text = ""
        try:
            sparql_text, _ = _generate_graphdb_sparql(question, safe_sql)
            graph_comparison["sparql_query"] = sparql_text
        except Exception as exc:
            graph_comparison["error"] = f"SPARQL generation failed: {exc}"
            log.warning("orchestrated_pipeline.graphdb_sparql_gen_failed error=%s", exc)
        graph_comparison["timing"]["sparql_gen_ms"] = int(
            (time.perf_counter() - _sparql_t0) * 1000
        )
        if sparql_text:
            yield _sse("progress", stage="querying_graphdb", status="started",
                       label="Querying GraphDB", detail="Executing SPARQL against local RDF graph…")
            _gdb_exec_t0 = time.perf_counter()
            try:
                graphdb_ok = _gdb.probe()
                graph_comparison["graphdb_available"] = graphdb_ok
                if graphdb_ok:
                    tc = _gdb.triple_count()
                    graph_comparison["triple_count"] = tc
                    graph_comparison["data_loaded"] = tc > 0
                    if tc == 0:
                        graph_comparison["setup_hint"] = (
                            "GraphDB is running but the repository is empty. "
                            "Load data with: python3 scripts/rdf_uplift.py --import"
                        )
                    g_cols, g_rows = _gdb.query(sparql_text)
                    graph_comparison["columns"] = g_cols
                    graph_comparison["rows"] = g_rows
                    graph_comparison["row_count"] = len(g_rows)
                    _gdb_available = graphdb_ok
                    _gdb_loaded = graph_comparison["data_loaded"]
                    if _gdb_available and _gdb_loaded and sparql_text:
                        _sql_n = len(rows)
                        _gdb_n = graph_comparison["row_count"]
                        _rc_diff = _sql_n != _gdb_n
                        _val_diff = False
                        if not _rc_diff and _sql_n == 1 and _gdb_n == 1:
                            _sv = _first_numeric(rows[0])
                            _gv = _first_numeric(graph_comparison["rows"][0])
                            _val_diff = (
                                _sv is not None and _gv is not None and _sv != _gv
                            )
                        if _rc_diff or _val_diff:
                            graph_comparison["mismatch_explanation"] = _explain_result_mismatch(
                                question=question,
                                sql=safe_sql,
                                sparql=sparql_text,
                                sql_rows=rows,
                                sparql_rows=graph_comparison["rows"],
                            )
            except Exception as exc:
                graph_comparison["error"] = str(exc)
                log.warning("orchestrated_pipeline.graphdb_execute_failed error=%s", exc)
            graph_comparison["timing"]["graphdb_ms"] = int(
                (time.perf_counter() - _gdb_exec_t0) * 1000
            )
        _gdb_total_ms = int((time.perf_counter() - _gdb_stage_t0) * 1000)
        if graph_comparison["graphdb_available"]:
            _tc = graph_comparison["triple_count"]
            _tc_label = f" · {_tc:,} triples" if _tc >= 0 else ""
            _gdb_detail = (
                f"{graph_comparison['row_count']} row(s){_tc_label} · "
                f"SPARQL gen {graph_comparison['timing']['sparql_gen_ms']} ms · "
                f"query {graph_comparison['timing']['graphdb_ms']} ms"
            )
        else:
            _gdb_detail = (
                "GraphDB offline — SPARQL generated, not executed"
                if sparql_text else "SPARQL generation failed"
            )
        yield _sse("progress", stage="querying_graphdb", status="completed",
                   label="Querying GraphDB", detail=_gdb_detail, duration_ms=_gdb_total_ms)

    # Neo4j Cypher comparison removed — RQ6 now uses SQL vs SPARQL (two paradigms).
    # The in-process graph supplies qualitative corroboration, not count comparison.

    # ── Phase 6 — Fusion & reconciliation ─────────────────────────────────────
    t0 = time.perf_counter()
    yield _sse("progress", stage="querying_fusion", status="started",
               label="Reconciling Sources",
               detail="Aligning SQLite, GraphDB, and VRTI results on resolved entity…")
    fusion_result = _fuse_lanes(
        sqlite_rows=rows,
        sqlite_columns=columns,
        graphdb_rows=graph_comparison.get("rows", []),
        graphdb_columns=graph_comparison.get("columns", []),
        vrti_rows=vrti_rows,
        canonical_townland=canonical_townland,
        entity_resolution=townland_resolution.get("entity_resolution"),
        question=question,
    )
    if fusion_result["discrepancies"]:
        if kg_context is None:
            kg_context = {}
        kg_context["phase6_discrepancies"] = fusion_result["discrepancies"]
        kg_context["phase6_fusion_text"] = fusion_result["fusion_text"]
    ms = int((time.perf_counter() - t0) * 1000)
    _f_detail = (
        f"{fusion_result['discrepancy_count']} discrepancy(ies) · "
        f"{fusion_result['agreement_count']} agreement(s)"
        if (fusion_result["discrepancy_count"] or fusion_result["agreement_count"])
        else "No numeric overlap between sources to compare"
    )
    yield _sse("progress", stage="querying_fusion", status="completed",
               label="Reconciling Sources", detail=_f_detail, duration_ms=ms)

    # ── Stage 5 — Preparing Output ────────────────────────────────────────────
    t0 = time.perf_counter()
    yield _sse("progress", stage="preparing_output", status="started",
               label="Preparing Output",
               detail="Building data tables, LLM rewrite, and PDF report…")
    availability = _build_availability_payload(
        question=question,
        analysis=analysis,
        columns=columns,
        rows=rows,
        townland_resolution=townland_resolution,
    )
    related_insights = _build_related_insights(
        question=question, analysis=analysis,
        rows=rows, townland_norm=canonical_townland,
    )
    chart_spec = _build_chart_spec(
        question=question, columns=columns, rows=rows,
        availability=availability, chart_hint=chart_hint,
    )
    actual_answer = _build_answer_text(
        question, columns, rows, canonical_townland, kg_context,
        availability=availability,
    )
    summary_block = _build_structured_summary(
        question=question, local_columns=columns, local_rows=rows,
        vrti_columns=vrti_columns, vrti_rows=vrti_rows,
        kg_context=kg_context, availability=availability,
        related_insights=related_insights,
    )
    supporting_context = _build_supporting_context(
        question=question, townland_norm=canonical_townland,
        townland_resolution=townland_resolution,
        primary_columns=columns, primary_rows=rows,
        kg_context=kg_context, related_insights=related_insights,
    )
    llm_data_context = _build_llm_data_context(
        local_columns=columns, local_rows=rows,
        vrti_columns=vrti_columns, vrti_rows=vrti_rows,
    )
    llm_rephrased_answer: str | None = None
    llm_rewrite_meta: dict[str, Any] = {
        "provider": "none", "model": None, "mode": "not_requested",
    }
    try:
        yield _sse(
            "progress", stage="synthesising_answer", status="started",
            label="Synthesising Answer",
            detail="Combining SQL, graph, and retrieval context into the final answer…",
        )
        # Part F — Claude synthesis when new pipeline is active
        # Builds a structured payload so the answer leads with the direct result,
        # cites provenance, surfaces disambiguation, and ends with next steps.
        _chunk_summary = " ".join(c.get("text", "")[:300] for c in _chunk_context[:3]) if _chunk_context else ""
        _graph_ctx_str = (kg_context or {}).get("subgraph_linearized", "") or _chunk_summary
        _resolved_entities: list[dict[str, Any]] = []
        if canonical_townland:
            _re_entry: dict[str, Any] = {
                "entity_type": "townland",
                "label": townland_resolution.get("name"),
                "sql_id": townland_resolution.get("sql_id"),
                "kg_uri": townland_resolution.get("kg_uri"),
            }
            _resolved_entities.append(_re_entry)
        if _person_identity_result:
            _resolved_entities.append({
                "entity_type": "person",
                "raw_name": _person_identity_result.get("raw_name"),
                "is_ambiguous": _person_identity_result.get("is_ambiguous"),
                "candidates": _person_identity_result.get("person_candidates", []),
                "disambiguation_note": _person_identity_result.get("disambiguation_note"),
            })
        _sql_result_for_synthesis: dict[str, Any] = {
            "columns": columns,
            "rows": rows[:20],
            "row_count": len(rows),
            "sql_used": safe_sql,
        }
        _discrepancies_for_synthesis = [
            {
                "metric": d.get("metric"),
                "sql_value": d.get("sqlite_value"),
                "graph_value": d.get("graphdb_value"),
                "likely_reason": d.get("likely_reason") or d.get("likely_cause"),
            }
            for d in fusion_result.get("discrepancies", [])
        ]
        llm_rephrased_answer, llm_rewrite_meta = _claude_synthesize_answer(
            question=question,
            resolved_entities=_resolved_entities,
            sql_result=_sql_result_for_synthesis,
            graph_context=_graph_ctx_str,
            discrepancies=_discrepancies_for_synthesis,
            provenance={
                "townland_match_type": townland_resolution.get("match_type"),
                "route": intent_route,
                "sql_strategy": query_provenance.get("strategy"),
            },
        )
        # ── Numeric gate outcome ──────────────────────────────────────────────
        _gate_outcome = llm_rewrite_meta.get("gate_outcome", "not_applied")
        query_provenance["numeric_gate_outcome"] = _gate_outcome
        if _gate_outcome == "fallback":
            llm_rephrased_answer = ""
            query_provenance["gate_blocked_synthesis"] = llm_rewrite_meta.get("gate_blocked_text", "")
            query_provenance["gate_violations"] = llm_rewrite_meta.get("gate_violations", [])
            warnings.append(
                "Numeric-consistency gate: the synthesised answer introduced unsupported figures "
                "on two attempts and was discarded — the raw data answer is shown instead."
            )
        elif _gate_outcome == "regenerated":
            warnings.append(
                "Numeric-consistency gate: the first synthesis attempt contained unsupported "
                "numbers and was regenerated."
            )
        if llm_rephrased_answer:
            summary_block["llm_rephrased_text"] = llm_rephrased_answer

        # ── Cross-verifier (all LLM-generated answers) ───────────────────────
        _strategy = query_provenance.get("strategy", "")
        _is_llm_fallback = any(
            s in _strategy
            for s in ("emergency_fallback", "validated_sql_unavailable",
                      "llm_fallback", "fallback_llm_sql", "semantic_layer_llm")
        )
        if llm_rephrased_answer and _is_llm_fallback:
            try:
                _verifier = _cross_verify_synthesis(
                    question=question,
                    synthesis_text=llm_rephrased_answer,
                    sql_result=_sql_result_for_synthesis,
                )
                query_provenance["verifier"] = _verifier
                if _verifier.get("verdict") == "disagree":
                    _claims = _verifier.get("unsupported_claims", [])
                    if _claims:
                        _claim_str = "; ".join(str(c) for c in _claims[:3])
                        warnings.append(
                            f"Cross-verifier flagged claims not found in result data: {_claim_str}"
                        )
                    else:
                        warnings.append("Cross-verifier flagged potential unsupported claims.")
            except Exception as _vex:
                log.debug("orchestrated_pipeline.cross_verify_failed error=%s", _vex)
                query_provenance["verifier"] = {"verdict": "skip", "reason": str(_vex)}
        else:
            query_provenance.setdefault("verifier", {"verdict": "skip", "reason": "deterministic_route"})

        _gate_detail = f" · gate={_gate_outcome}" if _gate_outcome != "not_applied" else ""
        yield _sse(
            "progress", stage="synthesising_answer", status="completed",
            label="Synthesising Answer",
            detail=f"Synthesis complete via {llm_rewrite_meta.get('provider') or 'llm'}{_gate_detail}",
        )
    except Exception as exc:
        llm_rewrite_meta = {
            "provider": "unavailable", "model": None,
            "mode": "not_generated", "error": str(exc),
        }
        warnings.append(f"LLM rewrite unavailable: {exc}")
        query_provenance["numeric_gate_outcome"] = "not_applied"
        query_provenance.setdefault("verifier", {"verdict": "skip", "reason": "synthesis_error"})
        yield _sse(
            "progress", stage="synthesising_answer", status="completed",
            label="Synthesising Answer",
            detail="Synthesis skipped — falling back to direct answer text.",
        )

    structured_output = {
        "queries": {
            "local_sqlite_query": safe_sql,
            "vrti_postgresql_query": vrti_postgres_sql,
        },
        "processed_tables": {
            "local_database": {"columns": columns, "rows": rows, "row_count": len(rows)},
            "vrti_graph": {"columns": vrti_columns, "rows": vrti_rows, "row_count": len(vrti_rows)},
        },
        "summary": summary_block,
        "supporting_context": _supporting_context_for_display(supporting_context),
        "availability": availability,
        "related_insights": related_insights,
        "chart": chart_spec,
        "query_provenance": query_provenance,
        "discrepancies": fusion_result["discrepancies"],
        "fusion": {
            "discrepancy_count": fusion_result["discrepancy_count"],
            "agreement_count": fusion_result["agreement_count"],
            "fusion_text": fusion_result["fusion_text"],
        },
    }
    pdf_path = _write_pdf_report(
        question=question, answer=actual_answer, sql=safe_sql,
        columns=columns, rows=rows, llm_meta=llm_meta,
        kg_context=kg_context, include_sql=True,
        vrti_postgres_sql=vrti_postgres_sql,
        vrti_columns=vrti_columns, vrti_rows=vrti_rows,
        summary_block=summary_block,
        llm_rephrased_answer=llm_rephrased_answer,
        llm_rewrite_meta=llm_rewrite_meta,
    )
    if llm_meta.get("mode") == "fallback_rule":
        warnings.append("LLM SQL generation unavailable — fallback SQL template used.")
    elif llm_meta.get("mode") == "no_validated_sql":
        warnings.append(
            "The system did not find a validated SQL query for this request and returned "
            "safe guidance instead."
        )
    warnings.extend(_null_rate_warnings(columns, rows))
    ms = int((time.perf_counter() - t0) * 1000)
    yield _sse("progress", stage="preparing_output", status="completed",
               label="Preparing Output", detail="PDF generated", duration_ms=ms)
    yield _sse("progress", stage="done", status="completed",
               label="Done", detail="Ask response ready.")

    # ── Final result ──────────────────────────────────────────────────────────
    payload: dict[str, Any] = {
        "question": question,
        "answer": actual_answer,
        "actual_answer": actual_answer,
        "llm_rephrased_answer": llm_rephrased_answer,
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "llm": llm_meta,
        "llm_rewrite": llm_rewrite_meta,
        "vrti_query_generation": vrti_query_meta,
        "townland_context": canonical_townland,
        "townland_resolution": townland_resolution,
        # Phase 1 — entity_resolver ran once; sql_id + kg_uri shared by all lanes
        "entity_resolution": townland_resolution.get("entity_resolution"),
        "kg_context": kg_context,
        "availability": availability,
        "related_insights": related_insights,
        "chart": chart_spec,
        "query_provenance": query_provenance,
        "suggestions": availability.get("suggestions", []),
        "structured_output": structured_output,
        "pdf_url": f"/api/ask/pdf/{pdf_path.name}",
        "warnings": warnings,
        "source_tables": _extract_tables(safe_sql) if safe_sql else [],
        "graph_comparison": graph_comparison,
        "discrepancies": fusion_result["discrepancies"],
        "fusion": {
            "discrepancy_count": fusion_result["discrepancy_count"],
            "agreement_count": fusion_result["agreement_count"],
            "entity_label": fusion_result["entity_label"],
            "kg_uri": fusion_result["kg_uri"],
            "fusion_text": fusion_result["fusion_text"],
            "source_provenance": fusion_result["source_provenance"],
        },
        "subgraph_context": {
            "linearized":     _phase3_result.linearized,
            "hierarchy":      _phase3_result.hierarchy,
            "siblings":       _phase3_result.siblings,
            "external_links": _phase3_result.external_links,
            "sources_used":   _phase3_result.sources_used,
            "question_type":  _phase3_result.question_type,
            "k_hops":         _phase3_result.k_hops,
            "pruned":         _phase3_result.pruned,
        } if _phase3_result else None,
        "graphrag_context": {
            "linearized":          _graphrag_result.linearized,
            "seed_nodes":          _graphrag_result.seed_nodes,
            "community_summaries": _graphrag_result.community_summaries,
            "path_used":           _graphrag_result.path_used,
            "k_hops":              _graphrag_result.k_hops,
            "pruned":              _graphrag_result.pruned,
            "sources_used":        _graphrag_result.sources_used,
            "degradation_note":    _graphrag_result.degradation_note,
        } if _graphrag_result else None,
    }
    if include_sql:
        payload["sql"] = safe_sql
    yield _sse("result", **payload)


def answer_question_stream(
    question: str,
    townland_hint: str | None = None,
    include_sql: bool = False,
    force_llm: bool = False,
) -> Generator[str, None, None]:
    """
    Streaming pipeline — yields SSE-formatted strings.

    Events
    ------
    {"type":"progress","stage":"...","status":"started"|"completed","label":"...","detail":"...","duration_ms":N}
    {"type":"result", ...full payload...}
    {"type":"error", "message":"..."}
    """
    clean_q = (question or "").strip()
    if len(clean_q) < 3:
        yield _sse("error", message="Please enter a longer question.")
        return

    # ── Feature flag: routed architecture ─────────────────────────────────────
    # When ASK_USE_NEW_PIPELINE=true, delegate to the new orchestrator.
    # When false (default), run the existing pipeline unchanged below.
    if ASK_USE_NEW_PIPELINE:
        yield from _orchestrated_pipeline_stream(clean_q, townland_hint, include_sql, force_llm)
        return

    try:
        _ensure_unified_table_seeded()
        _ensure_heritage_feature_seeded()
        _ensure_query_memory_schema()
    except Exception as exc:
        yield _sse("error", message=f"Database not ready: {exc}")
        return

    townland_resolution = _resolve_townland_context(clean_q, townland_hint)
    canonical_townland = townland_resolution.get("name_norm")
    analysis = _analyse_question(clean_q, canonical_townland or townland_hint)
    warnings: list[str] = []
    if townland_resolution.get("warning"):
        warnings.append(str(townland_resolution["warning"]))
    warnings.extend(_question_data_coverage_warnings(clean_q))

    # Phase 2 — try semantic layer (deterministic rule-based fast lane)
    _semantic_slot_fill = None
    try:
        from backend.services.semantic_layer import (
            try_rule_based_fill as _try_rule_based_fill,
            compile_sql as _compile_semantic_sql,
            compile_sparql as _compile_semantic_sparql,
            slot_fill_meta as _slot_fill_meta,
            build_slot_fill_prompt as _build_slot_fill_prompt,
            parse_slot_fill as _parse_slot_fill,
        )
        _semantic_slot_fill = _try_rule_based_fill(clean_q, analysis, townland_resolution)
    except Exception as _sl_exc:
        log.debug("ask_service.semantic_layer_init_failed error=%s", _sl_exc)

    verified_analysis = _try_verified_analysis(clean_q, canonical_townland, analysis)
    approved_matches = _find_similar_approved_queries(clean_q, analysis, canonical_townland)
    direct_memory_match = approved_matches[0] if _can_reuse_memory_directly(clean_q, analysis, canonical_townland, approved_matches[0] if approved_matches else None) else None

    # Phase 4 — hybrid semantic retrieval: dense cosine + sparse keyword → RRF.
    # Runs over all templates + approved memory in parallel with the checks above.
    # _p4_template: high-confidence template fast lane (may be None)
    # _p4_memory:   approved memory rows re-ranked by embedding similarity;
    #               used as few-shot examples for Phase 7 LLM fallback.
    _raw_memory = _load_approved_query_memory()
    _p4_template, _p4_memory = _phase4_retrieve(clean_q, canonical_townland, _raw_memory)

    # ── Stage 1 — Contacting LLM / Query memory match ─────────────────────
    t0 = time.perf_counter()
    yield _sse("progress", stage="contacting_llm", status="started", label="Contacting LLM",
               detail="Checking approved query memory and preparing schema-aware SQL…")

    sql: str
    llm_meta: dict[str, Any]
    vrti_postgres_sql: str
    vrti_query_meta: dict[str, Any]
    chart_hint: str | None = None
    query_provenance: dict[str, Any] = {
        "used_approved_memory": False,
        "reused_memory_id": None,
        "direct_memory_reuse": False,
        "execution_mode": "executed_as_generated",
        "strategy": "llm_sql",
        "approved_query_candidates": _memory_matches_for_display(approved_matches),
    }

    # Phase 2 — semantic layer takes priority for analytical questions.
    # Falls through to verified_analysis → memory → LLM if fill is absent or
    # confidence is below threshold.
    _sl_confidence_threshold = 0.80
    if (
        _semantic_slot_fill is not None
        and _semantic_slot_fill.confidence >= _sl_confidence_threshold
        and not force_llm
    ):
        try:
            _compiled = _compile_semantic_sql(
                _semantic_slot_fill, _clearances_count_column()
            )
            if _compiled:
                sql = _compiled
                llm_meta = _slot_fill_meta(_semantic_slot_fill, sql)
                vrti_postgres_sql = _fallback_vrti_postgres_sql(clean_q, canonical_townland)
                vrti_query_meta = {"provider": "semantic_layer", "model": "rule_compiler", "mode": "semantic_layer"}
                query_provenance.update({"strategy": "semantic_layer"})
                ms = int((time.perf_counter() - t0) * 1000)
                yield _sse(
                    "progress", stage="contacting_llm", status="completed",
                    label="Contacting LLM",
                    detail=(
                        f"Semantic layer: {_semantic_slot_fill.metric} "
                        f"dims={_semantic_slot_fill.dimensions} "
                        f"filters={list(_semantic_slot_fill.filters.keys())} "
                        f"confidence={_semantic_slot_fill.confidence:.2f}"
                    ),
                    duration_ms=ms,
                )
                # Jump directly to SQL execution — skip all other routing branches.
                # The variable 'sql' is set; we proceed to Stage 2 (framing query).
                # Use a sentinel to skip the if/elif/else chain below.
                _semantic_routed = True
            else:
                _semantic_routed = False
        except Exception as _sl_compile_exc:
            log.warning("ask_service.semantic_compile_failed error=%s", _sl_compile_exc)
            _semantic_routed = False
    else:
        _semantic_routed = False

    # Phase 5 routing state — initialized here so they are visible after the chain.
    _intent_route: str = "fallback"
    _force_subgraph: bool = False

    if _semantic_routed:
        pass  # sql, llm_meta, vrti_postgres_sql all set above
    elif _p4_template and not force_llm:
        # Phase 4 template fast lane — high-confidence embedding match short-circuits LLM.
        # required_keywords hard filter was already applied inside _phase4_retrieve.
        _p4_tmpl = _p4_template["template"]
        sql = str(_p4_template["sql"])
        _p4_tid = _p4_template["template_id"]
        llm_meta = {
            "provider": "phase4_embedding",
            "model": "tfidf_rrf",
            "mode": "template_fast_lane",
            "analysis_id": _p4_tid,
            "description": _p4_template.get("description"),
            "cosine_score": _p4_template.get("cosine_score"),
            "rrf_score": _p4_template.get("rrf_score"),
        }
        vrti_postgres_sql = _fallback_vrti_postgres_sql(clean_q, canonical_townland)
        vrti_query_meta = {"provider": "phase4_embedding", "model": "tfidf_rrf", "mode": "template_fast_lane"}
        chart_hint = VERIFIED_ANALYSIS_CHART_HINTS.get(_p4_tid)
        query_provenance.update({
            "strategy": "phase4_template_fast_lane",
            "p4_template_id": _p4_tid,
            "p4_cosine_score": _p4_template.get("cosine_score"),
        })
        ms = int((time.perf_counter() - t0) * 1000)
        yield _sse(
            "progress", stage="contacting_llm", status="completed", label="Contacting LLM",
            detail=(
                f"Phase 4 fast lane: template={_p4_tid} "
                f"cosine={_p4_template.get('cosine_score', 0):.3f}"
            ),
            duration_ms=ms,
        )
    elif verified_analysis and not force_llm:
        sql = str(verified_analysis.get("sql") or "")
        llm_meta = dict(verified_analysis.get("meta") or {})
        vrti_postgres_sql = _fallback_vrti_postgres_sql(clean_q, canonical_townland)
        vrti_query_meta = {"provider": "verified_analysis", "model": "curated_sql", "mode": "verified_analysis"}
        chart_hint = verified_analysis.get("chart_hint")
        query_provenance.update({
            "strategy": "verified_analysis",
        })
        ms = int((time.perf_counter() - t0) * 1000)
        yield _sse("progress", stage="contacting_llm", status="completed", label="Contacting LLM",
                   detail=f"Using verified analysis SQL ({llm_meta.get('analysis_id')})", duration_ms=ms)
    elif direct_memory_match and not force_llm:
        sql = str(direct_memory_match.get("sql_text") or "")
        llm_meta = {
            "provider": "query_memory",
            "model": "approved_sql",
            "mode": "approved_memory_reuse",
            "memory_id": direct_memory_match.get("id"),
            "memory_similarity": direct_memory_match.get("match_score"),
            "description": "Reused a previously approved SQL query for a highly similar question",
        }
        vrti_postgres_sql = _fallback_vrti_postgres_sql(clean_q, canonical_townland)
        vrti_query_meta = {"provider": "query_memory", "model": "approved_sql", "mode": "approved_memory_reuse"}
        query_provenance.update({
            "used_approved_memory": True,
            "reused_memory_id": direct_memory_match.get("id"),
            "direct_memory_reuse": True,
            "strategy": "approved_query_memory",
        })
        ms = int((time.perf_counter() - t0) * 1000)
        yield _sse("progress", stage="contacting_llm", status="completed", label="Contacting LLM",
                   detail=f"Reused approved query memory (similarity {direct_memory_match.get('match_score')})", duration_ms=ms)
    else:
        # ── Phase 5 — Intent router ───────────────────────────────────────────
        # Classify before any LLM call so the right handler is chosen upfront.
        # Fast-lane paths above (Phase 4, verified analysis, memory reuse, high-
        # confidence semantic slot fill) bypass this block entirely.
        try:
            from backend.services.intent_router import (
                classify_intent as _classify_intent_fn,
                ANALYTICAL as _IR_ANALYTICAL,
                RELATIONAL as _IR_RELATIONAL,
                COMPARATIVE as _IR_COMPARATIVE,
            )
            _intent_route = _classify_intent_fn(clean_q, analysis, _semantic_slot_fill)
        except Exception as _ir_exc:
            log.debug("ask_service.intent_router_failed error=%s", _ir_exc)
            _intent_route = "fallback"

        query_provenance["intent_route"] = _intent_route
        # RELATIONAL and COMPARATIVE both require Phase 3 subgraph activation.
        _force_subgraph = _intent_route in {"relational", "comparative"}
        if _intent_route == "comparative":
            query_provenance["phase6_fusion"] = True

        yield _sse(
            "progress", stage="contacting_llm", status="started", label="Contacting LLM",
            detail=f"Route: {_intent_route} — preparing query…",
        )
        try:
            _llm_slot_sql: str | None = None

            # ANALYTICAL route — Phase 2: try LLM slot-fill for structured SQL.
            if _intent_route == "analytical" and _semantic_slot_fill is not None:
                try:
                    _sf_prompt = _build_slot_fill_prompt(clean_q, analysis, townland_resolution)
                    _sf_raw, _sf_meta = _llm_generate(_sf_prompt, purpose="slot_fill", max_tokens=256, temperature=0.0)
                    _sf_parsed = _parse_slot_fill(_sf_raw, clean_q)
                    if _sf_parsed and _sf_parsed.confidence >= 0.70:
                        _llm_slot_sql = _compile_semantic_sql(_sf_parsed, _clearances_count_column())
                        if _llm_slot_sql:
                            sql = _llm_slot_sql
                            llm_meta = _slot_fill_meta(_sf_parsed, sql)
                            llm_meta["llm_provider"] = _sf_meta.get("provider")
                            llm_meta["llm_model"] = _sf_meta.get("model")
                            vrti_postgres_sql = _fallback_vrti_postgres_sql(clean_q, canonical_townland)
                            vrti_query_meta = {"provider": "semantic_layer", "model": "llm_slot_fill", "mode": "semantic_layer"}
                            query_provenance["strategy"] = "semantic_layer_llm"
                            ms = int((time.perf_counter() - t0) * 1000)
                            yield _sse("progress", stage="contacting_llm", status="completed",
                                       label="Contacting LLM",
                                       detail=f"Phase 2 slot-fill [{_intent_route}]: {_sf_parsed.metric} confidence={_sf_parsed.confidence:.2f}",
                                       duration_ms=ms)
                except Exception as _sf_exc:
                    log.debug("ask_service.llm_slot_fill_failed error=%s", _sf_exc)

            if not _llm_slot_sql:
                # RELATIONAL → Phase 3 handles KG context; use Phase 7 free-form SQL.
                # COMPARATIVE → Phase 7 SQL + Phase 3 KG, fused in Phase 6 rewrite.
                # ANALYTICAL (slot-fill miss) / FALLBACK → Phase 7 free-form SQL.
                _few_shot = _p4_memory if _p4_memory else approved_matches
                sql, llm_meta = _generate_sql(
                    clean_q,
                    _ANNOTATED_SCHEMA,
                    canonical_townland,
                    analysis=analysis,
                    approved_examples=_few_shot,
                )
                vrti_postgres_sql, vrti_query_meta = _generate_vrti_postgres_query(clean_q, canonical_townland)
                query_provenance["strategy"] = (
                    "validated_sql_unavailable" if llm_meta.get("mode") == "no_validated_sql" else "llm_sql"
                )
                ms = int((time.perf_counter() - t0) * 1000)
                yield _sse("progress", stage="contacting_llm", status="completed", label="Contacting LLM",
                           detail=(
                               f"Phase 7 [{_intent_route}]: {llm_meta.get('mode')} | "
                               f"VRTI: {vrti_query_meta.get('mode')} | "
                               f"Model: {llm_meta.get('model')}"
                           ),
                           duration_ms=ms)
        except Exception as exc:
            ms = int((time.perf_counter() - t0) * 1000)
            if ASK_ALLOW_HEURISTIC_FALLBACK:
                sql = _fallback_sql(clean_q, canonical_townland)
                llm_meta = {"provider": "local_fallback", "model": "rule_template", "mode": "fallback_rule"}
                query_provenance["strategy"] = "emergency_fallback"
                detail = f"LLM unavailable ({exc}) - fallback template used"
            else:
                sql = _diagnostic_message_sql(
                    "I could not build a validated SQL query for this question from the current schema. "
                    "Please rephrase with a clearer townland, surname, year, ship, record type, or measure."
                )
                llm_meta = {
                    "provider": "validation_guard",
                    "model": "validated_sql_only",
                    "mode": "no_validated_sql",
                    "error": str(exc),
                }
                query_provenance["strategy"] = "validated_sql_unavailable"
                detail = f"LLM unavailable ({exc}) - returning safe guidance instead of guessed SQL"
            vrti_postgres_sql = _fallback_vrti_postgres_sql(clean_q, canonical_townland)
            vrti_query_meta = {"provider": "local_fallback", "model": "rule_template", "mode": "fallback_rule"}
            yield _sse("progress", stage="contacting_llm", status="completed", label="Contacting LLM",
                       detail=detail, duration_ms=ms)

    # ── Stage 2 — Framing Query ───────────────────────────────────────────
    t0 = time.perf_counter()
    yield _sse("progress", stage="framing_query", status="started", label="Framing Query",
               detail="Validating SQL for safety…")
    try:
        safe_sql = _sanitize_and_validate_sql(sql)
    except ValueError:
        if ASK_ALLOW_HEURISTIC_FALLBACK:
            safe_sql = _sanitize_and_validate_sql(_fallback_sql(clean_q, canonical_townland))
        else:
            safe_sql = _sanitize_and_validate_sql(_diagnostic_message_sql(
                "I could not validate a safe SQL query for this question. Please rephrase it with a clearer entity, year, townland, surname, ship, record type, or measure."
            ))
            llm_meta = {
                "provider": "validation_guard",
                "model": "validated_sql_only",
                "mode": "no_validated_sql",
                "error": "sql_validation_failed",
            }
            query_provenance["strategy"] = "validated_sql_unavailable"
    ms = int((time.perf_counter() - t0) * 1000)
    yield _sse("progress", stage="framing_query", status="completed", label="Framing Query",
               detail="Read-only query validated", duration_ms=ms)

    # ── Stage 3 — Querying Database ───────────────────────────────────────
    t0 = time.perf_counter()
    yield _sse("progress", stage="querying_database", status="started", label="Querying SQLite",
               detail="Running SQL against local SQLite database…")
    safe_sql, columns, rows, query_warning, execution_meta = _execute_with_recovery(
        question=clean_q,
        townland_hint=canonical_townland,
        sql=safe_sql,
        approved_examples=approved_matches,
    )
    if query_warning:
        warnings.append(query_warning)
    if execution_meta:
        query_provenance["execution_mode"] = execution_meta.get("mode") or "recovered"
        if execution_meta.get("mode") == "fallback_rule":
            warnings.append("The system had to use an emergency local heuristic because the generated SQL could not be executed safely.")
            query_provenance["strategy"] = "emergency_fallback"
        elif execution_meta.get("mode") == "no_validated_sql":
            warnings.append("No validated SQL query could be produced safely, so the system returned guidance instead of guessing.")
            query_provenance["strategy"] = "validated_sql_unavailable"
        else:
            warnings.append("The system repaired the generated SQL after SQLite reported an execution issue.")
        if llm_meta.get("mode") != "approved_memory_reuse":
            llm_meta = execution_meta
    if direct_memory_match:
        _mark_query_memory_used(int(direct_memory_match.get("id") or 0))
    sql_execution_ms = int((time.perf_counter() - t0) * 1000)
    yield _sse("progress", stage="querying_database", status="completed", label="Querying SQLite",
               detail=f"{len(rows)} row{'s' if len(rows)!=1 else ''} returned · {sql_execution_ms} ms",
               duration_ms=sql_execution_ms)

    # ── Stage 4 — Querying VRTI Graph ─────────────────────────────────────
    t0 = time.perf_counter()
    yield _sse("progress", stage="querying_vrti_graph", status="started", label="Querying VRTI Graph",
               detail="Fetching townland + parish data from VRTI Knowledge Graph…")
    kg_context, kg_warnings = _kg_context(clean_q, canonical_townland, force=True)
    vrti_columns, vrti_rows = _kg_context_to_table(kg_context)
    warnings.extend(kg_warnings)
    ms = int((time.perf_counter() - t0) * 1000)
    parish_count = (kg_context or {}).get("parish_count")
    vrti_detail = f"{len(vrti_rows)} townland(s) enriched"
    if parish_count:
        vrti_detail += f" | {parish_count} Wicklow parishes"
    yield _sse("progress", stage="querying_vrti_graph", status="completed", label="Querying VRTI Graph",
               detail=vrti_detail, duration_ms=ms)

    # ── Phase 3 — Subgraph retrieval (relational / multi-hop / heritage) ─────
    # Activates when Phase 5 router classified the question as RELATIONAL or
    # COMPARATIVE (_force_subgraph=True), or when is_subgraph_question() detects
    # relational/heritage signals independently (existing fast-lane paths).
    # Core Rule 1: never used to answer count/aggregate questions.
    _phase3_result = None
    try:
        from backend.services.subgraph_engine import (
            is_subgraph_question as _is_subgraph_q,
            retrieve_subgraph as _retrieve_subgraph,
        )
        if _force_subgraph or _is_subgraph_q(clean_q, analysis, _semantic_slot_fill):
            t0 = time.perf_counter()
            yield _sse(
                "progress", stage="querying_subgraph", status="started",
                label="Subgraph Retrieval",
                detail="Expanding knowledge graph neighbourhood for relational context…",
            )
            _phase3_result = _retrieve_subgraph(
                clean_q, analysis, townland_resolution,
                sources=("vrti", "graphdb"),
            )
            if _phase3_result and _phase3_result.linearized:
                if kg_context is None:
                    kg_context = {}
                kg_context["subgraph_linearized"] = _phase3_result.linearized
            ms = int((time.perf_counter() - t0) * 1000)
            _p3_src = ", ".join(_phase3_result.sources_used) if _phase3_result else "none"
            _p3_detail = (
                f"Subgraph from {_p3_src} · "
                f"{_phase3_result.k_hops} hop(s)"
                + (", pruned" if _phase3_result and _phase3_result.pruned else "")
                + f" · type: {_phase3_result.question_type}"
            ) if _phase3_result else "No subgraph retrieved"
            yield _sse(
                "progress", stage="querying_subgraph", status="completed",
                label="Subgraph Retrieval", detail=_p3_detail, duration_ms=ms,
            )
    except Exception as _p3_exc:
        log.debug("ask_service.phase3_failed error=%s", _p3_exc)

    # ── Phase 6 — Fusion annotation for COMPARATIVE questions ────────────────
    # When the router flagged a COMPARATIVE intent and Phase 3 produced subgraph
    # context, annotate kg_context so the LLM rewrite synthesises both sources.
    if _intent_route == "comparative" and _phase3_result and _phase3_result.linearized:
        if kg_context is None:
            kg_context = {}
        kg_context["phase6_fusion_note"] = (
            "This is a comparative question. The SQLite estate records provide counts "
            "and statistics; the VRTI knowledge graph subgraph provides qualitative and "
            "relational context. Synthesise both in your answer."
        )

    # ── Stage 4.5 — Querying GraphDB (RDF/KG comparison) ─────────────────
    from backend.integrations import graphdb_sparql as _gdb
    graph_comparison: dict[str, Any] = {
        "sparql_query": "",
        "sql_query": safe_sql,
        "columns": [],
        "rows": [],
        "row_count": 0,
        "graphdb_available": False,
        "triple_count": -1,
        "data_loaded": False,
        "error": None,
        "setup_hint": None,
        "timing": {
            "sql_ms": sql_execution_ms,
            "sparql_gen_ms": 0,
            "graphdb_ms": 0,
        },
    }
    if ActiveConfig.GRAPHDB_ENABLED:
        t0_stage = time.perf_counter()
        yield _sse("progress", stage="querying_graphdb", status="started", label="Querying GraphDB",
                   detail="Generating SPARQL query via LLM…")

        # Sub-step 1: LLM generates the SPARQL
        t0 = time.perf_counter()
        sparql_text = ""
        try:
            sparql_text, _sparql_meta = _generate_graphdb_sparql(clean_q, safe_sql)
            graph_comparison["sparql_query"] = sparql_text
        except Exception as exc:
            graph_comparison["error"] = f"SPARQL generation failed: {exc}"
            log.warning("ask_service.graphdb_sparql_gen_failed error=%s", exc)
        graph_comparison["timing"]["sparql_gen_ms"] = int((time.perf_counter() - t0) * 1000)

        # Sub-step 2: probe + execute against GraphDB
        if sparql_text:
            yield _sse("progress", stage="querying_graphdb", status="started", label="Querying GraphDB",
                       detail="Executing SPARQL against local RDF graph…")
            t0 = time.perf_counter()
            try:
                graphdb_ok = _gdb.probe()
                graph_comparison["graphdb_available"] = graphdb_ok
                if graphdb_ok:
                    tc = _gdb.triple_count()
                    graph_comparison["triple_count"] = tc
                    graph_comparison["data_loaded"] = tc > 0
                    if tc == 0:
                        graph_comparison["setup_hint"] = (
                            "GraphDB is running but the repository is empty. "
                            "Load data with: python3 scripts/rdf_uplift.py --import"
                        )
                    g_cols, g_rows = _gdb.query(sparql_text)
                    graph_comparison["columns"] = g_cols
                    graph_comparison["rows"] = g_rows
                    graph_comparison["row_count"] = len(g_rows)
            except Exception as exc:
                graph_comparison["error"] = str(exc)
                log.warning("ask_service.graphdb_execute_failed error=%s", exc)
            graph_comparison["timing"]["graphdb_ms"] = int((time.perf_counter() - t0) * 1000)

        # Generate mismatch explanation when GraphDB has data but results differ.
        # Checks row-count difference AND single-row value difference (e.g. two
        # COUNT queries returning 1 row each but with different totals).
        graph_comparison["mismatch_explanation"] = None
        _gdb_available = graph_comparison["graphdb_available"]
        _gdb_loaded    = graph_comparison["data_loaded"]
        if _gdb_available and _gdb_loaded and sparql_text:
            _sql_n  = len(rows)
            _gdb_n  = graph_comparison["row_count"]
            _row_count_differs = _sql_n != _gdb_n
            _value_differs = False
            if not _row_count_differs and _sql_n == 1 and _gdb_n == 1:
                _sv = _first_numeric(rows[0])
                _gv = _first_numeric(graph_comparison["rows"][0])
                _value_differs = (
                    _sv is not None and _gv is not None and _sv != _gv
                )
            if _row_count_differs or _value_differs:
                graph_comparison["mismatch_explanation"] = _explain_result_mismatch(
                    question=clean_q,
                    sql=safe_sql,
                    sparql=sparql_text,
                    sql_rows=rows,
                    sparql_rows=graph_comparison["rows"],
                )

        total_ms = int((time.perf_counter() - t0_stage) * 1000)
        tc = graph_comparison["triple_count"]
        if graph_comparison["graphdb_available"]:
            tc_label = f" · {tc:,} triples loaded" if tc >= 0 else ""
            gdb_detail = (
                f"{graph_comparison['row_count']} row(s){tc_label} · "
                f"SPARQL gen {graph_comparison['timing']['sparql_gen_ms']} ms · "
                f"query {graph_comparison['timing']['graphdb_ms']} ms"
            )
        else:
            gdb_detail = (
                "GraphDB offline — SPARQL generated, not executed"
                if sparql_text
                else "SPARQL generation failed"
            )
        yield _sse("progress", stage="querying_graphdb", status="completed", label="Querying GraphDB",
                   detail=gdb_detail, duration_ms=total_ms)

    # ── Phase 6 — Fusion & reconciliation ────────────────────────────────────
    # Align SQLite, GraphDB, and VRTI results on the resolved entity; detect
    # agreement vs. discrepancy on shared metrics; annotate rows with source
    # provenance. Directly serves the dissertation objective of comparing the
    # purpose-built co: estate graph against VRTI's general-purpose place graph.
    t0 = time.perf_counter()
    yield _sse("progress", stage="querying_fusion", status="started",
               label="Reconciling Sources",
               detail="Aligning SQLite, GraphDB, and VRTI results on resolved entity…")
    fusion_result = _fuse_lanes(
        sqlite_rows=rows,
        sqlite_columns=columns,
        graphdb_rows=graph_comparison.get("rows", []),
        graphdb_columns=graph_comparison.get("columns", []),
        vrti_rows=vrti_rows,
        canonical_townland=canonical_townland,
        entity_resolution=townland_resolution.get("entity_resolution"),
        question=clean_q,
    )
    if fusion_result["discrepancies"]:
        if kg_context is None:
            kg_context = {}
        kg_context["phase6_discrepancies"] = fusion_result["discrepancies"]
        kg_context["phase6_fusion_text"] = fusion_result["fusion_text"]
    ms = int((time.perf_counter() - t0) * 1000)
    if fusion_result["discrepancy_count"] or fusion_result["agreement_count"]:
        _f_detail = (
            f"{fusion_result['discrepancy_count']} discrepancy(ies) · "
            f"{fusion_result['agreement_count']} agreement(s)"
        )
    else:
        _f_detail = "No numeric overlap between sources to compare"
    yield _sse("progress", stage="querying_fusion", status="completed",
               label="Reconciling Sources", detail=_f_detail, duration_ms=ms)

    # ── Stage 5 — Preparing Output ────────────────────────────────────────
    t0 = time.perf_counter()
    yield _sse("progress", stage="preparing_output", status="started", label="Preparing Output",
               detail="Building data tables, LLM rewrite, and PDF report...")

    availability = _build_availability_payload(
        question=clean_q,
        analysis=analysis,
        columns=columns,
        rows=rows,
        townland_resolution=townland_resolution,
    )
    related_insights = _build_related_insights(
        question=clean_q,
        analysis=analysis,
        rows=rows,
        townland_norm=canonical_townland,
    )
    chart_spec = _build_chart_spec(
        question=clean_q,
        columns=columns,
        rows=rows,
        availability=availability,
        chart_hint=chart_hint,
    )
    actual_answer = _build_answer_text(
        clean_q,
        columns,
        rows,
        canonical_townland,
        kg_context,
        availability=availability,
    )
    summary_block = _build_structured_summary(
        question=clean_q, local_columns=columns, local_rows=rows,
        vrti_columns=vrti_columns, vrti_rows=vrti_rows, kg_context=kg_context,
        availability=availability, related_insights=related_insights,
    )
    supporting_context = _build_supporting_context(
        question=clean_q,
        townland_norm=canonical_townland,
        townland_resolution=townland_resolution,
        primary_columns=columns,
        primary_rows=rows,
        kg_context=kg_context,
        related_insights=related_insights,
    )
    llm_data_context = _build_llm_data_context(
        local_columns=columns, local_rows=rows,
        vrti_columns=vrti_columns, vrti_rows=vrti_rows,
    )
    llm_rephrased_answer: str | None = None
    llm_rewrite_meta: dict[str, Any] = {
        "provider": "none",
        "model": None,
        "mode": "not_requested",
    }
    try:
        llm_rephrased_answer, llm_rewrite_meta = _generate_rephrased_answer(
            question=clean_q,
            actual_answer=actual_answer,
            summary_block=summary_block,
            data_context=llm_data_context,
            supporting_context=supporting_context,
            kg_context=kg_context,
        )
        if llm_rephrased_answer:
            summary_block["llm_rephrased_text"] = llm_rephrased_answer
    except Exception as exc:
        llm_rewrite_meta = {
            "provider": "unavailable",
            "model": None,
            "mode": "not_generated",
            "error": str(exc),
        }
        warnings.append(f"LLM rewrite unavailable: {exc}")

    structured_output = {
        "queries": {"local_sqlite_query": safe_sql, "vrti_postgresql_query": vrti_postgres_sql},
        "processed_tables": {
            "local_database": {"columns": columns, "rows": rows, "row_count": len(rows)},
            "vrti_graph":     {"columns": vrti_columns, "rows": vrti_rows, "row_count": len(vrti_rows)},
        },
        "summary": summary_block,
        "supporting_context": _supporting_context_for_display(supporting_context),
        "availability": availability,
        "related_insights": related_insights,
        "chart": chart_spec,
        "query_provenance": query_provenance,
        "discrepancies": fusion_result["discrepancies"],
        "fusion": {
            "discrepancy_count": fusion_result["discrepancy_count"],
            "agreement_count": fusion_result["agreement_count"],
            "fusion_text": fusion_result["fusion_text"],
        },
    }

    pdf_path = _write_pdf_report(
        question=clean_q, answer=actual_answer, sql=safe_sql, columns=columns, rows=rows,
        llm_meta=llm_meta, kg_context=kg_context, include_sql=True,
        vrti_postgres_sql=vrti_postgres_sql, vrti_columns=vrti_columns,
        vrti_rows=vrti_rows, summary_block=summary_block,
        llm_rephrased_answer=llm_rephrased_answer,
        llm_rewrite_meta=llm_rewrite_meta,
    )

    if llm_meta.get("mode") == "fallback_rule":
        warnings.append("LLM SQL generation unavailable - fallback SQL template used.")
    elif llm_meta.get("mode") == "no_validated_sql":
        warnings.append("The system did not find a validated SQL query for this request and returned safe guidance instead.")

    warnings.extend(_null_rate_warnings(columns, rows))

    ms = int((time.perf_counter() - t0) * 1000)
    yield _sse("progress", stage="preparing_output", status="completed", label="Preparing Output",
               detail="PDF generated", duration_ms=ms)

    # ── Final result ──────────────────────────────────────────────────────
    payload: dict[str, Any] = {
        "question": clean_q,
        "answer": actual_answer,
        "actual_answer": actual_answer,
        "llm_rephrased_answer": llm_rephrased_answer,
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "llm": llm_meta,
        "llm_rewrite": llm_rewrite_meta,
        "vrti_query_generation": vrti_query_meta,
        "townland_context": canonical_townland,
        "townland_resolution": townland_resolution,
        # Phase 1 — shared entity resolution (sql_id + kg_uri available to all lanes)
        "entity_resolution": townland_resolution.get("entity_resolution"),
        "kg_context": kg_context,
        "availability": availability,
        "related_insights": related_insights,
        "chart": chart_spec,
        "query_provenance": query_provenance,
        "suggestions": availability.get("suggestions", []),
        "structured_output": structured_output,
        "pdf_url": f"/api/ask/pdf/{pdf_path.name}",
        "warnings": warnings,
        "source_tables": _extract_tables(safe_sql) if safe_sql else [],
        "graph_comparison": graph_comparison,
        # Phase 6 — fusion & reconciliation
        "discrepancies": fusion_result["discrepancies"],
        "fusion": {
            "discrepancy_count": fusion_result["discrepancy_count"],
            "agreement_count": fusion_result["agreement_count"],
            "entity_label": fusion_result["entity_label"],
            "kg_uri": fusion_result["kg_uri"],
            "fusion_text": fusion_result["fusion_text"],
            "source_provenance": fusion_result["source_provenance"],
        },
        "subgraph_context": {
            "linearized":     _phase3_result.linearized,
            "hierarchy":      _phase3_result.hierarchy,
            "siblings":       _phase3_result.siblings,
            "external_links": _phase3_result.external_links,
            "sources_used":   _phase3_result.sources_used,
            "question_type":  _phase3_result.question_type,
            "k_hops":         _phase3_result.k_hops,
            "pruned":         _phase3_result.pruned,
        } if _phase3_result else None,
    }
    if include_sql:
        payload["sql"] = safe_sql

    yield _sse("result", **payload)


# ─────────────────────────────────────────────────────────────────────────────
# LLM status / health check
# ─────────────────────────────────────────────────────────────────────────────

def check_llm_status() -> dict[str, Any]:
    """Return the currently usable LLM provider for the Ask page."""
    provider = (ASK_LLM_PROVIDER or "auto").lower()

    if provider in {"off", "none", "disabled"}:
        return {
            "available": False,
            "provider": "disabled",
            "configured_provider": provider,
            "hint": "LLM generation is disabled by ASK_LLM_PROVIDER.",
        }

    if provider == "openrouter":
        status = _openrouter_status()
        status["configured_provider"] = provider
        return status

    if provider == "ollama":
        status = check_ollama_status()
        status["configured_provider"] = provider
        return status

    if OPENROUTER_API_KEY:
        status = _openrouter_status()
        status["configured_provider"] = "auto"
        return status

    try:
        status = check_ollama_status()
        status["configured_provider"] = "auto"
        if status.get("available"):
            status["hint"] = (
                "Using local Ollama fallback. Set OPENROUTER_API_KEY to use OpenRouter."
            )
            return status
    except Exception:
        pass

    return {
        "available": False,
        "provider": "openrouter",
        "configured_provider": "auto",
        "has_api_key": False,
        "active_model": OPENROUTER_MODEL,
        "models": _candidate_openrouter_models()[:8],
        "base_url": OPENROUTER_BASE_URL,
        "hint": "Set OPENROUTER_API_KEY to enable the LLM rewrite and AI-enhanced SQL path.",
    }


def _openrouter_status() -> dict[str, Any]:
    if not OPENROUTER_API_KEY:
        return {
            "available": False,
            "provider": "openrouter",
            "connection_state": "missing_key",
            "has_api_key": False,
            "active_model": OPENROUTER_MODEL,
            "models": _candidate_openrouter_models()[:8],
            "base_url": OPENROUTER_BASE_URL,
            "hint": "OPENROUTER_API_KEY is missing. Add it before starting Flask.",
        }

    now = time.time()
    with _openrouter_status_cache_lock:
        cached = _OPENROUTER_STATUS_CACHE.get("status")
        if cached and now < float(_OPENROUTER_STATUS_CACHE.get("expires_at") or 0):
            return {**cached, "cached": True}

    def cache_status(status: dict[str, Any]) -> dict[str, Any]:
        with _openrouter_status_cache_lock:
            _OPENROUTER_STATUS_CACHE["status"] = dict(status)
            _OPENROUTER_STATUS_CACHE["expires_at"] = time.time() + OPENROUTER_STATUS_CACHE_TTL
        return status

    base_status = {
        "available": True,
        "provider": "openrouter",
        "connection_state": "connected",
        "has_api_key": True,
        "active_model": OPENROUTER_MODEL,
        "models": _candidate_openrouter_models()[:8],
        "base_url": OPENROUTER_BASE_URL,
    }
    try:
        resp = requests.get(
            f"{OPENROUTER_BASE_URL}/key",
            headers=_openrouter_headers(),
            timeout=(min(OPENROUTER_CONNECT_TIMEOUT, 3), OPENROUTER_STATUS_TIMEOUT),
        )
        if resp.status_code in {401, 403}:
            return cache_status({
                **base_status,
                "available": False,
                "connection_state": "rejected",
                "hint": "OpenRouter rejected the API key. Check or rotate OPENROUTER_API_KEY.",
            })
        if resp.status_code == 429:
            return cache_status({
                **base_status,
                "available": False,
                "connection_state": "rate_limited",
                "hint": "OpenRouter is reachable, but the key is currently rate limited.",
            })
        resp.raise_for_status()
        data = resp.json().get("data") or {}
        is_disabled = bool(data.get("disabled"))
        remaining = data.get("limit_remaining")
        usage_hint = ""
        if remaining is not None:
            usage_hint = f" Remaining limit: {remaining}."
        return cache_status({
            **base_status,
            "available": not is_disabled,
            "connection_state": "disabled" if is_disabled else "connected",
            "is_free_tier": data.get("is_free_tier"),
            "limit_remaining": remaining,
            "hint": (
                "OpenRouter key is connected."
                if not is_disabled
                else "OpenRouter key exists but is disabled."
            ) + usage_hint,
        })
    except Exception as exc:
        log.warning("ask_service.openrouter_status_failed error=%s", exc)
        friendly_hint, technical_detail, issue_code = _friendly_openrouter_connection_issue(exc)
        return cache_status({
            **base_status,
            "available": False,
            "connection_state": issue_code,
            "hint": friendly_hint,
            "detail": technical_detail,
        })


def _friendly_openrouter_connection_issue(exc: Exception) -> tuple[str, str, str]:
    detail = str(exc)
    lower = detail.lower()
    if any(token in lower for token in ["nameresolutionerror", "failed to resolve", "nodename nor servname"]):
        return (
            "OpenRouter is configured, but this server cannot currently reach OpenRouter. "
            "The Ask page can still show the database answer, but the LLM rewrite is temporarily unavailable.",
            detail,
            "dns_unreachable",
        )
    if "timed out" in lower or "timeout" in lower:
        return (
            "OpenRouter is configured, but the server connection to OpenRouter timed out. "
            "The Ask page can still show the database answer, but the LLM rewrite is temporarily unavailable.",
            detail,
            "timeout",
        )
    return (
        "OpenRouter is configured, but the server could not complete the live connection check. "
        "The Ask page can still show the database answer, but the LLM rewrite is temporarily unavailable.",
        detail,
        "unreachable",
    )


def check_ollama_status() -> dict[str, Any]:
    """Return a structured status dict for the /ollama-status endpoint."""
    try:
        models = _ollama_installed_models()
        if not models:
            return {
                "available": True,
                "provider": "ollama",
                "has_models": False,
                "models": [],
                "base_url": OLLAMA_BASE_URL,
                "hint": "Ollama is running but no models are installed. Run: ollama pull llama3.2:latest",
            }
        return {
            "available": True,
            "provider": "ollama",
            "has_models": True,
            "models": models,
            "active_model": _resolve_ollama_model(),
            "base_url": OLLAMA_BASE_URL,
        }
    except requests.exceptions.ConnectionError:
        return {
            "available": False,
            "provider": "ollama",
            "has_models": False,
            "models": [],
            "base_url": OLLAMA_BASE_URL,
            "hint": (
                f"Cannot connect to Ollama at {OLLAMA_BASE_URL}. "
                "Make sure Ollama is installed and running: ollama serve"
            ),
        }
    except Exception as exc:
        return {
            "available": False,
            "provider": "ollama",
            "has_models": False,
            "models": [],
            "base_url": OLLAMA_BASE_URL,
            "hint": str(exc),
        }


# ─────────────────────────────────────────────────────────────────────────────
# CSV seed
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_unified_table_seeded() -> None:
    if not UNIFIED_CSV_PATH.exists():
        raise RuntimeError(f"Unified CSV not found: {UNIFIED_CSV_PATH}")

    fingerprint = (
        f"{UNIFIED_SEED_SCHEMA_VERSION}:"
        f"{UNIFIED_CSV_PATH.stat().st_mtime_ns}:{UNIFIED_CSV_PATH.stat().st_size}"
    )
    from backend.repositories import refresh_state_repository
    state = refresh_state_repository.get(UNIFIED_SEED_KEY, stale_after_days=36500)

    conn = get_db_conn()
    try:
        schema_changed = _ensure_unified_record_schema(conn)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_unified_townland_norm ON unified_record(townland_norm)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_unified_has_emigration ON unified_record(has_emigration_record)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_unified_has_eviction ON unified_record(has_eviction_record)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_unified_has_tenancy ON unified_record(has_tenancy_record)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_unified_record_id ON unified_record(record_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_unified_year ON unified_record(year)")
        conn.commit()

        existing_count = conn.execute("SELECT COUNT(*) FROM unified_record").fetchone()[0]
        needs_reload = schema_changed or not state or state.query_hash != fingerprint or existing_count <= 0
        if not needs_reload:
            return

        conn.execute("DELETE FROM unified_record")
        batch: list[tuple] = []
        inserted = 0

        with open(UNIFIED_CSV_PATH, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                record_id = _clean_text(row.get("record_id")) or _clean_text(row.get("unique_id_no"))
                townland  = _clean_text(row.get("townland"))
                batch.append((
                    record_id, _clean_text(row.get("unique_id_no")),
                    _to_int(row.get("year")), _clean_text(row.get("month")),
                    _clean_text(row.get("nli_ref")), _clean_text(row.get("court_session")),
                    _clean_text(row.get("vol")) or _clean_text(row.get("volume")),
                    _clean_text(row.get("page")), _clean_text(row.get("estate_reference_no")),
                    _clean_text(row.get("townland_as_shown")),
                    _clean_text(row.get("townland_official_name")),
                    _clean_text(row.get("surname")), _clean_text(row.get("forename")),
                    _clean_text(row.get("canonical_name")),
                    townland, _norm_townland(townland),
                    _clean_text(row.get("parish")), _clean_text(row.get("estate")),
                    _clean_text(row.get("occupation")), _to_int(row.get("age")),
                    _clean_text(row.get("gender")),
                    _clean_text(row.get("role")), _clean_text(row.get("legal_action")),
                    _to_float(row.get("acres")), _to_float(row.get("acres_2")),
                    _to_float(row.get("acres_irish")), _to_float(row.get("acres_english")),
                    _best_holding_acres(row),
                    _to_int(row.get("sons")), _to_int(row.get("daughters")),
                    _to_int(row.get("servants_male")), _to_int(row.get("servants_female")),
                    _to_int(row.get("other_males_in_household")), _to_int(row.get("other_famales_in_household")),
                    _derived_children_count(row), _derived_family_size_estimate(row),
                    _to_int(row.get("age_head_of_household")),
                    _to_int(row.get("age_wife_widow_of_head_of_household")),
                    _clean_text(row.get("relationship_to_head_of_household")),
                    _to_float(row.get("rent_owed")), _to_float(row.get("arrears")),
                    _clean_text(row.get("chief_tenant_surname_original")),
                    _clean_text(row.get("chief_tenant_forename_original")),
                    _clean_text(row.get("under_tenant_surname_original")),
                    _clean_text(row.get("under_tenant_forename_original")),
                    _clean_text(row.get("chief_tenant_surname")),
                    _clean_text(row.get("chief_tenant_forename")),
                    _clean_text(row.get("under_tenant_surname")),
                    _clean_text(row.get("under_tenant_forename")),
                    _clean_text(row.get("ship_name")) or _clean_text(row.get("name_of_ship")),
                    _clean_text(row.get("departure")) or _clean_text(row.get("place_and_date_of_departure")),
                    _clean_text(row.get("arrival"))   or _clean_text(row.get("place_and_date_of_arrival")),
                    _clean_text(row.get("household_list")) or _clean_text(row.get("household_list_in_emigration_records")),
                    _clean_text(row.get("holding_on_fitzw_estate")),
                    _clean_text(row.get("holding_on_estate")),
                    _clean_text(row.get("mountains_in_common")),
                    _clean_text(row.get("comments")),
                    _clean_text(row.get("family_key")),
                    _derived_is_widow(row),
                    _derived_is_canada_destination(
                        _clean_text(row.get("arrival")) or _clean_text(row.get("place_and_date_of_arrival"))
                    ),
                    _to_bool_int(row.get("has_emigration_record")),
                    _to_bool_int(row.get("has_eviction_record")),
                    _to_bool_int(row.get("has_tenancy_record")),
                ))
                if len(batch) >= 1000:
                    _bulk_insert(conn, batch); inserted += len(batch); batch = []

        if batch:
            _bulk_insert(conn, batch); inserted += len(batch)

        conn.commit()
        refresh_state_repository.upsert(
            UNIFIED_SEED_KEY, source="csv_seed", query_hash=fingerprint, record_count=inserted,
        )
        log.info("ask_service.unified_seeded rows=%d", inserted)
    finally:
        conn.close()


def _ensure_unified_record_schema(conn) -> bool:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS unified_record (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            record_id TEXT,
            unique_id_no TEXT,
            year INTEGER,
            month TEXT
        )"""
    )
    required_columns = {
        "nli_ref": "TEXT",
        "court_session": "TEXT",
        "vol": "TEXT",
        "page": "TEXT",
        "estate_reference_no": "TEXT",
        "estate": "TEXT",
        "townland_as_shown": "TEXT",
        "townland_official_name": "TEXT",
        "surname": "TEXT",
        "forename": "TEXT",
        "canonical_name": "TEXT",
        "townland": "TEXT",
        "townland_norm": "TEXT",
        "parish": "TEXT",
        "occupation": "TEXT",
        "age": "INTEGER",
        "gender": "TEXT",
        "role": "TEXT",
        "legal_action": "TEXT",
        "acres": "REAL",
        "acres_2": "REAL",
        "acres_irish": "REAL",
        "acres_english": "REAL",
        "holding_acres": "REAL",
        "sons": "INTEGER",
        "daughters": "INTEGER",
        "servants_male": "INTEGER",
        "servants_female": "INTEGER",
        "other_males_in_household": "INTEGER",
        "other_famales_in_household": "INTEGER",
        "children_count": "INTEGER",
        "family_size_estimate": "INTEGER",
        "age_head_of_household": "INTEGER",
        "age_wife_widow_of_head_of_household": "INTEGER",
        "relationship_to_head_of_household": "TEXT",
        "rent_owed": "REAL",
        "arrears": "REAL",
        "chief_tenant_surname_original": "TEXT",
        "chief_tenant_forename_original": "TEXT",
        "under_tenant_surname_original": "TEXT",
        "under_tenant_forename_original": "TEXT",
        "chief_tenant_surname": "TEXT",
        "chief_tenant_forename": "TEXT",
        "under_tenant_surname": "TEXT",
        "under_tenant_forename": "TEXT",
        "ship_name": "TEXT",
        "departure": "TEXT",
        "arrival": "TEXT",
        "household_list": "TEXT",
        "holding_on_fitzw_estate": "TEXT",
        "holding_on_estate": "TEXT",
        "mountains_in_common": "TEXT",
        "comments": "TEXT",
        "family_key": "TEXT",
        "is_widow": "INTEGER DEFAULT 0",
        "is_canada_destination": "INTEGER DEFAULT 0",
        "has_emigration_record": "INTEGER DEFAULT 0",
        "has_eviction_record": "INTEGER DEFAULT 0",
        "has_tenancy_record": "INTEGER DEFAULT 0",
    }
    rows = conn.execute("PRAGMA table_info(unified_record)").fetchall()
    existing = {row["name"] for row in rows}
    changed = False
    for column, ddl in required_columns.items():
        if column in existing:
            continue
        conn.execute(f"ALTER TABLE unified_record ADD COLUMN {column} {ddl}")
        changed = True
    return changed


def _bulk_insert(conn, batch: list[tuple]) -> None:
    conn.executemany(
        """INSERT INTO unified_record (
            record_id,unique_id_no,year,month,nli_ref,court_session,vol,page,estate_reference_no,
            townland_as_shown,townland_official_name,surname,forename,canonical_name,
            townland,townland_norm,parish,estate,occupation,age,gender,role,legal_action,
            acres,acres_2,acres_irish,acres_english,holding_acres,
            sons,daughters,servants_male,servants_female,other_males_in_household,other_famales_in_household,
            children_count,family_size_estimate,age_head_of_household,age_wife_widow_of_head_of_household,
            relationship_to_head_of_household,rent_owed,arrears,
            chief_tenant_surname_original,chief_tenant_forename_original,
            under_tenant_surname_original,under_tenant_forename_original,
            chief_tenant_surname,chief_tenant_forename,under_tenant_surname,under_tenant_forename,
            ship_name,departure,arrival,household_list,
            holding_on_fitzw_estate,holding_on_estate,mountains_in_common,comments,family_key,
            is_widow,is_canada_destination,
            has_emigration_record,has_eviction_record,has_tenancy_record
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        batch,
    )


def _ensure_heritage_feature_seeded() -> None:
    files = [path for path in (HOLYWELLS_GEOJSON_PATH, ASI_GEOJSON_PATH) if path.exists()]
    if not files:
        return

    fingerprint = HERITAGE_SEED_SCHEMA_VERSION + "|" + "|".join(
        f"{path.name}:{path.stat().st_mtime_ns}:{path.stat().st_size}" for path in files
    )
    from backend.repositories import refresh_state_repository
    state = refresh_state_repository.get(HERITAGE_SEED_KEY, stale_after_days=36500)

    conn = get_db_conn()
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS heritage_feature (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_dataset TEXT,
                feature_group TEXT,
                monument_class TEXT,
                townland_raw TEXT,
                townland_norm TEXT,
                feature_name TEXT,
                source_link TEXT
            )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_heritage_townland_norm ON heritage_feature(townland_norm)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_heritage_feature_group ON heritage_feature(feature_group)")
        conn.commit()

        existing_count = conn.execute("SELECT COUNT(*) FROM heritage_feature").fetchone()[0]
        if state and state.query_hash == fingerprint and existing_count > 0:
            return

        conn.execute("DELETE FROM heritage_feature")
        batch: list[tuple[str | None, ...]] = []

        if HOLYWELLS_GEOJSON_PATH.exists():
            data = json.loads(HOLYWELLS_GEOJSON_PATH.read_text(encoding="utf-8"))
            for feature in data.get("features", []):
                props = feature.get("properties") or {}
                townland_raw = _clean_text(props.get("townland"))
                batch.append((
                    "holywells",
                    "holy_well",
                    _clean_text(props.get("monument_type")) or "Ritual site - holy well",
                    townland_raw,
                    _heritage_townland_norm(townland_raw),
                    _clean_text(props.get("latest_edit")) or _clean_text(props.get("id")),
                    _clean_text(props.get("source_link")),
                ))

        if ASI_GEOJSON_PATH.exists():
            data = json.loads(ASI_GEOJSON_PATH.read_text(encoding="utf-8"))
            for feature in data.get("features", []):
                props = feature.get("properties") or {}
                monument_class = _clean_text(props.get("monument_class"))
                if not _is_ring_fort_class(monument_class):
                    continue
                townland_raw = _clean_text(props.get("townland"))
                batch.append((
                    "asi",
                    "ring_fort",
                    monument_class,
                    townland_raw,
                    _heritage_townland_norm(townland_raw),
                    _clean_text(props.get("smrs")) or _clean_text(props.get("id")),
                    _clean_text(props.get("source_link")),
                ))

        if batch:
            conn.executemany(
                """
                INSERT INTO heritage_feature (
                    source_dataset, feature_group, monument_class, townland_raw, townland_norm, feature_name, source_link
                ) VALUES (?,?,?,?,?,?,?)
                """,
                batch,
            )
        conn.commit()
        refresh_state_repository.upsert(
            HERITAGE_SEED_KEY, source="geojson_seed", query_hash=fingerprint, record_count=len(batch),
        )
    finally:
        conn.close()


def _template_notes(template: dict[str, Any] | None) -> list[str]:
    if not template:
        return []
    notes = template.get("warnings") or template.get("warning") or []
    if isinstance(notes, str):
        return [notes]
    return [str(note) for note in notes if note]


def _question_data_coverage_warnings(question: str) -> list[str]:
    q = (question or "").lower()
    warnings: list[str] = []
    if "1821" in q and any(token in q for token in ["population", "census", "trend"]):
        warnings.append(
            "The Ask census table begins in 1841, so any population trend answer uses 1841–1861 rather than 1821–1861."
        )
    if any(token in q for token in ["age", " old", "years old", "elderly", "children", "child"]):
        warnings.append(
            "Age data is sparse in the Coolattin records — many entries omit individual ages, "
            "so age-based counts may understate the true figures."
        )
    if any(token in q for token in ["gender", "male", "female", "men", "women", "sex"]):
        warnings.append(
            "Gender is recorded for most emigration and tenancy entries but may be absent for "
            "eviction-only records; household-member counts can differ from individual counts."
        )
    if any(token in q for token in ["ship", "vessel", "voyage", "sailed", "aboard"]):
        warnings.append(
            "Ship name is recorded for most emigration entries but may be blank for early sailings "
            "(pre-1847) or records transcribed without a departure document."
        )
    if any(token in q for token in ["religion", "catholic", "protestant", "church", "faith", "denomination"]):
        warnings.append(
            "Religious denomination is not captured in the Coolattin estate records — "
            "no religion-based analysis is possible from this dataset."
        )
    return warnings


# Key columns that, when >60% null across result rows, indicate sparse data
_SPARSE_FIELD_LABELS: dict[str, str] = {
    "forename":     "first name",
    "surname":      "surname",
    "year":         "year",
    "townland":     "townland",
    "ship_name":    "ship name",
    "destination":  "destination",
    "occupation":   "occupation",
    "county":       "county",
    "age":          "age",
}
_SPARSE_MIN_ROWS = 5     # only warn when result has enough rows to be meaningful
_SPARSE_THRESHOLD = 0.60  # 60% null → warn


def _null_rate_warnings(columns: list[str], rows: list[dict]) -> list[str]:
    """
    Return user-facing warnings for result columns that are mostly null.
    Only fires when the result has >= _SPARSE_MIN_ROWS rows.
    """
    if not rows or len(rows) < _SPARSE_MIN_ROWS:
        return []
    warnings: list[str] = []
    col_set = set(c.lower() for c in columns)
    for col_key, col_label in _SPARSE_FIELD_LABELS.items():
        # Find the actual column name (case-insensitive match)
        actual = next((c for c in columns if c.lower() == col_key), None)
        if actual is None:
            continue
        null_count = sum(
            1 for row in rows
            if row.get(actual) is None or str(row.get(actual, "")).strip() in {"", "None", "null"}
        )
        rate = null_count / len(rows)
        if rate >= _SPARSE_THRESHOLD:
            pct = int(rate * 100)
            warnings.append(
                f"Sparse field — '{col_label}' is empty in {pct}% of the result rows. "
                "This field may not be recorded for all historical entries."
            )
    return warnings


def _diagnostic_message_sql(message: str) -> str:
    clean = " ".join((message or "").split()).strip() or "No validated SQL query could be produced."
    return f"SELECT '{_sql_escape(clean)}' AS message"


def _clean_message_result_text(text: str | None) -> str:
    clean = " ".join(str(text or "").split()).strip()
    clean = re.sub(r"\s*diagnostic\.?\s*$", "", clean, flags=re.IGNORECASE).strip()
    return clean


# ─────────────────────────────────────────────────────────────────────────────
# LLM SQL generation
# ─────────────────────────────────────────────────────────────────────────────

def _generate_sql(
    question: str,
    schema: str,
    townland_hint: str | None,
    analysis: dict[str, Any] | None = None,
    approved_examples: list[dict[str, Any]] | None = None,
) -> tuple[str, dict]:
    analysis = analysis or _analyse_question(question, townland_hint)
    prompt = _build_sql_prompt(question, schema, analysis, approved_examples or [])
    fallback_sql = _fallback_sql(question, townland_hint)
    try:
        sql, meta, mode = _llm_generate_validated_sql(
            prompt=prompt,
            purpose="sqlite_sql",
            dialect_label="SQLite",
        )
        if _requires_verified_fallback(question, sql):
            repair_prompt = _build_sql_semantic_repair_prompt(
                question=question,
                schema=schema,
                analysis=analysis,
                invalid_sql=sql,
                approved_examples=approved_examples or [],
            )
            sql, meta, mode = _llm_generate_validated_sql(
                prompt=repair_prompt,
                purpose="sqlite_sql_semantic_repair",
                dialect_label="SQLite",
            )
        return sql, {**meta, "mode": mode}
    except Exception as exc:
        log.warning("ask_service.llm_sql_unavailable error=%s", exc)
        if ASK_ALLOW_HEURISTIC_FALLBACK:
            return fallback_sql, {
                "provider": "local_fallback", "model": "rule_template", "mode": "fallback_rule"
            }
        return _diagnostic_message_sql(
            "I could not build a validated SQL query for this question from the current schema. "
            "Please rephrase with a clearer townland, surname, year, ship, record type, or measure."
        ), {
            "provider": "validation_guard",
            "model": "validated_sql_only",
            "mode": "no_validated_sql",
            "error": str(exc),
        }


def _generate_vrti_postgres_query(question: str, townland_hint: str | None) -> tuple[str, dict]:
    fallback_sql = _fallback_vrti_postgres_sql(question, townland_hint)
    if not ASK_GENERATE_VRTI_SQL_WITH_LLM:
        return fallback_sql, {
            "provider": "heuristic",
            "model": "local_rule",
            "mode": "quota_saving_template",
        }

    analysis = _analyse_question(question, townland_hint)
    prompt = _build_vrti_postgres_prompt(question, analysis)
    try:
        pg_sql, meta, mode = _llm_generate_validated_sql(
            prompt=prompt,
            purpose="vrti_postgres_sql",
            dialect_label="PostgreSQL",
        )
        return pg_sql, {**meta, "mode": mode}
    except Exception as exc:
        log.warning("ask_service.vrti_postgres_fallback error=%s", exc)
        return fallback_sql, {
            "provider": "local_fallback", "model": "rule_template", "mode": "fallback_rule"
        }


def _load_kg_context() -> str:
    """
    Load data/kg_context.yaml and format it as a compact text block for
    inclusion in the SPARQL generation prompt.  Returns a cached string.
    """
    if not hasattr(_load_kg_context, "_cache"):
        try:
            import yaml
            ctx_path = Path(__file__).resolve().parent.parent.parent / "data" / "kg_context.yaml"
            with open(ctx_path, encoding="utf-8") as fh:
                ctx = yaml.safe_load(fh)

            lines: list[str] = []

            # ── Prefixes ──────────────────────────────────────────
            lines.append("PREFIXES (pre-declared — omit from queries):")
            for pfx, uri in ctx.get("prefixes", {}).items():
                lines.append(f"  {pfx}  <{uri}>")

            # ── Classes ───────────────────────────────────────────
            lines.append("\nCLASSES AND PROPERTIES (derived from live GraphDB — 143,209 triples):")
            for cls, cdata in ctx.get("classes", {}).items():
                lines.append(f"\n{cls}  ({cdata.get('description', '')}  total={cdata.get('total', '?')})")
                for prop, pdata in cdata.get("properties", {}).items():
                    req = "REQUIRED" if pdata.get("required") else "OPTIONAL"
                    cov = pdata.get("coverage", "")
                    note = pdata.get("note", "").strip().replace("\n", " ")
                    vals = pdata.get("values")
                    if isinstance(vals, dict):
                        vstr = ", ".join(f'"{k}"={v}' for k, v in vals.items())
                    elif isinstance(vals, list):
                        vstr = ", ".join(f'"{v}"' for v in vals)
                    else:
                        vstr = ""
                    ex = pdata.get("example_values", [])
                    ex_str = f"  e.g. {ex[:4]}" if ex else ""
                    val_part = f"  values={{{vstr}}}" if vstr else ex_str
                    lines.append(f"  {prop}  [{req}]  cov={cov}{val_part}")
                    if note:
                        lines.append(f"    → {note}")

            # ── OPTIONAL rules ────────────────────────────────────
            lines.append("\nOPTIONAL RULES:")
            lines.append("  NEVER OPTIONAL: co:eventType, co:estate")
            lines.append("  ALWAYS OPTIONAL: schema:familyName, schema:givenName, co:parish, co:occupation")
            lines.append("  co:townland: OPTIONAL when projecting; no OPTIONAL when used as a WHERE filter")
            lines.append("  co:year: OPTIONAL only when projecting in listing queries; omit OPTIONAL in GROUP BY / COUNT")

            # ── Canonical patterns ────────────────────────────────
            lines.append("\nCANONICAL SPARQL PATTERNS (copy the closest match):")
            for name, sparql in ctx.get("patterns", {}).items():
                lines.append(f"\n# {name}")
                lines.append(sparql.strip())

            # ── Mistakes ──────────────────────────────────────────
            lines.append("\nCOMMON MISTAKES — DO NOT REPEAT:")
            for m in ctx.get("mistakes", []):
                lines.append(f"  ✗ {m}")

            _load_kg_context._cache = "\n".join(lines)
        except Exception as exc:
            log.warning("ask_service.kg_context_load_failed error=%s", exc)
            _load_kg_context._cache = ""
    return _load_kg_context._cache


# Properties that exist in SQLite but NOT in the RDF graph.  If the LLM
# hallucinates any of these as RDF predicates the generated SPARQL will
# silently return 0 rows.
_SPARQL_FORBIDDEN_PROPS = {
    "co:hasemigrationrecord", "co:hasevictionrecord", "co:hastenancyrecord",
    "co:has_emigration_record", "co:has_eviction_record", "co:has_tenancy_record",
    "co:totalfamilysize", "co:total_family_size", "co:adults", "co:children",
    "co:ship", "co:destination", "co:chief_tenant", "co:townland_id",
    "co:county", "co:barony", "co:record_id",
}


def _sparql_uses_forbidden_props(sparql: str) -> bool:
    lower = sparql.lower()
    return any(p in lower for p in _SPARQL_FORBIDDEN_PROPS)


def _match_sparql_template(question: str, sql: str) -> str | None:
    """
    Try to map the question to a canonical SPARQL pattern before calling the LLM.
    Returns a ready-to-execute SPARQL string (with any townland/year/surname
    substitutions applied), or None if no template matches confidently.
    """
    q = question.lower()
    s = sql.lower()

    # Extract a townland name from the SQL WHERE clause (case-sensitive, from SQL)
    townland_m = re.search(r"townland\s*(?:LIKE\s*'%?|=\s*'|ILIKE\s*'%?)([^'%]+)", sql, re.I)
    townland = townland_m.group(1).strip().rstrip("%'") if townland_m else None

    # Extract year filters from SQL
    year_m = re.search(r"year\s*=\s*(\d{4})", s)
    year = year_m.group(1) if year_m else None
    year_range_m = re.search(r"year\s*between\s*(\d{4})\s+and\s*(\d{4})", s)
    year_from = year_range_m.group(1) if year_range_m else None
    year_to   = year_range_m.group(2) if year_range_m else None
    if not year_from:
        yr_ge = re.search(r"year\s*>=\s*(\d{4})", s)
        yr_le = re.search(r"year\s*<=\s*(\d{4})", s)
        if yr_ge and yr_le:
            year_from, year_to = yr_ge.group(1), yr_le.group(1)

    # Extract a surname from the SQL
    surname_m = re.search(r"(?:surname|family_?name)\s*(?:LIKE\s*'%?|=\s*'|ILIKE\s*'%?)([^'%]+)", sql, re.I)
    surname = surname_m.group(1).strip().rstrip("%'") if surname_m else None

    is_count = any(k in q for k in ("how many", "count", "total number", "number of"))
    is_emigr = any(k in q for k in ("emigrat", "left", "depart"))
    is_evict = any(k in q for k in ("evict", "clearance", "cleared"))
    is_tenant = any(k in q for k in ("tenant", "tenancy", "rent"))
    is_list   = any(k in q for k in ("list", "show", "who", "names", "people"))
    by_year   = any(k in q for k in ("per year", "each year", "by year", "yearly", "annual"))
    by_townland = any(k in q for k in ("per townland", "by townland", "each townland", "breakdown by townland"))
    by_parish = any(k in q for k in ("per parish", "by parish", "each parish"))

    # ── COUNT emigration (total or from a townland) ──────────────────────
    if is_count and is_emigr and not by_year:
        if townland:
            return (
                f'SELECT (COUNT(DISTINCT ?person) AS ?emigrantCount)\n'
                f'WHERE {{\n'
                f'  ?person a co:Person ;\n'
                f'          co:townland "{townland}" ;\n'
                f'          co:hasEvent ?event .\n'
                f'  ?event co:eventType "emigration" .\n'
                f'}}'
            )
        return (
            'SELECT (COUNT(DISTINCT ?person) AS ?emigrantCount)\n'
            'WHERE {\n'
            '  ?person a co:Person ; co:hasEvent ?event .\n'
            '  ?event co:eventType "emigration" .\n'
            '}'
        )

    # ── COUNT evictions total ────────────────────────────────────────────
    if is_count and is_evict and not by_year:
        if townland:
            return (
                f'SELECT (COUNT(DISTINCT ?person) AS ?evictionCount)\n'
                f'WHERE {{\n'
                f'  ?person a co:Person ;\n'
                f'          co:townland "{townland}" ;\n'
                f'          co:hasEvent ?event .\n'
                f'  ?event co:eventType "eviction" .\n'
                f'}}'
            )
        return (
            'SELECT (COUNT(DISTINCT ?person) AS ?evictionCount)\n'
            'WHERE {\n'
            '  ?person a co:Person ; co:hasEvent ?event .\n'
            '  ?event co:eventType "eviction" .\n'
            '}'
        )

    # ── COUNT tenants total ──────────────────────────────────────────────
    if is_count and is_tenant and not by_year:
        return (
            'SELECT (COUNT(DISTINCT ?person) AS ?tenantCount)\n'
            'WHERE {\n'
            '  ?person a co:Person ; co:hasEvent ?event .\n'
            '  ?event co:eventType "tenancy" .\n'
            '}'
        )

    # ── Evictions per year ────────────────────────────────────────────────
    if is_evict and by_year:
        base = (
            'SELECT ?year (COUNT(DISTINCT ?person) AS ?evictionCount)\n'
            'WHERE {\n'
            '  ?person a co:Person ; co:hasEvent ?event .\n'
            '  ?event co:eventType "eviction" ; co:year ?year .\n'
        )
        if year_from and year_to:
            base += f'  FILTER(?year >= {year_from} && ?year <= {year_to})\n'
        elif year:
            base += f'  FILTER(?year = {year})\n'
        return base + '}\nGROUP BY ?year ORDER BY ?year'

    # ── Emigrations per year ─────────────────────────────────────────────
    if is_emigr and by_year:
        base = (
            'SELECT ?year (COUNT(DISTINCT ?person) AS ?emigrantCount)\n'
            'WHERE {\n'
            '  ?person a co:Person ; co:hasEvent ?event .\n'
            '  ?event co:eventType "emigration" ; co:year ?year .\n'
        )
        if year_from and year_to:
            base += f'  FILTER(?year >= {year_from} && ?year <= {year_to})\n'
        return base + '}\nGROUP BY ?year ORDER BY ?year'

    # ── Emigration breakdown by townland ─────────────────────────────────
    if is_emigr and by_townland:
        return (
            'SELECT ?townland (COUNT(DISTINCT ?person) AS ?emigrants)\n'
            'WHERE {\n'
            '  ?person a co:Person ;\n'
            '          co:townland ?townland ;\n'
            '          co:hasEvent ?event .\n'
            '  ?event co:eventType "emigration" .\n'
            '}\nGROUP BY ?townland ORDER BY DESC(?emigrants) LIMIT 20'
        )

    # ── Emigration breakdown by parish ───────────────────────────────────
    if is_emigr and by_parish:
        return (
            'SELECT ?parish (COUNT(DISTINCT ?person) AS ?emigrants)\n'
            'WHERE {\n'
            '  ?person a co:Person ;\n'
            '          co:parish ?parish ;\n'
            '          co:hasEvent ?event .\n'
            '  ?event co:eventType "emigration" .\n'
            '}\nGROUP BY ?parish ORDER BY DESC(?emigrants)'
        )

    # ── List emigrants from a specific townland ──────────────────────────
    if is_list and is_emigr and townland:
        return (
            f'SELECT ?surname ?givenName ?year\n'
            f'WHERE {{\n'
            f'  ?person a co:Person ;\n'
            f'          co:townland "{townland}" ;\n'
            f'          co:hasEvent ?event .\n'
            f'  ?event co:eventType "emigration" .\n'
            f'  OPTIONAL {{ ?event co:year ?year . }}\n'
            f'  OPTIONAL {{ ?person schema:familyName ?surname . }}\n'
            f'  OPTIONAL {{ ?person schema:givenName ?givenName . }}\n'
            f'}}\nORDER BY ?year ?surname LIMIT 50'
        )

    # ── People by surname ────────────────────────────────────────────────
    if surname:
        return (
            f'SELECT ?givenName ?townland ?eventType ?year\n'
            f'WHERE {{\n'
            f'  ?person a co:Person ;\n'
            f'          schema:familyName "{surname}" ;\n'
            f'          co:hasEvent ?event .\n'
            f'  ?event co:eventType ?eventType .\n'
            f'  OPTIONAL {{ ?event co:year ?year . }}\n'
            f'  OPTIONAL {{ ?person schema:givenName ?givenName . }}\n'
            f'  OPTIONAL {{ ?person co:townland ?townland . }}\n'
            f'}}\nORDER BY ?year LIMIT 50'
        )

    return None  # no template matched


def _generate_graphdb_sparql(question: str, sql: str) -> tuple[str, dict[str, Any]]:
    """
    Translate the question + SQL into a SPARQL SELECT for the Coolattin GraphDB.

    Steps:
    1. Rule-based template matching (fast, deterministic, always-correct).
    2. LLM generation with full kg_context.yaml schema context.
    3. Post-validation: reject any query that uses SQLite column names as RDF
       predicates, fall back to the listing template if validation fails.
    """
    fallback = (
        "SELECT ?surname ?givenName ?townland ?eventType ?year\n"
        "WHERE {\n"
        "  ?person a co:Person ;\n"
        "          co:hasEvent ?event .\n"
        "  ?event co:eventType ?eventType .\n"
        "  OPTIONAL { ?person schema:familyName ?surname . }\n"
        "  OPTIONAL { ?person schema:givenName ?givenName . }\n"
        "  OPTIONAL { ?person co:townland ?townland . }\n"
        "  OPTIONAL { ?event co:year ?year . }\n"
        "}\nORDER BY ?surname\nLIMIT 50"
    )

    # ── Step 1: Template matching ────────────────────────────────────────
    template = _match_sparql_template(question, sql)
    if template:
        log.debug("ask_service.graphdb_sparql_template_matched | q=%s", question[:60])
        return template, {"provider": "rule_template", "model": "local_rule", "mode": "template_match"}

    # ── Step 2: LLM generation ───────────────────────────────────────────
    kg_context_block = _load_kg_context()

    prompt = f"""You are writing a SPARQL 1.1 SELECT query for the Coolattin estate RDF knowledge graph stored in GraphDB.
Output ONLY the bare SPARQL query — no PREFIX declarations (they are pre-declared), no markdown code fences, no comments.

════════════════════════════════════════════════════════════════
AUTHORITATIVE KNOWLEDGE GRAPH SCHEMA
(derived from the live GraphDB repository — 143,209 triples)
════════════════════════════════════════════════════════════════
{kg_context_block}

════════════════════════════════════════════════════════════════
STRICT RULES — VIOLATIONS WILL PRODUCE ZERO RESULTS
════════════════════════════════════════════════════════════════
R1. Use ONLY the RDF properties listed in the schema above.
    FORBIDDEN (these are SQLite columns, NOT RDF properties — using them returns 0 rows):
      co:hasEmigrationRecord  co:hasEvictionRecord  co:hasTenancyRecord
      co:totalFamilySize  co:adults  co:children  co:ship  co:destination
      co:chief_tenant  co:townland_id  co:county  co:barony

R2. Event type MUST be one of exactly: "emigration"  "eviction"  "tenancy"
    Pattern: ?event co:eventType "emigration" .   (not OPTIONAL, no variable)

R3. co:year is on the EVENT node, not on the Person.
    Pattern: ?event co:year ?year .   (NOT ?person co:year ?year)

R4. Traverse via Person → hasEvent → Event (never co:forPerson).
    Pattern: ?person a co:Person ; co:hasEvent ?event .

R5. ALWAYS use OPTIONAL for: schema:familyName  schema:givenName  co:parish  co:occupation
    NEVER use OPTIONAL for: co:eventType  co:estate

R6. Never project raw ?person or ?event URI variables.
    Project only literals (surname, givenName, townland, parish, eventType, year)
    and aggregates (COUNT, MIN, MAX, AVG, etc.).

R7. COUNT queries: use COUNT(DISTINCT ?person).
    Descriptive alias required: ?emigrantCount  ?evictionCount  ?tenantCount  ?totalCount  ?personCount

R8. GROUP BY: every non-aggregate SELECT variable must appear in GROUP BY.

R9. Aggregate-only (COUNT/SUM etc.): omit LIMIT and ORDER BY.
    Listing queries: include ORDER BY + LIMIT 50.

R10. Choose the CLOSEST canonical pattern from the schema above, then adapt it minimally.

════════════════════════════════════════════════════════════════
SQLite query (shows the INTENT and any literal values like townland names or years):
{sql}

Question: {question}
════════════════════════════════════════════════════════════════

SPARQL:""".strip()

    try:
        text, meta = _llm_generate(prompt, purpose="graphdb_sparql", max_tokens=500, temperature=0.0)
        text = text.strip()
        # Strip markdown code fences
        if text.startswith("```"):
            lines_out = text.split("\n")
            # Remove first line (``` or ```sparql) and any trailing ```
            text = "\n".join(lines_out[1:] if len(lines_out) > 1 else lines_out)
        text = text.rstrip("`").strip()
        # Strip leading PREFIX lines (model sometimes adds them despite instructions)
        text_lines = text.splitlines()
        text_lines = [ln for ln in text_lines if not ln.lstrip().lower().startswith("prefix ")]
        # Strip leading comment lines
        text_lines = [ln for ln in text_lines if not ln.lstrip().startswith("#")]
        text = "\n".join(text_lines).strip()

        if not text.upper().lstrip().startswith("SELECT"):
            log.warning("ask_service.graphdb_sparql_invalid | generated=%s", text[:120])
            return fallback, {**meta, "mode": "llm_invalid_fallback"}

        # ── Step 3: Post-validation ──────────────────────────────────────
        if _sparql_uses_forbidden_props(text):
            log.warning("ask_service.graphdb_sparql_forbidden_prop | generated=%s", text[:200])
            # Try template matching one more time with a looser check
            template_retry = _match_sparql_template(question, sql)
            if template_retry:
                return template_retry, {**meta, "mode": "llm_forbidden_fallback_template"}
            return fallback, {**meta, "mode": "llm_forbidden_fallback"}

        return text, {**meta, "mode": "llm_generated"}

    except Exception as exc:
        log.warning("ask_service.graphdb_sparql_generation_failed error=%s", exc)
        return fallback, {"provider": "rule_template", "model": "local_rule", "mode": "fallback_rule"}


def _first_numeric(row: dict) -> float | None:
    """Return the first numeric value found in a result row, or None."""
    for v in row.values():
        if v is None:
            continue
        try:
            return float(v)
        except (ValueError, TypeError):
            pass
    return None


def _explain_result_mismatch(
    question: str,
    sql: str,
    sparql: str,
    sql_rows: list,
    sparql_rows: list,
) -> str | None:
    """
    Ask the LLM to explain why SQLite and GraphDB results differ.
    Handles both row-count differences and single-row value differences
    (the common case where both systems return one COUNT row but with
    different totals).
    """
    sql_count = len(sql_rows)
    sparql_count = len(sparql_rows)

    # Detect value mismatch for single-row aggregate results
    sql_val = _first_numeric(sql_rows[0]) if sql_count == 1 else None
    gdb_val = _first_numeric(sparql_rows[0]) if sparql_count == 1 else None
    value_mismatch = (
        sql_count == 1 and sparql_count == 1
        and sql_val is not None and gdb_val is not None
        and sql_val != gdb_val
    )

    if sql_count == sparql_count and not value_mismatch:
        return None

    if value_mismatch:
        diff_desc = (
            f"Both returned 1 row but values differ: "
            f"SQLite={int(sql_val) if sql_val == int(sql_val) else sql_val}, "
            f"GraphDB={int(gdb_val) if gdb_val == int(gdb_val) else gdb_val}."
        )
    else:
        diff_desc = f"SQLite returned {sql_count} row(s); GraphDB returned {sparql_count} row(s)."

    prompt = f"""Two systems answered the same historical-data question and produced different results.

Question: {question}

{diff_desc}

SQLite query (Coolattin estate records, closed-world, relational):
{sql}

SPARQL query (Coolattin RDF/GraphDB graph, open-world):
{sparql}

In 2–3 sentences explain the most likely reason for the discrepancy. Consider:
- Schema mismatch: the SPARQL query may be using a wrong property (e.g. a SQLite column name used as an RDF predicate when it does not exist in the graph).
- Scope: both databases contain Coolattin estate data but may not be 100% synchronised.
- Query semantics: COUNT(DISTINCT person) vs COUNT(*), different aggregation, OPTIONAL fields.
- Data normalisation: case differences, missing optional properties, alternate spellings.

Be direct and factual. If the SPARQL query uses a property that looks like a SQL column (e.g. co:hasEmigrationRecord), say so clearly."""

    try:
        text, _ = _llm_generate(prompt, purpose="mismatch_explanation", max_tokens=250, temperature=0.1)
        return text.strip() or None
    except Exception as exc:
        log.debug("ask_service.mismatch_explanation_failed error=%s", exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Phase 6 — Fusion & reconciliation helpers
# ─────────────────────────────────────────────────────────────────────────────

def _infer_metric_name(columns: list, row: dict) -> str | None:
    """Return a human-readable metric name from the first numeric column."""
    for col in columns:
        val = row.get(col)
        if val is None:
            continue
        try:
            float(val)
            return col.replace("_", " ").lower()
        except (ValueError, TypeError):
            pass
    return None


def _infer_discrepancy_cause(delta: float, sqlite_val: float, gdb_val: float) -> str:
    pct = (delta / max(abs(sqlite_val), abs(gdb_val), 1)) * 100
    if pct < 5:
        return "likely differing record scope (minor: < 5% difference)"
    elif pct < 20:
        return "moderate divergence — possible partial RDF uplift or alternate property path in SPARQL"
    else:
        return "substantial divergence — likely schema mismatch or incomplete data loading in GraphDB"


def _build_fusion_text(discrepancies: list[dict[str, Any]]) -> str:
    """Build a citation-ready sentence for each detected discrepancy."""
    parts = []
    for d in discrepancies:
        s_val = d["sqlite_value"]
        g_val = d["graphdb_value"]
        metric = d["metric"]
        delta = d["delta"]
        cause = d.get("likely_reason") or d.get("likely_cause", "")
        entity = d["entity"]

        def _fmt(v: float) -> str:
            return str(int(v)) if isinstance(v, float) and v == int(v) else str(v)

        parts.append(
            f"SQLite records {_fmt(s_val)} {metric} for {entity}; "
            f"the Coolattin RDF graph (GraphDB) attributes {_fmt(g_val)} — "
            f"a discrepancy of {_fmt(delta)}, {cause}."
        )
    return " ".join(parts)


def _fuse_lanes(
    sqlite_rows: list,
    sqlite_columns: list,
    graphdb_rows: list,
    graphdb_columns: list,
    vrti_rows: list,
    canonical_townland: str | None,
    entity_resolution: dict | None,
    question: str,
) -> dict[str, Any]:
    """
    Phase 6: align results from all lanes on the resolved entity, detect
    agreement vs. discrepancy on shared metrics, and annotate rows with
    per-source provenance.
    """
    entity_label = (
        (entity_resolution or {}).get("sql_id") or canonical_townland or "unknown entity"
    )
    kg_uri = (entity_resolution or {}).get("kg_uri")
    discrepancies: list[dict[str, Any]] = []
    agreement_count = 0

    sqlite_provenance = [{"source": "sqlite", "entity": entity_label, "kg_uri": None}
                         for _ in sqlite_rows]
    graphdb_provenance = [{"source": "graphdb", "entity": entity_label, "kg_uri": kg_uri}
                          for _ in graphdb_rows]
    vrti_provenance = [{"source": "vrti", "entity": entity_label, "kg_uri": kg_uri}
                       for _ in vrti_rows]

    # Aggregate comparison: both sources returned exactly one numeric row
    sqlite_val = _first_numeric(sqlite_rows[0]) if len(sqlite_rows) == 1 else None
    gdb_val = _first_numeric(graphdb_rows[0]) if len(graphdb_rows) == 1 else None

    if sqlite_val is not None and gdb_val is not None:
        delta = abs(sqlite_val - gdb_val)
        if delta == 0:
            agreement_count += 1
        else:
            metric = _infer_metric_name(sqlite_columns, sqlite_rows[0]) or "count"
            discrepancies.append({
                "metric": metric,
                "entity": entity_label,
                "kg_uri": kg_uri,
                "sqlite_value": sqlite_val,
                "vrti_value": None,
                "graphdb_value": gdb_val,
                "delta": delta,
                "likely_reason": _infer_discrepancy_cause(delta, sqlite_val, gdb_val),
                "likely_cause": _infer_discrepancy_cause(delta, sqlite_val, gdb_val),
            })
    elif len(sqlite_rows) > 1 and len(graphdb_rows) > 1:
        # List comparison: compare row counts
        s_count = len(sqlite_rows)
        g_count = len(graphdb_rows)
        if s_count == g_count:
            agreement_count += 1
        else:
            discrepancies.append({
                "metric": "record count",
                "entity": entity_label,
                "kg_uri": kg_uri,
                "sqlite_value": float(s_count),
                "vrti_value": None,
                "graphdb_value": float(g_count),
                "delta": float(abs(s_count - g_count)),
                "likely_reason": "differing record scope or incomplete RDF uplift",
                "likely_cause": "differing record scope or incomplete RDF uplift",
            })

    fusion_text = _build_fusion_text(discrepancies)

    return {
        "discrepancies": discrepancies,
        "agreement_count": agreement_count,
        "discrepancy_count": len(discrepancies),
        "entity_label": entity_label,
        "kg_uri": kg_uri,
        "source_provenance": {
            "sqlite": sqlite_provenance,
            "graphdb": graphdb_provenance,
            "vrti": vrti_provenance,
        },
        "fusion_text": fusion_text,
    }


def _build_sql_prompt(
    question: str,
    schema: str,
    analysis: dict[str, Any],
    approved_examples: list[dict[str, Any]] | None = None,
) -> str:
    clear_col = _clearances_count_column()
    return f"""Write ONE SQLite query for the question below.

Return SQL only.
No markdown.
No comments.
No explanation.
No semicolon.
Must start with SELECT or WITH.

Use this plan exactly:
{_analysis_prompt_block(analysis)}

Local database profile and high-signal examples:
{_database_profile_prompt_block()}

Live SQLite schema, row counts, and sampled categorical values:
{_live_sqlite_schema_prompt_block()}

Previously approved queries for similar user questions:
{_approved_query_examples_block(approved_examples or [])}

Mandatory rules:
- Count people with COUNT(DISTINCT record_id).
- Population uses census_record joined to townland.
- Eviction totals use clearances_record.{clear_col}.
- Emigration rows use has_emigration_record=1.
- Eviction people rows use has_eviction_record=1.
- Tenancy rows use has_tenancy_record=1.
- Landholding analysis should use holding_acres when it is available.
- Holy well and ring fort comparisons should use heritage_feature joined by townland_norm / townland name.
- Townland filtering uses townland_norm='NAME' or UPPER(t.name)='NAME'.
- Radius queries use distance_km() with a base townland CTE.
- Person lists should include person_name and LIMIT 50.
- NEVER use GROUP_CONCAT or string_agg to combine person names — always return one row per person. If the question asks for both a count and a list of people, return individual person rows (the count is derivable from result length).
- Prefer columns that appear in the live schema block over assumptions from examples.
- If the question is about a surname or a named family, filter by UPPER(surname) when possible.
- If the user asks for a count but the data is unavailable, return a diagnostic SELECT that shows the nearest available related fields instead of inventing a count.
- If the question mixes people with geography, focus this SQLite query on the local records part.

Core semantic hints:
{schema}

Question:
{question}

SQL:""".strip()


def _build_sql_semantic_repair_prompt(
    *,
    question: str,
    schema: str,
    analysis: dict[str, Any],
    invalid_sql: str,
    approved_examples: list[dict[str, Any]] | None = None,
) -> str:
    return f"""{_build_sql_prompt(question, schema, analysis, approved_examples or [])}

The previous SQL passed syntax checks but did not satisfy the app's semantic rules for the question.
PREVIOUS SQL:
{invalid_sql or "<empty>"}

Return ONLY one corrected SQLite SELECT/WITH query that answers the question more faithfully.
SQL:""".strip()


def _build_vrti_postgres_prompt(question: str, analysis: dict[str, Any]) -> str:
    return f"""Write ONE PostgreSQL query for the VRTI relational model.

Return SQL only.
No markdown.
No comments.
No explanation.
No semicolon.
Must start with SELECT or WITH.

Use this plan exactly:
{_analysis_prompt_block(analysis)}

Mandatory rules:
- Use only vrti_townland and vrti_census.
- Population/census uses vrti_census.
- Geography uses vrti_townland.
- If the main question is about data not present in VRTI, return the nearest useful geography query instead of inventing unsupported facts.
- Radius queries must calculate distance from centroid_lat and centroid_lon.

Schema:
{_VRTI_PG_SCHEMA}

Question:
{question}

SQL:""".strip()


def _llm_generate_claude(
    system_prompt: str,
    user_content: str,
    max_tokens: int = 512,
    temperature: float = 0.1,
) -> tuple[str, dict[str, Any]]:
    """
    Part D — Call Claude (Anthropic) directly via HTTP.
    Falls back to _llm_generate (OpenRouter/Ollama) when ANTHROPIC_API_KEY is unset.
    Never raises — returns ("", meta) on error.
    """
    if not ANTHROPIC_API_KEY or not LLM_ALLOW_PAID:
        # Graceful fallback: pack system + user into a single prompt
        combined = f"{system_prompt}\n\n{user_content}"
        try:
            text, meta = _llm_generate(combined, purpose="synthesis", max_tokens=max_tokens, temperature=temperature)
            return text, {**meta, "via": "fallback_openrouter_or_ollama"}
        except Exception as exc:
            log.warning("ask_service.claude_fallback_failed error=%s", exc)
            return "", {"provider": "none", "error": str(exc)}

    payload: dict[str, Any] = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_content}],
    }
    try:
        resp = requests.post(
            f"{ANTHROPIC_BASE_URL}/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": ANTHROPIC_API_VERSION,
                "content-type": "application/json",
            },
            json=payload,
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["content"][0]["text"] if data.get("content") else ""
        usage = data.get("usage", {})
        return text, {
            "provider": "anthropic",
            "model": ANTHROPIC_MODEL,
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
        }
    except Exception as exc:
        log.warning("ask_service.claude_generate_failed error=%s", exc)
        # Fall through to OpenRouter/Ollama
        combined = f"{system_prompt}\n\n{user_content}"
        try:
            text, meta = _llm_generate(combined, purpose="synthesis", max_tokens=max_tokens, temperature=temperature)
            return text, {**meta, "via": "fallback_after_claude_error"}
        except Exception as exc2:
            log.warning("ask_service.synthesis_fallback_failed error=%s", exc2)
            return "", {"provider": "none", "error": str(exc2)}


def _llm_generate_grok(
    prompt: str,
    max_tokens: int = 512,
    temperature: float = 0.1,
) -> tuple[str, dict[str, Any]]:
    """
    Part D — Call Grok (xAI) via the OpenAI-compatible API.
    Used when ASK_SYNTHESIS_MODEL=grok for comparative evaluation.
    Falls back to _llm_generate on error.
    """
    if not GROK_API_KEY or not LLM_ALLOW_PAID:
        return _llm_generate(prompt, purpose="synthesis", max_tokens=max_tokens, temperature=temperature)
    try:
        resp = requests.post(
            f"{GROK_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {GROK_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": GROK_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["choices"][0]["message"]["content"] if data.get("choices") else ""
        usage = data.get("usage", {})
        return text, {
            "provider": "grok",
            "model": GROK_MODEL,
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
        }
    except Exception as exc:
        log.warning("ask_service.grok_generate_failed error=%s", exc)
        return _llm_generate(prompt, purpose="synthesis", max_tokens=max_tokens, temperature=temperature)


# ── Numeric-consistency helpers ──────────────────────────────────────────────

def _synthesis_allowed_numbers(
    sql_result: dict[str, Any],
    graph_context: str,
    question: str = "",
) -> set[str]:
    """
    Build the set of numeric tokens that synthesis prose is permitted to use.
    Derived from the SQL result rows, graph context, and the question itself.
    Digit-subsequences are included so "6,016" permits "over 6,000".

    The question's own numeric tokens (e.g. a year like 1841) are included so
    that historically contextual years mentioned in the question are never
    flagged as unsupported hallucinations — they are part of the user's input,
    not an LLM fabrication.
    """
    rows_str = json.dumps(sql_result.get("rows", [])[:20], ensure_ascii=False, default=str)
    base = _extract_numeric_tokens(
        rows_str + " " + (graph_context or "") + " " + (question or "")
    )
    expanded: set[str] = set(base)
    for tok in base:
        stripped = tok.lstrip("-")
        if stripped.isdigit() and len(stripped) > 1:
            for start in range(len(stripped)):
                for end in range(start + 1, len(stripped) + 1):
                    expanded.add(str(int(stripped[start:end])))
    return expanded


def _cross_verify_synthesis(
    question: str,
    synthesis_text: str,
    sql_result: dict[str, Any],
) -> dict[str, Any]:
    """
    Independent verifier — confirms every factual claim in synthesis_text is
    supported by sql_result rows.  Uses Grok when available (and LLM_ALLOW_PAID),
    else falls back to OpenRouter/Ollama free models.

    Returns {verdict: "agree"|"disagree"|"skip", unsupported_claims: [...],
             model, provider, agreement_rate: 0..1}
    """
    if not synthesis_text or not sql_result.get("rows"):
        return {"verdict": "skip", "unsupported_claims": [], "model": None,
                "provider": None, "agreement_rate": None, "reason": "no_content"}

    rows_preview = json.dumps(sql_result.get("rows", [])[:10], ensure_ascii=False, default=str)
    verifier_prompt = (
        "You are a fact-checker for a historical research assistant.\n\n"
        f"DATA (complete result set the answer was generated from):\n{rows_preview}\n\n"
        f"ANSWER TO CHECK:\n{synthesis_text}\n\n"
        "TASK: Identify every factual claim in the ANSWER that is NOT directly supported "
        "by the DATA above.\n"
        "- A claim is unsupported if it states a specific fact, number, name, or date "
        "that cannot be found in the DATA.\n"
        "- Ignore vague contextual phrases like 'based on records' or 'historically'.\n"
        "- Do NOT check for world-knowledge accuracy — only internal data consistency.\n\n"
        "Respond with ONLY valid JSON (no markdown, no explanation):\n"
        '{"verdict": "agree", "unsupported_claims": []} '
        "if all claims are supported, or "
        '{"verdict": "disagree", "unsupported_claims": ["exact claim text", ...]}'
        " if any are not."
    )
    try:
        if GROK_API_KEY and LLM_ALLOW_PAID:
            text, meta = _llm_generate_grok(verifier_prompt, max_tokens=220, temperature=0.0)
        else:
            text, meta = _llm_generate(verifier_prompt, purpose="verify", max_tokens=220, temperature=0.0)

        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"```(?:json)?", "", cleaned).strip().rstrip("`").strip()
        parsed = json.loads(cleaned)
        verdict = parsed.get("verdict", "skip")
        if verdict not in ("agree", "disagree"):
            verdict = "skip"
        unsupported = [str(c) for c in parsed.get("unsupported_claims", []) if c]
        agreement_rate = 0.0 if verdict == "disagree" else 1.0
        return {
            "verdict": verdict,
            "unsupported_claims": unsupported,
            "model": meta.get("model"),
            "provider": meta.get("provider"),
            "agreement_rate": agreement_rate,
        }
    except Exception as exc:
        log.debug("ask_service.cross_verify_failed error=%s", exc)
        return {"verdict": "skip", "unsupported_claims": [], "model": None,
                "provider": None, "agreement_rate": None, "reason": str(exc)}


# Part F — System prompt for answer synthesis (verbatim from spec)
_SYNTHESIS_SYSTEM_PROMPT = """You are the answer-writer for an Irish estate-records research assistant. You receive
structured retrieval results and write the final answer a historian or genealogist reads.
Your job is to let them trust the answer and move faster — never to make them re-ask.

INPUTS (structured fields; some may be empty):
- question
- resolved_entities: townland/parish/person with sql_id, kg_uri, and for people a list of
  candidate individuals each {confidence, supporting_record_count, may_be_confused_with}
- sql_result: rows + the exact metric and scope (the ONLY authoritative source for any count)
- graph_context: linearised subgraph / community summary (context only, never a count)
- discrepancies: list of {metric, sql_value, graph_value, likely_reason}
- provenance: per fact, the source record(s)

RULES:
1. Counts and totals come ONLY from sql_result. A number appearing only in graph_context is
   not a fact — say the records don't hold it.
2. Lead with the direct answer in the first sentence, in plain language. No preamble, no
   restating the question.
3. State provenance inline and briefly: which source, how many records the answer rests on.
4. Surface uncertainty honestly. If the entity is a person with multiple candidates, say how
   many distinct individuals the name could refer to, whether the figure covers the confirmed
   one or all candidates, and the disambiguating detail (place, date). Never silently merge or
   pick one.
5. If discrepancies is non-empty, state the disagreement in one sentence with both values and
   the likely reason. Required, not optional.
6. End with 2–4 next steps phrased as things the user can act on immediately and specific to
   THIS entity — neighbouring townlands, the candidate individuals to disambiguate, "view the
   N source records" — never generic suggestions.
7. Be concise: the answer, the caveat, the next move. No hedging filler.
8. Never fabricate a record, name, date, or source. If context is thin, say what is known and
   what is not.

TONE: precise, neutral, archival. You are a finding aid, not a storyteller."""


def _claude_synthesize_answer(
    question: str,
    resolved_entities: list[dict[str, Any]],
    sql_result: dict[str, Any],
    graph_context: str,
    discrepancies: list[dict[str, Any]],
    provenance: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """
    Part F — Produce the final answer with an embedded numeric-consistency gate.

    Gate behaviour:
      1. Generate answer with the standard system prompt.
      2. Extract every number from the answer and compare against the allowlist
         built from sql_result rows + graph_context.
      3. If a violation is found, regenerate once with a stricter prompt addendum.
      4. If the second attempt still violates, return ("", meta | gate_outcome="fallback")
         so the caller falls back to the deterministic raw answer.
      meta["gate_outcome"]: "pass" | "regenerated" | "fallback" | "not_applied"
    """
    allowed_numbers = _synthesis_allowed_numbers(sql_result, graph_context or "", question)

    user_block: dict[str, Any] = {
        "question": question,
        "resolved_entities": resolved_entities,
        "sql_result": sql_result,
        "graph_context": graph_context or "(none)",
        "discrepancies": discrepancies,
        "provenance": provenance,
    }
    user_content = json.dumps(user_block, ensure_ascii=False, default=str)

    def _call_llm(system_prompt: str) -> tuple[str, dict[str, Any]]:
        if ASK_SYNTHESIS_MODEL == "grok":
            combined = f"{system_prompt}\n\nINPUT:\n{user_content}"
            return _llm_generate_grok(combined, max_tokens=600, temperature=0.1)
        if ASK_SYNTHESIS_MODEL in ("openrouter", "ollama"):
            combined = f"{system_prompt}\n\nINPUT:\n{user_content}"
            return _llm_generate(combined, purpose="synthesis", max_tokens=600, temperature=0.1)
        return _llm_generate_claude(
            system_prompt=system_prompt,
            user_content=user_content,
            max_tokens=600,
            temperature=0.1,
        )

    def _gate_violations(text: str) -> list[str]:
        if not text:
            return []
        # Strip markdown ordered-list markers (e.g. "1. " "2. " at line start)
        # before extraction to avoid false positives from numbered formatting.
        stripped = re.sub(r"(?m)^\s*\d+\.\s+", " ", text)
        generated = _extract_numeric_tokens(stripped)
        return sorted(n for n in generated if n not in allowed_numbers)

    try:
        text, meta = _call_llm(_SYNTHESIS_SYSTEM_PROMPT)
    except Exception as exc:
        log.warning("ask_service.synthesis_failed error=%s", exc)
        return "", {"provider": "none", "error": str(exc), "gate_outcome": "not_applied"}

    violations = _gate_violations(text)
    if not violations:
        return text, {**meta, "gate_outcome": "pass"}

    log.info("ask_service.numeric_gate_violation unsupported=%s", violations[:5])

    # Retry once with a stricter addendum
    _allowed_sample = ", ".join(sorted(allowed_numbers)[:30])
    strict_suffix = (
        "\n\nCRITICAL CONSTRAINT: The ONLY numbers you may state in your answer are those "
        f"that appear in the sql_result rows. Permitted values include: {_allowed_sample}. "
        "Do not introduce any other numeric value. If you cannot answer without an unsupported "
        "number, state what is known and omit the unsupported figure."
    )
    try:
        text2, meta2 = _call_llm(_SYNTHESIS_SYSTEM_PROMPT + strict_suffix)
    except Exception as exc2:
        log.warning("ask_service.synthesis_retry_failed error=%s", exc2)
        return "", {**meta, "gate_outcome": "fallback",
                    "gate_violations": violations, "gate_retry_error": str(exc2),
                    "gate_blocked_text": text[:600]}

    violations2 = _gate_violations(text2)
    if not violations2:
        return text2, {**meta2, "gate_outcome": "regenerated", "gate_violations_first": violations}

    log.warning("ask_service.numeric_gate_fallback violations=%s", violations2[:5])
    return "", {**meta2, "gate_outcome": "fallback",
                "gate_violations": violations2, "gate_violations_first": violations,
                "gate_blocked_text": text2[:600]}


def _llm_generate_validated_sql(
    prompt: str,
    purpose: str,
    dialect_label: str,
) -> tuple[str, dict[str, Any], str]:
    raw_sql, meta = _llm_generate(
        prompt,
        purpose=purpose,
        max_tokens=260,
        temperature=0.0,
    )
    try:
        return _sanitize_and_validate_sql(raw_sql), meta, "llm_sql"
    except ValueError as exc:
        repair_prompt = _build_sql_repair_prompt(
            base_prompt=prompt,
            invalid_sql=raw_sql,
            validation_error=str(exc),
            dialect_label=dialect_label,
        )
        repaired_sql, repaired_meta = _llm_generate(
            repair_prompt,
            purpose=f"{purpose}_repair",
            max_tokens=260,
            temperature=0.0,
        )
        repaired_sql = _sanitize_and_validate_sql(repaired_sql)
        return repaired_sql, repaired_meta, "llm_sql_repaired"


def _build_sql_repair_prompt(
    base_prompt: str,
    invalid_sql: str,
    validation_error: str,
    dialect_label: str,
) -> str:
    return f"""{base_prompt}

The previous {dialect_label} SQL output was invalid.
VALIDATION ERROR: {validation_error}
PREVIOUS OUTPUT:
{invalid_sql or "<empty>"}

Return ONLY one corrected read-only SQL query. No markdown. No explanation.
SQL:""".strip()


def _llm_generate(
    prompt: str,
    purpose: str = "text",
    max_tokens: int = 300,
    temperature: float = 0.0,
) -> tuple[str, dict[str, Any]]:
    """
    Generate text with the configured LLM provider.
    OpenRouter is preferred when OPENROUTER_API_KEY is present; Ollama remains
    available as a local fallback for offline development.
    """
    last_exc: Exception | None = None
    for provider in _llm_provider_order():
        try:
            if provider == "openrouter":
                return _openrouter_generate(
                    prompt=prompt,
                    purpose=purpose,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
            if provider == "ollama":
                text, model = _ollama_generate(
                    prompt=prompt,
                    purpose=purpose,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                return text, {"provider": "ollama", "model": model}
        except Exception as exc:
            last_exc = exc
            log.warning("ask_service.llm_provider_failed provider=%s purpose=%s error=%s",
                        provider, purpose, exc)

    if last_exc:
        raise RuntimeError(f"No LLM provider succeeded for {purpose}: {last_exc}")
    raise RuntimeError(
        "No LLM provider configured. Set OPENROUTER_API_KEY or ASK_LLM_PROVIDER=ollama."
    )


def _llm_provider_order() -> list[str]:
    provider = (ASK_LLM_PROVIDER or "auto").lower()
    if provider in {"off", "none", "disabled"}:
        return []
    if provider in {"openrouter", "ollama"}:
        return [provider]
    if provider != "auto":
        log.warning("ask_service.unknown_llm_provider provider=%s", provider)

    if OPENROUTER_API_KEY:
        return ["openrouter", "ollama"]
    return ["ollama", "openrouter"]


def _openrouter_headers(include_json_content_type: bool = False) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}"}
    if include_json_content_type:
        headers["Content-Type"] = "application/json"
    if OPENROUTER_SITE_URL:
        headers["HTTP-Referer"] = OPENROUTER_SITE_URL
    if OPENROUTER_APP_TITLE:
        headers["X-Title"] = OPENROUTER_APP_TITLE
        headers["X-OpenRouter-Title"] = OPENROUTER_APP_TITLE
    return headers


def _openrouter_generate(
    prompt: str,
    purpose: str,
    max_tokens: int,
    temperature: float,
) -> tuple[str, dict[str, Any]]:
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY is not set.")

    headers = _openrouter_headers(include_json_content_type=True)

    last_exc: Exception | None = None
    for attempt in range(1, OPENROUTER_MAX_RETRIES + 1):
        for model in _candidate_openrouter_models():
            try:
                resp = requests.post(
                    f"{OPENROUTER_BASE_URL}/chat/completions",
                    headers=headers,
                    json={
                        "model": model,
                        "messages": [
                            {
                                "role": "system",
                                "content": (
                                    "You are a careful data assistant. Follow the user's "
                                    "format instructions exactly and do not invent facts."
                                ),
                            },
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                    },
                    timeout=(OPENROUTER_CONNECT_TIMEOUT, OPENROUTER_REQUEST_TIMEOUT),
                )
                if resp.status_code == 429:
                    raise RuntimeError("OpenRouter rate limit or free daily quota reached.")
                if resp.status_code in {401, 403}:
                    raise RuntimeError("OpenRouter API key was rejected.")
                if resp.status_code == 402:
                    raise RuntimeError("OpenRouter account requires credits for this request.")
                resp.raise_for_status()
                data = resp.json()
                text = _extract_chat_content(data)
                if not text.strip():
                    raise RuntimeError("Empty response from OpenRouter.")
                return _strip_sql_formatting(text), {
                    "provider": "openrouter",
                    "model": data.get("model") or model,
                    "requested_model": model,
                }
            except RuntimeError as exc:
                last_exc = exc
                # Auth/quota errors are shared across models; do not burn retries.
                if "OpenRouter rate limit" in str(exc) or "API key" in str(exc) or "credits" in str(exc):
                    raise
                log.warning(
                    "ask_service.openrouter_request_failed purpose=%s model=%s attempt=%d/%d error=%s",
                    purpose, model, attempt, OPENROUTER_MAX_RETRIES, exc,
                )
            except Exception as exc:
                last_exc = exc
                log.warning(
                    "ask_service.openrouter_request_failed purpose=%s model=%s attempt=%d/%d error=%s",
                    purpose, model, attempt, OPENROUTER_MAX_RETRIES, exc,
                )
        time.sleep(min(0.35 * attempt, 1.0))
    raise RuntimeError(f"OpenRouter failed for {purpose}: {last_exc}")


def _candidate_openrouter_models() -> list[str]:
    configured = [
        m.strip()
        for m in os.environ.get("OPENROUTER_FALLBACK_MODELS", "").split(",")
        if m.strip()
    ]
    candidates = [OPENROUTER_MODEL] + configured + _OPENROUTER_FREE_MODELS
    unique: list[str] = []
    seen: set[str] = set()
    for model in candidates:
        if not model or model in seen:
            continue
        unique.append(model)
        seen.add(model)
    return unique


def _extract_chat_content(data: dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content", "")
    if isinstance(content, list):
        pieces = []
        for item in content:
            if isinstance(item, dict):
                pieces.append(str(item.get("text") or item.get("content") or ""))
            else:
                pieces.append(str(item))
        return "".join(pieces)
    return str(content or "")


def _ollama_generate(
    prompt: str,
    purpose: str = "sql",
    max_tokens: int = 220,
    temperature: float = 0.0,
) -> tuple[str, str]:
    last_exc: Exception | None = None
    for attempt in range(1, OLLAMA_MAX_RETRIES + 1):
        for model in _candidate_ollama_models(force_refresh=(attempt > 1)):
            try:
                resp = requests.post(
                    f"{OLLAMA_BASE_URL}/api/generate",
                    json={
                        "model": model,
                        "prompt": prompt,
                        "stream": False,
                        "keep_alive": OLLAMA_KEEP_ALIVE,
                        "options": {"temperature": temperature, "num_predict": max_tokens},
                    },
                    timeout=(OLLAMA_CONNECT_TIMEOUT, OLLAMA_REQUEST_TIMEOUT),
                )
                resp.raise_for_status()
                sql_text = resp.json().get("response", "")
                if not sql_text.strip():
                    raise RuntimeError("Empty response from Ollama.")
                _remember_ollama_model(model)
                return _strip_sql_formatting(sql_text), model
            except Exception as exc:
                last_exc = exc
                log.warning(
                    "ask_service.ollama_request_failed purpose=%s model=%s attempt=%d/%d error=%s",
                    purpose, model, attempt, OLLAMA_MAX_RETRIES, exc,
                )
                time.sleep(min(0.35 * attempt, 1.0))
    raise RuntimeError(f"Ollama failed for {purpose}: {last_exc}")


def _candidate_ollama_models(force_refresh: bool = False) -> list[str]:
    candidates: list[str] = []
    if OLLAMA_MODEL:
        candidates.append(OLLAMA_MODEL)
    candidates.extend(_ollama_running_models())
    with _ollama_cache_lock:
        cached_resolved = _OLLAMA_MODEL_CACHE.get("resolved_model")
        cached_models = list(_OLLAMA_MODEL_CACHE.get("models") or [])
    if cached_resolved:
        candidates.append(cached_resolved)
    candidates.extend(cached_models)
    if not candidates or force_refresh:
        candidates.extend(_ollama_installed_models(force_refresh=force_refresh))

    unique: list[str] = []
    seen: set[str] = set()
    for model in candidates:
        if not model or model in seen:
            continue
        unique.append(model)
        seen.add(model)
    if unique:
        return unique
    raise RuntimeError(
        f"No Ollama models found at {OLLAMA_BASE_URL}. Install: ollama pull llama3.2:latest"
    )


def _ollama_running_models() -> list[str]:
    try:
        resp = requests.get(
            f"{OLLAMA_BASE_URL}/api/ps",
            timeout=(OLLAMA_CONNECT_TIMEOUT, 8),
        )
        resp.raise_for_status()
        models = [
            (m.get("name") or "").strip()
            for m in (resp.json().get("models") or [])
            if m.get("name")
        ]
        return models
    except Exception:
        return []


def _resolve_ollama_model() -> str:
    installed = _ollama_installed_models()
    if OLLAMA_MODEL and (OLLAMA_MODEL in set(installed) or not installed):
        _remember_ollama_model(OLLAMA_MODEL)
        return OLLAMA_MODEL
    preferred = ["llama3.2:latest", "llama3.1:8b", "llama3.1:latest", "llama3:latest",
                 "qwen2.5:latest", "mistral:latest", "gemma2:latest"]
    installed_set = set(installed)
    for p in preferred:
        if p in installed_set:
            _remember_ollama_model(p)
            return p
    if installed:
        _remember_ollama_model(installed[0])
        return installed[0]
    raise RuntimeError(
        f"No Ollama models found at {OLLAMA_BASE_URL}. Install: ollama pull llama3.2:latest"
    )


def _ollama_installed_models(force_refresh: bool = False) -> list[str]:
    now = time.time()
    with _ollama_cache_lock:
        cached_models = list(_OLLAMA_MODEL_CACHE.get("models") or [])
        expires_at = float(_OLLAMA_MODEL_CACHE.get("expires_at") or 0.0)
    if cached_models and not force_refresh and expires_at > now:
        return cached_models

    try:
        resp = requests.get(
            f"{OLLAMA_BASE_URL}/api/tags",
            timeout=(OLLAMA_CONNECT_TIMEOUT, 8),
        )
        resp.raise_for_status()
        models = [
            (m.get("name") or "").strip()
            for m in (resp.json().get("models") or [])
            if m.get("name")
        ]
        ordered = _preferred_ollama_models(models)
        with _ollama_cache_lock:
            _OLLAMA_MODEL_CACHE["models"] = ordered
            _OLLAMA_MODEL_CACHE["expires_at"] = now + OLLAMA_MODEL_CACHE_TTL
        return ordered
    except Exception:
        if cached_models:
            return cached_models
        raise


def _preferred_ollama_models(models: list[str]) -> list[str]:
    preferred = [
        "llama3.2:latest",
        "llama3.1:8b",
        "llama3.1:latest",
        "llama3:latest",
        "qwen2.5:latest",
        "mistral:latest",
        "gemma2:latest",
    ]
    unique_models = [m for m in models if m]
    preferred_first = [m for m in preferred if m in unique_models]
    others = [m for m in unique_models if m not in set(preferred_first)]
    return preferred_first + others


def _remember_ollama_model(model: str) -> None:
    if not model:
        return
    now = time.time()
    with _ollama_cache_lock:
        models = list(_OLLAMA_MODEL_CACHE.get("models") or [])
        if model in models:
            models = [model] + [m for m in models if m != model]
        else:
            models.insert(0, model)
        _OLLAMA_MODEL_CACHE["models"] = models
        _OLLAMA_MODEL_CACHE["resolved_model"] = model
        _OLLAMA_MODEL_CACHE["expires_at"] = now + OLLAMA_MODEL_CACHE_TTL


def _strip_sql_formatting(sql_text: str) -> str:
    out = sql_text.strip()
    out = re.sub(r"^```(?:sql)?\s*", "", out, flags=re.IGNORECASE)
    out = re.sub(r"\s*```\s*$", "", out)
    out = re.sub(r"^SQL\s*[:\-]?\s*", "", out, flags=re.IGNORECASE)
    # Drop any trailing prose lines
    lines = []
    for line in out.splitlines():
        s = line.strip()
        if not s:
            continue
        if re.match(r"^(This |Note |The |Here |I |--\s+[A-Z])", s):
            break
        lines.append(line)
    return "\n".join(lines).strip() if lines else out.strip()


# ─────────────────────────────────────────────────────────────────────────────
# Fallback SQL templates
# ─────────────────────────────────────────────────────────────────────────────

def _fallback_sql(question: str, townland_hint: str | None) -> str:
    q    = (question or "").lower()
    hint = _norm_townland(townland_hint) or ""

    # Route richer questions to the heuristic builder first.
    # This prevents narrow static templates from ignoring useful intent
    # like "by year", "list", "around 20km", etc.
    advanced_markers = [
        " by ", "per ", "group", "list", "show", "who", "which",
        "around", "nearby", "radius", "within", "20km", "20 km",
        "between", "trend", "compare", "breakdown",
        "population", "census", "people", "person", "names",
        "evict", "clearance", "tenant", "parish", "barony", "county",
    ]
    if any(m in q for m in advanced_markers):
        return _dynamic_fallback_sql(question, townland_hint)

    if ("20km" in q or "20 km" in q or "around" in q) and hint:
        return f"""WITH base AS (SELECT centroid_lat lat,centroid_lon lon FROM townland WHERE UPPER(name)='{_sql_escape(hint)}' LIMIT 1),nearby AS (SELECT t.name FROM townland t,base b WHERE t.centroid_lat IS NOT NULL AND distance_km(t.centroid_lat,t.centroid_lon,b.lat,b.lon)<=20.0) SELECT COUNT(DISTINCT u.record_id) AS emigrated_within_20km FROM unified_record u JOIN nearby n ON u.townland_norm=UPPER(n.name) WHERE u.has_emigration_record=1"""

    if "parish" in q and ("how many" in q or "count" in q or "list" in q):
        return "SELECT COUNT(DISTINCT civil_parish) AS parish_count FROM townland WHERE civil_parish IS NOT NULL AND TRIM(civil_parish)!=''"

    if hint and ("from this townland" in q or "townland" in q):
        return f"SELECT COUNT(DISTINCT record_id) AS emigrated_people FROM unified_record WHERE has_emigration_record=1 AND townland_norm='{_sql_escape(hint)}'"

    if "total" in q and "emigra" in q:
        return "SELECT COUNT(DISTINCT record_id) AS total_emigrated_people FROM unified_record WHERE has_emigration_record=1"

    if hint:
        return f"SELECT COUNT(DISTINCT record_id) AS emigrated_people FROM unified_record WHERE has_emigration_record=1 AND townland_norm='{_sql_escape(hint)}'"

    return _dynamic_fallback_sql(question, townland_hint)


def _fallback_vrti_postgres_sql(question: str, townland_hint: str | None) -> str:
    q    = (question or "").lower()
    hint = _norm_townland(townland_hint) or ""

    advanced_markers = [
        " by ", "per ", "group", "list", "show", "who", "which",
        "around", "nearby", "radius", "within", "20km", "20 km",
        "between", "trend", "compare", "breakdown",
        "population", "census", "people", "person", "names",
        "evict", "clearance", "tenant", "parish", "barony", "county",
    ]
    if any(m in q for m in advanced_markers):
        return _dynamic_fallback_vrti_postgres_sql(question, townland_hint)

    if "parish" in q and ("how many" in q or "count" in q or "list" in q):
        return "SELECT DISTINCT civil_parish AS parish_name, COUNT(*) OVER (PARTITION BY civil_parish) AS townland_count FROM vrti_townland WHERE civil_parish IS NOT NULL AND BTRIM(civil_parish)<>'' ORDER BY civil_parish"

    if hint:
        return f"SELECT t.name,t.name_gaelic,t.civil_parish,t.barony,t.county,t.kg_uri FROM vrti_townland t WHERE UPPER(t.name)='{_sql_escape(hint)}'"

    return _dynamic_fallback_vrti_postgres_sql(question, townland_hint)


def _dynamic_fallback_sql(question: str, townland_hint: str | None) -> str:
    """
    Heuristic SQL constructor for fallback mode.
    Builds a relevant query from intent signals in the question.
    """
    analysis = _analyse_question(question, townland_hint)
    q = (question or "").lower()
    hint = analysis.get("townland_norm") or ""
    year = analysis.get("year")
    radius_km = analysis.get("radius_km") or 20

    wants_list = analysis.get("output_mode") == "list"
    wants_count = analysis.get("output_mode") == "count"
    wants_radius = analysis.get("scope") == "radius" and bool(hint)
    mentions_local_townland = analysis.get("scope") == "townland"
    asks_population = analysis.get("primary_intent") == "population"
    asks_emigration = analysis.get("primary_intent") == "emigration"
    asks_eviction = analysis.get("primary_intent") == "eviction"
    asks_tenancy = analysis.get("primary_intent") == "tenancy"
    asks_people = bool(analysis.get("asks_people")) or analysis.get("primary_intent") == "people"
    asks_parish = bool(analysis.get("asks_parish"))

    same_parish_sql = _same_parish_sql(question, analysis, hint)
    if same_parish_sql:
        return same_parish_sql

    # Non-person geography intent: parish-focused questions.
    if asks_parish and asks_people and hint:
        where_parts = [f"townland_norm='{_sql_escape(hint)}'"]
        if year:
            where_parts.append(f"year={year}")
        where_sql = " WHERE " + " AND ".join(where_parts)
        if wants_count:
            return f"""
SELECT COUNT(DISTINCT record_id) AS people_in_townland
FROM unified_record
{where_sql}
""".strip()
        return f"""
SELECT DISTINCT
  COALESCE(NULLIF(TRIM(canonical_name),''),TRIM(COALESCE(forename,'')||' '||COALESCE(surname,''))) AS person_name,
  surname, forename, townland, parish, year,
  has_emigration_record, has_eviction_record, has_tenancy_record
FROM unified_record
{where_sql}
ORDER BY year, person_name
LIMIT 200
""".strip()

    if asks_parish and any(x in q for x in ["how many", "count", "list", "which", "show"]):
        if analysis.get("output_mode") in {"list", "grouped"}:
            return """
SELECT
  civil_parish AS parish,
  COUNT(*) AS townland_count
FROM townland
WHERE civil_parish IS NOT NULL AND TRIM(civil_parish) <> ''
GROUP BY civil_parish
ORDER BY townland_count DESC, parish
LIMIT 200
""".strip()
        return """
SELECT COUNT(DISTINCT civil_parish) AS parish_count
FROM townland
WHERE civil_parish IS NOT NULL AND TRIM(civil_parish) <> ''
""".strip()

    if asks_population:
        if wants_radius and hint:
            if year:
                return f"""
WITH base AS (
  SELECT centroid_lat lat, centroid_lon lon
  FROM townland
  WHERE UPPER(name)='{_sql_escape(hint)}'
  LIMIT 1
),
nearby AS (
  SELECT t.id, t.name
  FROM townland t, base b
  WHERE t.centroid_lat IS NOT NULL
    AND t.centroid_lon IS NOT NULL
    AND distance_km(t.centroid_lat,t.centroid_lon,b.lat,b.lon)<={float(radius_km):.1f}
)
SELECT
  c.year,
  SUM(c.total) AS total_population,
  SUM(c.male) AS male_population,
  SUM(c.female) AS female_population,
  COUNT(DISTINCT n.name) AS townlands_covered
FROM census_record c
JOIN nearby n ON c.townland_id=n.id
WHERE c.year={year}
GROUP BY c.year
""".strip()
            return f"""
WITH base AS (
  SELECT centroid_lat lat, centroid_lon lon
  FROM townland
  WHERE UPPER(name)='{_sql_escape(hint)}'
  LIMIT 1
),
nearby AS (
  SELECT t.id, t.name
  FROM townland t, base b
  WHERE t.centroid_lat IS NOT NULL
    AND t.centroid_lon IS NOT NULL
    AND distance_km(t.centroid_lat,t.centroid_lon,b.lat,b.lon)<={float(radius_km):.1f}
)
SELECT
  c.year,
  SUM(c.total) AS total_population,
  SUM(c.male) AS male_population,
  SUM(c.female) AS female_population,
  COUNT(DISTINCT n.name) AS townlands_covered
FROM census_record c
JOIN nearby n ON c.townland_id=n.id
GROUP BY c.year
ORDER BY c.year
""".strip()

        if hint and year:
            return f"""
SELECT
  t.name AS townland,
  c.year,
  c.total AS population,
  c.male,
  c.female,
  c.inhabited,
  c.uninhabited
FROM census_record c
JOIN townland t ON c.townland_id=t.id
WHERE UPPER(t.name)='{_sql_escape(hint)}'
  AND c.year={year}
LIMIT 50
""".strip()

        if hint:
            return f"""
SELECT
  c.year,
  t.name AS townland,
  c.total AS population,
  c.male,
  c.female,
  c.inhabited,
  c.uninhabited
FROM census_record c
JOIN townland t ON c.townland_id=t.id
WHERE UPPER(t.name)='{_sql_escape(hint)}'
ORDER BY c.year
LIMIT 100
""".strip()

        if asks_parish:
            year_filter = f" AND c.year={year}" if year else ""
            return f"""
SELECT
  t.civil_parish AS parish,
  c.year,
  SUM(c.total) AS total_population
FROM census_record c
JOIN townland t ON c.townland_id=t.id
WHERE t.civil_parish IS NOT NULL{year_filter}
GROUP BY t.civil_parish, c.year
ORDER BY c.year, total_population DESC
LIMIT 100
""".strip()

        if year:
            return f"""
SELECT
  t.name AS townland,
  c.year,
  c.total AS population,
  c.male,
  c.female
FROM census_record c
JOIN townland t ON c.townland_id=t.id
WHERE c.year={year}
ORDER BY c.total DESC, t.name
LIMIT 100
""".strip()

        return """
SELECT
  c.year,
  SUM(c.total) AS estate_population,
  SUM(c.male) AS male_population,
  SUM(c.female) AS female_population,
  SUM(c.inhabited) AS inhabited_houses,
  SUM(c.uninhabited) AS uninhabited_houses
FROM census_record c
GROUP BY c.year
ORDER BY c.year
""".strip()

    if asks_people and not any([asks_emigration, asks_eviction, asks_tenancy, asks_population]):
        where_parts: list[str] = []
        if hint and mentions_local_townland:
            where_parts.append(f"townland_norm='{_sql_escape(hint)}'")
        if year:
            where_parts.append(f"year={year}")
        if analysis.get("surname"):
            where_parts.append(f"UPPER(surname)='{_sql_escape(str(analysis['surname']))}'")
        where_sql = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""

        if wants_count:
            return f"""
SELECT COUNT(DISTINCT record_id) AS matching_people
FROM unified_record
{where_sql}
""".strip()

        return f"""
SELECT DISTINCT
  COALESCE(NULLIF(TRIM(canonical_name),''),TRIM(COALESCE(forename,'')||' '||COALESCE(surname,''))) AS person_name,
  surname, forename, townland, parish, year,
  role, has_emigration_record, has_eviction_record, has_tenancy_record
FROM unified_record
{where_sql}
ORDER BY year, person_name
LIMIT 200
""".strip()

    if asks_eviction and not wants_list:
        if wants_radius and hint:
            if analysis.get("group_by") == "year":
                return f"""
WITH base AS (
  SELECT centroid_lat lat, centroid_lon lon
  FROM townland
  WHERE UPPER(name)='{_sql_escape(hint)}'
  LIMIT 1
),
nearby AS (
  SELECT t.id, t.name
  FROM townland t, base b
  WHERE t.centroid_lat IS NOT NULL
    AND t.centroid_lon IS NOT NULL
    AND distance_km(t.centroid_lat,t.centroid_lon,b.lat,b.lon)<={float(radius_km):.1f}
)
SELECT
  cr.year,
  SUM(cr.eviction_count) AS total_evictions
FROM clearances_record cr
JOIN nearby n ON cr.townland_id=n.id
GROUP BY cr.year
ORDER BY cr.year
LIMIT 100
""".strip()
            return f"""
WITH base AS (
  SELECT centroid_lat lat, centroid_lon lon
  FROM townland
  WHERE UPPER(name)='{_sql_escape(hint)}'
  LIMIT 1
),
nearby AS (
  SELECT t.id, t.name
  FROM townland t, base b
  WHERE t.centroid_lat IS NOT NULL
    AND t.centroid_lon IS NOT NULL
    AND distance_km(t.centroid_lat,t.centroid_lon,b.lat,b.lon)<={float(radius_km):.1f}
)
SELECT SUM(cr.eviction_count) AS total_evictions_within_20km
FROM clearances_record cr
JOIN nearby n ON cr.townland_id=n.id
""".strip()

        if hint and year:
            return f"""
SELECT
  t.name AS townland,
  cr.year,
  cr.eviction_count
FROM clearances_record cr
JOIN townland t ON cr.townland_id=t.id
WHERE UPPER(t.name)='{_sql_escape(hint)}'
  AND cr.year={year}
LIMIT 50
""".strip()

        if hint:
            return f"""
SELECT
  cr.year,
  SUM(cr.eviction_count) AS total_evictions
FROM clearances_record cr
JOIN townland t ON cr.townland_id=t.id
WHERE UPPER(t.name)='{_sql_escape(hint)}'
GROUP BY cr.year
ORDER BY cr.year
LIMIT 100
""".strip()

        if year:
            return f"""
SELECT
  t.name AS townland,
  SUM(cr.eviction_count) AS total_evictions
FROM clearances_record cr
JOIN townland t ON cr.townland_id=t.id
WHERE cr.year={year}
GROUP BY t.name
ORDER BY total_evictions DESC, t.name
LIMIT 100
""".strip()

        if analysis.get("group_by") == "year":
            return """
SELECT
  year,
  SUM(eviction_count) AS total_evictions
FROM clearances_record
GROUP BY year
ORDER BY year
LIMIT 100
""".strip()

        return """
SELECT SUM(eviction_count) AS total_evictions
FROM clearances_record
""".strip()

    # Primary record type signal.
    metric_alias = "matching_people"
    where_clauses: list[str] = []
    if asks_emigration:
        where_clauses.append("has_emigration_record=1")
        metric_alias = "emigrated_people"
    elif asks_eviction:
        where_clauses.append("has_eviction_record=1")
        metric_alias = "evicted_people"
    elif asks_tenancy:
        where_clauses.append("has_tenancy_record=1")
        metric_alias = "tenant_people"

    if year:
        where_clauses.append(f"year={year}")
    if analysis.get("surname"):
        where_clauses.append(f"UPPER(surname)='{_sql_escape(str(analysis['surname']))}'")

    # For radius intents we should filter by nearby set, not the center townland only.
    if hint and mentions_local_townland and not wants_radius:
        where_clauses.append(f"townland_norm='{_sql_escape(hint)}'")

    group_by = analysis.get("group_by")

    if wants_radius:
        base_where = " AND ".join(where_clauses) if where_clauses else "1=1"
        if group_by:
            return f"""
WITH base AS (
  SELECT centroid_lat lat, centroid_lon lon
  FROM townland
  WHERE UPPER(name)='{_sql_escape(hint)}'
  LIMIT 1
),
nearby AS (
  SELECT t.name
  FROM townland t, base b
  WHERE t.centroid_lat IS NOT NULL
    AND t.centroid_lon IS NOT NULL
    AND distance_km(t.centroid_lat,t.centroid_lon,b.lat,b.lon)<={float(radius_km):.1f}
)
SELECT
  u.{group_by} AS {group_by},
  COUNT(DISTINCT u.record_id) AS {metric_alias}
FROM unified_record u
JOIN nearby n ON u.townland_norm=UPPER(n.name)
WHERE {" AND ".join(where_clauses) if where_clauses else "1=1"}
GROUP BY u.{group_by}
ORDER BY {group_by if group_by == "year" else metric_alias + " DESC"}
LIMIT 100
""".strip()

        if wants_list:
            return f"""
WITH base AS (
  SELECT centroid_lat lat, centroid_lon lon
  FROM townland
  WHERE UPPER(name)='{_sql_escape(hint)}'
  LIMIT 1
),
nearby AS (
  SELECT t.name
  FROM townland t, base b
  WHERE t.centroid_lat IS NOT NULL
    AND t.centroid_lon IS NOT NULL
    AND distance_km(t.centroid_lat,t.centroid_lon,b.lat,b.lon)<={float(radius_km):.1f}
)
SELECT DISTINCT
  COALESCE(NULLIF(TRIM(u.canonical_name),''),TRIM(COALESCE(u.forename,'')||' '||COALESCE(u.surname,''))) AS person_name,
  u.surname, u.forename, u.townland, u.parish, u.year
FROM unified_record u
JOIN nearby n ON u.townland_norm=UPPER(n.name)
WHERE {base_where}
ORDER BY u.year, person_name
LIMIT 200
""".strip()

        return f"""
WITH base AS (
  SELECT centroid_lat lat, centroid_lon lon
  FROM townland
  WHERE UPPER(name)='{_sql_escape(hint)}'
  LIMIT 1
),
nearby AS (
  SELECT t.name
  FROM townland t, base b
  WHERE t.centroid_lat IS NOT NULL
    AND t.centroid_lon IS NOT NULL
    AND distance_km(t.centroid_lat,t.centroid_lon,b.lat,b.lon)<={float(radius_km):.1f}
)
SELECT COUNT(DISTINCT u.record_id) AS {metric_alias}_within_20km
FROM unified_record u
JOIN nearby n ON u.townland_norm=UPPER(n.name)
WHERE {" AND ".join(where_clauses) if where_clauses else "1=1"}
""".strip()

    where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    if group_by:
        order_sql = group_by if group_by == "year" else f"{metric_alias} DESC"
        return f"""
SELECT
  {group_by},
  COUNT(DISTINCT record_id) AS {metric_alias}
FROM unified_record
{where_sql}
GROUP BY {group_by}
ORDER BY {order_sql}
LIMIT 100
""".strip()

    if wants_list:
        return f"""
SELECT DISTINCT
  COALESCE(NULLIF(TRIM(canonical_name),''),TRIM(COALESCE(forename,'')||' '||COALESCE(surname,''))) AS person_name,
  surname, forename, townland, parish, year,
  has_emigration_record, has_eviction_record, has_tenancy_record
FROM unified_record
{where_sql}
ORDER BY year, person_name
LIMIT 200
""".strip()

    if wants_count or not wants_list:
        return f"""
SELECT COUNT(DISTINCT record_id) AS {metric_alias}
FROM unified_record
{where_sql}
""".strip()


def _dynamic_fallback_vrti_postgres_sql(question: str, townland_hint: str | None) -> str:
    """
    Heuristic PostgreSQL query constructor for VRTI relational/warehouse context.
    """
    analysis = _analyse_question(question, townland_hint)
    q = (question or "").lower()
    hint = analysis.get("townland_norm") or ""
    year = analysis.get("year")
    radius_km = analysis.get("radius_km") or 20

    if hint and ("same parish" in q or "in the parish as" in q):
        return f"""
SELECT
  t.name,
  t.civil_parish,
  t.barony,
  t.county,
  t.kg_uri
FROM vrti_townland t
WHERE t.civil_parish = (
  SELECT civil_parish
  FROM vrti_townland
  WHERE UPPER(name)='{_sql_escape(hint)}'
  LIMIT 1
)
  AND UPPER(t.name)!='{_sql_escape(hint)}'
ORDER BY t.name
LIMIT 200
""".strip()

    if analysis.get("asks_parish") and analysis.get("output_mode") in {"list", "grouped"}:
        return """
SELECT
  civil_parish,
  COUNT(*) AS townland_count
FROM vrti_townland
WHERE civil_parish IS NOT NULL AND BTRIM(civil_parish) <> ''
GROUP BY civil_parish
ORDER BY townland_count DESC, civil_parish
LIMIT 200
""".strip()

    if analysis.get("asks_parish") and analysis.get("output_mode") == "count":
        return """
SELECT COUNT(DISTINCT civil_parish) AS parish_count
FROM vrti_townland
WHERE civil_parish IS NOT NULL AND BTRIM(civil_parish) <> ''
""".strip()

    if hint and analysis.get("scope") == "radius":
        return f"""
WITH base AS (
  SELECT centroid_lat, centroid_lon
  FROM vrti_townland
  WHERE UPPER(name)='{_sql_escape(hint)}'
  LIMIT 1
)
SELECT
  t.name, t.civil_parish, t.barony, t.county, t.kg_uri
  , ROUND(
      6371.0 * ACOS(
        LEAST(1.0, GREATEST(-1.0,
          COS(RADIANS(b.centroid_lat)) * COS(RADIANS(t.centroid_lat)) *
          COS(RADIANS(t.centroid_lon) - RADIANS(b.centroid_lon)) +
          SIN(RADIANS(b.centroid_lat)) * SIN(RADIANS(t.centroid_lat))
        ))
      )
    , 2) AS dist_km
FROM vrti_townland t
CROSS JOIN base b
WHERE t.centroid_lat IS NOT NULL
  AND t.centroid_lon IS NOT NULL
  AND (
    6371.0 * ACOS(
      LEAST(1.0, GREATEST(-1.0,
        COS(RADIANS(b.centroid_lat)) * COS(RADIANS(t.centroid_lat)) *
        COS(RADIANS(t.centroid_lon) - RADIANS(b.centroid_lon)) +
        SIN(RADIANS(b.centroid_lat)) * SIN(RADIANS(t.centroid_lat))
      ))
    )
  ) <= {float(radius_km):.1f}
ORDER BY dist_km, t.name
LIMIT 200
""".strip()

    if analysis.get("primary_intent") == "population" or year:
        year_filter = f"WHERE c.census_year={year}" if year else ""
        return f"""
SELECT
  c.townland_name,
  c.census_year,
  c.total,
  c.male,
  c.female,
  c.inhabited,
  c.uninhabited
FROM vrti_census c
{year_filter}
ORDER BY c.census_year, c.townland_name
LIMIT 200
""".strip()

    if hint:
        return f"""
SELECT
  t.name, t.name_gaelic, t.civil_parish, t.barony, t.county, t.kg_uri
FROM vrti_townland t
WHERE UPPER(t.name)='{_sql_escape(hint)}'
LIMIT 100
""".strip()

    return """
SELECT
  t.name, t.civil_parish, t.barony, t.county, t.kg_uri
FROM vrti_townland t
ORDER BY t.name
LIMIT 150
""".strip()


# ─────────────────────────────────────────────────────────────────────────────
# SQL safety + execution
# ─────────────────────────────────────────────────────────────────────────────

def _sanitize_and_validate_sql(sql: str) -> str:
    if not sql:
        raise ValueError("Empty SQL.")
    cleaned = _normalise_schema_compat_sql(sql.strip().rstrip(";").strip())
    if ";" in cleaned:
        raise ValueError("Multiple statements not allowed.")
    if not re.match(r"^\s*(SELECT|WITH)\b", cleaned, flags=re.IGNORECASE):
        raise ValueError("Only SELECT/WITH allowed.")
    if FORBIDDEN_SQL.search(cleaned):
        raise ValueError("Unsafe SQL keyword blocked.")
    return cleaned


def _normalise_schema_compat_sql(sql: str) -> str:
    clearances_count_col = _clearances_count_column()
    if clearances_count_col and clearances_count_col != "eviction_count":
        sql = re.sub(r"\beviction_count\b", clearances_count_col, sql)
    return sql


def _clearances_count_column() -> str:
    with _schema_cache_lock:
        cached = _SCHEMA_COMPAT_CACHE.get("clearances_count_column")
        if cached:
            return str(cached)

    conn = get_db_conn()
    try:
        rows = conn.execute("PRAGMA table_info(clearances_record)").fetchall()
        names = {row["name"] for row in rows}
    except Exception:
        names = set()
    finally:
        conn.close()

    if "eviction_count" in names:
        column = "eviction_count"
    elif "count" in names:
        column = "count"
    else:
        column = "eviction_count"

    with _schema_cache_lock:
        _SCHEMA_COMPAT_CACHE["clearances_count_column"] = column
    return column


def _run_read_only_query(sql: str) -> tuple[list[str], list[dict]]:
    conn = get_db_conn()
    try:
        conn.create_function("distance_km", 4, _distance_km_sql)
        cur = conn.execute(sql)
        cols = [d[0] for d in (cur.description or [])]
        rows = [dict(r) for r in cur.fetchall()]
        return cols, rows[:300]
    finally:
        conn.close()


def _build_sql_runtime_repair_prompt(
    *,
    question: str,
    townland_hint: str | None,
    failing_sql: str,
    execution_error: str,
    approved_examples: list[dict[str, Any]] | None = None,
) -> str:
    analysis = _analyse_question(question, townland_hint)
    return f"""{_build_sql_prompt(question, _ANNOTATED_SCHEMA, analysis, approved_examples or [])}

The previous SQL failed when executed against SQLite.
EXECUTION ERROR: {execution_error}
PREVIOUS SQL:
{failing_sql or "<empty>"}

Return ONLY one corrected SQLite SELECT/WITH query that avoids the error and still answers the question.
SQL:""".strip()


def _execute_with_recovery(
    question: str,
    townland_hint: str | None,
    sql: str,
    approved_examples: list[dict[str, Any]] | None = None,
) -> tuple[str, list[str], list[dict], str | None, dict[str, Any] | None]:
    try:
        if _requires_verified_fallback(question, sql):
            raise ValueError("semantic_constraint_mismatch")
        cols, rows = _run_read_only_query(sql)
        return sql, cols, rows, None, None
    except Exception as exc:
        try:
            repaired_sql, repair_meta, repair_mode = _llm_generate_validated_sql(
                prompt=_build_sql_runtime_repair_prompt(
                    question=question,
                    townland_hint=townland_hint,
                    failing_sql=sql,
                    execution_error=str(exc),
                    approved_examples=approved_examples or [],
                ),
                purpose="sqlite_sql_runtime_repair",
                dialect_label="SQLite",
            )
            if _requires_verified_fallback(question, repaired_sql):
                raise ValueError("semantic_constraint_mismatch")
            cols, rows = _run_read_only_query(repaired_sql)
            return (
                repaired_sql,
                cols,
                rows,
                f"SQL was repaired after execution issue ({type(exc).__name__}).",
                {**repair_meta, "mode": repair_mode},
            )
        except Exception:
            if ASK_ALLOW_HEURISTIC_FALLBACK:
                fb = _sanitize_and_validate_sql(_fallback_sql(question, townland_hint))
                cols, rows = _run_read_only_query(fb)
                return (
                    fb,
                    cols,
                    rows,
                    f"Emergency heuristic SQL used after execution failure ({type(exc).__name__}).",
                    {"provider": "local_fallback", "model": "rule_template", "mode": "fallback_rule"},
                )
            diagnostic_sql = _sanitize_and_validate_sql(_diagnostic_message_sql(
                "I could not produce a validated SQL query that safely answers this question. "
                "Please rephrase it with a clearer townland, surname, year, ship, record type, or measure."
            ))
            cols, rows = _run_read_only_query(diagnostic_sql)
            return (
                diagnostic_sql,
                cols,
                rows,
                f"Returned safe guidance after SQL execution failure ({type(exc).__name__}).",
                {"provider": "validation_guard", "model": "validated_sql_only", "mode": "no_validated_sql"},
            )


def _requires_verified_fallback(question: str, sql: str) -> bool:
    q, s = question.lower(), sql.lower()
    clearances_col = _clearances_count_column().lower()
    has_clearances_metric = "clearances_record" in s and bool(
        re.search(rf"\b(?:cr\.)?{re.escape(clearances_col)}\b", s)
    )
    if "emigra" in q and "has_emigration_record" not in s:
        return True
    if ("evict" in q or "clearance" in q) and ("has_eviction_record" not in s and not has_clearances_metric):
        return True
    if "tenant" in q and "has_tenancy_record" not in s:
        return True
    if any(x in q for x in ["population", "census", "inhabited", "uninhabited"]) and "census_record" not in s:
        return True
    if ("20km" in q or "20 km" in q or "around" in q) and "distance_km" not in s:
        return True
    if "parish" in q and ("how many" in q or "count" in q) and "people" not in q and "civil_parish" not in s:
        return True
    return False


def _should_crosscheck(question: str) -> bool:
    q = question.lower()
    return "emigra" in q and ("how many" in q or "count" in q or "total" in q)


def _single_scalar(columns: list[str], rows: list[dict]) -> float | None:
    if len(columns) != 1 or len(rows) != 1:
        return None
    val = rows[0].get(columns[0])
    try:
        return float(val)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# VRTI Knowledge Graph enrichment
# ─────────────────────────────────────────────────────────────────────────────

def _vrti_temporarily_unavailable() -> bool:
    with _vrti_cache_lock:
        return float(_VRTI_STATUS_CACHE.get("down_until") or 0.0) > time.time()


def _mark_vrti_temporarily_unavailable() -> None:
    with _vrti_cache_lock:
        _VRTI_STATUS_CACHE["down_until"] = time.time() + _VRTI_UNAVAILABLE_COOLDOWN


def _get_cached_parish_data(county: str) -> tuple[int | None, list[str]]:
    if _vrti_temporarily_unavailable():
        return None, []
    cache_key = f"parishes:{county}"
    now = time.time()
    with _vrti_cache_lock:
        entry = _VRTI_PARISH_CACHE.get(cache_key)
        if entry and entry["expires_at"] > now:
            return entry["count"], entry["parishes"]
    try:
        from backend.integrations import vrti_sparql
        parishes = vrti_sparql.get_parish_names(county=county, limit=200)
        if not parishes:
            _mark_vrti_temporarily_unavailable()
            return None, []
        count = len(parishes)
        with _vrti_cache_lock:
            _VRTI_PARISH_CACHE[cache_key] = {"count": count, "parishes": parishes,
                                              "expires_at": now + _VRTI_CACHE_TTL}
        return count, parishes
    except Exception as exc:
        _mark_vrti_temporarily_unavailable()
        log.warning("ask_service._get_cached_parish_data failed: %s", exc)
        return None, []


def _get_local_townland_context(names: list[str]) -> dict[str, Any] | None:
    out: dict[str, Any] = {
        "source": "local_townland_reference",
        "townlands": [],
        "parishes": [],
        "parish_count": None,
    }
    conn = get_db_conn()
    try:
        norm_names = [_norm_townland(name) for name in names if _norm_townland(name)]
        if norm_names:
            placeholders = ",".join("?" for _ in norm_names)
            townland_rows = conn.execute(
                f"""
                SELECT
                  name, name_gaelic, civil_parish, barony, county,
                  kg_uri, centroid_lat, centroid_lon
                FROM townland
                WHERE UPPER(name) IN ({placeholders})
                ORDER BY name
                """,
                tuple(norm_names),
            ).fetchall()
            out["townlands"] = [dict(row) for row in townland_rows]

        parish_rows = conn.execute(
            """
            SELECT DISTINCT civil_parish
            FROM townland
            WHERE civil_parish IS NOT NULL AND TRIM(civil_parish) <> ''
            ORDER BY civil_parish
            """
        ).fetchall()
        parishes = [row["civil_parish"] for row in parish_rows if row["civil_parish"]]
        out["parishes"] = parishes
        out["parish_count"] = len(parishes) if parishes else None
    finally:
        conn.close()

    if not out["townlands"] and out["parish_count"] is None:
        return None
    return out


def _kg_context(question: str, townland_hint: str | None, force: bool = False) -> tuple[dict | None, list[str]]:
    q = question.lower()
    if not force and not any(w in q for w in ["townland", "parish", "barony", "around", "county"]):
        return None, []

    from backend.integrations import vrti_sparql
    out: dict[str, Any] = {"source": "vrti_kg", "townlands": [], "parishes": [], "parish_count": None}
    warnings: list[str] = []

    names: list[str] = []
    if townland_hint:
        names.append(townland_hint)

    conn = get_db_conn()
    try:
        rows = conn.execute(
            "SELECT name FROM townland WHERE instr(?,lower(name))>0 ORDER BY length(name) DESC LIMIT 5",
            (q,),
        ).fetchall()
        for r in rows:
            if _is_likely_townland_candidate(r["name"]):
                names.append(r["name"])
    finally:
        conn.close()

    # De-dup
    seen: set[str] = set()
    unique_names: list[str] = []
    for n in names:
        if not n or n in seen:
            continue
        seen.add(n)
        unique_names.append(n)

    # Parallel VRTI lookups
    vrti_lookup_failed = False
    if unique_names and not _vrti_temporarily_unavailable():
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(unique_names))) as pool:
            fut = {pool.submit(vrti_sparql.get_townland_details_by_name, n, "Wicklow"): n
                   for n in unique_names}
            for f in concurrent.futures.as_completed(fut):
                name = fut[f]
                try:
                    dto = f.result()
                    if dto:
                        out["townlands"].append({
                            "name": dto.name, "name_gaelic": dto.name_gaelic,
                            "civil_parish": dto.civil_parish, "barony": dto.barony,
                            "county": dto.county, "kg_uri": dto.uri,
                            "centroid_lat": dto.centroid_lat, "centroid_lon": dto.centroid_lon,
                        })
                except Exception as exc:
                    vrti_lookup_failed = True
                    _mark_vrti_temporarily_unavailable()
                    log.warning("ask_service.kg_townland_lookup_failed name=%s error=%s", name, exc)

    parish_count, parish_names = _get_cached_parish_data("Wicklow")
    if parish_count == 0 and not parish_names:
        parish_count = None
    out["parish_count"] = parish_count
    out["parishes"] = parish_names

    used_local_fallback = False
    if not out["townlands"] or out["parish_count"] is None:
        local_context = _get_local_townland_context(unique_names)
        if local_context:
            if not out["townlands"] and local_context.get("townlands"):
                out["townlands"] = local_context["townlands"]
                used_local_fallback = True
            if out["parish_count"] is None and local_context.get("parish_count") is not None:
                out["parish_count"] = local_context["parish_count"]
                out["parishes"] = local_context.get("parishes", [])
                used_local_fallback = True
            if used_local_fallback:
                out["source"] = "local_townland_reference"

    if used_local_fallback:
        warnings.append("VRTI Knowledge Graph unavailable, using local townland reference data.")
    elif parish_count is None:
        warnings.append("VRTI parish context unavailable.")
    elif vrti_lookup_failed:
        warnings.append("Some VRTI townland lookups failed.")

    if not out["townlands"] and out["parish_count"] is None:
        return None, warnings
    return out, warnings


def _kg_context_to_table(kg_context: dict | None) -> tuple[list[str], list[dict]]:
    if not kg_context:
        return [], []
    cols = ["name", "name_gaelic", "civil_parish", "barony", "county", "kg_uri",
            "centroid_lat", "centroid_lon"]
    rows = [{c: t.get(c) for c in cols} for t in kg_context.get("townlands", [])]
    return cols, rows


# ─────────────────────────────────────────────────────────────────────────────
# Output builders
# ─────────────────────────────────────────────────────────────────────────────

def _friendly_metric_name(metric: str) -> str:
    raw = (metric or "").strip().lower()
    known = {
        "emigrated_people": "emigrated people",
        "total_emigrated_people": "total emigrated people",
        "emigrated_people_within_20km": "emigrated people within 20km",
        "total_evictions": "total evictions",
        "total_evictions_within_20km": "total evictions within 20km",
        "evicted_people": "evicted people",
        "tenant_people": "tenant people",
        "people_in_townland": "people recorded in this townland",
        "matching_people": "matching people",
        "population": "population",
        "total_population": "total population",
        "estate_population": "estate population",
        "townland_count": "townland count",
    }
    if raw in known:
        return known[raw]
    return raw.replace("_", " ")


def _display_townland_name(value: str | None) -> str | None:
    if not value:
        return None
    return value.title() if value.isupper() else value


def _numeric_value(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _sample_names(rows: list[dict], key: str, limit: int = 5) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for row in rows:
        value = str(row.get(key) or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
        if len(out) >= limit:
            break
    return out


def _grouped_answer(columns: list[str], rows: list[dict]) -> str | None:
    if len(columns) < 2 or len(rows) < 2:
        return None
    group_col = columns[0]
    metric_col = columns[1]
    numeric_rows = [row for row in rows if _numeric_value(row.get(metric_col)) is not None]
    if not numeric_rows:
        return None

    top_row = max(numeric_rows, key=lambda row: _numeric_value(row.get(metric_col)) or float("-inf"))
    metric_name = _friendly_metric_name(metric_col)
    top_group = top_row.get(group_col)
    top_value = top_row.get(metric_col)

    if group_col == "year":
        years = [int(row.get(group_col)) for row in numeric_rows if _numeric_value(row.get(group_col)) is not None]
        if years:
            return (
                f"I found {len(rows)} yearly results. "
                f"The highest {metric_name} is {top_value} in {top_group}. "
                f"The range returned runs from {min(years)} to {max(years)}."
            )

    previews = []
    for row in numeric_rows[:3]:
        previews.append(f"{row.get(group_col)} ({row.get(metric_col)})")
    preview_text = ", ".join(previews)
    return (
        f"I found {len(rows)} grouped results for {metric_name}. "
        f"Top rows: {preview_text}."
    )


def _normalise_number_token(token: str) -> str:
    try:
        number = float(token)
    except (TypeError, ValueError):
        return token.strip()
    if number.is_integer():
        return str(int(number))
    out = f"{number:.6f}".rstrip("0").rstrip(".")
    return out or str(number)


def _extract_numeric_tokens(text: str) -> set[str]:
    # Collapse thousands-separator commas ("6,016" → "6016") before tokenising,
    # so the LLM formatting numbers with commas doesn't trip the allowlist check.
    cleaned = re.sub(r'(?<=\d),(?=\d{3}(?:[^\d]|$))', '', text or "")
    return {
        _normalise_number_token(match)
        for match in re.findall(r"-?\d+(?:\.\d+)?", cleaned)
    }


def _allowed_rewrite_number_tokens(
    actual_answer: str,
    summary_block: dict[str, Any],
    data_context: dict[str, Any],
    supporting_context: dict[str, Any],
    kg_context: dict | None,
) -> set[str]:
    payload = {
        "actual_answer": actual_answer,
        "summary": summary_block,
        "data_context": data_context,
        "supporting_context": supporting_context,
        "townland_context": (kg_context or {}).get("townlands", []),
    }
    base = _extract_numeric_tokens(json.dumps(payload, ensure_ascii=False, default=str))
    # Also allow numeric digit-subsequences of every allowed token (e.g. "6016"
    # permits "6", "60", "601", "6016", "16", etc.) so the LLM can naturally
    # rephrase "6,016 people" as "over 6,000" without tripping the check.
    expanded: set[str] = set(base)
    for tok in base:
        if tok.lstrip("-").isdigit() and len(tok) > 1:
            digits = tok.lstrip("-")
            for start in range(len(digits)):
                for end in range(start + 1, len(digits) + 1):
                    expanded.add(str(int(digits[start:end])))
    return expanded


def _assert_rewrite_numbers_supported(
    rewrite_text: str,
    actual_answer: str,
    summary_block: dict[str, Any],
    data_context: dict[str, Any],
    supporting_context: dict[str, Any],
    kg_context: dict | None,
) -> None:
    generated_numbers = _extract_numeric_tokens(rewrite_text)
    if not generated_numbers:
        return
    allowed_numbers = _allowed_rewrite_number_tokens(
        actual_answer=actual_answer,
        summary_block=summary_block,
        data_context=data_context,
        supporting_context=supporting_context,
        kg_context=kg_context,
    )
    unsupported = sorted(number for number in generated_numbers if number not in allowed_numbers)
    if unsupported:
        raise RuntimeError(
            "LLM rewrite introduced unsupported numeric values: " + ", ".join(unsupported[:8])
        )


def _build_chart_spec(
    *,
    question: str,
    columns: list[str],
    rows: list[dict],
    availability: dict[str, Any] | None,
    chart_hint: str | None = None,
) -> dict[str, Any] | None:
    if not availability or not rows:
        return None
    if not availability.get("available") and (not _rows_have_material_data(columns, rows) or _is_message_only_result(columns, rows)):
        return None

    if len(rows) == 1 and {"population_1841", "population_1861", "most_populous_1841", "most_populous_1861"}.issubset(set(columns)):
        row = rows[0]
        return {
            "type": "bar",
            "title": "Most Populous Townland Comparison",
            "x_label": "Census year",
            "y_label": "population",
            "labels": [
                f"1841: {row.get('most_populous_1841')}",
                f"1861: {row.get('most_populous_1861')}",
            ],
            "values": [
                _numeric_value(row.get("population_1841")),
                _numeric_value(row.get("population_1861")),
            ],
        }

    numeric_columns = [column for column in columns[1:] if any(_numeric_value(row.get(column)) is not None for row in rows)]
    if len(rows) > 1 and columns and numeric_columns:
        label_col = columns[0]
        metric_col = numeric_columns[0]
        sample_rows = rows[:12]
        labels = [str(row.get(label_col) or "") for row in sample_rows]
        values = [_numeric_value(row.get(metric_col)) or 0.0 for row in sample_rows]
        chart_type = chart_hint or ("line" if label_col == "year" else "bar")
        return {
            "type": chart_type,
            "title": _friendly_metric_name(metric_col).title(),
            "x_label": label_col.replace("_", " "),
            "y_label": metric_col.replace("_", " "),
            "labels": labels,
            "values": values,
        }

    if len(rows) == 1:
        row = rows[0]
        metric_pairs = []
        for column in columns:
            value = _numeric_value(row.get(column))
            if value is None:
                continue
            metric_pairs.append((column, value))
        if len(metric_pairs) >= 2:
            chosen = metric_pairs[:8]
            return {
                "type": chart_hint or "bar",
                "title": "Returned metrics",
                "x_label": "metric",
                "y_label": "value",
                "labels": [pair[0].replace("_", " ") for pair in chosen],
                "values": [pair[1] for pair in chosen],
            }
    return None


_LIST_COLUMN_WORDS = {"list", "names", "people", "persons", "sample", "members"}


def _is_list_column(col: str) -> bool:
    return any(w in col.lower() for w in _LIST_COLUMN_WORDS)


def _summarise_cell(col: str, val: Any, max_chars: int = 60) -> str:
    """Return a display-safe cell value: list-like long strings become item counts."""
    if val is None:
        return "—"
    s = str(val)
    if len(s) <= max_chars:
        return s
    # Looks like a comma-separated list — count items
    parts = [p.strip() for p in s.split(",") if p.strip()]
    if len(parts) > 3:
        return f"{len(parts)} names (see table)"
    return s[:max_chars] + "…"


def _detail_answer(columns: list[str], row: dict, townland_hint: str | None) -> str:
    display_townland = _display_townland_name(townland_hint)
    if {"townland", "year", "population"}.issubset(set(columns)):
        inhabited = row.get("inhabited")
        uninhabited = row.get("uninhabited")
        extra = []
        if row.get("male") is not None and row.get("female") is not None:
            extra.append(f"{row.get('male')} male and {row.get('female')} female")
        if inhabited is not None:
            extra.append(f"{inhabited} inhabited houses")
        if uninhabited is not None:
            extra.append(f"{uninhabited} uninhabited houses")
        suffix = f" ({'; '.join(extra)})" if extra else ""
        return f"For {row.get('townland')} in {row.get('year')}, the recorded population is {row.get('population')}{suffix}."
    if "person_name" in columns:
        pieces = [row.get("person_name")]
        if row.get("townland"):
            pieces.append(f"townland {row.get('townland')}")
        if row.get("parish"):
            pieces.append(f"parish {row.get('parish')}")
        if row.get("year"):
            pieces.append(f"year {row.get('year')}")
        return "Matching person record: " + ", ".join(str(p) for p in pieces if p) + "."
    # Prefer short numeric/count columns; skip long list dumps
    preferred = [c for c in columns if not _is_list_column(c)][:5] or columns[:5]
    shown = [f"{c.replace('_', ' ')}={_summarise_cell(c, row.get(c))}" for c in preferred]
    context = f" for {display_townland}" if display_townland else ""
    return f"I found one matching row{context}: " + ", ".join(shown) + "."


def _is_message_only_result(columns: list[str], rows: list[dict]) -> bool:
    return bool(rows and len(columns) == 1 and columns[0] in {"message", "availability_message", "diagnostic_message"})


def _data_answer_text(
    question: str,
    columns: list[str],
    rows: list[dict],
    townland_hint: str | None,
) -> str:
    analysis = _analyse_question(question, townland_hint)
    display_townland = _display_townland_name(townland_hint)

    if _is_message_only_result(columns, rows):
        return _clean_message_result_text(rows[0].get(columns[0]))

    if len(rows) == 1 and len(columns) == 1:
        key = columns[0]
        value = rows[0].get(key)
        metric_name = _friendly_metric_name(key)
        if analysis.get("scope") == "radius" and display_townland:
            return f"I found {value} {metric_name} within {analysis.get('radius_km') or 20}km of {display_townland}."
        if analysis.get("scope") == "townland" and display_townland:
            return f"I found {value} {metric_name} for {display_townland}."
        return f"I found {value} {metric_name}."

    if len(rows) == 1:
        return _detail_answer(columns, rows[0], townland_hint)

    grouped = _grouped_answer(columns, rows)
    if grouped:
        return grouped

    if "person_name" in columns:
        names = _sample_names(rows, "person_name", limit=5)
        context = f" for {display_townland}" if display_townland else ""
        sample = f" Examples: {', '.join(names)}." if names else ""
        return f"I found {len(rows)} matching people records{context}.{sample}"

    context = f" for {display_townland}" if display_townland else ""
    return f"I found {len(rows)} matching rows{context}."


def _rows_have_material_data(columns: list[str], rows: list[dict]) -> bool:
    if not rows:
        return False
    for row in rows:
        for column in columns:
            value = row.get(column)
            if value not in {None, ""}:
                return True
    return False


def _suggest_rephrasings(
    question: str,
    analysis: dict[str, Any],
    townland_resolution: dict[str, Any],
) -> list[str]:
    suggestions: list[str] = []
    townland = _display_townland_name(townland_resolution.get("name"))
    surname = analysis.get("surname")

    if townland_resolution.get("suggestions"):
        suggestion = townland_resolution["suggestions"][0].get("name")
        if suggestion:
            suggestions.append(f"Try the townland hint '{suggestion}' and ask again.")

    if analysis.get("primary_intent") == "population":
        if townland:
            suggestions.append(f"What was the population of {townland} in 1841, 1851, or 1861?")
        else:
            suggestions.append("Try a population question for 1841, 1851, or 1861, which are the strongest early census years in this database.")
    elif analysis.get("asks_emigration"):
        if townland:
            suggestions.append(f"How many people emigrated from {townland}?")
            suggestions.append(f"Which ships carried emigrants from {townland}?")
        else:
            suggestions.append("Try asking about emigration by year, ship, or townland.")
    elif analysis.get("asks_eviction"):
        suggestions.append("Try asking for eviction records by year or by townland.")
    elif analysis.get("asks_tenancy"):
        suggestions.append("Try asking for tenants by townland, holding size, or latest recorded year.")

    if surname:
        suggestions.append(f"Try 'How many people are named {str(surname).title()}?' or 'List all {str(surname).title()} records by townland.'")
    else:
        suggestions.append("Try asking for a surname, townland, year, ship, or record type explicitly.")

    deduped: list[str] = []
    for item in suggestions:
        if item and item not in deduped:
            deduped.append(item)
        if len(deduped) >= 4:
            break
    return deduped


def _build_availability_payload(
    *,
    question: str,
    analysis: dict[str, Any],
    columns: list[str],
    rows: list[dict],
    townland_resolution: dict[str, Any],
) -> dict[str, Any]:
    available = _rows_have_material_data(columns, rows)
    if _is_message_only_result(columns, rows):
        text = _clean_message_result_text(rows[0].get(columns[0]))
        lowered = text.lower()
        if any(token in lowered for token in [
            "unavailable",
            "not available",
            "no data",
            "no available data",
            "could not find",
            "could not build",
            "could not produce",
        ]) or re.search(r"\bno\b.+\bdata\b", lowered) or re.search(r"\bno\b.+\bavailable\b", lowered):
            state = "partial_unavailable" if (
                analysis.get("year")
                or "available years" in lowered
                or ("unavailable" in lowered and "year" in lowered)
            ) else "no_data"
            return {
                "available": False,
                "state": state,
                "message": text,
                "suggestions": _suggest_rephrasings(question, analysis, townland_resolution),
            }
    requested_year = analysis.get("year")
    year_columns = [name for name in columns if name in {"year", "census_year"}]
    if requested_year and rows and year_columns:
        matched_year = False
        for row in rows:
            for year_column in year_columns:
                try:
                    if int(row.get(year_column)) == int(requested_year):
                        matched_year = True
                        break
                except (TypeError, ValueError):
                    continue
            if matched_year:
                break
        if not matched_year:
            return {
                "available": False,
                "state": "partial_unavailable",
                "message": f"The asked year {requested_year} is not available in the current database result. The table below shows the nearest related data that could be found instead.",
                "suggestions": _suggest_rephrasings(question, analysis, townland_resolution),
            }
    if available:
        return {
            "available": True,
            "state": "available",
            "message": "The requested data is available in the current database.",
            "suggestions": [],
        }

    message = "The asked data is not available in the current database for this wording or filter."
    if "1821" in question and analysis.get("primary_intent") == "population":
        message = "The asked census year is not available here. The current Ask census data begins in 1841 rather than 1821."
    elif townland_resolution.get("warning"):
        message = "The asked data could not be matched cleanly because the townland reference is incomplete or ambiguous."
    elif analysis.get("surname"):
        message = f"I could not find matching database rows for the surname {str(analysis['surname']).title()} with the current filter."

    return {
        "available": False,
        "state": "no_data",
        "message": message + " Try rephrasing the question or broadening it.",
        "suggestions": _suggest_rephrasings(question, analysis, townland_resolution),
    }


def _build_related_insights(
    *,
    question: str,
    analysis: dict[str, Any],
    rows: list[dict],
    townland_norm: str | None,
) -> list[dict[str, str]]:
    insights: list[dict[str, str]] = []
    q = question.lower()
    conn = get_db_conn()
    try:
        if "widow" in q:
            row = conn.execute(
                """
                SELECT
                  COUNT(DISTINCT record_id) AS widow_records,
                  COUNT(DISTINCT CASE WHEN has_eviction_record=1 THEN record_id END) AS widows_on_eviction_records,
                  COUNT(DISTINCT CASE WHEN children_count > 0 THEN record_id END) AS widows_with_children
                FROM unified_record
                WHERE is_widow=1
                """
            ).fetchone()
            if row:
                insights.append({
                    "label": "Widow context",
                    "value": (
                        f"{int(row['widow_records'] or 0)} widow records appear overall; "
                        f"{int(row['widows_on_eviction_records'] or 0)} also appear on eviction records; "
                        f"{int(row['widows_with_children'] or 0)} have recorded children."
                    ),
                })

        surname = analysis.get("surname")
        if surname:
            row = conn.execute(
                """
                SELECT
                  COUNT(DISTINCT record_id) AS matching_people,
                  COUNT(DISTINCT CASE WHEN has_emigration_record=1 THEN record_id END) AS emigrants,
                  COUNT(DISTINCT CASE WHEN has_eviction_record=1 THEN record_id END) AS evicted,
                  COUNT(DISTINCT CASE WHEN has_tenancy_record=1 THEN record_id END) AS tenants,
                  MIN(year) AS first_year,
                  MAX(year) AS last_year
                FROM unified_record
                WHERE UPPER(surname)=?
                """,
                (str(surname).upper(),),
            ).fetchone()
            if row and row["matching_people"] is not None:
                insights.append({
                    "label": "Surname span",
                    "value": (
                        f"{row['matching_people']} record(s) use the surname {str(surname).title()}, "
                        f"spanning {row['first_year']} to {row['last_year']}; "
                        f"{row['emigrants']} include emigration, {row['evicted']} eviction, and {row['tenants']} tenancy records."
                    ),
                })
            top_townlands = [
                dict(r) for r in conn.execute(
                    """
                    SELECT townland, COUNT(DISTINCT record_id) AS matching_people
                    FROM unified_record
                    WHERE UPPER(surname)=?
                      AND townland IS NOT NULL
                      AND TRIM(townland) <> ''
                    GROUP BY townland_norm, townland
                    ORDER BY matching_people DESC, townland
                    LIMIT 3
                    """,
                    (str(surname).upper(),),
                ).fetchall()
            ]
            if top_townlands:
                insights.append({
                    "label": "Top surname townlands",
                    "value": ", ".join(f"{r['townland']} ({r['matching_people']})" for r in top_townlands),
                })

        if analysis.get("asks_emigration"):
            peak = conn.execute(
                """
                SELECT year, COUNT(DISTINCT record_id) AS emigrants
                FROM unified_record
                WHERE has_emigration_record=1
                  AND year IS NOT NULL
                GROUP BY year
                ORDER BY emigrants DESC, year
                LIMIT 1
                """
            ).fetchone()
            if peak and peak["year"] is not None:
                insights.append({
                    "label": "Peak emigration year",
                    "value": f"The highest recorded emigration volume in the local database is {peak['emigrants']} people in {peak['year']}.",
                })
            if townland_norm:
                ships = [
                    dict(r) for r in conn.execute(
                        """
                        SELECT ship_name, COUNT(DISTINCT record_id) AS emigrants
                        FROM unified_record
                        WHERE has_emigration_record=1
                          AND townland_norm=?
                          AND ship_name IS NOT NULL
                          AND TRIM(ship_name) <> ''
                        GROUP BY ship_name
                        ORDER BY emigrants DESC, ship_name
                        LIMIT 3
                        """,
                        (_norm_townland(townland_norm),),
                    ).fetchall()
                ]
                if ships:
                    insights.append({
                        "label": "Top ships for this townland",
                        "value": ", ".join(f"{r['ship_name']} ({r['emigrants']})" for r in ships),
                    })

        if analysis.get("primary_intent") == "population" and townland_norm:
            peak_pop = conn.execute(
                """
                SELECT c.year, c.total
                FROM census_record c
                JOIN townland t ON c.townland_id=t.id
                WHERE UPPER(t.name)=?
                ORDER BY c.total DESC, c.year
                LIMIT 1
                """,
                (_norm_townland(townland_norm),),
            ).fetchone()
            if peak_pop:
                insights.append({
                    "label": "Peak matched population",
                    "value": f"The highest recorded census population for this townland is {peak_pop['total']} in {peak_pop['year']}.",
                })
    finally:
        conn.close()

    if not insights and rows:
        numeric_cols = [k for k in rows[0].keys() if _numeric_value(rows[0].get(k)) is not None]
        if rows and numeric_cols:
            metric_col = numeric_cols[0]
            top_row = max(rows, key=lambda row: _numeric_value(row.get(metric_col)) or float("-inf"))
            insights.append({
                "label": "Top returned row",
                "value": ", ".join(f"{key}={top_row.get(key)}" for key in list(top_row.keys())[:4]),
            })
    return insights[:4]


def _build_answer_text(question: str, columns: list[str], rows: list[dict],
                       townland_hint: str | None, kg_context: dict | None,
                       availability: dict[str, Any] | None = None) -> str:
    if availability and not availability.get("available"):
        message = str(availability.get("message") or "The asked data is not available in the current database.")
        if _rows_have_material_data(columns, rows) and not _is_message_only_result(columns, rows):
            return f"{message} {_data_answer_text(question, columns, rows, townland_hint)}"
        return message
    return _data_answer_text(question, columns, rows, townland_hint)


def _build_structured_summary(question: str, local_columns: list[str], local_rows: list[dict],
                               vrti_columns: list[str], vrti_rows: list[dict],
                               kg_context: dict | None,
                               availability: dict[str, Any] | None = None,
                               related_insights: list[dict[str, str]] | None = None) -> dict[str, Any]:
    primary: str | None = None
    grouped = _grouped_answer(local_columns, local_rows)

    if availability and not availability.get("available"):
        primary = str(availability.get("message") or "The asked data is not available in the current database.")
        if _rows_have_material_data(local_columns, local_rows) and not _is_message_only_result(local_columns, local_rows):
            primary = f"{primary} {_data_answer_text(question, local_columns, local_rows, None)}"
    elif len(local_rows) == 1 and len(local_columns) == 1:
        key = local_columns[0]
        primary = f"{_friendly_metric_name(key).title()}: {local_rows[0].get(key)}"
    elif len(local_rows) == 1 and local_columns:
        primary = _detail_answer(local_columns, local_rows[0], None)
    elif grouped:
        primary = grouped

    stats: dict[str, Any] = {"local_records_returned": len(local_rows), "vrti_townlands_enriched": len(vrti_rows)}
    if primary:
        stats["primary_answer"] = primary
    if availability:
        stats["availability_state"] = availability.get("state")

    lines = [f"Query: {question}"]
    if primary:
        lines.append(f"Answer: {primary}")
    else:
        lines.append(f"Local database returned {len(local_rows)} row{'s' if len(local_rows)!=1 else ''}.")
    if vrti_rows:
        t_names = ", ".join(r.get("name","") for r in vrti_rows[:3] if r.get("name"))
        lines.append(f"VRTI enriched {len(vrti_rows)} townland(s){': ' + t_names if t_names else ''}.")
    if related_insights:
        lines.append("Related insights: " + " | ".join(f"{item.get('label')}: {item.get('value')}" for item in related_insights[:3]))
    lines.append("Sources: Coolattin estate records (SQLite) + VRTI Knowledge Graph (SPARQL).")

    return {
        "stats": stats,
        "final_summary_text": "  ".join(lines),
        "parish_sample": [],
        "related_insights": related_insights or [],
    }


def _build_llm_data_context(
    local_columns: list[str],
    local_rows: list[dict],
    vrti_columns: list[str],
    vrti_rows: list[dict],
    sample_limit: int = 25,
) -> dict[str, Any]:
    """
    Compact, explicit data view for LLM rewriting only.
    The full rows are still returned separately in processed_tables.
    """
    def _truncate_row(row: dict, max_val_len: int = 300) -> dict:
        out = {}
        for k, v in row.items():
            s = str(v) if v is not None else ""
            out[k] = (s[:max_val_len] + f"…[{len(s)} chars total]") if len(s) > max_val_len else v
        return out

    return {
        "local_database": {
            "columns": local_columns,
            "row_count": len(local_rows),
            "sample_limit": sample_limit,
            "sample_rows": [_truncate_row(r) for r in local_rows[:sample_limit]],
            "truncated": len(local_rows) > sample_limit,
        },
        "vrti_graph": {
            "columns": vrti_columns,
            "row_count": len(vrti_rows),
            "sample_limit": sample_limit,
            "sample_rows": [_truncate_row(r) for r in vrti_rows[:sample_limit]],
            "truncated": len(vrti_rows) > sample_limit,
        },
    }


def _build_supporting_context(
    question: str,
    townland_norm: str | None,
    townland_resolution: dict[str, Any],
    primary_columns: list[str],
    primary_rows: list[dict],
    kg_context: dict | None,
    related_insights: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """
    Extra bounded context for broad questions and answer rewriting.
    This is intentionally summarized/sampled so we do not send the whole DB
    to the LLM, while still giving it enough verified local data to work from.
    """
    context: dict[str, Any] = {
        "database_profile": _database_profile_context(),
        "townland_resolution": townland_resolution,
        "primary_result": {
            "columns": primary_columns,
            "row_count": len(primary_rows),
            "sample_rows": primary_rows[:20],
            "truncated": len(primary_rows) > 20,
        },
        "kg_source": (kg_context or {}).get("source"),
    }
    if related_insights:
        context["related_insights"] = related_insights[:4]
    if townland_norm:
        context["townland_detail"] = _townland_deep_context(townland_norm)
    keyword_context = _keyword_search_context(question, townland_norm)
    if keyword_context.get("rows"):
        context["keyword_matches"] = keyword_context
    return context


def _database_profile_context() -> dict[str, Any]:
    clear_col = _clearances_count_column()
    conn = get_db_conn()
    try:
        profile = dict(conn.execute(f"""
            SELECT
              (SELECT COUNT(*) FROM townland) AS townland_count,
              (SELECT COUNT(DISTINCT civil_parish)
                 FROM townland
                 WHERE civil_parish IS NOT NULL AND TRIM(civil_parish) <> '') AS parish_count,
              (SELECT COUNT(DISTINCT record_id) FROM unified_record) AS people_record_count,
              (SELECT COUNT(DISTINCT record_id)
                 FROM unified_record WHERE has_emigration_record=1) AS emigrated_people,
              (SELECT COUNT(DISTINCT record_id)
                 FROM unified_record WHERE has_eviction_record=1) AS evicted_people,
              (SELECT COUNT(DISTINCT record_id)
                 FROM unified_record WHERE has_tenancy_record=1) AS tenant_people,
              (SELECT COUNT(DISTINCT townland_norm)
                 FROM heritage_feature
                 WHERE feature_group='holy_well' AND townland_norm IS NOT NULL AND TRIM(townland_norm) <> '') AS townlands_with_holy_wells,
              (SELECT COUNT(DISTINCT townland_norm)
                 FROM heritage_feature
                 WHERE feature_group='ring_fort' AND townland_norm IS NOT NULL AND TRIM(townland_norm) <> '') AS townlands_with_ring_forts,
              (SELECT MIN(year) FROM unified_record) AS first_record_year,
              (SELECT MAX(year) FROM unified_record) AS last_record_year,
              (SELECT COALESCE(SUM({clear_col}), 0) FROM clearances_record) AS clearance_events
        """).fetchone())
        top_townlands = [
            dict(row) for row in conn.execute("""
                SELECT townland, COUNT(DISTINCT record_id) AS people_records
                FROM unified_record
                WHERE townland IS NOT NULL AND TRIM(townland) <> ''
                GROUP BY townland_norm, townland
                ORDER BY people_records DESC, townland
                LIMIT 8
            """).fetchall()
        ]
        top_surnames = [
            dict(row) for row in conn.execute("""
                SELECT surname, COUNT(DISTINCT record_id) AS people_records
                FROM unified_record
                WHERE surname IS NOT NULL AND TRIM(surname) <> ''
                GROUP BY UPPER(surname), surname
                ORDER BY people_records DESC, surname
                LIMIT 8
            """).fetchall()
        ]
        census_years = [
            dict(row) for row in conn.execute("""
                SELECT year, SUM(total) AS estate_population
                FROM census_record
                GROUP BY year
                ORDER BY year
                LIMIT 20
            """).fetchall()
        ]
        return {
            **profile,
            "top_townlands_by_people_records": top_townlands,
            "top_surnames": top_surnames,
            "census_year_totals": census_years,
        }
    finally:
        conn.close()


def _townland_deep_context(townland_norm: str) -> dict[str, Any]:
    clear_col = _clearances_count_column()
    conn = get_db_conn()
    try:
        townland_row = conn.execute(
            """
            SELECT
              id, name, name_gaelic, civil_parish, barony, county,
              centroid_lat, centroid_lon, kg_uri
            FROM townland
            WHERE UPPER(name)=?
            LIMIT 1
            """,
            (_norm_townland(townland_norm),),
        ).fetchone()
        if not townland_row:
            return {"found": False, "townland_norm": townland_norm}
        townland = dict(townland_row)
        townland_id = townland["id"]
        record_summary = dict(conn.execute(
            """
            SELECT
              COUNT(DISTINCT record_id) AS people_records,
              COUNT(DISTINCT CASE WHEN has_emigration_record=1 THEN record_id END) AS emigrated_people,
              COUNT(DISTINCT CASE WHEN has_eviction_record=1 THEN record_id END) AS evicted_people,
              COUNT(DISTINCT CASE WHEN has_tenancy_record=1 THEN record_id END) AS tenant_people,
              MIN(year) AS first_record_year,
              MAX(year) AS last_record_year
            FROM unified_record
            WHERE townland_norm=?
            """,
            (_norm_townland(townland_norm),),
        ).fetchone())
        census = [
            dict(row) for row in conn.execute(
                """
                SELECT year, total, male, female, inhabited, uninhabited
                FROM census_record
                WHERE townland_id=?
                ORDER BY year
                LIMIT 50
                """,
                (townland_id,),
            ).fetchall()
        ]
        clearances = [
            dict(row) for row in conn.execute(
                f"""
                SELECT year, {clear_col} AS eviction_count
                FROM clearances_record
                WHERE townland_id=?
                ORDER BY year
                LIMIT 50
                """,
                (townland_id,),
            ).fetchall()
        ]
        surnames = [
            dict(row) for row in conn.execute(
                """
                SELECT surname, COUNT(DISTINCT record_id) AS people_records
                FROM unified_record
                WHERE townland_norm=?
                  AND surname IS NOT NULL
                  AND TRIM(surname) <> ''
                GROUP BY UPPER(surname), surname
                ORDER BY people_records DESC, surname
                LIMIT 12
                """,
                (_norm_townland(townland_norm),),
            ).fetchall()
        ]
        ships = [
            dict(row) for row in conn.execute(
                """
                SELECT ship_name, COUNT(DISTINCT record_id) AS emigrants
                FROM unified_record
                WHERE townland_norm=?
                  AND has_emigration_record=1
                  AND ship_name IS NOT NULL
                  AND TRIM(ship_name) <> ''
                GROUP BY ship_name
                ORDER BY emigrants DESC, ship_name
                LIMIT 10
                """,
                (_norm_townland(townland_norm),),
            ).fetchall()
        ]
        sample_people = [
            dict(row) for row in conn.execute(
                """
                SELECT
                  COALESCE(NULLIF(TRIM(canonical_name),''),
                           TRIM(COALESCE(forename,'') || ' ' || COALESCE(surname,''))) AS person_name,
                  surname, forename, year, parish, role, ship_name,
                  has_emigration_record, has_eviction_record, has_tenancy_record
                FROM unified_record
                WHERE townland_norm=?
                ORDER BY year, person_name
                LIMIT 25
                """,
                (_norm_townland(townland_norm),),
            ).fetchall()
        ]
        return {
            "found": True,
            "townland": townland,
            "record_summary": record_summary,
            "census": census,
            "clearances": clearances,
            "top_surnames": surnames,
            "ships": ships,
            "sample_people": sample_people,
        }
    finally:
        conn.close()


def _keyword_search_context(question: str, townland_norm: str | None) -> dict[str, Any]:
    keywords = _question_keywords(question, limit=5)
    if not keywords:
        return {"keywords": [], "rows": []}

    params: list[Any] = []
    clauses: list[str] = []
    for keyword in keywords:
        like = f"%{keyword.upper()}%"
        clauses.append(
            """(
              UPPER(COALESCE(canonical_name,'')) LIKE ?
              OR UPPER(COALESCE(surname,'')) LIKE ?
              OR UPPER(COALESCE(forename,'')) LIKE ?
              OR UPPER(COALESCE(townland,'')) LIKE ?
              OR UPPER(COALESCE(parish,'')) LIKE ?
              OR UPPER(COALESCE(ship_name,'')) LIKE ?
            )"""
        )
        params.extend([like] * 6)
    where = " OR ".join(clauses)
    if townland_norm:
        where = f"({where}) AND townland_norm=?"
        params.append(_norm_townland(townland_norm))

    conn = get_db_conn()
    try:
        rows = [
            dict(row) for row in conn.execute(
                f"""
                SELECT DISTINCT
                  COALESCE(NULLIF(TRIM(canonical_name),''),
                           TRIM(COALESCE(forename,'') || ' ' || COALESCE(surname,''))) AS person_name,
                  surname, forename, townland, parish, year, ship_name,
                  has_emigration_record, has_eviction_record, has_tenancy_record
                FROM unified_record
                WHERE {where}
                ORDER BY year, person_name
                LIMIT 30
                """,
                tuple(params),
            ).fetchall()
        ]
        return {"keywords": keywords, "rows": rows, "row_count": len(rows)}
    finally:
        conn.close()


def _question_keywords(question: str, limit: int = 6) -> list[str]:
    tokens = re.findall(r"[A-Za-z][A-Za-z'-]{2,}", question or "")
    out: list[str] = []
    for token in tokens:
        norm = token.strip("'").upper()
        if not norm or norm.lower() in _TOWNLAND_STOPWORDS:
            continue
        if norm not in out:
            out.append(norm)
        if len(out) >= limit:
            break
    return out


def _supporting_context_for_display(context: dict[str, Any]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    profile = context.get("database_profile") or {}
    if profile:
        items.append({
            "label": "Database coverage",
            "value": (
                f"{profile.get('townland_count', 0)} townlands, "
                f"{profile.get('parish_count', 0)} parishes, "
                f"{profile.get('people_record_count', 0)} people records "
                f"({profile.get('first_record_year')}-{profile.get('last_record_year')})."
            ),
        })
    resolution = context.get("townland_resolution") or {}
    if resolution.get("matched"):
        items.append({
            "label": "Townland match",
            "value": (
                f"Using {resolution.get('name')} "
                f"({resolution.get('match_type')}, confidence {resolution.get('confidence')})."
            ),
        })
    elif resolution.get("suggestions"):
        names = ", ".join(s.get("name", "") for s in resolution.get("suggestions", [])[:4])
        items.append({"label": "Townland suggestions", "value": f"Did you mean: {names}?"})
    detail = context.get("townland_detail") or {}
    if detail.get("found"):
        summary = detail.get("record_summary") or {}
        townland = detail.get("townland") or {}
        census = detail.get("census") or []
        clearances = detail.get("clearances") or []
        items.append({
            "label": "Matched townland context",
            "value": (
                f"{townland.get('name')} is in parish {townland.get('civil_parish') or 'unknown'}, "
                f"barony {townland.get('barony') or 'unknown'}. "
                f"Records: {summary.get('people_records', 0)} people, "
                f"{summary.get('emigrated_people', 0)} emigrated, "
                f"{summary.get('evicted_people', 0)} evicted, "
                f"{summary.get('tenant_people', 0)} tenants."
            ),
        })
        if census:
            items.append({
                "label": "Census coverage",
                "value": f"{len(census)} census row(s), years {census[0].get('year')} to {census[-1].get('year')}.",
            })
        if clearances:
            total_evictions = sum(int(row.get("eviction_count") or 0) for row in clearances)
            items.append({
                "label": "Clearance coverage",
                "value": f"{total_evictions} clearance event(s) across {len(clearances)} year row(s).",
            })
    keyword_matches = context.get("keyword_matches") or {}
    if keyword_matches.get("rows"):
        items.append({
            "label": "Keyword matches",
            "value": (
                f"{keyword_matches.get('row_count', 0)} sampled row(s) matched "
                f"{', '.join(keyword_matches.get('keywords', []))}."
            ),
        })
    for insight in context.get("related_insights") or []:
        if insight.get("label") and insight.get("value"):
            items.append({
                "label": str(insight.get("label")),
                "value": str(insight.get("value")),
            })
    return items


def _generate_rephrased_answer(
    question: str,
    actual_answer: str,
    summary_block: dict[str, Any],
    data_context: dict[str, Any],
    supporting_context: dict[str, Any],
    kg_context: dict | None,
) -> tuple[str | None, dict[str, Any]]:
    prompt = _build_rephrase_prompt(
        question=question,
        actual_answer=actual_answer,
        summary_block=summary_block,
        data_context=data_context,
        supporting_context=supporting_context,
        kg_context=kg_context,
    )
    text, meta = _llm_generate(
        prompt,
        purpose="answer_rephrase",
        max_tokens=160,
        temperature=0.1,
    )
    cleaned = _strip_answer_formatting(text)
    if not cleaned:
        raise RuntimeError("Empty answer rewrite from LLM.")
    _assert_rewrite_numbers_supported(
        rewrite_text=cleaned,
        actual_answer=actual_answer,
        summary_block=summary_block,
        data_context=data_context,
        supporting_context=supporting_context,
        kg_context=kg_context,
    )
    return cleaned, {**meta, "mode": "llm_rewrite"}


def _build_rephrase_prompt(
    question: str,
    actual_answer: str,
    summary_block: dict[str, Any],
    data_context: dict[str, Any],
    supporting_context: dict[str, Any],
    kg_context: dict | None,
) -> str:
    # Pass only the key facts — a large JSON dump leads to over-long answers.
    townland_names = [t.get("name") for t in (kg_context or {}).get("townlands", [])[:3] if t.get("name")]
    key_stats = summary_block.get("stats", {})
    fuzzy_note = (supporting_context or {}).get("fuzzy_match_note", "")
    # Phase 3 subgraph context injected into kg_context by the SSE pipeline
    subgraph_linearized = (kg_context or {}).get("subgraph_linearized", "")

    prompt_payload = {
        "question": question,
        "data_backed_answer": actual_answer,
        "key_stats": key_stats,
        "townlands_mentioned": townland_names,
        "fuzzy_match_note": fuzzy_note,
    }
    row_count = data_context.get("local_database", {}).get("row_count", 0)
    list_note = (
        f"\n- The result contains {row_count} individual rows. Do NOT list all of them."
        " State the total count and give 1–2 representative examples at most."
        if row_count > 10 else ""
    )
    # Subgraph context block — present only for relational / hierarchy questions
    kg_block = (
        f"\n\nKNOWLEDGE GRAPH CONTEXT (administrative hierarchy and place "
        f"relationships retrieved by subgraph traversal — use for qualitative "
        f"and relational answers; do NOT use to produce counts or statistics):\n"
        f"{subgraph_linearized}"
        if subgraph_linearized else ""
    )
    kg_rule = (
        "\n- If KNOWLEDGE GRAPH CONTEXT is present, use it to answer relational or"
        " hierarchy questions. Never use it to produce counts or numbers."
        if subgraph_linearized else ""
    )
    # Phase 6 fusion: cite detected discrepancies explicitly; for COMPARATIVE
    # questions without numeric discrepancies, note that both sources contributed.
    fusion_note = (kg_context or {}).get("phase6_fusion_note", "")
    fusion_text = (kg_context or {}).get("phase6_fusion_text", "")
    discrepancy_rule = (
        f"\n- SOURCE DISCREPANCY DETECTED — you MUST state this in your answer: {fusion_text}"
        f" Attribute the exact figures to each source (SQLite estate records vs Coolattin RDF graph)."
        if fusion_text else ""
    )
    fusion_rule = (
        f"\n- COMPARATIVE question (Phase 6 fusion): {fusion_note}"
        if fusion_note and not fusion_text else ""
    )
    source_rules = fusion_rule + discrepancy_rule
    return f"""Rephrase this historical archive result in 1–3 sentences of plain English.

Rules:
- Use ONLY the supplied data. Never invent names, numbers, dates, or places.
- Keep every number identical to data_backed_answer.
- If data is unavailable, say so in one sentence.
- If a townland was fuzzy-matched, name which one was used.
- No markdown, no bullet points, no SQL, no preamble. Plain prose only.{list_note}{kg_rule}{source_rules}

DATA:
{json.dumps(prompt_payload, ensure_ascii=False, default=str)}{kg_block}

Answer (1–3 sentences):""".strip()


def _strip_answer_formatting(text: str) -> str:
    out = (text or "").strip()
    out = re.sub(r"^```(?:text|markdown)?\s*", "", out, flags=re.IGNORECASE)
    out = re.sub(r"\s*```\s*$", "", out)
    out = re.sub(r"^(Rephrased answer|Answer)\s*:\s*", "", out, flags=re.IGNORECASE)
    return out.strip()


# ─────────────────────────────────────────────────────────────────────────────
# PDF export
# ─────────────────────────────────────────────────────────────────────────────

def _write_pdf_report(question, answer, sql, columns, rows, llm_meta, kg_context,
                      include_sql=False, vrti_postgres_sql=None, vrti_columns=None,
                      vrti_rows=None, summary_block=None,
                      llm_rephrased_answer=None, llm_rewrite_meta=None) -> Path:
    out_dir = ActiveConfig.EXPORTS_DIR / "ask"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"ask_report_{ts}.pdf"

    lines: list[str] = []
    lines += ["Coolattin Archive – Ask Report",
              f"Generated (UTC): {datetime.now(timezone.utc).isoformat()}", ""]
    lines += ["QUESTION", question, ""]
    lines += ["ACTUAL DATA ANSWER", answer, ""]
    if llm_rephrased_answer:
        lines += ["LLM REPHRASED ANSWER"]
        lines.extend(_wrap_line(str(llm_rephrased_answer)))
        lines += [""]
    lines += [f"SQL LLM: {llm_meta.get('provider')} | {llm_meta.get('model')} | {llm_meta.get('mode')}", ""]
    if llm_rewrite_meta:
        lines += [f"REWRITE LLM: {llm_rewrite_meta.get('provider')} | {llm_rewrite_meta.get('model')} | {llm_rewrite_meta.get('mode')}", ""]
    lines += ["SQL (SQLite – local database)"]
    lines += ["  " + ln for ln in sql.splitlines()]
    lines += [""]
    if vrti_postgres_sql:
        lines += ["SQL (PostgreSQL – VRTI warehouse model)"]
        lines += ["  " + ln for ln in vrti_postgres_sql.splitlines()]
        lines += [""]
    if kg_context:
        for t in kg_context.get("townlands", []):
            lines.append(f"  {t.get('name')} | parish={t.get('civil_parish')} | barony={t.get('barony')}")
        lines += [""]
    lines += [f"LOCAL RESULTS ({len(rows)} rows)"]
    if columns:
        lines.append("Columns: " + " | ".join(columns))
    lines += [""]
    for row in rows[:160]:
        compact = " | ".join(f"{c}={_stringify_pdf(row.get(c))}" for c in columns[:8])
        lines.extend(_wrap_line(compact))
    if len(rows) > 160:
        lines.append(f"  … {len(rows)-160} rows truncated")
    lines += [""]
    if vrti_columns and vrti_rows:
        lines += [f"VRTI RESULTS ({len(vrti_rows)} rows)", "Columns: " + " | ".join(vrti_columns), ""]
        for row in vrti_rows[:100]:
            compact = " | ".join(f"{c}={_stringify_pdf(row.get(c))}" for c in vrti_columns[:8])
            lines.extend(_wrap_line(compact))
        lines += [""]
    if summary_block:
        lines += ["SUMMARY"]
        lines.extend(_wrap_line(summary_block.get("final_summary_text", "")))
        for k, v in summary_block.get("stats", {}).items():
            lines.append(f"  {k}: {v}")

    path.write_bytes(_build_simple_pdf(lines))
    return path


def _build_simple_pdf(lines: list[str]) -> bytes:
    page_height = 792
    start_x, start_y, line_step, bottom_margin = 48, 760, 13, 48
    lpp = max(1, (start_y - bottom_margin) // line_step)
    pages = [lines[i:i+lpp] for i in range(0, len(lines), lpp)] or [["No content"]]

    objects: dict[int, bytes] = {}
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"

    next_obj = 4
    page_ids: list[int] = []
    for page_lines in pages:
        pid, cid = next_obj, next_obj + 1; next_obj += 2; page_ids.append(pid)
        sl = [b"BT", b"/F1 10 Tf", f"{start_x} {start_y} Td".encode("latin-1")]
        first = True
        for raw in page_lines:
            safe = _escape_pdf_text(raw)
            sl.append((f"({safe}) Tj" if first else f"0 {-line_step} Td ({safe}) Tj").encode("latin-1"))
            first = False
        sl.append(b"ET")
        sb = b"\n".join(sl) + b"\n"
        objects[cid] = (f"<< /Length {len(sb)} >>\n".encode("latin-1") + b"stream\n" + sb + b"endstream")
        objects[pid] = (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 {page_height}] "
                        f"/Resources << /Font << /F1 3 0 R >> >> /Contents {cid} 0 R >>").encode("latin-1")

    kids = " ".join(f"{p} 0 R" for p in page_ids)
    objects[2] = f"<< /Type /Pages /Count {len(page_ids)} /Kids [{kids}] >>".encode("latin-1")
    total = max(objects.keys())
    chunks: list[bytes] = [b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"]
    offsets = [0]; cursor = len(chunks[0])
    for n in range(1, total + 1):
        b = f"{n} 0 obj\n".encode("latin-1") + objects[n] + b"\nendobj\n"
        offsets.append(cursor); chunks.append(b); cursor += len(b)
    xref_start = cursor
    xref = [f"xref\n0 {total+1}\n".encode("latin-1"), b"0000000000 65535 f \n"]
    for off in offsets[1:]:
        xref.append(f"{off:010d} 00000 n \n".encode("latin-1"))
    trailer = (f"trailer\n<< /Size {total+1} /Root 1 0 R >>\nstartxref\n{xref_start}\n%%EOF\n").encode("latin-1")
    return b"".join(chunks + xref + [trailer])


def _escape_pdf_text(text: str) -> str:
    raw = (text or "").replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return raw.encode("latin-1", "replace").decode("latin-1")[:240]


def _wrap_line(text: str, width: int = 120) -> list[str]:
    if len(text) <= width:
        return [text]
    words = text.split()
    if not words:
        return [text[:width]]
    out: list[str] = []; cur = words[0]
    for w in words[1:]:
        if len(cur) + 1 + len(w) <= width:
            cur += " " + w
        else:
            out.append(cur); cur = w
    out.append(cur)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _distance_km_sql(lat1, lon1, lat2, lon2):
    try:
        if None in (lat1, lon1, lat2, lon2):
            return None
        lat1, lon1, lat2, lon2 = float(lat1), float(lon1), float(lat2), float(lon2)
    except (TypeError, ValueError):
        return None
    r = 6371.0; dlat = math.radians(lat2-lat1); dlon = math.radians(lon2-lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlon/2)**2
    return r * 2.0 * math.atan2(math.sqrt(a), math.sqrt(1-a))


def suggest_townlands(query: str, limit: int = 8) -> list[dict[str, Any]]:
    """Public helper used by the Ask API for townland autocomplete."""
    return _suggest_townland_matches(query, limit=limit, min_score=0.55)


def _resolve_townland_hint(question: str, hint: str | None) -> str | None:
    return _resolve_townland_context(question, hint).get("name_norm")


def _resolve_townland_context(question: str, hint: str | None) -> dict[str, Any]:
    raw_hint = (hint or "").strip()
    if raw_hint:
        exact = _find_exact_townland(raw_hint)
        if exact:
            exact_suggestions, exact_warning = _suggest_archive_backed_alternatives(raw_hint, exact)
            return _townland_resolution_payload(
                exact,
                source="hint",
                raw_text=raw_hint,
                match_type="exact",
                confidence=1.0,
                suggestions=exact_suggestions,
                warning=exact_warning,
            )
        suggestions = _suggest_townland_matches(raw_hint, limit=5, min_score=0.58)
        if suggestions and suggestions[0]["score"] >= 0.82:
            match = suggestions[0]
            return _townland_resolution_payload(
                match,
                source="hint",
                raw_text=raw_hint,
                match_type="fuzzy",
                confidence=match["score"],
                suggestions=suggestions,
                warning=(
                    f"Townland '{raw_hint}' was not an exact match. "
                    f"Using {match['name']}. Did you mean {match['name']}?"
                ),
            )
        return {
            "matched": False,
            "source": "hint",
            "raw_text": raw_hint,
            "name": None,
            "name_norm": None,
            "match_type": "none",
            "confidence": 0.0,
            "suggestions": suggestions,
            "warning": (
                f"Townland '{raw_hint}' was not found. "
                f"Did you mean {', '.join(s['name'] for s in suggestions[:3])}?"
                if suggestions else f"Townland '{raw_hint}' was not found in the local townland list."
            ),
        }

    exact_in_question = _find_exact_townland_in_question(question)
    if exact_in_question:
        exact_suggestions, exact_warning = _suggest_archive_backed_alternatives(
            exact_in_question["name"], exact_in_question
        )
        return _townland_resolution_payload(
            exact_in_question,
            source="question",
            raw_text=exact_in_question["name"],
            match_type="contained",
            confidence=1.0,
            suggestions=exact_suggestions,
            warning=exact_warning,
        )

    best: dict[str, Any] | None = None
    best_raw = ""
    for candidate in _townland_query_candidates(question):
        suggestions = _suggest_townland_matches(candidate, limit=5, min_score=0.66)
        if suggestions and (best is None or suggestions[0]["score"] > best["score"]):
            best = {**suggestions[0], "suggestions": suggestions}
            best_raw = candidate

    if best and best["score"] >= 0.86:
        return _townland_resolution_payload(
            best,
            source="question",
            raw_text=best_raw,
            match_type="fuzzy",
            confidence=best["score"],
            suggestions=best.get("suggestions") or [best],
            warning=(
                f"I could not find an exact townland named '{best_raw}'. "
                f"Using {best['name']}. Did you mean {best['name']}?"
            ),
        )
    if best and _question_seems_townland_scoped(question):
        suggestions = best.get("suggestions") or [best]
        return {
            "matched": False,
            "source": "question",
            "raw_text": best_raw,
            "name": None,
            "name_norm": None,
            "match_type": "none",
            "confidence": 0.0,
            "suggestions": suggestions,
            "warning": (
                f"I could not find an exact townland named '{best_raw}'. "
                f"Did you mean {', '.join(s['name'] for s in suggestions[:3])}?"
            ),
        }
    if re.search(r"\b(this townland|this place)\b", question or "", flags=re.IGNORECASE):
        return {
            "matched": False,
            "source": "question",
            "raw_text": "this townland",
            "name": None,
            "name_norm": None,
            "match_type": "none",
            "confidence": 0.0,
            "suggestions": [],
            "warning": (
                "No map townland is selected for 'this townland', so I answered at estate scope. "
                "Type a townland name in the hint box for a townland-specific answer."
            ),
        }
    return {
        "matched": False,
        "source": "none",
        "raw_text": None,
        "name": None,
        "name_norm": None,
        "match_type": "none",
        "confidence": 0.0,
        "suggestions": [],
    }


def _townland_resolution_payload(
    match: dict[str, Any],
    source: str,
    raw_text: str,
    match_type: str,
    confidence: float,
    suggestions: list[dict[str, Any]] | None = None,
    warning: str | None = None,
) -> dict[str, Any]:
    payload = {
        "matched": True,
        "source": source,
        "raw_text": raw_text,
        "name": match.get("name"),
        "name_norm": _norm_townland(match.get("name")),
        "civil_parish": match.get("civil_parish"),
        "barony": match.get("barony"),
        "county": match.get("county"),
        "centroid_lat": match.get("centroid_lat"),
        "centroid_lon": match.get("centroid_lon"),
        "match_type": match_type,
        "confidence": round(float(confidence), 3),
        "suggestions": suggestions or [],
    }
    if warning:
        payload["warning"] = warning

    # Phase 1 — enrich with sql_id + kg_uri from shared entity resolver.
    # This ensures every downstream lane (SQL compiler, SPARQL engine,
    # fusion reconciler) references the same resolved entity.
    try:
        from backend.services.entity_resolver import resolve_entity as _re
        er = _re(match.get("name") or "", "townland")
        payload["sql_id"] = er.sql_id
        payload["kg_uri"] = er.kg_uri or match.get("kg_uri")
        payload["entity_resolution"] = {
            "sql_id": er.sql_id,
            "kg_uri": er.kg_uri,
            "confidence": er.confidence,
            "match_type": er.match_type,
        }
    except Exception as _er_exc:
        log.debug("ask_service.entity_resolver_skipped error=%s", _er_exc)
        payload["sql_id"] = None
        payload["kg_uri"] = None

    return payload


def _townland_catalog() -> list[dict[str, Any]]:
    with _townland_catalog_lock:
        cached = _TOWNLAND_CATALOG_CACHE.get("items") or []
        if cached:
            return list(cached)

    conn = get_db_conn()
    try:
        try:
            rows = conn.execute(
                """
                SELECT
                  townland.name, townland.civil_parish, townland.barony, townland.county,
                  townland.centroid_lat, townland.centroid_lon,
                  COUNT(DISTINCT unified_record.record_id) AS local_record_count
                FROM townland
                LEFT JOIN unified_record
                  ON unified_record.townland_norm=UPPER(townland.name)
                WHERE townland.name IS NOT NULL AND TRIM(townland.name) <> ''
                GROUP BY
                  townland.name, townland.civil_parish, townland.barony, townland.county,
                  townland.centroid_lat, townland.centroid_lon
                ORDER BY townland.name
                """
            ).fetchall()
        except Exception:
            rows = conn.execute(
                """
                SELECT name, civil_parish, barony, county, centroid_lat, centroid_lon, 0 AS local_record_count
                FROM townland
                WHERE name IS NOT NULL AND TRIM(name) <> ''
                ORDER BY name
                """
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            name = row["name"]
            if not _is_likely_townland_candidate(name):
                continue
            item = dict(row)
            item["name_norm"] = _norm_townland(name)
            item["word_key"] = _place_word_key(name)
            item["compact_key"] = _place_compact_key(name)
            items.append(item)
        with _townland_catalog_lock:
            _TOWNLAND_CATALOG_CACHE["items"] = items
            _TOWNLAND_CATALOG_CACHE["loaded_at"] = time.time()
        return list(items)
    finally:
        conn.close()


def _find_exact_townland(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    value_norm = _norm_townland(value)
    value_word_key = _place_word_key(value)
    value_compact_key = _place_compact_key(value)
    for item in _townland_catalog():
        if (
            item.get("name_norm") == value_norm
            or item.get("word_key") == value_word_key
            or item.get("compact_key") == value_compact_key
        ):
            return item
    return None


def _suggest_archive_backed_alternatives(
    raw_text: str,
    exact_match: dict[str, Any],
) -> tuple[list[dict[str, Any]], str | None]:
    if (exact_match.get("local_record_count") or 0) > 0:
        return [], None
    suggestions = [
        s for s in _suggest_townland_matches(raw_text, limit=6, min_score=0.72)
        if s.get("name") != exact_match.get("name") and (s.get("local_record_count") or 0) > 0
    ][:5]
    if not suggestions:
        return [], None
    top = suggestions[0]
    return suggestions, (
        f"{exact_match.get('name')} is in the townland reference list, but this archive has no "
        f"local people records attached to it. Did you mean {top.get('name')}?"
    )


def _find_exact_townland_in_question(question: str) -> dict[str, Any] | None:
    q_words = f" {_place_word_key(question).lower()} "
    for item in sorted(_townland_catalog(), key=lambda x: len(x.get("word_key") or ""), reverse=True):
        key = (item.get("word_key") or "").lower()
        if len(key) < 3:
            continue
        if f" {key} " in q_words:
            return item
    return None


def _suggest_townland_matches(query: str | None, limit: int = 5, min_score: float = 0.6) -> list[dict[str, Any]]:
    raw = (query or "").strip()
    compact = _place_compact_key(raw)
    word_key = _place_word_key(raw)
    if len(compact) < 3 or word_key.lower() in _TOWNLAND_STOPWORDS:
        return []

    scored: list[dict[str, Any]] = []
    for item in _townland_catalog():
        item_compact = item.get("compact_key") or ""
        item_words = item.get("word_key") or ""
        if not item_compact:
            continue
        score = difflib.SequenceMatcher(None, compact, item_compact).ratio()
        score = max(score, difflib.SequenceMatcher(None, word_key, item_words).ratio())
        if compact == item_compact:
            score = 1.0
        elif compact in item_compact and len(compact) >= 4:
            score = max(score, 0.9 - min(0.2, (len(item_compact) - len(compact)) / 40))
        elif item_compact in compact and len(item_compact) >= 4:
            score = max(score, 0.86 - min(0.2, (len(compact) - len(item_compact)) / 40))
        elif compact[:4] == item_compact[:4]:
            score = max(score, 0.7)

        if len(compact) < 5 and score < 0.86:
            continue
        if score >= min_score:
            scored.append({
                "name": item.get("name"),
                "name_norm": item.get("name_norm"),
                "civil_parish": item.get("civil_parish"),
                "barony": item.get("barony"),
                "county": item.get("county"),
                "centroid_lat": item.get("centroid_lat"),
                "centroid_lon": item.get("centroid_lon"),
                "local_record_count": item.get("local_record_count") or 0,
                "score": round(float(score), 3),
                "_rank_score": round(
                    float(score)
                    + (0.06 if (item.get("local_record_count") or 0) > 0 else 0.0)
                    + (0.02 if str(item.get("county") or "").lower() == "wicklow" else 0.0),
                    3,
                ),
            })

    scored.sort(key=lambda x: (-x.get("_rank_score", x["score"]), -x["score"], len(x.get("name") or ""), x.get("name") or ""))
    for row in scored:
        row.pop("_rank_score", None)
    return scored[:limit]


def _townland_query_candidates(question: str) -> list[str]:
    text = question or ""
    candidates: list[str] = []
    patterns = [
        r"\b(?:from|in|near|around|within\s+\d{1,3}\s*km\s+of|within|for|at)\s+([A-Za-z][A-Za-z' -]{2,50})",
        r"\btownland\s+(?:called|named)?\s*([A-Za-z][A-Za-z' -]{2,50})",
        r"\b([A-Za-z][A-Za-z' -]{2,50})\s+townland\b",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            candidate = _trim_townland_candidate(match.group(1))
            if candidate:
                candidates.append(candidate)

    tokens = re.findall(r"[A-Za-z][A-Za-z'-]{1,}", text)
    indexed = [
        token for token in tokens
        if token.lower() not in _TOWNLAND_STOPWORDS and not re.fullmatch(r"\d+", token)
    ]
    for size in range(min(4, len(indexed)), 0, -1):
        for i in range(0, len(indexed) - size + 1):
            candidate = " ".join(indexed[i:i + size])
            if candidate and candidate.lower() not in _TOWNLAND_STOPWORDS:
                candidates.append(candidate)

    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = _place_compact_key(candidate)
        if len(key) < 3 or key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped[:80]


def _trim_townland_candidate(value: str) -> str:
    candidate = re.split(
        r"\b(?:and|with|where|who|what|which|how|when|why|year|in\s+18\d{2}|from\s+18\d{2})\b",
        value,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    candidate = re.sub(r"\b\d{1,4}\b", " ", candidate)
    words = [
        word for word in re.findall(r"[A-Za-z][A-Za-z'-]{1,}", candidate)
        if word.lower() not in _TOWNLAND_STOPWORDS
    ]
    return " ".join(words[:4]).strip()


def _question_seems_townland_scoped(question: str) -> bool:
    q = (question or "").lower()
    return any(token in q for token in ["townland", "from ", "near ", "around ", "within ", " in "])


def _is_likely_townland_candidate(value: str | None) -> bool:
    if not value:
        return False
    return value.strip().upper() not in {
        "TOTAL","COUNT","PEOPLE","PERSON","PERSONS","EMIGRATED",
        "EMIGRATION","THIS","AROUND","PARISH","PARISHES",
    }


def _norm_townland(value: str | None) -> str | None:
    return " ".join(value.strip().upper().split()) if value else None


def _place_word_key(value: str | None) -> str:
    return " ".join(re.findall(r"[A-Za-z0-9]+", (value or "").upper()))


def _place_compact_key(value: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def _sql_escape(value: str) -> str:
    return value.replace("'", "''")


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() == "nan":
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() == "nan":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return None if not s or s.lower() == "nan" else s


def _to_bool_int(value: Any) -> int:
    return 1 if str(value or "").strip().lower() in {"1", "true", "yes", "y", "t"} else 0


def _sum_defined_ints(*values: Any) -> int:
    total = 0
    found = False
    for value in values:
        parsed = _to_int(value)
        if parsed is None:
            continue
        total += parsed
        found = True
    return total if found else 0


def _best_holding_acres(row: dict[str, Any]) -> float | None:
    for key in ("acres_irish", "acres_english", "acres", "acres_2"):
        value = _to_float(row.get(key))
        if value is not None:
            return value
    return None


def _derived_children_count(row: dict[str, Any]) -> int | None:
    values = [_to_int(row.get("sons")), _to_int(row.get("daughters"))]
    if all(value is None for value in values):
        return None
    return sum(value or 0 for value in values)


def _derived_family_size_estimate(row: dict[str, Any]) -> int | None:
    parts = [
        1,
        1 if _to_int(row.get("age_wife_widow_of_head_of_household")) is not None else 0,
        _to_int(row.get("sons")) or 0,
        _to_int(row.get("daughters")) or 0,
        _to_int(row.get("servants_male")) or 0,
        _to_int(row.get("servants_female")) or 0,
        _to_int(row.get("other_males_in_household")) or 0,
        _to_int(row.get("other_famales_in_household")) or 0,
    ]
    has_detail = any(part > 0 for part in parts[1:])
    return sum(parts) if has_detail else None


def _derived_is_widow(row: dict[str, Any]) -> int:
    text_parts = [
        _clean_text(row.get("forename")),
        _clean_text(row.get("role")),
        _clean_text(row.get("comments")),
    ]
    haystack = " ".join(part for part in text_parts if part).lower()
    return 1 if " widow" in f" {haystack}" else 0


def _derived_is_canada_destination(arrival: str | None) -> int:
    if not arrival:
        return 0
    place = arrival.lower()
    canada_tokens = [
        "canada", "quebec", "st andrews", "st. andrews", "st andrew",
        "grosse isle", "new brunswick", "nova scotia", "ontario", "montreal", "toronto",
    ]
    return 1 if any(token in place for token in canada_tokens) else 0


def _heritage_townland_norm(value: str | None) -> str | None:
    raw = _clean_text(value)
    if not raw:
        return None
    cleaned = re.sub(r"\s*\([^)]*\)\s*", " ", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return _norm_townland(cleaned)


def _is_ring_fort_class(value: str | None) -> bool:
    text = (value or "").strip().lower()
    return any(token in text for token in ["ringfort", "ring-ditch", "ringwork", "ring-barrow", "hillfort"])


def _stringify_pdf(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=True)
    return str(value)
