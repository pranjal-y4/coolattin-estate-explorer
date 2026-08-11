"""
coolattin/repositories/townland_repository.py

Local database access for townland records.

This module is the ONLY place that reads/writes the `townland` table.
It does not know about SPARQL, HTTP, or business rules.
"""
from __future__ import annotations

import json
import logging
import uuid
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


def find_by_entity_id(entity_id: str) -> Optional[Townland]:
    """Return a townland by its UUID entity_id, or None."""
    conn = get_db_conn()
    try:
        row = conn.execute(
            "SELECT * FROM townland WHERE entity_id = ?", (entity_id,)
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


def find_all_as_dicts() -> list[dict]:
    """Return all townlands as plain dicts (for the resolution engine)."""
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


def watermark() -> tuple:
    """(row count, latest update timestamp) — used for cache invalidation."""
    conn = get_db_conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n, MAX(updated_at) AS ts FROM townland"
        ).fetchone()
        return (row["n"], row["ts"])
    finally:
        conn.close()


def upsert(townland: Townland) -> int:
    """
    Insert or update a townland record.

    On insert, assigns a UUID entity_id if the model has none.
    Returns the rowid of the inserted/updated row.
    """
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
    """Upsert a batch of townlands. Returns count of processed rows."""
    count = 0
    for t in townlands:
        upsert(t)
        count += 1
    log.info("townland_repository.upsert_many | count=%d", count)
    return count


def save_kg_cache(name: str, kg_dto) -> None:
    """
    Write KG-fetched fields back to the townland table.
    Only updates KG-sourced columns — never overwrites GeoJSON measurements.
    """
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
    """Returns (townland_id, created).  Creates the townland if it doesn't exist."""
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


# ---------------------------------------------------------------------------
# Entity-resolution support
#
# These take an optional open connection so one source record can be resolved
# inside a single transaction (candidate lookup → canonical write → xref).
# ---------------------------------------------------------------------------

# Authority identifier columns, in descending order of reliability.
AUTHORITY_ID_COLUMNS: tuple[str, ...] = (
    "kg_uri", "vrti_id", "osi_id", "osm_id", "logainm_id", "td_id", "guid",
)

_MUTABLE_COLUMNS: frozenset[str] = frozenset({
    "qualifier", "logainm_id", "name_gaelic", "barony", "civil_parish",
    "electoral_division", "placename_theme", "description", "td_id", "guid",
    "area_sqm", "kg_uri", "wkt_geometry", "centroid_lat", "centroid_lon",
    "county", "osm_id", "osi_id", "vrti_id", "geometry_flag",
})


def _with_conn(conn):
    """Return (conn, should_close) so callers can pass an open transaction."""
    return (conn, False) if conn is not None else (get_db_conn(), True)


def find_row_by_name(name_upper: str, conn=None) -> Optional[dict]:
    """Exact canonical-name lookup. Returns a plain dict or None."""
    c, close = _with_conn(conn)
    try:
        row = c.execute(
            "SELECT * FROM townland WHERE UPPER(name) = ? ORDER BY id LIMIT 1",
            (name_upper.strip().upper(),),
        ).fetchone()
        return dict(row) if row else None
    finally:
        if close:
            c.close()


def find_row_by_id(townland_id: int, conn=None) -> Optional[dict]:
    """Return a canonical townland row as a plain dict, or None."""
    c, close = _with_conn(conn)
    try:
        row = c.execute("SELECT * FROM townland WHERE id = ?", (townland_id,)).fetchone()
        return dict(row) if row else None
    finally:
        if close:
            c.close()


