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

    try:
        from backend.services.workhouse_entity_resolution import (
            get_resolution_map,
            has_persisted_links,
        )

        if has_persisted_links():
            resolution_map = get_resolution_map([str(r.get("record_id") or "") for r in recs])
        else:
            resolution_map = {}
    except Exception:
        resolution_map = {}

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
