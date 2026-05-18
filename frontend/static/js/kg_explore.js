/* kg_explore.js — D3.js force-directed Knowledge Graph (full-database view) */

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
    return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  // ── Node colour helper ────────────────────────────────────────────────────
  function nodeColor(d) {
    if (d.type === "EventHub") {
      if (d.id === "eh_emigration") return "#16a34a";
      if (d.id === "eh_eviction")   return "#dc2626";
      return "#2563eb";
    }
    if (d.type === "DataHub") {
      if (d.id === "dh_census")     return "#0891b2";
      return "#9333ea";
    }
    if (d.type === "Parish") return "#7c3aed";
    return d.color || "#d97706";
  }

  // ── Load graph data ───────────────────────────────────────────────────────
  async function loadGraph() {
    statsEl.textContent = "Loading graph…";
    try {
      const res  = await fetch("/api/kg/graph");
      const data = await res.json();
      allNodes = data.nodes || [];
      allEdges = data.edges || [];
      const m = data.meta || {};
      statsEl.innerHTML = [
        `<div>Nodes: <strong>${m.node_count}</strong> &nbsp; Edges: <strong>${m.edge_count}</strong></div>`,
        `<div style="margin-top:4px;">`,
        `  <div>Parishes: <strong>${m.parish_count}</strong> &nbsp; Townlands: <strong>${m.townland_count}</strong></div>`,
        `  <div>Total estate records: <strong>${(m.total_records || 0).toLocaleString()}</strong></div>`,
        `  <div>Townlands with census: <strong>${m.census_townland_count}</strong></div>`,
        `  <div>Townlands with clearances: <strong>${m.clearances_townland_count}</strong></div>`,
        `</div>`,
        `<div style="margin-top:6px;font-size:10px;color:#94a3b8;">Click any node to explore. Drag to rearrange.</div>`,
      ].join("");
      nodeCountEl.textContent = `${allNodes.length} nodes · ${allEdges.length} edges`;
      buildSimulation();
    } catch (err) {
      statsEl.textContent = "Failed to load graph: " + err.message;
    }
  }

  // ── D3 force simulation ───────────────────────────────────────────────────
  function buildSimulation() {
    svg.selectAll("*").remove();

    const rect = svgEl.getBoundingClientRect();
    const W = rect.width  || 900;
    const H = rect.height || 600;

    zoomBehavior = d3.zoom().scaleExtent([0.03, 4]).on("zoom", (event) => {
      gRoot.attr("transform", event.transform);
    });
    svg.call(zoomBehavior);

    const defs = svg.append("defs");
    defs.append("marker")
      .attr("id", "arrowhead")
      .attr("viewBox", "0 -4 8 8")
      .attr("refX", 14).attr("refY", 0)
      .attr("markerWidth", 4).attr("markerHeight", 4)
      .attr("orient", "auto")
      .append("path").attr("d", "M0,-4L8,0L0,4").attr("fill", "#475569");

    gRoot = svg.append("g");

    const nodes = allNodes.map((d) => ({ ...d }));
    const nodeById = new Map(nodes.map((n) => [n.id, n]));
    const edges = allEdges
      .map((e) => ({ ...e, source: nodeById.get(e.source), target: nodeById.get(e.target) }))
      .filter((e) => e.source && e.target);

    // Pre-position hub nodes so the layout is semantic
    nodes.forEach((n) => {
      if (n.id === "eh_emigration")  { n.fx = W * 0.12; n.fy = H * 0.18; }
      if (n.id === "eh_eviction")    { n.fx = W * 0.88; n.fy = H * 0.18; }
      if (n.id === "eh_tenancy")     { n.fx = W * 0.50; n.fy = H * 0.88; }
      if (n.id === "dh_census")      { n.fx = W * 0.12; n.fy = H * 0.82; }
      if (n.id === "dh_clearances")  { n.fx = W * 0.88; n.fy = H * 0.82; }
    });

    simulation = d3.forceSimulation(nodes)
      .force("link", d3.forceLink(edges).id((d) => d.id)
        .distance((e) => {
          if (e.type === "parish_townland")    return 55;
          if (e.type === "townland_event")     return 110;
          if (e.type === "townland_census")    return 130;
          if (e.type === "townland_clearances")return 130;
          return 90;
        })
        .strength((e) => {
          if (e.type === "parish_townland") return 0.55;
          if (e.type === "townland_event")  return 0.25;
          return 0.15;
        }))
      .force("charge", d3.forceManyBody().strength((d) => {
        if (d.type === "EventHub") return -900;
        if (d.type === "DataHub")  return -700;
        if (d.type === "Parish")   return -350;
        return -45;
      }))
      .force("center", d3.forceCenter(W / 2, H / 2))
      .force("collide", d3.forceCollide().radius((d) => (d.size || 8) + 4))
      .alphaDecay(0.022);

    // Links — colour by type
    linkSel = gRoot.append("g").attr("class", "links").selectAll("line")
      .data(edges).enter().append("line")
        .attr("stroke", (e) => {
          if (e.type === "parish_townland")    return "#a78bfa";
          if (e.type === "townland_census")    return "#0891b2";
          if (e.type === "townland_clearances")return "#9333ea";
          if (e.type === "townland_event") {
            if (e.target.id === "eh_emigration") return "#16a34a";
            if (e.target.id === "eh_eviction")   return "#dc2626";
            return "#2563eb";
          }
          return "#64748b";
        })
        .attr("stroke-width", (e) => e.type === "parish_townland" ? 0.7 : 0.5)
        .attr("stroke-opacity", 0.35)
        .attr("marker-end", "url(#arrowhead)");

    // Node circles
    nodeSel = gRoot.append("g").attr("class", "nodes").selectAll("circle")
      .data(nodes).enter().append("circle")
        .attr("r", (d) => d.size || 8)
        .attr("fill", nodeColor)
        .attr("stroke", "#0f172a")
        .attr("stroke-width", 1)
        .attr("cursor", "pointer")
        .call(
          d3.drag()
            .on("start", (event, d) => {
              if (!event.active) simulation.alphaTarget(0.3).restart();
              d.fx = d.x; d.fy = d.y;
            })
            .on("drag", (event, d) => { d.fx = event.x; d.fy = event.y; })
            .on("end", (event, d) => {
              if (!event.active) simulation.alphaTarget(0);
              // Keep hubs fixed; release everything else
              const fixed = ["eh_emigration","eh_eviction","eh_tenancy","dh_census","dh_clearances"];
              if (!fixed.includes(d.id)) { d.fx = null; d.fy = null; }
            })
        )
        .on("click", (event, d) => { event.stopPropagation(); showDetail(d); })
        .on("mouseover", (event, d) => showTooltip(event, d))
        .on("mousemove", (event) => moveTooltip(event))
        .on("mouseout", () => hideTooltip());

    // Labels for hubs and parishes only (townlands unlabelled — too many)
    labelSel = gRoot.append("g").attr("class", "labels").selectAll("text")
      .data(nodes.filter((n) => n.type === "EventHub" || n.type === "DataHub" || n.type === "Parish"))
      .enter().append("text")
        .attr("font-size", (d) => d.type === "Parish" ? 8 : 10)
        .attr("fill", (d) => {
          if (d.type === "EventHub") return "#e2e8f0";
          if (d.type === "DataHub")  return "#bae6fd";
          return "#c4b5fd";
        })
        .attr("text-anchor", "middle")
        .attr("pointer-events", "none")
        .text((d) => d.label);

    simulation.on("tick", () => {
      linkSel
        .attr("x1", (e) => e.source.x).attr("y1", (e) => e.source.y)
        .attr("x2", (e) => e.target.x).attr("y2", (e) => e.target.y);
      nodeSel.attr("cx", (d) => d.x).attr("cy", (d) => d.y);
      labelSel
        .attr("x", (d) => d.x)
        .attr("y", (d) => (d.y || 0) - (d.size || 8) - 3);
    });

    svg.on("click", () => { clearSelection(); });
  }

  // ── Detail panel ──────────────────────────────────────────────────────────
  async function showDetail(d) {
    highlightNode(d.id);
    detailCard.style.display = "block";

    if (d.type === "Townland") {
      await showTownlandDetail(d);
    } else if (d.type === "Parish") {
      showParishDetail(d);
    } else if (d.type === "DataHub") {
      showDataHubDetail(d);
    } else {
      showEventHubDetail(d);
    }
  }

  async function showTownlandDetail(d) {
    detailEl.innerHTML = [
      `<div style="font-weight:700;font-size:13px;color:#0f172a;">${escHtml(d.label)}</div>`,
      d.civil_parish ? `<div><span style="color:#475467;">Parish:</span> ${escHtml(d.civil_parish)}</div>` : "",
      d.barony ? `<div><span style="color:#475467;">Barony:</span> ${escHtml(d.barony)}</div>` : "",
      d.county ? `<div><span style="color:#475467;">County:</span> ${escHtml(d.county)}</div>` : "",
      `<div style="margin-top:6px;font-size:11px;">`,
      d.emigrant_count ? `<span style="color:#16a34a;font-weight:600;">${d.emigrant_count} emigrants</span>&nbsp; ` : "",
      d.eviction_count ? `<span style="color:#dc2626;font-weight:600;">${d.eviction_count} evictions</span>&nbsp; ` : "",
      d.tenancy_count  ? `<span style="color:#2563eb;font-weight:600;">${d.tenancy_count} tenants</span>` : "",
      `</div>`,
      `<div style="margin-top:6px;font-size:10px;color:#94a3b8;">Loading persons…</div>`,
    ].join("");

    try {
      const res  = await fetch(`/api/kg/townland/${encodeURIComponent(d.label)}`);
      const data = await res.json();
      const persons = data.persons || [];
      const total   = data.total   || 0;

      const EVENT_COLOR = { emigration: "#dcfce7", eviction: "#fee2e2", tenancy: "#dbeafe" };
      const personRows = persons.map((p) => `
        <div style="padding:3px 0;border-bottom:1px solid #f1f5f9;font-size:11px;">
          <strong>${escHtml(p.name)}</strong>
          ${p.year ? `<span style="color:#64748b;"> (${escHtml(String(p.year))})</span>` : ""}
          <span style="padding:1px 5px;background:${EVENT_COLOR[p.event_type] || "#f1f5f9"};border-radius:4px;margin-left:4px;">${escHtml(p.event_type)}</span>
          ${p.occupation ? `<span style="color:#64748b;"> · ${escHtml(p.occupation)}</span>` : ""}
        </div>`).join("");

      detailEl.innerHTML = [
        `<div style="font-weight:700;font-size:13px;color:#0f172a;">${escHtml(d.label)}</div>`,
        d.civil_parish ? `<div><span style="color:#475467;">Parish:</span> ${escHtml(d.civil_parish)}</div>` : "",
        d.barony ? `<div><span style="color:#475467;">Barony:</span> ${escHtml(d.barony)}</div>` : "",
        d.county ? `<div><span style="color:#475467;">County:</span> ${escHtml(d.county)}</div>` : "",
        `<div style="margin-top:6px;font-size:11px;">`,
        d.emigrant_count ? `<span style="color:#16a34a;font-weight:600;">${d.emigrant_count} emigrants</span>&nbsp; ` : "",
        d.eviction_count ? `<span style="color:#dc2626;font-weight:600;">${d.eviction_count} evictions</span>&nbsp; ` : "",
        d.tenancy_count  ? `<span style="color:#2563eb;font-weight:600;">${d.tenancy_count} tenants</span>` : "",
        `</div>`,
        `<div style="margin-top:6px;font-weight:600;font-size:11px;">Persons (${total} total):</div>`,
        `<div style="margin-top:4px;max-height:240px;overflow-y:auto;">${personRows}</div>`,
        total > 50 ? `<div style="margin-top:4px;font-size:10px;color:#94a3b8;">Showing first 50 of ${total}</div>` : "",
      ].join("");
    } catch (e) {
      const last = detailEl.innerHTML.replace(/<div[^>]*>Loading persons.*?<\/div>/, "");
      detailEl.innerHTML = last + `<div style="color:#dc2626;font-size:10px;">Could not load persons.</div>`;
    }
  }

  function showParishDetail(d) {
    const townlandsInParish = allNodes.filter(
      (n) => n.type === "Townland" && n.civil_parish === d.label
    );
    const totalRecords = townlandsInParish.reduce((s, n) => s + (n.total_records || 0), 0);
    const totalEmigr   = townlandsInParish.reduce((s, n) => s + (n.emigrant_count || 0), 0);
    const totalEvict   = townlandsInParish.reduce((s, n) => s + (n.eviction_count || 0), 0);
    const totalTenan   = townlandsInParish.reduce((s, n) => s + (n.tenancy_count  || 0), 0);

    const tlList = townlandsInParish
      .sort((a, b) => (b.total_records || 0) - (a.total_records || 0))
      .slice(0, 20)
      .map((n) => `<span style="padding:2px 6px;background:#f1f5f9;border-radius:6px;font-size:10px;margin:2px;display:inline-block;">${escHtml(n.label)} (${n.total_records})</span>`)
      .join("");

    detailEl.innerHTML = [
      `<div style="font-weight:700;font-size:13px;color:#0f172a;">Parish: ${escHtml(d.label)}</div>`,
      `<div style="margin-top:4px;font-size:12px;">Townlands: <strong>${townlandsInParish.length}</strong></div>`,
      `<div>Total records: <strong>${totalRecords}</strong></div>`,
      `<div style="font-size:11px;margin-top:4px;">`,
      totalEmigr ? `<span style="color:#16a34a;font-weight:600;">${totalEmigr} emigrants</span>&nbsp; ` : "",
      totalEvict ? `<span style="color:#dc2626;font-weight:600;">${totalEvict} evictions</span>&nbsp; ` : "",
      totalTenan ? `<span style="color:#2563eb;font-weight:600;">${totalTenan} tenants</span>` : "",
      `</div>`,
      `<div style="margin-top:8px;line-height:1.8;">${tlList}</div>`,
    ].join("");
  }

  function showDataHubDetail(d) {
    if (d.id === "dh_census") {
      const connectedTls = allEdges.filter((e) => e.target === "dh_census" || e.target?.id === "dh_census").length;
      detailEl.innerHTML = [
        `<div style="font-weight:700;font-size:13px;color:#0f172a;">Census Records</div>`,
        `<div style="margin-top:4px;font-size:12px;">Census data (1827–1891) from the VRTI Knowledge Graph covering population totals per townland per year.</div>`,
        `<div style="margin-top:6px;color:#0891b2;font-weight:600;">${connectedTls} townlands with census data</div>`,
        `<div style="margin-top:4px;font-size:11px;color:#64748b;">Years: 1827, 1839, 1841, 1848, 1850, 1851, 1860, 1861, 1868, 1871, 1881, 1891</div>`,
      ].join("");
    } else {
      const connectedTls = allEdges.filter((e) => e.target === "dh_clearances" || e.target?.id === "dh_clearances").length;
      detailEl.innerHTML = [
        `<div style="font-weight:700;font-size:13px;color:#0f172a;">Clearances Records</div>`,
        `<div style="margin-top:4px;font-size:12px;">Estate eviction clearances data (1847–1856) tracking households cleared per townland per year.</div>`,
        `<div style="margin-top:6px;color:#9333ea;font-weight:600;">${connectedTls} townlands with clearances data</div>`,
      ].join("");
    }
  }

  function showEventHubDetail(d) {
    const count = allNodes
      .filter((n) => n.type === "Townland")
      .reduce((s, n) => {
        if (d.id === "eh_emigration") return s + (n.emigrant_count || 0);
        if (d.id === "eh_eviction")   return s + (n.eviction_count || 0);
        return s + (n.tenancy_count || 0);
      }, 0);
    const townlandCount = allEdges.filter((e) => {
      const tid = typeof e.target === "object" ? e.target?.id : e.target;
      return tid === d.id && e.type === "townland_event";
    }).length;

    detailEl.innerHTML = [
      `<div style="font-weight:700;font-size:13px;color:#0f172a;">${escHtml(d.label)} Events</div>`,
      `<div style="margin-top:4px;font-size:12px;">Total records: <strong>${count.toLocaleString()}</strong></div>`,
      `<div>Townlands with ${d.label.toLowerCase()} records: <strong>${townlandCount}</strong></div>`,
      `<div style="margin-top:6px;font-size:11px;color:#64748b;">All townlands connected to this hub have at least one ${d.label.toLowerCase()} record.</div>`,
    ].join("");
  }

  function clearSelection() {
    detailCard.style.display = "none";
    detailEl.innerHTML = "";
    if (nodeSel) nodeSel.attr("opacity", 1).attr("stroke-width", 1).attr("stroke", "#0f172a");
    if (linkSel) linkSel.attr("stroke-opacity", 0.35);
  }

  function highlightNode(nodeId) {
    if (!nodeSel) return;
    nodeSel
      .attr("opacity", (d) => d.id === nodeId ? 1 : 0.2)
      .attr("stroke",       (d) => d.id === nodeId ? "#fbbf24" : "#0f172a")
      .attr("stroke-width", (d) => d.id === nodeId ? 2.5 : 1);
    if (linkSel) {
      linkSel.attr("stroke-opacity", (e) => {
        const sid = typeof e.source === "object" ? e.source.id : e.source;
        const tid = typeof e.target === "object" ? e.target.id : e.target;
        return (sid === nodeId || tid === nodeId) ? 0.85 : 0.05;
      });
    }
  }

  // ── Tooltip ───────────────────────────────────────────────────────────────
  function showTooltip(event, d) {
    let html = `<strong>${escHtml(d.label)}</strong><br/><span style="color:#94a3b8;">${escHtml(d.type)}</span>`;
    if (d.type === "Townland") {
      html += `<br/>Records: ${d.total_records || 0}`;
      if (d.emigrant_count) html += ` · <span style="color:#86efac;">${d.emigrant_count} em.</span>`;
      if (d.eviction_count) html += ` · <span style="color:#fca5a5;">${d.eviction_count} ev.</span>`;
      if (d.civil_parish)   html += `<br/>Parish: ${escHtml(d.civil_parish)}`;
    } else if (d.type === "Parish") {
      const count = allNodes.filter((n) => n.type === "Townland" && n.civil_parish === d.label).length;
      html += `<br/>${count} townlands`;
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

  // ── Search ────────────────────────────────────────────────────────────────
  searchEl.addEventListener("input", () => {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(doSearch, 180);
  });

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

  function doSearch() {
    const q = (searchEl.value || "").trim().toLowerCase();
    if (!q || !nodeSel) {
      clearSelection();
      searchCard.style.display = "none";
      return;
    }

    const matchIds = new Set(
      allNodes
        .filter((n) =>
          (n.label         || "").toLowerCase().includes(q) ||
          (n.civil_parish  || "").toLowerCase().includes(q) ||
          (n.barony        || "").toLowerCase().includes(q) ||
          (n.county        || "").toLowerCase().includes(q) ||
          (n.dominant_event|| "").toLowerCase().includes(q) ||
          (n.type          || "").toLowerCase().includes(q)
        )
        .map((n) => n.id)
    );

    nodeSel
      .attr("opacity",      (d) => matchIds.has(d.id) ? 1 : 0.06)
      .attr("r",            (d) => matchIds.has(d.id) ? Math.min((d.size || 8) + 3, 24) : (d.size || 8))
      .attr("stroke",       (d) => matchIds.has(d.id) ? "#fbbf24" : "#0f172a")
      .attr("stroke-width", (d) => matchIds.has(d.id) ? 2 : 1);

    if (linkSel) linkSel.attr("stroke-opacity", (e) => {
      const sid = typeof e.source === "object" ? e.source.id : e.source;
      const tid = typeof e.target === "object" ? e.target.id : e.target;
      return (matchIds.has(sid) || matchIds.has(tid)) ? 0.55 : 0.03;
    });

    const matched   = allNodes.filter((n) => matchIds.has(n.id));
    const parishes  = matched.filter((n) => n.type === "Parish").length;
    const townlands = matched.filter((n) => n.type === "Townland").length;
    const hubs      = matched.filter((n) => n.type === "EventHub" || n.type === "DataHub").length;

    searchCard.style.display = "block";
    searchResultsEl.innerHTML = matched.length
      ? [
          `<div><strong>${matched.length}</strong> entities match "<em>${escHtml(q)}</em>"</div>`,
          `<div>Parishes: <strong>${parishes}</strong> · Townlands: <strong>${townlands}</strong> · Hubs: <strong>${hubs}</strong></div>`,
          matched.length <= 10
            ? `<div style="margin-top:4px;">${matched.map((n) =>
                `<span style="padding:2px 6px;background:#f1f5f9;border-radius:6px;font-size:10px;margin:2px;">${escHtml(n.label)}</span>`
              ).join("")}</div>`
            : "",
        ].join("")
      : `<div style="color:#64748b;">No entities match "<em>${escHtml(q)}</em>"</div>`;

    nodeCountEl.textContent = `${matched.length} of ${allNodes.length} nodes highlighted`;
  }

  // ── Boot ──────────────────────────────────────────────────────────────────
  loadGraph();
});
