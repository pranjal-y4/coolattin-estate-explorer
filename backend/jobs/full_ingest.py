"""
coolattin/jobs/full_ingest.py

Comprehensive ingest job — builds the entire local database from two authoritative sources:

  1. Estate GeoJSON  (frontend/static/data/townlands.json)
     - Townland name list (152 Coolattin estate townlands)
     - Irish/Gaelic names
     - Area measurements (square metres)
     - Estate identifiers (TD_ID, GUID)
     - Estate population surveys: 1827, 1839, 1848, 1850, 1860, 1868 (totals)
     - Clearances records: per year 1847-1856

  2. VRTI Knowledge Graph (SPARQL endpoint)
     - Boundary WKT geometry and centroid coordinates
     - Place hierarchy: civil parish, barony, county
     - External identifiers: OSM ID, OSI ID, VRTI ID
     - Images and external links (logainm, townlands.ie)
     - Standard census records: 1841, 1851, 1861, 1871, 1881, 1891
       (male, female, inhabited houses, uninhabited houses)

No CSV files are read.  Run this job once at setup and whenever the GeoJSON or KG is updated.

Usage (from project root):
  python -m backend.jobs.full_ingest
  python -m backend.jobs.full_ingest --dry-run   # log what would be written, skip DB writes

What gets stored:
  townland          — one row per townland, fully enriched
  census_record     — estate survey years (source='json') + standard years (source='kg')
  clearances_record — per-year evictions 1847-1856 (source='json')
  refresh_state     — marks the ingest as complete

After this runs, the web app serves all data from the local DB.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
log = logging.getLogger(__name__)

# Estate survey years present in the GeoJSON
ESTATE_SURVEY_YEARS = [1827, 1839, 1848, 1850, 1860, 1868]

# Estate survey year column names (the GeoJSON key is slightly inconsistent for 1839)
ESTATE_POP_COLUMNS = {
    1827: "T_POP_1827",
    1839: "T_POP_1839_",
    1848: "T_POP_1848",
    1850: "T_POP_1850",
    1860: "T_POP_1860",
    1868: "T_POP_1868",
}

# Clearances years recorded in the GeoJSON
CLEARANCE_YEARS = list(range(1847, 1857))   # 1847 … 1856


# ------------------------------------------------------------------ #
# Entry point                                                          #
# ------------------------------------------------------------------ #

def run_full_ingest(dry_run: bool = False) -> dict:
    """
    Full ingest pipeline.

    Returns a summary dict:
      {
        townlands_processed: int,
        townlands_kg_enriched: int,
        census_records_json: int,
        census_records_kg: int,
        clearances_records: int,
        kg_errors: int,
      }
    """
    from backend.integrations import vrti_sparql
    from backend.repositories import (
        townland_repository,
        census_repository,
        refresh_state_repository,
    )
    from backend.repositories import clearances_repository
    from backend.models.census_models import Townland, CensusRecord, ClearancesRecord
    from backend.services.townland_service import normalize_townland_name
    from config import ActiveConfig

    stats = {
        "townlands_processed": 0,
        "townlands_kg_enriched": 0,
        "census_records_json": 0,
        "census_records_kg": 0,
        "clearances_records": 0,
        "kg_errors": 0,
    }

    # ---- Step 1: Load GeoJSON name list ---------------------------------
    geojson_path = ActiveConfig.STATIC_DATA_DIR / "townlands.json"
    if not geojson_path.exists():
        log.error("full_ingest.geojson_missing | path=%s — aborting", geojson_path)
        return stats

    features = _load_geojson_features(geojson_path)
    log.info("full_ingest.geojson_loaded | features=%d", len(features))

    # ---- Step 2: Probe KG -----------------------------------------------
    kg_online = vrti_sparql.probe_endpoint()
    if kg_online:
        log.info("full_ingest.kg_online | endpoint=%s", vrti_sparql.SPARQL_ENDPOINT)
    else:
        log.warning(
            "full_ingest.kg_offline — geometry, identifiers and standard census will "
            "be skipped.  GeoJSON data (names, area, estate populations, clearances) "
            "will still be stored."
        )

    # ---- Step 3: Process each townland ----------------------------------
    all_census_records: list[CensusRecord] = []
    all_clearances: list[ClearancesRecord] = []

    for feat in features:
        props = feat.get("properties") or {}
        raw_name = str(props.get("TL_ENGLISH") or "").strip()
        if not raw_name:
            log.debug("full_ingest.skip_unnamed_feature | props=%s", list(props.keys()))
            continue

        canonical = normalize_townland_name(raw_name)
        if not canonical:
            continue

        stats["townlands_processed"] += 1

        # ---- 3a: Build base Townland from GeoJSON ----------------------
        gaelic_name = _str_or_none(props.get("TL_GAEILGE"))
        area_sqm    = _float_or_none(props.get("AREA"))
        td_id       = _str_or_none(props.get("TD_ID"))
        guid        = _str_or_none(props.get("GUID"))

        townland = Townland(
            name=canonical,
            name_gaelic=gaelic_name,
            td_id=td_id,
            guid=guid,
            area_sqm=area_sqm,
            county=_str_or_none(props.get("COUNTY_ENGLISH")),
            source="json",
        )

        # ---- 3b: KG enrichment -----------------------------------------
        # Pass the county from the GeoJSON so the KG prefers the right match
        # when a townland name exists in multiple counties.
        json_county = _str_or_none(props.get("COUNTY_ENGLISH"))
        if kg_online:
            kg_dto = _fetch_kg_details(vrti_sparql, raw_name, county=json_county)
            if kg_dto:
                stats["townlands_kg_enriched"] += 1
                # Overlay all KG fields — KG is authoritative for geography
                townland.kg_uri       = kg_dto.uri
                townland.wkt_geometry = kg_dto.wkt_geometry
                townland.centroid_lat = kg_dto.centroid_lat
                townland.centroid_lon = kg_dto.centroid_lon
                townland.barony       = kg_dto.barony
                townland.civil_parish = kg_dto.civil_parish
                townland.county       = kg_dto.county or townland.county
                townland.osm_id       = kg_dto.osm_id
                townland.osi_id       = kg_dto.osi_id
                townland.vrti_id      = kg_dto.vrti_id
                townland.images       = kg_dto.images
                townland.links        = kg_dto.links
                # Prefer KG Gaelic name if not already set from GeoJSON
                if not townland.name_gaelic and kg_dto.name_gaelic:
                    townland.name_gaelic = kg_dto.name_gaelic
                if kg_dto.wkt_geometry or kg_dto.centroid_lat:
                    townland.source = "kg"
            else:
                log.debug("full_ingest.kg_no_match | name=%s", raw_name)
                stats["kg_errors"] += 1

        # ---- 3c: Persist townland --------------------------------------
        if not dry_run:
            townland_repository.upsert(townland)

        log.debug(
            "full_ingest.townland | name=%s kg=%s area_sqm=%s",
            canonical, townland.kg_uri, area_sqm,
        )

        # ---- 3d: Estate population survey records (from GeoJSON) -------
        for year, col in ESTATE_POP_COLUMNS.items():
            total = _int_or_none(props.get(col))
            if total is None:
                continue
            all_census_records.append(CensusRecord(
                townland_name=canonical,
                year=year,
                total=total,
                male=None,       # not recorded in estate surveys
                female=None,
                inhabited=None,
                uninhabited=None,
                source="json",
                kg_uri=None,
            ))
            stats["census_records_json"] += 1

        # ---- 3e: Standard census from KG (1841-1891) -------------------
        if kg_online and townland.kg_uri:
            kg_census = _fetch_kg_census(vrti_sparql, townland.kg_uri)
            for dto in kg_census:
                all_census_records.append(CensusRecord(
                    townland_name=canonical,
                    year=dto.year,
                    male=dto.male,
                    female=dto.female,
                    total=(dto.male or 0) + (dto.female or 0) if (dto.male or dto.female) else None,
                    inhabited=dto.inhabited,
                    uninhabited=dto.uninhabited,
                    source="kg",
                    kg_uri=townland.kg_uri,
                ))
                stats["census_records_kg"] += 1

        # ---- 3f: Clearances records (from GeoJSON) ---------------------
        for year in CLEARANCE_YEARS:
            col = f"Clearances_{year}"
            count_val = _int_or_none(props.get(col))
            if count_val is None:
                continue
            all_clearances.append(ClearancesRecord(
                townland_name=canonical,
                year=year,
                count=count_val,
                source="json",
            ))
            stats["clearances_records"] += 1

    # ---- Step 4: Bulk persist all records ------------------------------
    if not dry_run:
        if all_census_records:
            census_repository.upsert_many(all_census_records)
            log.info(
                "full_ingest.census_persisted | json=%d kg=%d",
                stats["census_records_json"],
                stats["census_records_kg"],
            )

        if all_clearances:
            clearances_repository.upsert_many(all_clearances)
            log.info(
                "full_ingest.clearances_persisted | count=%d",
                stats["clearances_records"],
            )

        # ---- Step 5: Update refresh state ------------------------------
        total_records = (
            stats["townlands_processed"]
            + stats["census_records_json"]
            + stats["census_records_kg"]
            + stats["clearances_records"]
        )
        refresh_state_repository.upsert(
            "full_ingest",
            source="json+kg" if kg_online else "json",
            record_count=total_records,
        )

    log.info(
        "full_ingest.complete | "
        "townlands=%d kg_enriched=%d "
        "census_json=%d census_kg=%d "
        "clearances=%d kg_errors=%d dry_run=%s",
        stats["townlands_processed"],
        stats["townlands_kg_enriched"],
        stats["census_records_json"],
        stats["census_records_kg"],
        stats["clearances_records"],
        stats["kg_errors"],
        dry_run,
    )
    return stats


# ------------------------------------------------------------------ #
# Internal helpers                                                     #
# ------------------------------------------------------------------ #

def _load_geojson_features(path: Path) -> list[dict]:
    """Load GeoJSON features from file."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("features") or []
    except (json.JSONDecodeError, OSError) as exc:
        log.error("full_ingest.geojson_load_failed | path=%s error=%s", path, exc)
        return []


