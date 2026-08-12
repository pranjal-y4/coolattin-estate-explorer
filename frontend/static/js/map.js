

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

  const defaultId = config.default || "standard";
  if (layerMap[defaultId]) {
    layerMap[defaultId].addTo(mapInstance);
  }

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

function switchLayer(mapInstance, layerMap, layerId) {
  Object.values(layerMap).forEach(l => {
    if (mapInstance.hasLayer(l)) mapInstance.removeLayer(l);
  });
  if (layerMap[layerId]) {
    layerMap[layerId].addTo(mapInstance);
    localStorage.setItem("coolattin_map_layer", layerId);
  }
}


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

      if (layer.id === "satellite" && overlayMap["labels_overlay"]) {
        overlayMap["labels_overlay"].addTo(mapInstance);
      } else if (overlayMap["labels_overlay"]) {
        mapInstance.removeLayer(overlayMap["labels_overlay"]);
      }
    });

    container.appendChild(btn);
  });
}

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

