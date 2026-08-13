from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from backend.config import ActiveConfig
from backend.models.census_models import (
    CensusFilters,
    CensusRecord,
    CensusMeta,
    CensusResponse,
)

log = logging.getLogger(__name__)

DATASET_KEY_PREFIX = "wicklow_census"


def get_census_data(filters: CensusFilters) -> CensusResponse:
    from backend.repositories import census_repository, refresh_state_repository

    now_iso = datetime.now(timezone.utc).isoformat() + "Z"
    dataset_key = filters.dataset_key()

    records = census_repository.find(filters)
    state = refresh_state_repository.get(
        dataset_key,
        stale_after_days=ActiveConfig.CENSUS_STALE_AFTER_DAYS,
    )

    if records and (state is None or not state.is_stale):
        log.info(
            "census_service.cache_hit | key=%s count=%d",
            dataset_key, len(records),
        )
        return CensusResponse(
            data=[r.to_dict() for r in records],
            meta=CensusMeta(
                source="database",
                cache_status="hit",
                generated_at=now_iso,
                record_count=len(records),
                export_file=state.export_file if state else None,
            ),
        )

    if records and state and state.is_stale:
        log.info(
            "census_service.cache_stale | key=%s — serving existing data, refresh queued",
            dataset_key,
        )
        _schedule_background_refresh(filters)
        return CensusResponse(
            data=[r.to_dict() for r in records],
            meta=CensusMeta(
                source="database",
                cache_status="stale_refresh",
                generated_at=now_iso,
                record_count=len(records),
                export_file=state.export_file if state else None,
            ),
        )

    log.info("census_service.cache_miss | key=%s — querying VRTI KG", dataset_key)
    kg_records, source = _ingest_from_kg_or_seed(filters)

    if not kg_records:
        log.warning("census_service.no_data | key=%s — KG and seed both empty", dataset_key)
        return CensusResponse(
            data=[],
            meta=CensusMeta(
                source=source,
                cache_status="miss",
                generated_at=now_iso,
                record_count=0,
            ),
        )

    from backend.repositories import census_repository as cr
    cr.upsert_many(kg_records)
    log.info("census_service.persisted | count=%d", len(kg_records))

    from backend.services import export_service
    try:
        export_path = export_service.export_census(kg_records, filters)
    except Exception as exc:
        log.warning("census_service.export_failed | error=%s", exc)
        export_path = None

    from backend.repositories import refresh_state_repository as rsr
    rsr.upsert(
        dataset_key,
        source=source,
        record_count=len(kg_records),
        export_file=export_path,
    )

    fresh_records = cr.find(filters)
    return CensusResponse(
        data=[r.to_dict() for r in fresh_records],
        meta=CensusMeta(
            source=source,
            cache_status="miss",
            generated_at=now_iso,
            record_count=len(fresh_records),
            export_file=export_path,
        ),
    )


def get_census_summary(year: Optional[int] = None) -> CensusResponse:
    from backend.repositories import census_repository

    _ensure_census_seeded()

    summary = census_repository.get_summary(year=year)
    now_iso = datetime.now(timezone.utc).isoformat() + "Z"
    return CensusResponse(
        data=[summary],
        meta=CensusMeta(
            source="database",
            cache_status="hit",
            generated_at=now_iso,
            record_count=summary.get("rows", 0),
        ),
    )


