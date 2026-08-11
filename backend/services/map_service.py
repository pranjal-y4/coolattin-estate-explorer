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
import threading
from pathlib import Path

log = logging.getLogger(__name__)

# Cached map FeatureCollection.  Keyed on the estate GeoJSON mtime plus the
# latest townland/xref timestamps, so an ingest run invalidates it without any
# explicit cache-busting call.
_FC_LOCK = threading.Lock()
_FC_CACHE: dict[str, object] = {"key": None, "value": None}


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


# ------------------------------------------------------------------ #
# Townland map data                                                    #
#                                                                      #
# The map consumes ONE FeatureCollection.  Its geometry baseline is the #
# estate GeoJSON, but every feature is stamped with the canonical       #
# entity_id resolved in the database, and any canonical townland the DB #
# knows about that is not in the baseline file is appended — so a newly #
# resolved townland reaches the map without editing any list by hand.   #
# ------------------------------------------------------------------ #

def build_townland_featurecollection() -> dict:
    """
    Return the townland FeatureCollection served to the map.

    Baseline features keep their original properties untouched and gain
    `entity_id` / `canonical_name`.  Canonical townlands held only in the
    database are appended when they have usable geometry; ones awaiting match
    review, or with no geometry, are counted in `meta` but not drawn.
    """
    from config import ActiveConfig

    geojson_path = ActiveConfig.STATIC_DATA_DIR / "townlands.json"
    cache_key = _cache_key(geojson_path)

    with _FC_LOCK:
        if _FC_CACHE["key"] == cache_key and _FC_CACHE["value"] is not None:
            return _FC_CACHE["value"]  # type: ignore[return-value]

    collection = _build_townland_featurecollection(geojson_path)

    with _FC_LOCK:
        _FC_CACHE["key"] = cache_key
        _FC_CACHE["value"] = collection
    return collection


def invalidate_townland_featurecollection() -> None:
    """Drop the cached map data — call after an ingest changes the database."""
    with _FC_LOCK:
        _FC_CACHE["key"] = None
        _FC_CACHE["value"] = None
    log.info("map_service.featurecollection_invalidated")


def _cache_key(geojson_path: Path) -> tuple:
    from backend.repositories import match_review_repository, townland_repository

    mtime = geojson_path.stat().st_mtime if geojson_path.exists() else 0.0
    try:
        return (mtime, townland_repository.watermark(), match_review_repository.xref_watermark())
    except Exception as exc:
        log.debug("map_service.cache_key_failed | error=%s", exc)
        return (mtime, None, None)


