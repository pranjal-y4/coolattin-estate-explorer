"""
coolattin/jobs/townlands_ingest.py

Wicklow townland ingestion job.

Usage (from project root):
  python -m coolattin.jobs.townlands_ingest

What this does:
  1. Fetches Wicklow townlands from the VRTI KG
  2. Reconciles names against the townlands.ie reference seed
  3. Upserts records into the local townland table
  4. Exports to Excel in exports/townlands/
  5. Updates refresh_state

Also saves a seed snapshot to data/seed/wicklow_townlands_reference.json
for use by townlands_reference.py in future reconciliation runs.
"""
from __future__ import annotations

import json
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
log = logging.getLogger(__name__)


def run_townlands_ingest() -> int:
    """
    Full townland ingestion pipeline.
    Returns count of townlands persisted.
    """
    from backend.integrations import vrti_sparql
    from backend.repositories import townland_repository, refresh_state_repository
    from backend.services.townland_service import (
        normalize_townland_name,
        reconcile_with_reference,
    )
    from backend.services import export_service
    from backend.models.census_models import Townland
    from config import ActiveConfig

    log.info("townlands_ingest.start")

    # Step 1: Probe endpoint
    if not vrti_sparql.probe_endpoint():
        log.error("townlands_ingest.endpoint_unreachable — aborting")
        return 0

    # Step 2: Fetch from KG — no county restriction, get all available townlands
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

    # Step 3: Reconcile with reference
    townlands = reconcile_with_reference(townlands)

    # Step 4: Persist
    count = townland_repository.upsert_many(townlands)
    log.info("townlands_ingest.persisted | count=%d", count)

    # Step 5: Save reference snapshot for future reconciliation
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

    # Step 6: Export
    export_path = None
    try:
        export_path = export_service.export_townlands(townlands)
        log.info("townlands_ingest.exported | path=%s", export_path)
    except Exception as exc:
        log.warning("townlands_ingest.export_failed | error=%s", exc)

    # Step 7: Update refresh state
    refresh_state_repository.upsert(
        "wicklow_townlands",
        source=source,
        record_count=count,
        export_file=export_path,
    )

    log.info("townlands_ingest.complete | count=%d", count)
    return count


if __name__ == "__main__":
    from create_app import create_app
    app = create_app()
    with app.app_context():
        n = run_townlands_ingest()
        sys.exit(0 if n > 0 else 1)
