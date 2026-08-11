/**
 * heritage.js — Historic Landscape page
 *
 * Displays Wicklow-only heritage features (ASI archaeology, holy wells,
 * monuments-to-visit) filtered to the currently selected Coolattin townland.
 *
 * Data files consumed (produced by scripts/preprocess_heritage.py):
 *   /static/data/townlands.json          — estate townland polygons
 *   /static/data/asi_wicklow.geojson     — Wicklow ASI archaeology
 *   /static/data/holywells_wicklow.geojson
 *   /static/data/monuments_wicklow.geojson
 *
 * Photo fallback uses the Wikipedia geosearch + pageimages API (no key needed).
 * To add Google Street View or Flickr, configure API keys in HP_CONFIG below.
 */

// ─── Configuration ────────────────────────────────────────────────────────────

const HP_CONFIG = {
  COUNTY: "WICKLOW",
  DEFAULT_RADIUS_M: 2000,
  MAP_CENTER: [52.85, -6.35],
  MAP_ZOOM: 10,
  MAP_MIN_ZOOM: 8,
  MAP_MAX_ZOOM: 18,

  DATASETS: {
    asi: {
      label: "Archaeological Sites",
      color: "#9c8a6e",
      fillColor: "#c4b090",
      radius: 6,
      file: "/static/data/asi_wicklow.geojson",
    },
    holywells: {
      label: "Holy Wells",
      color: "#2d7da0",
      fillColor: "#5aa5c8",
      radius: 7,
      file: "/static/data/holywells_wicklow.geojson",
    },
    monuments: {
      label: "Monuments to Visit",
      color: "#6d4e8c",
      fillColor: "#9b7abf",
      radius: 8,
      file: "/static/data/monuments_wicklow.geojson",
    },
  },

  // ── Photo source: "wikipedia" is the no-key fallback.
  //    To use Google Street View: set STREET_VIEW_KEY to your API key.
  //    To use Flickr: set FLICKR_KEY.
  PHOTO_SOURCE: "wikipedia",
  STREET_VIEW_KEY: "",  // optional: set to enable Google Street View thumbnails
  FLICKR_KEY: "",       // optional: set to enable Flickr geotagged photos
  PHOTO_SEARCH_RADIUS_M: 10000,

  TOWNLANDS_FILE: "/static/data/townlands.json",
};

// ─── State ────────────────────────────────────────────────────────────────────

const hpState = {
  map: null,
  townlandsGeo: null,

  selectedName: null,
  selectedFeature: null,
  centroid: null,          // [lat, lng]

  radius: HP_CONFIG.DEFAULT_RADIUS_M,
  activeLayers: new Set(["asi", "holywells", "monuments"]),

  rawData: { asi: null, holywells: null, monuments: null },
  layerGroups: { asi: null, holywells: null, monuments: null },
  visibleCounts: { asi: 0, holywells: 0, monuments: 0 },

  townlandPolygonLayer: null,
  townlandsBrowseLayer: null,
  nearbyLayer: null,
  radiusCircleLayer: null,

  rightMode: "empty",  // "empty" | "townland" | "feature"
};

// ─── DOM shortcuts ─────────────────────────────────────────────────────────────

const $ = (id) => document.getElementById(id);
const show = (id) => { const el = $(id); if (el) el.style.display = ""; };
const hide = (id) => { const el = $(id); if (el) el.style.display = "none"; };

// ─── Entry point ──────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  initMap();
  wireEvents();
  startHeritagePage();
});

async function startHeritagePage() {
  showLoading(true);
  try {
    await loadTownlandsGeo();
    const { townland, radius, layers } = readUrlParams();
    if (radius) hpState.radius = radius;
    if (layers) {
      hpState.activeLayers = new Set(layers);
      syncLayerCheckboxes();
    }
    syncRadioButtons();
    await loadAllHeritageData();
    if (townland) {
      await selectTownlandByName(townland, { dataReady: true });
    } else {
      showAllLandscape({ fitBounds: true });
    }
  } finally {
    showLoading(false);
  }
}

// ─── URL params ───────────────────────────────────────────────────────────────

function readUrlParams() {
  const p = new URLSearchParams(window.location.search);
  const townland = p.get("townland") || p.get("townlandId") || null;
  const radiusVal = p.get("radius") ? parseInt(p.get("radius"), 10) : null;
  const layersVal = p.get("layers") ? p.get("layers").split(",") : null;
  return { townland, radius: radiusVal, layers: layersVal };
}

