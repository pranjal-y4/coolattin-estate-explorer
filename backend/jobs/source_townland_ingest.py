"""
backend/jobs/source_townland_ingest.py

Resolve townland names carried by the estate person records into canonical
townland entities.

`unified_record.townland` holds the place name exactly as it was written in the
estate ledgers — thousands of spelling variants, compound holdings and
historical forms.  Each distinct value is a SOURCE townland record: this job
feeds it through the entity-resolution flow, which decides whether it belongs
to an existing canonical townland, needs review, or is a townland the database
does not yet hold.

Usage (from project root):
  python -m backend.jobs.source_townland_ingest
  python -m backend.jobs.source_townland_ingest --dry-run
  python -m backend.jobs.source_townland_ingest --limit 50

Idempotent: every distinct source name keeps a stable source_record_id, so a
second run replays the existing cross-references instead of creating anything.
"""
from __future__ import annotations

import argparse
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
log = logging.getLogger(__name__)

SOURCE = "unified_record"
DEFAULT_COUNTY = "WICKLOW"


def run_source_townland_ingest(dry_run: bool = False, limit: int = 0) -> dict:
    """
    Resolve every distinct estate-record townland name.

    Returns a summary dict with the resolution breakdown and the entities that
    ended up without map geometry.
    """
    from backend.repositories import unified_townland_repository
    from backend.services import townland_resolution
    from backend.services.map_service import invalidate_townland_featurecollection

    stats = {
        "source_names": 0,
        "matched_existing": 0,
        "new_canonical": 0,
        "pending_review": 0,
        "ambiguous": 0,
        "skipped": 0,
        "without_geometry": 0,
    }
    created: list[tuple[str, str]] = []
    review: list[tuple[str, str]] = []

    names = unified_townland_repository.distinct_source_townlands(limit=limit)
    log.info("source_townland_ingest.start | distinct_names=%d dry_run=%s", len(names), dry_run)

    for raw_name, occurrences in names:
        stats["source_names"] += 1
        source_record_id = f"{SOURCE}:{raw_name.strip().upper()}"
        src = townland_resolution.SourceTownland(
            name=raw_name,
            source=SOURCE,
            source_record_id=source_record_id,
            county=DEFAULT_COUNTY,
        )
        if dry_run:
            continue

        outcome = townland_resolution.resolve_source_townland(src)
        if outcome.status == "matched":
            stats["matched_existing"] += 1
        elif outcome.status == "created":
            stats["new_canonical"] += 1
            created.append((raw_name, outcome.canonical_name or ""))
        elif outcome.status == "review":
            stats["pending_review"] += 1
            review.append((raw_name, outcome.canonical_name or ""))
        elif outcome.status == "ambiguous":
            stats["ambiguous"] += 1
        else:
            stats["skipped"] += 1

        if outcome.status in ("created", "review") and not outcome.has_geometry:
            stats["without_geometry"] += 1

        log.debug(
            "source_townland_ingest.resolved | name=%s occurrences=%d status=%s "
            "method=%s canonical=%s",
            raw_name, occurrences, outcome.status, outcome.method, outcome.canonical_name,
        )

    if not dry_run:
        invalidate_townland_featurecollection()

    if created:
        log.info(
            "source_townland_ingest.new_canonical_townlands | count=%d sample=%s",
            len(created), created[:10],
        )
    if stats["without_geometry"]:
        log.warning(
            "source_townland_ingest.canonical_without_geometry | count=%d — these "
            "townlands exist in the database but cannot be drawn on the map until "
            "boundary geometry is available",
            stats["without_geometry"],
        )

    log.info("source_townland_ingest.complete | %s", stats)
    return stats


