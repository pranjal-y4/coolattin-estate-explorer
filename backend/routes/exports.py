"""
coolattin/routes/exports.py

Export download routes.

Routes:
  GET /api/exports/census/latest     — latest census export info
  GET /api/exports/census/download   — download the export file
"""
from flask import Blueprint, jsonify, send_file, abort
from pathlib import Path

bp = Blueprint("exports_api", __name__)


@bp.get("/census/latest")
def census_export_latest():
    from backend.services.export_service import get_latest_census_export
    return jsonify(get_latest_census_export())


@bp.get("/census/download")
def census_export_download():
    from backend.services.export_service import get_latest_census_export
    info = get_latest_census_export()
    export_file = info.get("export_file")
    if not export_file:
        abort(404, description="No export available. Run a refresh first.")
    path = Path(export_file)
    if not path.exists():
        abort(404, description="Export file not found on disk.")
    return send_file(path, as_attachment=True)