function updateUrl() {
  const p = new URLSearchParams();
  if (hpState.selectedName) {
    p.set("townland", hpState.selectedName);
    if (hpState.radius !== HP_CONFIG.DEFAULT_RADIUS_M) p.set("radius", hpState.radius);
  }
  const active = [...hpState.activeLayers].sort();
  if (active.length !== 3) p.set("layers", active.join(","));
  const qs = p.toString();
  history.replaceState({}, "", qs ? "?" + qs : window.location.pathname);
}

// ─── Map initialisation ──────────────────────────────────────────────────────

function initMap() {
  hpState.map = L.map("heritageMap", {
    center: HP_CONFIG.MAP_CENTER,
    zoom: HP_CONFIG.MAP_ZOOM,
    minZoom: HP_CONFIG.MAP_MIN_ZOOM,
    maxZoom: HP_CONFIG.MAP_MAX_ZOOM,
  });

  // Shared layer-switcher utility from map.js
  initLayerSwitcher(hpState.map, "hp-layer-switcher-container");
}

// ─── Townland GeoJSON ─────────────────────────────────────────────────────────

async function loadTownlandsGeo() {
  try {
    const res = await fetch(HP_CONFIG.TOWNLANDS_FILE);
    if (!res.ok) throw new Error("Failed to load townlands");
    hpState.townlandsGeo = await res.json();
    populateTownlandSelector();
    drawTownlandBrowseLayer();
  } catch (e) {
    console.error("heritage.js: could not load townlands GeoJSON", e);
  }
}

function townlandName(feature) {
  return String(feature?.properties?.TL_ENGLISH || "").trim();
}

function populateTownlandSelector() {
  const select = $("hp-townland-select");
  if (!select || !hpState.townlandsGeo) return;

  const names = (hpState.townlandsGeo.features || [])
    .map(townlandName)
    .filter(Boolean)
    .sort((a, b) => a.localeCompare(b));

  select.innerHTML = `<option value="">All townlands</option>` +
    names.map((name) => `<option value="${escapeHtml(name)}">${escapeHtml(titleCaseName(name))}</option>`).join("");
}

function drawTownlandBrowseLayer() {
  if (!hpState.map || !hpState.townlandsGeo) return;
  if (hpState.townlandsBrowseLayer) hpState.map.removeLayer(hpState.townlandsBrowseLayer);

  hpState.townlandsBrowseLayer = L.geoJSON(hpState.townlandsGeo, {
    style: (feature) => {
      const selected = hpState.selectedName && townlandName(feature) === hpState.selectedName;
      return {
        color: selected ? "#234B3A" : "#6f806c",
        weight: selected ? 2.4 : 0.9,
        fillColor: selected ? "#234B3A" : "#ffffff",
        fillOpacity: selected ? 0.14 : 0.03,
        opacity: selected ? 1 : 0.5,
      };
    },
    onEachFeature: (feature, layer) => {
      const name = townlandName(feature);
      layer.bindTooltip(titleCaseName(name), { sticky: true, className: "hp-tip" });
      layer.on({
        click: () => selectTownland(feature),
        mouseover: () => {
          if (name !== hpState.selectedName) {
            layer.setStyle({ color: "#234B3A", weight: 1.8, fillOpacity: 0.08, opacity: 0.9 });
          }
        },
        mouseout: () => {
          if (hpState.townlandsBrowseLayer) hpState.townlandsBrowseLayer.resetStyle(layer);
        },
      });
    },
  }).addTo(hpState.map);
}

// ─── Townland selection ───────────────────────────────────────────────────────

function selectTownlandByName(name, options = {}) {
  if (!hpState.townlandsGeo) return;
  const feature = hpState.townlandsGeo.features.find((f) => {
    const nm = townlandName(f);
    return nm.toLowerCase() === name.toLowerCase();
  });
  if (!feature) {
    console.warn("heritage.js: townland not found:", name);
    return;
  }
  return selectTownland(feature, options);
}

