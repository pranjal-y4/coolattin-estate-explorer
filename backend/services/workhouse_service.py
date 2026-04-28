"""
coolattin/services/workhouse_service.py

Workhouse data service.

Extracted from app.py (_get_workhouse, _build_workhouse_match_index,
_split_workhouse_name, _name_variants, _norm).

Responsibilities:
  - Load workhouse Excel data
  - Parse and normalise pauper names
  - Build fuzzy name match index against unified records
  - Return ranked workhouse matches for a given record_id
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

_WORKHOUSE_CACHE: pd.DataFrame | None = None
_WORKHOUSE_MATCH_INDEX: dict[str, list[dict]] | None = None


def _data_dir() -> Path:
    from config import ActiveConfig
    return ActiveConfig.STATIC_DATA_DIR


def _norm(s: object) -> str:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    t = str(s).strip().lower()
    return " ".join(t.split())


def _split_workhouse_name(name: str) -> tuple[str, str]:
    t = _norm(name)
    if not t:
        return "", ""
    parts = t.split(" ")
    if len(parts) == 1:
        return "", parts[0]
    return " ".join(parts[1:]), parts[0]   # forename, surname (stored as "Surname Forename")


def _name_variants(forename: object, surname: object, canonical: object) -> set[str]:
    f = _norm(forename)
    s = _norm(surname)
    c = _norm(canonical)
    out: set[str] = set()
    if f and s:
        out.add(f"{f} {s}")
        out.add(f"{s} {f}")
    if c:
        out.add(c)
    return {x for x in out if x}


def _safe_int(value: object) -> int | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        return int(float(str(value).strip()))
    except Exception:
        return None


def get_workhouse() -> pd.DataFrame:
    """Load and cache workhouse Excel data."""
    global _WORKHOUSE_CACHE
    if _WORKHOUSE_CACHE is not None:
        return _WORKHOUSE_CACHE.copy()

    path = _data_dir() / "workhouse_data_final.xlsx"
    if not path.exists():
        log.warning("workhouse_service.xlsx_missing | path=%s", path)
        return pd.DataFrame()

    sheet1 = pd.read_excel(path, sheet_name="1-127", engine="openpyxl")
    sheet2 = pd.read_excel(path, sheet_name="from 128", engine="openpyxl")

    rows = []

    for _, r in sheet1.iterrows():
        raw_name = r.get("Pauper Name")
        if pd.isna(raw_name):
            continue
        forename, surname = _split_workhouse_name(str(raw_name))
        rows.append({
            "source_sheet": "1-127",
            "raw_name": str(raw_name).strip(),
            "forename": forename.title() if forename else None,
            "surname": surname.title() if surname else None,
            "register_number": _safe_int(r.get("Number in Register")),
            "electoral_division": None,
            "sex": None, "age": None, "status": None,
            "employment": None, "religion": None, "disability": None,
            "spouse": None, "children_count": None,
            "admitted_or_born": None, "died_or_left": None,
        })

    for _, r in sheet2.iterrows():
        raw_name = r.get("Names and Surnames of Paupers")
        if pd.isna(raw_name):
            continue
        forename, surname = _split_workhouse_name(str(raw_name))

        def safe_str(col: str) -> str | None:
            v = r.get(col)
            return None if pd.isna(v) else str(v).strip()

        rows.append({
            "source_sheet": "from 128",
            "raw_name": str(raw_name).strip(),
            "forename": forename.title() if forename else None,
            "surname": surname.title() if surname else None,
            "register_number": None,
            "electoral_division": safe_str("Electoral division"),
            "sex": safe_str("Sex"),
            "age": safe_str("Age"),
            "status": safe_str("If Adult, other single, married, widower, or widow, if child, whether orphan, deserted or bastard"),
            "employment": safe_str("Employment or Calling"),
            "religion": safe_str("religious denomination"),
            "disability": safe_str("If disable then description"),
            "spouse": safe_str("Name of wife or husband"),
            "children_count": safe_str("Number of children"),
            "admitted_or_born": safe_str("date when admitted or born in workhouse"),
            "died_or_left": safe_str("Date when died or left workhouse"),
        })

    _WORKHOUSE_CACHE = pd.DataFrame(rows)
    log.info("workhouse_service.loaded | rows=%d", len(_WORKHOUSE_CACHE))
    return _WORKHOUSE_CACHE.copy()


def get_match_index() -> dict[str, list[dict]]:
    """
    Build and cache fuzzy match index: unified record_id → matching workhouse records.
    Computed once per process lifetime.
    """
    global _WORKHOUSE_MATCH_INDEX
    if _WORKHOUSE_MATCH_INDEX is not None:
        return _WORKHOUSE_MATCH_INDEX

    from backend.services.unified_service import get_unified

    unified = get_unified()
    workhouse = get_workhouse()

    if workhouse.empty:
        _WORKHOUSE_MATCH_INDEX = {}
        return {}

    wh_by_name: dict[str, list[dict]] = {}
    for _, wr in workhouse.iterrows():
        variants = _name_variants(wr.get("forename"), wr.get("surname"), wr.get("raw_name"))
        payload = {k: (None if pd.isna(v) else v) for k, v in wr.to_dict().items()}
        for v in variants:
            wh_by_name.setdefault(v, []).append(payload)

    out: dict[str, list[dict]] = {}
    for _, ur in unified.iterrows():
        rid = str(ur.get("record_id") or "").strip()
        if not rid:
            continue

        u_townland = _norm(ur.get("townland"))
        u_parish = _norm(ur.get("parish"))
        variants = _name_variants(ur.get("forename"), ur.get("surname"), ur.get("canonical_name"))

        matches: list[dict] = []
        seen: set[str] = set()

        for v in variants:
            for m in wh_by_name.get(v, []):
                sig = f"{m.get('source_sheet')}|{m.get('raw_name')}|{m.get('electoral_division')}|{m.get('admitted_or_born')}"
                if sig in seen:
                    continue
                seen.add(sig)
                ed = _norm(m.get("electoral_division"))
                location_match = bool(
                    ed and (ed == u_townland or ed == u_parish or ed in u_townland or ed in u_parish)
                )
                m2 = dict(m)
                m2["location_match"] = location_match
                m2["match_basis"] = "name + electoral division" if location_match else "name"
                matches.append(m2)

        matches.sort(key=lambda x: (not bool(x.get("location_match")), str(x.get("raw_name") or "")))
        out[rid] = matches

    _WORKHOUSE_MATCH_INDEX = out
    log.info("workhouse_service.index_built | unified_records=%d", len(out))
    return out


def get_matches_for_record(record_id: str) -> dict:
    """Return workhouse matches for a given unified record_id."""
    idx = get_match_index()
    matches = idx.get(str(record_id), [])
    return {"record_id": record_id, "count": len(matches), "matches": matches}
