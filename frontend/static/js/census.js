document.addEventListener("DOMContentLoaded", async () => {
  const $ = (id) => document.getElementById(id);
  const CENSUS_YEARS  = [1841, 1851, 1861, 1871, 1881, 1891];
  const SURVEY_YEARS  = [1827, 1839, 1848, 1850, 1860, 1868];
  const ALL_DATA_YEARS = [...SURVEY_YEARS, ...CENSUS_YEARS];

  const state = {
    year: 1841,
    selectedTownland: null,
    townlandDetails: null,
    workhouseData: null,
    unifiedSummary: null,
    recordsByTownlandYear: {},
    summaryByYear: {},
    geoLayer: null,
    geoFeatureByName: {},
    geoNameToCanonical: {},
    satelliteMap: null,
    satelliteOverlay: null,
    layerMap: {},
  };

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
    const canonical = state.geoNameToCanonical[townland.toLowerCase()];
    if (canonical) {
      const aliased = state.recordsByTownlandYear[key(canonical, year)];
      if (aliased) return aliased;
    }
    return null;
  }

  function getBestRecord(townland, preferredYear) {
    const direct = getRecord(townland, preferredYear);
    if (direct) return direct;
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

  const map = L.map("censusMap", { minZoom: 8, maxZoom: 15 }).setView([52.95, -6.4], 10);

  const { layerMap } = await initLayerSwitcher(map, "census-map-layer-switcher");
  state.layerMap = layerMap;

  const geoRes = await fetch("/static/data/townlands.json");
  const geo = geoRes.ok ? await geoRes.json() : { features: [] };

  const validTownlandNames = new Set(
    (geo.features || [])
      .map(f => String(f?.properties?.TL_ENGLISH || "").trim().toLowerCase())
      .filter(Boolean)
  );

  let responseMeta = null;

  for (const y of ALL_DATA_YEARS) {
    const res = await fetch(`/api/census/?year=${y}&limit=2000`);
    if (!res.ok) continue;
    const payload = await res.json();
    const yRows = Array.isArray(payload) ? payload : (payload.data || []);
    if (y === CENSUS_YEARS[0] && !Array.isArray(payload)) {
      responseMeta = payload.meta || null;
    }
    yRows.forEach(r => {
      const tl = String(r.townland || "").trim().toLowerCase();
      if (tl && validTownlandNames.has(tl)) {
        state.recordsByTownlandYear[key(r.townland, r.year)] = r;
      }
    });
  }

  updateDataSourceBadge(responseMeta);

  for (const y of CENSUS_YEARS) {
    const sRes = await fetch(`/api/census/summary?year=${y}`);
    if (sRes.ok) state.summaryByYear[y] = await sRes.json();
  }

  const allDbNames = new Set();
  for (const k of Object.keys(state.recordsByTownlandYear)) {
    allDbNames.add(k.split("|")[0]);
  }

  (geo.features || []).forEach(f => {
    const nm = String(f?.properties?.TL_ENGLISH || "").trim().toLowerCase();
    if (nm) {
      state.geoFeatureByName[nm] = f;
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
        const [details, workhouseData, unifiedRecs] = await Promise.all([
          fetchTownlandDetails(nm),
          fetchWorkhouseByTownland(nm),
          fetchUnifiedSummary(nm),
        ]);
        state.townlandDetails = details;
        state.workhouseData = workhouseData;
        state.unifiedSummary = unifiedRecs;
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

  window.addEventListener("languageChanged", () => renderTownlandPanel(state.selectedTownland));

  renderTownlandPanel(null);

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
    state.geoLayer.eachLayer(layer => {
      const nm = String(layer.feature?.properties?.TL_ENGLISH || "").trim();
      const r = getBestRecord(nm, state.year);
      const hasExact = !!getRecord(nm, state.year);
      const yearLabel = (r && !hasExact) ? `~${r.year}` : String(state.year);
      layer.setTooltipContent(`<b>${nm}</b><br/>Population ${yearLabel}: ${valueOrDash(r?.total)}`);
    });
  }

  async function fetchTownlandDetails(townlandName) {
    try {
      const res = await fetch(`/api/census/townland?name=${encodeURIComponent(townlandName)}`);
      if (res.ok) {
        const payload = await res.json();
        return payload.data || payload;
      }
    } catch (e) {
      console.error("census.js: fetchTownlandDetails error", e);
    }
    return null;
  }

  async function fetchUnifiedSummary(townlandName) {
    try {
      const res = await fetch(`/api/unified/records?townland=${encodeURIComponent(townlandName)}`);
      if (!res.ok) return null;
      const recs = await res.json();
      if (!Array.isArray(recs) || recs.length === 0) return null;
      return {
        total: recs.length,
        tenancy:   recs.filter(r => r.has_tenancy_record).length,
        eviction:  recs.filter(r => r.has_eviction_record).length,
        emigration: recs.filter(r => r.has_emigration_record).length,
      };
    } catch (e) {
      return null;
    }
  }

  async function fetchWorkhouseByTownland(townlandName) {
    try {
      const res = await fetch(`/api/unified/workhouse-by-townland?townland=${encodeURIComponent(townlandName)}`);
      if (res.ok) return await res.json();
    } catch (e) {
      console.error("census.js: fetchWorkhouseByTownland error", e);
    }
    return { linked: [], unlinked: [] };
  }

  function renderWorkhouseSection(wh) {
    if (!wh) return "";
    const linked = Array.isArray(wh.linked) ? wh.linked : [];
    const unlinked = Array.isArray(wh.unlinked) ? wh.unlinked : [];
    if (!linked.length && !unlinked.length) return "";

    const renderLinkedCard = (r) => `
      <div style="padding:10px;border:1px solid #d8b4fe;border-radius:8px;background:#faf5ff;margin-bottom:8px;">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;">
          <span style="font-weight:700;color:#4c1d95;font-size:13px;">${r.wh_forename || ""} ${r.wh_surname || r.raw_name || "Unknown"}</span>
          <span style="font-size:10px;font-weight:700;padding:2px 8px;border-radius:999px;background:${r.match_label === "CONFIRMED_MATCH" ? "#dcfce7" : "#fff7ed"};color:${r.match_label === "CONFIRMED_MATCH" ? "#15803d" : "#92400e"};">${r.match_label === "CONFIRMED_MATCH" ? "Confirmed" : "Possible"}</span>
        </div>
        <div style="font-size:11px;color:#6b21a8;">Workhouse: ${r.raw_place || r.normalised_place || "-"} · Year: ${r.event_year || "-"} · Age: ${r.age || "-"}</div>
        <div style="font-size:11px;color:#475569;">Estate record: ${r.estate_forename || ""} ${r.estate_surname || ""} · ${r.townland || ""} · ${r.estate_year || ""} · Role: ${r.role || "-"}</div>
        <div style="font-size:10px;color:#7c3aed;margin-top:3px;">Match score: ${r.match_score != null ? (r.match_score * 100).toFixed(0) + "%" : "-"}</div>
        <div style="font-size:10px;color:#92400e;background:#fef3c7;border-radius:4px;padding:3px 6px;margin-top:5px;">
          ⚠ Please verify: this workhouse record may or may not refer to the same person as the estate record.
        </div>
      </div>`;

    const renderUnlinkedCard = (r) => `
      <div style="padding:8px;border:1px solid #e9d5ff;border-radius:8px;background:#f5f3ff;margin-bottom:6px;opacity:0.85;">
        <div style="font-weight:600;color:#5b21b6;font-size:12px;">${r.raw_name || "Unknown"}</div>
        <div style="font-size:11px;color:#6b21a8;">Place: ${r.raw_place || "-"} · Year: ${r.event_year || "-"} · Age: ${r.age || "-"}</div>
        <div style="font-size:10px;color:#7c3aed;">Source: ${r.source_table || "workhouse register"}</div>
      </div>`;

    const summaryParts = [
      linked.length ? `${linked.length} linked to estate records` : "",
      unlinked.length ? `${unlinked.length} unmatched` : "",
    ].filter(Boolean).join(", ");

    let innerHtml = "";
    if (linked.length) {
      innerHtml += `
        <div style="font-size:11px;font-weight:700;color:#5b21b6;margin-bottom:6px;">Linked to estate records (${linked.length})</div>
        <div style="font-size:11px;color:#7c2d12;background:#fff7ed;border:1px solid #fdba74;border-radius:6px;padding:6px 8px;margin-bottom:8px;line-height:1.5;">
          These workhouse records have been algorithmically matched to estate records in this townland.
          <strong>Always verify that these refer to the same individual</strong> before drawing conclusions.
          common names and shared places can produce false matches.
        </div>
        ${linked.map(renderLinkedCard).join("")}`;
    }
    if (unlinked.length) {
      innerHtml += `
        <div style="font-size:11px;font-weight:700;color:#5b21b6;margin-top:${linked.length ? "10px" : "0"};margin-bottom:6px;">Unmatched mentions (${unlinked.length})</div>
        <div style="font-size:11px;color:#475569;margin-bottom:6px;">
          Workhouse records mentioning this area, not yet matched to a specific estate record.
        </div>
        ${unlinked.map(renderUnlinkedCard).join("")}`;
    }

    const whId = "wh-" + Math.random().toString(36).slice(2);
    return `
      <div style="margin-top:14px;">
        <button type="button"
          onclick="var c=document.getElementById('${whId}');var isOpen=c.style.display!=='none';c.style.display=isOpen?'none':'block';this.querySelector('.wh-arrow').textContent=isOpen?'▸':'▾';this.querySelector('.wh-hint').textContent=isOpen?'Click to expand':'Click to collapse';"
          style="width:100%;cursor:pointer;padding:10px 14px;background:#faf5ff;border:1px solid #d8b4fe;border-radius:10px;font-size:12px;font-weight:700;color:#4c1d95;display:flex;align-items:center;justify-content:space-between;text-align:left;">
          <span>🏥 Workhouse Records: ${summaryParts}</span>
          <span style="display:flex;align-items:center;gap:6px;">
            <span class="wh-hint" style="font-size:10px;font-weight:400;color:#7c3aed;">Click to expand</span>
            <span class="wh-arrow" style="font-size:13px;color:#7c3aed;">▸</span>
          </span>
        </button>
        <div id="${whId}" style="display:none;padding:12px 14px;background:#faf5ff;border:1px solid #d8b4fe;border-top:none;border-radius:0 0 10px 10px;">
          ${innerHtml}
        </div>
      </div>`;
  }

  function renderTownlandPanel(townland) {
    const title     = $("censusTownlandTitle");
    const meta      = $("censusTownlandMeta");
    const kpis      = $("censusKpis");
    const timeline  = $("censusTimeline");
    const picContainer   = $("censusTownlandSvgContainer");
    const detailContainer = $("censusTownlandDetail");
    const t = window.t || (k => k);

    const workhouseContainer = $("censusWorkhouseSection");
    if (!townland) {
      if (title) title.textContent = t("selectTownland") || "Select a Townland";
      if (meta) meta.textContent = t("clickMapPolygonInspect") || "Click a townland on the map to inspect yearly values.";
      if (kpis) kpis.innerHTML = "";
      if (timeline) timeline.innerHTML = "";
      if (detailContainer) detailContainer.innerHTML = "";
      if (workhouseContainer) workhouseContainer.innerHTML = "";
      state.unifiedSummary = null;
      if (picContainer) picContainer.style.display = "none";
      return;
    }

    const d = state.townlandDetails || {};
    const canonicalName = d.townland ? d.townland.toLowerCase() : townland.toLowerCase();
    const rec        = state.recordsByTownlandYear[key(canonicalName, state.year)]
                    || getRecord(townland, state.year);
    const bestRec    = rec || getBestRecord(townland, state.year);
    const kpiYear    = bestRec?.year ?? state.year;
    const kpiIsApprox = !rec && bestRec;

    if (title) {
      title.textContent = townland;
      if (d.gaelic_name) title.innerHTML = `${townland} <span style="font-size:0.7em;font-style:italic;color:var(--moss);font-weight:400;">${d.gaelic_name}</span>`;
    }

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

    if (picContainer) {
      picContainer.style.display = "flex";
      const feature = state.geoFeatureByName[townland.trim().toLowerCase()];
      updateSatelliteView(feature, townland, rec?.total);
    }

    if (detailContainer) {
      let html = "";
      const kgUri = d.uri || d.kg_uri || null;
      const hasKg = Boolean(kgUri);

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

      const us = state.unifiedSummary;
      if (us && us.total > 0) {
        const exploreUrl = `/?townland=${encodeURIComponent(townland)}`;
        const badges = [
          us.tenancy   ? `<span style="background:#dbeafe;color:#1e40af;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;">Tenancy: ${us.tenancy}</span>` : "",
          us.eviction  ? `<span style="background:#fee2e2;color:#991b1b;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;">Eviction: ${us.eviction}</span>` : "",
          us.emigration? `<span style="background:#d1fae5;color:#065f46;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;">Emigration: ${us.emigration}</span>` : "",
        ].filter(Boolean);
        html += `
        <div style="margin-bottom:12px;padding:10px 14px;background:#f0f9ff;border-radius:10px;border:1px solid #bae6fd;">
          <div style="font-size:10px;color:#0c4a6e;font-weight:800;text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px;">📋 Estate Records</div>
          <div style="font-size:22px;font-weight:800;color:#0369a1;margin-bottom:6px;">${us.total} <span style="font-size:13px;font-weight:500;color:#0c4a6e;">records</span></div>
          <div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:10px;">${badges.join("")}</div>
          <a href="${exploreUrl}"
             style="display:inline-flex;align-items:center;gap:5px;padding:6px 12px;background:#0369a1;border-radius:8px;color:#fff;font-size:12px;font-weight:600;text-decoration:none;">
            Explore on Map →
          </a>
        </div>`;
      }

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

    if (workhouseContainer) {
      workhouseContainer.innerHTML = renderWorkhouseSection(state.workhouseData);
    }

    if (kpis) {
      const kpiIsEstate = bestRec && bestRec.source === "json";

      let approxNote = "";
      if (kpiIsApprox && kpiIsEstate) {
        approxNote = `<div style="grid-column:1/-1;padding:8px 10px;background:#fef9c3;border:1px solid #fde68a;border-radius:6px;font-size:11px;color:#78350f;margin-bottom:6px;line-height:1.5;">
          <strong>No official census data for ${state.year}.</strong> Showing estate survey data from <strong>${kpiYear}</strong>.
          Male/female breakdown and house counts were not recorded in the estate surveys; only total population is available.
        </div>`;
      } else if (!kpiIsApprox && kpiIsEstate) {
        approxNote = `<div style="grid-column:1/-1;padding:8px 10px;background:#fef9c3;border:1px solid #fde68a;border-radius:6px;font-size:11px;color:#78350f;margin-bottom:6px;line-height:1.5;">
          <strong>Official census data is not available for this townland.</strong>
          The figures below come from the Coolattin estate survey (total population only).
          Male, female, and house counts were not recorded in these estate records.
        </div>`;
      } else if (kpiIsApprox) {
        approxNote = `<div style="grid-column:1/-1;padding:6px 10px;background:#fef9c3;border:1px solid #fde68a;border-radius:6px;font-size:11px;color:#78350f;margin-bottom:4px;">
          No data for ${state.year}. Showing nearest available year: ${kpiYear}.
        </div>`;
      }

      kpis.innerHTML = approxNote + [
        kpiCard(t("totalPopulation") || "Total Population", valueOrDash(bestRec?.total)),
        kpiCard(t("male") || "Male", kpiIsEstate ? "—" : valueOrDash(bestRec?.male)),
        kpiCard(t("female") || "Female", kpiIsEstate ? "—" : valueOrDash(bestRec?.female)),
        kpiCard(t("inhabitedHouses") || "Inhabited Houses", kpiIsEstate ? "—" : valueOrDash(bestRec?.inhabited)),
        kpiCard(t("uninhabitedHouses") || "Uninhabited", kpiIsEstate ? "—" : valueOrDash(bestRec?.uninhabited)),
        kpiCard(t("year") || "Year", kpiIsApprox ? `~${kpiYear}` : String(state.year)),
      ].join("");
    }

    if (timeline) {
      const allYearsWithData = ALL_DATA_YEARS.filter(y => {
        const r = state.recordsByTownlandYear[key(canonicalName, y)]
               || getRecord(townland, y);
        return r && r.total != null;
      });

      const geoFeature = state.geoFeatureByName[townland.trim().toLowerCase()];
      const geoProps   = geoFeature?.properties || {};
      const totalClearances = geoProps.Total_Clearances;
      const clearanceYears  = [1847,1848,1849,1850,1851,1852,1853,1854,1855,1856]
        .map(y => ({ year: y, count: geoProps[`Clearances_${y}`] || 0 }))
        .filter(c => c.count > 0);

      if (allYearsWithData.length === 0) {
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
              <div style="margin-top:6px;font-size:11px;color:#92400e;">By year: ${breakdown}</div>
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
                ? "This townland is named <em>Newtown</em>; population data in the records is stored under electoral-division variants (e.g. <em>Newtown ED Tinahely</em>) which cannot be matched to this entry."
                : "The townland may have been too small, uninhabited, or merged with a neighbouring area in the original records."}
            </div>
            ${clearanceNote}
          </div>`;
        return;
      }

      const hasCensusData = allYearsWithData.some(y => {
        const r = state.recordsByTownlandYear[key(canonicalName, y)] || getRecord(townland, y);
        return r && (r.source === "csv_seed" || r.source === "kg");
      });
      const hasEstateData = allYearsWithData.some(y => {
        const r = state.recordsByTownlandYear[key(canonicalName, y)] || getRecord(townland, y);
        return r && r.source === "json";
      });
      const hasMixed = hasCensusData && hasEstateData;

      let html = "";

      if (!hasCensusData) {
        html += `
          <div style="font-size:12px;color:#78350f;margin-bottom:10px;padding:10px 12px;background:#fef9c3;border-radius:8px;border:1px solid #fde68a;line-height:1.6;">
            <strong>Official census data (1841–1891) is not available for this townland.</strong><br>
            The table below shows records from the <strong>Coolattin estate survey</strong> only.
            These figures record total population. Male/female breakdown and house counts were not captured in estate surveys.
          </div>`;
      } else if (hasMixed) {
        html += `
          <div style="font-size:11px;color:#475569;margin-bottom:8px;padding:6px 10px;background:#f8fafc;border-radius:6px;border:1px solid #e2e8f0;">
            This townland has both <strong>official census records</strong> (1841–1891, full breakdown) and
            <strong>Coolattin estate survey</strong> entries (1827–1868, total population only).
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
              const active    = y === state.year || y === kpiYear;
              const isSurvey  = r && r.source === "json";
              const rowBg     = active ? "#eff6ff" : "transparent";
              const srcLabel  = isSurvey ? "Estate Survey" : "Official Census";
              const srcColor  = isSurvey ? "#78350f" : "#1d4ed8";
              const maleVal   = isSurvey ? "—" : valueOrDash(r?.male);
              const femaleVal = isSurvey ? "—" : valueOrDash(r?.female);
              const housesVal = isSurvey ? "—" : valueOrDash(r?.inhabited);
              return `<tr style="background:${rowBg};cursor:pointer;${isSurvey ? "opacity:0.85;" : ""}" data-year="${y}">
                <td style="padding:7px 8px;border-bottom:1px solid #f1f5f9;font-weight:${active ? "800" : "600"};color:${active ? "#1d4ed8" : "#374151"};">${y}</td>
                <td style="padding:7px 8px;border-bottom:1px solid #f1f5f9;text-align:right;font-weight:${active ? "700" : "400"};">${valueOrDash(r?.total)}</td>
                <td style="padding:7px 8px;border-bottom:1px solid #f1f5f9;text-align:right;color:${isSurvey ? "#94a3b8" : "inherit"};">${maleVal}</td>
                <td style="padding:7px 8px;border-bottom:1px solid #f1f5f9;text-align:right;color:${isSurvey ? "#94a3b8" : "inherit"};">${femaleVal}</td>
                <td style="padding:7px 8px;border-bottom:1px solid #f1f5f9;text-align:right;color:${isSurvey ? "#94a3b8" : "inherit"};">${housesVal}</td>
                <td style="padding:7px 8px;border-bottom:1px solid #f1f5f9;text-align:right;font-size:10px;color:${srcColor};">${srcLabel}</td>
              </tr>`;
            }).join("")}
          </tbody>
        </table>`;

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
    if (svgDesc) svgDesc.textContent = `${townlandName}, historic townland in County Wicklow.`;

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

  const urlParams = new URLSearchParams(window.location.search);
  const tlParam = urlParams.get("townland");
  if (tlParam) {
    const matchedFeature = (geo.features || []).find(f => {
      const nm = String(f?.properties?.TL_ENGLISH || "").trim();
      return nm.toLowerCase() === tlParam.toLowerCase();
    });

    if (matchedFeature) {
      const nm = String(matchedFeature.properties.TL_ENGLISH || "").trim();
      state.selectedTownland = nm;
      window.history.replaceState({}, "", window.location.pathname);
      state.townlandDetails = await fetchTownlandDetails(nm);
      recolorMap();
      renderTownlandPanel(nm);
      try {
        map.fitBounds(L.geoJSON(matchedFeature).getBounds(), { padding: [40, 40], maxZoom: 14 });
      } catch (_) {}
    }
  }
});
