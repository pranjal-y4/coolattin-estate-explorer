from flask import Blueprint, jsonify

bp = Blueprint("map_api", __name__)


@bp.get("/layers")
def get_map_layers():
    from backend.services.map_service import get_layer_config
    return jsonify(get_layer_config())


@bp.get("/centroids")
def get_centroids():
    from backend.services.map_service import build_centroids
    centroids = build_centroids()
    return jsonify(centroids)
