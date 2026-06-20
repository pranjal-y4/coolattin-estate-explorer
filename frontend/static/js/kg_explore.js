/* kg_explore.js — D3.js force-directed Geographic Knowledge Graph */

document.addEventListener("DOMContentLoaded", () => {
  const searchEl        = document.getElementById("kg-search");
  const clearBtn        = document.getElementById("kg-search-clear");
  const resetZoomBtn    = document.getElementById("kg-reset-zoom");
  const nodeCountEl     = document.getElementById("kg-node-count");
  const statsEl         = document.getElementById("kg-stats");
  const detailCard      = document.getElementById("kg-detail-card");
  const detailEl        = document.getElementById("kg-detail");
  const searchCard      = document.getElementById("kg-search-card");
  const searchResultsEl = document.getElementById("kg-search-results");
  const tooltipEl       = document.getElementById("kg-tooltip");

  const svg   = d3.select("#kg-svg");
  const svgEl = document.getElementById("kg-svg");

  let allNodes = [], allEdges = [];
  let simulation = null;
  let zoomBehavior = null;
  let gRoot = null;
  let linkSel = null, nodeSel = null, labelSel = null;
  let searchTimeout = null;

  function escHtml(s) {
    return String(s ?? "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
  }

  // ── Node colour by type ────────────────────────────────────────────────────
  function nodeColor(d) {
    switch (d.type) {
      case "County":      return "#0369a1";
      case "Barony":      return "#b45309";
      case "CivilParish": return "#7c3aed";
      case "Townland":    return d.color || "#15803d";
      default:            return "#64748b";
    }
  }

  // ── Load graph data ────────────────────────────────────────────────────────
  async function loadGraph() {
    statsEl.textContent = "Loading geographic hierarchy…";
    try {
      const res  = await fetch("/api/kg/graph");
      const data = await res.json();
      allNodes = data.nodes || [];
      allEdges = data.edges || [];
      const m = data.meta || {};

      statsEl.innerHTML = [
        `<div style="margin-bottom:2px;font-weight:700;color:#0f172a;">Geographic Hierarchy</div>`,
        `<div>Counties: <strong>${m.county_count ?? 0}</strong></div>`,
        `<div>Baronies: <strong>${m.barony_count ?? 0}</strong></div>`,
        `<div>Civil Parishes: <strong>${m.parish_count ?? 0}</strong></div>`,
        `<div>Townlands: <strong>${m.townland_count ?? 0}</strong></div>`,
        `<div style="margin-top:4px;">Townlands with Gaelic name: <strong>${m.with_gaelic ?? 0}</strong></div>`,
        `<div>Total nodes: <strong>${m.node_count ?? 0}</strong> &nbsp; Edges: <strong>${m.edge_count ?? 0}</strong></div>`,
        `<div style="margin-top:6px;font-size:10px;color:#94a3b8;">Click any node to explore. Drag to rearrange.</div>`,
      ].join("");

      nodeCountEl.textContent = `${allNodes.length} nodes · ${allEdges.length} edges`;
      buildSimulation();
    } catch (err) {
      statsEl.textContent = "Failed to load graph: " + err.message;
    }
  }

  // ── D3 force simulation ────────────────────────────────────────────────────
  function buildSimulation() {
    svg.selectAll("*").remove();

    const rect = svgEl.getBoundingClientRect();
    const W = rect.width  || 900;
    const H = rect.height || 600;

    zoomBehavior = d3.zoom().scaleExtent([0.03, 6]).on("zoom", (event) => {
      gRoot.attr("transform", event.transform);
    });
    svg.call(zoomBehavior);

    const defs = svg.append("defs");
    defs.append("marker")
      .attr("id", "arrowhead")
      .attr("viewBox", "0 -4 8 8").attr("refX", 14).attr("refY", 0)
      .attr("markerWidth", 4).attr("markerHeight", 4).attr("orient", "auto")
      .append("path").attr("d", "M0,-4L8,0L0,4").attr("fill", "#475569");

    gRoot = svg.append("g");

    const nodes = allNodes.map(d => ({ ...d }));
    const nodeById = new Map(nodes.map(n => [n.id, n]));
    const edges = allEdges
      .map(e => ({ ...e, source: nodeById.get(e.source), target: nodeById.get(e.target) }))
      .filter(e => e.source && e.target);

    simulation = d3.forceSimulation(nodes)
      .force("link", d3.forceLink(edges).id(d => d.id)
        .distance(e => {
          if (e.type === "county_barony")  return 140;
          if (e.type === "barony_parish")  return 90;
          if (e.type === "parish_townland") return 50;
          return 80;
        })
        .strength(e => {
          if (e.type === "county_barony")  return 0.6;
          if (e.type === "barony_parish")  return 0.5;
          if (e.type === "parish_townland") return 0.45;
          return 0.3;
        }))
      .force("charge", d3.forceManyBody().strength(d => {
        if (d.type === "County")      return -1200;
        if (d.type === "Barony")      return -600;
        if (d.type === "CivilParish") return -280;
        return -30;
      }))
      .force("center", d3.forceCenter(W / 2, H / 2))
      .force("collide", d3.forceCollide().radius(d => (d.size || 8) + 3))
      .alphaDecay(0.02);

    // Links — coloured by hierarchy level
    linkSel = gRoot.append("g").attr("class","links").selectAll("line")
      .data(edges).enter().append("line")
        .attr("stroke", e => {
          if (e.type === "county_barony")  return "#60a5fa";
          if (e.type === "barony_parish")  return "#a78bfa";
          if (e.type === "parish_townland") return "#6ee7b7";
          return "#94a3b8";
        })
        .attr("stroke-width", e => {
          if (e.type === "county_barony")  return 1.5;
          if (e.type === "barony_parish")  return 1.0;
          return 0.6;
        })
        .attr("stroke-opacity", e => {
          if (e.type === "county_barony")  return 0.7;
          if (e.type === "barony_parish")  return 0.55;
          return 0.3;
        })
        .attr("marker-end", "url(#arrowhead)");

    // Node circles
    nodeSel = gRoot.append("g").attr("class","nodes").selectAll("circle")
      .data(nodes).enter().append("circle")
        .attr("r", d => d.size || 8)
        .attr("fill", nodeColor)
        .attr("stroke", "#0f172a")
        .attr("stroke-width", 1)
        .attr("cursor", "pointer")
        .call(d3.drag()
          .on("start", (event, d) => {
            if (!event.active) simulation.alphaTarget(0.3).restart();
            d.fx = d.x; d.fy = d.y;
          })
          .on("drag", (event, d) => { d.fx = event.x; d.fy = event.y; })
          .on("end", (event, d) => {
            if (!event.active) simulation.alphaTarget(0);
            d.fx = null; d.fy = null;
          }))
        .on("click", (event, d) => { event.stopPropagation(); showDetail(d); })
        .on("mouseover", (event, d) => showTooltip(event, d))
        .on("mousemove", event => moveTooltip(event))
        .on("mouseout", () => hideTooltip());

    // Labels for County, Barony, and Parish nodes
    labelSel = gRoot.append("g").attr("class","labels").selectAll("text")
      .data(nodes.filter(n => n.type === "County" || n.type === "Barony" || n.type === "CivilParish"))
      .enter().append("text")
        .attr("font-size", d => d.type === "County" ? 13 : d.type === "Barony" ? 10 : 8)
        .attr("fill", d => d.type === "County" ? "#bae6fd" : d.type === "Barony" ? "#fde68a" : "#c4b5fd")
        .attr("font-weight", d => d.type === "County" ? "700" : "600")
        .attr("text-anchor", "middle")
        .attr("pointer-events", "none")
        .text(d => d.label);

    simulation.on("tick", () => {
      linkSel
        .attr("x1", e => e.source.x).attr("y1", e => e.source.y)
        .attr("x2", e => e.target.x).attr("y2", e => e.target.y);
      nodeSel.attr("cx", d => d.x).attr("cy", d => d.y);
      labelSel.attr("x", d => d.x).attr("y", d => (d.y || 0) - (d.size || 8) - 3);
    });

    svg.on("click", () => clearSelection());
  }

  // ── Detail panel ───────────────────────────────────────────────────────────
  function showDetail(d) {
    highlightNode(d.id);
    detailCard.style.display = "block";
    if (d.type === "Townland") {
      showTownlandDetail(d);
    } else if (d.type === "CivilParish") {
      showParishDetail(d);
    } else if (d.type === "Barony") {
      showBaronyDetail(d);
    } else if (d.type === "County") {
      showCountyDetail(d);
    }
  }

  function showTownlandDetail(d) {
    // Show basic info immediately, then async-load the rich detail
    const rows = [];
    rows.push(`<div style="font-weight:700;font-size:14px;color:#15803d;">Townland</div>`);
    rows.push(`<div style="font-weight:700;font-size:14px;color:#0f172a;">${escHtml(d.label)}</div>`);
    if (d.name_gaelic) {
      rows.push(`<div style="font-size:13px;color:#6b21a8;font-style:italic;margin-top:1px;">${escHtml(d.name_gaelic)}</div>`);
    }
    rows.push(`<div style="height:1px;background:#f1f5f9;margin:8px 0;"></div>`);
    if (d.civil_parish) rows.push(`<div><span style="color:#7c3aed;font-weight:700;">Parish:</span> ${escHtml(d.civil_parish)}</div>`);
    if (d.barony)       rows.push(`<div><span style="color:#b45309;font-weight:700;">Barony:</span> ${escHtml(d.barony)}</div>`);
    if (d.county)       rows.push(`<div><span style="color:#0369a1;font-weight:700;">County:</span> ${escHtml(d.county)}</div>`);
    if (d.electoral_division) rows.push(`<div><span style="color:#475467;">Electoral Division:</span> ${escHtml(d.electoral_division)}</div>`);
    if (d.placename_theme) rows.push(`<div><span style="color:#475467;">Placename Theme:</span> ${escHtml(d.placename_theme)}</div>`);
    if (d.centroid_lat && d.centroid_lon) {
      rows.push(`<div><span style="color:#475467;">Coordinates:</span> ${Number(d.centroid_lat).toFixed(5)}, ${Number(d.centroid_lon).toFixed(5)}</div>`);
    }
    if (d.record_count) {
      rows.push(`<div style="margin-top:6px;"><span style="color:#475467;">Estate records:</span> <strong>${d.record_count}</strong></div>`);
    }
    if (d.kg_uri) {
      rows.push(`<a href="${escHtml(d.kg_uri)}" target="_blank" rel="noopener" style="display:inline-block;margin-top:8px;font-size:12px;color:#0f766e;">Open VRTI KG URI ↗</a>`);
    }
    // Rich detail loading area
    rows.push(`<div id="kg-rich-detail" style="margin-top:12px;">
      <div style="display:flex;align-items:center;gap:6px;color:#64748b;font-size:11px;">
        <span style="animation:spin 1s linear infinite;display:inline-block;">⟳</span>
        Loading geographical & historical detail…
      </div>
    </div>`);
    detailEl.innerHTML = rows.join("");

    // Async fetch rich detail
    fetchRichTownlandDetail(d.label);
  }

  async function fetchRichTownlandDetail(name) {
    const richEl = document.getElementById("kg-rich-detail");
    if (!richEl) return;
    try {
      const res = await fetch(`/api/kg/townland-rich/${encodeURIComponent(name)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      renderRichDetail(richEl, data);
    } catch (err) {
      richEl.innerHTML = `<div style="font-size:11px;color:#dc2626;margin-top:6px;">Could not load rich detail: ${escHtml(String(err))}</div>`;
    }
  }

  function renderRichDetail(el, data) {
    const parts = [];
    parts.push(`<div style="height:1px;background:#e2e8f0;margin:10px 0;"></div>`);
    parts.push(`<div style="font-size:11px;font-weight:700;color:#0369a1;letter-spacing:.04em;margin-bottom:6px;">GEOGRAPHICAL & HISTORICAL DETAIL</div>`);

    // ── People summary ────────────────────────────────────────────────
    const ppl = (data.db_data || {}).people_summary || {};
    if (ppl.total_people) {
      parts.push(`<div style="font-size:12px;margin-bottom:6px;">
        <span style="font-weight:600;">Estate records (${ppl.earliest_year}–${ppl.latest_year}):</span>
        ${ppl.total_people} people —
        <span style="color:#0369a1;">${ppl.emigrants} emigrated</span> ·
        <span style="color:#dc2626;">${ppl.evicted} evicted</span> ·
        <span style="color:#059669;">${ppl.tenants} tenants</span>
      </div>`);
    }

    // Top surnames
    const snames = (data.db_data || {}).top_surnames || [];
    if (snames.length) {
      parts.push(`<div style="font-size:11px;color:#475467;margin-bottom:6px;">
        <span style="font-weight:600;">Key families:</span>
        ${snames.map(s => `<span style="background:#f0f9ff;border-radius:4px;padding:1px 5px;margin:1px;">${escHtml(s.surname)} (${s.n})</span>`).join(" ")}
      </div>`);
    }

    // ── Census trend ─────────────────────────────────────────────────
    const census = (data.db_data || {}).census || [];
    if (census.length) {
      const cells = census.map(r =>
        `<td style="text-align:center;padding:3px 6px;">${r.year}</td><td style="text-align:center;padding:3px 6px;">${r.total ?? "—"}</td>`
      );
      parts.push(`<details style="margin-bottom:6px;">
        <summary style="font-size:11px;font-weight:600;color:#475467;cursor:pointer;">Census population (${census.length} years)</summary>
        <table style="font-size:11px;border-collapse:collapse;margin-top:4px;width:100%;">
          <tr style="background:#f8fafc;"><th style="padding:3px 6px;">Year</th><th style="padding:3px 6px;">Total</th></tr>
          ${census.map(r => `<tr><td style="text-align:center;padding:3px 6px;">${r.year}</td><td style="text-align:center;padding:3px 6px;">${r.total ?? "—"}</td></tr>`).join("")}
        </table>
      </details>`);
    }

    // ── Clearances ────────────────────────────────────────────────────
    const clears = (data.db_data || {}).clearances || [];
    if (clears.length) {
      parts.push(`<details style="margin-bottom:6px;">
        <summary style="font-size:11px;font-weight:600;color:#dc2626;cursor:pointer;">Evictions / clearances (${clears.length} year(s))</summary>
        <div style="font-size:11px;margin-top:4px;">
          ${clears.map(r => `<div>${r.year}: <strong>${r.evictions}</strong> eviction(s)</div>`).join("")}
        </div>
      </details>`);
    }

    // ── Heritage features ─────────────────────────────────────────────
    const heritage = (data.db_data || {}).heritage || [];
    if (heritage.length) {
      parts.push(`<div style="font-size:11px;margin-bottom:6px;">
        <span style="font-weight:600;color:#92400e;">Heritage:</span>
        ${heritage.map(h => `<span style="background:#fefce8;border-radius:4px;padding:1px 5px;margin:1px;">${escHtml(h.feature_group)}${h.monument_class ? ': ' + escHtml(h.monument_class) : ''}</span>`).join(" ")}
      </div>`);
    }

    // ── VRTI external links ────────────────────────────────────────────
    const vrti = data.vrti_data || {};
    if (Array.isArray(vrti.links) && vrti.links.length) {
      parts.push(`<div style="font-size:11px;margin-bottom:6px;">
        <span style="font-weight:600;">External links:</span>
        ${vrti.links.map(l => `<a href="${escHtml(l)}" target="_blank" rel="noopener" style="color:#0f766e;margin-left:4px;">${escHtml(l.replace(/^https?:\/\//, "").split("/")[0])}</a>`).join(" · ")}
      </div>`);
    }

    // ── LLM narrative ─────────────────────────────────────────────────
    if (data.narrative) {
      parts.push(`<div style="font-size:12px;line-height:1.6;color:#0f172a;background:#f8fafc;border-radius:8px;padding:10px;margin-bottom:8px;border-left:3px solid #15803d;">
        ${escHtml(data.narrative).replace(/\n\n/g, "</p><p style='margin-top:8px;'>").replace(/\n/g, "<br>")}
      </div>`);
    } else if (data.narrative_error) {
      parts.push(`<div style="font-size:11px;color:#92400e;background:#fef3c7;border-radius:6px;padding:6px;margin-bottom:6px;">Narrative unavailable: ${escHtml(data.narrative_error)}</div>`);
    }

    // ── Generated SPARQL query ────────────────────────────────────────
    if (data.generated_sparql) {
      const resultCount = (data.sparql_results || []).length;
      const sparqlErrNote = data.sparql_error ? `<div style="color:#dc2626;font-size:10px;margin-top:4px;">${escHtml(data.sparql_error)}</div>` : "";
      parts.push(`<details style="margin-bottom:6px;">
        <summary style="font-size:11px;font-weight:600;color:#6b21a8;cursor:pointer;">
          VRTI SPARQL Query (LLM-generated · ${resultCount} result row(s))
        </summary>
        <pre style="font-size:10px;background:#0f172a;color:#e2e8f0;border-radius:6px;padding:8px;overflow-x:auto;margin-top:4px;white-space:pre-wrap;">${escHtml(data.generated_sparql)}</pre>
        ${sparqlErrNote}
        ${resultCount > 0 ? `<div style="font-size:10px;color:#475467;margin-top:4px;">${resultCount} binding(s) returned from VRTI endpoint.</div>` : ""}
      </details>`);
    }

    el.innerHTML = parts.join("") ||
      `<div style="font-size:11px;color:#64748b;">No additional detail available for this townland.</div>`;
  }

  function showParishDetail(d) {
    const townlands = allNodes.filter(n => n.type === "Townland" && n.civil_parish === d.label);
    const withGaelic = townlands.filter(n => n.name_gaelic).length;
    const tlList = townlands
      .sort((a, b) => escHtml(a.label).localeCompare(escHtml(b.label)))
      .slice(0, 30)
      .map(n => {
        const gaelic = n.name_gaelic ? ` <span style="color:#a78bfa;font-size:10px;">(${escHtml(n.name_gaelic)})</span>` : "";
        return `<div style="padding:2px 0;font-size:11px;">${escHtml(n.label)}${gaelic}</div>`;
      }).join("");

    detailEl.innerHTML = [
      `<div style="font-weight:700;font-size:14px;color:#7c3aed;">Civil Parish</div>`,
      `<div style="font-weight:700;font-size:13px;color:#0f172a;">${escHtml(d.label)}</div>`,
      d.barony ? `<div style="font-size:12px;color:#b45309;">Barony: ${escHtml(d.barony)}</div>` : "",
      d.county ? `<div style="font-size:12px;color:#0369a1;">County: ${escHtml(d.county)}</div>` : "",
      `<div style="height:1px;background:#f1f5f9;margin:8px 0;"></div>`,
      `<div>Townlands: <strong>${townlands.length}</strong></div>`,
      withGaelic ? `<div>With Gaelic name: <strong>${withGaelic}</strong></div>` : "",
      `<div style="margin-top:6px;max-height:240px;overflow-y:auto;">${tlList}</div>`,
      townlands.length > 30 ? `<div style="font-size:10px;color:#94a3b8;margin-top:4px;">Showing first 30</div>` : "",
    ].join("");
  }

  function showBaronyDetail(d) {
    const parishes  = allNodes.filter(n => n.type === "CivilParish" && n.barony === d.label);
    const townlands = allNodes.filter(n => n.type === "Townland"    && n.barony === d.label);
    detailEl.innerHTML = [
      `<div style="font-weight:700;font-size:14px;color:#b45309;">Barony</div>`,
      `<div style="font-weight:700;font-size:13px;color:#0f172a;">${escHtml(d.label)}</div>`,
      d.county ? `<div style="font-size:12px;color:#0369a1;">County: ${escHtml(d.county)}</div>` : "",
      `<div style="height:1px;background:#f1f5f9;margin:8px 0;"></div>`,
      `<div>Civil Parishes: <strong>${parishes.length}</strong></div>`,
      `<div>Townlands: <strong>${townlands.length}</strong></div>`,
      `<div style="margin-top:6px;font-size:11px;color:#64748b;">Parishes: ${parishes.map(p => escHtml(p.label)).join(", ") || "—"}</div>`,
    ].join("");
  }

  function showCountyDetail(d) {
    const baronies  = allNodes.filter(n => n.type === "Barony"      && n.county === d.label);
    const parishes  = allNodes.filter(n => n.type === "CivilParish" && n.county === d.label);
    const townlands = allNodes.filter(n => n.type === "Townland"    && n.county === d.label);
    detailEl.innerHTML = [
      `<div style="font-weight:700;font-size:14px;color:#0369a1;">County</div>`,
      `<div style="font-weight:700;font-size:13px;color:#0f172a;">${escHtml(d.label)}</div>`,
      `<div style="height:1px;background:#f1f5f9;margin:8px 0;"></div>`,
      `<div>Baronies: <strong>${baronies.length}</strong></div>`,
      `<div>Civil Parishes: <strong>${parishes.length}</strong></div>`,
      `<div>Townlands: <strong>${townlands.length}</strong></div>`,
    ].join("");
  }

  function clearSelection() {
    detailCard.style.display = "none";
    detailEl.innerHTML = "";
    if (nodeSel) nodeSel.attr("opacity", 1).attr("stroke-width", 1).attr("stroke", "#0f172a");
    if (linkSel) linkSel.attr("stroke-opacity", e => {
      if (e.type === "county_barony")  return 0.7;
      if (e.type === "barony_parish")  return 0.55;
      return 0.3;
    });
  }

  function highlightNode(nodeId) {
    if (!nodeSel) return;
    // Find all connected node IDs
    const connected = new Set([nodeId]);
    allEdges.forEach(e => {
      const sid = typeof e.source === "object" ? e.source?.id : e.source;
      const tid = typeof e.target === "object" ? e.target?.id : e.target;
      if (sid === nodeId) connected.add(tid);
      if (tid === nodeId) connected.add(sid);
    });
    nodeSel
      .attr("opacity",      d => connected.has(d.id) ? 1 : 0.15)
      .attr("stroke",       d => d.id === nodeId ? "#fbbf24" : "#0f172a")
      .attr("stroke-width", d => d.id === nodeId ? 3 : 1);
    if (linkSel) {
      linkSel.attr("stroke-opacity", e => {
        const sid = typeof e.source === "object" ? e.source?.id : e.source;
        const tid = typeof e.target === "object" ? e.target?.id : e.target;
        return (sid === nodeId || tid === nodeId) ? 0.85 : 0.05;
      });
    }
  }

  // ── Tooltip ────────────────────────────────────────────────────────────────
  function showTooltip(event, d) {
    let html = `<strong>${escHtml(d.label)}</strong>`;
    if (d.name_gaelic) html += ` <span style="color:#c4b5fd;font-style:italic;">(${escHtml(d.name_gaelic)})</span>`;
    html += `<br/><span style="color:#94a3b8;font-size:10px;">${escHtml(d.type)}</span>`;
    if (d.type === "Townland") {
      if (d.civil_parish) html += `<br/>Parish: ${escHtml(d.civil_parish)}`;
      if (d.barony)       html += `<br/>Barony: ${escHtml(d.barony)}`;
      if (d.record_count) html += `<br/>Records: ${d.record_count}`;
    } else if (d.type === "CivilParish") {
      const count = allNodes.filter(n => n.type === "Townland" && n.civil_parish === d.label).length;
      html += `<br/>${count} townland${count !== 1 ? "s" : ""}`;
    } else if (d.type === "Barony") {
      const count = allNodes.filter(n => n.type === "Townland" && n.barony === d.label).length;
      html += `<br/>${count} townland${count !== 1 ? "s" : ""}`;
    }
    tooltipEl.innerHTML = html;
    tooltipEl.style.display = "block";
    moveTooltip(event);
  }
  function moveTooltip(event) {
    tooltipEl.style.left = (event.clientX + 14) + "px";
    tooltipEl.style.top  = (event.clientY - 10) + "px";
  }
  function hideTooltip() { tooltipEl.style.display = "none"; }

  // ── Search ─────────────────────────────────────────────────────────────────
  searchEl.addEventListener("input", () => {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(doSearch, 180);
  });

  function doSearch() {
    const q = (searchEl.value || "").trim().toLowerCase();
    if (!q || q.length < 2) {
      clearSelection();
      searchCard.style.display = "none";
      searchResultsEl.innerHTML = "";
      nodeCountEl.textContent = `${allNodes.length} nodes · ${allEdges.length} edges`;
      return;
    }

    // Match against name, name_gaelic, civil_parish, barony, county
    const matches = allNodes.filter(n =>
      (n.label || "").toLowerCase().includes(q) ||
      (n.name_gaelic || "").toLowerCase().includes(q) ||
      (n.civil_parish || "").toLowerCase().includes(q) ||
      (n.barony || "").toLowerCase().includes(q) ||
      (n.county || "").toLowerCase().includes(q)
    );

    if (!nodeSel) return;

    const matchIds = new Set(matches.map(n => n.id));
    nodeSel
      .attr("opacity",      d => matchIds.has(d.id) ? 1 : 0.08)
      .attr("stroke",       d => matchIds.has(d.id) ? "#fbbf24" : "#0f172a")
      .attr("stroke-width", d => matchIds.has(d.id) ? 2.5 : 1);
    if (linkSel) {
      linkSel.attr("stroke-opacity", e => {
        const sid = typeof e.source === "object" ? e.source?.id : e.source;
        const tid = typeof e.target === "object" ? e.target?.id : e.target;
        return (matchIds.has(sid) && matchIds.has(tid)) ? 0.6 : 0.04;
      });
    }

    nodeCountEl.textContent = `${matches.length} match${matches.length !== 1 ? "es" : ""} · ${allNodes.length} total`;
    searchCard.style.display = "block";

    const tlMatches    = matches.filter(n => n.type === "Townland");
    const parishMatches = matches.filter(n => n.type === "CivilParish");

    const items = [
      ...parishMatches.slice(0, 5).map(n => `
        <div style="padding:5px 0;border-bottom:1px solid #f1f5f9;">
          <div style="font-weight:700;color:#7c3aed;font-size:12px;">${escHtml(n.label)} <span style="font-weight:400;color:#94a3b8;font-size:10px;">parish</span></div>
        </div>`),
      ...tlMatches.slice(0, 10).map(n => {
        const gaelic = n.name_gaelic ? ` <span style="color:#a78bfa;font-size:10px;">(${escHtml(n.name_gaelic)})</span>` : "";
        return `<div style="padding:5px 0;border-bottom:1px solid #f1f5f9;font-size:12px;">
          <span style="font-weight:700;color:#15803d;">${escHtml(n.label)}</span>${gaelic}
          ${n.civil_parish ? `<div style="font-size:10px;color:#94a3b8;">${escHtml(n.civil_parish)}</div>` : ""}
        </div>`;
      }),
    ];

    searchResultsEl.innerHTML = items.join("") +
      (matches.length > 15 ? `<div style="font-size:10px;color:#94a3b8;margin-top:4px;">…and ${matches.length - 15} more</div>` : "");
  }

  clearBtn.addEventListener("click", () => {
    searchEl.value = "";
    clearSelection();
    searchCard.style.display = "none";
    searchResultsEl.innerHTML = "";
    nodeCountEl.textContent = `${allNodes.length} nodes · ${allEdges.length} edges`;
  });

  resetZoomBtn.addEventListener("click", () => {
    if (zoomBehavior) svg.transition().duration(400).call(zoomBehavior.transform, d3.zoomIdentity);
  });

  svg.on("click", () => clearSelection());

  loadGraph();
});