def find_row_by_entity_id(entity_id: str, conn=None) -> Optional[dict]:
    """Return the canonical townland row carrying an entity_id, or None."""
    c, close = _with_conn(conn)
    try:
        row = c.execute(
            "SELECT * FROM townland WHERE entity_id = ? ORDER BY id LIMIT 1", (entity_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        if close:
            c.close()


def assign_entity_id(townland_id: int, conn=None) -> str:
    """Give a legacy row without an entity_id a stable surrogate key."""
    entity_id = str(uuid.uuid4())
    c, close = _with_conn(conn)
    try:
        c.execute(
            "UPDATE townland SET entity_id = ? WHERE id = ?", (entity_id, townland_id)
        )
        if close:
            c.commit()
        return entity_id
    finally:
        if close:
            c.close()


def find_resolved_without_geometry(limit: int = 0, conn=None) -> list[dict]:
    """
    Canonical townlands that a source record resolved to but which have no
    boundary geometry — the ones the map cannot draw yet.
    """
    c, close = _with_conn(conn)
    try:
        sql = """
            SELECT DISTINCT t.* FROM townland t
              JOIN townland_xref x ON x.entity_id = t.entity_id
             WHERE t.wkt_geometry IS NULL
             ORDER BY t.name
        """
        params: tuple = ()
        if limit and limit > 0:
            sql += " LIMIT ?"
            params = (limit,)
        return [dict(r) for r in c.execute(sql, params).fetchall()]
    finally:
        if close:
            c.close()


def find_rows_by_authority_id(column: str, value: str, conn=None) -> list[dict]:
    """Look up canonical townlands sharing an authority identifier."""
    if column not in AUTHORITY_ID_COLUMNS or not value:
        return []
    c, close = _with_conn(conn)
    try:
        rows = c.execute(
            f"SELECT * FROM townland WHERE {column} = ? ORDER BY id",  # noqa: S608 — column is allow-listed
            (value,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        if close:
            c.close()


def find_block_candidates(
    name_upper: str,
    county: Optional[str] = None,
    limit: int = 200,
    conn=None,
) -> list[dict]:
    """
    Return bounded, deterministic candidate rows for a source name.

    Blocking key mirrors townland_service._block_key: same county (when known)
    and the same first three characters of the normalised name.
    """
    prefix = (name_upper or "").strip().upper()[:3]
    if not prefix:
        return []
    c, close = _with_conn(conn)
    try:
        if county:
            rows = c.execute(
                """
                SELECT * FROM townland
                 WHERE UPPER(SUBSTR(name, 1, 3)) = ?
                   AND (county IS NULL OR UPPER(county) = ?)
                 ORDER BY id LIMIT ?
                """,
                (prefix, county.strip().upper(), limit),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM townland WHERE UPPER(SUBSTR(name, 1, 3)) = ? ORDER BY id LIMIT ?",
                (prefix, limit),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        if close:
            c.close()


def enrich_row(townland_id: int, fields: dict, conn=None) -> list[str]:
    """
    Fill NULL columns on an existing canonical townland.

    Never overwrites a populated value and never touches `name` — the canonical
    representation stays stable regardless of import order.  Returns the names
    of the columns this call actually filled.
    """
    updates = {
        k: v for k, v in fields.items()
        if k in _MUTABLE_COLUMNS and v is not None and v != ""
    }
    if not updates:
        return []

    c, close = _with_conn(conn)
    try:
        current = c.execute(
            "SELECT * FROM townland WHERE id = ?", (townland_id,)
        ).fetchone()
        if current is None:
            return []
        filled = [k for k in updates if current[k] is None or current[k] == ""]
        if not filled:
            return []
        assignments = ", ".join(f"{k} = ?" for k in filled)
        c.execute(
            f"UPDATE townland SET {assignments}, updated_at = datetime('now') WHERE id = ?",  # noqa: S608 — keys are allow-listed
            [updates[k] for k in filled] + [townland_id],
        )
        if close:
            c.commit()
        return filled
    finally:
        if close:
            c.close()


def insert_canonical(fields: dict, conn=None) -> tuple[int, str]:
    """
    Create a new canonical townland row. Returns (townland_id, entity_id).

    Only the fields supplied are written — missing values stay NULL rather than
    being manufactured.
    """
    name = (fields.get("name") or "").strip().upper()
    if not name:
        raise ValueError("insert_canonical requires a name")

    entity_id = fields.get("entity_id") or str(uuid.uuid4())
    cols = ["entity_id", "name", "source"]
    vals = [entity_id, name, fields.get("source") or "manual"]
    for col in sorted(_MUTABLE_COLUMNS):
        val = fields.get(col)
        if val is not None and val != "":
            cols.append(col)
            vals.append(val)

    placeholders = ", ".join("?" for _ in cols)
    c, close = _with_conn(conn)
    try:
        cursor = c.execute(
            f"INSERT INTO townland ({', '.join(cols)}) VALUES ({placeholders})",  # noqa: S608 — columns are allow-listed
            vals,
        )
        if close:
            c.commit()
        return int(cursor.lastrowid), entity_id
    finally:
        if close:
            c.close()


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