async function selectTownland(feature, options = {}) {
  const name = townlandName(feature);
  hpState.selectedName = name;
  hpState.selectedFeature = feature;
  hpState.centroid = polygonCentroid(feature.geometry);

  const select = $("hp-townland-select");
  if (select) select.value = name;

  // Update back link to preserve townland context on home page
  const backLink = $("hp-back-link");
  if (backLink) backLink.href = `/?townland=${encodeURIComponent(name)}#explore`;

  // Hide the no-selection overlay
  const noSel = $("hp-map-no-sel");
  if (noSel) noSel.style.display = "none";

  // Update left panel
  $("hp-townland-name").textContent = name;
  $("hp-county-label").style.display = "";
  hide("hp-no-sel-hint");
  show("hp-radius-section");
  show("hp-layers-section");
  show("hp-gmaps-section");

  // Highlight the selected townland on the map
  drawTownlandHighlight(feature);
  drawTownlandBrowseLayer();

  // Zoom map to the selected townland
  const bounds = L.geoJSON(feature).getBounds();
  hpState.map.fitBounds(bounds, { padding: [40, 40] });

  // Load heritage data (lazy — only once, then filter locally)
  if (!options.dataReady) {
    showLoading(true);
    await loadAllHeritageData();
  }
  filterAndRenderAll();
  if (!options.dataReady) showLoading(false);

  updateGoogleMapsLinks();
  updateRightTownlandPanel();
  updateUrl();

  // Photos
  showPhotosSection(true);
  fetchAndRenderPhotos(hpState.centroid[0], hpState.centroid[1], name);
}

function showAllLandscape(options = {}) {
  hpState.selectedName = null;
  hpState.selectedFeature = null;
  hpState.centroid = null;

  const select = $("hp-townland-select");
  if (select) select.value = "";

  const backLink = $("hp-back-link");
  if (backLink) backLink.href = "/";

  const noSel = $("hp-map-no-sel");
  if (noSel) noSel.style.display = "none";

  $("hp-townland-name").textContent = "All Coolattin townlands";
  $("hp-county-label").style.display = "";
  const hint = $("hp-no-sel-hint");
  if (hint) {
    hint.textContent = "Showing all mapped townlands and heritage features. Click any townland to focus.";
    hint.style.display = "";
  }

  hide("hp-radius-section");
  show("hp-layers-section");
  hide("hp-gmaps-section");

  if (hpState.townlandPolygonLayer) {
    hpState.map.removeLayer(hpState.townlandPolygonLayer);
    hpState.townlandPolygonLayer = null;
  }
  if (hpState.radiusCircleLayer) {
    hpState.map.removeLayer(hpState.radiusCircleLayer);
    hpState.radiusCircleLayer = null;
  }

  drawTownlandBrowseLayer();
  filterAndRenderAll();
  updateAllOverviewPanel();
  showPhotosSection(false);
  updateUrl();

  if (options.fitBounds && hpState.townlandsGeo) {
    const bounds = L.geoJSON(hpState.townlandsGeo).getBounds();
    hpState.map.fitBounds(bounds, { padding: [28, 28] });
  }
}

// ─── Townland highlight on map ────────────────────────────────────────────────

function drawTownlandHighlight(feature) {
  if (hpState.townlandPolygonLayer) {
    hpState.map.removeLayer(hpState.townlandPolygonLayer);
  }

  // Draw the selected townland with strong emphasis
  hpState.townlandPolygonLayer = L.geoJSON(feature, {
    style: {
      color: "#234B3A",
      weight: 2.5,
      fillColor: "#234B3A",
      fillOpacity: 0.12,
      opacity: 1,
    },
    interactive: false,
  }).addTo(hpState.map);

  // Draw radius circle when radius > 0
  if (hpState.radiusCircleLayer) hpState.map.removeLayer(hpState.radiusCircleLayer);
  if (hpState.centroid) {
    const [lat, lng] = hpState.centroid;
    const boundaryOnly = hpState.radius === 0;
    const circles = [
      { radius: 2000, color: "#f59e0b", fillColor: "#fbbf24", dashArray: "10 6" },
      { radius: 5000, color: "#ef4444", fillColor: "#f87171", dashArray: "14 8" },
    ].map((ring) => {
      const active = hpState.radius === ring.radius;
      return L.circle([lat, lng], {
        radius: ring.radius,
        color: ring.color,
        weight: active ? 3.4 : 2.2,
        fillColor: ring.fillColor,
        fillOpacity: active ? 0.08 : boundaryOnly ? 0.03 : 0.025,
        dashArray: ring.dashArray,
        opacity: active ? 0.96 : boundaryOnly ? 0.70 : 0.58,
        interactive: false,
      });
    });
    hpState.radiusCircleLayer = L.layerGroup(circles).addTo(hpState.map);
  }
}

// ─── Heritage data loading ────────────────────────────────────────────────────