def get_townland_detail(townland_name: str) -> CensusResponse:
    from backend.repositories import census_repository

    _ensure_census_seeded()

    detail = census_repository.find_townland_detail(townland_name)
    now_iso = datetime.now(timezone.utc).isoformat() + "Z"

    if detail is None:
        detail = {
            "townland": townland_name.strip().upper(),
            "gaelic_name": None,
            "description": None,
            "placename_theme": None,
            "barony": None,
            "civil_parish": None,
            "electoral_division": None,
            "census": {},
        }

    kg_found = False
    try:
        from backend.integrations import vrti_sparql
        from backend.repositories import townland_repository

        kg_name = townland_name.strip()
        kg_dto = vrti_sparql.get_townland_details_by_name(kg_name, county="Wicklow")
        if kg_dto:
            kg_found = True
            townland_repository.save_kg_cache(townland_name, kg_dto)
            detail["uri"]            = kg_dto.uri
            detail["kg_uri"]         = kg_dto.uri
            detail["county"]         = kg_dto.county
            detail["centroid_lat"]   = kg_dto.centroid_lat
            detail["centroid_lon"]   = kg_dto.centroid_lon
            detail["boundary_wkt"]   = kg_dto.wkt_geometry
            detail["images"]         = kg_dto.images
            detail["links"]          = kg_dto.links
            detail["osm_id"]         = kg_dto.osm_id
            detail["osi_id"]         = kg_dto.osi_id
            detail["vrti_id"]        = kg_dto.vrti_id
            detail["kg_civil_parish"] = kg_dto.civil_parish
            detail["kg_barony"]      = kg_dto.barony
            if kg_dto.name_gaelic:
                detail["gaelic_name"] = kg_dto.name_gaelic
    except Exception as exc:
        log.debug("census_service.kg_enrich_failed townland=%s error=%s", townland_name, exc)

    has_db_cache = bool(
        detail.get("kg_uri") or detail.get("centroid_lat")
        or detail.get("county") or detail.get("barony")
    )
    if not kg_found and has_db_cache:
        kg_found = True

    has_census = bool(detail.get("census"))
    if not has_census and not kg_found:
        return CensusResponse(
            data=[],
            meta=CensusMeta(
                source="database",
                cache_status="miss",
                generated_at=now_iso,
                record_count=0,
            ),
        )

    source = "kg_enriched" if kg_found else "database"
    return CensusResponse(
        data=[detail],
        meta=CensusMeta(
            source=source,
            cache_status="hit",
            generated_at=now_iso,
            record_count=1,
        ),
    )


def get_available_townlands() -> CensusResponse:
    from backend.repositories import census_repository

    _ensure_census_seeded()
    names = census_repository.get_townland_names()
    now_iso = datetime.now(timezone.utc).isoformat() + "Z"

    return CensusResponse(
        data=names,
        meta=CensusMeta(
            source="database",
            cache_status="hit",
            generated_at=now_iso,
            record_count=len(names),
        ),
    )


def force_refresh(filters: Optional[CensusFilters] = None) -> dict:
    if filters is None:
        filters = CensusFilters()

    log.info("census_service.force_refresh | filters=%s", vars(filters))

    kg_records, source = _ingest_from_kg_or_seed(filters, force=True)

    from backend.repositories import census_repository, refresh_state_repository
    from backend.services import export_service

    census_repository.upsert_many(kg_records)

    export_path = None
    try:
        export_path = export_service.export_census(kg_records, filters)
    except Exception as exc:
        log.warning("census_service.force_refresh.export_failed | error=%s", exc)

    refresh_state_repository.upsert(
        filters.dataset_key(),
        source=source,
        record_count=len(kg_records),
        export_file=export_path,
    )

    return {
        "status": "refreshed",
        "record_count": len(kg_records),
        "source": source,
        "export_file": export_path,
    }


def _ingest_from_kg_or_seed(
    filters: CensusFilters,
    force: bool = False,
) -> tuple[list[CensusRecord], str]:
    from backend.integrations import vrti_sparql

    try:
        kg_dtos = vrti_sparql.get_census_records_for_county(
            county=None,
            year=filters.year,
        )
    except Exception as exc:
        log.warning("census_service.kg_unreachable | error=%s", exc)
        return [], "kg_error"

    if not kg_dtos:
        log.warning(
            "census_service.kg_empty | filters=%s — "
            "Run: python -m coolattin.jobs.full_ingest",
            vars(filters),
        )
        return [], "kg_empty"

    log.info("census_service.kg_returned | count=%d", len(kg_dtos))
    from backend.services.townland_service import canonical_name
    records = [
        CensusRecord(
            townland_name=canonical_name(dto.townland_name),
            year=dto.year,
            male=dto.male,
            female=dto.female,
            total=(dto.male or 0) + (dto.female or 0) if (dto.male or dto.female) else None,
            inhabited=dto.inhabited,
            uninhabited=dto.uninhabited,
            source="kg",
            kg_uri=dto.townland_uri,
        )
        for dto in kg_dtos
        if dto.year and dto.townland_name
    ]
    return records, "kg_refresh"


def _ensure_census_seeded() -> None:
    from backend.repositories import census_repository
    if census_repository.count_records() == 0:
        log.warning(
            "census_service.db_empty — no census records in database. "
            "Run: python -m coolattin.jobs.full_ingest"
        )


def _schedule_background_refresh(filters: CensusFilters) -> None:
    log.info(
        "census_service.refresh_scheduled | key=%s (stub — wire to task queue to activate)",
        filters.dataset_key(),
    )
