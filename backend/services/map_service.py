from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


MAP_LAYERS: dict[str, dict] = {
    "standard": {
        "id": "standard",
        "label": "Standard",
        "tile_url": "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
        "attribution": '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        "max_zoom": 19,
        "description": "Default OpenStreetMap basemap",
    },
    "satellite": {
        "id": "satellite",
        "label": "Satellite",
        "tile_url": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "attribution": "Tiles © Esri — Source: Esri, i-cubed, USDA, USGS, AEX, GeoEye, Getmapping, Aerogrid, IGN, IGP, UPR-EGP, and the GIS User Community",
        "max_zoom": 19,
        "description": "Esri World Imagery satellite view",
    },
    "terrain": {
        "id": "terrain",
        "label": "Terrain",
        "tile_url": "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
        "attribution": 'Map data: © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, <a href="http://viewfinderpanoramas.org">SRTM</a> | Map style: © <a href="https://opentopomap.org">OpenTopoMap</a>',
        "max_zoom": 17,
        "description": "OpenTopoMap terrain with elevation contours — best for estate boundary visibility",
    },
    "labels_overlay": {
        "id": "labels_overlay",
        "label": "Satellite + Labels",
        "tile_url": "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
        "attribution": "Labels © Esri",
        "max_zoom": 19,
        "is_overlay": True,
        "description": "Place name labels overlay for satellite view",
    },
}

DEFAULT_LAYER = "standard"


def get_layer_config() -> dict:
    layers = [
        layer for layer in MAP_LAYERS.values()
        if not layer.get("is_overlay")
    ]
    overlays = [
        layer for layer in MAP_LAYERS.values()
        if layer.get("is_overlay")
    ]
    return {
        "layers": layers,
        "overlays": overlays,
        "default": DEFAULT_LAYER,
    }


def build_centroids(geojson_path: Path | None = None) -> dict[str, tuple[float, float]]:
    from backend.services.townland_service import build_centroids_from_geojson
    from config import ActiveConfig

    if geojson_path is None:
        geojson_path = ActiveConfig.STATIC_DATA_DIR / "townlands.json"

    return build_centroids_from_geojson(geojson_path)
