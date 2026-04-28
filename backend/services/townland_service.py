"""
coolattin/services/townland_service.py

Townland service.

Responsibilities:
  - Townland name normalisation (canonical form)
  - Alias map resolution (spelling variants → canonical)
  - Reconciliation of townland metadata with the townlands.ie reference
  - DB-first retrieval for the townland list
  - Centroid computation from GeoJSON

Data is populated by running:
    python -m coolattin.jobs.full_ingest

This module owns normalisation logic.  No other module should
normalise townland names independently.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from pathlib import Path
from typing import Optional

from backend.models.census_models import Townland

log = logging.getLogger(__name__)

# Alias map: { variant_normalised_key: canonical_normalised_key }
# Loaded once from seed file; populated by jobs/townlands_ingest.py
_ALIAS_MAP: dict[str, str] = {}
_ALIAS_MAP_LOADED = False


# ------------------------------------------------------------------ #
# Name normalisation                                                   #
# ------------------------------------------------------------------ #

def normalize_townland_name(name: str) -> str:
    """
    Convert a raw townland name into its canonical form.

    Normalisation pipeline:
      1. Strip leading/trailing whitespace
      2. Collapse internal whitespace
      3. Remove brackets and parenthetical content (e.g. "Upper", qualifiers)
         — only if the full name without brackets still has content
      4. Strip common prefixes like "Townland of"
      5. Fold to UPPER CASE
      6. Remove punctuation except hyphens and apostrophes

    This function is the single authority for townland normalisation.
    Call it on any name before storing or comparing townland values.
    """
    if not name or not isinstance(name, str):
        return ""

    s = name.strip()
    s = " ".join(s.split())                      # collapse whitespace

    # Remove "Townland of X" prefix
    s = re.sub(r"^[Tt]ownland\s+of\s+", "", s)

    # Strip outer brackets that qualify the name — keep base name
    # e.g. "Ballinacor (North)" → "Ballinacor North"
    s = re.sub(r"[()]", "", s)
    s = " ".join(s.split())                      # re-collapse after bracket removal

    # Remove stray punctuation (keep hyphen and apostrophe as they're part of names)
    s = re.sub(r"[^\w\s\-']", "", s)

    s = s.strip().upper()

    # Replace unicode fancy quotes with standard apostrophe
    s = s.replace("\u2019", "'").replace("\u2018", "'")

    return s


def resolve_alias(name: str) -> str:
    """
    Resolve a normalised name through the alias map.
    Returns the canonical name if an alias exists, else the input unchanged.
    """
    _ensure_alias_map_loaded()
    return _ALIAS_MAP.get(name, name)


def canonical_name(raw: str) -> str:
    """Full pipeline: normalise then resolve alias."""
    return resolve_alias(normalize_townland_name(raw))


# ------------------------------------------------------------------ #
# DB-first / KG-second townland retrieval                             #
# ------------------------------------------------------------------ #

def get_wicklow_townlands() -> dict:
    """
    Return all townlands from the local DB.

    The DB is populated by running:
        python -m coolattin.jobs.full_ingest

    Flow:
      1. Check local DB
      2. If records exist, serve from DB
      3. If DB empty, log a clear warning and return empty (do not silently seed)
    """
    from backend.repositories import townland_repository, refresh_state_repository
    from config import ActiveConfig
    from datetime import datetime, timezone

    now_iso = datetime.now(timezone.utc).isoformat() + "Z"
    db_count = townland_repository.count()

    if db_count > 0:
        state = refresh_state_repository.get(
            "full_ingest", stale_after_days=ActiveConfig.TOWNLAND_STALE_AFTER_DAYS
        )
        cache_status = "hit"
        if state and state.is_stale:
            cache_status = "stale_refresh"
            log.info("townland_service.townlands_stale — serving DB, refresh recommended")
        townlands = [t.to_dict() for t in townland_repository.find_all()]
        return {
            "data": townlands,
            "meta": {
                "source": "database",
                "cache_status": cache_status,
                "generated_at": now_iso,
                "record_count": len(townlands),
            },
        }

    log.warning(
        "townland_service.db_empty — no townlands in database. "
        "Run: python -m coolattin.jobs.full_ingest"
    )
    return {
        "data": [],
        "meta": {
            "source": "database",
            "cache_status": "miss",
            "generated_at": now_iso,
            "record_count": 0,
            "hint": "Run python -m coolattin.jobs.full_ingest to populate the database.",
        },
    }


# ------------------------------------------------------------------ #
# Centroid computation                                                 #
# ------------------------------------------------------------------ #

def build_centroids_from_geojson(geojson_path: Path) -> dict[str, tuple[float, float]]:
    """
    Compute lat/lon centroids for all townlands in a GeoJSON file.
    Returns dict: normalised_name → (lat, lon)
    """
    if not geojson_path.exists():
        log.warning("townland_service.geojson_missing | path=%s", geojson_path)
        return {}

    geo = json.loads(geojson_path.read_text(encoding="utf-8"))

    def centroid(coords: list) -> tuple[float, float]:
        ring = coords[0]
        xs = [pt[0] for pt in ring]
        ys = [pt[1] for pt in ring]
        return (sum(ys) / len(ys), sum(xs) / len(xs))

    out: dict[str, tuple[float, float]] = {}
    for feat in geo.get("features", []):
        props = feat.get("properties", {}) or {}
        name = str(props.get("TL_ENGLISH", "")).strip()
        geom = feat.get("geometry", {}) or {}
        gtype = geom.get("type")
        if not name:
            continue
        try:
            if gtype == "Polygon":
                out[name] = centroid(geom.get("coordinates", []))
            elif gtype == "MultiPolygon":
                mp = geom.get("coordinates", [])
                if mp and mp[0]:
                    out[name] = centroid(mp[0])
        except (IndexError, ZeroDivisionError):
            pass

    log.info("townland_service.centroids_built | count=%d", len(out))
    return out


# ------------------------------------------------------------------ #
# Reconciliation helpers                                               #
# ------------------------------------------------------------------ #

def reconcile_with_reference(townlands: list[Townland]) -> list[Townland]:
    """
    Enrich a list of Townland objects with barony/parish context from the
    townlands.ie reference file.

    For each townland:
      1. Normalise name
      2. Look up in reference index
      3. If found, fill in barony, civil_parish, electoral_division
      4. If not found, flag for review (logged at DEBUG)
    """
    from backend.integrations.townlands_reference import (
        load_wicklow_reference,
        build_name_index,
    )

    refs = load_wicklow_reference()
    if not refs:
        log.warning("townland_service.reconcile — reference empty, skipping enrichment")
        return townlands

    index = build_name_index(refs)
    gaps = []

    for t in townlands:
        key = normalize_townland_name(t.name)
        ref = index.get(key)
        if ref:
            t.barony = t.barony or ref.barony
            t.civil_parish = t.civil_parish or ref.civil_parish
            t.electoral_division = t.electoral_division or ref.electoral_division
            if not t.name_gaelic and ref.gaelic_name:
                t.name_gaelic = ref.gaelic_name
        else:
            gaps.append(t.name)
            log.debug("townland_service.reconcile_gap | name=%s", t.name)

    if gaps:
        log.info(
            "townland_service.reconcile | enriched=%d gaps=%d",
            len(townlands) - len(gaps),
            len(gaps),
        )
        _write_reconciliation_gaps(gaps)

    return townlands


# ------------------------------------------------------------------ #
# Internal helpers                                                     #
# ------------------------------------------------------------------ #

def _ensure_alias_map_loaded() -> None:
    global _ALIAS_MAP, _ALIAS_MAP_LOADED
    if _ALIAS_MAP_LOADED:
        return
    from config import ActiveConfig
    alias_path = ActiveConfig.DATA_SEED_DIR / "townland_aliases.json"
    if alias_path.exists():
        try:
            raw = json.loads(alias_path.read_text(encoding="utf-8"))
            _ALIAS_MAP = {
                normalize_townland_name(k): normalize_townland_name(v)
                for k, v in raw.items()
            }
            log.info("townland_service.alias_map_loaded | entries=%d", len(_ALIAS_MAP))
        except Exception as exc:
            log.warning("townland_service.alias_map_load_failed | error=%s", exc)
    _ALIAS_MAP_LOADED = True


def _write_reconciliation_gaps(gaps: list[str]) -> None:
    """Write unmatched townland names to the source snapshots dir for manual review."""
    from config import ActiveConfig
    path = ActiveConfig.DATA_SNAPSHOT_DIR / "reconciliation_gaps.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("townland_name\n")
        for g in gaps:
            f.write(f"{g}\n")
    log.info("townland_service.gaps_written | path=%s count=%d", path, len(gaps))
