"""
coolattin/repositories/townland_repository.py

Local database access for townland records.

This module is the ONLY place that reads/writes the `townland` table.
It does not know about SPARQL, HTTP, or business rules.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from extensions import get_db_conn
from backend.models.census_models import Townland

log = logging.getLogger(__name__)


def find_by_name(name: str) -> Optional[Townland]:
    """Return a townland by its canonical name, or None."""
    canonical = name.strip().upper()
    conn = get_db_conn()
    try:
        row = conn.execute(
            "SELECT * FROM townland WHERE name = ?", (canonical,)
        ).fetchone()
        return _row_to_model(row) if row else None
    finally:
        conn.close()


def find_all() -> list[Townland]:
    """Return all townlands ordered by name."""
    conn = get_db_conn()
    try:
        rows = conn.execute("SELECT * FROM townland ORDER BY name").fetchall()
        return [_row_to_model(r) for r in rows]
    finally:
        conn.close()


def count() -> int:
    """Return total number of townlands in local DB."""
    conn = get_db_conn()
    try:
        result = conn.execute("SELECT COUNT(*) FROM townland").fetchone()
        return result[0] if result else 0
    finally:
        conn.close()


def upsert(townland: Townland) -> int:
    """
    Insert or update a townland record.
    Returns the rowid of the inserted/updated row.
    """
    canonical = townland.name.strip().upper()
    images_json = json.dumps(townland.images or [])
    links_json  = json.dumps(townland.links or [])
    conn = get_db_conn()
    try:
        existing = conn.execute(
            "SELECT id FROM townland WHERE name = ?", (canonical,)
        ).fetchone()

        if existing:
            conn.execute(
                """
                UPDATE townland SET
                    name_gaelic        = COALESCE(?, name_gaelic),
                    barony             = COALESCE(?, barony),
                    civil_parish       = COALESCE(?, civil_parish),
                    electoral_division = COALESCE(?, electoral_division),
                    placename_theme    = COALESCE(?, placename_theme),
                    description        = COALESCE(?, description),
                    td_id              = COALESCE(?, td_id),
                    guid               = COALESCE(?, guid),
                    area_sqm           = COALESCE(?, area_sqm),
                    kg_uri             = COALESCE(?, kg_uri),
                    wkt_geometry       = COALESCE(?, wkt_geometry),
                    centroid_lat       = COALESCE(?, centroid_lat),
                    centroid_lon       = COALESCE(?, centroid_lon),
                    county             = COALESCE(?, county),
                    osm_id             = COALESCE(?, osm_id),
                    osi_id             = COALESCE(?, osi_id),
                    vrti_id            = COALESCE(?, vrti_id),
                    images_json        = CASE WHEN ? != '[]' THEN ? ELSE images_json END,
                    links_json         = CASE WHEN ? != '[]' THEN ? ELSE links_json END,
                    source             = ?,
                    updated_at         = datetime('now')
                WHERE name = ?
                """,
                (
                    townland.name_gaelic,
                    townland.barony,
                    townland.civil_parish,
                    townland.electoral_division,
                    townland.placename_theme,
                    townland.description,
                    townland.td_id,
                    townland.guid,
                    townland.area_sqm,
                    townland.kg_uri,
                    townland.wkt_geometry,
                    townland.centroid_lat,
                    townland.centroid_lon,
                    townland.county,
                    townland.osm_id,
                    townland.osi_id,
                    townland.vrti_id,
                    images_json, images_json,   # CASE WHEN check + value
                    links_json,  links_json,
                    townland.source,
                    canonical,
                ),
            )
            conn.commit()
            return existing[0]
        else:
            cursor = conn.execute(
                """
                INSERT INTO townland
                    (name, name_gaelic, barony, civil_parish, electoral_division,
                     placename_theme, description,
                     td_id, guid, area_sqm,
                     kg_uri, wkt_geometry, centroid_lat, centroid_lon,
                     county, osm_id, osi_id, vrti_id,
                     images_json, links_json, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    canonical,
                    townland.name_gaelic,
                    townland.barony,
                    townland.civil_parish,
                    townland.electoral_division,
                    townland.placename_theme,
                    townland.description,
                    townland.td_id,
                    townland.guid,
                    townland.area_sqm,
                    townland.kg_uri,
                    townland.wkt_geometry,
                    townland.centroid_lat,
                    townland.centroid_lon,
                    townland.county,
                    townland.osm_id,
                    townland.osi_id,
                    townland.vrti_id,
                    images_json,
                    links_json,
                    townland.source,
                ),
            )
            conn.commit()
            return cursor.lastrowid
    finally:
        conn.close()