def _build_townland_featurecollection(geojson_path: Path) -> dict:
    from backend.repositories import match_review_repository, townland_repository
    from backend.services.townland_service import normalize_townland_name

    features: list[dict] = []
    if geojson_path.exists():
        try:
            features = json.loads(geojson_path.read_text(encoding="utf-8")).get("features") or []
        except (OSError, json.JSONDecodeError) as exc:
            log.error("map_service.geojson_load_failed | path=%s error=%s", geojson_path, exc)
            features = []

    rows = townland_repository.find_all_as_dicts()
    by_name = {normalize_townland_name(r["name"]): r for r in rows}
    xref_by_source_id = _geojson_xrefs()
    by_entity_id = {r["entity_id"]: r for r in rows if r["entity_id"]}

    out: list[dict] = []
    linked_entity_ids: set[str] = set()
    drawn_names: set[str] = set()

    for feat in features:
        props = dict(feat.get("properties") or {})
        baseline_name = normalize_townland_name(props.get("TL_ENGLISH") or "")
        drawn_names.add(baseline_name)
        source_record_id = str(props.get("TD_ID") or "").strip()
        row = by_entity_id.get(xref_by_source_id.get(source_record_id, ""))
        if row is None:
            row = by_name.get(baseline_name)
        if row is not None:
            props["entity_id"] = row["entity_id"]
            props["canonical_name"] = row["name"]
            if row["entity_id"]:
                linked_entity_ids.add(row["entity_id"])
        out.append({**feat, "properties": props})

    pending_ids = match_review_repository.pending_townland_ids()
    linked_entity_ids_with_source = _entity_ids_with_source_record()
    appended = 0
    no_geometry = 0
    pending_skipped = 0
    duplicate_skipped = 0

    for row in rows:
        if row["entity_id"] and row["entity_id"] in linked_entity_ids:
            continue
        if not row["wkt_geometry"]:
            if row["entity_id"] in linked_entity_ids_with_source:
                no_geometry += 1
            continue
        if row["id"] in pending_ids:
            pending_skipped += 1
            log.info(
                "map_service.townland_pending_review | name=%s entity_id=%s",
                row["name"], row["entity_id"],
            )
            continue
        # A row whose normalised name is already drawn is a legacy duplicate of
        # a baseline townland, not a new place — drawing it would stack two
        # polygons on the same ground.
        if normalize_townland_name(row["name"]) in drawn_names:
            duplicate_skipped += 1
            log.warning(
                "map_service.duplicate_canonical_not_drawn | name=%s entity_id=%s id=%s",
                row["name"], row["entity_id"], row["id"],
            )
            continue
        geometry = _wkt_to_geojson(row["wkt_geometry"])
        if geometry is None:
            no_geometry += 1
            continue
        out.append({
            "type": "Feature",
            "geometry": geometry,
            "properties": {
                "TL_ENGLISH": row["name"],
                "TL_GAEILGE": row["name_gaelic"],
                "COUNTY_ENGLISH": row["county"],
                "TD_ID": row["td_id"],
                "AREA": row["area_sqm"],
                "entity_id": row["entity_id"],
                "canonical_name": row["name"],
                "source": row["source"],
            },
        })
        drawn_names.add(normalize_townland_name(row["name"]))
        appended += 1

    log.info(
        "map_service.featurecollection_built | baseline=%d appended=%d "
        "resolved_without_geometry=%d pending_review=%d duplicate_names=%d",
        len(features), appended, no_geometry, pending_skipped, duplicate_skipped,
    )
    return {
        "type": "FeatureCollection",
        "features": out,
        "meta": {
            "baseline_features": len(features),
            "appended_from_database": appended,
            # Canonical townlands that a source record resolved to but which
            # have no boundary geometry: they exist, they just cannot be drawn.
            "resolved_without_geometry": no_geometry,
            "pending_review_not_drawn": pending_skipped,
            "duplicate_name_not_drawn": duplicate_skipped,
            "total_canonical_townlands": len(rows),
        },
    }


def _entity_ids_with_source_record() -> set[str]:
    """Entity ids that at least one source record resolved to."""
    from backend.repositories import match_review_repository

    try:
        return match_review_repository.entity_ids_with_xref()
    except Exception as exc:
        log.debug("map_service.entity_ids_with_xref_failed | error=%s", exc)
        return set()


def _geojson_xrefs() -> dict[str, str]:
    """{source_record_id: entity_id} for confirmed GeoJSON cross-references."""
    from backend.repositories import match_review_repository

    try:
        return match_review_repository.xrefs_by_source("geojson")
    except Exception as exc:
        log.debug("map_service.xref_lookup_failed | error=%s", exc)
        return {}


def _wkt_to_geojson(wkt: str) -> dict | None:
    try:
        from shapely import wkt as _swkt
        from shapely.geometry import mapping
        return mapping(_swkt.loads(wkt))
    except Exception as exc:
        log.warning("map_service.wkt_conversion_failed | error=%s", exc)
        return None


def build_centroids(geojson_path: Path | None = None) -> dict[str, tuple[float, float]]:
    """
    Compute lat/lon centroid for each townland polygon in the GeoJSON file.
    Returns dict: townland_name → (lat, lon)

    With no explicit path, centroids come from the database-backed
    FeatureCollection so newly resolved townlands get markers too.
    Delegates to townland_service so normalisation is consistent.
    """
    from backend.services.townland_service import (
        build_centroids_from_features,
        build_centroids_from_geojson,
    )

    if geojson_path is not None:
        return build_centroids_from_geojson(geojson_path)

    return build_centroids_from_features(
        build_townland_featurecollection().get("features") or []
    )