async function loadAllHeritageData() {
  const keys = Object.keys(HP_CONFIG.DATASETS);
  await Promise.all(
    keys.map(async (key) => {
      if (hpState.rawData[key] !== null) return; // already loaded
      try {
        const res = await fetch(HP_CONFIG.DATASETS[key].file);
        if (!res.ok) {
          console.warn(`heritage.js: ${key} dataset not found (${res.status}). Skipping.`);
          hpState.rawData[key] = [];
          return;
        }
        const geo = await res.json();
        hpState.rawData[key] = geo.features || [];
      } catch (e) {
        console.warn(`heritage.js: failed to load ${key}`, e);
        hpState.rawData[key] = [];
      }
    })
  );
}

// ─── Spatial filtering ────────────────────────────────────────────────────────

function filterFeatures(features, geometry, centroid, radiusM) {
  if (!features || !features.length) return [];
  const [cLat, cLng] = centroid;

  return features.filter((f) => {
    const coords = f.geometry?.coordinates;
    if (!coords) return false;
    const [lng, lat] = coords;

    // Test: inside the selected polygon
    if (pointInGeoJSONPolygon(lng, lat, geometry)) return true;

    // Test: within radius of centroid (when radius > 0)
    if (radiusM > 0) {
      return haversineM(lat, lng, cLat, cLng) <= radiusM;
    }
    return false;
  });
}

// Ray-casting point-in-polygon for GeoJSON Polygon / MultiPolygon
function pointInGeoJSONPolygon(lng, lat, geometry) {
  if (!geometry) return false;
  const rings =
    geometry.type === "Polygon"
      ? geometry.coordinates
      : geometry.type === "MultiPolygon"
      ? geometry.coordinates.flat(1)
      : [];
  return rings.some((ring) => pointInRing(lng, lat, ring));
}

function pointInRing(lng, lat, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    if ((yi > lat) !== (yj > lat) && lng < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) {
      inside = !inside;
    }
  }
  return inside;
}

