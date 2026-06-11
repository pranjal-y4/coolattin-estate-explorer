"""
coolattin/services/unified_service.py

Service layer for the unified estate records database.

Extracted from app.py (_get_unified, _get_workhouse, _build_centroids).
The main records search, workhouse linking, and centroid data live here.

Routes call this service; this service handles caching and data loading.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

# Process-level caches (same approach as original app.py, now in one place)
_UNIFIED_CACHE: pd.DataFrame | None = None
_CENTROIDS_CACHE: dict[str, tuple[float, float]] | None = None


def _data_dir() -> Path:
    from config import ActiveConfig
    return ActiveConfig.STATIC_DATA_DIR


def get_unified() -> pd.DataFrame:
    """Load and cache the unified records DataFrame."""
    global _UNIFIED_CACHE
    if _UNIFIED_CACHE is None:
        path = _data_dir() / "unified_processed.csv"
        _UNIFIED_CACHE = pd.read_csv(path)
        log.info("unified_service.loaded | rows=%d", len(_UNIFIED_CACHE))
    return _UNIFIED_CACHE.copy()


def get_centroids() -> dict[str, tuple[float, float]]:
    """Compute and cache townland centroids from GeoJSON."""
    global _CENTROIDS_CACHE
    if _CENTROIDS_CACHE is not None:
        return _CENTROIDS_CACHE
    from backend.services.map_service import build_centroids
    _CENTROIDS_CACHE = build_centroids()
    return _CENTROIDS_CACHE


def search_records(
    surname: str = "",
    forename: str = "",
    townland: str = "",
    year: str = "",
    estate: str = "",
    limit: int = 0,
) -> list[dict]:
    """
    Search unified records with optional filters.
    Returns JSON-safe list of dicts.
    """
    df = get_unified()

    if surname:
        df = df[df["surname"].fillna("").astype(str).str.lower().str.contains(surname.lower())]
    if forename:
        df = df[df["forename"].fillna("").astype(str).str.lower().str.contains(forename.lower())]
    if townland:
        df = df[df["townland"].fillna("").astype(str).str.lower().str.contains(townland.lower())]
    if year:
        df = df[df["year"].astype("Int64").astype(str).str.contains(year)]
    if estate:
        df = df[df["estate"].fillna("").astype(str).str.lower().str.contains(estate.lower())]

    if limit:
        df = df.head(limit)

    # Remove noisy duplicate collision columns
    noisy = ["surname_2", "forename_2", "surname_3", "forename_3"]
    df = df.drop(columns=[c for c in noisy if c in df.columns], errors="ignore")

    if "year" in df.columns:
        df["year"] = pd.to_numeric(df["year"], errors="coerce").astype("Int64")

    df = df.astype(object).where(pd.notna(df), None)
    return df.to_dict(orient="records")


def get_stats() -> dict:
    """Return summary statistics about the unified records."""
    df = get_unified()
    return {
        "total_records": int(len(df)),
        "unique_surnames": int(df["surname"].dropna().nunique()),
        "unique_forenames": int(df["forename"].dropna().nunique()),
        "unique_townlands": int(df["townland"].dropna().nunique()),
        "unique_estates": int(df["estate"].dropna().nunique()),
        "records_with_year": int(df["year"].notna().sum()),
        "records_with_townland": int(df["townland"].notna().sum()),
    }


def get_townland_list() -> list[str]:
    df = get_unified()
    return sorted([x for x in df["townland"].dropna().astype(str).unique() if x])


def get_surname_list() -> list[str]:
    df = get_unified()
    return sorted([x for x in df["surname"].dropna().astype(str).unique() if x])


_SKIP_CT_NAMES = {"common grazing", "house lot", "multiple chief tenants"}


def suggest_surnames(q: str = "", townland: str = "") -> list[str]:
    df = get_unified()
    if townland:
        df = df[df["townland"].fillna("").astype(str).str.lower() == townland.lower()]

    # Primary surnames
    primary = df["surname"].dropna().astype(str)

    # Include valid chief_tenant_surnames as fallback (same rules as buildIndexes in main.js)
    ct_sn = df["chief_tenant_surname"].fillna("").astype(str)
    ct_fn = df["chief_tenant_forename"].fillna("").astype(str)
    valid_ct_mask = (
        ct_sn.str.len().gt(0)
        & ct_sn.str.match(r"^[A-Za-z\s'\-]+$")
        & ~ct_sn.str.lower().isin(_SKIP_CT_NAMES)
        & ct_fn.str.len().gt(0)
        & ~ct_fn.isin(["-", "Multiple Chief Tenants"])
        # Only use CT surname when there's no direct surname on the record
        & df["surname"].isna()
    )
    ct_surnames = ct_sn[valid_ct_mask]

    import pandas as pd
    all_surnames = pd.concat([primary, ct_surnames]).dropna().astype(str)
    # Filter multi-word compound entries like "Grant, Richard And James"
    all_surnames = all_surnames[~all_surnames.str.contains(",")]

    if q:
        all_surnames = all_surnames[all_surnames.str.lower().str.startswith(q.lower())]
    return sorted(all_surnames.unique().tolist())[:50]
