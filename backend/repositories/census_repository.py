from __future__ import annotations

import json
import logging
from typing import Optional

from backend.extensions import get_db_conn
from backend.models.census_models import CensusFilters, CensusRecord
from backend.repositories import townland_repository

log = logging.getLogger(__name__)


def find(filters: CensusFilters) -> list[CensusRecord]:
    conditions = []
    params: list = []

    if filters.year:
        conditions.append("cr.year = ?")
        params.append(filters.year)

    if filters.townland:
        canonical = filters.townland.strip().upper()
        conditions.append("t.name = ?")
        params.append(canonical)

    if filters.barony:
        conditions.append("t.barony LIKE ?")
        params.append(f"%{filters.barony}%")

    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    offset = (filters.page - 1) * filters.limit

    sql = f"""
        SELECT cr.*, t.name AS townland_name,
               t.name_gaelic, t.barony, t.civil_parish,
               t.electoral_division, t.description, t.placename_theme
        FROM census_record cr
        JOIN townland t ON t.id = cr.townland_id
        {where}
        ORDER BY t.name, cr.year
        LIMIT ? OFFSET ?
    """
    params += [filters.limit, offset]

    conn = get_db_conn()
    try:
        rows = conn.execute(sql, params).fetchall()
        return [_row_to_model(r) for r in rows]
    finally:
        conn.close()


def find_townland_detail(townland_name: str) -> Optional[dict]:
    canonical = townland_name.strip().upper()
    conn = get_db_conn()
    try:
        t_row = conn.execute(
            "SELECT * FROM townland WHERE name = ?", (canonical,)
        ).fetchone()

        if not t_row:
            t_row = conn.execute(
                "SELECT * FROM townland WHERE name LIKE ? ORDER BY name LIMIT 1",
                (canonical + " %",),
            ).fetchone()

        if not t_row:
            return None

        census_rows = conn.execute(
            "SELECT * FROM census_record WHERE townland_id = ? ORDER BY year",
            (t_row["id"],),
        ).fetchall()

        census_by_year = {}
        for r in census_rows:
            census_by_year[r["year"]] = {
                "male": r["male"],
                "female": r["female"],
                "total": r["total"],
                "inhabited": r["inhabited"],
                "uninhabited": r["uninhabited"],
                "source": r["source"],
            }

        def _json_list(val):
            if not val:
                return []
            try:
                return json.loads(val)
            except Exception:
                return []

        return {
            "townland":          t_row["name"],
            "gaelic_name":       t_row["name_gaelic"],
            "description":       t_row["description"],
            "placename_theme":   t_row["placename_theme"],
            "barony":            t_row["barony"],
            "civil_parish":      t_row["civil_parish"],
            "electoral_division": t_row["electoral_division"],
            "kg_uri":            t_row["kg_uri"],
            "uri":               t_row["kg_uri"],
            "centroid_lat":      t_row["centroid_lat"],
            "centroid_lon":      t_row["centroid_lon"],
            "boundary_wkt":      t_row["wkt_geometry"],
            "county":            t_row["county"] if "county" in t_row.keys() else None,
            "osm_id":            t_row["osm_id"]  if "osm_id"  in t_row.keys() else None,
            "osi_id":            t_row["osi_id"]  if "osi_id"  in t_row.keys() else None,
            "vrti_id":           t_row["vrti_id"] if "vrti_id" in t_row.keys() else None,
            "images":            _json_list(t_row["images_json"] if "images_json" in t_row.keys() else None),
            "links":             _json_list(t_row["links_json"]  if "links_json"  in t_row.keys() else None),
            "census":            census_by_year,
        }
    finally:
        conn.close()


def get_summary(year: Optional[int] = None) -> dict:
    params: list = []
    year_clause = ""
    if year:
        year_clause = "WHERE year = ?"
        params.append(year)

    conn = get_db_conn()
    try:
        row = conn.execute(
            f"""
            SELECT
                COUNT(*) AS rows,
                COUNT(DISTINCT townland_id) AS townlands,
                COALESCE(SUM(total), 0) AS population_total,
                COALESCE(SUM(male), 0) AS male_total,
                COALESCE(SUM(female), 0) AS female_total,
                COALESCE(SUM(inhabited), 0) AS inhabited_total,
                COALESCE(SUM(uninhabited), 0) AS uninhabited_total
            FROM census_record
            {year_clause}
            """,
            params,
        ).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def get_townland_names() -> list[str]:
    conn = get_db_conn()
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT t.name
            FROM townland t
            JOIN census_record cr ON cr.townland_id = t.id
            ORDER BY t.name
            """
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def count_records(year: Optional[int] = None) -> int:
    conn = get_db_conn()
    try:
        if year:
            result = conn.execute(
                "SELECT COUNT(*) FROM census_record WHERE year = ?", (year,)
            ).fetchone()
        else:
            result = conn.execute("SELECT COUNT(*) FROM census_record").fetchone()
        return result[0] if result else 0
    finally:
        conn.close()


def upsert_many(records: list[CensusRecord]) -> int:
    count = 0
    for rec in records:
        townland_id, _ = townland_repository.get_or_create(
            rec.townland_name,
            kg_uri=rec.kg_uri,
            source=rec.source,
        )

        total = rec.total
        if total is None:
            total = (rec.male or 0) + (rec.female or 0)

        conn = get_db_conn()
        try:
            existing = conn.execute(
                "SELECT id FROM census_record WHERE townland_id = ? AND year = ?",
                (townland_id, rec.year),
            ).fetchone()

            if existing:
                conn.execute(
                    """
                    UPDATE census_record SET
                        male           = ?,
                        female         = ?,
                        total          = ?,
                        inhabited      = ?,
                        uninhabited    = ?,
                        source         = ?,
                        kg_uri         = ?,
                        last_synced_at = datetime('now')
                    WHERE townland_id = ? AND year = ?
                    """,
                    (
                        rec.male, rec.female, total,
                        rec.inhabited, rec.uninhabited,
                        rec.source, rec.kg_uri,
                        townland_id, rec.year,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO census_record
                        (townland_id, year, male, female, total,
                         inhabited, uninhabited, source, kg_uri)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        townland_id, rec.year,
                        rec.male, rec.female, total,
                        rec.inhabited, rec.uninhabited,
                        rec.source, rec.kg_uri,
                    ),
                )
            conn.commit()
            count += 1
        finally:
            conn.close()

    log.info("census_repository.upsert_many | count=%d", count)
    return count


def _row_to_model(row) -> CensusRecord:
    return CensusRecord(
        id=row["id"],
        townland_id=row["townland_id"],
        townland_name=row["townland_name"],
        year=row["year"],
        male=row["male"],
        female=row["female"],
        total=row["total"],
        inhabited=row["inhabited"],
        uninhabited=row["uninhabited"],
        source=row["source"],
        kg_uri=row["kg_uri"],
        last_synced_at=row["last_synced_at"],
    )
