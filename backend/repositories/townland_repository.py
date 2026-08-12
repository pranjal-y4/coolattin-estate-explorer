from __future__ import annotations

import json
import logging
import uuid
from typing import Optional

from extensions import get_db_conn
from backend.models.census_models import Townland

log = logging.getLogger(__name__)


def find_by_name(name: str) -> Optional[Townland]:
    canonical = name.strip().upper()
    conn = get_db_conn()
    try:
        row = conn.execute(
            "SELECT * FROM townland WHERE name = ?", (canonical,)
        ).fetchone()
        return _row_to_model(row) if row else None
    finally:
        conn.close()


def find_by_entity_id(entity_id: str) -> Optional[Townland]:
    conn = get_db_conn()
    try:
        row = conn.execute(
            "SELECT * FROM townland WHERE entity_id = ?", (entity_id,)
        ).fetchone()
        return _row_to_model(row) if row else None
    finally:
        conn.close()


def find_all() -> list[Townland]:
    conn = get_db_conn()
    try:
        rows = conn.execute("SELECT * FROM townland ORDER BY name").fetchall()
        return [_row_to_model(r) for r in rows]
    finally:
        conn.close()


def find_all_as_dicts() -> list[dict]:
    conn = get_db_conn()
    try:
        rows = conn.execute("SELECT * FROM townland ORDER BY name").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def count() -> int:
    conn = get_db_conn()
    try:
        result = conn.execute("SELECT COUNT(*) FROM townland").fetchone()
        return result[0] if result else 0
    finally:
        conn.close()


def upsert(townland: Townland) -> int:
    canonical   = townland.name.strip().upper()
    images_json = json.dumps(townland.images or [])
    links_json  = json.dumps(townland.links  or [])

    conn = get_db_conn()
    try:
        existing = conn.execute(
            "SELECT id, entity_id FROM townland WHERE name = ?", (canonical,)
        ).fetchone()

        if existing:
            conn.execute(
                """
                UPDATE townland SET
                    entity_id          = COALESCE(entity_id, ?),
                    qualifier          = COALESCE(?, qualifier),
                    logainm_id         = COALESCE(?, logainm_id),
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
                    geometry_flag      = COALESCE(?, geometry_flag),
                    source             = ?,
                    updated_at         = datetime('now')
                WHERE name = ?
                """,
                (
                    townland.entity_id or str(uuid.uuid4()),
                    townland.qualifier,
                    townland.logainm_id,
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
                    images_json, images_json,
                    links_json,  links_json,
                    townland.geometry_flag,
                    townland.source,
                    canonical,
                ),
            )
            conn.commit()
            return existing[0]
        else:
            eid = townland.entity_id or str(uuid.uuid4())
            cursor = conn.execute(
                """
                INSERT INTO townland
                    (entity_id, name, qualifier, logainm_id,
                     name_gaelic, barony, civil_parish, electoral_division,
                     placename_theme, description,
                     td_id, guid, area_sqm,
                     kg_uri, wkt_geometry, centroid_lat, centroid_lon,
                     county, osm_id, osi_id, vrti_id,
                     images_json, links_json, geometry_flag, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    eid,
                    canonical,
                    townland.qualifier,
                    townland.logainm_id,
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
                    townland.geometry_flag,
                    townland.source,
                ),
            )
            conn.commit()
            return cursor.lastrowid
    finally:
        conn.close()


def upsert_many(townlands: list[Townland]) -> int:
    count = 0
    for t in townlands:
        upsert(t)
        count += 1
    log.info("townland_repository.upsert_many | count=%d", count)
    return count


def save_kg_cache(name: str, kg_dto) -> None:
    canonical   = name.strip().upper()
    images_json = json.dumps(kg_dto.images or [])
    links_json  = json.dumps(kg_dto.links  or [])
    conn = get_db_conn()
    try:
        conn.execute(
            """
            UPDATE townland SET
                name_gaelic   = COALESCE(name_gaelic, ?),
                barony        = COALESCE(barony, ?),
                civil_parish  = COALESCE(civil_parish, ?),
                kg_uri        = ?,
                wkt_geometry  = COALESCE(wkt_geometry, ?),
                centroid_lat  = COALESCE(centroid_lat, ?),
                centroid_lon  = COALESCE(centroid_lon, ?),
                county        = ?,
                osm_id        = ?,
                osi_id        = ?,
                vrti_id       = ?,
                images_json   = CASE WHEN ? != '[]' THEN ? ELSE images_json END,
                links_json    = CASE WHEN ? != '[]' THEN ? ELSE links_json END,
                geometry_flag = COALESCE(geometry_flag, ?),
                updated_at    = datetime('now')
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
                getattr(kg_dto, "centroid_flag", None),
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

    def _col(col, default=None):
        return row[col] if col in keys else default

    def _json_list(col):
        val = _col(col)
        if not val:
            return []
        try:
            return json.loads(val)
        except Exception:
            return []

    return Townland(
        id=row["id"],
        entity_id=_col("entity_id"),
        name=row["name"],
        qualifier=_col("qualifier"),
        logainm_id=_col("logainm_id"),
        name_gaelic=row["name_gaelic"],
        barony=row["barony"],
        civil_parish=row["civil_parish"],
        electoral_division=row["electoral_division"],
        placename_theme=row["placename_theme"],
        description=row["description"],
        td_id=_col("td_id"),
        guid=_col("guid"),
        area_sqm=_col("area_sqm"),
        kg_uri=row["kg_uri"],
        wkt_geometry=row["wkt_geometry"],
        centroid_lat=row["centroid_lat"],
        centroid_lon=row["centroid_lon"],
        county=_col("county"),
        osm_id=_col("osm_id"),
        osi_id=_col("osi_id"),
        vrti_id=_col("vrti_id"),
        images=_json_list("images_json"),
        links=_json_list("links_json"),
        geometry_flag=_col("geometry_flag"),
        source=row["source"],
        created_at=_col("created_at"),
        updated_at=_col("updated_at"),
    )
