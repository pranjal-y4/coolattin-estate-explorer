"""
extensions.py

Flask extension singletons — imported by create_app and by modules
that need db access without circular imports.

Currently provides:
  - init_db(): registers the SQLite database path
  - get_db_conn(): returns a sqlite3 connection with row_factory set
  - ensure_schema(): creates all tables if they do not exist
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

log = logging.getLogger(__name__)

# Module-level reference to the DB path — set once by init_db()
_DB_PATH: Path | None = None


def init_db(db_path: Path) -> None:
    """Call from create_app() to register the database path."""
    global _DB_PATH
    _DB_PATH = db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    log.info("extensions.db_path_set | path=%s", str(db_path))


def get_db_conn() -> sqlite3.Connection:
    """Return a sqlite3 connection.  Caller is responsible for closing it."""
    if _DB_PATH is None:
        raise RuntimeError("Database not initialised — call init_db() first via create_app()")
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    # D14: Performance pragmas — safe with WAL, no durability trade-off for reads
    conn.execute("PRAGMA synchronous=NORMAL")       # WAL makes FULL unnecessary
    conn.execute("PRAGMA cache_size=-65536")         # 64 MB page cache
    conn.execute("PRAGMA temp_store=2")              # keep temp tables in memory
    conn.execute("PRAGMA mmap_size=268435456")       # 256 MB memory-mapped I/O
    return conn


def ensure_schema() -> None:
    """
    Create all tables if they do not exist.
    Safe to call on every startup — uses IF NOT EXISTS.
    """
    conn = get_db_conn()
    try:
        conn.executescript("""
        -- Canonical townlands — enriched from both the estate GeoJSON and the VRTI KG
        CREATE TABLE IF NOT EXISTS townland (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            name                TEXT NOT NULL UNIQUE,   -- canonical UPPER-CASE English name
            name_gaelic         TEXT,                   -- Irish/Gaelic name
            barony              TEXT,                   -- from KG place hierarchy
            civil_parish        TEXT,                   -- from KG place hierarchy
            electoral_division  TEXT,
            placename_theme     TEXT,                   -- from CSV/manual enrichment
            description         TEXT,                   -- from CSV/manual enrichment
            -- Estate GeoJSON identifiers
            td_id               TEXT,                   -- TD_ID from estate GeoJSON
            guid                TEXT,                   -- GUID from estate GeoJSON
            -- Measurements
            area_sqm            REAL,                   -- area in square metres (from GeoJSON AREA)
            -- KG identifiers & geography
            kg_uri              TEXT,                   -- VRTI KG subject URI
            wkt_geometry        TEXT,                   -- boundary WKT polygon from KG
            centroid_lat        REAL,
            centroid_lon        REAL,
            county              TEXT,                   -- county name from KG
            osm_id              TEXT,                   -- OpenStreetMap identifier
            osi_id              TEXT,                   -- Ordnance Survey Ireland identifier
            vrti_id             TEXT,                   -- VRTI short identifier
            images_json         TEXT,                   -- JSON array of image URLs from KG
            links_json          TEXT,                   -- JSON array of external links from KG
            source              TEXT DEFAULT 'json',    -- 'json'|'kg'|'manual'
            created_at          TEXT DEFAULT (datetime('now')),
            updated_at          TEXT DEFAULT (datetime('now'))
        );

        -- Census records (one row per townland × year)
        -- Covers both standard census years (1841-1891, from KG) and
        -- estate survey years (1827-1868, from the estate GeoJSON).
        CREATE TABLE IF NOT EXISTS census_record (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            townland_id     INTEGER NOT NULL REFERENCES townland(id),
            year            INTEGER NOT NULL,
            male            INTEGER,                    -- null for estate records (total only)
            female          INTEGER,                    -- null for estate records (total only)
            total           INTEGER,
            inhabited       INTEGER,
            uninhabited     INTEGER,
            source          TEXT DEFAULT 'json',        -- 'json'|'kg'|'manual'
            kg_uri          TEXT,                       -- KG URI of the census record entity
            last_synced_at  TEXT DEFAULT (datetime('now')),
            created_at      TEXT DEFAULT (datetime('now')),
            UNIQUE(townland_id, year)
        );

        -- Clearances records — estate evictions per townland per year (1847-1856)
        -- Sourced from the estate GeoJSON; no KG equivalent.
        CREATE TABLE IF NOT EXISTS clearances_record (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            townland_id     INTEGER NOT NULL REFERENCES townland(id),
            year            INTEGER NOT NULL,           -- 1847-1856
            count           INTEGER,                    -- number of clearances that year
            source          TEXT DEFAULT 'json',
            created_at      TEXT DEFAULT (datetime('now')),
            UNIQUE(townland_id, year)
        );

        -- Refresh state — tracks when each dataset was last fetched from the KG or GeoJSON
        CREATE TABLE IF NOT EXISTS refresh_state (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            dataset_key     TEXT NOT NULL UNIQUE,       -- e.g. 'full_ingest'
            last_synced_at  TEXT NOT NULL,
            source          TEXT,                       -- 'kg_refresh'|'json'|'manual'
            query_hash      TEXT,
            record_count    INTEGER DEFAULT 0,
            export_file     TEXT                        -- path to most recent Excel export
        );
        """)
        # Migrate existing DBs: add any missing columns without failing on re-runs
        townland_cols = [
            "county      TEXT",
            "osm_id      TEXT",
            "osi_id      TEXT",
            "vrti_id     TEXT",
            "images_json TEXT",
            "links_json  TEXT",
            "td_id       TEXT",
            "guid        TEXT",
            "area_sqm    REAL",
        ]
        for col_def in townland_cols:
            try:
                conn.execute(f"ALTER TABLE townland ADD COLUMN {col_def}")
            except Exception:
                pass  # column already exists

        # Ensure clearances table exists on older DBs that predate this migration
        conn.execute("""
            CREATE TABLE IF NOT EXISTS clearances_record (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                townland_id INTEGER NOT NULL REFERENCES townland(id),
                year        INTEGER NOT NULL,
                count       INTEGER,
                source      TEXT DEFAULT 'json',
                created_at  TEXT DEFAULT (datetime('now')),
                UNIQUE(townland_id, year)
            )
        """)

        # D14: Core table indexes for common query patterns
        # townland hierarchy lookups (map + census pages)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_townland_civil_parish ON townland(civil_parish)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_townland_barony       ON townland(barony)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_townland_county       ON townland(county)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_townland_kg_uri       ON townland(kg_uri)")
        # census year-range queries (census dashboard)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_census_year           ON census_record(year)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_census_townland_year  ON census_record(townland_id, year)")
        # clearances year queries (analytics page)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_clearances_year       ON clearances_record(year)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_clearances_tl_year    ON clearances_record(townland_id, year)")

        conn.commit()
        log.info("extensions.schema_ready")
    finally:
        conn.close()
