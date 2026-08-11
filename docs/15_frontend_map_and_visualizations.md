# 15. Frontend: Map & Visualization JavaScript

Technical reference for the five most complex client-side files in the Coolattin Estate Records Explorer. All are vanilla JavaScript (no bundler, no build step). There is no `import`/`export` module system — every file relies on global functions/variables and script load order declared in the Jinja2 templates.

Covered here:

| File | Lines | Page(s) | Purpose |
|---|---|---|---|
| `frontend/static/js/map.js` | 178 | shared (loaded in `base.html`) | Leaflet basemap layer switcher utility |
| `frontend/static/js/census.js` | 916 | `/census` | Population choropleth, year slider, townland detail panel |
| `frontend/static/js/heritage.js` | 985 | `/heritage` | Heritage monument map, spatial filtering, photo lookup |
| `frontend/static/js/kg_explore.js` | 539 | `/kg-explore` | D3.js force-directed geographic knowledge graph |
| `frontend/static/js/ask.js` | 1247 | `/ask` | SSE-driven LLM Q&A chat UI (most complex file in the app) |

External libraries used: **Leaflet 1.9.4** (CDN, `unpkg.com`, loaded once in `base.html`), **D3 v7** (CDN, `cdn.jsdelivr.net`, loaded only in `kg_explore.html`), **marked.min.js** (vendored locally at `frontend/static/js/marked.min.js`, loaded only in `ask.html`). No React/Vue, no npm-bundled frontend code — the `@supabase/supabase-js` and `leaflet` npm packages listed in `package.json` are not consumed as ES modules; Leaflet is loaded via the CDN `<script>` tag instead.

---

## 1. `map.js` — shared Leaflet layer-switcher utility

`map.js` is loaded globally by `base.html` (`<script src="{{ url_for('static', filename='js/map.js') }}?v=6"></script>`, immediately before `main.js`), so its three exported functions are available as global functions on every page without a per-page `<script>` include. It does **not** create any Leaflet map instance itself — the comment block at the top of the file is explicit about this:

```js
// map.js is a utility library only — no map initialisation here.
// The home page map (index.html) is managed exclusively by main.js.
// The census page map uses initLayerSwitcher() from census.js.
```

### Exported functions

| Function | Signature | Purpose |
|---|---|---|
| `initLayerSwitcher` | `async (mapInstance, containerId) => { layerMap, overlayMap, config }` | Fetches layer config, builds `L.TileLayer` instances, adds the default/saved layer to the map, renders the switcher button UI |
| `buildLayerMap` | `(layers) => { [id]: L.TileLayer }` | Converts a layer-config array into a dict of Leaflet tile layer objects |
| `switchLayer` | `(mapInstance, layerMap, layerId) => void` | Removes all layers in `layerMap` from the map, adds the requested one, persists the choice to `localStorage` |

### Config fetch and fallback

`initLayerSwitcher` calls `GET /api/map/layers` (response shape consumed here: `{ default, layers: [...], overlays: [...] }`, each layer/overlay object having `id`, `label`, `tile_url`, `attribution`, `max_zoom`, `description`). On fetch failure or a non-OK response, `_fallbackLayerConfig()` supplies a hardcoded three-layer config (`standard` = OSM tiles, `satellite` = Esri World Imagery, `terrain` = OpenTopoMap) plus one overlay (`labels_overlay` = Esri World Boundaries and Places, used to add place labels on top of otherwise label-free satellite imagery).

```js
async function initLayerSwitcher(mapInstance, containerId) {
  let config;
  try {
    const res = await fetch("/api/map/layers");
    config = res.ok ? await res.json() : _fallbackLayerConfig();
  } catch (e) {
    config = _fallbackLayerConfig();
  }
  ...
}
```

### Layer construction

`buildLayerMap(layers)` builds one `L.tileLayer(layer.tile_url, { attribution, maxZoom: layer.max_zoom || 19 })` per entry, keyed by `layer.id`. Tile URLs are never hardcoded outside this fallback — the file's header comment states this is a deliberate architectural rule ("Tile URLs are NEVER hardcoded here — always from the backend config").

### Persistence and auto-behavior

- The active base layer id is persisted to `localStorage["coolattin_map_layer"]` on every switch.
- On init, `initLayerSwitcher` restores this saved preference if it differs from the config's declared default, swapping the layer in immediately.
- **Satellite → Labels auto-toggle**: if the active/restored layer is `"satellite"` and a `labels_overlay` overlay exists, that overlay is auto-added on top (raw satellite tiles carry no place names). The same auto-toggle logic re-runs on every button click inside `_renderSwitcherUI`: switching *to* `"satellite"` adds the labels overlay, switching *away* removes it.

### Switcher UI rendering

`_renderSwitcherUI` clears and rebuilds `#<containerId>` (class `layer-switcher`) with one `<button class="layer-btn">` per layer, `data-layer-id` set to the layer id, `.active` class applied to whichever layer id matches `localStorage` (or the config default). Clicking a button: clears `.active` from siblings, adds it to itself, calls `switchLayer`, and re-runs the satellite/labels toggle.

### Callers

- `census.js` line 125: `const { layerMap } = await initLayerSwitcher(map, "census-map-layer-switcher");` — the resolved `layerMap` is stashed on `state.layerMap` but not read elsewhere in the file.
- `heritage.js` line 156: `initLayerSwitcher(hpState.map, "hp-layer-switcher-container");` — return value discarded (fire-and-forget).
- The home page's map (managed by `main.js`, out of scope for this doc) is explicitly excluded from this shared mechanism per the header comment.

---

## 2. `census.js` — Census choropleth explorer

Runs inside a single `DOMContentLoaded` async handler — nearly everything, including the map instance and all fetch logic, lives inside that one listener's closure. Uses a `$ = (id) => document.getElementById(id)` shorthand, independently redeclared (not shared with the other files' identical helpers).

### 2.1 State object

```js
const state = {
  year: 1841,
  selectedTownland: null,
  townlandDetails: null,
  workhouseData: null,
  unifiedSummary: null,
  recordsByTownlandYear: {},   // key(townland, year) -> record
  summaryByYear: {},           // year -> summary payload
  geoLayer: null,               // the L.geoJSON layer for townland polygons
  geoFeatureByName: {},         // lowercased TL_ENGLISH -> GeoJSON feature
  geoNameToCanonical: {},       // short GeoJSON name -> full DB townland name
  satelliteMap: null,           // secondary Leaflet map instance (detail panel)
  satelliteOverlay: null,
  layerMap: {},                 // from initLayerSwitcher
};
```

`key(townland, year)` builds a composite lookup key as `` `${townland.toLowerCase()}|${year}` ``.

### 2.2 Year sets

```js
const CENSUS_YEARS  = [1841, 1851, 1861, 1871, 1881, 1891];       // official census
const SURVEY_YEARS  = [1827, 1839, 1848, 1850, 1860, 1868];       // Coolattin estate GeoJSON survey years (total only)
const ALL_DATA_YEARS = [...SURVEY_YEARS, ...CENSUS_YEARS];
```

Estate survey years only carry total population (no male/female/house breakdown); this distinction propagates through nearly every rendering function via the `r.source === "json"` check (estate survey rows are tagged `source: "json"` by the backend; official census rows are tagged `"csv_seed"` or `"kg"`).

