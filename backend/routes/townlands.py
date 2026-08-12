from flask import Blueprint, jsonify, request

bp = Blueprint("townlands_api", __name__)


@bp.get("")
@bp.get("/")
def get_townlands():
    from backend.services.townland_service import get_wicklow_townlands
    county = request.args.get("county")
    if county:
        result = get_wicklow_townlands()
    else:
        result = get_wicklow_townlands()
    return jsonify(result)


@bp.get("/wicklow")
def get_wicklow_townlands():
    from backend.services.townland_service import get_wicklow_townlands
    result = get_wicklow_townlands()
    return jsonify(result)


@bp.post("/refresh")
@bp.post("/wicklow/refresh")
def refresh_townlands():
    from backend.services.refresh_service import trigger_townlands_refresh
    result = trigger_townlands_refresh()
    return jsonify(result), 202
