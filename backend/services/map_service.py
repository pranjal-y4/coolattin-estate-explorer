"""
coolattin/services/map_service.py

Map configuration service.

Responsibilities:
  - Defines all available basemap tile layers in one place
  - Serves layer config to the frontend via /api/map/layers
  - Computes townland centroids from GeoJSON
  - Frontend never hardcodes tile URLs — always fetches from this service

Adding a new basemap:
  Add an entry to MAP_LAYERS below.  The frontend picks it up automatically.
  No frontend code changes needed.

Note on 3D/terrain:
  True 3D perspective rendering (Cesium-style) requires Mapbox GL JS or
  CesiumJS and is outside the current Leaflet stack.  OpenTopoMap is the
  closest practical terrain equivalent within Leaflet.  The id "terrain"
  is reserved for a future Mapbox GL migration path.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
# Centralised basemap layer definitions                               #
# Changing a tile URL here automatically propagates to all map pages. #
# ------------------------------------------------------------------ #
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
        "is_overlay": True,   # frontend renders this on top of satellite, not as a standalone base
        "description": "Place name labels overlay for satellite view",
    },
}

DEFAULT_LAYER = "standard"


def get_layer_config() -> dict:
    """
    Return basemap layer configuration for the frontend.
    Called by GET /api/map/layers.

    The frontend must not hardcode tile URLs.
    It must fetch this endpoint on page load and build all layer objects from here.
    """
    layers = [
        layer for layer in MAP_LAYERS.values()
        if not layer.get("is_overlay")   # overlays are combined by the frontend
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
    """
    Compute lat/lon centroid for each townland polygon in the GeoJSON file.
    Returns dict: townland_name → (lat, lon)

    Delegates to townland_service.build_centroids_from_geojson so that
    normalisation is consistent.
    """
    from backend.services.townland_service import build_centroids_from_geojson
    from config import ActiveConfig

    if geojson_path is None:
        geojson_path = ActiveConfig.STATIC_DATA_DIR / "townlands.json"

    return build_centroids_from_geojson(geojson_path)
