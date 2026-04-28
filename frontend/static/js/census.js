/**
 * coolattin/static/js/census.js
 *
 * Census explorer — map choropleth, year slider, townland detail panel,
 * data-source status indicator, and basemap layer switcher.
 *
 * Architecture:
 *   - All data fetched from /api/census/* (never from KG directly)
 *   - Layer config fetched from /api/map/layers (never hardcoded)
 *   - map.js provides: initLayerSwitcher, buildLayerMap, switchLayer
 *   - meta.source / meta.cache_status from API drives the status badge
 */
document.addEventListener("DOMContentLoaded", async () => {
  const $ = (id) => document.getElementById(id);
  const CENSUS_YEARS  = [1841, 1851, 1861, 1871, 1881, 1891];
  // Estate survey years recorded in the Coolattin GeoJSON (total population only)
  const SURVEY_YEARS  = [1827, 1839, 1848, 1850, 1860, 1868];
  const ALL_DATA_YEARS = [...SURVEY_YEARS, ...CENSUS_YEARS]; // sorted chronologically

  // ---------------------------------------------------------------- //
  // State                                                              //
  // ---------------------------------------------------------------- //
  const state = {
    year: 1841,
    selectedTownland: null,
    townlandDetails: null,
    recordsByTownlandYear: {},
    summaryByYear: {},
    geoLayer: null,
    geoFeatureByName: {},
    // Maps short GeoJSON name → full canonical DB name
    // e.g. "newtown" → "newtown ed powerscourt"
    geoNameToCanonical: {},
    // satellite sub-map (shown in detail panel)
    satelliteMap: null,
    satelliteOverlay: null,
    // main map layer handles (populated by initLayerSwitcher)
    layerMap: {},
  };

  // ---------------------------------------------------------------- //
  // Helpers                                                            //
  // ---------------------------------------------------------------- //
  function key(townland, year) { return `${townland.toLowerCase()}|${year}`; }
  function valueOrDash(v) {
    return v === null || v === undefined || Number.isNaN(v) ? "-" : String(v);
  }

  function kpiCard(label, value) {
    return `
      <div style="padding:10px;border:1px solid #e2e8f0;border-radius:10px;background:#f8fafc;">
        <div style="font-size:11px;color:#64748b;text-transform:uppercase;font-weight:700;">${label}</div>
        <div style="font-size:22px;font-weight:800;color:#0f172a;line-height:1.2;">${value}</div>
      </div>`;
  }

  function getRecord(townland, year) {
    const exact = state.recordsByTownlandYear[key(townland, year)];
    if (exact) return exact;
    // Fall back to canonical alias (handles GeoJSON short names like "Newtown")
    const canonical = state.geoNameToCanonical[townland.toLowerCase()];
    if (canonical) {
      const aliased = state.recordsByTownlandYear[key(canonical, year)];
      if (aliased) return aliased;
    }
    return null;
  }

  // Return the best record for a townland near the chosen year.
  // For townlands with only estate survey data (1827–1868), picks the
  // closest available year so the choropleth isn't always pale.
  function getBestRecord(townland, preferredYear) {
    const direct = getRecord(townland, preferredYear);
    if (direct) return direct;
    // Search all loaded years for this townland, pick closest to preferredYear
    let best = null, bestDist = Infinity;
    for (const y of ALL_DATA_YEARS) {
      const r = getRecord(townland, y);
      if (r && r.total != null) {
        const dist = Math.abs(y - preferredYear);
        if (dist < bestDist) { best = r; bestDist = dist; }
      }
    }
    return best;
  }

  // ---------------------------------------------------------------- //
  // Data-source status badge                                           //
  // Shows whether data came from DB cache, KG refresh, or CSV seed.  //
  // ---------------------------------------------------------------- //
  function updateDataSourceBadge(meta) {
    const badge = $("census-data-source");
    const label = $("census-source-label");
    const updated = $("census-last-updated");
    if (!badge || !meta) return;

    badge.hidden = false;
    const statusMap = {
      hit:            { text: "Cached",          color: "#dcfce7", border: "#86efac", icon: "✓" },
      stale_refresh:  { text: "Serving cached (refresh queued)", color: "#fef9c3", border: "#fde047", icon: "⟳" },
      miss:           { text: "Freshly loaded",  color: "#dbeafe", border: "#93c5fd", icon: "↓" },
    };
    const sourceMap = {
      database:   "Database",
      kg_refresh: "Knowledge Graph",
      csv_seed:   "CSV Seed",
    };

    const st = statusMap[meta.cache_status] || { text: meta.cache_status, color: "#f1f5f9", border: "#cbd5e1", icon: "?" };
    badge.style.cssText = `background:${st.color};border:1px solid ${st.border};border-radius:4px;padding:3px 10px;font-size:11px;display:inline-flex;align-items:center;gap:6px;`;
    if (label) label.textContent = `${st.icon} ${st.text} · Source: ${sourceMap[meta.source] || meta.source}`;
    if (updated && meta.generated_at) {
      updated.textContent = `· ${new Date(meta.generated_at).toLocaleString()}`;
    }
  }

  // ---------------------------------------------------------------- //
  // Main census map                                                    //
  // ---------------------------------------------------------------- //
  const map = L.map("censusMap", { minZoom: 8, maxZoom: 15 }).setView([52.95, -6.4], 10);

  // Layer switcher — fetches config from /api/map/layers
  const { layerMap } = await initLayerSwitcher(map, "census-map-layer-switcher");
  state.layerMap = layerMap;

  // ---------------------------------------------------------------- //
  // Load townlands.json first — this is the authoritative reference   //
  // of which townlands belong to the Coolattin / Wicklow estate.      //
  // Census data is then filtered to only these townlands.             //
  // ---------------------------------------------------------------- //
  const geoRes = await fetch("/static/data/townlands.json");
  const geo = geoRes.ok ? await geoRes.json() : { features: [] };

  // Build the valid-townland set from the GeoJSON reference file
  const validTownlandNames = new Set(
    (geo.features || [])
      .map(f => String(f?.properties?.TL_ENGLISH || "").trim().toLowerCase())
      .filter(Boolean)
  );

  // ---------------------------------------------------------------- //
  // Fetch census data — load ALL years upfront (census + estate       //
  // survey). Estate survey years (1827–1868) are only totals with     //
  // no male/female breakdown. Both sets are stored together in        //
  // state.recordsByTownlandYear so the timeline can render them.      //
  // Only records whose townland appears in townlands.json are kept.   //
  // ---------------------------------------------------------------- //
  let responseMeta = null;

  for (const y of ALL_DATA_YEARS) {
    const res = await fetch(`/api/census/?year=${y}&limit=2000`);
    if (!res.ok) continue;
    const payload = await res.json();
    const yRows = Array.isArray(payload) ? payload : (payload.data || []);
    // Capture meta from the first standard census year for the status badge
    if (y === CENSUS_YEARS[0] && !Array.isArray(payload)) {
      responseMeta = payload.meta || null;
    }
    yRows.forEach(r => {
      // Only index records that correspond to a known estate townland
      const tl = String(r.townland || "").trim().toLowerCase();
      if (tl && validTownlandNames.has(tl)) {
        state.recordsByTownlandYear[key(r.townland, r.year)] = r;
      }
    });
  }

  // Show data source status badge
  updateDataSourceBadge(responseMeta);

  // Fetch summaries per year
  for (const y of CENSUS_YEARS) {
    const sRes = await fetch(`/api/census/summary?year=${y}`);
    if (sRes.ok) state.summaryByYear[y] = await sRes.json();
  }

  // ---------------------------------------------------------------- //
  // Build alias map: GeoJSON short name → canonical DB name           //
  // Fixes cases like "Newtown" (GeoJSON) → "NEWTOWN ED POWERSCOURT"  //
  // (DB). Built from all loaded census record keys.                   //
  // ---------------------------------------------------------------- //
  const allDbNames = new Set();
  for (const k of Object.keys(state.recordsByTownlandYear)) {
    allDbNames.add(k.split("|")[0]); // e.g. "newtown ed powerscourt"
  }

  (geo.features || []).forEach(f => {
    const nm = String(f?.properties?.TL_ENGLISH || "").trim().toLowerCase();
    if (nm) {
      state.geoFeatureByName[nm] = f;
      // If no exact census record exists, find first DB name with this as prefix
      const hasExact = CENSUS_YEARS.some(y => state.recordsByTownlandYear[key(nm, y)]);
      if (!hasExact) {
        for (const dbName of allDbNames) {
          if (dbName.startsWith(nm + " ") || dbName.startsWith(nm + "\t")) {
            state.geoNameToCanonical[nm] = dbName;
            break;
          }
        }
      }
    }
  });

  state.geoLayer = L.geoJSON(geo, {
    style: f => {
      const nm = String(f?.properties?.TL_ENGLISH || "").trim();
      const rec = getBestRecord(nm, state.year);
      return choroplethStyle(rec?.total, state.selectedTownland === nm);
    },
    onEachFeature: (feature, layer) => {
      const nm = String(feature?.properties?.TL_ENGLISH || "").trim();
      layer.on("click", async () => {
        state.selectedTownland = nm;
        state.townlandDetails = await fetchTownlandDetails(nm);
        recolorMap();
        renderTownlandPanel(nm);
      });
      const r = getBestRecord(nm, state.year);
      const hasExact = !!getRecord(nm, state.year);
      const yearLabel = (r && !hasExact) ? `~${r.year}` : String(state.year);
      layer.bindTooltip(`<b>${nm}</b><br/>Population ${yearLabel}: ${valueOrDash(r?.total)}`);
    },
  }).addTo(map);

  map.fitBounds(state.geoLayer.getBounds());

  // ---------------------------------------------------------------- //
  // Year slider                                                        //
  // ---------------------------------------------------------------- //
  const slider = $("yearSlider");
  const yearValue = $("yearValue");
  if (slider) {
    slider.addEventListener("input", () => {
      state.year = Number(slider.value);
      if (yearValue) yearValue.textContent = String(state.year);
      recolorMap();
      renderTownlandPanel(state.selectedTownland);
    });
  }

  // Re-render on language change
  window.addEventListener("languageChanged", () => renderTownlandPanel(state.selectedTownland));

  renderTownlandPanel(null);

  // ---------------------------------------------------------------- //
  // Choropleth colour scale                                            //
  // ---------------------------------------------------------------- //
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

  function choroplethStyle(total, isSelected) {
    return {
      color: "#7f1d1d",
      weight: isSelected ? 2.5 : 1,
      fillColor: getColor(total),
      fillOpacity: 0.65,
    };
  }

  function recolorMap() {
    if (!state.geoLayer) return;
    state.geoLayer.setStyle(f => {
      const nm = String(f?.properties?.TL_ENGLISH || "").trim();
      const rec = getBestRecord(nm, state.year);
      return choroplethStyle(rec?.total, state.selectedTownland === nm);
    });
    // Refresh hover tooltips to show the newly selected census year
    state.geoLayer.eachLayer(layer => {
      const nm = String(layer.feature?.properties?.TL_ENGLISH || "").trim();
      const r = getBestRecord(nm, state.year);
      const hasExact = !!getRecord(nm, state.year);
      const yearLabel = (r && !hasExact) ? `~${r.year}` : String(state.year);
      layer.setTooltipContent(`<b>${nm}</b><br/>Population ${yearLabel}: ${valueOrDash(r?.total)}`);
    });
  }

  // ---------------------------------------------------------------- //
  // Townland detail panel                                              //
  // ---------------------------------------------------------------- //
  async function fetchTownlandDetails(townlandName) {
    try {
      const res = await fetch(`/api/census/townland?name=${encodeURIComponent(townlandName)}`);
      if (res.ok) {
        const payload = await res.json();
        return payload.data || payload;  // handle both old and new response shape
      }
    } catch (e) {
      console.error("census.js: fetchTownlandDetails error", e);
    }
    return null;
  }

  function renderTownlandPanel(townland) {
    const title     = $("censusTownlandTitle");
    const meta      = $("censusTownlandMeta");
    const kpis      = $("censusKpis");
    const timeline  = $("censusTimeline");
    const picContainer   = $("censusTownlandSvgContainer");
    const detailContainer = $("censusTownlandDetail");
    const t = window.t || (k => k);

    if (!townland) {
      if (title) title.textContent = t("selectTownland") || "Select a Townland";
      if (meta) meta.textContent = t("clickMapPolygonInspect") || "Click a map polygon to inspect yearly values.";
      if (kpis) kpis.innerHTML = "";
      if (timeline) timeline.innerHTML = "";
      if (detailContainer) detailContainer.innerHTML = "";
      if (picContainer) picContainer.style.display = "none";
      return;
    }

    // Use the canonical DB townland name (from detail API) for census record lookups
    // This resolves mismatches like GeoJSON "Newtown" → DB "NEWTOWN ED POWERSCOURT"
    const d = state.townlandDetails || {};
    const canonicalName = d.townland ? d.townland.toLowerCase() : townland.toLowerCase();
    // Primary record for selected census year; fall back to nearest available year
    // so townlands with only estate survey data (1827-1868) still populate KPI cards.
    const rec        = state.recordsByTownlandYear[key(canonicalName, state.year)]
                    || getRecord(townland, state.year);
    const bestRec    = rec || getBestRecord(townland, state.year);
    const kpiYear    = bestRec?.year ?? state.year;
    const kpiIsApprox = !rec && bestRec;

    // Title: show English + Gaelic name
    if (title) {
      title.textContent = townland;
      if (d.gaelic_name) title.innerHTML = `${townland} <span style="font-size:0.7em;font-style:italic;color:var(--moss);font-weight:400;">${d.gaelic_name}</span>`;
    }

    // Meta line + link to home page map explorer for this townland
    const exploreUrl = `/?townland=${encodeURIComponent(townland)}`;
    if (meta) {
      meta.innerHTML = `
        Viewing census year ${state.year}. Use the year slider to compare trends.
        <a href="${exploreUrl}"
           style="display:inline-flex;align-items:center;gap:5px;margin-top:10px;padding:8px 14px;background:rgba(35,75,58,.07);border:1px solid rgba(35,75,58,.2);border-radius:10px;color:var(--forest);font-size:12px;font-weight:600;text-decoration:none;transition:background .18s;"
           onmouseover="this.style.background='rgba(35,75,58,.13)'" onmouseout="this.style.background='rgba(35,75,58,.07)'">
          <span>Explore Records</span><span style="font-size:14px;">→</span>
        </a>`;
    }

    // Satellite sub-map
    if (picContainer) {
      picContainer.style.display = "flex";
      const feature = state.geoFeatureByName[townland.trim().toLowerCase()];
      updateSatelliteView(feature, townland, rec?.total);
    }

    // ---- Full KG information panel ----
    if (detailContainer) {
      let html = "";
      const kgUri = d.uri || d.kg_uri || null;
      const hasKg = Boolean(kgUri);

      // ── KG source card ──────────────────────────────────────────────
      if (hasKg) {
        html += `
        <div style="margin-bottom:14px;padding:10px 14px;background:#fff7ed;border-radius:10px;border:1px solid #fed7aa;">
          <div style="font-size:10px;color:#7c2d12;font-weight:800;text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px;">🔗 Knowledge Graph Resource</div>
          <div style="font-size:12px;color:#431407;line-height:1.5;margin-bottom:6px;">
            Geographic &amp; administrative data sourced from the
            <a href="https://virtualtreasury.ie" target="_blank" rel="noopener" style="color:#c2410c;font-weight:700;text-decoration:underline;">VRTI Knowledge Graph</a>.
          </div>
          <a href="${kgUri}" target="_blank" rel="noopener"
             style="display:inline-block;font-size:11px;color:#ea580c;font-weight:700;word-break:break-all;background:#fff;border:1px solid #fed7aa;border-radius:5px;padding:3px 8px;">
            View this townland in KG ↗
          </a>
        </div>`;
      }

      // ── Gaelic name ──────────────────────────────────────────────────
      if (d.gaelic_name) {
        html += `
        <div style="margin-bottom:12px;padding:8px 12px;background:#f0fdf4;border-radius:8px;border-left:3px solid #16a34a;">
          <div style="font-size:10px;color:#14532d;font-weight:800;text-transform:uppercase;letter-spacing:.05em;margin-bottom:2px;">
            Irish Name
            ${hasKg ? `<a href="${kgUri}" target="_blank" rel="noopener" style="font-weight:400;color:#16a34a;margin-left:5px;">· KG ↗</a>` : ""}
          </div>
          <div style="font-size:15px;font-style:italic;color:#166534;font-weight:600;">${d.gaelic_name}</div>
        </div>`;
      }

      // ── Administrative location ──────────────────────────────────────
      const parish = d.kg_civil_parish || d.civil_parish;
      const barony = d.kg_barony || d.barony;
      const county = d.county;
      const adminParts = [
        parish && `<div style="display:flex;justify-content:space-between;align-items:center;padding:4px 0;border-bottom:1px solid #d1fae5;">
          <span style="color:#064e3b;font-weight:600;font-size:12px;">Parish</span>
          <span style="color:#065f46;font-size:12px;">${parish}</span></div>`,
        barony && `<div style="display:flex;justify-content:space-between;align-items:center;padding:4px 0;border-bottom:1px solid #d1fae5;">
          <span style="color:#064e3b;font-weight:600;font-size:12px;">Barony</span>
          <span style="color:#065f46;font-size:12px;">${barony}</span></div>`,
        county && `<div style="display:flex;justify-content:space-between;align-items:center;padding:4px 0;">
          <span style="color:#064e3b;font-weight:600;font-size:12px;">County</span>
          <span style="color:#065f46;font-size:12px;">${county}</span></div>`,
      ].filter(Boolean);
      if (adminParts.length) {
        html += `
        <div style="margin-bottom:12px;padding:10px 14px;background:#f0fdf4;border-radius:10px;border:1px solid #bbf7d0;">
          <div style="font-size:10px;color:#064e3b;font-weight:800;text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px;">
            📍 Administrative Location
            ${hasKg ? `<a href="${kgUri}" target="_blank" rel="noopener" style="font-weight:400;color:#16a34a;margin-left:5px;">· Source: VRTI KG ↗</a>` : ""}
          </div>
          ${adminParts.join("")}
        </div>`;
      }

      // ── Coordinates ──────────────────────────────────────────────────
      if (d.centroid_lat && d.centroid_lon) {
        const lat = Number(d.centroid_lat).toFixed(6);
        const lon = Number(d.centroid_lon).toFixed(6);
        const osmUrl = `https://www.openstreetmap.org/?mlat=${lat}&mlon=${lon}#map=15/${lat}/${lon}`;
        html += `
        <div style="margin-bottom:12px;padding:10px 14px;background:#f8fafc;border-radius:10px;border:1px solid #e2e8f0;">
          <div style="font-size:10px;color:#374151;font-weight:800;text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px;">
            🌐 Centroid Coordinates
            ${hasKg ? `<a href="${kgUri}" target="_blank" rel="noopener" style="font-weight:400;color:#6b7280;margin-left:5px;">· Source: VRTI KG ↗</a>` : ""}
          </div>
          <div style="font-size:13px;color:#1e293b;margin-bottom:4px;">Lat <b>${lat}</b> · Lon <b>${lon}</b></div>
          <a href="${osmUrl}" target="_blank" rel="noopener"
             style="font-size:11px;color:#2563eb;text-decoration:underline;">Open in OpenStreetMap ↗</a>
        </div>`;
      }

      // ── Placename description (from DB/CSV) ───────────────────────────
      if (d.description) {
        html += `
        <div style="margin-bottom:12px;padding:10px 14px;background:#fefce8;border-radius:10px;border:1px solid #fde68a;">
          <div style="font-size:10px;color:#713f12;font-weight:800;text-transform:uppercase;letter-spacing:.06em;margin-bottom:4px;">📖 Placename Meaning
            <span style="font-weight:400;color:#92400e;margin-left:5px;">· Source: Local Research</span>
          </div>
          <div style="font-size:13px;color:#374151;line-height:1.6;">${d.description}</div>
          ${d.placename_theme ? `<div style="margin-top:4px;font-size:11px;color:#92400e;">Theme: <b>${d.placename_theme}</b></div>` : ""}
        </div>`;
      }

      // ── Identifiers ────────────────────────────────────────────────────
      const idItems = [];
      if (d.vrti_id) idItems.push(`<div style="display:flex;justify-content:space-between;align-items:center;padding:3px 0;">
        <span style="font-size:11px;color:#374151;">VRTI Identifier</span>
        <span style="background:#e0f2fe;color:#0369a1;padding:2px 8px;border-radius:4px;font-size:11px;font-family:monospace;">${d.vrti_id}</span></div>`);
      if (d.osi_id) idItems.push(`<div style="display:flex;justify-content:space-between;align-items:center;padding:3px 0;">
        <span style="font-size:11px;color:#374151;">OSI Identifier</span>
        <span style="background:#fef9c3;color:#854d0e;padding:2px 8px;border-radius:4px;font-size:11px;font-family:monospace;">${d.osi_id}</span></div>`);
      if (d.osm_id) idItems.push(`<div style="display:flex;justify-content:space-between;align-items:center;padding:3px 0;">
        <span style="font-size:11px;color:#374151;">OSM Identifier</span>
        <a href="https://www.openstreetmap.org/relation/${String(d.osm_id).replace('-','')}" target="_blank" rel="noopener"
           style="background:#f3e8ff;color:#7e22ce;padding:2px 8px;border-radius:4px;font-size:11px;font-family:monospace;text-decoration:none;">${d.osm_id} ↗</a></div>`);
      if (idItems.length) {
        html += `
        <div style="margin-bottom:12px;padding:10px 14px;background:#f8fafc;border-radius:10px;border:1px solid #e2e8f0;">
          <div style="font-size:10px;color:#374151;font-weight:800;text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px;">
            🏷️ Identifiers
            ${hasKg ? `<a href="${kgUri}" target="_blank" rel="noopener" style="font-weight:400;color:#6b7280;margin-left:5px;">· Source: VRTI KG ↗</a>` : ""}
          </div>
          ${idItems.join("")}
        </div>`;
      }

      // ── External links ──────────────────────────────────────────────────
      if (d.links && d.links.length) {
        const logainm    = d.links.find(l => l.includes("logainm.ie"));
        const townlandsIe = d.links.find(l => l.includes("townlands.ie"));
        const linkItems = [];
        if (logainm)     linkItems.push(`<a href="${logainm}" target="_blank" rel="noopener"
          style="display:flex;align-items:center;gap:6px;padding:7px 10px;background:#eff6ff;border:1px solid #bfdbfe;border-radius:8px;text-decoration:none;">
          <span style="font-size:12px;color:#1d4ed8;font-weight:700;">Logainm.ie</span>
          <span style="font-size:10px;color:#3b82f6;">Irish placename database ↗</span></a>`);
        if (townlandsIe) linkItems.push(`<a href="${townlandsIe}" target="_blank" rel="noopener"
          style="display:flex;align-items:center;gap:6px;padding:7px 10px;background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;text-decoration:none;">
          <span style="font-size:12px;color:#166534;font-weight:700;">Townlands.ie</span>
          <span style="font-size:10px;color:#22c55e;">Townland index ↗</span></a>`);
        if (linkItems.length) {
          html += `
          <div style="margin-bottom:12px;">
            <div style="font-size:10px;color:#374151;font-weight:800;text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px;">
              🔎 External Records
              ${hasKg ? `<a href="${kgUri}" target="_blank" rel="noopener" style="font-weight:400;color:#6b7280;margin-left:5px;">· via VRTI KG ↗</a>` : ""}
            </div>
            <div style="display:flex;flex-direction:column;gap:6px;">${linkItems.join("")}</div>
          </div>`;
        }
      }

      // ── Historic images ──────────────────────────────────────────────
      if (d.images && d.images.length) {
        html += `
        <div style="margin-bottom:12px;">
          <div style="font-size:10px;color:#64748b;font-weight:800;text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px;">
            📸 Historic Images
            ${hasKg ? `<a href="${kgUri}" target="_blank" rel="noopener" style="font-weight:400;color:#6b7280;margin-left:5px;">· Source: VRTI KG ↗</a>` : ""}
          </div>
          <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(90px,1fr));gap:6px;">
            ${d.images.slice(0, 6).map(src =>
              `<img src="${src}" alt="${townland}" loading="lazy"
                style="width:100%;height:70px;object-fit:cover;border-radius:8px;border:1px solid #e2e8f0;cursor:pointer;transition:transform .15s;"
                onmouseover="this.style.transform='scale(1.04)'"
                onmouseout="this.style.transform='scale(1)'"
                onclick="window.open('${src}','_blank')"
                onerror="this.style.display='none'">`
            ).join("")}
          </div>
        </div>`;
      }

      detailContainer.innerHTML = html;
    }

    // KPI cards for selected year (or nearest available year for estate-survey townlands)
    if (kpis) {
      const approxNote = kpiIsApprox
        ? `<div style="grid-column:1/-1;padding:6px 10px;background:#fef9c3;border:1px solid #fde68a;border-radius:6px;font-size:11px;color:#78350f;margin-bottom:4px;">
            No census data for ${state.year} — showing nearest available year (${kpiYear}).
            This townland's population was recorded in the estate survey (${SURVEY_YEARS.filter(y => getBestRecord(townland, y)).join(", ")}).
           </div>`
        : "";
      kpis.innerHTML = approxNote + [
        kpiCard(t("totalPopulation") || "Total Population", valueOrDash(bestRec?.total)),
        kpiCard(t("male") || "Male", valueOrDash(bestRec?.male)),
        kpiCard(t("female") || "Female", valueOrDash(bestRec?.female)),
        kpiCard(t("inhabitedHouses") || "Inhabited", valueOrDash(bestRec?.inhabited)),
        kpiCard(t("uninhabitedHouses") || "Uninhabited", valueOrDash(bestRec?.uninhabited)),
        kpiCard(t("year") || "Year", kpiIsApprox ? `~${kpiYear}` : String(state.year)),
      ].join("");
    }

    // Population & clearances timeline
    if (timeline) {
      // Collect every year that has a record for this townland
      // (covers both estate survey years loaded upfront and standard census years)
      const allYearsWithData = ALL_DATA_YEARS.filter(y => {
        const r = state.recordsByTownlandYear[key(canonicalName, y)]
               || getRecord(townland, y);
        return r && r.total != null;
      });

      // Clearance data from GeoJSON feature properties
      const geoFeature = state.geoFeatureByName[townland.trim().toLowerCase()];
      const geoProps   = geoFeature?.properties || {};
      const totalClearances = geoProps.Total_Clearances;
      const clearanceYears  = [1847,1848,1849,1850,1851,1852,1853,1854,1855,1856]
        .map(y => ({ year: y, count: geoProps[`Clearances_${y}`] || 0 }))
        .filter(c => c.count > 0);

      if (allYearsWithData.length === 0) {
        // No population data at all — explain why and show clearances if any
        let clearanceNote = "";
        if (totalClearances > 0) {
          const breakdown = clearanceYears.map(c => `${c.year}: ${c.count}`).join(" · ");
          clearanceNote = `
            <div style="margin-top:10px;padding:10px 12px;background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;">
              <div style="font-size:12px;font-weight:700;color:#9a3412;margin-bottom:4px;">Eviction / Clearance Records Available</div>
              <div style="font-size:11px;color:#78350f;line-height:1.5;">
                Although no population census figures survive for <strong>${townland}</strong>,
                eviction records from the Coolattin estate are present:
                <strong>${totalClearances} total clearances</strong> (1847–1856).
              </div>
              <div style="margin-top:6px;font-size:11px;color:#92400e;">By year — ${breakdown}</div>
              <div style="margin-top:6px;font-size:11px;color:#92400e;">
                To explore individuals, use the <strong>Map Explorer</strong> on the home page and select <em>${townland}</em>.
              </div>
            </div>`;
        }

        timeline.innerHTML = `
          <div style="padding:12px;background:#fef2f2;border-radius:8px;border:1px solid #fecaca;">
            <div style="font-size:12px;font-weight:700;color:#991b1b;margin-bottom:4px;">No census population data</div>
            <div style="font-size:11px;color:#7f1d1d;line-height:1.5;">
              No population figures were found for <strong>${townland}</strong> in the estate surveys (1827–1868)
              or the standard census returns (1841–1891).
              ${townland === "NEWTOWN"
                ? "This estate polygon is named <em>Newtown</em>; population data in the CSV seed is recorded under electoral-division variants (e.g. <em>Newtown ED Tinahely</em>) which cannot be matched to this single polygon."
                : "The townland may have been too small, uninhabited, or merged with a neighbouring area in the original records."}
            </div>
            ${clearanceNote}
          </div>`;
        return;
      }

      // Build a unified table for ALL years with data
      const hasMixed = allYearsWithData.some(y => SURVEY_YEARS.includes(y))
                    && allYearsWithData.some(y => CENSUS_YEARS.includes(y));

      let html = "";

      if (hasMixed) {
        html += `
          <div style="font-size:11px;color:#475569;margin-bottom:8px;padding:6px 10px;background:#f8fafc;border-radius:6px;border:1px solid #e2e8f0;">
            This townland has both <strong>estate survey</strong> totals (1827–1868, total only) and
            <strong>standard census</strong> records (1841–1891, with male/female).
          </div>`;
      } else if (allYearsWithData.every(y => SURVEY_YEARS.includes(y))) {
        html += `
          <div style="font-size:11px;color:#475569;margin-bottom:8px;padding:6px 10px;background:#fefce8;border-radius:6px;border:1px solid #fde68a;">
            Population data for this townland comes from the <strong>Coolattin estate survey</strong> (1827–1868).
            These records show total population only — male/female breakdown was not captured.
            Standard census returns (1841–1891) are not available for this townland.
          </div>`;
      }

      html += `
        <table style="width:100%;border-collapse:collapse;font-size:12px;">
          <thead>
            <tr style="background:#f8fafc;">
              <th style="padding:6px 8px;text-align:left;border-bottom:2px solid #e2e8f0;color:#374151;">Year</th>
              <th style="padding:6px 8px;text-align:right;border-bottom:2px solid #e2e8f0;color:#374151;">Total</th>
              <th style="padding:6px 8px;text-align:right;border-bottom:2px solid #e2e8f0;color:#374151;">Male</th>
              <th style="padding:6px 8px;text-align:right;border-bottom:2px solid #e2e8f0;color:#374151;">Female</th>
              <th style="padding:6px 8px;text-align:right;border-bottom:2px solid #e2e8f0;color:#374151;">Houses</th>
              <th style="padding:6px 8px;text-align:right;border-bottom:2px solid #e2e8f0;color:#374151;">Source</th>
            </tr>
          </thead>
          <tbody>
            ${allYearsWithData.map(y => {
              const r = state.recordsByTownlandYear[key(canonicalName, y)]
                     || getRecord(townland, y);
              const active  = y === state.year || y === kpiYear;
              const isSurvey = SURVEY_YEARS.includes(y);
              const rowBg   = active ? "#eff6ff" : "transparent";
              const srcLabel = isSurvey ? "Estate survey" : "Census";
              const srcColor = isSurvey ? "#78350f" : "#1d4ed8";
              return `<tr style="background:${rowBg};cursor:pointer;" data-year="${y}">
                <td style="padding:7px 8px;border-bottom:1px solid #f1f5f9;font-weight:${active ? "800" : "600"};color:${active ? "#1d4ed8" : "#374151"};">${y}</td>
                <td style="padding:7px 8px;border-bottom:1px solid #f1f5f9;text-align:right;font-weight:${active ? "700" : "400"};">${valueOrDash(r?.total)}</td>
                <td style="padding:7px 8px;border-bottom:1px solid #f1f5f9;text-align:right;">${valueOrDash(r?.male)}</td>
                <td style="padding:7px 8px;border-bottom:1px solid #f1f5f9;text-align:right;">${valueOrDash(r?.female)}</td>
                <td style="padding:7px 8px;border-bottom:1px solid #f1f5f9;text-align:right;">${valueOrDash(r?.inhabited)}</td>
                <td style="padding:7px 8px;border-bottom:1px solid #f1f5f9;text-align:right;font-size:10px;color:${srcColor};">${srcLabel}</td>
              </tr>`;
            }).join("")}
          </tbody>
        </table>`;

      // Clearances strip
      if (totalClearances > 0) {
        const breakdown = clearanceYears.map(c => `${c.year}: ${c.count}`).join(" · ");
        html += `
          <div style="margin-top:14px;padding:10px 12px;background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;">
            <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:#9a3412;margin-bottom:4px;">
              Evictions / Clearances (1847–1856)
            </div>
            <div style="font-size:12px;color:#78350f;margin-bottom:4px;">
              <strong>${totalClearances}</strong> total recorded clearances
            </div>
            <div style="font-size:11px;color:#92400e;">${breakdown}</div>
          </div>`;
      }

      timeline.innerHTML = html;

      // Clicking a row in the table changes the selected year (census years only)
      timeline.querySelectorAll("tr[data-year]").forEach(row => {
        row.addEventListener("click", () => {
          const y = Number(row.getAttribute("data-year"));
          if (!Number.isNaN(y) && CENSUS_YEARS.includes(y)) {
            state.year = y;
            if (slider) slider.value = String(y);
            if (yearValue) yearValue.textContent = String(y);
            recolorMap();
            renderTownlandPanel(state.selectedTownland);
          }
        });
      });
    }
  }

  // ---------------------------------------------------------------- //
  // Satellite sub-map (detail panel)                                   //
  // ---------------------------------------------------------------- //
  function initSatelliteMap() {
    if (state.satelliteMap) return;
    const container = $("townlandSatelliteMap");
    if (!container) return;

    state.satelliteMap = L.map("townlandSatelliteMap", {
      zoomControl: false,
      attributionControl: false,
    });

    L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      { maxZoom: 19 }
    ).addTo(state.satelliteMap);

    L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
      { maxZoom: 19, opacity: 0.5 }
    ).addTo(state.satelliteMap);

    state.satelliteMap.setView([52.95, -6.4], 10);
  }

  function updateSatelliteView(geoFeature, townlandName, _population) {
    if (!state.satelliteMap) initSatelliteMap();

    const svgDesc = $("censusTownlandSvgDesc");
    if (svgDesc) svgDesc.textContent = `${townlandName} — historic townland, County Wicklow.`;

    if (state.satelliteOverlay) {
      state.satelliteMap.removeLayer(state.satelliteOverlay);
      state.satelliteOverlay = null;
    }
    if (!geoFeature) {
      state.satelliteMap.setView([52.95, -6.4], 10);
      return;
    }

    state.satelliteOverlay = L.geoJSON(geoFeature, {
      style: { color: "#22c55e", weight: 3, fillColor: "#22c55e", fillOpacity: 0.15 },
    }).addTo(state.satelliteMap);

    state.satelliteMap.fitBounds(L.geoJSON(geoFeature).getBounds(), {
      padding: [30, 30],
      maxZoom: 16,
    });
  }

  // ---------------------------------------------------------------- //
  // URL-param auto-select                                              //
  // If census page was opened via ?townland= (e.g. from the home page //
  // story panel "View Census Data" link), auto-click that townland.   //
  // ---------------------------------------------------------------- //
  const urlParams = new URLSearchParams(window.location.search);
  const tlParam = urlParams.get("townland");
  if (tlParam) {
    // Find the matching GeoJSON feature (case-insensitive)
    const matchedFeature = (geo.features || []).find(f => {
      const nm = String(f?.properties?.TL_ENGLISH || "").trim();
      return nm.toLowerCase() === tlParam.toLowerCase();
    });

    if (matchedFeature) {
      const nm = String(matchedFeature.properties.TL_ENGLISH || "").trim();
      state.selectedTownland = nm;
      // Strip the param so a page refresh doesn't re-select the townland
      window.history.replaceState({}, "", window.location.pathname);
      state.townlandDetails = await fetchTownlandDetails(nm);
      recolorMap();
      renderTownlandPanel(nm);
      // Zoom the map to the selected townland
      try {
        map.fitBounds(L.geoJSON(matchedFeature).getBounds(), { padding: [40, 40], maxZoom: 14 });
      } catch (_) {}
    }
  }
});
