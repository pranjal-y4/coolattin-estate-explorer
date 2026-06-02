"""
coolattin/repositories/match_review_repository.py

Database access for the resolution-engine support tables:
  - match_review   — uncertain candidate pairs awaiting human decision
  - townland_xref  — confirmed (source, source_record_id) → entity_id mappings
  - field_provenance — which source contributed each field value and why

No SQL lives outside this module for these tables.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from extensions import get_db_conn

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# match_review
# ---------------------------------------------------------------------------

def enqueue(
    townland_id_a: int,
    townland_id_b: int,
    score: float,
    features: dict,
) -> int:
    """
    Write a candidate pair to match_review with status 'pending'.
    Returns the new row id.
    """
    conn = get_db_conn()
    try:
        cursor = conn.execute(
            """
            INSERT INTO match_review
                (townland_id_a, townland_id_b, score, score_breakdown, status)
            VALUES (?, ?, ?, ?, 'pending')
            """,
            (townland_id_a, townland_id_b, score, json.dumps(features)),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_pending() -> list[dict]:
    """Return all pending match_review rows as dicts."""
    conn = get_db_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM match_review WHERE status = 'pending' ORDER BY score DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def apply_decision(match_id: int, decision: str, note: str = "") -> None:
    """
    Record a reviewer decision on a match_review entry.

    decision: 'confirmed' | 'rejected'

    For a 'confirmed' decision, also creates a townland_xref entry linking
    both records under the same entity_id, seeding future ingest runs.
    """
    valid = {"confirmed", "rejected"}
    if decision not in valid:
        raise ValueError(f"decision must be one of {valid}, got {decision!r}")

    now = datetime.now(timezone.utc).isoformat()
    conn = get_db_conn()
    try:
        conn.execute(
            """
            UPDATE match_review
               SET status = ?, reviewer_note = ?, reviewed_at = ?
             WHERE id = ?
            """,
            (decision, note, now, match_id),
        )

        if decision == "confirmed":
            row = conn.execute(
                "SELECT townland_id_a, townland_id_b FROM match_review WHERE id = ?",
                (match_id,),
            ).fetchone()
            if row:
                _link_confirmed_pair(conn, row["townland_id_a"], row["townland_id_b"])

        conn.commit()
        log.info(
            "match_review_repository.apply_decision | id=%d decision=%s",
            match_id, decision,
        )
    finally:
        conn.close()


def _link_confirmed_pair(conn, id_a: int, id_b: int) -> None:
    """
    Assign the same entity_id to both townland rows and write xref entries.
    Uses townland A's entity_id as the canonical one.
    """
    rows = conn.execute(
        "SELECT id, entity_id, td_id, kg_uri, source FROM townland WHERE id IN (?, ?)",
        (id_a, id_b),
    ).fetchall()
    if len(rows) != 2:
        return

    by_id = {r["id"]: r for r in rows}
    ra, rb = by_id[id_a], by_id[id_b]

    # Canonical entity_id = A's (or generate one if missing)
    canonical_eid = ra["entity_id"] or rb["entity_id"] or _new_uuid()

    for r in (ra, rb):
        if r["entity_id"] != canonical_eid:
            conn.execute(
                "UPDATE townland SET entity_id = ? WHERE id = ?",
                (canonical_eid, r["id"]),
            )
        src_id = r["td_id"] or r["kg_uri"] or str(r["id"])
        src    = r["source"] or "unknown"
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO townland_xref
                    (entity_id, source, source_record_id, confidence, match_method)
                VALUES (?, ?, ?, 1.0, 'manual_confirmed')
                """,
                (canonical_eid, src, src_id),
            )
        except Exception as exc:
            log.debug("match_review_repository._link xref insert skipped | %s", exc)


# ---------------------------------------------------------------------------
# townland_xref
# ---------------------------------------------------------------------------

def add_xref(
    entity_id: str,
    source: str,
    source_record_id: str,
    confidence: float = 1.0,
    match_method: str = "exact_id",
) -> None:
    """Insert a cross-reference entry; ignores duplicates."""
    conn = get_db_conn()
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO townland_xref
                (entity_id, source, source_record_id, confidence, match_method)
            VALUES (?, ?, ?, ?, ?)
            """,
            (entity_id, source, source_record_id, confidence, match_method),
        )
        conn.commit()
    finally:
        conn.close()


def get_xrefs_for_entity(entity_id: str) -> list[dict]:
    """Return all xref rows for a given entity_id."""
    conn = get_db_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM townland_xref WHERE entity_id = ?", (entity_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# field_provenance
# ---------------------------------------------------------------------------

def record_provenance(
    entity_id: str,
    field_name: str,
    field_value: Optional[str],
    source: str,
    source_record_id: Optional[str] = None,
    rule: Optional[str] = None,
) -> None:
    """
    Record which source contributed a field value and why.
    Updates the existing row if one exists for (entity_id, field_name).
    """
    conn = get_db_conn()
    try:
        conn.execute(
            """
            INSERT INTO field_provenance
                (entity_id, field_name, field_value, source, source_record_id, rule)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(entity_id, field_name) DO UPDATE SET
                field_value      = excluded.field_value,
                source           = excluded.source,
                source_record_id = excluded.source_record_id,
                rule             = excluded.rule
            """,
            (entity_id, field_name, str(field_value) if field_value is not None else None,
             source, source_record_id, rule),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Quality report
# ---------------------------------------------------------------------------

def quality_summary() -> dict:
    """
    Return a data-quality summary dict.

    Covers: total townlands, unmatched (no kg_uri), geometry flags,
    missing centroids, match_review status breakdown, same-name-different-
    geometry collisions.
    """
    conn = get_db_conn()
    try:
        total = conn.execute("SELECT COUNT(*) FROM townland").fetchone()[0]

        unmatched = conn.execute(
            "SELECT COUNT(*) FROM townland WHERE kg_uri IS NULL"
        ).fetchone()[0]

        geom_flagged = conn.execute(
            "SELECT COUNT(*) FROM townland WHERE geometry_flag IS NOT NULL"
        ).fetchone()[0]

        no_centroid = conn.execute(
            "SELECT COUNT(*) FROM townland "
            "WHERE centroid_lat IS NULL OR centroid_lon IS NULL"
        ).fetchone()[0]

        review_rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM match_review GROUP BY status"
        ).fetchall()
        review_by_status = {r["status"]: r["n"] for r in review_rows}

        # Same canonical name appearing in 2+ rows with non-null wkt_geometry
        # indicates potential same-name-different-geometry collision
        collisions = conn.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT name FROM townland
                WHERE wkt_geometry IS NOT NULL
                GROUP BY name HAVING COUNT(*) > 1
            )
            """
        ).fetchone()[0]

        return {
            "total_townlands":         total,
            "unmatched_no_kg_uri":     unmatched,
            "geometry_flagged":        geom_flagged,
            "missing_centroid":        no_centroid,
            "same_name_collisions":    collisions,
            "match_review":            review_by_status,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _new_uuid() -> str:
    import uuid
    return str(uuid.uuid4())
