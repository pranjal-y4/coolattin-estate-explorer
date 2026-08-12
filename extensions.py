from __future__ import annotations

import logging
import sqlite3
import uuid
from pathlib import Path

log = logging.getLogger(__name__)

_DB_PATH: Path | None = None


def init_db(db_path: Path) -> None:
    global _DB_PATH
    _DB_PATH = db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    log.info("extensions.db_path_set | path=%s", str(db_path))


def get_db_conn() -> sqlite3.Connection:
    if _DB_PATH is None:
        raise RuntimeError("Database not initialised — call init_db() first via create_app()")
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-65536")
    conn.execute("PRAGMA temp_store=2")
    conn.execute("PRAGMA mmap_size=268435456")
    return conn


_TOWNLAND_CREATE = """
CREATE TABLE townland (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id           TEXT,                   -- UUID surrogate key
    name                TEXT NOT NULL,          -- canonical UPPERCASE English name
    qualifier           TEXT,                   -- locational qualifier: UPPER/LOWER/etc.
    logainm_id          TEXT,                   -- logainm.ie place identifier
    name_gaelic         TEXT,
    barony              TEXT,
    civil_parish        TEXT,
    electoral_division  TEXT,
    placename_theme     TEXT,
    description         TEXT,
    td_id               TEXT,
    guid                TEXT,
    area_sqm            REAL,
    kg_uri              TEXT,
    wkt_geometry        TEXT,
    centroid_lat        REAL,
    centroid_lon        REAL,
    county              TEXT,
    osm_id              TEXT,
    osi_id              TEXT,
    vrti_id             TEXT,
    images_json         TEXT DEFAULT '[]',
    links_json          TEXT DEFAULT '[]',
    geometry_flag       TEXT,                   -- quality flags from geometry validation
    source              TEXT DEFAULT 'json',
    created_at          TEXT DEFAULT (datetime('now')),
    updated_at          TEXT DEFAULT (datetime('now'))
)
"""

_V2_MIGRATION_COLS: list[tuple[str, str]] = [
    ("entity_id",      "TEXT"),
    ("qualifier",      "TEXT"),
    ("logainm_id",     "TEXT"),
    ("geometry_flag",  "TEXT"),
]

