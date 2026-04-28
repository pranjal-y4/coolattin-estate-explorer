"""
coolattin/routes/map_config.py

Map configuration and centroid routes.

Routes:
  GET /api/map/layers     — basemap tile layer definitions
  GET /api/centroids      — townland centroid lat/lon coordinates
  GET /api/townlands/wicklow         — townland list with KG enrichment
  POST /api/townlands/wicklow/refresh — force KG refresh of townland data
"""
from flask import Blueprint, jsonify

bp = Blueprint("map_api", __name__)


@bp.get("/layers")
def get_map_layers():
    """
    Return basemap layer configuration.

    The frontend fetches this on page load to build its layer switcher.
    Frontend must never hardcode tile URLs.

    Response shape:
      {
        "layers": [{ id, label, tile_url, attribution, max_zoom }],
        "overlays": [...],
        "default": "standard"
      }
    """
    from backend.services.map_service import get_layer_config
    return jsonify(get_layer_config())


@bp.get("/centroids")
def get_centroids():
    """
    Return townland centroid coordinates as { townland_name: [lat, lon] }.
    Used by the main map to place markers.
    """
    from backend.services.map_service import build_centroids
    centroids = build_centroids()
    return jsonify(centroids)
