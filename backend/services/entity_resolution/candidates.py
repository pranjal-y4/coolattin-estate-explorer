"""
backend/services/entity_resolution/candidates.py

Candidate blocking and fuzzy candidate generation for workhouse mentions.
"""
from __future__ import annotations

from collections import defaultdict
from difflib import SequenceMatcher
from typing import Any

try:
    from rapidfuzz import fuzz
except Exception:  # pragma: no cover - optional fallback
    fuzz = None


def _fuzzy_ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if fuzz is not None:
        return float(fuzz.token_sort_ratio(a, b))
    return SequenceMatcher(None, a, b).ratio() * 100.0


def build_unified_index(unified_records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """
    Pre-build a blocking index keyed by phonetic surname.
    Reduces generate_candidates from O(n_workhouse × n_unified) to
    O(n_workhouse × avg_surname_bucket_size).
    Also includes a '_place' bucket for place-matched lookups.
    """
    idx: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rec in unified_records:
        psn = rec.get("phonetic_surname") or ""
        if psn:
            idx[f"ps:{psn}"].append(rec)
        place = rec.get("normalised_place") or ""
        if place:
            idx[f"pl:{place}"].append(rec)
    return dict(idx)


def generate_candidates(
    mention: dict[str, Any],
    unified_records: list[dict[str, Any]],
    *,
    max_candidates: int = 25,
    unified_index: dict[str, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """
    Generate candidate unified records for a workhouse mention.

    Pass ``unified_index`` (built once via ``build_unified_index``) to avoid
    scanning all 13 k unified records for every mention — typically reduces
    the search space from 13,707 to a few dozen per mention.
    """
    candidates: dict[str, dict[str, Any]] = {}
    m_name = mention.get("normalised_name") or ""
    m_surname = mention.get("surname") or ""
    m_forename_initial = mention.get("forename_initial") or ""
    m_phonetic_surname = mention.get("phonetic_surname") or ""
    m_place = mention.get("normalised_place") or ""
    m_year = mention.get("event_year")

    # When an index is available, restrict the search to relevant surname/place
    # buckets instead of iterating the entire unified corpus.
    if unified_index is not None:
        pool: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for bucket_key in (
            f"ps:{m_phonetic_surname}" if m_phonetic_surname else "",
            f"pl:{m_place}" if m_place else "",
        ):
            if not bucket_key:
                continue
            for rec in unified_index.get(bucket_key, []):
                rid = str(rec.get("record_id") or "")
                if rid and rid not in seen_ids:
                    seen_ids.add(rid)
                    pool.append(rec)
        # Fall back to full scan only when no index bucket produced any candidates
        if not pool:
            pool = unified_records
    else:
        pool = unified_records

    for record in pool:
        record_id = str(record.get("record_id") or "")
        if not record_id:
            continue
        strategies: list[str] = []
        r_name = record.get("normalised_name") or ""
        r_place = record.get("normalised_place") or ""
        r_year = record.get("event_year")

        if m_name and r_name and m_name == r_name:
            strategies.append("exact_normalised_name")
        if (
            m_surname
            and m_surname == record.get("surname")
            and m_forename_initial
            and m_forename_initial == record.get("forename_initial")
        ):
            strategies.append("surname_plus_initial")
        if (
            m_phonetic_surname
            and m_phonetic_surname == record.get("phonetic_surname")
            and record.get("phonetic_surname")
        ):
            strategies.append("phonetic_surname")
        if _fuzzy_ratio(m_name, r_name) >= 82.0:
            strategies.append("fuzzy_full_name")
        if m_place and r_place and m_place == r_place:
            strategies.append("same_canonical_place")
        elif m_place and r_place and (m_place in r_place or r_place in m_place):
            strategies.append("variant_place")
        if m_year and r_year and abs(int(m_year) - int(r_year)) <= 10:
            strategies.append("compatible_event_year")

        if not strategies:
            continue

        name_ratio = _fuzzy_ratio(m_name, r_name)
        candidate = dict(record)
        candidate["matched_strategies"] = sorted(set(strategies))
        candidate["blocking_name_ratio"] = round(name_ratio, 2)
        candidates[record_id] = candidate

    ranked = sorted(
        candidates.values(),
        key=lambda item: (
            len(item.get("matched_strategies") or []),
            float(item.get("blocking_name_ratio") or 0.0),
            1 if "same_canonical_place" in (item.get("matched_strategies") or []) else 0,
        ),
        reverse=True,
    )
    return ranked[:max_candidates]