_SUPPORT_TABLES = """
-- Cross-reference: maps (source, source_record_id) → entity_id.
-- Allows one estate townland to map to multiple modern units and
-- same-named distinct townlands to remain separate entities.
CREATE TABLE IF NOT EXISTS townland_xref (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id        TEXT NOT NULL,
    source           TEXT NOT NULL,          -- 'geojson' | 'kg' | 'reference' | 'manual'
    source_record_id TEXT NOT NULL,          -- TD_ID, kg_uri, townlands.ie URL, etc.
    confidence       REAL,                   -- match confidence 0..1
    match_method     TEXT,                   -- 'exact_id' | 'name_geo' | 'manual'
    created_at       TEXT DEFAULT (datetime('now')),
    UNIQUE(source, source_record_id)
);

-- Uncertain pairs waiting for human review.  A reviewer decision writes back a
-- confirmed match or alias that seeds future ingest runs.
CREATE TABLE IF NOT EXISTS match_review (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    townland_id_a    INTEGER NOT NULL REFERENCES townland(id),
    townland_id_b    INTEGER NOT NULL REFERENCES townland(id),
    score            REAL NOT NULL,
    score_breakdown  TEXT,                   -- JSON feature vector
    status           TEXT DEFAULT 'pending', -- 'pending' | 'confirmed' | 'rejected'
    reviewer_note    TEXT,
    created_at       TEXT DEFAULT (datetime('now')),
    reviewed_at      TEXT
);

-- Field-level survivorship: records which source contributed each field value
-- and why, replacing the order-dependent COALESCE upsert.
CREATE TABLE IF NOT EXISTS field_provenance (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id        TEXT NOT NULL,
    field_name       TEXT NOT NULL,
    field_value      TEXT,
    source           TEXT NOT NULL,
    source_record_id TEXT,
    rule             TEXT,                   -- e.g. 'kg_authoritative' | 'first_non_null'
    created_at       TEXT DEFAULT (datetime('now')),
    UNIQUE(entity_id, field_name)
);

-- Persisted source mentions for workhouse-to-unified-record entity resolution.
CREATE TABLE IF NOT EXISTS source_mentions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    source_table        TEXT NOT NULL,
    source_record_id    TEXT NOT NULL UNIQUE,
    raw_name            TEXT,
    normalised_name     TEXT,
    forename            TEXT,
    surname             TEXT,
    phonetic_forename   TEXT,
    phonetic_surname    TEXT,
    raw_place           TEXT,
    normalised_place    TEXT,
    canonical_townland_id INTEGER REFERENCES townland(id),
    event_year          INTEGER,
    age                 INTEGER,
    inferred_birth_year INTEGER,
    occupation          TEXT,
    household_fields    TEXT,
    source_payload_json TEXT,
    created_at          TEXT DEFAULT (datetime('now')),
    updated_at          TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS entity_resolution_candidates (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    mention_id            INTEGER NOT NULL REFERENCES source_mentions(id) ON DELETE CASCADE,
    candidate_source_table TEXT NOT NULL DEFAULT 'unified_record',
    candidate_record_id   TEXT NOT NULL,
    candidate_name        TEXT,
    candidate_place       TEXT,
    candidate_year        INTEGER,
    score                 REAL NOT NULL,
    label                 TEXT NOT NULL,
    evidence_json         TEXT DEFAULT '[]',
    conflicts_json        TEXT DEFAULT '[]',
    missing_evidence_json TEXT DEFAULT '[]',
    review_required       INTEGER DEFAULT 0,
    created_at            TEXT DEFAULT (datetime('now')),
    updated_at            TEXT DEFAULT (datetime('now')),
    UNIQUE(mention_id, candidate_source_table, candidate_record_id)
);

CREATE TABLE IF NOT EXISTS workhouse_unified_links (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    mention_id              INTEGER NOT NULL REFERENCES source_mentions(id) ON DELETE CASCADE,
    unified_record_id       TEXT NOT NULL,
    score                   REAL NOT NULL,
    label                   TEXT NOT NULL,
    review_required         INTEGER DEFAULT 0,
    supporting_evidence_json TEXT DEFAULT '[]',
    conflicting_evidence_json TEXT DEFAULT '[]',
    missing_evidence_json   TEXT DEFAULT '[]',
    created_at              TEXT DEFAULT (datetime('now')),
    updated_at              TEXT DEFAULT (datetime('now')),
    UNIQUE(mention_id, unified_record_id)
);

CREATE TABLE IF NOT EXISTS entity_resolution_decisions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id  INTEGER NOT NULL REFERENCES entity_resolution_candidates(id) ON DELETE CASCADE,
    decision      TEXT NOT NULL,
    reviewer_note TEXT,
    decided_at    TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_source_mentions_source_record
ON source_mentions(source_table, source_record_id);

CREATE INDEX IF NOT EXISTS idx_resolution_candidates_mention
ON entity_resolution_candidates(mention_id, label, review_required);

CREATE INDEX IF NOT EXISTS idx_workhouse_links_record
ON workhouse_unified_links(unified_record_id, label, review_required);

-- In-process property graph (GraphRAG substrate — replaces Neo4j).
-- Built by scripts/build_graph.py; loaded into NetworkX at startup.
CREATE TABLE IF NOT EXISTS graph_nodes (
    node_id     TEXT PRIMARY KEY,
    label       TEXT NOT NULL,
    name        TEXT,
    props       TEXT,
    community   TEXT,
    embedding   BLOB
);
CREATE INDEX IF NOT EXISTS idx_gn_label ON graph_nodes(label);

CREATE TABLE IF NOT EXISTS graph_edges (
    src         TEXT NOT NULL REFERENCES graph_nodes(node_id),
    dst         TEXT NOT NULL REFERENCES graph_nodes(node_id),
    rel_type    TEXT NOT NULL,
    props       TEXT,
    PRIMARY KEY (src, dst, rel_type)
);
CREATE INDEX IF NOT EXISTS idx_ge_src ON graph_edges(src, rel_type);
CREATE INDEX IF NOT EXISTS idx_ge_dst ON graph_edges(dst, rel_type);
"""


