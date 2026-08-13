from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
log = logging.getLogger(__name__)

ESTATE_SURVEY_YEARS = [1827, 1839, 1848, 1850, 1860, 1868]

ESTATE_POP_COLUMNS = {
    1827: "T_POP_1827",
    1839: "T_POP_1839_",
    1848: "T_POP_1848",
    1850: "T_POP_1850",
    1860: "T_POP_1860",
    1868: "T_POP_1868",
}

CLEARANCE_YEARS = list(range(1847, 1857))


def run_full_ingest(dry_run: bool = False) -> dict:
    from backend.integrations import vrti_sparql
    from backend.repositories import (
        townland_repository,
        census_repository,
        refresh_state_repository,
    )
    from backend.repositories import clearances_repository
    from backend.jobs.census_seed import load_standard_census_seed_records
    from backend.models.census_models import Townland, CensusRecord, ClearancesRecord
    from backend.services.townland_service import canonical_name, normalize_townland_name
    from backend.config import ActiveConfig

    stats = {
        "townlands_processed": 0,
        "townlands_kg_enriched": 0,
        "census_records_json": 0,
        "census_records_kg": 0,
        "census_records_csv_seed": 0,
        "clearances_records": 0,
        "kg_errors": 0,
    }

    geojson_path = ActiveConfig.STATIC_DATA_DIR / "townlands.json"
    if not geojson_path.exists():
        log.error("full_ingest.geojson_missing | path=%s — aborting", geojson_path)
        return stats

    features = _load_geojson_features(geojson_path)
    log.info("full_ingest.geojson_loaded | features=%d", len(features))

    kg_online = vrti_sparql.probe_endpoint()
    if kg_online:
        log.info("full_ingest.kg_online | endpoint=%s", vrti_sparql.SPARQL_ENDPOINT)
    else:
        log.warning(
            "full_ingest.kg_offline — geometry, identifiers and standard census will "
            "be skipped.  GeoJSON data (names, area, estate populations, clearances) "
            "will still be stored."
        )

    all_census_records: list[CensusRecord] = []
    all_clearances: list[ClearancesRecord] = []
    estate_name_map: dict[str, str] = {}

    for feat in features:
        props = feat.get("properties") or {}
        raw_name = str(props.get("TL_ENGLISH") or "").strip()
        if not raw_name:
            log.debug("full_ingest.skip_unnamed_feature | props=%s", list(props.keys()))
            continue

        canonical = normalize_townland_name(raw_name)
        if not canonical:
            continue
        estate_name_map[canonical] = canonical
        estate_name_map[canonical_name(raw_name)] = canonical

        stats["townlands_processed"] += 1

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

        json_county = _str_or_none(props.get("COUNTY_ENGLISH"))
        if kg_online:
            kg_dto = _fetch_kg_details(vrti_sparql, raw_name, county=json_county)
            if kg_dto:
                stats["townlands_kg_enriched"] += 1
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
                if not townland.name_gaelic and kg_dto.name_gaelic:
                    townland.name_gaelic = kg_dto.name_gaelic
                if kg_dto.centroid_flag:
                    townland.geometry_flag = kg_dto.centroid_flag
                if kg_dto.wkt_geometry or kg_dto.centroid_lat:
                    townland.source = "kg"
            else:
                log.debug("full_ingest.kg_no_match | name=%s", raw_name)
                stats["kg_errors"] += 1

        if not dry_run:
            townland_repository.upsert(townland)

        log.debug(
            "full_ingest.townland | name=%s kg=%s area_sqm=%s",
            canonical, townland.kg_uri, area_sqm,
        )

        for year, col in ESTATE_POP_COLUMNS.items():
            total = _int_or_none(props.get(col))
            if total is None:
                continue
            all_census_records.append(CensusRecord(
                townland_name=canonical,
                year=year,
                total=total,
                male=None,
                female=None,
                inhabited=None,
                uninhabited=None,
                source="json",
                kg_uri=None,
            ))
            stats["census_records_json"] += 1

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

    if kg_online and estate_name_map:
        kg_census = vrti_sparql.get_census_records_for_county(county="Wicklow")
        seen_census_keys = {(rec.townland_name, rec.year) for rec in all_census_records}
        skipped_non_estate = 0

        for dto in kg_census:
            raw_name = dto.townland_name or ""
            match_key = canonical_name(raw_name)
            canonical = estate_name_map.get(match_key)
            if not canonical:
                canonical = estate_name_map.get(normalize_townland_name(raw_name))
            if not canonical:
                skipped_non_estate += 1
                continue

            census_key = (canonical, dto.year)
            if census_key in seen_census_keys:
                continue

            all_census_records.append(CensusRecord(
                townland_name=canonical,
                year=dto.year,
                male=dto.male,
                female=dto.female,
                total=(dto.male or 0) + (dto.female or 0) if (dto.male or dto.female) else None,
                inhabited=dto.inhabited,
                uninhabited=dto.uninhabited,
                source="kg",
                kg_uri=dto.townland_uri,
            ))
            seen_census_keys.add(census_key)
            stats["census_records_kg"] += 1

        log.info(
            "full_ingest.census_kg_filtered | wicklow_rows=%d estate_rows=%d skipped_non_estate=%d",
            len(kg_census),
            stats["census_records_kg"],
            skipped_non_estate,
        )

        if stats["census_records_kg"] == 0:
            seed_records = load_standard_census_seed_records(
                allowed_townlands=set(estate_name_map.values())
            )
            for rec in seed_records:
                census_key = (rec.townland_name, rec.year)
                if census_key in seen_census_keys:
                    continue
                all_census_records.append(rec)
                seen_census_keys.add(census_key)
                stats["census_records_csv_seed"] += 1
            log.warning(
                "full_ingest.census_kg_empty_using_csv_seed | estate_seed_rows=%d",
                stats["census_records_csv_seed"],
            )

    if not dry_run:
        if all_census_records:
            census_repository.upsert_many(all_census_records)
            log.info(
                "full_ingest.census_persisted | json=%d kg=%d csv_seed=%d",
                stats["census_records_json"],
                stats["census_records_kg"],
                stats["census_records_csv_seed"],
            )

        if all_clearances:
            clearances_repository.upsert_many(all_clearances)
            log.info(
                "full_ingest.clearances_persisted | count=%d",
                stats["clearances_records"],
            )

        total_records = (
            stats["townlands_processed"]
            + stats["census_records_json"]
            + stats["census_records_kg"]
            + stats["census_records_csv_seed"]
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
        "census_json=%d census_kg=%d census_csv_seed=%d "
        "clearances=%d kg_errors=%d dry_run=%s",
        stats["townlands_processed"],
        stats["townlands_kg_enriched"],
        stats["census_records_json"],
        stats["census_records_kg"],
        stats["census_records_csv_seed"],
        stats["clearances_records"],
        stats["kg_errors"],
        dry_run,
    )
    return stats


def _load_geojson_features(path: Path) -> list[dict]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("features") or []
    except (json.JSONDecodeError, OSError) as exc:
        log.error("full_ingest.geojson_load_failed | path=%s error=%s", path, exc)
        return []


def _fetch_kg_details(vrti_sparql, raw_name: str, county: Optional[str] = None):
    kg_county = county.strip().title() if county else None
    try:
        dto = vrti_sparql.get_townland_details_by_name(
            raw_name.strip().title(), county=kg_county
        )
        if dto:
            return dto
        dto = vrti_sparql.get_townland_details_by_name(
            raw_name.strip(), county=kg_county
        )
        return dto
    except Exception as exc:
        log.debug("full_ingest._fetch_kg_details failed name=%s err=%s", raw_name, exc)
        return None


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

    from backend.app import create_app
    app = create_app()
    with app.app_context():
        summary = run_full_ingest(dry_run=args.dry_run)
        print("\n=== Full Ingest Summary ===")
        for key, val in summary.items():
            print(f"  {key:<30} {val}")
        ok = summary["townlands_processed"] > 0
        sys.exit(0 if ok else 1)
