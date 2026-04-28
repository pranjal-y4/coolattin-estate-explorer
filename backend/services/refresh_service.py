"""
coolattin/services/refresh_service.py

Refresh orchestration — coordinates forced re-ingestion from KG.

Called by POST /api/census/refresh and POST /api/townlands/wicklow/refresh.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def trigger_census_refresh(year: int | None = None) -> dict:
    """
    Force a synchronous census refresh from the VRTI KG.
    Returns a status dict for the API response.
    """
    from backend.services.census_service import force_refresh
    from backend.models.census_models import CensusFilters

    filters = CensusFilters(year=year)
    log.info("refresh_service.census_refresh_triggered | year=%s", year)
    return force_refresh(filters)


def trigger_townlands_refresh() -> dict:
    """
    Force a synchronous townland refresh from the VRTI KG.
    Returns a status dict for the API response.
    """
    from backend.integrations import vrti_sparql
    from backend.repositories import townland_repository, refresh_state_repository
    from backend.services.townland_service import normalize_townland_name, reconcile_with_reference
    from backend.services import export_service
    from backend.models.census_models import Townland

    log.info("refresh_service.townlands_refresh_triggered")

    kg_dtos = vrti_sparql.get_townlands(county=None, limit=5000)
    if not kg_dtos:
        log.warning("refresh_service.townlands_kg_empty")
        return {"status": "no_data", "record_count": 0, "source": "kg"}

    townlands = [
        Townland(
            name=normalize_townland_name(dto.name),
            name_gaelic=dto.name_gaelic,
            kg_uri=dto.uri,
            wkt_geometry=dto.wkt_geometry,
            source="kg",
        )
        for dto in kg_dtos
        if dto.name
    ]

    # Enrich with barony/parish from reference
    townlands = reconcile_with_reference(townlands)

    count = townland_repository.upsert_many(townlands)

    export_path = None
    try:
        export_path = export_service.export_townlands(townlands)
    except Exception as exc:
        log.warning("refresh_service.townlands_export_failed | error=%s", exc)

    refresh_state_repository.upsert(
        "wicklow_townlands",
        source="kg_refresh",
        record_count=count,
        export_file=export_path,
    )

    return {
        "status": "refreshed",
        "record_count": count,
        "source": "kg_refresh",
        "export_file": export_path,
    }