def ensure_schema() -> None:
    conn = get_db_conn()
    try:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS census_record (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            townland_id     INTEGER NOT NULL REFERENCES townland(id),
            year            INTEGER NOT NULL,
            male            INTEGER,
            female          INTEGER,
            total           INTEGER,
            inhabited       INTEGER,
            uninhabited     INTEGER,
            source          TEXT DEFAULT 'json',
            kg_uri          TEXT,
            last_synced_at  TEXT DEFAULT (datetime('now')),
            created_at      TEXT DEFAULT (datetime('now')),
            UNIQUE(townland_id, year)
        );

        CREATE TABLE IF NOT EXISTS clearances_record (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            townland_id     INTEGER NOT NULL REFERENCES townland(id),
            year            INTEGER NOT NULL,
            count           INTEGER,
            source          TEXT DEFAULT 'json',
            created_at      TEXT DEFAULT (datetime('now')),
            UNIQUE(townland_id, year)
        );

        CREATE TABLE IF NOT EXISTS refresh_state (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            dataset_key     TEXT NOT NULL UNIQUE,
            last_synced_at  TEXT NOT NULL,
            source          TEXT,
            query_hash      TEXT,
            record_count    INTEGER DEFAULT 0,
            export_file     TEXT
        );
        """)

        townland_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='townland'"
        ).fetchone() is not None

        if not townland_exists:
            conn.execute(_TOWNLAND_CREATE)
            conn.commit()
        else:
            col_names = {
                c["name"]
                for c in conn.execute("PRAGMA table_info(townland)").fetchall()
            }
            if "entity_id" not in col_names:
                _apply_v2_migration(conn)
            else:
                for col_name, col_type in _V2_MIGRATION_COLS:
                    if col_name not in col_names:
                        try:
                            conn.execute(
                                f"ALTER TABLE townland ADD COLUMN {col_name} {col_type}"
                            )
                        except Exception:
                            pass
                conn.commit()

        conn.executescript(_SUPPORT_TABLES)

        for stmt in [
            "CREATE INDEX IF NOT EXISTS idx_townland_civil_parish ON townland(civil_parish)",
            "CREATE INDEX IF NOT EXISTS idx_townland_barony       ON townland(barony)",
            "CREATE INDEX IF NOT EXISTS idx_townland_county       ON townland(county)",
            "CREATE INDEX IF NOT EXISTS idx_townland_kg_uri       ON townland(kg_uri)",
            "CREATE INDEX IF NOT EXISTS idx_townland_entity_id    ON townland(entity_id)",
            "CREATE INDEX IF NOT EXISTS idx_census_year           ON census_record(year)",
            "CREATE INDEX IF NOT EXISTS idx_census_townland_year  ON census_record(townland_id, year)",
            "CREATE INDEX IF NOT EXISTS idx_clearances_year       ON clearances_record(year)",
            "CREATE INDEX IF NOT EXISTS idx_clearances_tl_year    ON clearances_record(townland_id, year)",
            "CREATE INDEX IF NOT EXISTS idx_xref_entity_id        ON townland_xref(entity_id)",
            "CREATE INDEX IF NOT EXISTS idx_match_review_status   ON match_review(status)",
        ]:
            conn.execute(stmt)

        conn.commit()
        log.info("extensions.schema_ready")
    finally:
        conn.close()


def _apply_v2_migration(conn: sqlite3.Connection) -> None:
    col_names = {
        c["name"]
        for c in conn.execute("PRAGMA table_info(townland)").fetchall()
    }
    for col_name, col_type in _V2_MIGRATION_COLS:
        if col_name not in col_names:
            conn.execute(f"ALTER TABLE townland ADD COLUMN {col_name} {col_type}")

    rows = conn.execute("SELECT id FROM townland WHERE entity_id IS NULL").fetchall()
    for row in rows:
        conn.execute(
            "UPDATE townland SET entity_id = ? WHERE id = ?",
            (str(uuid.uuid4()), row[0]),
        )
    conn.commit()
    log.info(
        "extensions.v2_migration_complete | rows_backfilled=%d", len(rows)
    )
