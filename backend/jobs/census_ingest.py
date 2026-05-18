"""
coolattin/jobs/census_ingest.py

Census ingestion job — full Wicklow census ingest from VRTI KG.

=================================================================
RUN THIS TO POPULATE THE DATABASE FROM THE KNOWLEDGE GRAPH.
=================================================================

Usage (from project root):
  python -m coolattin.jobs.census_ingest
  python -m coolattin.jobs.census_ingest --year 1841

What this does:
  1. Probes the VRTI SPARQL endpoint (fails fast if unreachable)
  2. Fetches all Wicklow census records from the KG
  3. Normalises townland names via townland_service
  4. Upserts records into the local SQLite database
  5. Generates an Excel export in exports/census/
  6. Updates the refresh_state table

When to run:
  - First setup (DB is empty)
  - After the KG is updated with new census data
  - On a schedule (e.g. nightly via cron) when census data changes frequently

After this runs, the web app serves data from the local DB
without calling the KG on every request.
"""
from __future__ import annotations

import argparse
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
log = logging.getLogger(__name__)


def run_census_ingest(year: int | None = None) -> int:
    """
    Full census ingestion pipeline.
    Returns the count of records ingested (0 on failure).
    """
    from backend.integrations import vrti_sparql
    from backend.repositories import census_repository, refresh_state_repository
    from backend.services.census_service import DATASET_KEY_PREFIX
    from backend.services import export_service
    from backend.services.townland_service import canonical_name
    from backend.models.census_models import CensusRecord, CensusFilters
    from backend.jobs.census_seed import load_standard_census_seed_records

    log.info("census_ingest.start | year=%s", year)

    # Step 1: Probe endpoint
    if not vrti_sparql.probe_endpoint():
        log.error(
            "census_ingest.endpoint_unreachable | %s — aborting.",
            vrti_sparql.SPARQL_ENDPOINT,
        )
        return 0

    log.info("census_ingest.endpoint_ok | %s", vrti_sparql.SPARQL_ENDPOINT)

    # Step 2: Fetch from KG
    kg_dtos = vrti_sparql.get_census_records_for_county(county="Wicklow", year=year)
    log.info("census_ingest.kg_fetched | count=%d", len(kg_dtos))

    if not kg_dtos:
        log.warning(
            "census_ingest.kg_empty — no census records returned from KG. "
            "Falling back to bundled CSV seed."
        )
        records = load_standard_census_seed_records(years=[year] if year else None)
        source = "csv_seed"
    else:
        # Step 3: Normalise
        records = [
            CensusRecord(
                townland_name=canonical_name(dto.townland_name),
                year=dto.year,
                male=dto.male,
                female=dto.female,
                total=(dto.male or 0) + (dto.female or 0),
                inhabited=dto.inhabited,
                uninhabited=dto.uninhabited,
                source="kg",
                kg_uri=dto.townland_uri,
            )
            for dto in kg_dtos
            if dto.year and dto.townland_name
        ]
        source = "kg_refresh"

    if not records:
        log.error("census_ingest.no_records — nothing to persist.")
        return 0

    # Step 4: Persist to DB
    count = census_repository.upsert_many(records)
    log.info("census_ingest.persisted | count=%d", count)

    # Step 5: Export to Excel
    dataset_key = f"{DATASET_KEY_PREFIX}_{year}" if year else DATASET_KEY_PREFIX
    filters = CensusFilters(year=year, limit=len(records))
    export_path = export_service.export_census(records, filters)
    log.info("census_ingest.exported | path=%s", export_path)

    # Step 6: Update refresh state
    refresh_state_repository.upsert(
        dataset_key,
        source=source,
        record_count=count,
        export_file=export_path,
    )

    log.info("census_ingest.complete | count=%d source=%s export=%s",
             count, source, export_path)
    return count


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest Wicklow census data from VRTI KG")
    parser.add_argument("--year", type=int, default=None,
                        help="Census year to ingest (e.g. 1841). Omit for all years.")
    args = parser.parse_args()

    from create_app import create_app
    app = create_app()
    with app.app_context():
        n = run_census_ingest(year=args.year)
        sys.exit(0 if n > 0 else 1)
