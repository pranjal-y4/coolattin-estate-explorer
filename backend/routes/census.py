"""
coolattin/routes/census.py

Census API routes.

======================================================
NO SPARQL. NO DATA LOADING. NO NORMALISATION HERE.
======================================================

Routes:
  GET  /api/census                     — paginated census records
  GET  /api/census/townlands           — list of townlands with census data
  GET  /api/census/summary             — aggregate stats (totals by year)
  GET  /api/census/townland            — single townland detail (all years)
  POST /api/census/refresh             — force KG re-ingestion
  GET  /api/census/export/latest       — info about most recent export
  POST /api/census/export/regenerate   — re-generate Excel from DB (no KG call)

All responses follow the envelope:
  { "data": [...], "meta": { source, cache_status, generated_at, record_count, export_file } }
"""
import functools

from flask import Blueprint, jsonify, request

from backend.models.census_models import CensusFilters


def _require_admin(fn):
    """Reject requests that don't carry a valid ADMIN_API_KEY header or query param.

    If ADMIN_API_KEY is not configured, the endpoint is disabled entirely —
    it should never be reachable on a public deployment without a key set.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        from config import ActiveConfig
        required = (ActiveConfig.ADMIN_API_KEY or "").strip()
        if not required:
            return jsonify({"error": "Admin operations are disabled — set ADMIN_API_KEY in the environment."}), 403
        provided = (
            request.headers.get("X-Admin-Key")
            or request.args.get("admin_key")
            or ""
        ).strip()
        if not provided or provided != required:
            return jsonify({"error": "Forbidden"}), 403
        return fn(*args, **kwargs)
    return wrapper

bp = Blueprint("census_api", __name__)


@bp.get("/")
def get_census():
    """
    Return paginated census records.
    Implements DB-first / KG-second via census_service.

    Query params:
      year      int  — filter to census year (1841|1851|1861|1871|1881|1891)
      townland  str  — filter by townland name (partial match, case-insensitive)
      barony    str  — filter by barony name
      page      int  — page number (default 1)
      limit     int  — records per page (default 100, max 500)
    """
    from backend.services.census_service import get_census_data

    filters = CensusFilters(
        year=request.args.get("year", type=int),
        townland=request.args.get("townland"),
        barony=request.args.get("barony"),
        page=max(1, request.args.get("page", default=1, type=int)),
        limit=min(request.args.get("limit", default=100, type=int), 2000),
    )
    result = get_census_data(filters)
    return jsonify({"data": result.data, "meta": result.meta.to_dict()})


@bp.get("/townlands")
def get_townlands():
    """
    Return list of townland names that have census records.
    Used by the census map sidebar filter and frontend dropdowns.
    """
    from backend.services.census_service import get_available_townlands
    result = get_available_townlands()
    return jsonify({"data": result.data, "meta": result.meta.to_dict()})


@bp.get("/summary")
def get_summary():
    """
    Return aggregate census statistics for a given year (or all years).

    Query params:
      year  int  — optional census year filter
    """
    from backend.services.census_service import get_census_summary
    year = request.args.get("year", type=int)
    result = get_census_summary(year=year)
    # Flatten summary dict into response for backward compatibility
    summary = result.data[0] if result.data else {}
    return jsonify({**summary, **{"meta": result.meta.to_dict()}})


@bp.get("/townland")
def get_townland_detail():
    """
    Return full census detail for a single townland (all years).

    Query params:
      name  str  — townland name (required)
    """
    from backend.services.census_service import get_townland_detail

    name = (request.args.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name parameter is required"}), 400

    result = get_townland_detail(townland_name=name)
    if not result.data:
        return jsonify({"error": f"Townland '{name}' not found"}), 404

    return jsonify({"data": result.data[0], "meta": result.meta.to_dict()})


@bp.post("/refresh")
@_require_admin
def trigger_refresh():
    """
    Force a synchronous census refresh from the VRTI KG.
    Ignores TTL — always queries KG.

    Body params (JSON, optional):
      year  int  — refresh only a specific census year
    """
    from backend.services.refresh_service import trigger_census_refresh

    body = request.get_json(silent=True) or {}
    year = body.get("year") or request.args.get("year", type=int)

    result = trigger_census_refresh(year=year)
    return jsonify(result), 202


@bp.get("/export/latest")
def get_latest_export():
    """Return information about the most recent census export file."""
    from backend.services.export_service import get_latest_census_export
    return jsonify(get_latest_census_export())


@bp.post("/export/regenerate")
@_require_admin
def regenerate_export():
    """
    Re-generate Excel export from the local DB.
    Does NOT call the KG — reads from persisted DB data only.
    """
    from backend.services.export_service import regenerate_from_db
    year = request.args.get("year", type=int)
    path = regenerate_from_db(year=year)
    return jsonify({"export_file": path, "status": "regenerated"}), 201


# ------------------------------------------------------------------ #
# Backward-compatible alias routes                                     #
# The old app.py used /api/census/records; keep working.             #
# ------------------------------------------------------------------ #

@bp.get("/records")
def get_records_compat():
    """Backward-compatible alias for GET /api/census/."""
    return get_census()
