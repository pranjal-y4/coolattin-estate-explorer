"""
coolattin/repositories/refresh_state_repository.py

Persistence for refresh state — tracks when datasets were last
fetched from the VRTI KG so the service layer can implement
DB-first / KG-second retrieval without repeated KG calls.

This module is the ONLY place that reads/writes the `refresh_state` table.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from extensions import get_db_conn
from backend.models.census_models import RefreshState

log = logging.getLogger(__name__)

# Default staleness TTL in days — overridden per dataset in config
DEFAULT_STALE_AFTER_DAYS = 7


def get(dataset_key: str, stale_after_days: int = DEFAULT_STALE_AFTER_DAYS) -> Optional[RefreshState]:
    """
    Return the refresh state for a dataset, or None if never synced.

    The `is_stale` field is computed here based on `last_synced_at`
    vs the configured TTL — it is not stored in the DB.
    """
    conn = get_db_conn()
    try:
        row = conn.execute(
            "SELECT * FROM refresh_state WHERE dataset_key = ?", (dataset_key,)
        ).fetchone()
        if not row:
            return None

        last_synced = _parse_dt(row["last_synced_at"])
        now = datetime.now(timezone.utc)
        age_days = (now - last_synced).days if last_synced else None
        is_stale = (age_days is None) or (age_days >= stale_after_days)

        return RefreshState(
            dataset_key=row["dataset_key"],
            last_synced_at=row["last_synced_at"],
            source=row["source"],
            query_hash=row["query_hash"],
            record_count=row["record_count"] or 0,
            export_file=row["export_file"],
            is_stale=is_stale,
        )
    finally:
        conn.close()


def upsert(
    dataset_key: str,
    source: str = "kg_refresh",
    record_count: int = 0,
    export_file: Optional[str] = None,
    query_hash: Optional[str] = None,
) -> None:
    """
    Insert or update refresh state for a dataset.
    Call this immediately after a successful KG ingestion.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    conn = get_db_conn()
    try:
        existing = conn.execute(
            "SELECT id FROM refresh_state WHERE dataset_key = ?", (dataset_key,)
        ).fetchone()

        if existing:
            conn.execute(
                """
                UPDATE refresh_state SET
                    last_synced_at = ?,
                    source         = ?,
                    query_hash     = ?,
                    record_count   = ?,
                    export_file    = ?
                WHERE dataset_key = ?
                """,
                (now_iso, source, query_hash, record_count, export_file, dataset_key),
            )
        else:
            conn.execute(
                """
                INSERT INTO refresh_state
                    (dataset_key, last_synced_at, source, query_hash, record_count, export_file)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (dataset_key, now_iso, source, query_hash, record_count, export_file),
            )

        conn.commit()
        log.info(
            "refresh_state.upsert | key=%s source=%s count=%d",
            dataset_key, source, record_count,
        )
    finally:
        conn.close()


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO datetime string into an aware datetime object."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, AttributeError):
        return None