### 2.3 Data loading sequence (on `DOMContentLoaded`)

1. `L.map("censusMap", { minZoom: 8, maxZoom: 15 }).setView([52.95, -6.4], 10)` — creates the main Leaflet map bound to the `#censusMap` container.
2. `await initLayerSwitcher(map, "census-map-layer-switcher")` from `map.js`.
3. `fetch("/static/data/townlands.json")` — loads the authoritative GeoJSON townland reference. The set of valid townland names (`f.properties.TL_ENGLISH`, lowercased/trimmed) is built into `validTownlandNames`; only census rows whose `townland` field matches this set are indexed, filtering out non-Coolattin-estate rows that might otherwise appear in the census API response.
4. Loop over `ALL_DATA_YEARS` (12 years total): `fetch(`/api/census/?year=${y}&limit=2000`)` for each. Response can be either a bare array or `{ data: [...], meta: {...} }`; `responseMeta` is captured only from the first standard census year (1841) when the payload has a `meta` object. Each row is stored at `state.recordsByTownlandYear[key(r.townland, r.year)]` if its townland passes the `validTownlandNames` filter.
5. `updateDataSourceBadge(responseMeta)` — see §2.5.
6. Loop over `CENSUS_YEARS` only (not survey years): `fetch(`/api/census/summary?year=${y}`)`, stored in `state.summaryByYear[y]`.
7. **Alias-map construction**: builds `state.geoNameToCanonical` by checking, for every GeoJSON `TL_ENGLISH` name lacking an exact census-record match, whether any loaded DB townland name is a prefix-match (`dbName.startsWith(nm + " ")`). This is how a GeoJSON entry like `"newtown"` resolves to a DB row stored as `"newtown ed powerscourt"`.
8. Builds `state.geoLayer = L.geoJSON(geo, { style, onEachFeature }).addTo(map)` — see §2.6/2.7.
9. `map.fitBounds(state.geoLayer.getBounds())`.
10. Wires the year `<input>` slider (`#yearSlider`, `#yearValue`).
11. Wires a `window.addEventListener("languageChanged", ...)` handler that re-renders the detail panel (i18n integration with `window.t()`).
12. Calls `renderTownlandPanel(null)` to show the empty state.
13. **URL param auto-select** (end of file): if the page was opened with `?townland=NAME` (e.g. a deep link from the home page "View Census Data" action), the matching GeoJSON feature is located case-insensitively, selected programmatically, and the map zoomed to it via `fitBounds` with `maxZoom: 14`; `window.history.replaceState` strips the param so a refresh doesn't re-trigger it.

### 2.4 Choropleth color scale

Fixed 7-bucket sequential red scale, hand-coded (not D3/Chroma):

```js
function getColor(total) {
  const v = Number(total || 0);
  if (v > 350) return "#7f1d1d";
  if (v > 220) return "#b91c1c";
  if (v > 140) return "#dc2626";
  if (v > 80)  return "#ef4444";
  if (v > 40)  return "#f87171";
  if (v > 0)   return "#fecaca";
  return "#fef2f2";
}
```

`choroplethStyle(total, isSelected)` returns the Leaflet style object: `color: "#7f1d1d"` (dark red outline always), `weight: isSelected ? 2.5 : 1`, `fillColor: getColor(total)`, `fillOpacity: 0.65`. There is no dynamically-computed legend/breaks (e.g. via D3 quantile scales) — the six thresholds (40/80/140/220/350) are static values chosen by inspection, not data-driven.

### 2.5 Data-source status badge

`updateDataSourceBadge(meta)` populates `#census-data-source` / `#census-source-label` / `#census-last-updated` from the API's `meta.cache_status` (`hit` / `stale_refresh` / `miss`) and `meta.source` (`database` / `kg_refresh` / `csv_seed`), each mapped to a colored badge (green "Cached", yellow "Serving cached (refresh queued)", blue "Freshly loaded") plus a human-readable last-updated timestamp from `meta.generated_at`.

### 2.6 "Best record" fallback logic

