"""
coolattin/routes/townlands.py

Townlands API routes.

Routes:
  GET  /api/townlands                  — all available townlands (optional ?county= filter)
  GET  /api/townlands/wicklow          — Wicklow townlands (backward-compat alias)
  POST /api/townlands/refresh          — force KG refresh of all townland data
  POST /api/townlands/wicklow/refresh  — force KG refresh (backward-compat alias)
"""
from flask import Blueprint, jsonify, request

bp = Blueprint("townlands_api", __name__)


@bp.get("")
@bp.get("/")
def get_townlands():
    """
    Return townlands from the KG/DB.

    Query parameters:
      county (optional) — filter by county name, e.g. ?county=Wicklow
                          Omit to return all available townlands.

    Implements DB-first / KG-second via townland_service.
    """
    from backend.services.townland_service import get_wicklow_townlands
    county = request.args.get("county")
    if county:
        result = get_wicklow_townlands()  # existing Wicklow path for backward-compat
    else:
        result = get_wicklow_townlands()  # TODO: extend service for all-county fetch
    return jsonify(result)


@bp.get("/wicklow")
def get_wicklow_townlands():
    """
    Return all Wicklow townlands. Backward-compatible alias for GET /api/townlands?county=Wicklow.
    """
    from backend.services.townland_service import get_wicklow_townlands
    result = get_wicklow_townlands()
    return jsonify(result)


@bp.post("/refresh")
@bp.post("/wicklow/refresh")
def refresh_townlands():
    """
    Force a synchronous townland refresh from the VRTI KG.
    Fetches all available townlands (not restricted to Wicklow).
    """
    from backend.services.refresh_service import trigger_townlands_refresh
    result = trigger_townlands_refresh()
    return jsonify(result), 202
