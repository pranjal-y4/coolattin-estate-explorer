"""
backend/services/retrieval_chunks.py

Shared Ask-page retrieval chunk builders.

This module is intentionally storage-agnostic: it prepares rich chunk documents
for either in-memory fallback retrieval or persistent pgvector indexing.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from extensions import get_db_conn

log = logging.getLogger(__name__)


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _source_type_for_unified_row(row: dict[str, Any]) -> str:
    if row.get("has_emigration_record"):
        return "emigration"
    if row.get("has_eviction_record"):
        return "eviction"
    if row.get("has_tenancy_record"):
        return "estate_survey"
    return "unified_record"


def _record_title(row: dict[str, Any]) -> str:
    name = (row.get("canonical_name") or "Unknown Person").strip()
    source_type = _source_type_for_unified_row(row).replace("_", " ").title()
    year = row.get("year") or "Unknown year"
    return f"{name} - {source_type} - {year}"


def _record_text(row: dict[str, Any]) -> str:
    related_fields: list[str] = []
    if row.get("legal_action"):
        related_fields.append(f"Legal action: {row['legal_action']}")
    if row.get("occupation"):
        related_fields.append(f"Occupation: {row['occupation']}")
    if row.get("relationship_to_head_of_household"):
        related_fields.append(
            f"Relationship: {row['relationship_to_head_of_household']}"
        )
    if row.get("ship_name"):
        related_fields.append(f"Ship: {row['ship_name']}")
    if row.get("departure"):
        related_fields.append(f"Departure: {row['departure']}")
    if row.get("arrival"):
        related_fields.append(f"Arrival: {row['arrival']}")
    if row.get("comments"):
        related_fields.append(f"Comments: {row['comments']}")

    related = " | ".join(related_fields) if related_fields else "None recorded"
    return (
        f"Name: {row.get('canonical_name') or 'Unknown'}\n"
        f"Place: {row.get('townland_norm') or row.get('townland') or row.get('parish') or 'Unknown'}\n"
        f"Source: {_source_type_for_unified_row(row).replace('_', ' ').title()}\n"
        f"Year: {row.get('year') or 'Unknown'}\n"
        f"Age: {row.get('age') or row.get('age_head_of_household') or 'Unknown'}\n"
        f"Related fields: {related}\n"
        f"Record id: {row.get('record_id') or 'Unknown'}"
    )


def _build_unified_chunks(conn) -> list[dict[str, Any]]:
    if not _table_exists(conn, "unified_record"):
        return []

    rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT record_id, canonical_name, townland_norm, townland, parish, year,
                   role, legal_action, occupation, age, age_head_of_household,
                   relationship_to_head_of_household, ship_name, departure, arrival,
                   comments, has_emigration_record, has_eviction_record,
                   has_tenancy_record
            FROM unified_record
            WHERE COALESCE(canonical_name, '') != ''
            ORDER BY year, record_id
            """
        ).fetchall()
    ]
    chunks: list[dict[str, Any]] = []
    person_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        source_type = _source_type_for_unified_row(row)
        chunk_id = f"{source_type}:{row.get('record_id')}"
        chunks.append(
            {
                "id": chunk_id,
                "chunk_id": chunk_id,
                "source_type": source_type,
                "source_table": "unified_record",
                "source_record_id": str(row.get("record_id") or ""),
                "title": _record_title(row),
                "text": _record_text(row),
                "metadata": {
                    "record_id": row.get("record_id"),
                    "canonical_name": row.get("canonical_name"),
                    "townland_norm": row.get("townland_norm") or row.get("townland"),
                    "parish": row.get("parish"),
                    "year": row.get("year"),
                    "source_type": source_type,
                },
            }
        )
        person_key = (
            str(row.get("canonical_name") or "").strip(),
            str(row.get("townland_norm") or row.get("townland") or "").strip(),
        )
        if person_key[0]:
            person_groups[person_key].append(
                {
                    "source": source_type,
                    "year": row.get("year"),
                    "townland_norm": row.get("townland_norm") or row.get("townland"),
                }
            )

    try:
        from backend.services.voyage_embeddings import build_person_passport

        for (display_name, townland_norm), records in person_groups.items():
            person_chunk_id = f"person:{display_name.upper()}:{townland_norm.upper()}"
            chunks.append(
                {
                    "id": person_chunk_id,
                    "chunk_id": person_chunk_id,
                    "source_type": "person_passport",
                    "source_table": "unified_record",
                    "source_record_id": display_name,
                    "title": f"{display_name} - Person Passport",
                    "text": build_person_passport(display_name, records),
                    "metadata": {
                        "display_name": display_name,
                        "townland_norm": townland_norm or None,
                        "supporting_record_count": len(records),
                    },
                }
            )
    except Exception as exc:
        log.debug("retrieval_chunks.person_passports_skipped error=%s", exc)

    return chunks


