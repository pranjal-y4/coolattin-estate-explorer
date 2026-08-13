from __future__ import annotations

import json
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
log = logging.getLogger(__name__)


def run_townlands_ingest() -> int:
    from backend.integrations import vrti_sparql
    from backend.repositories import townland_repository, refresh_state_repository
    from backend.services.townland_service import (
        normalize_townland_name,
        reconcile_with_reference,
    )
    from backend.services import export_service
    from backend.models.census_models import Townland
    from backend.config import ActiveConfig

    log.info("townlands_ingest.start")

    if not vrti_sparql.probe_endpoint():
        log.error("townlands_ingest.endpoint_unreachable — aborting")
        return 0

    kg_dtos = vrti_sparql.get_townlands(county=None, limit=5000)
    log.info("townlands_ingest.kg_fetched | count=%d", len(kg_dtos))

    if kg_dtos:
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
        source = "kg_refresh"
    else:
        log.warning(
            "townlands_ingest.kg_empty — no townlands returned. "
            "For a complete ingest (GeoJSON + KG), run: "
            "python -m coolattin.jobs.full_ingest"
        )
        return 0

    if not townlands:
        log.error("townlands_ingest.no_townlands — nothing to persist")
        return 0

    townlands = reconcile_with_reference(townlands)

    count = townland_repository.upsert_many(townlands)
    log.info("townlands_ingest.persisted | count=%d", count)

    snapshot_path = ActiveConfig.DATA_SEED_DIR / "wicklow_townlands_reference.json"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot = [
        {
            "name": t.name,
            "barony": t.barony,
            "civil_parish": t.civil_parish,
            "electoral_division": t.electoral_division,
            "gaelic_name": t.name_gaelic,
        }
        for t in townlands
    ]
    snapshot_path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("townlands_ingest.snapshot_saved | path=%s", snapshot_path)

    export_path = None
    try:
        export_path = export_service.export_townlands(townlands)
        log.info("townlands_ingest.exported | path=%s", export_path)
    except Exception as exc:
        log.warning("townlands_ingest.export_failed | error=%s", exc)

    refresh_state_repository.upsert(
        "wicklow_townlands",
        source=source,
        record_count=count,
        export_file=export_path,
    )

    log.info("townlands_ingest.complete | count=%d", count)
    return count


if __name__ == "__main__":
    from backend.app import create_app
    app = create_app()
    with app.app_context():
        n = run_townlands_ingest()
        sys.exit(0 if n > 0 else 1)