Because estate-survey-only townlands have sparse year coverage, `getBestRecord(townland, preferredYear)` does not simply look up the exact year — if no exact match exists it scans every year in `ALL_DATA_YEARS` for that townland and picks whichever has the smallest `Math.abs(y - preferredYear)`. This is used both for choropleth fill color (`state.geoLayer`'s `style` callback) and for KPI-card population in the detail panel, so a townland with only 1848/1860 survey data still colors and populates sensibly when the year slider is set to, say, 1861.

### 2.7 Map interactivity

`onEachFeature` on the GeoJSON layer wires:
- **`click`**: sets `state.selectedTownland`, then fires `Promise.all([fetchTownlandDetails, fetchWorkhouseByTownland, fetchUnifiedSummary])` in parallel, stores results on `state`, then calls `recolorMap()` (to draw the selection outline) and `renderTownlandPanel(nm)`.
- **`bindTooltip`**: hover tooltip shows `<b>NAME</b><br/>Population {year}: {value}`. If the displayed record came from `getBestRecord` rather than an exact-year match, the year label is prefixed with `~` (e.g. `~1848`) to signal approximation.

`recolorMap()` re-applies `state.geoLayer.setStyle(...)` for every feature (recomputing fill color + selection outline for the new year) and also refreshes every layer's tooltip content via `layer.setTooltipContent(...)`.

### 2.8 Detail-panel data fetches

Three parallel fetches per townland click:

| Function | Endpoint | Purpose |
|---|---|---|
| `fetchTownlandDetails` | `GET /api/census/townland?name=<name>` | KG-enriched metadata: Gaelic name, parish/barony/county, centroid, VRTI/OSI/OSM identifiers, description, links, images |
| `fetchWorkhouseByTownland` | `GET /api/unified/workhouse-by-townland?townland=<name>` | Entity-resolution results: `{ linked: [...], unlinked: [...] }` workhouse mentions matched (or not) to estate records for this townland |
| `fetchUnifiedSummary` | `GET /api/unified/records?townland=<name>` | Client-side aggregated `{ total, tenancy, eviction, emigration }` counts computed by filtering the returned record array on `has_tenancy_record` / `has_eviction_record` / `has_emigration_record` flags |

### 2.9 `renderTownlandPanel(townland)` — the detail panel

The largest function in the file (~430 lines). When `townland` is `null`, all sub-panels are cleared and a placeholder message shown. Otherwise it builds, in order:

1. **Title**: townland name plus Gaelic name in italic if present.
2. **Meta line**: "Viewing census year N…" plus an "Explore Records →" link to `/?townland=<name>` (deep link into the home-page map explorer).
3. **Satellite sub-map** (`updateSatelliteView`, §2.11).
4. **Full KG detail panel** (`#censusTownlandDetail`), built as a sequence of styled `<div>` cards, conditionally included: KG source card (link to `virtualtreasury.ie` + the specific `kg_uri`, only if `d.uri`/`d.kg_uri` present); Irish/Gaelic name card; administrative location card (Parish/Barony/County rows); centroid coordinates card with an "Open in OpenStreetMap" link built from `lat`/`lon`; placename-meaning card (`d.description`, `d.placename_theme`); identifiers card (VRTI id, OSI id, OSM id — the OSM id becomes a clickable `openstreetmap.org/relation/<id>` link); estate-records-summary card built from `state.unifiedSummary` (tenancy/eviction/emigration badges) with an "Explore on Map →" link; external-links card filtering `d.links` for `logainm.ie` and `townlands.ie` URLs specifically; historic-images grid (up to 6 images from `d.images`, `loading="lazy"`, `onerror` hides the broken `<img>`, click opens full-size in a new tab).
5. **Workhouse section** (`#censusWorkhouseSection`, via `renderWorkhouseSection`, §2.10) — rendered as a collapsible accordion built with an inline `onclick` handler (no `addEventListener`; the toggle logic is string-templated directly into the `onclick=""` attribute) that flips `display: none/block` and swaps the `▸`/`▾` arrow glyph and "Click to expand/collapse" hint text.
6. **KPI cards** (`#censusKpis`): Total Population, Male, Female, Inhabited Houses, Uninhabited Houses, Year — each built with the shared `kpiCard(label, value)` template. For estate-survey-derived records (`bestRec.source === "json"`), Male/Female/Houses show `"—"` since those fields weren't recorded, and an amber "approx" banner explains why (three different wordings depending on whether the exact year is missing vs. the entire townland lacks census coverage).
7. **Timeline table** (`#censusTimeline`): one row per year with data (`ALL_DATA_YEARS` filtered to years where `getBestRecord`/`getRecord` returns a non-null `total`), columns Year / Total / Male / Female / Houses / Source. Rows for the active year are highlighted (`background:#eff6ff`). Clicking a row (only if the row's year is in `CENSUS_YEARS`, i.e. not an estate-survey-only year) updates `state.year`, syncs the slider, and re-renders. If a townland has zero population data at all, a red "No census population data" box is shown instead, with a special-cased explanation for the townland literally named `"NEWTOWN"` (its data is filed under electoral-division variants that can't auto-match).
8. **Clearances strip**: reads `Total_Clearances` and `Clearances_1847`..`Clearances_1856` directly off the GeoJSON feature properties (not from the census API) and renders a per-year breakdown string.

### 2.10 `renderWorkhouseSection(wh)`

Splits `wh.linked` (algorithmically matched to an estate record via the entity-resolution pipeline) from `wh.unlinked` (unmatched mentions). Linked cards show: workhouse name, a "Confirmed"/"Possible" badge (green if `match_label === "CONFIRMED_MATCH"`, amber otherwise), workhouse place/year/age, the matched estate record's name/townland/year/role, a percentage match score (`match_score * 100`), and a fixed amber disclaimer: *"⚠ Please verify: this workhouse record may or may not refer to the same person as the estate record."* This is a direct UI expression of the `entity_resolution_candidates` / `workhouse_unified_links` tables described in the database-schema doc.

### 2.11 Satellite sub-map

`initSatelliteMap()` (lazy, called once) creates a second, independent Leaflet map bound to `#townlandSatelliteMap` with `zoomControl: false, attributionControl: false`, two stacked tile layers (Esri World Imagery + a semi-transparent Esri World Boundaries/Places overlay at `opacity: 0.5`), initial view `[52.95, -6.4]` zoom 10. `updateSatelliteView(geoFeature, townlandName)` removes any prior polygon overlay, draws the selected townland's GeoJSON geometry in green (`#22c55e`, `fillOpacity: 0.15`), and `fitBounds`s to it with `padding: [30, 30], maxZoom: 16`. This is a second, fully separate `L.map` instance from the main choropleth map — not a sub-layer of it.

---

## 3. `heritage.js` — Heritage monument map

Structured as top-level functions (not wrapped in a single closure like `census.js`) sharing a module-scope `hpState` object and `HP_CONFIG` constant. Entry point: `document.addEventListener("DOMContentLoaded", () => { initMap(); wireEvents(); startHeritagePage(); })`.

### 3.1 Configuration (`HP_CONFIG`)

```js
const HP_CONFIG = {
  COUNTY: "WICKLOW",
  DEFAULT_RADIUS_M: 2000,
  MAP_CENTER: [52.85, -6.35], MAP_ZOOM: 10, MAP_MIN_ZOOM: 8, MAP_MAX_ZOOM: 18,
  DATASETS: {
    asi:        { label: "Archaeological Sites", color: "#9c8a6e", fillColor: "#c4b090", radius: 6, file: "/static/data/asi_wicklow.geojson" },
    holywells:  { label: "Holy Wells",            color: "#2d7da0", fillColor: "#5aa5c8", radius: 7, file: "/static/data/holywells_wicklow.geojson" },
    monuments:  { label: "Monuments to Visit",    color: "#6d4e8c", fillColor: "#9b7abf", radius: 8, file: "/static/data/monuments_wicklow.geojson" },
  },
  PHOTO_SOURCE: "wikipedia",
  STREET_VIEW_KEY: "", FLICKR_KEY: "",  // unused placeholders — no key configured
  PHOTO_SEARCH_RADIUS_M: 10000,
  TOWNLANDS_FILE: "/static/data/townlands.json",
};
```

**Important — data source**: unlike the rest of the app, this page's markers are not sourced from the `heritage_feature` SQLite table via a `/api/*` endpoint. They are read directly by the browser from three static GeoJSON files under `/static/data/` (produced offline by `scripts/preprocess_heritage.py`), then filtered client-side against the townland polygon/radius. There is no `fetch("/api/...")` call anywhere in this file for heritage feature data itself — only for `townlands.json` and for the external Wikipedia photo API.

### 3.2 State (`hpState`)

Tracks: the Leaflet `map`, loaded `townlandsGeo`, `selectedName`/`selectedFeature`/`centroid` of the active townland, `radius` (meters, `0` means "boundary only"), `activeLayers` (a `Set` of dataset keys currently visible), `rawData` (per-dataset raw GeoJSON features, loaded once and cached — `if (hpState.rawData[key] !== null) return;` guards against re-fetching), `layerGroups` (per-dataset `L.layerGroup()` currently on the map), `visibleCounts` (post-filter counts per dataset, used for stat chips and narrative text), plus several Leaflet layer handles (`townlandPolygonLayer`, `townlandsBrowseLayer`, `nearbyLayer` (declared but unused), `radiusCircleLayer`) and `rightMode` (`"empty" | "townland" | "feature"`, controlling which right-hand panel is shown).

### 3.3 Map init

`initMap()`: `L.map("heritageMap", { center, zoom, minZoom, maxZoom })`, then calls the shared `initLayerSwitcher(hpState.map, "hp-layer-switcher-container")` from `map.js` — the same shared basemap mechanism as `census.js`.

### 3.4 Startup sequence (`startHeritagePage`)

1. `loadTownlandsGeo()` — fetches `townlands.json`, populates the `<select id="hp-townland-select">` dropdown (sorted alphabetically, English names title-cased for display via `titleCaseName`), and draws `drawTownlandBrowseLayer()` (all townland outlines, thin/pale by default, highlighted when selected).
2. Reads URL params (`townland`/`townlandId`, `radius`, `layers` — comma-separated dataset keys) via `readUrlParams()`.
3. `loadAllHeritageData()` — parallel `Promise.all` fetch of all three static GeoJSON datasets (skips any already cached in `hpState.rawData`).
4. If a `townland` URL param was present, auto-selects it (`selectTownlandByName`); otherwise calls `showAllLandscape({ fitBounds: true })` to show every townland and every heritage feature estate-wide.

### 3.5 Spatial filtering — no Turf.js, hand-rolled geometry

`filterFeatures(features, geometry, centroid, radiusM)` includes a feature if either:
- it falls inside the selected townland polygon (`pointInGeoJSONPolygon`, a hand-written ray-casting algorithm supporting both `Polygon` and `MultiPolygon` GeoJSON types via `pointInRing`), or
- (when `radiusM > 0`) it is within `radiusM` meters of the townland's centroid, computed with a hand-written `haversineM(lat1, lng1, lat2, lng2)` (Earth radius `6371000` m).

`polygonCentroid(geometry)` computes a bounding-box centroid (midpoint of min/max lat/lng across the outer ring) rather than a true area centroid — a deliberate simplification.

The radius UI offers named radio options mapping to `hpState.radius`, confirmed by the two hardcoded ring definitions in `drawTownlandHighlight`:

```js
const circles = [
  { radius: 2000, color: "#f59e0b", fillColor: "#fbbf24", dashArray: "10 6" },
  { radius: 5000, color: "#ef4444", fillColor: "#f87171", dashArray: "14 8" },
];
```

Both rings are always drawn (dashed circles) around the selected townland's centroid; whichever matches `hpState.radius` is rendered with heavier weight/opacity to indicate it is the active filter. `hpState.radius === 0` is the "boundary only" mode (no radius circle emphasized as active; `filterFeatures` then only includes features that fall inside the polygon).

### 3.6 Layer rendering

`renderLayer(key, features)` builds one `L.circleMarker([lat, lng], { radius: cfg.radius, color: cfg.color, fillColor: cfg.fillColor, fillOpacity: 0.75, weight: 1.2 })` per feature — no clustering library, plain circle markers, one `L.layerGroup()` per dataset key, toggled on/off via the `.hp-layer-cb` checkboxes calling `clearLayer`/`renderLayer`. Marker labels come from `featureDisplayName(props, key)`: monuments prefer `props.name`; otherwise falls back to `props.monument_class` or `props.monument_type` shortened via `shortMonumentClass` (splits on `" - "` and takes the first segment, e.g. `"Ritual site - holy well"` → `"Ritual site"`); final fallback is `props.smrs` (Sites and Monuments Record number) or the dataset's generic label.

Clicking a marker computes distance-from-selected-centroid (if any) via `haversineM` and calls `showFeatureDetail(feature, key, distM)`.

### 3.7 Right panel

Three mutually exclusive states toggled by `showRightPanel(mode)`: `#hp-right-empty`, `#hp-right-townland`, `#hp-right-feature`.

- **Townland mode** (`updateRightTownlandPanel`): renders one stat chip per dataset (colored dot + label + `visibleCounts[key]`), a narrative sentence from `buildNarrative(name)` (e.g. *"The historic landscape within 2 km of Ballinglen contains 3 archaeological sites, 1 holy well and 2 monuments to visit."*, pluralization handled inline), back-links to `/census?townland=<name>` and `/?townland=<name>#explore`, and Google Maps view/directions links built from the centroid.
- **All-townlands mode** (`updateAllOverviewPanel`): same stat-chip pattern but aggregated across the whole estate, generic "how to use this map" instructions instead of Google Maps links, back-links hidden.
- **Feature mode** (`showFeatureDetail`): a single feature card — type, name, class, townland, SMRS ref, distance from selected townland (if any), coordinates, notes (truncated to 600 chars with `…`), Google Maps view/directions links, and an optional `archaeology.ie` source link.

### 3.8 Google Maps link builder

`googleMapsUrl(type, lat, lng, name)` requires no API key — it builds public deep-link URLs:

| `type` | URL pattern |
|---|---|
| `view` / `search` | `https://www.google.com/maps/search/?api=1&query=<lat>,<lng>` |
| `directions` | `https://www.google.com/maps/dir/?api=1&destination=<lat>,<lng>&travelmode=driving` |
| `streetview` | `https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=<lat>,<lng>` |

### 3.9 Photo lookup (Wikipedia geosearch, no API key)

`fetchAndRenderPhotos(lat, lng, townlandName)` calls `fetchPhotosWikipedia`, which hits `en.wikipedia.org/w/api.php` directly from the browser (`origin: "*"` CORS param, the standard trick for anonymous cross-origin MediaWiki API access) using a `generator=geosearch` query: `prop=pageimages|extracts`, `ggscoord=<lat>|<lng>`, `ggsradius=10000` (10 km, from `PHOTO_SEARCH_RADIUS_M`), `ggslimit=12`, requesting 400px thumbnails and a 180-character intro extract. Results are filtered to only pages that returned a `thumbnail`. If zero photos come back, the grid instead shows "Search Google Images ↗" and "Search Flickr ↗" fallback links built from `google.com/search?...&tbm=isch` and `flickr.com/search/?text=...`. `HP_CONFIG.STREET_VIEW_KEY`/`FLICKR_KEY` are present as configuration hooks but unused (empty strings) — Street View and Flickr photo sources are not implemented, only referenced in a comment as future options.

### 3.10 Event wiring (`wireEvents`)

Radius radio buttons, layer checkboxes, "Reset" button (restores `DEFAULT_RADIUS_M` + all three layers active), feature-detail "back" button (returns to townland or all-overview panel), and the townland `<select>` dropdown — every handler ends by calling `updateUrl()`, which serializes `selectedName`/`radius`/active-`layers` back into the query string via `history.replaceState` (shareable/bookmarkable URLs, no page reload).

---

## 4. `kg_explore.js` — D3.js force-directed geographic knowledge graph

Single `DOMContentLoaded` handler wrapping the whole file. Renders an SVG (not Canvas) force-directed graph of the geographic hierarchy (County → Barony → Civil Parish → Townland) into `<svg id="kg-svg">` using D3 v7 (loaded from `cdn.jsdelivr.net/npm/d3@7/dist/d3.min.js` — the only CDN-loaded JS library among these five files besides Leaflet).

### 4.1 Data loading

`loadGraph()` fetches `GET /api/kg/graph` once, expecting `{ nodes: [...], edges: [...], meta: {...} }`. Stored in module-scope `allNodes`/`allEdges`. The `meta` object populates a static summary panel (`#kg-stats`): county/barony/parish/townland counts, count of townlands with a Gaelic name, and total node/edge counts. `#kg-node-count` shows `"{N} nodes · {M} edges"`. On fetch failure, `#kg-stats` displays the error message text directly.

### 4.2 Force simulation setup — exact parameters

```js
const nodes = allNodes.map(d => ({ ...d }));
const nodeById = new Map(nodes.map(n => [n.id, n]));
const edges = allEdges
  .map(e => ({ ...e, source: nodeById.get(e.source), target: nodeById.get(e.target) }))
  .filter(e => e.source && e.target);

simulation = d3.forceSimulation(nodes)
  .force("link", d3.forceLink(edges).id(d => d.id)
    .distance(e => {
      if (e.type === "county_barony")   return 140;
      if (e.type === "barony_parish")   return 90;
      if (e.type === "parish_townland") return 50;
      return 80;
    })
    .strength(e => {
      if (e.type === "county_barony")   return 0.6;
      if (e.type === "barony_parish")   return 0.5;
      if (e.type === "parish_townland") return 0.45;
      return 0.3;
    }))
  .force("charge", d3.forceManyBody().strength(d => {
    if (d.type === "County")      return -1200;
    if (d.type === "Barony")      return -600;
    if (d.type === "CivilParish") return -280;
    return -30;                    // Townland (the vast majority of nodes)
  }))
  .force("center", d3.forceCenter(W / 2, H / 2))
  .force("collide", d3.forceCollide().radius(d => (d.size || 8) + 3))
  .alphaDecay(0.02);
```

`W`/`H` come from `svgEl.getBoundingClientRect()` (falling back to `900 × 600` if the SVG hasn't been laid out yet). Notable design choices:
- Link `distance`/`strength` are tiered by hierarchy level (county↔barony links are longest/strongest at 140px/0.6, parish↔townland links shortest/weakest at 50px/0.45), producing a naturally clustered layout where townlands cluster tightly near their parish.
- Charge repulsion is strongly negative for County (-1200) and tapers down to a mild -30 for Townland nodes, so the townland layer (the bulk of nodes returned by this endpoint) doesn't blow the layout apart while counties/baronies space themselves out clearly.
- `alphaDecay(0.02)` is a slower-than-default cooldown (D3's default is ~0.0228), letting the simulation settle more gradually.
- `forceCollide` prevents node overlap using each node's `size` property (or `8` default) plus a 3px buffer.

### 4.3 SVG rendering

All rendering is SVG DOM elements bound via D3's data-join pattern (`.data().enter().append(...)`), not Canvas:
- `<defs><marker id="arrowhead">` — a directional arrowhead marker (`viewBox="0 -4 8 8"`, `refX: 14`) referenced by every edge line via `marker-end`.
- `gRoot = svg.append("g")` — a single group that receives the zoom/pan transform (§4.4); all links, nodes, and labels are children of `gRoot`.
- **Links**: `<line>` per edge, colored/weighted by `e.type`: `county_barony` = `#60a5fa` (blue, 1.5px, opacity 0.7), `barony_parish` = `#a78bfa` (purple, 1.0px, opacity 0.55), `parish_townland` = `#6ee7b7` (green, 0.6px, opacity 0.3), default = `#94a3b8` grey.
- **Nodes**: `<circle>` per node, `r = d.size || 8`, fill from `nodeColor(d)` (County `#0369a1`, Barony `#b45309`, CivilParish `#7c3aed`, Townland `d.color || #15803d`, default `#64748b`), `stroke: #0f172a`, `stroke-width: 1`, `cursor: pointer`.
- **Labels**: `<text>` rendered only for County/Barony/CivilParish nodes (Townland nodes — the overwhelming majority — get no persistent label, only a hover tooltip, to avoid clutter). Font size scales by tier: County 13px, Barony 10px, CivilParish 8px; fill color also tiered (`#bae6fd`/`#fde68a`/`#c4b5fd`); positioned via the tick handler at `d.y - d.size - 3` (just above the node).

### 4.4 Interactivity

- **Zoom/pan**: `d3.zoom().scaleExtent([0.03, 6]).on("zoom", event => gRoot.attr("transform", event.transform))`, bound to the root `svg` selection. A "Reset zoom" button (`#kg-reset-zoom`) does `svg.transition().duration(400).call(zoomBehavior.transform, d3.zoomIdentity)`.
- **Drag**: standard D3 drag lifecycle on each node circle — `start` sets `alphaTarget(0.3)` and pins `fx`/`fy` to the current position; `drag` updates `fx`/`fy` to the pointer; `end` resets `alphaTarget(0)` and releases `fx`/`fy` back to `null` (the node resumes floating under simulation forces once released).
- **Click-to-expand / detail**: clicking a node calls `showDetail(d)`, which calls `highlightNode(d.id)` (dims all non-adjacent nodes to `opacity: 0.15`, highlights the clicked node's own stroke gold `#fbbf24` at `stroke-width: 3`, boosts adjacent-edge opacity to `0.85` while dropping non-adjacent edges to `0.05`) and opens `#kg-detail-card` with type-specific content — §4.5. Clicking empty SVG space calls `clearSelection()` which restores default opacities/strokes.
- **Hover tooltip**: `#kg-tooltip`, positioned via `mousemove` at `(event.clientX + 14, event.clientY - 10)`, shown on `mouseover`/hidden on `mouseout`. Content varies by node type (Gaelic name, parish/barony/record-count for Townland; child-townland count for Barony/CivilParish).
- **Search** (`#kg-search`, debounced 180ms via `searchTimeout`): filters `allNodes` client-side (no network call) by substring match against `label`, `name_gaelic`, `civil_parish`, `barony`, `county` (all lowercased). Matches are highlighted the same way as `highlightNode` (opacity 1 vs 0.08, gold stroke) and a results list of up to 5 parish + 10 townland matches is rendered into `#kg-search-card` / `#kg-search-results`, with a "…and N more" overflow note past 15 total.

### 4.5 Detail panel content by node type

`showDetail(d)` dispatches on `d.type`:

- **Townland** (`showTownlandDetail`): immediately renders known fields (label, Gaelic name, parish/barony/county, electoral division, placename theme, centroid coordinates, `record_count`, a link to `d.kg_uri`), then asynchronously fetches `GET /api/kg/townland-rich/<name>` (`fetchRichTownlandDetail`) and appends a second block once it resolves (`renderRichDetail`), showing a spinner (`⟳` with a CSS `spin` animation) in the interim. The rich-detail block covers: people summary (total/emigrants/evicted/tenants for a year range), top surnames (as pill badges), a collapsible `<details>` census population table, a collapsible clearances/evictions list, heritage feature tags, external VRTI links, an LLM-generated narrative paragraph (`data.narrative`, or an amber error box if `data.narrative_error`), and a collapsible `<details>` block showing the LLM-generated SPARQL query (`data.generated_sparql`) plus its result-row count and any `data.sparql_error`.
- **CivilParish** (`showParishDetail`): lists all child Townland nodes (filtered client-side from `allNodes` by `n.civil_parish === d.label`), sorted alphabetically, capped at 30 with a "Showing first 30" note, each with its Gaelic name if present.
- **Barony** (`showBaronyDetail`): counts of child parishes and townlands, plus a comma-joined list of parish names.
- **County** (`showCountyDetail`): counts of child baronies, parishes, and townlands.

All child-node lookups for Parish/Barony/County detail panels are done by filtering the already-loaded `allNodes` array client-side — no additional network requests beyond the initial `/api/kg/graph` load (except the async townland-rich fetch).

### 4.6 Tick handler

```js
simulation.on("tick", () => {
  linkSel.attr("x1", e => e.source.x).attr("y1", e => e.source.y)
         .attr("x2", e => e.target.x).attr("y2", e => e.target.y);
  nodeSel.attr("cx", d => d.x).attr("cy", d => d.y);
  labelSel.attr("x", d => d.x).attr("y", d => (d.y || 0) - (d.size || 8) - 3);
});
```

Standard D3 force-layout tick pattern — positions are mutated in place on the bound data objects by the simulation and read directly here every frame.

---

## 5. `ask.js` — SSE-driven LLM Q&A chat interface

The largest and most intricate client file (1247 lines). Everything lives inside one `DOMContentLoaded` handler. It renders roughly 25 distinct output panels driven by a single JSON payload returned at the end of a Server-Sent-Events stream, plus a live per-stage progress tracker driven by intermediate SSE events.

### 5.1 DOM element map

The file opens with ~50 `const xEl = $("someId")` lookups (`askQuestion`, `askTownlandHint`, `askTownlandDropdown`, `askSubmit`, `askStatus`, `askProgress`, `askError`, `askResult`, `askActualAnswer`, `askLlmAnswer`, `askLlmMeta`, `askProvenance`, `askRetrievalLane`, `askTownlandResolution`, `askWarnings`, `askExplainBlock`/`askExplainContent`, `askSuggestionsBlock`/`askSuggestions`, `askInsightsBlock`/`askInsights`, `askFeedbackNote`/`askFeedbackUp`/`askFeedbackDown`/`askFeedbackStatus`, `askPdfLink`, `askKgBlock`/`askKgContent`, `askTable`, `askVrtiTable`, `askSqlRowCount`, `askKgSection`, `askKgResultBlock`, `askKgRowCount`, `askSparqlBlock`/`askSparqlQuery`, `askSqliteQuery`, `askSummary`, `askChartBlock`/`askChart`, `askSupportContextBlock`/`askSupportContext`, `llmStatus`, `askEstateOverview`/`askEstateOverviewBody`, `askLoadingBanner`/`askLoadingBannerText`, `askInlineStatus`/`askInlineStatusText`/`askInlineStage`). This confirms the template (`ask.html`) exposes one distinct container per logical section of the answer — the JS does not construct the outer page skeleton, only fills these containers.

### 5.2 SSE transport mechanism — `fetch()` + `ReadableStream`, not `EventSource`

The stream to `POST /api/ask/query` is consumed via `fetch()` with a manually-parsed `ReadableStream`, **not** the browser `EventSource` API. This is a necessary choice: `EventSource` only supports `GET` requests with no request body, but the Ask endpoint requires `POST` (question text + `townland_hint` in a JSON body), so a raw `fetch()`-based reader is the only option available.

```js
async function consumeSSEPost(url, body, onEvent) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) { const t = await res.text(); throw new Error(t || `Request failed (${res.status})`); }
  if (!res.body) throw new Error("Streaming response body not available.");

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");       // SSE events are separated by a blank line
    buffer = parts.pop() || "";                // keep any incomplete trailing event in the buffer
    for (const part of parts) {
      const lines = part.split("\n").map(l => l.trim()).filter(l => l.startsWith("data:"));
      if (!lines.length) continue;
      const dataText = lines.map(l => l.slice(5).trim()).join("\n");  // support multi-line "data:" payloads
      if (!dataText) continue;
      let _parsed;
      try { _parsed = JSON.parse(dataText); } catch { continue; }     // silently drop malformed frames
      onEvent(_parsed);
    }
  }
}
```

Parsing details worth noting: the reader manually implements the SSE wire-format grammar (`data: <payload>\n\n` frame boundaries on double-newline), decodes incrementally with `TextDecoder({ stream: true })` to correctly handle multi-byte UTF-8 characters split across chunk boundaries, tolerates multi-line `data:` fields by joining them with `\n`, and silently skips any frame whose JSON fails to parse (no error surfaced to the user for a single bad frame — the stream just continues). It does not parse `event:` or `id:` SSE fields — only `data:` lines are read, consistent with the backend's `_sse()` helper always sending a single JSON blob per `data:` line without a custom event name.

### 5.3 Event handling and the three event types

`runQuery()`'s callback distinguishes three `evt.type` values:

```js
(evt) => {
  if (!evt || !evt.type) return;
  if (evt.type === "progress") { setStage(evt); return; }
  if (evt.type === "error") throw new Error(evt.message || "Unknown stream error.");
  if (evt.type === "result") finalPayload = evt;
}
```

- `"progress"` → routed to `setStage(evt)`, which updates the live per-stage tracker (§5.4).
- `"error"` → thrown synchronously inside the callback, which propagates out through the `await consumeSSEPost(...)` call and is caught by `runQuery`'s outer `try/catch`, surfacing via `showError()`.
- `"result"` → the terminal event; its full payload is stashed in `finalPayload` and used for all downstream rendering once the stream loop exits. If the stream ends without ever receiving a `"result"` event, `runQuery` throws `"No final result received."`
- Any event with a missing/unrecognized `type` is silently ignored — there is no default/else branch, so unhandled types are simple no-ops.

### 5.4 Progress tracker — recognized stages

`progressOrder` is a fixed, ordered whitelist of nine stage keys the UI knows how to display, each with a human label:

```js
const progressOrder = [
  { key: "classifying_intent",  label: "Routing Question" },
  { key: "contacting_llm",      label: "Building SQL Query" },
  { key: "slot_filling",        label: "Slot Filling" },
  { key: "framing_query",       label: "Framing Query" },
  { key: "querying_database",   label: "Querying Database" },
  { key: "querying_subgraph",   label: "KG Townland Lookup" },
  { key: "querying_vrti_graph", label: "VRTI Geographic Context" },
  { key: "querying_fusion",     label: "Synthesising Answer" },
  { key: "preparing_output",    label: "Preparing Output" },
];
```

`setStage(evt)` stores any `evt.stage` key into `progressMap` (a `Map`) regardless of whether it's in `progressOrder`, tracking `status` (`"in_progress"` while `evt.status === "started"`, else `"completed"`), `duration_ms`, and `detail`. However, `renderProgress()` only iterates `progressOrder` when drawing the visible tracker (`#askProgress`) — so backend-emitted stages not in this whitelist (confirmed by grepping `backend/services/ask_service.py`: it also emits `resolving_identity`, `querying_graphrag`, `querying_graphdb`, `synthesising_answer`, `done`) update `progressMap` but never appear as their own row in the itemized tracker UI. They still influence the free-text status line, though: `setStage` always calls `setStatus(evt.detail, ...)` when `evt.detail` is present, regardless of whether the stage key is in the whitelist, so the single-line "current activity" text (`#askStatus`, `#askLoadingBannerText`, `#askInlineStatusText`) reflects every stage the backend emits, even ones with no dedicated progress-list row.

Each visible progress row (`renderProgress`) shows: a status icon (`✓` completed, spinning `⟳` in-progress via a CSS `spin` keyframe, `○` otherwise — though the pending case is actually filtered out entirely since `if (status === "pending") return "";` skips rendering any stage not yet seen), the stage label, an optional `{duration_ms} ms` timing chip, and an optional detail line.

### 5.5 Answer rendering — `marked.js` Markdown

```js
marked.use({ gfm: true, breaks: false });
function markdownToHtml(text) { return text ? marked.parse(String(text)) : ""; }
```

The primary LLM answer (`payload.llm_rephrased_answer`) is rendered through `marked.parse()` into `#askLlmAnswer`'s `innerHTML` — raw HTML injection with no sanitization step (no DOMPurify or equivalent). This is a notable trust boundary: the rendered HTML originates from the LLM's synthesized answer text as returned by the backend `/api/ask/query` endpoint. If empty, a placeholder italic message ("No summary available. See database result below.") is shown instead. `marked.min.js` is loaded locally (not CDN) via `<script src="{{ url_for('static', filename='js/marked.min.js') }}">` in `ask.html`, so the dependency is vendored into the repo rather than fetched at runtime.

A secondary "raw computed answer" line (`#askActualAnswer`) shows `payload.actual_answer || payload.answer` as plain `textContent` (not `innerHTML` — no markdown, no HTML injection risk here), prefixed `"Raw computed answer: "`, hidden entirely if empty.

### 5.6 Result payload rendering pipeline

Once `finalPayload` is received, `runQuery()` calls, in this exact order:

1. `renderWarnings(payload.warnings)` — amber boxes for each warning string.
2. `renderKg(payload.kg_context)` — VRTI townland metadata cards (name, Gaelic name, parish/barony/county, KG URI link) inside `#askKgBlock`.
3. `renderStructured(payload)` — the SQL result table, VRTI result table, generated-SQL/SPARQL code blocks, and the chart (§5.7–5.8).
4. `renderEstateOverview(isEstateWide)` — only fires (and only then makes its own async fetch to `/api/ask/estate-overview`) when the question had no townland context at all (`!payload.townland_context && !payload.townland_resolution?.name`), i.e. estate-wide questions get a supplementary "County Wicklow overview" panel with geography/census/estate-record stat cards, barony pills, and ranked lists (top parishes by townland count, most populated townlands/parishes in 1841, top surnames, baronies by record count).
5. `renderLlmMeta(payload)` — technical connection/provider/model info line.
6. `renderProvenance(payload)` — confidence/gate/verifier badges plus a technical detail list (SQL source, execution mode, retrieval route/lane, matched townland entity, KG subgraph triple count, closest approved-query-memory matches).
7. `renderExplainability(payload)` — a plain-English "why this answer" panel: tables queried, records retrieved (SQL row count + KG row count), filters applied (parsed by regexing the generated SQL text for patterns like `HAS_EMIGRATION_RECORD = 1`, `GENDER = 'F'`, `AGE > 60`, `YEAR BETWEEN 1847 AND 1856`, etc. — this is client-side heuristic SQL-string inspection, not structured filter metadata from the backend), geographic scope, KG context, query strategy description, and memory-reuse note.
8. `renderTownlandResolution(payload.townland_resolution)` — a blue info banner showing which townland was matched, with a confidence-percentage badge if the match was fuzzy (not `"exact"`).
9. `renderSupportingContext(payload)` — generic label/value cards from `structured_output.supporting_context`.
10. `renderSuggestions(payload)` — shown only when `availability.available` is falsy and `suggestions`/`availability.suggestions` is non-empty (i.e. data wasn't available for this question and the backend proposes alternatives).
11. `renderInsights(payload)` — label/value cards from `related_insights`.

### 5.7 Structured table/query rendering (`renderStructured`)

Reads from `payload.structured_output.processed_tables`:
- `processed_tables.local_database` → `{ columns, rows }` rendered into `#askTable` via the shared `renderTable(tableEl, columns, rows, emptyText)` helper.
- `processed_tables.vrti_graph` → same shape, rendered into `#askVrtiTable`; the containing `#askKgResultBlock` is only shown if `vrtiRows.length > 0`.
- Generated query text: `structured_output.queries.local_sqlite_query` → `#askSqliteQuery` (plain `textContent`); `queries.vrti_postgresql_query || queries.vrti_sparql_query` → `#askSparqlQuery`, with `#askSparqlBlock` hidden entirely if no query text exists.
- `structured_output.summary.final_summary_text` → `#askSummary` (a hidden/technical section, per the surrounding source comment `// Summary (hidden section)`).
- Delegates to `renderChart(payload.chart || structured_output.chart)`.

`renderTable` is a shared table-builder used for both the SQL and VRTI result tables: it flags any column literally named `record_id`/`id`/ending in `_id` as an "ID column" (highlighted with a yellow header background, `#fef9c3`, and a 🔑 emoji suffix, plus yellow-tinted cell backgrounds), and for any long comma-containing cell value (>200 chars) it truncates to the first 5 comma-separated parts with a `<details>` "Show all N" expander revealing the rest.

### 5.8 Chart rendering — hand-written inline SVG, no chart library

`renderChart(chart)` supports two `chart.type` values, both rendered as raw inline SVG strings (no D3, no Canvas, no external charting library):

- **`"line"`**: builds a 720×260 viewBox SVG with 36px padding, plots points via linear interpolation of `value / maxValue` against the vertical axis, connects them with a single `<path>` (`stroke: #0f766e`, `stroke-width: 3`), places a filled circle + value label above each point and an x-axis category label below.
- **default (bar)**: renders a CSS-grid list of horizontal bars — each row is `grid-template-columns: minmax(120px,220px) 1fr auto` (label / bar-track / value), bar width computed as `Math.max(2, (value/maxValue)*100)%` (minimum 2% so zero-or-near-zero values remain visible as a sliver), with a teal→green linear gradient (`linear-gradient(90deg,#0f766e,#22c55e)`).

The chart block (`#askChartBlock`) is hidden entirely if `chart` is falsy or `labels.length !== values.length`.

### 5.9 Feedback UI — thumbs up/down

`#askFeedbackUp` / `#askFeedbackDown` buttons call `sendFeedback("up"|"down")`, which requires `latestResultPayload` to be set (guards against rating before any query has completed) and `POST`s to `/api/ask/feedback` with a rich payload: `question`, `townland_hint`, `sql_text`, `vrti_postgres_sql`, `feedback`, an optional free-text `note` (from `#askFeedbackNote`), `result_row_count`, `availability_state`, `llm_meta`, `reused_memory_id`, `sample_answer`, and `summary_json`. Buttons are disabled during the request. Response handling distinguishes three outcomes via `#askFeedbackStatus` text/color: `data.stored_in_memory` truthy → green "…now part of approved query memory" (the UI-visible confirmation that a thumbs-up promoted the query into `ask_query_memory`, per the CLAUDE.md schema description); `feedback === "down"` → green "…treat this query pattern more cautiously"; otherwise generic "Feedback saved." On error, the status line turns red/error-toned with the server's error message or a generic fallback.

### 5.10 PDF export

`payload.pdf_url` (present in the SSE `"result"` event, generated server-side into `exports/ask/` per CLAUDE.md) is applied directly as `pdfLinkEl.href`; the link (`#askPdfLink`) is only shown (`display: inline-flex`) when a URL is present, otherwise hidden and its `href` attribute removed. There is no client-side PDF generation logic — the button is a plain anchor pointing at a server-generated file.

### 5.11 Townland autocomplete dropdown

A custom-built (non-`<datalist>`) autocomplete for the "townland hint" input (`#askTownlandHint`), backed by a full client-side catalog pre-fetched once on page load (`fetch("/api/ask/townland-catalog")`, cached in module-scope `_townlandCatalog`) for instant filtering with no per-keystroke network round-trip:

- `_filterTownlandsClientSide(query)` normalizes both the query and each candidate's `name`/`name_gaelic` by stripping to `[a-z0-9]` only, then scores: exact match `1.0`, prefix match `0.9` minus a small length-difference penalty, substring match `0.75`, Gaelic-name prefix `0.7`, Gaelic-name substring `0.6`; results sorted by score descending (ties broken alphabetically), capped at 8.
- If the catalog hasn't loaded yet (`_townlandCatalog === null`), falls back to a debounced (180ms) network call to `GET /api/ask/townland-suggest?q=<val>`.
- A special `ALL_TOWNLANDS_ITEM` (`{ name: null, _isAll: true }`) is prepended to the dropdown whenever the input is empty or focused-while-empty, letting the user explicitly select "All Townlands" (estate-wide, no location filter) — selecting it sets `hintEl.dataset.isAll = "true"` and styles the input teal/bold; `runQuery()` checks this flag (`hintEl?.dataset.isAll ? null : ...`) to send `townland_hint: null` rather than the literal string "All Townlands".
- Full keyboard nav: `ArrowDown`/`ArrowUp` move `dropdownActiveIdx`, `Enter` selects the active item, `Escape` closes. Dropdown-item clicks use a single delegated `pointerdown` listener on the dropdown container (chosen deliberately over per-item `mousedown` bindings per the inline comment "more reliable than per-item mousedown bindings" — `pointerdown` fires before the input's `blur`, avoiding a race where blur closes the dropdown before the click registers).
- Blur closes the dropdown after a 150ms delay (`closeDropdownTimer`) to allow an in-flight pointerdown-driven selection to complete first.

### 5.12 Query submission flow (`runQuery`)

On submit (click on `#askSubmit` or Cmd/Ctrl+Enter in the question textarea): validates a non-empty question, resets nearly every panel to its empty state (progress map cleared, error/warnings cleared, all badges cleared, chart/estate-overview hidden), shows the loading banner and inline status strip, disables the submit button, then calls `consumeSSEPost("/api/ask/query", { question, townland_hint }, callback)`. On success, runs the full rendering pipeline (§5.6) and re-enables feedback. On any thrown error (network failure, non-OK response, explicit `"error"` SSE event, or missing final result), calls `showError(err.message)` and resets `setStatus("")`. The `finally` block unconditionally hides the loading banner/inline status and re-enables the submit button, guaranteeing the UI never gets stuck in a disabled state even on failure.

### 5.13 LLM connectivity status chip

`checkLlmStatus()` (called once on page load, not tied to query submission) fetches `GET /api/ask/llm-status` and renders a top-of-page pill (`#llmStatus`) — green "LLM Connected" or amber "LLM not connected" — showing `provider`/`active_model`, an optional hint string, and a collapsible technical-detail `<details>` block. The same `latestLlmStatus` value is reused inside `renderLlmMeta` to help describe whether the answer-rewrite step actually reached a live LLM versus falling back.

### 5.14 Example-question buttons

Any element with class `.ask-example` and a `data-q` attribute, when clicked, populates `#askQuestion` with that text and focuses it (does not auto-submit) — a simple one-way binding with no dedicated function, wired inline in the event-listener section at the bottom of the file.

---

## 6. Cross-file patterns: shared vs. duplicated

| Pattern | Shared? | Notes |
|---|---|---|
| Leaflet layer switcher (`initLayerSwitcher`/`buildLayerMap`/`switchLayer`) | Shared | Only genuinely shared utility across files; lives in `map.js`, consumed by `census.js` and `heritage.js`. `ask.js` and `kg_explore.js` don't use Leaflet at all. |
| `$ = (id) => document.getElementById(id)` shorthand | Duplicated | Independently redeclared in `census.js`, `heritage.js`, and `ask.js` (as a local `const` inside each `DOMContentLoaded` closure or module scope) — no shared `utils.js`. |
| `escapeHtml` / HTML-escaping helper | Duplicated with different implementations | `census.js` has no standalone escaper (builds HTML via template literals without escaping user-controlled text in several places, e.g. townland detail fields). `heritage.js` defines its own `escapeHtml(str)` (regex `.replace` chain for `& < > "`). `ask.js` defines its own, more complete `escapeHtml(str)` (adds `'` → `&#39;` via `replaceAll` chain). `kg_explore.js` uses a differently-named `escHtml(s)` with the same four-entity behavior. No cross-file reuse. |
| Loading-state indicators | Duplicated, different mechanisms per file | `census.js` has no dedicated full-page loading overlay (panels populate progressively as fetches resolve). `heritage.js` uses `showLoading(visible)` toggling `#hp-loading` display. `ask.js` uses a much richer multi-part system: `#askLoadingBanner`, `#askInlineStatus`, plus the itemized `#askProgress` stage tracker driven by live SSE events — no equivalent exists in any other file since only `ask.js` has a long-running streamed backend call. `kg_explore.js` has no loading overlay, just a text-replacement in `#kg-stats` during the initial graph fetch. |
| Fetch-with-error-handling wrapper | Not shared — no common wrapper at all | Every file writes its own inline `try { await fetch(...); if (!res.ok) ... } catch (e) { ... }` blocks ad hoc at each call site. There is no shared `apiFetch()`/`safeFetch()` utility anywhere across these five files. |
| URL query-param state sync (`history.replaceState`) | Duplicated, independently implemented | `heritage.js`'s `updateUrl()`/`readUrlParams()` pair is the most developed instance (serializes `townland`/`radius`/`layers`). `census.js` only reads a `townland` param once at startup (no corresponding write-back as state changes). `ask.js` and `kg_explore.js` do not sync any state to the URL. |
| Debounced input handling | Duplicated, same 180ms constant, different implementations | `ask.js`'s townland-hint autocomplete and `kg_explore.js`'s node search both debounce at 180ms via `setTimeout`/`clearTimeout`, but each maintains its own separate timer variable and neither shares a `debounce()` helper function. |

No file imports from another via ES modules (`import`/`export`) — everything relies on plain global-scope `<script>` tags and, in the one case of `map.js`, functions attached implicitly to the global scope by virtue of being declared at the top level of a classic script.

---

## 7. Notes for cross-referencing

- Endpoint contracts referenced here (`/api/map/layers`, `/api/census/*`, `/api/unified/*`, `/api/kg/graph`, `/api/kg/townland-rich/<name>`, `/api/ask/query`, `/api/ask/feedback`, `/api/ask/llm-status`, `/api/ask/townland-catalog`, `/api/ask/townland-suggest`, `/api/ask/estate-overview`) are documented from the backend side in the API-routes doc (if present in this doc set) — this document describes only how the frontend consumes them, not their server-side implementation.
- The SSE event contract (`{type, stage, status, detail, duration_ms, label}` for `"progress"` events; a full result object for the single `"result"` event) is defined by `_sse()` calls throughout `backend/services/ask_service.py`; `ask.js`'s `progressOrder` whitelist and `setStage`/`renderProgress` functions are the authoritative frontend-side description of which of those backend-emitted stages actually surface in the UI.
- Database tables referenced indirectly through rendered UI (`heritage_feature`, `entity_resolution_candidates`/`workhouse_unified_links` via the workhouse accordion in `census.js`, `graph_nodes`/`graph_edges` via `kg_explore.js`, `ask_query_memory` via the feedback flow in `ask.js`) are documented in the database-schema doc (`docs/02_database_schema.md`).
