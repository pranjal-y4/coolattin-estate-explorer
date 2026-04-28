"""
backend/services/ask_service.py

Natural-language Q&A over local SQLite using LLM-generated SQL.

Design
------
- 100+ pre-built SQL templates matched by keyword scoring — instant results, no LLM needed.
- The configured LLM provider rewrites the verified data answer for readability.
- Parallel LLM calls (SQLite SQL + VRTI PostgreSQL) via ThreadPoolExecutor.
- VRTI parish data cached in-process (TTL 1 h).
- Parallel VRTI townland detail lookups.
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

from config import ActiveConfig
from extensions import get_db_conn

log = logging.getLogger(__name__)


def _load_local_env_files() -> None:
    """
    Load local key=value env files without adding another dependency.
    Existing process env wins over file values.
    """
    root = Path(__file__).resolve().parents[2]
    for env_path in (root / ".env.local", root / ".env"):
        if not env_path.exists():
            continue
        try:
            for raw_line in env_path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
        except OSError as exc:
            log.warning("ask_service.env_load_failed path=%s error=%s", env_path, exc)


_load_local_env_files()

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

# ── Townland catalog cache ───────────────────────────────────────────────────
_townland_catalog_lock: threading.Lock = threading.Lock()
_TOWNLAND_CATALOG_CACHE: dict[str, Any] = {
    "loaded_at": 0.0,
    "items": [],
}

_TOWNLAND_STOPWORDS = {
    "a", "about", "an", "and", "are", "around", "as", "at", "be", "been", "between",
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
  ship_name TEXT             — ship for emigration
  departure TEXT             — departure place + date
  arrival TEXT               — arrival place + date
  household_list TEXT        — household members
  has_emigration_record INT  — 1=emigrated  0=not
  has_eviction_record INT    — 1=evicted    0=not
  has_tenancy_record INT     — 1=tenant     0=not
  JOIN: UPPER(townland.name) = unified_record.townland_norm

Table: townland  — 152 Coolattin estate townlands
  id INTEGER
  name TEXT              — canonical name e.g. 'Ballinacor'
  name_gaelic TEXT       — Irish name
  civil_parish TEXT      — e.g. 'Knockrath', 'Moyacomb'
  barony TEXT            — e.g. 'Shillelagh'
  county TEXT            — e.g. 'Wicklow'
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
  count INTEGER          — eviction count (some older prompts may call this eviction_count)

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
     "category": "geography", "description": "Townlands within a given parish",
     "required_keywords": ["townland", "parish"],
     "optional_keywords": ["in", "within", "list", "which", "all"],
     "sql_template": "SELECT name, barony, county FROM townland WHERE civil_parish IS NOT NULL ORDER BY civil_parish, name LIMIT 100"},

    {"id": "townlands_by_county",
     "category": "geography", "description": "Townlands grouped by county",
     "required_keywords": ["townland", "county"],
     "optional_keywords": ["by county", "each county", "which county", "list"],
     "sql_template": "SELECT county, COUNT(*) AS townland_count FROM townland WHERE county IS NOT NULL GROUP BY county ORDER BY townland_count DESC"},

    {"id": "townland_details",
     "category": "geography", "description": "Full details of a specific townland",
     "required_keywords": [],
     "optional_keywords": ["details", "about", "information", "info", "townland"],
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
    patterns = [
        r"\bsurname[s]?\s+(?:of\s+|is\s+)?['\"]?(\w+)['\"]?",
        r"\bfamily\s+(?:name\s+)?['\"]?(\w+)['\"]?",
        r"\b([A-Za-z][A-Za-z'-]{2,})\s+family\b",
        r"\b(?:about|for|on)\s+([A-Za-z][A-Za-z'-]{2,})\s+(?:family|surname|people|records)\b",
        r"\bnamed?\s+['\"]?(\w+)['\"]?",
        r"\bby\s+the\s+name\s+(?:of\s+)?['\"]?(\w+)['\"]?",
    ]
    for p in patterns:
        m = re.search(p, question, re.I)
        if m:
            candidate = m.group(1).upper()
            # reject common words that aren't surnames
            if candidate not in {"THE", "A", "AN", "THIS", "THAT", "THEIR", "ALL", "HOW", "MANY"}:
                return candidate
    return None


def _analyse_question(question: str, townland_hint: str | None) -> dict[str, Any]:
    q = (question or "").lower()
    year = _extract_year(question)
    surname = _extract_surname(question)
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

    wants_list = any(x in q for x in ["list", "show", "who", "which people", "names", "what all"])
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
    """
    q = question.lower()
    year = _extract_year(question)
    surname = _extract_surname(question)

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

    if not best_tmpl or best_score < 1:
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


