"""
coolattin/routes/unified.py

Unified records API routes.

Routes:
  GET /api/unified/records        — search unified estate database
  GET /api/unified/stats          — record counts and field coverage
  GET /api/unified/townlands      — list of townlands
  GET /api/unified/surnames       — list of surnames
  GET /api/unified/surname-suggest — autocomplete surnames
  GET /api/centroids              — townland centroid lat/lon
  GET /api/workhouse/match/<id>   — workhouse fuzzy matches for a record
"""
from flask import Blueprint, jsonify, request

bp = Blueprint("unified_api", __name__)


@bp.get("/records")
def api_unified_records():
    from backend.services.unified_service import search_records

    surname = (request.args.get("surname") or "").strip()
    forename = (request.args.get("forename") or "").strip()
    townland = (request.args.get("townland") or "").strip()
    year = (request.args.get("year") or "").strip()
    estate = (request.args.get("estate") or "").strip()
    limit = request.args.get("limit", type=int, default=0)

    recs = search_records(
        surname=surname, forename=forename, townland=townland,
        year=year, estate=estate, limit=limit,
    )

    # Try persisted entity-resolution links first; fall back to in-memory fuzzy index.
    resolution_map: dict = {}
    use_legacy = False
    try:
        from backend.services.workhouse_entity_resolution import (
            get_resolution_map,
            has_persisted_links,
        )
        if has_persisted_links():
            resolution_map = get_resolution_map([str(r.get("record_id") or "") for r in recs])
        else:
            use_legacy = True
    except Exception:
        use_legacy = True

    if use_legacy:
        try:
            from backend.services.workhouse_service import get_match_index
            _wh_idx = get_match_index()

            def _legacy_to_new(m: dict, confirmed: bool) -> dict:
                why = []
                if m.get("location_match"):
                    why.append("Electoral division matches townland")
                if m.get("match_basis") and "date" in m.get("match_basis", ""):
                    why.append("Year within ±1 of record year")
                if m.get("occupation_match"):
                    why.append("Occupation keyword overlap")
                return {
                    "name": m.get("raw_name") or "",
                    "place": m.get("electoral_division") or m.get("raw_place") or "",
                    "year": m.get("admitted_or_born") or "",
                    "age": m.get("age") or "",
                    "confidence_score": m.get("name_score"),
                    "confidence": m.get("confidence", "Low"),
                    "label": "CONFIRMED_MATCH" if confirmed else "POSSIBLE_MATCH",
                    "why_it_matched": why or [m.get("match_basis", "Name-based match")],
                    "what_evidence_is_missing": [] if confirmed else ["Further corroborating evidence needed"],
                    "conflicting_evidence": [],
                    "source_record_id": m.get("register_number") or "",
                    "raw_name": m.get("raw_name") or "",
                    "electoral_division": m.get("electoral_division") or "",
                }

            for r in recs:
                rid = str(r.get("record_id") or "")
                matches = _wh_idx.get(rid, [])
                linked = [_legacy_to_new(m, True) for m in matches if m.get("confidence") == "High"]
                possible = [_legacy_to_new(m, False) for m in matches if m.get("confidence") in ("Medium", "Low")]
                resolution_map[rid] = {
                    "linked_workhouse_records": linked,
                    "possible_workhouse_matches": possible,
                    "please_check_records": possible,
                    "identity_is_ambiguous": False,
                    "identity_disambiguation_note": None,
                    "supporting_evidence": [],
                    "conflicting_evidence": [],
                }
        except Exception:
            pass

    for r in recs:
        resolution = resolution_map.get(str(r.get("record_id") or ""), {})
        linked = list(resolution.get("linked_workhouse_records") or [])
        possible = list(resolution.get("possible_workhouse_matches") or [])
        please_check = list(resolution.get("please_check_records") or possible)
        r["linked_workhouse_records"] = linked
        r["possible_workhouse_matches"] = possible
        r["please_check_records"] = please_check
        r["identity_is_ambiguous"] = bool(resolution.get("identity_is_ambiguous"))
        r["identity_disambiguation_note"] = resolution.get("identity_disambiguation_note")
        r["supporting_evidence"] = list(resolution.get("supporting_evidence") or [])
        r["conflicting_evidence"] = list(resolution.get("conflicting_evidence") or [])
        r["has_workhouse_record"] = bool(linked or possible)
        r["workhouse_record_count"] = len(linked) + len(possible)

    return jsonify(recs)


