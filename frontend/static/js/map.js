/**
 * coolattin/static/js/map.js
 *
 * Main map initialisation — shared across all map pages.
 *
 * Layer config is fetched from /api/map/layers (served by map_service.py).
 * Tile URLs are NEVER hardcoded here — always from the backend config.
 *
 * Exports:
 *   initLayerSwitcher(mapInstance, containerId)  — builds the basemap switcher UI
 *   buildLayerMap(layers)                        — returns { id: L.TileLayer }
 *   switchLayer(mapInstance, layerMap, layerId)  — swaps the active base layer
 */

// ------------------------------------------------------------------ //
// Layer switcher factory                                               //
// Called from census.js and any other page that has a Leaflet map.   //
// ------------------------------------------------------------------ //

/**
 * Fetch layer config from backend, build Leaflet tile layers,
 * and render the layer switcher button group.
 *
 * @param {L.Map} mapInstance  — Leaflet map to attach layers to
 * @param {string} containerId — id of the HTML element to render buttons into
 * @returns {Promise<{ layerMap: Object, config: Object }>}
 */
async function initLayerSwitcher(mapInstance, containerId) {
  let config;
  try {
    const res = await fetch("/api/map/layers");
    config = res.ok ? await res.json() : _fallbackLayerConfig();
  } catch (e) {
    console.warn("map.js: failed to fetch /api/map/layers — using fallback", e);
    config = _fallbackLayerConfig();
  }

  const layerMap = buildLayerMap(config.layers);
  const overlayMap = buildLayerMap(config.overlays || []);

  // Add default base layer
  const defaultId = config.default || "standard";
  if (layerMap[defaultId]) {
    layerMap[defaultId].addTo(mapInstance);
  }

  // Restore user preference from localStorage
  const savedLayer = localStorage.getItem("coolattin_map_layer");
  if (savedLayer && layerMap[savedLayer] && savedLayer !== defaultId) {
    if (layerMap[defaultId]) mapInstance.removeLayer(layerMap[defaultId]);
    layerMap[savedLayer].addTo(mapInstance);
  }
  const activeLayerId = savedLayer && layerMap[savedLayer] ? savedLayer : defaultId;
  if (activeLayerId === "satellite" && overlayMap["labels_overlay"]) {
    overlayMap["labels_overlay"].addTo(mapInstance);
  }

  _renderSwitcherUI(mapInstance, layerMap, overlayMap, config, containerId);

  return { layerMap, overlayMap, config };
}

/**
 * Build a map of { layerId: L.TileLayer } from a layers config array.
 *
 * @param {Array} layers — from /api/map/layers response
 * @returns {Object}
 */
function buildLayerMap(layers) {
  const map = {};
  (layers || []).forEach(layer => {
    map[layer.id] = L.tileLayer(layer.tile_url, {
      attribution: layer.attribution || "",
      maxZoom: layer.max_zoom || 19,
    });
  });
  return map;
}

/**
 * Swap the active base layer.
 *
 * @param {L.Map} mapInstance
 * @param {Object} layerMap    — { layerId: L.TileLayer }
 * @param {string} layerId     — id of the layer to activate
 */
function switchLayer(mapInstance, layerMap, layerId) {
  Object.values(layerMap).forEach(l => {
    if (mapInstance.hasLayer(l)) mapInstance.removeLayer(l);
  });
  if (layerMap[layerId]) {
    layerMap[layerId].addTo(mapInstance);
    localStorage.setItem("coolattin_map_layer", layerId);
  }
}

// ------------------------------------------------------------------ //
// Internal helpers                                                     //
// ------------------------------------------------------------------ //

function _renderSwitcherUI(mapInstance, layerMap, overlayMap, config, containerId) {
  const container = document.getElementById(containerId);
  if (!container) return;

  container.innerHTML = "";
  container.className = "layer-switcher";

  const activeSaved = localStorage.getItem("coolattin_map_layer") || config.default;

  (config.layers || []).forEach(layer => {
    const btn = document.createElement("button");
    btn.textContent = layer.label;
    btn.dataset.layerId = layer.id;
    btn.className = "layer-btn" + (layer.id === activeSaved ? " active" : "");
    btn.title = layer.description || layer.label;

    btn.addEventListener("click", () => {
      container.querySelectorAll(".layer-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      switchLayer(mapInstance, layerMap, layer.id);

      // Auto-toggle labels overlay when switching to satellite
      if (layer.id === "satellite" && overlayMap["labels_overlay"]) {
        overlayMap["labels_overlay"].addTo(mapInstance);
      } else if (overlayMap["labels_overlay"]) {
        mapInstance.removeLayer(overlayMap["labels_overlay"]);
      }
    });

    container.appendChild(btn);
  });
}

/** Fallback tile config used when /api/map/layers is unreachable. */
function _fallbackLayerConfig() {
  return {
    default: "standard",
    layers: [
      {
        id: "standard",
        label: "Standard",
        tile_url: "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
        attribution: "© OpenStreetMap contributors",
        max_zoom: 19,
      },
      {
        id: "satellite",
        label: "Satellite",
        tile_url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attribution: "© Esri",
        max_zoom: 19,
      },
      {
        id: "terrain",
        label: "Terrain",
        tile_url: "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
        attribution: "© OpenTopoMap contributors",
        max_zoom: 17,
      },
    ],
    overlays: [
      {
        id: "labels_overlay",
        label: "Labels",
        tile_url: "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
        attribution: "© Esri",
        max_zoom: 19,
      },
    ],
  };
}

// ------------------------------------------------------------------ //
// map.js is a utility library only — no map initialisation here.      //
// The home page map (index.html) is managed exclusively by main.js.  //
// The census page map uses initLayerSwitcher() from census.js.        //
// ------------------------------------------------------------------ //