# ─────────────────────────────────────────────────────────────────────────────
# SSE streaming entry point
# ─────────────────────────────────────────────────────────────────────────────

def _sse(type_: str, **kw: Any) -> str:
    return f"data: {json.dumps({'type': type_, **kw})}\n\n"


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

    try:
        _ensure_unified_table_seeded()
    except Exception as exc:
        yield _sse("error", message=f"Database not ready: {exc}")
        return

    townland_resolution = _resolve_townland_context(clean_q, townland_hint)
    canonical_townland = townland_resolution.get("name_norm")
    warnings: list[str] = []
    if townland_resolution.get("warning"):
        warnings.append(str(townland_resolution["warning"]))

    # ── Stage 1 — Contacting LLM / Template match ─────────────────────────
    t0 = time.perf_counter()
    yield _sse("progress", stage="contacting_llm", status="started", label="Contacting LLM",
               detail="Checking pre-built templates…")

    sql: str
    llm_meta: dict[str, Any]
    vrti_postgres_sql: str
    vrti_query_meta: dict[str, Any]
    if not force_llm:
        tmpl, tmpl_sql = _match_and_build_template(clean_q, canonical_townland)
    else:
        tmpl, tmpl_sql = None, None

    if tmpl_sql:
        sql = tmpl_sql
        llm_meta = {
            "provider": "template",
            "model": "pre_built",
            "mode": "template",
            "template_id": tmpl["id"],
            "description": tmpl["description"],
        }
        vrti_postgres_sql = _fallback_vrti_postgres_sql(clean_q, canonical_townland)
        vrti_query_meta = {"provider": "template", "model": "pre_built", "mode": "template"}
        ms = int((time.perf_counter() - t0) * 1000)
        yield _sse("progress", stage="contacting_llm", status="completed", label="Contacting LLM",
                   detail=f"Template: {tmpl['description']}", duration_ms=ms)
    else:
        yield _sse("progress", stage="contacting_llm", status="started", label="Contacting LLM",
                   detail="Sending schema + question to the configured LLM...")
        try:
            # Run sequentially to avoid overloading small/free LLM providers
            # with concurrent generations on the same request.
            sql, llm_meta = _generate_sql(clean_q, _ANNOTATED_SCHEMA, canonical_townland)
            vrti_postgres_sql, vrti_query_meta = _generate_vrti_postgres_query(clean_q, canonical_townland)
            ms = int((time.perf_counter() - t0) * 1000)
            yield _sse("progress", stage="contacting_llm", status="completed", label="Contacting LLM",
                       detail=(
                           f"Local: {llm_meta.get('mode')} | "
                           f"VRTI: {vrti_query_meta.get('mode')} | "
                           f"Model: {llm_meta.get('model')}"
                       ),
                       duration_ms=ms)
        except Exception as exc:
            ms = int((time.perf_counter() - t0) * 1000)
            sql = _fallback_sql(clean_q, canonical_townland)
            llm_meta = {"provider": "local_fallback", "model": "rule_template", "mode": "fallback_rule"}
            vrti_postgres_sql = _fallback_vrti_postgres_sql(clean_q, canonical_townland)
            vrti_query_meta = {"provider": "local_fallback", "model": "rule_template", "mode": "fallback_rule"}
            yield _sse("progress", stage="contacting_llm", status="completed", label="Contacting LLM",
                       detail=f"LLM unavailable ({exc}) - fallback template used", duration_ms=ms)

    # ── Stage 2 — Framing Query ───────────────────────────────────────────
    t0 = time.perf_counter()
    yield _sse("progress", stage="framing_query", status="started", label="Framing Query",
               detail="Validating SQL for safety…")
    try:
        safe_sql = _sanitize_and_validate_sql(sql)
    except ValueError:
        safe_sql = _sanitize_and_validate_sql(_fallback_sql(clean_q, canonical_townland))
    ms = int((time.perf_counter() - t0) * 1000)
    yield _sse("progress", stage="framing_query", status="completed", label="Framing Query",
               detail="Read-only query validated", duration_ms=ms)

    # ── Stage 3 — Querying Database ───────────────────────────────────────
    t0 = time.perf_counter()
    yield _sse("progress", stage="querying_database", status="started", label="Querying Database",
               detail="Running SQL against local SQLite database…")
    safe_sql, columns, rows, query_warning = _execute_with_recovery(
        question=clean_q, townland_hint=canonical_townland, sql=safe_sql,
    )
    if query_warning:
        warnings.append(query_warning)
    ms = int((time.perf_counter() - t0) * 1000)
    yield _sse("progress", stage="querying_database", status="completed", label="Querying Database",
               detail=f"{len(rows)} row{'s' if len(rows)!=1 else ''} returned", duration_ms=ms)

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

    # ── Stage 5 — Preparing Output ────────────────────────────────────────
    t0 = time.perf_counter()
    yield _sse("progress", stage="preparing_output", status="started", label="Preparing Output",
               detail="Building data tables, LLM rewrite, and PDF report...")

    actual_answer = _build_answer_text(clean_q, columns, rows, canonical_townland, kg_context)
    summary_block = _build_structured_summary(
        question=clean_q, local_columns=columns, local_rows=rows,
        vrti_columns=vrti_columns, vrti_rows=vrti_rows, kg_context=kg_context,
    )
    supporting_context = _build_supporting_context(
        question=clean_q,
        townland_norm=canonical_townland,
        townland_resolution=townland_resolution,
        primary_columns=columns,
        primary_rows=rows,
        kg_context=kg_context,
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
        "kg_context": kg_context,
        "structured_output": structured_output,
        "pdf_url": f"/api/ask/pdf/{pdf_path.name}",
        "warnings": warnings,
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
        return cache_status({
            **base_status,
            "available": False,
            "connection_state": "unreachable",
            "hint": f"OpenRouter key is configured, but the live connection check failed: {exc}",
        })


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

    fingerprint = f"{UNIFIED_CSV_PATH.stat().st_mtime_ns}:{UNIFIED_CSV_PATH.stat().st_size}"
    from backend.repositories import refresh_state_repository
    state = refresh_state_repository.get(UNIFIED_SEED_KEY, stale_after_days=36500)

    conn = get_db_conn()
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS unified_record (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                record_id TEXT, unique_id_no TEXT, year INTEGER, month TEXT,
                surname TEXT, forename TEXT, canonical_name TEXT,
                townland TEXT, townland_norm TEXT, parish TEXT, estate TEXT,
                role TEXT, legal_action TEXT, ship_name TEXT,
                departure TEXT, arrival TEXT, household_list TEXT,
                has_emigration_record INTEGER DEFAULT 0,
                has_eviction_record INTEGER DEFAULT 0,
                has_tenancy_record INTEGER DEFAULT 0)"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_unified_townland_norm ON unified_record(townland_norm)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_unified_has_emigration ON unified_record(has_emigration_record)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_unified_record_id ON unified_record(record_id)")
        conn.commit()

        existing_count = conn.execute("SELECT COUNT(*) FROM unified_record").fetchone()[0]
        if state and state.query_hash == fingerprint and existing_count > 0:
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
                    _clean_text(row.get("surname")), _clean_text(row.get("forename")),
                    _clean_text(row.get("canonical_name")),
                    townland, _norm_townland(townland),
                    _clean_text(row.get("parish")), _clean_text(row.get("estate")),
                    _clean_text(row.get("role")), _clean_text(row.get("legal_action")),
                    _clean_text(row.get("ship_name")) or _clean_text(row.get("name_of_ship")),
                    _clean_text(row.get("departure")) or _clean_text(row.get("place_and_date_of_departure")),
                    _clean_text(row.get("arrival"))   or _clean_text(row.get("place_and_date_of_arrival")),
                    _clean_text(row.get("household_list")) or _clean_text(row.get("household_list_in_emigration_records")),
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


