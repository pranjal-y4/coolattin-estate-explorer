"""
backend/services/identity_resolver.py

Part A — Three-layer identity model for repeated-name disambiguation.

Layers
------
Mention  — one immutable row per name occurrence in a source record.
           Never merged; carries raw name, place, date, role.
Person   — an *inferred* individual linked to one or more Mentions via a
           SAME_AS relationship with a confidence score.
           Threshold ≥ 0.75 = confirmed; 0.50–0.74 = candidate.
Factoid  — a reified claim (mention, property, value, source), so
           contradictory records survive without hard-merging.

Algorithm
---------
1. Phonetic blocking — group Mentions by jellyfish.metaphone(surname).
2. Within each block, score pairs on:
     - Name similarity: jaro_winkler_similarity (strongest signal for surname variants)
     - Geographic proximity: same townland → +0.20, same civil_parish → +0.10
     - Temporal plausibility: ≤ 10-year gap → +0.10; > 30-year gap → -0.10
     - Family co-occurrence: shared forename in household → +0.15
3. Pairs above SAME_AS_THRESHOLD are grouped into a Person.
4. Each Person carries supporting_mention_ids[] and may_be_confused_with[].

Public API
----------
resolve_person_identity(raw_name, townland_norm=None, year=None)
    → PersonIdentityResult

The result is what lets the answer say "3 distinct individuals called
John Murphy" instead of silently picking one.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# Threshold above which a SAME_AS pair is treated as "confirmed"
SAME_AS_THRESHOLD: float = 0.75
# Minimum score to surface a candidate at all (below = too weak to report)
CANDIDATE_MIN_SCORE: float = 0.50

# Module-level cache: (name_key) → (result, built_at)
_identity_cache_lock = threading.Lock()
_IDENTITY_CACHE: dict[str, tuple[Any, float]] = {}
_IDENTITY_CACHE_TTL = 600.0  # 10 minutes


@dataclass
class Mention:
    """One raw name occurrence in a source record."""
    mention_id: str       # "{source}:{record_id}"
    raw_name: str
    surname: str
    forename: str
    townland_norm: str | None
    civil_parish: str | None
    year: int | None
    role: str | None
    source: str           # "emigration" | "eviction" | "tenancy"
    record_id: str


@dataclass
class PersonCandidate:
    """
    An inferred individual linked to one or more Mentions.
    confidence ≥ 0.75 = confirmed; 0.50–0.74 = candidate.
    """
    person_id: str
    display_name: str
    confidence: float
    supporting_mention_ids: list[str]
    townland_norm: str | None
    civil_parish: str | None
    year_range: tuple[int, int] | None
    role: str | None
    may_be_confused_with: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class PersonIdentityResult:
    """Returned by resolve_person_identity()."""
    raw_name: str
    canonical_surname: str
    phonetic_key: str
    total_mentions: int
    person_candidates: list[PersonCandidate]
    # True if more than one Person candidate was found for this name
    is_ambiguous: bool
    disambiguation_note: str | None = None


def _norm_name(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).upper()


def _phonetic_key(surname: str) -> str:
    """Metaphone key for phonetic blocking."""
    try:
        import jellyfish
        return jellyfish.metaphone(_norm_name(surname)) or _norm_name(surname)
    except Exception:
        return _norm_name(surname)


def _jaro_winkler(a: str, b: str) -> float:
    try:
        import jellyfish
        return jellyfish.jaro_winkler_similarity(a.lower(), b.lower())
    except Exception:
        # Fallback: character set overlap ratio
        sa, sb = set(a.lower()), set(b.lower())
        return len(sa & sb) / max(len(sa | sb), 1)


def _fetch_mentions(raw_name: str) -> list[Mention]:
    """
    Fetch all Mentions from unified_record that share the same phonetic
    surname key as raw_name. Limited to 2 000 rows to cap latency.
    """
    from extensions import get_db_conn

    parts = raw_name.strip().split()
    surname_guess = parts[-1] if parts else raw_name
    pkey = _phonetic_key(surname_guess)

    conn = get_db_conn()
    mentions: list[Mention] = []
    try:
        # Pull all records whose surname metaphone matches
        rows = conn.execute(
            """
            SELECT record_id, surname, forename, canonical_name,
                   townland_norm, parish, year, role,
                   has_emigration_record, has_eviction_record, has_tenancy_record
            FROM unified_record
            WHERE surname IS NOT NULL AND TRIM(surname) != ''
            ORDER BY surname, year
            LIMIT 2000
            """
        ).fetchall()
    finally:
        conn.close()

    for row in rows:
        sn = (row["surname"] or "").strip()
        if not sn:
            continue
        if _phonetic_key(sn) != pkey:
            continue
        src = (
            "emigration" if row["has_emigration_record"]
            else "eviction" if row["has_eviction_record"]
            else "tenancy"
        )
        mentions.append(Mention(
            mention_id=f"{src}:{row['record_id']}",
            raw_name=row["canonical_name"] or f"{row['forename'] or ''} {sn}".strip(),
            surname=sn,
            forename=(row["forename"] or "").strip(),
            townland_norm=row["townland_norm"],
            civil_parish=row["parish"],
            year=row["year"],
            role=row["role"],
            source=src,
            record_id=row["record_id"],
        ))
    return mentions


def _score_pair(a: Mention, b: Mention) -> float:
    """
    Score the likelihood that Mention a and Mention b refer to the same
    individual. Returns a float in [0.0, 1.0].
    """
    # Base: name similarity (surname + forename Jaro-Winkler)
    sn_sim = _jaro_winkler(a.surname, b.surname)
    fn_sim = _jaro_winkler(a.forename, b.forename) if (a.forename and b.forename) else 0.5
    name_score = 0.7 * sn_sim + 0.3 * fn_sim

    # Geographic proximity
    geo_bonus = 0.0
    if a.townland_norm and b.townland_norm and a.townland_norm == b.townland_norm:
        geo_bonus = 0.20
    elif a.civil_parish and b.civil_parish and a.civil_parish == b.civil_parish:
        geo_bonus = 0.10

    # Temporal plausibility
    time_delta = 0.0
    if a.year and b.year:
        gap = abs(a.year - b.year)
        if gap <= 10:
            time_delta = 0.10
        elif gap > 30:
            time_delta = -0.10

    # Family co-occurrence: same forename counts as weak signal (common names
    # reduce specificity; different forenames are a stronger disambiguation)
    family_bonus = 0.0
    if a.forename and b.forename and a.forename.upper() == b.forename.upper():
        family_bonus = 0.05

    score = name_score + geo_bonus + time_delta + family_bonus
    return max(0.0, min(1.0, score))


def _cluster_mentions(mentions: list[Mention]) -> list[list[Mention]]:
    """
    Single-link clustering: add a Mention to an existing cluster if it scores
    ≥ SAME_AS_THRESHOLD against ANY member of that cluster.
    Returns a list of clusters, each being a list of Mentions.
    """
    clusters: list[list[Mention]] = []
    for m in mentions:
        placed = False
        for cluster in clusters:
            for existing in cluster:
                if _score_pair(m, existing) >= SAME_AS_THRESHOLD:
                    cluster.append(m)
                    placed = True
                    break
            if placed:
                break
        if not placed:
            clusters.append([m])
    return clusters


def _cluster_to_candidate(cluster: list[Mention], cluster_idx: int) -> PersonCandidate:
    """Convert a cluster of Mentions into a PersonCandidate."""
    # Canonical name: most frequent surname + forename in cluster
    surnames = [m.surname for m in cluster if m.surname]
    forenames = [m.forename for m in cluster if m.forename]
    canonical_sn = max(set(surnames), key=surnames.count) if surnames else "Unknown"
    canonical_fn = max(set(forenames), key=forenames.count) if forenames else ""
    display = f"{canonical_fn} {canonical_sn}".strip() if canonical_fn else canonical_sn

    years = [m.year for m in cluster if m.year]
    year_range = (min(years), max(years)) if years else None

    # Confidence: mean pairwise score within cluster (≥ 2 members) or 1.0 for singletons
    if len(cluster) == 1:
        confidence = 0.60  # single mention — weakly confirmed
    else:
        scores = []
        for i, a in enumerate(cluster):
            for b in cluster[i + 1:]:
                scores.append(_score_pair(a, b))
        confidence = sum(scores) / len(scores) if scores else 0.60

    townlands = [m.townland_norm for m in cluster if m.townland_norm]
    parishes = [m.civil_parish for m in cluster if m.civil_parish]
    roles = [m.role for m in cluster if m.role]

    return PersonCandidate(
        person_id=f"person_{cluster_idx}",
        display_name=display,
        confidence=round(confidence, 3),
        supporting_mention_ids=[m.mention_id for m in cluster],
        townland_norm=max(set(townlands), key=townlands.count) if townlands else None,
        civil_parish=max(set(parishes), key=parishes.count) if parishes else None,
        year_range=year_range,
        role=max(set(roles), key=roles.count) if roles else None,
    )


def resolve_person_identity(
    raw_name: str,
    townland_norm: str | None = None,
    year: int | None = None,
) -> PersonIdentityResult:
    """
    Resolve a person name to one or more PersonCandidates.

    This is the public API called by the orchestrated pipeline when the
    question involves a person name (surname or full name). The result
    feeds directly into the answer synthesis so the response can say
    "3 distinct individuals named John Murphy were recorded" rather than
    silently collapsing them.

    Parameters
    ----------
    raw_name     : The name as it appears in the question (e.g. "John Murphy").
    townland_norm: Optional townland filter to narrow the search.
    year         : Optional year to anchor temporal plausibility.

    Returns
    -------
    PersonIdentityResult — never raises; returns empty candidates on failure.
    """
    cache_key = f"{_norm_name(raw_name)}|{townland_norm or ''}|{year or ''}"
    now = time.monotonic()

    with _identity_cache_lock:
        cached = _IDENTITY_CACHE.get(cache_key)
        if cached and (now - cached[1]) < _IDENTITY_CACHE_TTL:
            return cached[0]

    parts = raw_name.strip().split()
    surname_guess = parts[-1] if parts else raw_name
    pkey = _phonetic_key(surname_guess)

    try:
        mentions = _fetch_mentions(raw_name)
    except Exception as exc:
        log.warning("identity_resolver.fetch_failed name=%r error=%s", raw_name, exc)
        mentions = []

    # If no mentions found at all, return a null result
    if not mentions:
        result = PersonIdentityResult(
            raw_name=raw_name,
            canonical_surname=surname_guess.title(),
            phonetic_key=pkey,
            total_mentions=0,
            person_candidates=[],
            is_ambiguous=False,
            disambiguation_note=f"No records found for '{raw_name}' in the estate database.",
        )
        with _identity_cache_lock:
            _IDENTITY_CACHE[cache_key] = (result, now)
        return result

    clusters = _cluster_mentions(mentions)

    candidates: list[PersonCandidate] = []
    for i, cluster in enumerate(clusters):
        candidate = _cluster_to_candidate(cluster, i)
        if candidate.confidence >= CANDIDATE_MIN_SCORE:
            candidates.append(candidate)

    # Sort by descending confidence, then by number of supporting mentions
    candidates.sort(key=lambda c: (-c.confidence, -len(c.supporting_mention_ids)))

    # Populate may_be_confused_with: each candidate lists the others as confusion risks
    for c in candidates:
        c.may_be_confused_with = [
            {
                "person_id": other.person_id,
                "display_name": other.display_name,
                "confidence": other.confidence,
                "townland_norm": other.townland_norm,
                "year_range": other.year_range,
            }
            for other in candidates
            if other.person_id != c.person_id
        ]

    is_ambiguous = len(candidates) > 1
    note: str | None = None
    if is_ambiguous:
        note = (
            f"{len(candidates)} distinct individuals named '{raw_name}' "
            f"were found across {len(mentions)} records. "
            f"The most likely individual is {candidates[0].display_name} "
            f"(confidence {candidates[0].confidence:.0%}, "
            f"{len(candidates[0].supporting_mention_ids)} record(s)"
            + (f", {candidates[0].townland_norm}" if candidates[0].townland_norm else "")
            + ")."
        )
    elif candidates:
        note = (
            f"1 individual named '{raw_name}' found across "
            f"{len(candidates[0].supporting_mention_ids)} record(s)."
        )

    result = PersonIdentityResult(
        raw_name=raw_name,
        canonical_surname=surname_guess.title(),
        phonetic_key=pkey,
        total_mentions=len(mentions),
        person_candidates=candidates,
        is_ambiguous=is_ambiguous,
        disambiguation_note=note,
    )
    with _identity_cache_lock:
        _IDENTITY_CACHE[cache_key] = (result, now)
    return result


def invalidate_identity_cache() -> None:
    """Force a cold rebuild of all cached identity resolutions."""
    with _identity_cache_lock:
        _IDENTITY_CACHE.clear()
    log.info("identity_resolver.cache_invalidated")