def _fetch_kg_details(vrti_sparql, raw_name: str, county: Optional[str] = None):
    """
    Query the KG for a single townland by its English name.

    Passes the county so the KG prefers the correct match when the same
    name exists in multiple Irish counties (e.g. "Ballard" exists in both
    Wicklow and Offaly — we want the Wicklow one).

    Tries title-case first (the KG standard), then raw name as fallback.
    Returns a TownlandDTO or None.
    """
    # Normalise county to title-case for KG comparison (KG stores "Wicklow" not "WICKLOW")
    kg_county = county.strip().title() if county else None
    try:
        dto = vrti_sparql.get_townland_details_by_name(
            raw_name.strip().title(), county=kg_county
        )
        if dto:
            return dto
        # Fallback: try raw name casing
        dto = vrti_sparql.get_townland_details_by_name(
            raw_name.strip(), county=kg_county
        )
        return dto
    except Exception as exc:
        log.debug("full_ingest._fetch_kg_details failed name=%s err=%s", raw_name, exc)
        return None


def _fetch_kg_census(vrti_sparql, townland_uri: str) -> list:
    """
    Query the KG for census records linked to the given townland URI.
    Returns a list of CensusRecordDTO.
    """
    try:
        return vrti_sparql.get_census_records_for_townland(townland_uri)
    except Exception as exc:
        log.debug("full_ingest._fetch_kg_census failed uri=%s err=%s", townland_uri, exc)
        return []


def _str_or_none(val) -> Optional[str]:
    if val is None:
        return None
    s = str(val).strip()
    return s if s and s.lower() not in ("none", "null", "") else None


def _float_or_none(val) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _int_or_none(val) -> Optional[int]:
    if val is None:
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


# ------------------------------------------------------------------ #
# CLI entry point                                                      #
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Full Coolattin ingest: GeoJSON names + KG enrichment → local DB"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log what would be written without modifying the database.",
    )
    args = parser.parse_args()

    from create_app import create_app
    app = create_app()
    with app.app_context():
        summary = run_full_ingest(dry_run=args.dry_run)
        print("\n=== Full Ingest Summary ===")
        for key, val in summary.items():
            print(f"  {key:<30} {val}")
        ok = summary["townlands_processed"] > 0
        sys.exit(0 if ok else 1)
