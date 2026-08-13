import functools

from flask import Blueprint, jsonify, request

from backend.models.census_models import CensusFilters


def _require_admin(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        from backend.config import ActiveConfig
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
    from backend.services.census_service import get_available_townlands
    result = get_available_townlands()
    return jsonify({"data": result.data, "meta": result.meta.to_dict()})


@bp.get("/summary")
def get_summary():
    from backend.services.census_service import get_census_summary
    year = request.args.get("year", type=int)
    result = get_census_summary(year=year)
    summary = result.data[0] if result.data else {}
    return jsonify({**summary, **{"meta": result.meta.to_dict()}})


@bp.get("/townland")
def get_townland_detail():
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
    from backend.services.refresh_service import trigger_census_refresh

    body = request.get_json(silent=True) or {}
    year = body.get("year") or request.args.get("year", type=int)

    result = trigger_census_refresh(year=year)
    return jsonify(result), 202


@bp.get("/export/latest")
def get_latest_export():
    from backend.services.export_service import get_latest_census_export
    return jsonify(get_latest_census_export())


@bp.post("/export/regenerate")
@_require_admin
def regenerate_export():
    from backend.services.export_service import regenerate_from_db
    year = request.args.get("year", type=int)
    path = regenerate_from_db(year=year)
    return jsonify({"export_file": path, "status": "regenerated"}), 201


@bp.get("/records")
def get_records_compat():
    return get_census()
