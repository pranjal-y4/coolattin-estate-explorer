"""
backend/services/entity_resolution/candidates.py

Candidate blocking and fuzzy candidate generation for workhouse mentions.
"""
from __future__ import annotations

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


def generate_candidates(
    mention: dict[str, Any],
    unified_records: list[dict[str, Any]],
    *,
    max_candidates: int = 25,
) -> list[dict[str, Any]]:
    """
    Generate candidate unified records for a workhouse mention.

    Candidate generation intentionally uses deterministic/fuzzy blocking rules,
    not embeddings.
    """
    candidates: dict[str, dict[str, Any]] = {}
    m_name = mention.get("normalised_name") or ""
    m_surname = mention.get("surname") or ""
    m_forename_initial = mention.get("forename_initial") or ""
    m_phonetic_surname = mention.get("phonetic_surname") or ""
    m_place = mention.get("normalised_place") or ""
    m_year = mention.get("event_year")

    for record in unified_records:
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

        candidate = dict(record)
        candidate["matched_strategies"] = sorted(set(strategies))
        candidate["blocking_name_ratio"] = round(_fuzzy_ratio(m_name, r_name), 2)
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
