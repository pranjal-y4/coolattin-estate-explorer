from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

_WORKHOUSE_CACHE: list[dict] | None = None
_WORKHOUSE_MATCH_INDEX: dict[str, list[dict]] | None = None

_NAME_SCORE_THRESHOLD = 0.60
_HIGH_NAME_SCORE = 0.80
_OCCUPATION_BONUS = 0.05

_OCC_KEYWORDS = {
    "labourer", "farmer", "servant", "weaver", "tailor", "carpenter",
    "blacksmith", "shoemaker", "mason", "widow", "spinster", "herder",
    "shepherd", "miller", "fisherman", "innkeeper", "shopkeeper",
}


def _data_dir() -> Path:
    from backend.config import ActiveConfig
    return ActiveConfig.STATIC_DATA_DIR


def _norm(s: object) -> str:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    t = str(s).strip().lower()
    return " ".join(t.split())


def _parse_year(s: object) -> int | None:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return None
    text = str(s)
    m = re.search(r"\b(18[0-9]{2}|19[0-9]{2})\b", text)
    if m:
        return int(m.group(1))
    m = re.search(r"\b([3-7][0-9])\b", text)
    if m:
        return 1800 + int(m.group(1))
    return None


def _split_workhouse_name(name: str) -> tuple[str, str]:
    t = _norm(name)
    if not t:
        return "", ""
    parts = t.split(" ")
    if len(parts) == 1:
        return "", parts[0]
    return " ".join(parts[1:]), parts[0]


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


def _name_score(unified_variants: set[str], wh_variants: set[str]) -> float:
    best = 0.0
    for uv in unified_variants:
        for wv in wh_variants:
            r = SequenceMatcher(None, uv, wv).ratio()
            if r > best:
                best = r
                if best >= 1.0:
                    return best
    return best


def _occupation_bonus(unified_occupation: object, wh_employment: object) -> float:
    u = _norm(unified_occupation)
    w = _norm(wh_employment)
    if not u or not w:
        return 0.0
    u_words = set(u.split())
    w_words = set(w.split())
    shared = (u_words | w_words) & _OCC_KEYWORDS
    if shared and (u_words & w_words & _OCC_KEYWORDS):
        return _OCCUPATION_BONUS
    return 0.0


def _safe_int(value: object) -> int | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        return int(float(str(value).strip()))
    except Exception:
        return None


def get_workhouse() -> list[dict]:
    global _WORKHOUSE_CACHE
    if _WORKHOUSE_CACHE is not None:
        return _WORKHOUSE_CACHE

    path = _data_dir() / "workhouse_data_final.xlsx"
    if not path.exists():
        log.warning("workhouse_service.xlsx_missing | path=%s", path)
        _WORKHOUSE_CACHE = []
        return []

    sheet1 = pd.read_excel(path, sheet_name="1-127", engine="openpyxl")
    sheet2 = pd.read_excel(path, sheet_name="from 128", engine="openpyxl")

    rows: list[dict] = []

    for _, r in sheet1.iterrows():
        raw_name = r.get("Pauper Name")
        if pd.isna(raw_name):
            continue
        forename, surname = _split_workhouse_name(str(raw_name))
        rows.append({
            "source_sheet": "1-127",
            "source_record_id": f"1-127:{_safe_int(r.get('Number in Register')) or (len(rows) + 1)}",
            "raw_name": str(raw_name).strip(),
            "forename": forename.title() if forename else None,
            "surname": surname.title() if surname else None,
            "register_number": _safe_int(r.get("Number in Register")),
            "electoral_division": None,
            "sex": None, "age": None, "status": None,
            "employment": None, "religion": None, "disability": None,
            "spouse": None, "children_count": None,
            "admitted_or_born": None, "died_or_left": None,
            "_year_admitted": None, "_year_left": None,
        })

    for _, r in sheet2.iterrows():
        raw_name = r.get("Names and Surnames of Paupers")
        if pd.isna(raw_name):
            continue
        forename, surname = _split_workhouse_name(str(raw_name))

        def safe_str(col: str) -> str | None:
            v = r.get(col)
            return None if pd.isna(v) else str(v).strip()

        admitted = safe_str("date when admitted or born in workhouse")
        died_left = safe_str("Date when died or left workhouse")
        rows.append({
            "source_sheet": "from 128",
            "source_record_id": f"from 128:{len(rows) + 1}",
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
            "admitted_or_born": admitted,
            "died_or_left": died_left,
            "_year_admitted": _parse_year(admitted),
            "_year_left": _parse_year(died_left),
        })

    _WORKHOUSE_CACHE = rows
    log.info("workhouse_service.loaded | rows=%d", len(rows))
    return rows


def _wh_year(record: dict) -> int | None:
    return record.get("_year_admitted") or record.get("_year_left")


def _assign_confidence(
    place_match: bool,
    date_match: bool,
    score: float,
) -> str | None:
    if score < _NAME_SCORE_THRESHOLD:
        return None
    if place_match and date_match and score >= _HIGH_NAME_SCORE:
        return "High"
    if (place_match or date_match) and score >= _NAME_SCORE_THRESHOLD:
        return "Medium"
    return "Low"


