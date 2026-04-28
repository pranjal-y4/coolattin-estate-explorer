"""
coolattin/repositories/clearances_repository.py

Local database access for clearances records.

This module is the ONLY place that reads/writes the `clearances_record` table.
It does not know about SPARQL, HTTP, or business rules.

Source: estate GeoJSON (Clearances_1847 … Clearances_1856).
"""
from __future__ import annotations

import logging
from typing import Optional

from extensions import get_db_conn
from backend.models.census_models import ClearancesRecord
from backend.repositories import townland_repository

log = logging.getLogger(__name__)


def find_by_townland(townland_name: str) -> list[ClearancesRecord]:
    """Return all clearances records for a townland, ordered by year."""
    canonical = townland_name.strip().upper()
    conn = get_db_conn()
    try:
        rows = conn.execute(
            """
            SELECT cr.*, t.name AS townland_name
            FROM clearances_record cr
            JOIN townland t ON t.id = cr.townland_id
            WHERE t.name = ?
            ORDER BY cr.year
            """,
            (canonical,),
        ).fetchall()
        return [_row_to_model(r) for r in rows]
    finally:
        conn.close()


def find_all() -> list[ClearancesRecord]:
    """Return all clearances records ordered by townland name and year."""
    conn = get_db_conn()
    try:
        rows = conn.execute(
            """
            SELECT cr.*, t.name AS townland_name
            FROM clearances_record cr
            JOIN townland t ON t.id = cr.townland_id
            ORDER BY t.name, cr.year
            """
        ).fetchall()
        return [_row_to_model(r) for r in rows]
    finally:
        conn.close()


def get_summary_by_townland() -> list[dict]:
    """Return total clearances per townland (for map/chart display)."""
    conn = get_db_conn()
    try:
        rows = conn.execute(
            """
            SELECT t.name AS townland,
                   SUM(cr.count) AS total_clearances,
                   COUNT(cr.year) AS years_recorded
            FROM clearances_record cr
            JOIN townland t ON t.id = cr.townland_id
            WHERE cr.count IS NOT NULL
            GROUP BY t.id
            ORDER BY total_clearances DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def count_records() -> int:
    conn = get_db_conn()
    try:
        result = conn.execute("SELECT COUNT(*) FROM clearances_record").fetchone()
        return result[0] if result else 0
    finally:
        conn.close()


def upsert_many(records: list[ClearancesRecord]) -> int:
    """
    Insert or update clearances records.
    Townland rows must already exist (created by townland_repository.upsert).
    Returns count of upserted records.
    """
    count = 0
    for rec in records:
        townland_id, _ = townland_repository.get_or_create(
            rec.townland_name,
            source=rec.source,
        )

        conn = get_db_conn()
        try:
            existing = conn.execute(
                "SELECT id FROM clearances_record WHERE townland_id = ? AND year = ?",
                (townland_id, rec.year),
            ).fetchone()

            if existing:
                conn.execute(
                    """
                    UPDATE clearances_record SET
                        count  = ?,
                        source = ?
                    WHERE townland_id = ? AND year = ?
                    """,
                    (rec.count, rec.source, townland_id, rec.year),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO clearances_record (townland_id, year, count, source)
                    VALUES (?, ?, ?, ?)
                    """,
                    (townland_id, rec.year, rec.count, rec.source),
                )
            conn.commit()
            count += 1
        finally:
            conn.close()

    log.info("clearances_repository.upsert_many | count=%d", count)
    return count


def _row_to_model(row) -> ClearancesRecord:
    return ClearancesRecord(
        id=row["id"],
        townland_id=row["townland_id"],
        townland_name=row["townland_name"],
        year=row["year"],
        count=row["count"],
        source=row["source"],
    )