def _build_place_chunks(conn) -> list[dict[str, Any]]:
    if not _table_exists(conn, "townland"):
        return []

    try:
        from backend.services.voyage_embeddings import (
            build_community_summary,
            build_place_passport,
        )
    except Exception:
        build_place_passport = None
        build_community_summary = None

    chunks: list[dict[str, Any]] = []

    townland_rows = conn.execute(
        """
        SELECT id, name, civil_parish, barony, county, description
        FROM townland
        WHERE name IS NOT NULL
        ORDER BY name
        """
    ).fetchall()
    for row in townland_rows:
        stats = conn.execute(
            """
            SELECT
              (SELECT total FROM census_record WHERE townland_id=? AND year=1851 LIMIT 1) AS population_1851,
              (SELECT SUM(count) FROM clearances_record WHERE townland_id=?) AS total_evictions,
              (SELECT COUNT(DISTINCT record_id) FROM unified_record WHERE townland_norm=? AND has_emigration_record=1) AS total_emigrants
            """,
            (row["id"], row["id"], row["name"]),
        ).fetchone()
        stats_dict = dict(stats) if stats else {}
        text = (
            build_place_passport(
                name=row["name"],
                civil_parish=row["civil_parish"],
                barony=row["barony"],
                county=row["county"],
                description=row["description"],
                stats=stats_dict,
            )
            if build_place_passport
            else f"{row['name']} is a Coolattin estate townland in {row['civil_parish'] or row['barony'] or row['county'] or 'unknown location'}."
        )
        chunk_id = f"place:{row['id']}"
        chunks.append(
            {
                "id": chunk_id,
                "chunk_id": chunk_id,
                "source_type": "place_passport",
                "source_table": "townland",
                "source_record_id": str(row["id"]),
                "title": f"{row['name']} - Place Passport",
                "text": text,
                "metadata": {
                    "townland_id": row["id"],
                    "name": row["name"],
                    "civil_parish": row["civil_parish"],
                    "barony": row["barony"],
                    "county": row["county"],
                },
            }
        )

    if build_community_summary:
        parish_rows = conn.execute(
            """
            SELECT t.civil_parish,
                   COUNT(DISTINCT t.id) AS townland_count,
                   SUM(cr.count) AS total_evictions,
                   MIN(cr.year) AS min_year,
                   MAX(cr.year) AS max_year
            FROM townland t
            LEFT JOIN clearances_record cr ON cr.townland_id = t.id
            WHERE t.civil_parish IS NOT NULL
            GROUP BY t.civil_parish
            ORDER BY t.civil_parish
            """
        ).fetchall()
        for row in parish_rows:
            emig_row = conn.execute(
                """
                SELECT COUNT(DISTINCT record_id) AS total_emigrants
                FROM unified_record
                WHERE parish=? AND has_emigration_record=1
                """,
                (row["civil_parish"],),
            ).fetchone()
            text = build_community_summary(
                parish=row["civil_parish"],
                townland_count=int(row["townland_count"] or 0),
                total_emigrants=int((emig_row["total_emigrants"] if emig_row else 0) or 0),
                total_evictions=int(row["total_evictions"] or 0),
                year_range=(
                    int(row["min_year"]),
                    int(row["max_year"]),
                )
                if row["min_year"] and row["max_year"]
                else None,
            )
            chunk_id = f"community:{row['civil_parish'].upper()}"
            chunks.append(
                {
                    "id": chunk_id,
                    "chunk_id": chunk_id,
                    "source_type": "community_summary",
                    "source_table": "townland",
                    "source_record_id": row["civil_parish"],
                    "title": f"{row['civil_parish']} - Community Summary",
                    "text": text,
                    "metadata": {
                        "civil_parish": row["civil_parish"],
                        "townland_count": int(row["townland_count"] or 0),
                    },
                }
            )

    return chunks


