"""
backend/services/entity_resolution/scoring.py

Transparent scoring for workhouse-to-unified-record candidate links.
"""
from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

try:
    from rapidfuzz import fuzz
except Exception:  # pragma: no cover - optional fallback
    fuzz = None


def _ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if fuzz is not None:
        return float(fuzz.token_sort_ratio(a, b))
    return SequenceMatcher(None, a, b).ratio() * 100.0


def _token_overlap(a: str, b: str) -> float:
    sa = {tok for tok in (a or "").split() if tok}
    sb = {tok for tok in (b or "").split() if tok}
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / max(len(sa | sb), 1)


@dataclass
class ScoreResult:
    score: float
    label: str
    evidence: list[str]
    conflicts: list[str]
    missing_evidence: list[str]


def score_candidate(mention: dict[str, Any], candidate: dict[str, Any]) -> ScoreResult:
    evidence: list[str] = []
    conflicts: list[str] = []
    missing: list[str] = []

    raw_points = 0.0

    name_ratio = _ratio(mention.get("normalised_name") or "", candidate.get("normalised_name") or "")
    name_points = min(25.0, (name_ratio / 100.0) * 25.0)
    raw_points += name_points
    if name_points >= 20:
        evidence.append(f"Strong name similarity ({name_ratio:.0f}%)")
    elif name_points > 0:
        evidence.append(f"Partial name similarity ({name_ratio:.0f}%)")
    else:
        missing.append("No strong full-name match")

    surname_points = 0.0
    if mention.get("surname") and mention.get("surname") == candidate.get("surname"):
        surname_points = 15.0
        evidence.append("Exact surname match")
    elif (
        mention.get("phonetic_surname")
        and mention.get("phonetic_surname") == candidate.get("phonetic_surname")
    ):
        surname_points = 11.0
        evidence.append("Phonetic surname match")
    else:
        missing.append("No exact or phonetic surname match")
    raw_points += surname_points

    # Forename compatibility: when both forenames are known and clearly different,
    # subtract points equal to the surname bonus so that surname+place homonyms
    # cannot be confirmed on surname alone. The penalty only fires when the
    # forename ratio is well below the typical variant threshold (Jno/John = 100%,
    # Judy/Judith ≈ 73% — safe; Peter/Mary-Anne ≈ 17% — flagged).
    m_fore = mention.get("forename") or ""
    c_fore = candidate.get("forename") or ""
    if m_fore and c_fore:
        forename_ratio = _ratio(m_fore, c_fore)
        if forename_ratio < 60.0:
            raw_points -= 15.0
            conflicts.append(
                f"Forename mismatch ({m_fore} vs {c_fore}; {forename_ratio:.0f}% similarity)"
            )

    place_points = 0.0
    m_place = mention.get("normalised_place") or ""
    c_place = candidate.get("normalised_place") or ""
    if m_place and c_place and m_place == c_place:
        place_points = 20.0
        evidence.append("Same canonical place")
    elif m_place and c_place and (m_place in c_place or c_place in m_place):
        place_points = 12.0
        evidence.append("Variant or nearby place match")
    elif m_place and c_place:
        # Places are known but don't match — genuine geographic conflict.
        # Workhouse uses Electoral Division; estate records use townland.
        # Only flag as a conflict when both sides have resolvable place data.
        conflicts.append("Place mismatch (ED vs townland may be incompatible)")
    # When either side has no place data, neither a bonus nor a conflict applies.
    raw_points += place_points

    age_points = 0.0
    m_birth = mention.get("inferred_birth_year")
    c_birth = candidate.get("inferred_birth_year")
    if m_birth and c_birth:
        birth_gap = abs(int(m_birth) - int(c_birth))
        if birth_gap <= 3:
            age_points = 15.0
            evidence.append("Birth-year estimates are closely aligned")
        elif birth_gap <= 8:
            age_points = 9.0
            evidence.append("Birth-year estimates are broadly compatible")
        elif birth_gap > 20:
            conflicts.append("Impossible age/date conflict")
    else:
        missing.append("Missing age or birth-year evidence")
    raw_points += age_points

    household_points = 0.0
    overlap = _token_overlap(
        mention.get("household_fields") or "",
        candidate.get("household_fields") or "",
    )
    if overlap >= 0.5:
        household_points = 15.0
        evidence.append("Household or family overlap")
    elif overlap > 0.0:
        household_points = 7.0
        evidence.append("Weak household overlap")
    else:
        missing.append("No household overlap evidence")
    raw_points += household_points

    occupation_points = 0.0
    occ_overlap = _token_overlap(
        mention.get("occupation_norm") or "",
        candidate.get("occupation_norm") or "",
    )
    if occ_overlap > 0.0:
        occupation_points = 5.0
        evidence.append("Occupation similarity")
    else:
        missing.append("No occupation similarity")
    raw_points += occupation_points

    timeline_points = 0.0
    m_year = mention.get("event_year")
    c_year = candidate.get("event_year")
    if m_year and c_year:
        gap = abs(int(m_year) - int(c_year))
        if gap <= 2:
            timeline_points = 5.0
            evidence.append("Timeline strongly aligned")
        elif gap <= 10:
            timeline_points = 2.5
            evidence.append("Timeline broadly aligned")
        elif gap > 40:
            conflicts.append("Impossible timeline gap")
    else:
        missing.append("No event-year evidence")
    raw_points += timeline_points

    if mention.get("gender") and candidate.get("gender"):
        if str(mention["gender"]).upper()[:1] != str(candidate["gender"]).upper()[:1]:
            conflicts.append("Incompatible gender")

    impossible = any(
        conflict in conflicts
        for conflict in ("Impossible age/date conflict", "Impossible timeline gap", "Incompatible gender")
    )
    score = max(0.0, min(raw_points / 100.0, 1.0))
    if impossible:
        score = min(score, 0.39)

    if score >= 0.75:
        label = "CONFIRMED_MATCH"
    elif score >= 0.60:
        label = "POSSIBLE_MATCH"
    elif score >= 0.40:
        label = "WEAK_CANDIDATE"
    else:
        label = "NO_MATCH"

    return ScoreResult(
        score=round(score, 4),
        label=label,
        evidence=evidence,
        conflicts=conflicts,
        missing_evidence=missing,
    )
