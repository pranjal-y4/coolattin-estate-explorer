"""
backend/services/townland_resolution.py

Source-townland entity resolution.

Takes one source townland record (a GeoJSON feature, a KG hit, a reference-file
row, a manual entry) and returns the canonical townland entity it belongs to,
creating that entity only when nothing existing credibly matches.

Flow (one source record = one transaction):

    source record
      → symmetric normalisation        (townland_service.normalize_townland_name)
      → xref replay / exact / alias    (townland_xref, townland.name, alias layer)
      → candidate generation           (blocking on county + name prefix)
      → shared authority identifier    (kg_uri, vrti_id, osi_id, osm_id, logainm_id, …)
      → independent corroboration      (townland_service.score_pair / decide_match)
      → merge | review | new canonical
      → townland_xref + field_provenance

Scoring, thresholds and the three-band decision are townland_service's — this
module only sequences them and persists the outcome.  A fuzzy name score alone
never merges: decide_match() requires an authority ID, polygon overlap, or
administrative agreement as corroboration.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from backend.repositories import match_review_repository, townland_repository
from backend.repositories.townland_repository import AUTHORITY_ID_COLUMNS
from backend.services.townland_service import (
    canonical_name,
    decide_match,
    extract_qualifier,
    normalize_townland_name,
    resolve_alias,
    resolve_compound,
    score_pair,
    validate_and_clean_geometry,
)
from extensions import get_db_conn

log = logging.getLogger(__name__)

# Fields a source record may contribute to a canonical townland.
_CONTRIBUTABLE_FIELDS: tuple[str, ...] = (
    "name_gaelic", "qualifier", "barony", "civil_parish", "electoral_division",
    "county", "area_sqm", "wkt_geometry", "centroid_lat", "centroid_lon",
    "geometry_flag", "kg_uri", "osm_id", "osi_id", "vrti_id", "logainm_id",
    "td_id", "guid",
)


@dataclass
class SourceTownland:
    """One townland record as it arrives from a source dataset."""
    name: str
    source: str                       # 'geojson' | 'kg' | 'reference' | 'manual' | …
    source_record_id: str             # TD_ID, kg_uri, townlands.ie URL, …
    name_gaelic: Optional[str] = None
    barony: Optional[str] = None
    civil_parish: Optional[str] = None
    electoral_division: Optional[str] = None
    county: Optional[str] = None
    area_sqm: Optional[float] = None
    wkt_geometry: Optional[str] = None
    centroid_lat: Optional[float] = None
    centroid_lon: Optional[float] = None
    kg_uri: Optional[str] = None
    osm_id: Optional[str] = None
    osi_id: Optional[str] = None
    vrti_id: Optional[str] = None
    logainm_id: Optional[str] = None
    td_id: Optional[str] = None
    guid: Optional[str] = None

    def as_record(self) -> dict:
        """Comparison dict for townland_service.score_pair()."""
        return asdict(self)


@dataclass
class Resolution:
    """Outcome of resolving one source record."""
    status: str                       # matched | created | review | ambiguous | skipped
    method: str                       # xref_replay | exact | alias | authority_id | …
    source_name: str
    normalised_name: str
    canonical_name: Optional[str] = None
    entity_id: Optional[str] = None
    townland_id: Optional[int] = None
    confidence: float = 0.0
    has_geometry: bool = False
    review_id: Optional[int] = None
    candidates: list[dict] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    fields_filled: list[str] = field(default_factory=list)


def geojson_geometry_to_wkt(geometry: dict | None) -> Optional[str]:
    """Convert a GeoJSON geometry object to WKT. Returns None if unusable."""
    if not geometry:
        return None
    try:
        from shapely.geometry import shape as _shape
        return _shape(geometry).wkt
    except Exception as exc:
        log.debug("townland_resolution.wkt_conversion_failed | error=%s", exc)
        return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def resolve_source_townland(
    src: SourceTownland, conn=None, allow_create: bool = True
) -> Resolution:
    """
    Resolve one source townland to a canonical entity and persist the outcome.

    Runs in a single transaction: either the canonical assignment, the
    cross-reference and the provenance rows all land, or none of them do.
    Safe to run repeatedly — the same source record always resolves to the
    same canonical entity and updates the same xref row.

    `allow_create=False` restricts the source record to enriching townlands the
    database already holds: when nothing matches it is reported as unmatched and
    nothing is written.  Use it for records that carry supplementary detail
    (geometry, identifiers) rather than asserting a place exists.
    """
    own_conn = conn is None
    c = conn if conn is not None else get_db_conn()
    try:
        if own_conn:
            c.execute("BEGIN")
        result = _resolve(src, c, allow_create=allow_create)
        if own_conn:
            c.commit()
        return result
    except Exception:
        if own_conn:
            c.rollback()
        log.exception(
            "townland_resolution.failed | source=%s record=%s name=%r",
            src.source, src.source_record_id, src.name,
        )
        raise
    finally:
        if own_conn:
            c.close()


# ---------------------------------------------------------------------------
# Resolution steps
# ---------------------------------------------------------------------------

def _resolve(src: SourceTownland, c, allow_create: bool = True) -> Resolution:
    # ---- Step 2: symmetric normalisation --------------------------------
    name_norm = normalize_townland_name(src.name)
    if not name_norm or not src.source_record_id:
        return Resolution(
            status="skipped", method="no_name",
            source_name=src.name, normalised_name=name_norm,
            missing_evidence=["usable source name or source_record_id"],
        )

    src = _with_validated_geometry(src)
    alias_target = resolve_alias(name_norm)
    compound_parts = resolve_compound(name_norm)

    # ---- Step 3: alias + exact catalogue lookup -------------------------
    existing_xref = match_review_repository.find_xref(
        src.source, src.source_record_id, conn=c
    )
    if existing_xref and existing_xref.get("status") == "confirmed":
        row = _row_by_entity_id(c, existing_xref["entity_id"])
        if row:
            return _attach(
                src, row, c,
                method="xref_replay",
                confidence=float(existing_xref.get("confidence") or 1.0),
                name_norm=name_norm,
                evidence=[f"existing xref {src.source}:{src.source_record_id}"],
            )

    # A compound source name covers several townlands — no single canonical
    # target exists, so it is never auto-resolved.
    if len(compound_parts) > 1:
        if not allow_create:
            return _unmatched(src, name_norm, ["compound source name"])
        return _record_compound(src, c, name_norm, compound_parts)

    # A shared name is not a shared place: Irish townland names repeat across
    # counties, so an exact or alias hit whose administrative context explicitly
    # disagrees is treated as a different townland, not a match.
    name_conflicts: list[str] = []

    lookup_name = compound_parts[0] if compound_parts else alias_target
    row = townland_repository.find_row_by_name(name_norm, conn=c)
    if row:
        clash = _hierarchy_conflict(src, row)
        if not clash:
            return _attach(
                src, row, c, method="exact", confidence=1.0, name_norm=name_norm,
                evidence=["exact canonical name match"],
            )
        name_conflicts.append(f"exact name match rejected: {clash}")

    if lookup_name != name_norm:
        row = townland_repository.find_row_by_name(lookup_name, conn=c)
        if row:
            clash = _hierarchy_conflict(src, row)
            if not clash:
                return _attach(
                    src, row, c, method="alias", confidence=0.98, name_norm=name_norm,
                    evidence=[f"curated alias {name_norm} → {lookup_name}"],
                )
            name_conflicts.append(f"alias match rejected: {clash}")

    # ---- Step 5: shared authority identifier ----------------------------
    authority_hit, authority_conflicts = _authority_match(src, c)
    authority_conflicts = name_conflicts + authority_conflicts
    if authority_hit:
        column, row = authority_hit
        return _attach(
            src, row, c, method="authority_id", confidence=0.99, name_norm=name_norm,
            evidence=[f"shared authority identifier {column}={src.as_record()[column]}"],
            conflicts=authority_conflicts,
        )

    # ---- Step 4 + 6: candidates and independent corroboration -----------
    scored = _score_candidates(src, name_norm, lookup_name, c)
    candidates = [
        {
            "townland_id": r["id"],
            "entity_id": r["entity_id"],
            "name": r["name"],
            "score": round(f.score, 4),
            "decision": decide_match(f),
            "features": f.to_dict(),
        }
        for r, f in scored[:5]
    ]

    if scored:
        best_row, best_features = scored[0]
        decision = decide_match(best_features)
        if decision == "merge":
            return _attach(
                src, best_row, c, method="corroborated", name_norm=name_norm,
                confidence=round(best_features.score, 4),
                evidence=_evidence_from_features(best_features),
                conflicts=authority_conflicts,
                candidates=candidates,
            )
        if decision == "review":
            if not allow_create:
                return _unmatched(
                    src, name_norm,
                    [f"best candidate {best_row['name']} needs review"],
                    candidates=candidates,
                )
            return _record_review(
                src, c, name_norm, best_row, best_features, candidates,
                authority_conflicts,
            )

    # ---- Step 10: genuinely new canonical townland ----------------------
    if not allow_create:
        return _unmatched(
            src, name_norm,
            ["no canonical townland matched and creation is disabled"],
            candidates=candidates,
            conflicts=authority_conflicts,
        )

    # Created under the alias-resolved name, so a variant spelling seen first
    # does not become the canonical form and split the entity in two.
    return _create_canonical(
        src, c, name_norm,
        canonical=lookup_name,
        candidates=candidates,
        conflicts=authority_conflicts,
        reason="no candidate reached the review threshold" if scored
               else "no candidate townland generated",
    )


# ---------------------------------------------------------------------------
# Outcome handlers
# ---------------------------------------------------------------------------

def _unmatched(
    src: SourceTownland,
    name_norm: str,
    reasons: list[str],
    candidates: Optional[list[dict]] = None,
    conflicts: Optional[list[str]] = None,
) -> Resolution:
    """Nothing matched and this source record may not create a townland."""
    log.info(
        "townland_resolution.unmatched | name=%s source=%s:%s reasons=%s",
        name_norm, src.source, src.source_record_id, reasons,
    )
    return Resolution(
        status="unmatched",
        method="no_match",
        source_name=src.name,
        normalised_name=name_norm,
        candidates=candidates or [],
        conflicts=conflicts or [],
        missing_evidence=reasons,
    )

def _attach(
    src: SourceTownland,
    row: dict,
    c,
    *,
    method: str,
    confidence: float,
    name_norm: str,
    evidence: Optional[list[str]] = None,
    conflicts: Optional[list[str]] = None,
    candidates: Optional[list[dict]] = None,
) -> Resolution:
    """Step 8 + 9: reuse the canonical entity, cross-reference it, keep provenance."""
    entity_id = row["entity_id"] or _backfill_entity_id(c, row)
    filled = townland_repository.enrich_row(row["id"], _contributions(src), conn=c)

    match_review_repository.add_xref(
        entity_id=entity_id,
        source=src.source,
        source_record_id=src.source_record_id,
        confidence=confidence,
        match_method=method,
        source_name=src.name,
        status="confirmed",
        evidence=evidence or [],
        conflicts=conflicts or [],
        conn=c,
    )
    _write_provenance(src, entity_id, filled, c)

    refreshed = _row_by_id(c, row["id"]) or row
    return Resolution(
        status="matched",
        method=method,
        source_name=src.name,
        normalised_name=name_norm,
        canonical_name=refreshed["name"],
        entity_id=entity_id,
        townland_id=int(row["id"]),
        confidence=confidence,
        has_geometry=bool(refreshed.get("wkt_geometry")),
        evidence=evidence or [],
        conflicts=conflicts or [],
        candidates=candidates or [],
        fields_filled=filled,
    )


def _create_canonical(
    src: SourceTownland,
    c,
    name_norm: str,
    *,
    canonical: Optional[str] = None,
    candidates: Optional[list[dict]] = None,
    conflicts: Optional[list[str]] = None,
    reason: str = "",
    status: str = "created",
    method: str = "new_canonical",
    xref_status: str = "confirmed",
) -> Resolution:
    """
    Step 10: create a new canonical townland.

    `canonical` is the name the entity is created under — the alias-resolved
    form when the source spelling is a known variant, otherwise the normalised
    source name.  Re-checks the exact, alias and xref lookups immediately
    before inserting so a concurrent or repeated run cannot produce a second
    canonical entity for the same place.
    """
    canonical_name_value = canonical or resolve_alias(name_norm)
    guard = (
        townland_repository.find_row_by_name(name_norm, conn=c)
        or townland_repository.find_row_by_name(canonical_name_value, conn=c)
    )
    if guard is not None and _hierarchy_conflict(src, guard):
        # Same name, different place — the re-check must not undo the conflict
        # decision that sent this record down the create path.
        guard = None
    if guard:
        return _attach(
            src, guard, c, method="exact_recheck", confidence=1.0,
            name_norm=name_norm, evidence=["canonical name existed on re-check"],
        )
    existing_xref = match_review_repository.find_xref(
        src.source, src.source_record_id, conn=c
    )
    if existing_xref:
        row = _row_by_entity_id(c, existing_xref["entity_id"])
        if row:
            return _attach(
                src, row, c, method="xref_recheck",
                confidence=float(existing_xref.get("confidence") or 1.0),
                name_norm=name_norm, evidence=["xref existed on re-check"],
            )

    fields = _contributions(src)
    fields["name"] = canonical_name_value
    fields["source"] = src.source
    townland_id, entity_id = townland_repository.insert_canonical(fields, conn=c)

    evidence = [reason] if reason else []
    if canonical_name_value != name_norm:
        evidence.append(f"canonical name taken from alias layer: {name_norm} → {canonical_name_value}")
    match_review_repository.add_xref(
        entity_id=entity_id,
        source=src.source,
        source_record_id=src.source_record_id,
        confidence=1.0,
        match_method=method,
        source_name=src.name,
        status=xref_status,
        evidence=evidence,
        conflicts=conflicts or [],
        conn=c,
    )
    match_review_repository.record_provenance_if_absent(
        entity_id, "name", canonical_name_value, src.source, src.source_record_id,
        rule="canonical_created", conn=c,
    )
    _write_provenance(src, entity_id, list(fields.keys()), c)

    has_geometry = bool(fields.get("wkt_geometry"))
    if not has_geometry:
        log.info(
            "townland_resolution.no_geometry | name=%s entity_id=%s source=%s:%s",
            canonical_name_value, entity_id, src.source, src.source_record_id,
        )

    return Resolution(
        status=status,
        method=method,
        source_name=src.name,
        normalised_name=name_norm,
        canonical_name=canonical_name_value,
        entity_id=entity_id,
        townland_id=townland_id,
        confidence=1.0,
        has_geometry=has_geometry,
        candidates=candidates or [],
        evidence=evidence,
        conflicts=conflicts or [],
        missing_evidence=[] if has_geometry else ["geometry"],
    )


def _record_review(
    src: SourceTownland,
    c,
    name_norm: str,
    best_row: dict,
    best_features,
    candidates: list[dict],
    conflicts: list[str],
) -> Resolution:
    """
    Step 7: ambiguous match.

    The source record becomes its own canonical entity — it is NOT merged — and
    the pair is queued for review.  Confirming the review through
    match_review_repository.apply_decision() collapses the two entities onto one
    entity_id; rejecting it leaves them separate.
    """
    result = _create_canonical(
        src, c, name_norm,
        candidates=candidates,
        conflicts=conflicts,
        reason=(
            f"ambiguous against {best_row['name']} "
            f"(score {best_features.score:.2f}, no independent corroboration)"
        ),
        status="review",
        method="pending_review",
        xref_status="pending",
    )
    if result.status != "review" or result.townland_id is None:
        return result

    review_id = match_review_repository.enqueue(
        townland_id_a=result.townland_id,
        townland_id_b=int(best_row["id"]),
        score=round(best_features.score, 4),
        features={
            "source": src.source,
            "source_record_id": src.source_record_id,
            "source_name": src.name,
            "normalised_name": name_norm,
            "candidate_name": best_row["name"],
            "features": best_features.to_dict(),
            "candidates": candidates,
            "conflicts": conflicts,
            "missing_evidence": _missing_evidence(best_features),
        },
        conn=c,
    )
    result.review_id = review_id
    result.missing_evidence = _missing_evidence(best_features)
    log.info(
        "townland_resolution.review_queued | name=%s candidate=%s score=%.2f review_id=%s",
        name_norm, best_row["name"], best_features.score, review_id,
    )
    return result


def _record_compound(
    src: SourceTownland,
    c,
    name_norm: str,
    parts: list[str],
) -> Resolution:
    """
    A compound source name ("Ballard And Crone") spans several townlands.

    No canonical entity is created or merged.  The source record is filed
    against the first resolvable part with status 'pending' so the link is
    visible for review and the observed spelling is not lost.
    """
    part_rows = [
        (p, townland_repository.find_row_by_name(p, conn=c))
        for p in parts
    ]
    known = [(p, r) for p, r in part_rows if r]
    missing = [p for p, r in part_rows if not r]

    evidence = [f"compound source name covering {', '.join(parts)}"]
    if not known:
        return Resolution(
            status="ambiguous", method="compound_unresolved",
            source_name=src.name, normalised_name=name_norm,
            candidates=[{"name": p} for p in parts],
            evidence=evidence,
            missing_evidence=[f"no canonical townland for {p}" for p in missing],
        )

    anchor_name, anchor_row = known[0]
    entity_id = anchor_row["entity_id"] or _backfill_entity_id(c, anchor_row)
    match_review_repository.add_xref(
        entity_id=entity_id,
        source=src.source,
        source_record_id=src.source_record_id,
        confidence=0.0,
        match_method="compound_ambiguous",
        source_name=src.name,
        status="pending",
        evidence=evidence,
        conflicts=[f"source name also covers {p}" for p, _ in known[1:]],
        conn=c,
    )
    return Resolution(
        status="ambiguous",
        method="compound_ambiguous",
        source_name=src.name,
        normalised_name=name_norm,
        canonical_name=anchor_row["name"],
        entity_id=entity_id,
        townland_id=int(anchor_row["id"]),
        confidence=0.0,
        has_geometry=bool(anchor_row.get("wkt_geometry")),
        candidates=[
            {"townland_id": r["id"], "entity_id": r["entity_id"], "name": r["name"]}
            for _, r in known
        ],
        evidence=evidence,
        conflicts=[f"source name also covers {p}" for p, _ in known[1:]],
        missing_evidence=[f"no canonical townland for {p}" for p in missing],
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _with_validated_geometry(src: SourceTownland) -> SourceTownland:
    """Validate/repair source WKT and derive a centroid guaranteed to sit inside it."""
    if not src.wkt_geometry:
        return src
    geom = validate_and_clean_geometry(src.wkt_geometry)
    if geom.valid:
        src.wkt_geometry = geom.wkt
        src.centroid_lat = src.centroid_lat if src.centroid_lat is not None else geom.centroid_lat
        src.centroid_lon = src.centroid_lon if src.centroid_lon is not None else geom.centroid_lon
    elif geom.wkt is None:
        log.warning(
            "townland_resolution.geometry_unusable | name=%r flags=%s", src.name, geom.flags
        )
        src.wkt_geometry = None
    return src


def _contributions(src: SourceTownland) -> dict[str, Any]:
    """Non-empty field values this source record can contribute."""
    record = src.as_record()
    out = {k: record.get(k) for k in _CONTRIBUTABLE_FIELDS if record.get(k) not in (None, "")}
    qualifier = extract_qualifier(src.name)
    if qualifier:
        out["qualifier"] = qualifier
    geom_flags = validate_and_clean_geometry(src.wkt_geometry).flags if src.wkt_geometry else []
    if geom_flags:
        out["geometry_flag"] = ",".join(geom_flags)
    return out


def _authority_match(src: SourceTownland, c) -> tuple[Optional[tuple[str, dict]], list[str]]:
    """
    Step 5: look for a canonical townland sharing an authority identifier.

    Returns ((column, row), conflicts).  An identifier matching several
    canonical rows, or one whose row disagrees on barony/civil parish, is
    reported as a conflict instead of being used to merge.
    """
    record = src.as_record()
    conflicts: list[str] = []
    for column in AUTHORITY_ID_COLUMNS:
        value = record.get(column)
        if not value:
            continue
        rows = townland_repository.find_rows_by_authority_id(column, str(value), conn=c)
        if not rows:
            continue
        if len(rows) > 1:
            conflicts.append(
                f"{column}={value} matches {len(rows)} canonical townlands"
            )
            continue
        row = rows[0]
        hierarchy_conflict = _hierarchy_conflict(src, row)
        if hierarchy_conflict:
            conflicts.append(f"{column} match rejected: {hierarchy_conflict}")
            continue
        return (column, row), conflicts
    return None, conflicts


def _hierarchy_conflict(src: SourceTownland, row: dict) -> Optional[str]:
    """Return a description of an explicit administrative conflict, else None."""
    for field_name in ("barony", "civil_parish", "county"):
        a = (getattr(src, field_name) or "").strip().upper()
        b = (row.get(field_name) or "").strip().upper()
        if a and b and a != b:
            return f"{field_name} {a!r} vs {b!r}"
    return None


def _score_candidates(src: SourceTownland, name_norm: str, lookup_name: str, c):
    """Step 4: generate bounded candidates and score each pair."""
    seen: dict[int, dict] = {}
    for key in {name_norm, lookup_name, canonical_name(src.name)}:
        for row in townland_repository.find_block_candidates(key, src.county, conn=c):
            seen[int(row["id"])] = row

    scored = [(row, score_pair(src.as_record(), row)) for row in seen.values()]
    scored.sort(key=lambda pair: (-pair[1].score, pair[0]["name"]))
    return scored


def _evidence_from_features(features) -> list[str]:
    out = [f"name similarity {features.jaro_winkler:.2f}"]
    if features.external_id_match:
        out.append("shared authority identifier")
    if features.same_civil_parish:
        out.append("same civil parish")
    if features.same_barony:
        out.append("same barony")
    if features.polygon_iou:
        out.append(f"polygon overlap {features.polygon_iou:.2f}")
    if features.area_ratio:
        out.append(f"area ratio {features.area_ratio:.2f}")
    return out


def _missing_evidence(features) -> list[str]:
    out = []
    if not features.external_id_match:
        out.append("shared authority identifier")
    if not features.polygon_iou:
        out.append("comparable geometry")
    if not features.same_civil_parish:
        out.append("civil parish agreement")
    if not features.same_barony:
        out.append("barony agreement")
    return out


def _write_provenance(src: SourceTownland, entity_id: str, fields: list[str], c) -> None:
    record = src.as_record()
    for field_name in fields:
        if field_name in ("name", "source"):
            continue
        match_review_repository.record_provenance_if_absent(
            entity_id, field_name, record.get(field_name),
            src.source, src.source_record_id, conn=c,
        )


def _row_by_id(c, townland_id: int) -> Optional[dict]:
    return townland_repository.find_row_by_id(townland_id, conn=c)


def _row_by_entity_id(c, entity_id: str) -> Optional[dict]:
    return townland_repository.find_row_by_entity_id(entity_id, conn=c)


def _backfill_entity_id(c, row: dict) -> str:
    return townland_repository.assign_entity_id(int(row["id"]), conn=c)