def upsert_many(townlands: list[Townland]) -> int:
    """Upsert a batch of townlands. Returns count of processed rows."""
    count = 0
    for t in townlands:
        upsert(t)
        count += 1
    log.info("townland_repository.upsert_many | count=%d", count)
    return count


def save_kg_cache(name: str, kg_dto) -> None:
    """
    Write KG-fetched fields back to the townland table so they are
    available as a cache when the KG endpoint is offline.

    Called by census_service after every successful KG enrichment.
    Only updates KG-sourced columns — never overwrites GeoJSON measurements.
    """
    canonical = name.strip().upper()
    images_json = json.dumps(kg_dto.images or [])
    links_json  = json.dumps(kg_dto.links or [])
    conn = get_db_conn()
    try:
        conn.execute(
            """
            UPDATE townland SET
                name_gaelic  = COALESCE(name_gaelic, ?),
                barony       = COALESCE(barony, ?),
                civil_parish = COALESCE(civil_parish, ?),
                kg_uri       = ?,
                wkt_geometry = COALESCE(wkt_geometry, ?),
                centroid_lat = COALESCE(centroid_lat, ?),
                centroid_lon = COALESCE(centroid_lon, ?),
                county       = ?,
                osm_id       = ?,
                osi_id       = ?,
                vrti_id      = ?,
                images_json  = CASE WHEN ? != '[]' THEN ? ELSE images_json END,
                links_json   = CASE WHEN ? != '[]' THEN ? ELSE links_json END,
                updated_at   = datetime('now')
            WHERE name = ?
            """,
            (
                kg_dto.name_gaelic,
                kg_dto.barony,
                kg_dto.civil_parish,
                kg_dto.uri,
                kg_dto.wkt_geometry,
                kg_dto.centroid_lat,
                kg_dto.centroid_lon,
                kg_dto.county,
                kg_dto.osm_id,
                kg_dto.osi_id,
                kg_dto.vrti_id,
                images_json, images_json,
                links_json,  links_json,
                canonical,
            ),
        )
        conn.commit()
        log.debug("townland_repository.save_kg_cache | name=%s", canonical)
    except Exception as exc:
        log.warning("townland_repository.save_kg_cache failed name=%s err=%s", canonical, exc)
    finally:
        conn.close()


def get_or_create(name: str, **kwargs) -> tuple[int, bool]:
    """
    Returns (townland_id, created).
    Creates the townland if it doesn't exist.
    """
    canonical = name.strip().upper()
    conn = get_db_conn()
    try:
        existing = conn.execute(
            "SELECT id FROM townland WHERE name = ?", (canonical,)
        ).fetchone()
        if existing:
            return existing[0], False
    finally:
        conn.close()

    t = Townland(name=canonical, **kwargs)
    rowid = upsert(t)
    return rowid, True


def _row_to_model(row) -> Townland:
    keys = row.keys()

    def _json_list(col):
        val = row[col] if col in keys else None
        if not val:
            return []
        try:
            return json.loads(val)
        except Exception:
            return []

    return Townland(
        id=row["id"],
        name=row["name"],
        name_gaelic=row["name_gaelic"],
        barony=row["barony"],
        civil_parish=row["civil_parish"],
        electoral_division=row["electoral_division"],
        placename_theme=row["placename_theme"],
        description=row["description"],
        td_id=row["td_id"] if "td_id" in keys else None,
        guid=row["guid"] if "guid" in keys else None,
        area_sqm=row["area_sqm"] if "area_sqm" in keys else None,
        kg_uri=row["kg_uri"],
        wkt_geometry=row["wkt_geometry"],
        centroid_lat=row["centroid_lat"],
        centroid_lon=row["centroid_lon"],
        county=row["county"] if "county" in keys else None,
        osm_id=row["osm_id"] if "osm_id" in keys else None,
        osi_id=row["osi_id"] if "osi_id" in keys else None,
        vrti_id=row["vrti_id"] if "vrti_id" in keys else None,
        images=_json_list("images_json"),
        links=_json_list("links_json"),
        source=row["source"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