// Haversine distance in metres
function haversineM(lat1, lng1, lat2, lng2) {
  const R = 6371000;
  const φ1 = (lat1 * Math.PI) / 180;
  const φ2 = (lat2 * Math.PI) / 180;
  const Δφ = ((lat2 - lat1) * Math.PI) / 180;
  const Δλ = ((lng2 - lng1) * Math.PI) / 180;
  const a =
    Math.sin(Δφ / 2) ** 2 +
    Math.cos(φ1) * Math.cos(φ2) * Math.sin(Δλ / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

// Bounding-box centroid of a GeoJSON polygon
function polygonCentroid(geometry) {
  const ring =
    geometry.type === "Polygon"
      ? geometry.coordinates[0]
      : geometry.type === "MultiPolygon"
      ? geometry.coordinates[0][0]
      : [];
  let minLat = Infinity, maxLat = -Infinity, minLng = Infinity, maxLng = -Infinity;
  for (const [lng, lat] of ring) {
    if (lat < minLat) minLat = lat;
    if (lat > maxLat) maxLat = lat;
    if (lng < minLng) minLng = lng;
    if (lng > maxLng) maxLng = lng;
  }
  return [(minLat + maxLat) / 2, (minLng + maxLng) / 2];
}

// ─── Layer rendering ──────────────────────────────────────────────────────────

function filterAndRenderAll() {
  const geometry = hpState.selectedFeature?.geometry || null;

  for (const key of Object.keys(HP_CONFIG.DATASETS)) {
    clearLayer(key);
    if (!hpState.activeLayers.has(key)) {
      hpState.visibleCounts[key] = 0;
      continue;
    }
    const filtered = geometry && hpState.centroid
      ? filterFeatures(hpState.rawData[key], geometry, hpState.centroid, hpState.radius)
      : (hpState.rawData[key] || []);
    hpState.visibleCounts[key] = filtered.length;
    renderLayer(key, filtered);
  }
}

function clearLayer(key) {
  if (hpState.layerGroups[key]) {
    hpState.map.removeLayer(hpState.layerGroups[key]);
    hpState.layerGroups[key] = null;
  }
}

function renderLayer(key, features) {
  const cfg = HP_CONFIG.DATASETS[key];
  const group = L.layerGroup();

  for (const feature of features) {
    const [lng, lat] = feature.geometry.coordinates;
    const props = feature.properties || {};

    const marker = L.circleMarker([lat, lng], {
      radius: cfg.radius,
      color: cfg.color,
      fillColor: cfg.fillColor,
      fillOpacity: 0.75,
      weight: 1.2,
      opacity: 1,
    });

    const label = featureDisplayName(props, key);
    marker.bindTooltip(label, { sticky: true, className: "hp-tip" });

    marker.on("click", () => {
      const distM = hpState.centroid
        ? haversineM(lat, lng, hpState.centroid[0], hpState.centroid[1])
        : null;
      showFeatureDetail(feature, key, distM);
    });

    group.addLayer(marker);
  }

  hpState.layerGroups[key] = group;
  group.addTo(hpState.map);
}

function featureDisplayName(props, key) {
  if (key === "monuments" && props.name) return props.name;
  if (props.monument_class) return shortMonumentClass(props.monument_class);
  if (props.monument_type) return shortMonumentClass(props.monument_type);
  if (props.smrs) return props.smrs;
  return HP_CONFIG.DATASETS[key].label;
}

function shortMonumentClass(cls) {
  if (!cls) return "Heritage Feature";
  // "Ritual site - holy well" → "Ritual site"
  return cls.split(" - ")[0].trim();
}

function titleCaseName(name) {
  return String(name || "")
    .toLowerCase()
    .replace(/\b([a-z])/g, (m) => m.toUpperCase())
    .replace(/\bOr\b/g, "or");
}

// ─── Right panel ──────────────────────────────────────────────────────────────

function showRightPanel(mode) {
  hpState.rightMode = mode;
  const ids = ["hp-right-empty", "hp-right-townland", "hp-right-feature"];
  for (const id of ids) hide(id);
  if (mode === "empty") show("hp-right-empty");
  else if (mode === "townland") show("hp-right-townland");
  else if (mode === "feature") show("hp-right-feature");
}

function updateRightTownlandPanel() {
  const name = hpState.selectedName;
  if (!name) return;

  const title = $("hp-right-tl-title");
  if (title) title.textContent = "Townland Overview";
  $("hp-right-tl-name").textContent = name;

  // Stat chips
  const statRow = $("hp-stat-row");
  statRow.innerHTML = "";
  for (const [key, cfg] of Object.entries(HP_CONFIG.DATASETS)) {
    const count = hpState.visibleCounts[key];
    const chip = document.createElement("div");
    chip.className = "hp-stat-chip";
    chip.innerHTML = `
      <span class="hp-stat-label">
        <span class="hp-stat-dot" style="background:${cfg.color};"></span>
        ${cfg.label}
      </span>
      <span class="hp-stat-count">${count}</span>
    `;
    statRow.appendChild(chip);
  }

  // Narrative summary
  const narrative = buildNarrative(name);
  const narrativeEl = $("hp-narrative");
  narrativeEl.textContent = narrative;
  narrativeEl.style.display = "";

  // Back-links
  const censusLink = $("hp-right-census-link");
  const homeLink = $("hp-right-home-link");
  if (censusLink) censusLink.style.display = "block";
  if (homeLink) homeLink.style.display = "block";
  if (censusLink) censusLink.href = `/census?townland=${encodeURIComponent(name)}`;
  if (homeLink) homeLink.href = `/?townland=${encodeURIComponent(name)}#explore`;

  // Google Maps links in right panel
  const gmapsDiv = $("hp-right-gmaps-links");
  if (gmapsDiv && hpState.centroid) {
    const [lat, lng] = hpState.centroid;
    const viewUrl = googleMapsUrl("view", lat, lng, name);
    const directionsUrl = googleMapsUrl("directions", lat, lng, name);
    gmapsDiv.innerHTML = `
      <div class="panel-title" style="margin-bottom:6px;">Open in Google Maps</div>
      <div class="hp-coordinate-readout">
        Coordinates: <code>${formatCoord(lat)}, ${formatCoord(lng)}</code>
      </div>
      <div class="hp-gmaps-btns">
        <a href="${viewUrl}" target="_blank" rel="noopener noreferrer" class="hp-gmaps-btn">
          <span>View Exact Coordinates</span><span aria-hidden="true">↗</span>
        </a>
        <a href="${directionsUrl}" target="_blank" rel="noopener noreferrer" class="hp-gmaps-btn">
          <span>Get Directions</span><span aria-hidden="true">↗</span>
        </a>
      </div>
    `;
  }

  showRightPanel("townland");
}

function updateAllOverviewPanel() {
  const title = $("hp-right-tl-title");
  if (title) title.textContent = "All Townlands Overview";
  $("hp-right-tl-name").textContent = "Coolattin historic landscape";

  const statRow = $("hp-stat-row");
  statRow.innerHTML = "";
  for (const [key, cfg] of Object.entries(HP_CONFIG.DATASETS)) {
    const count = hpState.visibleCounts[key];
    const chip = document.createElement("div");
    chip.className = "hp-stat-chip";
    chip.innerHTML = `
      <span class="hp-stat-label">
        <span class="hp-stat-dot" style="background:${cfg.color};"></span>
        ${cfg.label}
      </span>
      <span class="hp-stat-count">${count}</span>
    `;
    statRow.appendChild(chip);
  }

  const narrativeEl = $("hp-narrative");
  const townlandCount = hpState.townlandsGeo?.features?.length || 0;
  narrativeEl.textContent =
    `Showing ${townlandCount} Coolattin townlands and all currently enabled Wicklow heritage features. ` +
    "Click any townland outline to focus the page, show its nearby sites, and get coordinate-based Google Maps directions.";
  narrativeEl.style.display = "";

  const gmapsDiv = $("hp-right-gmaps-links");
  if (gmapsDiv) {
    gmapsDiv.innerHTML = `
      <div class="panel-title" style="margin-bottom:6px;">How to use this map</div>
      <div class="hp-coordinate-readout">
        All townlands are visible. Use the layer toggles on the left to reduce marker clutter,
        then click a townland polygon or marker for detail.
      </div>
    `;
  }

  const censusLink = $("hp-right-census-link");
  const homeLink = $("hp-right-home-link");
  if (censusLink) censusLink.style.display = "none";
  if (homeLink) homeLink.style.display = "none";

  showRightPanel("townland");
}

function buildNarrative(name) {
  const radiusLabel =
    hpState.radius === 0
      ? "within the townland boundary"
      : `within ${hpState.radius / 1000} km of the townland`;

  const parts = [];
  const counts = hpState.visibleCounts;
  if (counts.asi > 0) parts.push(`${counts.asi} archaeological site${counts.asi !== 1 ? "s" : ""}`);
  if (counts.holywells > 0) parts.push(`${counts.holywells} holy well${counts.holywells !== 1 ? "s" : ""}`);
  if (counts.monuments > 0) parts.push(`${counts.monuments} monument${counts.monuments !== 1 ? "s" : ""} to visit`);

  if (parts.length === 0) {
    return `No recorded heritage features found ${radiusLabel} for ${escapeHtml(name)}. Try widening the radius.`;
  }

  const joined =
    parts.length === 1
      ? parts[0]
      : parts.slice(0, -1).join(", ") + " and " + parts[parts.length - 1];

  return `The historic landscape ${radiusLabel} of ${escapeHtml(name)} contains ${joined}.`;
}

// ─── Feature detail panel ─────────────────────────────────────────────────────

function showFeatureDetail(feature, key, distM) {
  const props = feature.properties || {};
  const cfg = HP_CONFIG.DATASETS[key];
  const [lng, lat] = feature.geometry.coordinates;

  const name = featureDisplayName(props, key);
  const fullClass = props.monument_class || props.monument_type || cfg.label;
  const townland = props.townland || props.locality || "—";
  const smrs = props.smrs || props.id || "—";
  const sourceLink = props.source_link || props.external_link || "";
  const notes = props.notes || "";
  const distStr = distM != null ? `${(distM / 1000).toFixed(2)} km from ${hpState.selectedName}` : "";

  const gmapsFeatureUrl = googleMapsUrl("view", lat, lng, name);
  const gmapsDirectionsUrl = googleMapsUrl("directions", lat, lng, name);

  $("hp-feature-content").innerHTML = `
    <div class="hp-feature-card">
      <div class="hp-feature-type" style="color:${cfg.color};">${cfg.label}</div>
      <div class="hp-feature-name">${escapeHtml(name)}</div>
      <div class="hp-feature-meta">
        ${fullClass ? `<div><strong>Class:</strong> ${escapeHtml(fullClass)}</div>` : ""}
        ${townland !== "—" ? `<div><strong>Townland:</strong> ${escapeHtml(townland)}</div>` : ""}
        ${smrs !== "—" ? `<div><strong>SMRS Ref:</strong> ${escapeHtml(smrs)}</div>` : ""}
        ${distStr ? `<div><strong>Distance:</strong> ${escapeHtml(distStr)}</div>` : ""}
        <div><strong>Coordinates:</strong> ${lat.toFixed(5)}, ${lng.toFixed(5)}</div>
      </div>
      ${notes ? `<div class="hp-feature-notes">${escapeHtml(notes).substring(0, 600)}${notes.length > 600 ? "…" : ""}</div>` : ""}
      <div class="hp-feature-links">
        <a href="${gmapsFeatureUrl}" target="_blank" rel="noopener noreferrer" class="hp-gmaps-btn">
          <span>View Exact Coordinates</span><span aria-hidden="true">↗</span>
        </a>
        <a href="${gmapsDirectionsUrl}" target="_blank" rel="noopener noreferrer" class="hp-gmaps-btn">
          <span>Get Directions</span><span aria-hidden="true">↗</span>
        </a>
        ${sourceLink ? `<a href="${escapeHtml(sourceLink)}" target="_blank" rel="noopener noreferrer" class="btn soft" style="font-size:13px;text-align:center;">View on archaeology.ie ↗</a>` : ""}
      </div>
    </div>
  `;

  showRightPanel("feature");
}

function escapeHtml(str) {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ─── Google Maps links ────────────────────────────────────────────────────────

function googleMapsUrl(type, lat, lng, name) {
  const coord = `${formatCoord(lat)},${formatCoord(lng)}`;
  switch (type) {
    case "view":
      return `https://www.google.com/maps/search/?api=1&query=${coord}`;
    case "search":
      return `https://www.google.com/maps/search/?api=1&query=${coord}`;
    case "directions":
      return `https://www.google.com/maps/dir/?api=1&destination=${coord}&travelmode=driving`;
    case "streetview":
      return `https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=${coord}`;
    default:
      return `https://www.google.com/maps/search/?api=1&query=${coord}`;
  }
}

function formatCoord(value) {
  return Number(value).toFixed(6);
}

function updateGoogleMapsLinks() {
  if (!hpState.centroid || !hpState.selectedName) return;
  const [lat, lng] = hpState.centroid;
  const name = hpState.selectedName;

  const viewEl = $("hp-gmaps-view");
  const directionsEl = $("hp-gmaps-directions");
  const svEl = $("hp-gmaps-sv");
  const coordEl = $("hp-coordinate-readout");

  if (viewEl) viewEl.href = googleMapsUrl("view", lat, lng, name);
  if (directionsEl) directionsEl.href = googleMapsUrl("directions", lat, lng, name);
  if (svEl) svEl.href = googleMapsUrl("streetview", lat, lng, name);
  if (coordEl) coordEl.innerHTML = `Coordinates: <code>${formatCoord(lat)}, ${formatCoord(lng)}</code>`;
}

// ─── Area photos ──────────────────────────────────────────────────────────────

function showPhotosSection(visible) {
  const section = $("hp-photos-section");
  if (section) section.style.display = visible ? "" : "none";
}

async function fetchAndRenderPhotos(lat, lng, townlandName) {
  const sub = $("hp-photos-sub");
  const grid = $("hp-photos-grid");
  const loading = $("hp-photos-loading");

  if (!grid) return;

  grid.innerHTML = `<p class="note" id="hp-photos-loading">Searching for photos of ${escapeHtml(townlandName)} and nearby places…</p>`;
  if (sub) sub.textContent = `Near ${townlandName}, County Wicklow`;

  try {
    const photos = await fetchPhotosWikipedia(lat, lng);
    renderPhotos(photos, grid, townlandName);
  } catch (e) {
    console.warn("heritage.js: photo fetch failed", e);
    renderPhotos([], grid, townlandName);
  }
}

async function fetchPhotosWikipedia(lat, lng) {
  const url = new URL("https://en.wikipedia.org/w/api.php");
  url.searchParams.set("action", "query");
  url.searchParams.set("prop", "pageimages|extracts");
  url.searchParams.set("format", "json");
  url.searchParams.set("piprop", "thumbnail");
  url.searchParams.set("pithumbsize", "400");
  url.searchParams.set("pilimit", "8");
  url.searchParams.set("exintro", "1");
  url.searchParams.set("explaintext", "1");
  url.searchParams.set("exchars", "180");
  url.searchParams.set("generator", "geosearch");
  url.searchParams.set("ggscoord", `${lat}|${lng}`);
  url.searchParams.set("ggsradius", HP_CONFIG.PHOTO_SEARCH_RADIUS_M);
  url.searchParams.set("ggslimit", "12");
  url.searchParams.set("origin", "*");

  const res = await fetch(url.toString());
  if (!res.ok) throw new Error(`Wikipedia API error ${res.status}`);
  const data = await res.json();

  const pages = data?.query?.pages || {};
  return Object.values(pages)
    .filter((p) => p.thumbnail)
    .map((p) => ({
      title: p.title,
      thumb: p.thumbnail.source,
      extract: p.extract || "",
      url: `https://en.wikipedia.org/wiki/${encodeURIComponent(p.title.replace(/ /g, "_"))}`,
    }));
}

function renderPhotos(photos, grid, townlandName) {
  if (!photos || photos.length === 0) {
    grid.innerHTML = `
      <div style="grid-column:1/-1;padding:1.25rem;text-align:center;color:var(--ink-muted,#64748b);">
        <p style="margin:0 0 10px;font-size:13.5px;">No photos found via Wikipedia for this area.</p>
        <a href="https://www.google.com/search?q=${encodeURIComponent(townlandName + " County Wicklow Ireland")}&tbm=isch"
           target="_blank" rel="noopener noreferrer"
           class="btn soft" style="font-size:13px;">
          Search Google Images ↗
        </a>
        <a href="https://www.flickr.com/search/?text=${encodeURIComponent(townlandName + " Wicklow")}&sort=relevance"
           target="_blank" rel="noopener noreferrer"
           class="btn ghost" style="font-size:13px;margin-left:8px;">
          Search Flickr ↗
        </a>
      </div>
    `;
    return;
  }

  grid.innerHTML = photos
    .map(
      (p) => `
      <a href="${escapeHtml(p.url)}" target="_blank" rel="noopener noreferrer"
         class="hp-photo-card" style="text-decoration:none;">
        <img src="${escapeHtml(p.thumb)}" alt="${escapeHtml(p.title)}"
             class="hp-photo-img" loading="lazy"
             data-hide-closest=".hp-photo-card">
        <div class="hp-photo-body">
          <div class="hp-photo-title">${escapeHtml(p.title)}</div>
          ${p.extract ? `<div class="hp-photo-caption">${escapeHtml(p.extract.substring(0, 90))}…</div>` : ""}
        </div>
      </a>
    `
    )
    .join("");
}

// ─── Loading overlay ──────────────────────────────────────────────────────────

function showLoading(visible) {
  const el = $("hp-loading");
  if (el) el.style.display = visible ? "flex" : "none";
}

// ─── Event wiring ─────────────────────────────────────────────────────────────

function wireEvents() {
  // Radius radio buttons
  document.querySelectorAll('input[name="hp-radius"]').forEach((radio) => {
    radio.addEventListener("change", () => {
      hpState.radius = parseInt(radio.value, 10);
      if (hpState.selectedFeature) {
        drawTownlandHighlight(hpState.selectedFeature);
        filterAndRenderAll();
        updateRightTownlandPanel();
        updateUrl();
      } else {
        filterAndRenderAll();
        updateAllOverviewPanel();
        updateUrl();
      }
    });
  });

  // Layer checkboxes
  document.querySelectorAll(".hp-layer-cb").forEach((cb) => {
    cb.addEventListener("change", () => {
      const key = cb.dataset.layer;
      if (cb.checked) hpState.activeLayers.add(key);
      else hpState.activeLayers.delete(key);
      filterAndRenderAll();
      if (hpState.selectedFeature) {
        if (hpState.rightMode === "townland") updateRightTownlandPanel();
      } else {
        updateAllOverviewPanel();
      }
      updateUrl();
    });
  });

  // Reset button
  const resetBtn = $("hp-reset-btn");
  if (resetBtn) {
    resetBtn.addEventListener("click", () => {
      hpState.radius = HP_CONFIG.DEFAULT_RADIUS_M;
      hpState.activeLayers = new Set(["asi", "holywells", "monuments"]);
      syncRadioButtons();
      syncLayerCheckboxes();
      if (hpState.selectedFeature) {
        drawTownlandHighlight(hpState.selectedFeature);
        filterAndRenderAll();
        updateRightTownlandPanel();
        updateUrl();
      } else {
        showAllLandscape();
      }
    });
  }

  // Feature-detail back button
  const featureBack = $("hp-feature-back");
  if (featureBack) {
    featureBack.addEventListener("click", () => {
      if (hpState.selectedFeature) showRightPanel("townland");
      else updateAllOverviewPanel();
    });
  }

  const townlandSelect = $("hp-townland-select");
  if (townlandSelect) {
    townlandSelect.addEventListener("change", () => {
      if (townlandSelect.value) selectTownlandByName(townlandSelect.value);
      else showAllLandscape({ fitBounds: true });
    });
  }
}

function syncRadioButtons() {
  document.querySelectorAll('input[name="hp-radius"]').forEach((radio) => {
    radio.checked = parseInt(radio.value, 10) === hpState.radius;
  });
}

function syncLayerCheckboxes() {
  document.querySelectorAll(".hp-layer-cb").forEach((cb) => {
    cb.checked = hpState.activeLayers.has(cb.dataset.layer);
  });
}
