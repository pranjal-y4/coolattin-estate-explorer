from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

try:
    from rapidfuzz import fuzz
except Exception:  # pragma: no cover
    fuzz = None

_MAX_POINTS = 60.0


def _ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if fuzz is not None:
        return float(fuzz.token_sort_ratio(a, b))
    return SequenceMatcher(None, a, b).ratio() * 100.0


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
    raw = 0.0

    m_name = mention.get("normalised_name") or ""
    c_name = candidate.get("normalised_name") or ""
    name_ratio = _ratio(m_name, c_name)
    if name_ratio >= 90:
        raw += 10.0
        evidence.append(f"Strong name similarity ({name_ratio:.0f}%)")
    elif name_ratio >= 75:
        raw += 7.0
        evidence.append(f"Good name similarity ({name_ratio:.0f}%)")
    elif name_ratio >= 60:
        raw += 4.0
        evidence.append(f"Partial name similarity ({name_ratio:.0f}%)")
    else:
        missing.append("No strong full-name match")

    if mention.get("surname") and mention.get("surname") == candidate.get("surname"):
        raw += 10.0
        evidence.append("Exact surname match")
    elif (
        mention.get("phonetic_surname")
        and mention.get("phonetic_surname") == candidate.get("phonetic_surname")
    ):
        raw += 7.0
        evidence.append("Phonetic surname match")
    else:
        missing.append("No surname match")

    m_fore = mention.get("forename") or ""
    c_fore = candidate.get("forename") or ""
    if not m_fore or not c_fore:
        raw += 5.0
        missing.append("Forename unknown on one side")
    else:
        fore_ratio = _ratio(m_fore, c_fore)
        if fore_ratio >= 90:
            raw += 10.0
            evidence.append("Exact forename match")
        elif fore_ratio >= 80:
            raw += 7.0
            evidence.append(f"Close forename match ({fore_ratio:.0f}%)")
        elif fore_ratio >= 60:
            raw += 4.0
            evidence.append(f"Partial forename similarity ({fore_ratio:.0f}%)")
        else:
            conflicts.append(
                f"Forename mismatch ({m_fore} vs {c_fore}; {fore_ratio:.0f}%)"
            )

    m_place = mention.get("normalised_place") or ""
    c_place = candidate.get("normalised_place") or ""
    if m_place and c_place and m_place == c_place:
        raw += 10.0
        evidence.append("Same canonical place")
    elif m_place and c_place and (m_place in c_place or c_place in m_place):
        raw += 6.0
        evidence.append("Variant or nearby place match")
    elif not m_place or not c_place:
        missing.append("Place data missing on one side")
    else:
        missing.append("Place mismatch (Electoral Division vs townland may differ)")

    m_birth = mention.get("inferred_birth_year")
    c_birth = candidate.get("inferred_birth_year")
    if m_birth and c_birth:
        birth_gap = abs(int(m_birth) - int(c_birth))
        if birth_gap <= 3:
            raw += 5.0
            evidence.append("Birth-year estimates closely aligned")
        elif birth_gap <= 8:
            raw += 3.0
            evidence.append("Birth-year estimates broadly compatible")
        elif birth_gap > 20:
            conflicts.append("Impossible age/date conflict")
    else:
        missing.append("Missing age or birth-year evidence")

    m_gender = (mention.get("gender") or "").upper()[:1]
    c_gender = (candidate.get("gender") or "").upper()[:1]
    if m_gender and c_gender:
        if m_gender == c_gender:
            raw += 10.0
            evidence.append("Gender match")
        else:
            conflicts.append(f"Gender mismatch ({m_gender} vs {c_gender})")
    else:
        raw += 5.0
        missing.append("Gender unknown on one or both sides")

    m_year = mention.get("event_year")
    c_year = candidate.get("event_year")
    m_age = mention.get("age")
    c_age = candidate.get("age")

    if m_year and c_year and m_age and c_age:
        year_gap = int(c_year) - int(m_year)
        expected_c_age = int(m_age) + year_gap
        age_diff = abs(expected_c_age - int(c_age))
        if age_diff <= 2:
            raw += 5.0
            evidence.append("Age progression consistent across records")
        elif age_diff <= 5:
            raw += 3.0
            evidence.append("Age progression broadly compatible")
        elif age_diff > 30:
            conflicts.append("Impossible timeline gap")
    elif m_year and c_year:
        gap = abs(int(m_year) - int(c_year))
        if gap <= 2:
            raw += 5.0
            evidence.append("Timeline strongly aligned")
        elif gap <= 10:
            raw += 2.5
            evidence.append("Timeline broadly aligned")
        elif gap > 40:
            conflicts.append("Impossible timeline gap")
    else:
        missing.append("No event-year evidence for timeline check")

    impossible = any(
        c in conflicts
        for c in ("Impossible age/date conflict", "Impossible timeline gap")
    )
    score = max(0.0, min(raw / _MAX_POINTS, 1.0))
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