def get_match_index() -> dict[str, list[dict]]:
    global _WORKHOUSE_MATCH_INDEX
    if _WORKHOUSE_MATCH_INDEX is not None:
        return _WORKHOUSE_MATCH_INDEX

    from backend.services.unified_service import get_unified

    unified = get_unified()
    workhouse = get_workhouse()

    if not workhouse:
        _WORKHOUSE_MATCH_INDEX = {}
        return {}

    wh_by_place: dict[str, list[dict]] = {}
    wh_all = list(workhouse)
    for wr in wh_all:
        ed = _norm(wr.get("electoral_division"))
        if ed:
            wh_by_place.setdefault(ed, []).append(wr)

    out: dict[str, list[dict]] = {}

    for _, ur in unified.iterrows():
        rid = str(ur.get("record_id") or "").strip()
        if not rid:
            continue

        u_townland = _norm(ur.get("townland"))
        u_parish = _norm(ur.get("parish"))
        u_year_raw = ur.get("year")
        u_year: int | None = None
        if u_year_raw is not None:
            try:
                u_year = int(u_year_raw)
            except (TypeError, ValueError):
                pass

        u_variants = _name_variants(ur.get("forename"), ur.get("surname"), ur.get("canonical_name"))
        u_occupation = ur.get("occupation")

        place_candidates: list[dict] = []
        seen_ed_keys: set[str] = set()
        for ed_key in (u_townland, u_parish):
            if ed_key and ed_key not in seen_ed_keys:
                seen_ed_keys.add(ed_key)
                place_candidates.extend(wh_by_place.get(ed_key, []))
        for wr in wh_all:
            ed = _norm(wr.get("electoral_division"))
            if ed and wr not in place_candidates:
                if (ed in u_townland or u_townland in ed or
                        ed in u_parish or u_parish in ed):
                    place_candidates.append(wr)

        place_date_candidates: list[dict] = []
        if u_year is not None:
            for wr in place_candidates:
                wy = _wh_year(wr)
                if wy is not None and abs(wy - u_year) <= 1:
                    place_date_candidates.append(wr)

        if place_date_candidates:
            score_pool = place_date_candidates
            pool_place = True
            pool_date = True
        elif place_candidates:
            score_pool = place_candidates
            pool_place = True
            pool_date = False
        else:
            score_pool = wh_all
            pool_place = False
            pool_date = False

        seen_sig: set[str] = set()
        scored: list[tuple[float, bool, bool, dict]] = []

        for wr in score_pool:
            sig = f"{wr.get('source_sheet')}|{wr.get('raw_name')}|{wr.get('electoral_division')}|{wr.get('admitted_or_born')}"
            if sig in seen_sig:
                continue
            seen_sig.add(sig)

            wh_variants = _name_variants(wr.get("forename"), wr.get("surname"), wr.get("raw_name"))
            score = _name_score(u_variants, wh_variants)

            ed = _norm(wr.get("electoral_division"))
            rec_place = bool(
                ed and (
                    ed == u_townland or ed == u_parish
                    or ed in u_townland or u_townland in ed
                    or ed in u_parish or u_parish in ed
                )
            )
            wy = _wh_year(wr)
            rec_date = bool(u_year is not None and wy is not None and abs(wy - u_year) <= 1)

            confidence = _assign_confidence(rec_place, rec_date, score)
            if confidence is None:
                continue

            occ_bonus = _occupation_bonus(u_occupation, wr.get("employment"))
            effective_score = min(score + occ_bonus, 1.0)

            scored.append((effective_score, rec_place, rec_date, wr, confidence, sig))

        if not scored and score_pool is not wh_all:
            for wr in wh_all:
                sig = f"{wr.get('source_sheet')}|{wr.get('raw_name')}|{wr.get('electoral_division')}|{wr.get('admitted_or_born')}"
                if sig in seen_sig:
                    continue
                seen_sig.add(sig)
                wh_variants = _name_variants(wr.get("forename"), wr.get("surname"), wr.get("raw_name"))
                score = _name_score(u_variants, wh_variants)
                ed = _norm(wr.get("electoral_division"))
                rec_place = bool(
                    ed and (
                        ed == u_townland or ed == u_parish
                        or ed in u_townland or u_townland in ed
                        or ed in u_parish or u_parish in ed
                    )
                )
                wy = _wh_year(wr)
                rec_date = bool(u_year is not None and wy is not None and abs(wy - u_year) <= 1)
                confidence = _assign_confidence(rec_place, rec_date, score)
                if confidence is None:
                    continue
                occ_bonus = _occupation_bonus(u_occupation, wr.get("employment"))
                effective_score = min(score + occ_bonus, 1.0)
                scored.append((effective_score, rec_place, rec_date, wr, confidence, sig))

        tier_order = {"High": 0, "Medium": 1, "Low": 2}
        scored.sort(key=lambda x: (tier_order.get(x[4], 3), -x[0]))

        matches: list[dict] = []
        for item in scored:
            effective_score, rec_place, rec_date, wr, confidence, _ = item
            m = {k: v for k, v in wr.items() if not k.startswith("_")}
            m["location_match"] = rec_place
            occ_b = _occupation_bonus(u_occupation, wr.get("employment"))
            basis_parts = []
            if rec_place:
                basis_parts.append("electoral division")
            if rec_date:
                basis_parts.append("date")
            if occ_b:
                basis_parts.append("occupation")
            m["match_basis"] = "name" + ((" + " + " + ".join(basis_parts)) if basis_parts else "")
            m["confidence"] = confidence
            m["name_score"] = round(effective_score, 3)
            m["occupation_match"] = bool(occ_b)
            matches.append(m)

        out[rid] = matches

    _WORKHOUSE_MATCH_INDEX = out
    log.info("workhouse_service.index_built | unified_records=%d", len(out))
    return out


def get_matches_for_record(record_id: str) -> dict:
    try:
        from backend.services.workhouse_entity_resolution import (
            get_matches_for_record as _persisted_matches_for_record,
            has_persisted_links,
        )

        if has_persisted_links():
            return _persisted_matches_for_record(record_id)
    except Exception as exc:
        log.debug("workhouse_service.persisted_links_unavailable error=%s", exc)

    idx = get_match_index()
    matches = idx.get(str(record_id), [])
    return {"record_id": record_id, "count": len(matches), "matches": matches}
