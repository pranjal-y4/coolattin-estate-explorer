document.addEventListener("DOMContentLoaded", () => {
  const $ = (id) => document.getElementById(id);

  const questionEl = $("askQuestion");
  const hintEl = $("askTownlandHint");
  const hintSuggestEl = $("askTownlandSuggest");
  const submitEl = $("askSubmit");
  const statusEl = $("askStatus");
  const progressEl = $("askProgress");
  const errorEl = $("askError");
  const resultEl = $("askResult");
  const answerEl = $("askAnswer"); // legacy id
  const actualAnswerEl = $("askActualAnswer") || answerEl;
  const llmAnswerEl = $("askLlmAnswer");
  const llmMetaEl = $("askLlmMeta");
  const provenanceEl = $("askProvenance");
  const retrievalLaneEl = $("askRetrievalLane");
  const townlandResolutionEl = $("askTownlandResolution");
  const warningsEl = $("askWarnings");
  const suggestionsBlockEl = $("askSuggestionsBlock");
  const suggestionsEl = $("askSuggestions");
  const insightsBlockEl = $("askInsightsBlock");
  const insightsEl = $("askInsights");
  const feedbackNoteEl = $("askFeedbackNote");
  const feedbackUpEl = $("askFeedbackUp");
  const feedbackDownEl = $("askFeedbackDown");
  const feedbackStatusEl = $("askFeedbackStatus");
  const pdfLinkEl = $("askPdfLink");
  const kgBlockEl = $("askKgBlock");
  const kgContentEl = $("askKgContent");
  const localTableEl = $("askTable");
  const vrtiTableEl = $("askVrtiTable");
  const sqliteQueryEl = $("askSqliteQuery");
  const vrtiPgQueryEl = $("askVrtiPgQuery");
  const summaryEl = $("askSummary");
  const chartBlockEl = $("askChartBlock");
  const chartEl = $("askChart");
  const supportContextBlockEl = $("askSupportContextBlock");
  const supportContextEl = $("askSupportContext");
  const llmStatusEl = $("llmStatus") || $("ollamaStatus");

  const progressOrder = [
    { key: "classifying_intent", label: "Routing Question" },
    { key: "contacting_llm",    label: "Building Query" },
    { key: "slot_filling",      label: "Slot Filling" },
    { key: "framing_query",     label: "Framing Query" },
    { key: "querying_database", label: "Querying SQLite" },
    { key: "querying_subgraph", label: "Subgraph Retrieval" },
    { key: "querying_vrti_graph", label: "Querying VRTI Graph" },
    { key: "querying_graphdb",  label: "Querying GraphDB" },
    { key: "querying_fusion",   label: "Reconciling Sources" },
    { key: "preparing_output",  label: "Preparing Output" },
  ];

  const progressMap = new Map();
  let latestLlmStatus = null;
  let townlandSuggestTimer = null;
  let latestResultPayload = null;

  function setStatus(msg) {
    if (statusEl) statusEl.textContent = msg || "";
  }

  function showError(msg) {
    if (!errorEl) return;
    if (!msg) {
      errorEl.style.display = "none";
      errorEl.textContent = "";
      return;
    }
    errorEl.style.display = "block";
    errorEl.textContent = msg;
  }

  function escapeHtml(str) {
    return String(str ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function renderWarnings(list) {
    if (!warningsEl) return;
    const warnings = Array.isArray(list) ? list.filter(Boolean) : [];
    if (!warnings.length) {
      warningsEl.innerHTML = "";
      return;
    }
    warningsEl.innerHTML = warnings
      .map(
        (w) =>
          `<div style="margin-top:6px;padding:8px 10px;border-radius:8px;background:#fff7ed;border:1px solid #fed7aa;color:#9a3412;font-size:12px;">${escapeHtml(w)}</div>`
      )
      .join("");
  }

  function renderSuggestions(payload) {
    if (!suggestionsBlockEl || !suggestionsEl) return;
    const availability = payload?.availability || payload?.structured_output?.availability || {};
    const suggestions = Array.isArray(payload?.suggestions)
      ? payload.suggestions
      : Array.isArray(availability?.suggestions)
      ? availability.suggestions
      : [];
    if (availability?.available || !suggestions.length) {
      suggestionsBlockEl.style.display = "none";
      suggestionsEl.innerHTML = "";
      return;
    }
    suggestionsBlockEl.style.display = "block";
    suggestionsEl.innerHTML = suggestions
      .map(
        (item) =>
          `<div style="padding:9px 11px;border:1px solid #dbeafe;background:#eff6ff;border-radius:10px;color:#1d4ed8;font-size:12px;line-height:1.45;">${escapeHtml(item)}</div>`
      )
      .join("");
  }

  function renderInsights(payload) {
    if (!insightsBlockEl || !insightsEl) return;
    const insights = Array.isArray(payload?.related_insights)
      ? payload.related_insights
      : Array.isArray(payload?.structured_output?.related_insights)
      ? payload.structured_output.related_insights
      : [];
    if (!insights.length) {
      insightsBlockEl.style.display = "none";
      insightsEl.innerHTML = "";
      return;
    }
    insightsBlockEl.style.display = "block";
    insightsEl.innerHTML = insights
      .map(
        (item) => `
          <div style="padding:9px 11px;border:1px solid #e2e8f0;background:#fff;border-radius:10px;font-size:12px;line-height:1.45;">
            <div style="font-weight:800;color:#334155;">${escapeHtml(item?.label || "Insight")}</div>
            <div style="margin-top:3px;color:#475467;">${escapeHtml(item?.value || "")}</div>
          </div>`
      )
      .join("");
  }

  const _laneLabels = {
    analytical_rule: "ANALYTICAL · semantic rule",
    analytical_llm:  "ANALYTICAL · LLM slot-fill",
    relational:      "RELATIONAL · subgraph",
    comparative:     "COMPARATIVE · SQL + subgraph",
    fallback_verified_analysis: "FALLBACK · verified analysis",
    fallback_p4:     "FALLBACK · phase-4 template",
    fallback_memory: "FALLBACK · approved memory",
    fallback_llm:    "FALLBACK · LLM SQL",
    fallback:        "FALLBACK",
  };
  const _routeColors = {
    analytical:  { bg: "#f0fdf4", border: "#86efac", color: "#166534" },
    relational:  { bg: "#eff6ff", border: "#93c5fd", color: "#1e40af" },
    comparative: { bg: "#faf5ff", border: "#d8b4fe", color: "#6b21a8" },
    fallback:    { bg: "#f8fafc", border: "#cbd5e1", color: "#475467" },
  };

  function renderProvenance(payload) {
    if (!provenanceEl) return;
    const provenance = payload?.query_provenance || payload?.structured_output?.query_provenance || {};
    const matches = Array.isArray(provenance?.approved_query_candidates) ? provenance.approved_query_candidates : [];

    // ── New-pipeline lane badge (only when route/lane present) ──────────────
    if (retrievalLaneEl) {
      const route = provenance?.route;
      const lane  = provenance?.lane;
      if (route || lane) {
        const laneKey  = lane || route;
        const laneText = _laneLabels[laneKey] || laneKey;
        const palette  = _routeColors[route] || _routeColors.fallback;
        const subNodes = provenance?.subgraph_node_count != null
          ? `<span style="margin-left:6px;font-size:10px;color:${palette.color};opacity:.8;">${provenance.subgraph_node_count} subgraph triples</span>`
          : "";
        const phase6   = provenance?.phase6_todo
          ? `<span style="margin-left:6px;font-size:10px;color:#713f12;">reconciliation pending</span>`
          : "";
        retrievalLaneEl.innerHTML = `
          <span style="display:inline-flex;align-items:center;gap:4px;padding:3px 10px;border-radius:999px;
            background:${palette.bg};border:1px solid ${palette.border};color:${palette.color};
            font-size:11px;font-weight:700;">
            ${escapeHtml(laneText)}${subNodes}${phase6}
          </span>`;
      } else {
        retrievalLaneEl.innerHTML = "";
      }
    }

    const rows = [
      `SQL source: ${provenance?.direct_memory_reuse ? "approved query memory" : payload?.llm?.mode || "generated"}.`,
      `Execution mode: ${provenance?.execution_mode || "executed_as_generated"}.`,
    ];

    // New-pipeline fields (only present when ASK_USE_NEW_PIPELINE=true)
    if (provenance?.route) {
      rows.unshift(`Retrieval route: ${provenance.route}.`);
    }
    if (provenance?.lane) {
      rows.splice(provenance?.route ? 1 : 0, 0, `Dispatch lane: ${provenance.lane}.`);
    }
    if (provenance?.subgraph_node_count != null) {
      const sources = Array.isArray(provenance.subgraph_sources)
        ? provenance.subgraph_sources.join(", ")
        : "—";
      rows.push(`Subgraph: ${provenance.subgraph_node_count} triples from [${sources}].`);
    }
    if (provenance?.phase6_todo) {
      rows.push(`Phase 6: ${provenance.phase6_todo}`);
    }

    if (provenance?.reused_memory_id) {
      rows.push(`Approved memory id reused: ${provenance.reused_memory_id}.`);
    }
    if (matches.length) {
      const preview = matches
        .slice(0, 3)
        .map((item) => `${item.question_text} (score ${item.match_score})`)
        .join(" | ");
      rows.push(`Closest approved query patterns: ${preview}.`);
    } else {
      rows.push("No approved past query was close enough to reuse yet.");
    }
    provenanceEl.innerHTML = rows
      .map((line) => `<div style="padding:7px 9px;border:1px solid #e2e8f0;background:#f8fafc;border-radius:8px;">${escapeHtml(line)}</div>`)
      .join("");
  }

  function setFeedbackStatus(message, tone = "muted") {
    if (!feedbackStatusEl) return;
    const colors =
      tone === "success"
        ? { color: "#166534" }
        : tone === "error"
        ? { color: "#b42318" }
        : { color: "#475467" };
    feedbackStatusEl.style.color = colors.color;
    feedbackStatusEl.textContent = message || "";
  }

  function suggestionButton(suggestion, compact = false) {
    const parish = suggestion?.civil_parish ? ` · ${suggestion.civil_parish}` : "";
    return `<button type="button" class="btn btn-soft ask-townland-suggestion"
      data-townland="${escapeHtml(suggestion?.name || "")}"
      style="font-size:11px;padding:${compact ? "5px 8px" : "7px 10px"};border-color:#bae6fd;background:#f0f9ff;color:#075985;">
      ${escapeHtml(suggestion?.name || "")}${escapeHtml(parish)}
    </button>`;
  }

  function bindSuggestionButtons(root = document) {
    root.querySelectorAll(".ask-townland-suggestion").forEach((btn) => {
      if (btn.dataset.bound === "1") return;
      btn.dataset.bound = "1";
      btn.addEventListener("click", () => {
        const name = btn.getAttribute("data-townland") || "";
        if (hintEl) hintEl.value = name;
        if (hintSuggestEl) {
          hintSuggestEl.style.display = "none";
          hintSuggestEl.innerHTML = "";
        }
        if (hintEl) hintEl.focus();
      });
    });
  }

  function renderHintSuggestions(suggestions) {
    if (!hintSuggestEl) return;
    const rows = Array.isArray(suggestions) ? suggestions.filter((s) => s?.name) : [];
    if (!rows.length) {
      hintSuggestEl.style.display = "none";
      hintSuggestEl.innerHTML = "";
      return;
    }
    hintSuggestEl.style.display = "flex";
    hintSuggestEl.innerHTML = rows.slice(0, 6).map((s) => suggestionButton(s, true)).join("");
    bindSuggestionButtons(hintSuggestEl);
  }

  function renderTownlandResolution(resolution) {
    if (!townlandResolutionEl) return;
    if (!resolution || (!resolution.warning && !resolution.matched && !(resolution.suggestions || []).length)) {
      townlandResolutionEl.style.display = "none";
      townlandResolutionEl.innerHTML = "";
      return;
    }

    const suggestions = Array.isArray(resolution.suggestions) ? resolution.suggestions : [];
    const matchText = resolution.matched
      ? `Using townland: ${resolution.name}${resolution.civil_parish ? ` · ${resolution.civil_parish}` : ""}`
      : "Townland was not matched exactly.";
    const suggestionHtml = suggestions.length
      ? `<div style="margin-top:7px;display:flex;gap:6px;flex-wrap:wrap;">${suggestions
          .slice(0, 5)
          .map((s) => suggestionButton(s))
          .join("")}</div>`
      : "";

    // Fuzzy-match confidence badge
    let confidenceBadge = "";
    if (resolution.confidence != null && resolution.match_type && resolution.match_type !== "exact") {
      const pct = Math.round(resolution.confidence * 100);
      const badgeColor = pct >= 90 ? "#166534" : pct >= 70 ? "#854d0e" : "#9f1239";
      const badgeBg    = pct >= 90 ? "#dcfce7"  : pct >= 70 ? "#fef9c3"  : "#fff1f2";
      confidenceBadge = `<span style="margin-left:6px;padding:2px 7px;border-radius:12px;font-size:10px;font-weight:700;background:${badgeBg};color:${badgeColor};">${pct}% match</span>`;
    }

    townlandResolutionEl.style.display = "block";
    townlandResolutionEl.innerHTML = `
      <div style="padding:10px 12px;border:1px solid #bae6fd;background:#f0f9ff;border-radius:10px;color:#075985;font-size:12px;line-height:1.5;">
        <div style="display:flex;align-items:center;flex-wrap:wrap;gap:4px;">
          <strong>${escapeHtml(matchText)}</strong>${confidenceBadge}
        </div>
        ${resolution.warning ? `<div style="margin-top:4px;">${escapeHtml(resolution.warning)}</div>` : ""}
        ${suggestionHtml}
      </div>`;
    bindSuggestionButtons(townlandResolutionEl);
  }

  function renderSupportingContext(payload) {
    if (!supportContextBlockEl || !supportContextEl) return;
    const items = payload?.structured_output?.supporting_context || [];
    if (!Array.isArray(items) || !items.length) {
      supportContextBlockEl.style.display = "none";
      supportContextEl.innerHTML = "";
      return;
    }
    supportContextBlockEl.style.display = "block";
    supportContextEl.innerHTML = items
      .map(
        (item) => `
          <div style="padding:9px 11px;border:1px solid #e2e8f0;background:#fff;border-radius:10px;font-size:12px;line-height:1.45;">
            <div style="font-weight:800;color:#334155;">${escapeHtml(item.label || "Context")}</div>
            <div style="margin-top:3px;color:#475467;">${escapeHtml(item.value || "")}</div>
          </div>`
      )
      .join("");
  }

  function renderTable(tableEl, columns, rows, emptyText) {
    if (!tableEl) return;
    const cols = Array.isArray(columns) ? columns : [];
    const rs = Array.isArray(rows) ? rows : [];

    if (!cols.length || !rs.length) {
      tableEl.innerHTML = `
        <tbody>
          <tr><td style="padding:10px 12px;color:#64748b;">${escapeHtml(emptyText || "No rows returned.")}</td></tr>
        </tbody>`;
      return;
    }

    const header = `<thead><tr>${cols
      .map(
        (c) =>
          `<th style="position:sticky;top:0;background:#f8fafc;text-align:left;padding:8px 10px;border-bottom:1px solid #e2e8f0;font-size:12px;color:#475467;">${escapeHtml(c)}</th>`
      )
      .join("")}</tr></thead>`;

    function renderCell(val) {
      const raw = String(val ?? "");
      if (raw.length > 200 && raw.includes(",")) {
        const parts = raw.split(",").map((s) => s.trim()).filter(Boolean);
        const preview = parts.slice(0, 5).map(escapeHtml).join(", ");
        const rest = parts.length - 5;
        const all = parts.map(escapeHtml).join("<br>");
        return `<td style="padding:8px 10px;border-bottom:1px solid #f1f5f9;font-size:12px;color:#0f172a;vertical-align:top;">
          <span>${preview}</span>${rest > 0 ? `<span style="color:#94a3b8;"> …and ${rest} more</span>` : ""}
          ${rest > 0 ? `<details style="margin-top:4px;"><summary style="font-size:11px;cursor:pointer;color:#1d4ed8;">Show all ${parts.length}</summary><div style="max-height:200px;overflow-y:auto;font-size:11px;line-height:1.6;margin-top:4px;">${all}</div></details>` : ""}
        </td>`;
      }
      return `<td style="padding:8px 10px;border-bottom:1px solid #f1f5f9;font-size:12px;color:#0f172a;vertical-align:top;">${escapeHtml(raw)}</td>`;
    }

    const body = `<tbody>${rs
      .map((row) => {
        const cells = cols.map((c) => renderCell(row[c])).join("");
        return `<tr>${cells}</tr>`;
      })
      .join("")}</tbody>`;

    tableEl.innerHTML = header + body;
  }

  function renderKg(kg) {
    if (!kgBlockEl || !kgContentEl) return;
    if (!kg || !Array.isArray(kg.townlands) || !kg.townlands.length) {
      kgBlockEl.style.display = "none";
      kgContentEl.innerHTML = "";
      return;
    }

    const blocks = [];
    if (Array.isArray(kg.townlands)) {
      kg.townlands.forEach((t) => {
        blocks.push(
          `<div style="padding:10px 12px;border:1px solid #e2e8f0;background:#fff;border-radius:10px;">
            <div style="font-weight:700;color:#0f172a;">${escapeHtml(t.name)}</div>
            <div style="font-size:12px;color:#475467;margin-top:3px;">
              Parish: ${escapeHtml(t.civil_parish || "-")} | Barony: ${escapeHtml(t.barony || "-")} | County: ${escapeHtml(t.county || "-")}
            </div>
            ${
              t.kg_uri
                ? `<a href="${escapeHtml(t.kg_uri)}" target="_blank" rel="noopener" style="display:inline-block;margin-top:6px;font-size:12px;color:#0f766e;">Open KG URI</a>`
                : ""
            }
          </div>`
        );
      });
    }

    kgBlockEl.style.display = "block";
    kgContentEl.innerHTML = blocks.join("");
  }

  function renderProgress() {
    if (!progressEl) return;
    progressEl.innerHTML = progressOrder
      .map((stage) => {
        const s = progressMap.get(stage.key);
        const status = s?.status || "pending";
        if (status === "pending") return "";   // hide until touched
        const icon =
          status === "completed" ? "✓" :
          status === "in_progress" ? '<span style="animation:spin 1s linear infinite;display:inline-block">⟳</span>' : "○";
        const bg     = status === "completed" ? "#ecfdf3" : "#eff6ff";
        const border = status === "completed" ? "#86efac" : "#93c5fd";
        const timeChip = s?.duration_ms
          ? `<span style="margin-left:6px;padding:1px 6px;border-radius:4px;background:#f1f5f9;color:#64748b;font-size:10px;">${s.duration_ms} ms</span>`
          : "";
        const detail = s?.detail
          ? `<div style="margin-top:2px;font-size:11px;color:#64748b;">${escapeHtml(s.detail)}</div>`
          : "";
        return `<div style="padding:7px 10px;border:1px solid ${border};background:${bg};border-radius:8px;font-size:12px;color:#334155;">
          <div style="display:flex;align-items:center;gap:4px;">${icon} <strong>${escapeHtml(stage.label)}</strong>${timeChip}</div>
          ${detail}
        </div>`;
      })
      .join("");
  }

  function setStage(evt) {
    if (!evt || !evt.stage) return;
    const existing = progressMap.get(evt.stage) || {};
    const isCompleted = evt.status !== "started";
    progressMap.set(evt.stage, {
      status: isCompleted ? "completed" : "in_progress",
      // Keep the completed duration; update detail on every event
      duration_ms: isCompleted ? (evt.duration_ms || existing.duration_ms || null) : null,
      detail: evt.detail || existing.detail || "",
    });
    renderProgress();
    if (evt.detail) setStatus(evt.detail);
  }

  function renderStructured(payload) {
    const structured = payload?.structured_output || {};
    const queries = structured?.queries || {};
    const processed = structured?.processed_tables || {};
    const summary = structured?.summary || {};
    const chart = payload?.chart || structured?.chart || null;

    if (sqliteQueryEl) sqliteQueryEl.textContent = queries.local_sqlite_query || payload.sql || "";
    if (vrtiPgQueryEl) vrtiPgQueryEl.textContent = queries.vrti_postgresql_query || "";

    const local = processed.local_database || {};
    renderTable(
      localTableEl,
      local.columns || payload.columns || [],
      local.rows || payload.rows || [],
      "No local database rows returned."
    );

    const vrti = processed.vrti_graph || {};
    renderTable(vrtiTableEl, vrti.columns || [], vrti.rows || [], "No VRTI graph rows returned.");

    if (summaryEl) {
      summaryEl.textContent = summary.final_summary_text || "Summary unavailable.";
    }
    renderChart(chart);
  }

  function renderChart(chart) {
    if (!chartBlockEl || !chartEl) return;
    const labels = Array.isArray(chart?.labels) ? chart.labels.map((v) => String(v ?? "")) : [];
    const values = Array.isArray(chart?.values) ? chart.values.map((v) => Number(v ?? 0)) : [];
    if (!chart || !labels.length || labels.length !== values.length) {
      chartBlockEl.style.display = "none";
      chartEl.innerHTML = "";
      return;
    }

    const safeValues = values.map((value) => (Number.isFinite(value) ? value : 0));
    const maxValue = Math.max(...safeValues, 1);

    if (chart.type === "line") {
      const width = 720;
      const height = 260;
      const padding = 36;
      const points = safeValues
        .map((value, idx) => {
          const x =
            labels.length === 1
              ? width / 2
              : padding + (idx * (width - padding * 2)) / (labels.length - 1);
          const y = height - padding - (value / maxValue) * (height - padding * 2);
          return { x, y, value, label: labels[idx] };
        });
      const path = points.map((point, idx) => `${idx === 0 ? "M" : "L"} ${point.x} ${point.y}`).join(" ");
      chartBlockEl.style.display = "block";
      chartEl.innerHTML = `
        <div style="font-size:12px;font-weight:700;color:#0f172a;margin-bottom:8px;">${escapeHtml(chart.title || "Chart")}</div>
        <svg viewBox="0 0 ${width} ${height}" style="width:100%;min-width:620px;height:auto;">
          <line x1="${padding}" y1="${height - padding}" x2="${width - padding}" y2="${height - padding}" stroke="#cbd5e1" stroke-width="1.5"></line>
          <line x1="${padding}" y1="${padding}" x2="${padding}" y2="${height - padding}" stroke="#cbd5e1" stroke-width="1.5"></line>
          <path d="${path}" fill="none" stroke="#0f766e" stroke-width="3"></path>
          ${points
            .map(
              (point) => `
                <circle cx="${point.x}" cy="${point.y}" r="4" fill="#0f766e"></circle>
                <text x="${point.x}" y="${point.y - 10}" text-anchor="middle" font-size="11" fill="#0f172a">${escapeHtml(
                  String(point.value)
                )}</text>
                <text x="${point.x}" y="${height - 12}" text-anchor="middle" font-size="10" fill="#475467">${escapeHtml(
                  point.label
                )}</text>
              `
            )
            .join("")}
        </svg>
      `;
      return;
    }

    const bars = labels.map((label, idx) => {
      const value = safeValues[idx];
      const widthPct = Math.max(2, (value / maxValue) * 100);
      return `
        <div style="display:grid;grid-template-columns:minmax(120px, 220px) 1fr auto;gap:10px;align-items:center;">
          <div style="font-size:12px;color:#334155;font-weight:600;">${escapeHtml(label)}</div>
          <div style="height:18px;background:#e2e8f0;border-radius:999px;overflow:hidden;">
            <div style="height:100%;width:${widthPct}%;background:linear-gradient(90deg,#0f766e,#22c55e);border-radius:999px;"></div>
          </div>
          <div style="font-size:12px;color:#0f172a;font-weight:700;">${escapeHtml(String(value))}</div>
        </div>
      `;
    });
    chartBlockEl.style.display = "block";
    chartEl.innerHTML = `
      <div style="font-size:12px;font-weight:700;color:#0f172a;margin-bottom:8px;">${escapeHtml(chart.title || "Chart")}</div>
      <div style="display:grid;gap:8px;">${bars.join("")}</div>
    `;
  }

  function renderLlmMeta(payload) {
    if (!llmMetaEl) return;
    const sqlLlm = payload?.llm || {};
    const rewrite = payload?.llm_rewrite || {};
    const status = latestLlmStatus || {};
    const rewriteGenerated = Boolean(payload?.llm_rephrased_answer && rewrite.mode === "llm_rewrite");
    const connected = Boolean(status.available || rewriteGenerated);
    const provider = status.provider || rewrite.provider || sqlLlm.provider || "llm";
    const model = status.active_model || rewrite.model || sqlLlm.model || "-";
    const rows = [
      `Connection: ${connected ? "Connected to LLM" : "LLM not connected"}.`,
      `Provider: ${provider}.`,
      `Model: ${model}.`,
      `LLM rewrite: ${rewriteGenerated ? "generated" : "not generated"} (${rewrite.mode || "unknown"}).`,
      `Data path: read-only SQLite query returned ${payload?.row_count ?? 0} local row(s); VRTI context returned ${
        payload?.structured_output?.processed_tables?.vrti_graph?.row_count ?? 0
      } row(s).`,
      `SQL framing: ${sqlLlm.provider || "unknown"} / ${sqlLlm.model || "-"} / ${sqlLlm.mode || "-"}.`,
    ];
    if (payload?.townland_context) rows.push(`Townland context: ${payload.townland_context}.`);
    if (payload?.townland_resolution?.matched) {
      rows.push(
        `Townland resolver: ${payload.townland_resolution.name} / ${payload.townland_resolution.match_type} / confidence ${payload.townland_resolution.confidence}.`
      );
    }
    if (payload?.availability?.state) rows.push(`Availability: ${payload.availability.state}.`);
    if (status.detail) rows.push(`Technical connection detail: ${status.detail}.`);
    if (rewrite.error) rows.push(`LLM detail: ${rewrite.error}.`);
    llmMetaEl.innerHTML = rows
      .map((line) => `<div style="padding:7px 9px;border:1px solid #e2e8f0;background:#f8fafc;border-radius:8px;">${escapeHtml(line)}</div>`)
      .join("");
  }

  function renderLlmStatus(payload) {
    if (!llmStatusEl) return;
    const ok = Boolean(payload?.available);
    const provider = payload?.provider || "llm";
    const model = payload?.active_model || payload?.model || "-";
    const bg = ok ? "#ecfdf3" : "#fff7ed";
    const border = ok ? "#86efac" : "#fed7aa";
    const color = ok ? "#166534" : "#9a3412";
    const label = ok ? "Connected to LLM" : "LLM not connected";
    const hint = payload?.hint ? ` ${payload.hint}` : "";
    const detail = payload?.detail ? `Technical detail: ${payload.detail}` : "";
    llmStatusEl.innerHTML = `
      <div style="display:inline-flex;align-items:center;gap:8px;padding:7px 10px;border-radius:999px;background:${bg};border:1px solid ${border};color:${color};font-size:12px;font-weight:700;">
        <span>${escapeHtml(label)}</span>
        <span style="font-weight:600;">Provider: ${escapeHtml(provider)} | Model: ${escapeHtml(model)}</span>
      </div>
      ${hint ? `<div style="margin-top:5px;font-size:12px;color:#64748b;">${escapeHtml(hint)}</div>` : ""}
      ${detail ? `<div style="margin-top:4px;font-size:11px;color:#94a3b8;">${escapeHtml(detail)}</div>` : ""}
    `;
  }

  async function consumeSSEPost(url, body, onEvent) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    if (!res.ok) {
      const t = await res.text();
      throw new Error(t || `Request failed (${res.status})`);
    }
    if (!res.body) {
      throw new Error("Streaming response body not available.");
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() || "";

      for (const part of parts) {
        const lines = part
          .split("\n")
          .map((l) => l.trim())
          .filter((l) => l.startsWith("data:"));
        if (!lines.length) continue;
        const dataText = lines.map((l) => l.slice(5).trim()).join("\n");
        if (!dataText) continue;
        try {
          onEvent(JSON.parse(dataText));
        } catch {
          // ignore malformed chunks
        }
      }
    }
  }

  async function checkLlmStatus() {
    try {
      const res = await fetch("/api/ask/llm-status");
      const payload = await res.json();
      latestLlmStatus = payload;
      renderLlmStatus(payload);
    } catch {
      latestLlmStatus = {
        available: false,
        provider: "llm",
        active_model: "-",
        hint: "Could not check LLM provider status.",
      };
      renderLlmStatus(latestLlmStatus);
    }
  }

  async function runQuery() {
    const question = (questionEl?.value || "").trim();
    const townlandHint = (hintEl?.value || "").trim();

    if (!question) {
      showError("Please enter a question.");
      return;
    }

    showError("");
    renderWarnings([]);
    progressMap.clear();
    renderProgress();
    setStatus("Starting data-first LLM workflow...");
    if (resultEl) resultEl.style.display = "none";
    if (submitEl) submitEl.disabled = true;
    latestResultPayload = null;
    setFeedbackStatus("", "muted");
    if (provenanceEl) provenanceEl.innerHTML = "";
    if (retrievalLaneEl) retrievalLaneEl.innerHTML = "";
    if (suggestionsBlockEl) suggestionsBlockEl.style.display = "none";
    if (suggestionsEl) suggestionsEl.innerHTML = "";
    if (insightsBlockEl) insightsBlockEl.style.display = "none";
    if (insightsEl) insightsEl.innerHTML = "";
    if (chartBlockEl) chartBlockEl.style.display = "none";
    if (chartEl) chartEl.innerHTML = "";

    let finalPayload = null;

    try {
      await consumeSSEPost(
        "/api/ask/query",
        { question, townland_hint: townlandHint || null },
        (evt) => {
          if (!evt || !evt.type) return;
          if (evt.type === "progress") {
            setStage(evt);
            return;
          }
          if (evt.type === "error") {
            throw new Error(evt.message || "Unknown stream error.");
          }
          if (evt.type === "result") {
            finalPayload = evt;
          }
        }
      );

      if (!finalPayload) {
        throw new Error("No final result received.");
      }

      const payload = finalPayload;
      // Table is primary. LLM summary is secondary (plain-English rewrite below the table).
      if (llmAnswerEl) {
        const rewrite = payload.llm_rephrased_answer || "";
        llmAnswerEl.textContent = rewrite || "No LLM summary available — see database result above.";
        llmAnswerEl.style.display = "";
      }
      if (actualAnswerEl) {
        const raw = payload.actual_answer || payload.answer || "";
        actualAnswerEl.textContent = raw ? `Raw answer: ${raw}` : "";
        actualAnswerEl.style.display = raw ? "" : "none";
      }
      renderWarnings(payload.warnings || []);
      renderKg(payload.kg_context || null);
      renderStructured(payload);
      renderLlmMeta(payload);
      renderProvenance(payload);
      renderTownlandResolution(payload.townland_resolution || null);
      renderSupportingContext(payload);
      renderSuggestions(payload);
      renderInsights(payload);
      const srcTablesEl = document.getElementById("askSourceTables");
      if (srcTablesEl) {
        if (payload.source_tables && payload.source_tables.length) {
          srcTablesEl.textContent = "Source tables: " + payload.source_tables.join(", ");
          srcTablesEl.style.display = "";
        } else {
          srcTablesEl.style.display = "none";
        }
      }

      // SQL vs Graph comparison panel
      const gc = payload.graph_comparison;
      const gcBlock = document.getElementById("askGraphComparisonBlock");
      if (gcBlock && gc) {
        gcBlock.style.display = "block";

        // Status chip in header
        const gcStatus = document.getElementById("askGraphStatus");
        if (gcStatus) {
          let statusText, statusColor;
          if (!gc.graphdb_available) {
            statusText = gc.error ? `GraphDB error: ${gc.error}` : "GraphDB offline";
            statusColor = "#9a3412";
          } else if (!gc.data_loaded) {
            statusText = "GraphDB connected · repository empty";
            statusColor = "#92400e";
          } else {
            const tcLabel = gc.triple_count >= 0 ? ` · ${gc.triple_count.toLocaleString()} triples` : "";
            statusText = `GraphDB connected${tcLabel} · ${gc.row_count} row(s) returned`;
            statusColor = "#166534";
          }
          gcStatus.textContent = statusText;
          gcStatus.style.color = statusColor;
        }

        // Setup hint (empty repo)
        const gcHint = document.getElementById("askGraphSetupHint");
        if (gcHint) {
          if (gc.setup_hint) {
            gcHint.textContent = gc.setup_hint;
            gcHint.style.display = "block";
          } else {
            gcHint.style.display = "none";
          }
        }

        // Metrics bar chips
        const t = gc.timing || {};
        const sqlRows = (payload.rows || []).length;
        const gcMetrics = document.getElementById("askGraphMetrics");
        if (gcMetrics) {
          const chip = (label, value, accent, sub) =>
            `<div style="display:flex;flex-direction:column;align-items:center;padding:6px 12px;border:1px solid #e2e8f0;border-radius:8px;background:#fff;min-width:90px;text-align:center;">
               <span style="font-size:10px;color:#64748b;font-weight:600;text-transform:uppercase;letter-spacing:.04em;">${label}</span>
               <span style="font-size:15px;font-weight:700;color:${accent};margin-top:2px;">${value}</span>
               ${sub ? `<span style="font-size:10px;color:#94a3b8;margin-top:1px;">${sub}</span>` : ""}
             </div>`;

          const gdbRowLabel = !gc.graphdb_available ? "offline" : !gc.data_loaded ? "0 (empty)" : gc.row_count;
          const gdbRowColor = gc.data_loaded ? "#1e40af" : "#9a3412";
          const tcLabel = gc.triple_count >= 0 ? `${gc.triple_count.toLocaleString()} triples` : "—";
          const speedRatio = (t.sql_ms > 0 && t.graphdb_ms > 0)
            ? `${(t.graphdb_ms / t.sql_ms).toFixed(1)}× slower`
            : null;

          gcMetrics.innerHTML = [
            chip("SQLite rows", sqlRows, "#166534", `${t.sql_ms ?? "—"} ms`),
            chip("GraphDB rows", gdbRowLabel, gdbRowColor, gc.graphdb_available ? `${t.graphdb_ms ?? "—"} ms` : null),
            chip("SQL time", t.sql_ms != null ? `${t.sql_ms} ms` : "—", "#0f172a", "SQLite"),
            chip("SPARQL gen", t.sparql_gen_ms != null ? `${t.sparql_gen_ms} ms` : "—", "#7c3aed", "LLM"),
            chip("GraphDB exec", t.graphdb_ms != null ? `${t.graphdb_ms} ms` : "—", "#0f172a", speedRatio || "RDF"),
            chip("Triples", tcLabel, "#475467", "repo size"),
          ].join("");
        }

        // SQLite side — metadata and table
        const gcSqlMeta = document.getElementById("askGraphSqlMeta");
        if (gcSqlMeta) {
          gcSqlMeta.textContent = `${sqlRows} row(s) · ${t.sql_ms ?? "—"} ms · ${(payload.columns || []).length} col(s)`;
        }
        const gcSqlTable = document.getElementById("askGraphSqlTable");
        if (gcSqlTable) {
          renderTable(gcSqlTable, payload.columns || [], payload.rows || [], "No rows returned.");
        }
        const gcSqlQuery = document.getElementById("askGraphSqlQuery");
        if (gcSqlQuery) {
          gcSqlQuery.textContent = gc.sql_query || payload.sql || "";
        }

        // GraphDB side — metadata and table
        const gcGdbMeta = document.getElementById("askGraphGdbMeta");
        if (gcGdbMeta) {
          if (!gc.graphdb_available) {
            gcGdbMeta.textContent = "offline";
          } else if (!gc.data_loaded) {
            gcGdbMeta.textContent = "connected · no data loaded";
          } else {
            gcGdbMeta.textContent = `${gc.row_count} row(s) · ${t.graphdb_ms ?? "—"} ms · ${(gc.columns || []).length} col(s)`;
          }
        }
        const gcSparqlTable = document.getElementById("askGraphSparqlTable");
        if (gcSparqlTable) {
          if (gc.graphdb_available && gc.columns && gc.columns.length) {
            renderTable(gcSparqlTable, gc.columns, gc.rows || [], "No matching rows in RDF graph.");
          } else if (gc.graphdb_available && !gc.data_loaded) {
            gcSparqlTable.innerHTML = `<tbody><tr><td style="padding:10px 12px;color:#92400e;font-size:12px;">Repository is empty — run: python3 scripts/rdf_uplift.py --import</td></tr></tbody>`;
          } else {
            const msg = gc.graphdb_available ? "No rows returned." : "GraphDB not running — start it and re-ask.";
            gcSparqlTable.innerHTML = `<tbody><tr><td style="padding:10px 12px;color:#94a3b8;font-size:12px;">${msg}</td></tr></tbody>`;
          }
        }
        const gcSparqlQuery = document.getElementById("askGraphSparqlQuery");
        if (gcSparqlQuery && gc.sparql_query) {
          gcSparqlQuery.textContent = gc.sparql_query;
        }

        // Mismatch explanation
        const gcMismatch = document.getElementById("askMismatchExplanation");
        const gcMismatchText = document.getElementById("askMismatchText");
        if (gcMismatch && gcMismatchText) {
          if (gc.mismatch_explanation) {
            gcMismatchText.textContent = gc.mismatch_explanation;
            gcMismatch.style.display = "block";
          } else {
            gcMismatch.style.display = "none";
          }
        }
      }
      latestResultPayload = payload;
      setFeedbackStatus(
        payload?.query_provenance?.direct_memory_reuse
          ? "This answer reused an approved query pattern. Rate it if it still looks right."
          : "Rate this answer to teach the system whether this SQL pattern should be reused.",
        "muted"
      );

      if (pdfLinkEl) {
        if (payload.pdf_url) {
          pdfLinkEl.href = payload.pdf_url;
          pdfLinkEl.style.display = "inline-flex";
        } else {
          pdfLinkEl.style.display = "none";
          pdfLinkEl.removeAttribute("href");
        }
      }

      if (resultEl) resultEl.style.display = "block";
      setStatus("Completed.");
    } catch (err) {
      showError(err?.message || "Something went wrong.");
      setStatus("");
      setFeedbackStatus("", "muted");
    } finally {
      if (submitEl) submitEl.disabled = false;
    }
  }

  if (submitEl) submitEl.addEventListener("click", () => runQuery());
  if (questionEl) {
    questionEl.addEventListener("keydown", (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") runQuery();
    });
  }

  if (hintEl) {
    hintEl.addEventListener("input", () => {
      const value = (hintEl.value || "").trim();
      if (townlandSuggestTimer) clearTimeout(townlandSuggestTimer);
      if (value.length < 2) {
        renderHintSuggestions([]);
        return;
      }
      townlandSuggestTimer = setTimeout(async () => {
        try {
          const res = await fetch(`/api/ask/townland-suggest?q=${encodeURIComponent(value)}`);
          const payload = await res.json();
          renderHintSuggestions(payload.suggestions || []);
        } catch {
          renderHintSuggestions([]);
        }
      }, 180);
    });
  }

  document.querySelectorAll(".ask-example").forEach((btn) => {
    btn.addEventListener("click", () => {
      const q = btn.getAttribute("data-q") || "";
      if (questionEl) questionEl.value = q;
      if (questionEl) questionEl.focus();
    });
  });

  async function sendFeedback(feedback) {
    if (!latestResultPayload) {
      setFeedbackStatus("Run a query first so there is something to rate.", "error");
      return;
    }
    const structured = latestResultPayload?.structured_output || {};
    const queries = structured?.queries || {};
    const summary = structured?.summary || {};
    const provenance = latestResultPayload?.query_provenance || structured?.query_provenance || {};

    if (feedbackUpEl) feedbackUpEl.disabled = true;
    if (feedbackDownEl) feedbackDownEl.disabled = true;
    setFeedbackStatus("Saving feedback...", "muted");

    try {
      const res = await fetch("/api/ask/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: latestResultPayload.question,
          townland_hint: latestResultPayload.townland_context || hintEl?.value || null,
          sql_text: queries.local_sqlite_query || latestResultPayload.sql || "",
          vrti_postgres_sql: queries.vrti_postgresql_query || "",
          feedback,
          note: (feedbackNoteEl?.value || "").trim() || null,
          result_row_count: latestResultPayload.row_count || 0,
          availability_state: latestResultPayload?.availability?.state || null,
          llm_meta: latestResultPayload.llm || {},
          reused_memory_id: provenance?.reused_memory_id || null,
          sample_answer: latestResultPayload.actual_answer || latestResultPayload.answer || "",
          summary_json: summary || {},
        }),
      });
      const payload = await res.json();
      if (!res.ok) {
        throw new Error(payload?.error || `Feedback failed (${res.status})`);
      }
      if (feedbackNoteEl) feedbackNoteEl.value = "";
      if (payload?.stored_in_memory) {
        setFeedbackStatus("Thumbs up saved. This SQL pattern is now part of approved query memory.", "success");
      } else if (feedback === "down") {
        setFeedbackStatus("Thumbs down recorded. The system will treat this query pattern more cautiously.", "success");
      } else {
        setFeedbackStatus("Feedback saved.", "success");
      }
    } catch (err) {
      setFeedbackStatus(err?.message || "Could not save feedback.", "error");
    } finally {
      if (feedbackUpEl) feedbackUpEl.disabled = false;
      if (feedbackDownEl) feedbackDownEl.disabled = false;
    }
  }

  if (feedbackUpEl) feedbackUpEl.addEventListener("click", () => sendFeedback("up"));
  if (feedbackDownEl) feedbackDownEl.addEventListener("click", () => sendFeedback("down"));

  checkLlmStatus();

  // GraphDB live status chip
  (async () => {
    const dot  = $("graphdbStatusDot");
    const text = $("graphdbStatusText");
    if (!dot || !text) return;
    try {
      const res = await fetch("/api/kg/graphdb-status");
      if (!res.ok) throw new Error("status check failed");
      const s = await res.json();
      if (!s.enabled) {
        dot.style.background = "#94a3b8";
        text.textContent = "GraphDB: disabled";
      } else if (!s.available) {
        dot.style.background = "#dc2626";
        text.textContent = "GraphDB: offline";
        $("graphdbStatusChip").style.background = "#fef2f2";
        $("graphdbStatusChip").style.borderColor = "#fecaca";
        $("graphdbStatusChip").style.color = "#991b1b";
      } else if (!s.data_loaded) {
        dot.style.background = "#f59e0b";
        text.textContent = "GraphDB: connected · repository empty";
        $("graphdbStatusChip").style.background = "#fffbeb";
        $("graphdbStatusChip").style.borderColor = "#fde68a";
        $("graphdbStatusChip").style.color = "#92400e";
      } else {
        const tc = s.triple_count >= 0 ? ` · ${s.triple_count.toLocaleString()} triples` : "";
        dot.style.background = "#16a34a";
        text.textContent = `GraphDB: connected${tc}`;
        $("graphdbStatusChip").style.background = "#f0fdf4";
        $("graphdbStatusChip").style.borderColor = "#bbf7d0";
        $("graphdbStatusChip").style.color = "#166534";
      }
    } catch (_) {
      if (dot) dot.style.background = "#dc2626";
      if (text) text.textContent = "GraphDB: unreachable";
    }
  })();
});
