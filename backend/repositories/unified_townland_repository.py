"""
coolattin/repositories/unified_townland_repository.py

Read access to the townland names carried by the unified estate records.

`unified_record.townland` is a source field — the place name as written in the
estate ledgers.  This module is the only place that reads it for resolution
purposes; the canonical townland table is owned by townland_repository.
"""
from __future__ import annotations

import logging

from extensions import get_db_conn

log = logging.getLogger(__name__)


def distinct_source_townlands(limit: int = 0) -> list[tuple[str, int]]:
    """
    Return [(source_name, occurrences)] ordered by occurrence count.

    Deterministic: ties break on the name so repeated runs process the same
    records in the same order.
    """
    conn = get_db_conn()
    try:
        sql = """
            SELECT townland AS name, COUNT(*) AS occurrences
              FROM unified_record
             WHERE townland IS NOT NULL AND TRIM(townland) != ''
             GROUP BY townland
             ORDER BY occurrences DESC, name
        """
        params: tuple = ()
        if limit and limit > 0:
            sql += " LIMIT ?"
            params = (limit,)
        rows = conn.execute(sql, params).fetchall()
        return [(r["name"], int(r["occurrences"])) for r in rows]
    finally:
        conn.close()