def enrich_geometry_from_kg(dry_run: bool = False, limit: int = 0) -> dict:
    """
    Give resolved townlands that have no boundary geometry a polygon, when the
    VRTI Knowledge Graph holds one for the same place.

    The KG hit is fed in as an ordinary `kg` SOURCE record with
    allow_create=False, so entity resolution — not this job — decides whether it
    is the same townland.  A KG record whose county or barony disagrees is
    rejected: several Irish townland names repeat across counties and the KG
    name query does not reliably honour the county filter.  No geometry is ever
    invented, and a townland that cannot be corroborated simply stays
    geometry-less.
    """
    from backend.integrations import vrti_sparql
    from backend.repositories import townland_repository
    from backend.services import townland_resolution
    from backend.services.map_service import invalidate_townland_featurecollection

    stats = {"considered": 0, "kg_hits": 0, "geometry_added": 0, "rejected": 0, "no_kg_match": 0}

    if not vrti_sparql.probe_endpoint():
        log.error("source_townland_ingest.kg_offline — geometry enrichment skipped")
        return stats

    targets = townland_repository.find_resolved_without_geometry(limit=limit)
    log.info("source_townland_ingest.geometry_targets | count=%d", len(targets))

    for row in targets:
        stats["considered"] += 1
        try:
            dto = vrti_sparql.get_townland_details_by_name(
                row["name"].title(), county=(row["county"] or "Wicklow").title()
            )
        except Exception as exc:
            log.debug("source_townland_ingest.kg_lookup_failed | name=%s error=%s", row["name"], exc)
            dto = None

        if not dto or not dto.wkt_geometry:
            stats["no_kg_match"] += 1
            continue
        stats["kg_hits"] += 1

        if dry_run:
            continue

        outcome = townland_resolution.resolve_source_townland(
            townland_resolution.SourceTownland(
                name=row["name"],
                source="kg",
                source_record_id=dto.uri,
                name_gaelic=dto.name_gaelic,
                barony=dto.barony,
                civil_parish=dto.civil_parish,
                county=dto.county,
                wkt_geometry=dto.wkt_geometry,
                centroid_lat=dto.centroid_lat,
                centroid_lon=dto.centroid_lon,
                kg_uri=dto.uri,
                osm_id=dto.osm_id,
                osi_id=dto.osi_id,
                vrti_id=dto.vrti_id,
            ),
            allow_create=False,
        )
        if outcome.status == "matched" and "wkt_geometry" in outcome.fields_filled:
            stats["geometry_added"] += 1
            log.info(
                "source_townland_ingest.geometry_resolved | name=%s entity_id=%s kg=%s",
                outcome.canonical_name, outcome.entity_id, dto.uri,
            )
        elif outcome.status != "matched":
            stats["rejected"] += 1
            log.info(
                "source_townland_ingest.geometry_rejected | name=%s kg_barony=%s "
                "kg_county=%s reasons=%s",
                row["name"], dto.barony, dto.county, outcome.missing_evidence + outcome.conflicts,
            )

    if not dry_run:
        invalidate_townland_featurecollection()
    log.info("source_townland_ingest.geometry_complete | %s", stats)
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Resolve estate-record townland names into canonical entities"
    )
    parser.add_argument("--dry-run", action="store_true", help="Report without writing.")
    parser.add_argument("--limit", type=int, default=0, help="Process only the N most common names.")
    parser.add_argument(
        "--enrich-geometry", action="store_true",
        help="Also resolve boundary geometry for townlands that have none.",
    )
    args = parser.parse_args()

    from create_app import create_app
    app = create_app()
    with app.app_context():
        summary = run_source_townland_ingest(dry_run=args.dry_run, limit=args.limit)
        print("\n=== Source Townland Ingest Summary ===")
        for key, val in summary.items():
            print(f"  {key:<24} {val}")

        if args.enrich_geometry:
            geometry = enrich_geometry_from_kg(dry_run=args.dry_run, limit=args.limit)
            print("\n=== Geometry Resolution Summary ===")
            for key, val in geometry.items():
                print(f"  {key:<24} {val}")

        sys.exit(0 if summary["source_names"] > 0 else 1)