def _build_census_chunks(conn) -> list[dict[str, Any]]:
    if not (_table_exists(conn, "census_record") and _table_exists(conn, "townland")):
        return []

    rows = conn.execute(
        """
        SELECT c.id, c.year, c.total, c.male, c.female, c.inhabited, c.uninhabited,
               t.id AS townland_id, t.name, t.civil_parish
        FROM census_record c
        JOIN townland t ON t.id = c.townland_id
        ORDER BY c.year, t.name
        """
    ).fetchall()
    chunks: list[dict[str, Any]] = []
    for row in rows:
        chunk_id = f"census:{row['id']}"
        text = (
            f"Name: {row['name']}\n"
            f"Place: {row['civil_parish'] or row['name']}\n"
            f"Source: Census\n"
            f"Year: {row['year']}\n"
            f"Age: Not recorded\n"
            f"Related fields: Total population {row['total'] or 0}, male {row['male'] or 0}, female {row['female'] or 0}, inhabited houses {row['inhabited'] or 0}, uninhabited houses {row['uninhabited'] or 0}\n"
            f"Record id: census-{row['id']}"
        )
        chunks.append(
            {
                "id": chunk_id,
                "chunk_id": chunk_id,
                "source_type": "census",
                "source_table": "census_record",
                "source_record_id": str(row["id"]),
                "title": f"{row['name']} - Census {row['year']}",
                "text": text,
                "metadata": {
                    "townland_id": row["townland_id"],
                    "name": row["name"],
                    "civil_parish": row["civil_parish"],
                    "year": row["year"],
                },
            }
        )
    return chunks


def _build_workhouse_chunks() -> list[dict[str, Any]]:
    try:
        from backend.services.workhouse_service import get_workhouse
    except Exception as exc:
        log.debug("retrieval_chunks.workhouse_unavailable error=%s", exc)
        return []

    chunks: list[dict[str, Any]] = []
    for idx, row in enumerate(get_workhouse()):
        source_record_id = str(
            row.get("source_record_id")
            or row.get("register_number")
            or f"{row.get('source_sheet', 'sheet')}-{idx + 1}"
        )
        chunk_id = f"workhouse:{source_record_id}"
        related = []
        if row.get("employment"):
            related.append(f"Employment: {row['employment']}")
        if row.get("status"):
            related.append(f"Status: {row['status']}")
        if row.get("religion"):
            related.append(f"Religion: {row['religion']}")
        if row.get("spouse"):
            related.append(f"Spouse: {row['spouse']}")
        if row.get("children_count"):
            related.append(f"Children: {row['children_count']}")
        if row.get("disability"):
            related.append(f"Disability: {row['disability']}")
        related_fields = " | ".join(related) if related else "None recorded"
        text = (
            f"Name: {row.get('raw_name') or 'Unknown'}\n"
            f"Place: {row.get('electoral_division') or 'Unknown'}\n"
            f"Source: Workhouse\n"
            f"Year: {row.get('_year_admitted') or row.get('_year_left') or 'Unknown'}\n"
            f"Age: {row.get('age') or 'Unknown'}\n"
            f"Related fields: {related_fields}\n"
            f"Record id: {source_record_id}"
        )
        chunks.append(
            {
                "id": chunk_id,
                "chunk_id": chunk_id,
                "source_type": "workhouse_record",
                "source_table": "workhouse_excel",
                "source_record_id": source_record_id,
                "title": f"{row.get('raw_name') or 'Unknown'} - Workhouse Record",
                "text": text,
                "metadata": {
                    "source_sheet": row.get("source_sheet"),
                    "electoral_division": row.get("electoral_division"),
                    "sex": row.get("sex"),
                    "age": row.get("age"),
                    "employment": row.get("employment"),
                },
            }
        )
    return chunks


def _build_source_summary_chunks(conn) -> list[dict[str, Any]]:
    table_counts: list[str] = []
    for table in (
        "townland",
        "census_record",
        "clearances_record",
        "unified_record",
        "heritage_feature",
    ):
        if not _table_exists(conn, table):
            continue
        row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
        table_counts.append(f"{table}: {int(row['n'] or 0)} rows")

    if not table_counts:
        return []

    return [
        {
            "id": "summary:source_tables",
            "chunk_id": "summary:source_tables",
            "source_type": "source_summary",
            "source_table": "system",
            "source_record_id": "source_tables",
            "title": "Source Table Summary",
            "text": (
                "Source: Table summary\n"
                "Related fields: " + "; ".join(table_counts)
            ),
            "metadata": {"tables": table_counts},
        }
    ]


def build_retrieval_chunks() -> list[dict[str, Any]]:
    """
    Build the shared Ask-page retrieval corpus.

    The returned docs are intentionally JSON-safe and contain only the fields
    needed by either the in-memory retriever or the persistent pgvector store.
    """
    conn = get_db_conn()
    try:
        chunks: list[dict[str, Any]] = []
        chunks.extend(_build_place_chunks(conn))
        chunks.extend(_build_census_chunks(conn))
        chunks.extend(_build_unified_chunks(conn))
        chunks.extend(_build_workhouse_chunks())
        chunks.extend(_build_source_summary_chunks(conn))
        return chunks
    finally:
        conn.close()