@bp.get("/stats")
def api_unified_stats():
    from backend.services.unified_service import get_stats
    return jsonify(get_stats())


@bp.get("/townlands")
def api_unified_townlands():
    from backend.services.unified_service import get_townland_list
    return jsonify(get_townland_list())


@bp.get("/surnames")
def api_unified_surnames():
    from backend.services.unified_service import get_surname_list
    return jsonify(get_surname_list())


@bp.get("/surname-suggest")
def api_surname_suggest():
    from backend.services.unified_service import suggest_surnames
    q = (request.args.get("q") or "").strip()
    townland = (request.args.get("townland") or "").strip()
    return jsonify(suggest_surnames(q=q, townland=townland))


@bp.get("/surnames-all")
def api_surnames_all():
    """Return all surnames across the entire dataset (not scoped to a townland)."""
    from backend.services.unified_service import suggest_surnames
    q = (request.args.get("q") or "").strip()
    return jsonify(suggest_surnames(q=q, townland=""))


@bp.get("/workhouse-by-townland")
def api_workhouse_by_townland():
    """Return workhouse records (source_mentions) linked to a given townland via entity resolution."""
    townland = (request.args.get("townland") or "").strip()
    if not townland:
        return jsonify({"records": [], "linked": [], "unlinked": [], "error": "townland required"})

    from extensions import get_db_conn
    conn = get_db_conn()
    try:
        tn = townland.upper().replace("'", "''")

        # Workhouse records linked to estate records in this townland via entity resolution
        linked_rows = conn.execute(
            """
            SELECT sm.id AS mention_id, sm.raw_name, sm.forename AS wh_forename,
                   sm.surname AS wh_surname, sm.event_year, sm.raw_place, sm.normalised_place,
                   sm.age, sm.source_table,
                   erc.score AS match_score, erc.label AS match_label,
                   ur.record_id, ur.forename AS estate_forename, ur.surname AS estate_surname,
                   ur.townland, ur.year AS estate_year, ur.role,
                   ur.has_emigration_record, ur.has_eviction_record, ur.has_tenancy_record
            FROM source_mentions sm
            JOIN entity_resolution_candidates erc ON erc.mention_id = sm.id
            JOIN unified_record ur ON ur.record_id = erc.candidate_record_id
            WHERE UPPER(ur.townland_norm) = ?
              AND erc.label IN ('CONFIRMED_MATCH', 'POSSIBLE_MATCH')
            ORDER BY erc.score DESC
            LIMIT 30
            """,
            (tn,)
        ).fetchall()

        # Also find unlinked workhouse mentions that mention this townland by place name
        unlinked_rows = conn.execute(
            """
            SELECT sm.id AS mention_id, sm.raw_name, sm.forename, sm.surname,
                   sm.event_year, sm.raw_place, sm.normalised_place, sm.age, sm.source_table
            FROM source_mentions sm
            WHERE UPPER(COALESCE(sm.normalised_place, sm.raw_place, '')) LIKE ?
            ORDER BY sm.event_year
            LIMIT 20
            """,
            (f"%{tn}%",)
        ).fetchall()

        return jsonify({
            "townland": townland,
            "linked": [dict(r) for r in linked_rows],
            "unlinked": [dict(r) for r in unlinked_rows],
        })
    except Exception as exc:
        return jsonify({"townland": townland, "linked": [], "unlinked": [], "error": str(exc)})
    finally:
        conn.close()