def _bulk_insert(conn, batch: list[tuple]) -> None:
    conn.executemany(
        """INSERT INTO unified_record (
            record_id,unique_id_no,year,month,surname,forename,canonical_name,
            townland,townland_norm,parish,estate,role,legal_action,ship_name,
            departure,arrival,household_list,
            has_emigration_record,has_eviction_record,has_tenancy_record
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        batch,
    )


# ─────────────────────────────────────────────────────────────────────────────
# LLM SQL generation
# ─────────────────────────────────────────────────────────────────────────────

def _generate_sql(question: str, schema: str, townland_hint: str | None) -> tuple[str, dict]:
    analysis = _analyse_question(question, townland_hint)
    prompt = _build_sql_prompt(question, schema, analysis)
    fallback_sql = _fallback_sql(question, townland_hint)
    try:
        sql, meta, mode = _llm_generate_validated_sql(
            prompt=prompt,
            purpose="sqlite_sql",
            dialect_label="SQLite",
        )
        return sql, {**meta, "mode": mode}
    except Exception as exc:
        log.warning("ask_service.llm_sql_unavailable error=%s", exc)
        return fallback_sql, {
            "provider": "local_fallback", "model": "rule_template", "mode": "fallback_rule"
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


def _build_sql_prompt(question: str, schema: str, analysis: dict[str, Any]) -> str:
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

Mandatory rules:
- Count people with COUNT(DISTINCT record_id).
- Population uses census_record joined to townland.
- Eviction totals use clearances_record.count.
- Emigration rows use has_emigration_record=1.
- Eviction people rows use has_eviction_record=1.
- Tenancy rows use has_tenancy_record=1.
- Townland filtering uses townland_norm='NAME' or UPPER(t.name)='NAME'.
- Radius queries use distance_km() with a base townland CTE.
- Person lists should include person_name and LIMIT 200.
- If the question mixes people with geography, focus this SQLite query on the local records part.

Schema:
{schema}

Question:
{question}

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


def _execute_with_recovery(
    question: str, townland_hint: str | None, sql: str,
) -> tuple[str, list[str], list[dict], str | None]:
    try:
        if _requires_verified_fallback(question, sql):
            raise ValueError("constraint_mismatch")
        cols, rows = _run_read_only_query(sql)
        if not rows:
            fb = _sanitize_and_validate_sql(_fallback_sql(question, townland_hint))
            fb_cols, fb_rows = _run_read_only_query(fb)
            if fb_rows:
                return fb, fb_cols, fb_rows, "Fallback template used (LLM query returned no rows)."
        if _should_crosscheck(question):
            fb = _sanitize_and_validate_sql(_fallback_sql(question, townland_hint))
            fb_cols, fb_rows = _run_read_only_query(fb)
            lv = _single_scalar(cols, rows)
            fv = _single_scalar(fb_cols, fb_rows)
            if lv is not None and fv is not None and lv != fv:
                return fb, fb_cols, fb_rows, "Verified template used (value mismatch with LLM result)."
        return sql, cols, rows, None
    except Exception as exc:
        fb = _sanitize_and_validate_sql(_fallback_sql(question, townland_hint))
        cols, rows = _run_read_only_query(fb)
        return fb, cols, rows, f"Fallback template used ({type(exc).__name__})."


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
    shown = [f"{c.replace('_', ' ')}={row.get(c)}" for c in columns[:5]]
    context = f" for {display_townland}" if display_townland else ""
    return f"I found one matching row{context}: " + ", ".join(shown) + "."


def _build_answer_text(question: str, columns: list[str], rows: list[dict],
                       townland_hint: str | None, kg_context: dict | None) -> str:
    analysis = _analyse_question(question, townland_hint)
    display_townland = _display_townland_name(townland_hint)
    if not rows:
        context = f" for {display_townland}" if display_townland else ""
        return f"I could not find matching records{context}."

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


def _build_structured_summary(question: str, local_columns: list[str], local_rows: list[dict],
                               vrti_columns: list[str], vrti_rows: list[dict],
                               kg_context: dict | None) -> dict[str, Any]:
    primary: str | None = None
    grouped = _grouped_answer(local_columns, local_rows)

    if len(local_rows) == 1 and len(local_columns) == 1:
        key = local_columns[0]
        primary = f"{_friendly_metric_name(key).title()}: {local_rows[0].get(key)}"
    elif len(local_rows) == 1 and local_columns:
        primary = _detail_answer(local_columns, local_rows[0], None)
    elif grouped:
        primary = grouped

    stats: dict[str, Any] = {"local_records_returned": len(local_rows), "vrti_townlands_enriched": len(vrti_rows)}
    if primary:
        stats["primary_answer"] = primary

    lines = [f"Query: {question}"]
    if primary:
        lines.append(f"Answer: {primary}")
    else:
        lines.append(f"Local database returned {len(local_rows)} row{'s' if len(local_rows)!=1 else ''}.")
    if vrti_rows:
        t_names = ", ".join(r.get("name","") for r in vrti_rows[:3] if r.get("name"))
        lines.append(f"VRTI enriched {len(vrti_rows)} townland(s){': ' + t_names if t_names else ''}.")
    lines.append("Sources: Coolattin estate records (SQLite) + VRTI Knowledge Graph (SPARQL).")

    return {"stats": stats, "final_summary_text": "  ".join(lines), "parish_sample": []}


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
    return {
        "local_database": {
            "columns": local_columns,
            "row_count": len(local_rows),
            "sample_limit": sample_limit,
            "sample_rows": local_rows[:sample_limit],
            "truncated": len(local_rows) > sample_limit,
        },
        "vrti_graph": {
            "columns": vrti_columns,
            "row_count": len(vrti_rows),
            "sample_limit": sample_limit,
            "sample_rows": vrti_rows[:sample_limit],
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
        max_tokens=420,
        temperature=0.2,
    )
    cleaned = _strip_answer_formatting(text)
    if not cleaned:
        raise RuntimeError("Empty answer rewrite from LLM.")
    return cleaned, {**meta, "mode": "llm_rewrite"}


def _build_rephrase_prompt(
    question: str,
    actual_answer: str,
    summary_block: dict[str, Any],
    data_context: dict[str, Any],
    supporting_context: dict[str, Any],
    kg_context: dict | None,
) -> str:
    prompt_payload = {
        "question": question,
        "data_backed_answer": actual_answer,
        "summary_stats": summary_block.get("stats", {}),
        "supporting_context": supporting_context,
        "local_database_sample": data_context.get("local_database", {}),
        "vrti_graph_sample": data_context.get("vrti_graph", {}),
        "townland_context": (kg_context or {}).get("townlands", [])[:5],
    }
    return f"""You are rephrasing a historical archive query result for a website user.

Use ONLY the supplied data. Do not invent names, counts, dates, locations, or causes.
Keep every number exactly the same as the data-backed answer.
Use supporting_context for relevant townland metadata, database coverage, census rows, clearances, surname/ship samples, and typo/suggestion notes.
If a townland was fuzzy-matched, clearly say which townland was used and mention the suggestion.
If the data sample is truncated, say the table below contains the full returned rows.
Write a clear, concise answer in plain text. No markdown table. No SQL.

DATA:
{json.dumps(prompt_payload, ensure_ascii=False, default=str, indent=2)}

Rephrased answer:""".strip()


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


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return None if not s or s.lower() == "nan" else s


def _to_bool_int(value: Any) -> int:
    return 1 if str(value or "").strip().lower() in {"1", "true", "yes", "y", "t"} else 0


def _stringify_pdf(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=True)
    return str(value)
