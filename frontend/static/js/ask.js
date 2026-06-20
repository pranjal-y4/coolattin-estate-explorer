document.addEventListener("DOMContentLoaded", () => {
  const $ = (id) => document.getElementById(id);

  const questionEl       = $("askQuestion");
  const hintEl           = $("askTownlandHint");
  const hintDropdownEl   = $("askTownlandDropdown");
  const submitEl         = $("askSubmit");
  const statusEl         = $("askStatus");
  const progressEl       = $("askProgress");
  const errorEl          = $("askError");
  const resultEl         = $("askResult");
  const actualAnswerEl   = $("askActualAnswer");
  const llmAnswerEl      = $("askLlmAnswer");
  const llmMetaEl        = $("askLlmMeta");
  const provenanceEl     = $("askProvenance");
  const retrievalLaneEl  = $("askRetrievalLane");
  const townlandResEl    = $("askTownlandResolution");
  const warningsEl       = $("askWarnings");
  const explainBlockEl   = $("askExplainBlock");
  const explainContentEl = $("askExplainContent");
  const suggestionsBlockEl = $("askSuggestionsBlock");
  const suggestionsEl    = $("askSuggestions");
  const insightsBlockEl  = $("askInsightsBlock");
  const insightsEl       = $("askInsights");
  const feedbackNoteEl   = $("askFeedbackNote");
  const feedbackUpEl     = $("askFeedbackUp");
  const feedbackDownEl   = $("askFeedbackDown");
  const feedbackStatusEl = $("askFeedbackStatus");
  const pdfLinkEl        = $("askPdfLink");
  const kgBlockEl        = $("askKgBlock");
  const kgContentEl      = $("askKgContent");
  const localTableEl     = $("askTable");
  const vrtiTableEl      = $("askVrtiTable");
  const sqlRowCountEl    = $("askSqlRowCount");
  const kgSectionEl      = $("askKgSection");
  const kgResultBlockEl  = $("askKgResultBlock");
  const kgRowCountEl     = $("askKgRowCount");
  const sparqlBlockEl    = $("askSparqlBlock");
  const sparqlQueryEl    = $("askSparqlQuery");
  const sqliteQueryEl    = $("askSqliteQuery");
  const summaryEl        = $("askSummary");
  const chartBlockEl     = $("askChartBlock");
  const chartEl          = $("askChart");
  const supportContextBlockEl = $("askSupportContextBlock");
  const supportContextEl = $("askSupportContext");
  const llmStatusEl      = $("llmStatus");
  const estateOverviewEl = $("askEstateOverview");
  const estateOverviewBodyEl = $("askEstateOverviewBody");
  const loadingBannerEl      = $("askLoadingBanner");
  const loadingBannerTextEl  = $("askLoadingBannerText");
  const inlineStatusEl       = $("askInlineStatus");
  const inlineStatusTextEl   = $("askInlineStatusText");
  const inlineStageEl        = $("askInlineStage");

  const progressOrder = [
    { key: "classifying_intent",   label: "Routing Question" },
    { key: "contacting_llm",       label: "Building SQL Query" },
    { key: "slot_filling",         label: "Slot Filling" },
    { key: "framing_query",        label: "Framing Query" },
    { key: "querying_database",    label: "Querying Database" },
    { key: "querying_subgraph",    label: "KG Townland Lookup" },
    { key: "querying_vrti_graph",  label: "VRTI Geographic Context" },
    { key: "querying_fusion",      label: "Synthesising Answer" },
    { key: "preparing_output",     label: "Preparing Output" },
  ];

  const progressMap = new Map();
  let latestLlmStatus = null;
  let townlandSuggestTimer = null;
  let latestResultPayload = null;
  let dropdownItems = [];
  let dropdownActiveIdx = -1;
  let closeDropdownTimer = null;

  // ── Utilities ──────────────────────────────────────────────────────────────
  function setStatus(msg, stageLabel) {
    if (statusEl) statusEl.textContent = msg || "";
    if (loadingBannerTextEl && msg) loadingBannerTextEl.textContent = msg;
    if (inlineStatusTextEl && msg) inlineStatusTextEl.textContent = msg;
    if (inlineStageEl) inlineStageEl.textContent = stageLabel || "";
  }

  function showError(msg) {
    if (!errorEl) return;
    if (!msg) { errorEl.style.display = "none"; errorEl.textContent = ""; return; }
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

  // Configure marked.js (loaded via marked.min.js before this script)
  marked.use({ gfm: true, breaks: false });

  function markdownToHtml(text) {
    if (!text) return "";
    return marked.parse(String(text));
  }

  // ── Townland autocomplete dropdown ─────────────────────────────────────────
  const ALL_TOWNLANDS_ITEM = { name: null, _isAll: true };

  // Single delegated listener — more reliable than per-item mousedown bindings
  if (hintDropdownEl) {
    hintDropdownEl.addEventListener("pointerdown", (e) => {
      const option = e.target.closest(".tl-option");
      if (!option) return;
      e.preventDefault();
      const idx = parseInt(option.dataset.idx, 10);
      if (!isNaN(idx)) selectDropdownItem(idx);
    });
  }

  function renderDropdown(suggestions, prependAll) {
    if (!hintDropdownEl) return;
    const filtered = Array.isArray(suggestions) ? suggestions.filter(s => s?.name) : [];
    dropdownItems = prependAll ? [ALL_TOWNLANDS_ITEM, ...filtered] : filtered;
    dropdownActiveIdx = -1;

    if (!dropdownItems.length) {
      hintDropdownEl.style.display = "none";
      hintDropdownEl.innerHTML = "";
      return;
    }

    const isAllSelected = Boolean(hintEl?.dataset.isAll) || (hintEl?.value || "").trim() === "";
    hintDropdownEl.innerHTML = dropdownItems.map((s, i) => {
      if (s._isAll) {
        const selectedStyle = isAllSelected
          ? "background:#f0fdf4;border-left:3px solid #0f766e;"
          : "";
        const checkmark = isAllSelected ? `<span style="margin-left:auto;color:#0f766e;font-weight:700;">✓</span>` : "";
        return `<div class="tl-option" data-idx="${i}" role="option" style="border-bottom:1px solid #e2e8f0;color:#0f766e;font-weight:600;${selectedStyle}">
          <span class="tl-option-name">All Townlands</span><span class="tl-option-meta"> — estate-wide query (no location filter)</span>${checkmark}
        </div>`;
      }
      const parish = s.civil_parish ? `<span class="tl-option-meta"> · ${escapeHtml(s.civil_parish)}</span>` : "";
      const gaelic = s.name_gaelic ? `<span class="tl-option-meta"> (${escapeHtml(s.name_gaelic)})</span>` : "";
      return `<div class="tl-option" data-idx="${i}" role="option">
        <span class="tl-option-name">${escapeHtml(s.name)}</span>${gaelic}${parish}
      </div>`;
    }).join("");

    hintDropdownEl.style.display = "block";
  }

  function selectDropdownItem(idx) {
    if (idx < 0 || idx >= dropdownItems.length) return;
    const item = dropdownItems[idx];
    closeDropdown();
    if (item._isAll) {
      if (hintEl) {
        hintEl.value = "All Townlands";
        hintEl.dataset.isAll = "true";
        hintEl.style.color = "#0f766e";
        hintEl.style.fontWeight = "600";
        hintEl.blur();
      }
    } else {
      if (hintEl) {
        hintEl.value = item.name;
        delete hintEl.dataset.isAll;
        hintEl.style.color = "";
        hintEl.style.fontWeight = "";
        hintEl.blur();
      }
    }
  }

  function closeDropdown() {
    if (hintDropdownEl) { hintDropdownEl.style.display = "none"; hintDropdownEl.innerHTML = ""; }
    dropdownItems = [];
    dropdownActiveIdx = -1;
  }

  function setActiveDropdownItem(idx) {
    if (!hintDropdownEl) return;
    const options = hintDropdownEl.querySelectorAll(".tl-option");
    options.forEach((el, i) => el.classList.toggle("tl-active", i === idx));
    dropdownActiveIdx = idx;
    if (options[idx]) options[idx].scrollIntoView({ block: "nearest" });
  }

  if (hintEl) {
    hintEl.addEventListener("focus", () => {
      if (closeDropdownTimer) { clearTimeout(closeDropdownTimer); closeDropdownTimer = null; }
      if (hintEl.dataset.isAll) {
        renderDropdown([], true);
        setActiveDropdownItem(0);
        return;
      }
      const val = (hintEl.value || "").trim();
      if (val.length === 0) renderDropdown([], true);
    });

    hintEl.addEventListener("input", () => {
      // Typing clears an "All Townlands" selection
      if (hintEl.dataset.isAll) {
        delete hintEl.dataset.isAll;
        hintEl.style.color = "";
        hintEl.style.fontWeight = "";
      }
      const val = (hintEl.value || "").trim();
      if (townlandSuggestTimer) clearTimeout(townlandSuggestTimer);
      if (val.length === 0) { renderDropdown([], true); return; }
      if (val.length < 2) { closeDropdown(); return; }
      townlandSuggestTimer = setTimeout(async () => {
        try {
          const res = await fetch(`/api/ask/townland-suggest?q=${encodeURIComponent(val)}`);
          const data = await res.json();
          renderDropdown(data.suggestions || [], true);
        } catch { closeDropdown(); }
      }, 180);
    });

    hintEl.addEventListener("keydown", (e) => {
      if (!dropdownItems.length) return;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActiveDropdownItem(Math.min(dropdownActiveIdx + 1, dropdownItems.length - 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setActiveDropdownItem(Math.max(dropdownActiveIdx - 1, 0));
      } else if (e.key === "Enter" && dropdownActiveIdx >= 0) {
        e.preventDefault();
        selectDropdownItem(dropdownActiveIdx);
      } else if (e.key === "Escape") {
        closeDropdown();
      }
    });

    hintEl.addEventListener("blur", () => {
      closeDropdownTimer = setTimeout(closeDropdown, 150);
    });
  }

  // ── Warnings ───────────────────────────────────────────────────────────────
  function renderWarnings(list) {
    if (!warningsEl) return;
    const warnings = Array.isArray(list) ? list.filter(Boolean) : [];
    if (!warnings.length) { warningsEl.innerHTML = ""; return; }
    warningsEl.innerHTML = `<div style="margin-top:10px;display:grid;gap:4px;">` +
      warnings.map(w =>
        `<div style="padding:8px 10px;border-radius:8px;background:#fff7ed;border:1px solid #fed7aa;color:#9a3412;font-size:12px;line-height:1.5;">${escapeHtml(w)}</div>`
      ).join("") + `</div>`;
  }

  // ── Suggestions / Insights ─────────────────────────────────────────────────
  function renderSuggestions(payload) {
    if (!suggestionsBlockEl || !suggestionsEl) return;
    const availability = payload?.availability || payload?.structured_output?.availability || {};
    const suggestions = Array.isArray(payload?.suggestions)
      ? payload.suggestions
      : Array.isArray(availability?.suggestions) ? availability.suggestions : [];
    if (availability?.available || !suggestions.length) {
      suggestionsBlockEl.style.display = "none";
      suggestionsEl.innerHTML = "";
      return;
    }
    suggestionsBlockEl.style.display = "block";
    suggestionsEl.innerHTML = suggestions.map(item =>
      `<div style="padding:9px 11px;border:1px solid #dbeafe;background:#eff6ff;border-radius:10px;color:#1d4ed8;font-size:12px;line-height:1.45;">${escapeHtml(item)}</div>`
    ).join("");
  }

  function renderInsights(payload) {
    if (!insightsBlockEl || !insightsEl) return;
    const insights = Array.isArray(payload?.related_insights)
      ? payload.related_insights
      : Array.isArray(payload?.structured_output?.related_insights)
      ? payload.structured_output.related_insights : [];
    if (!insights.length) { insightsBlockEl.style.display = "none"; insightsEl.innerHTML = ""; return; }
    insightsBlockEl.style.display = "block";
    insightsEl.innerHTML = insights.map(item => `
      <div style="padding:9px 11px;border:1px solid #e2e8f0;background:#fff;border-radius:10px;font-size:12px;line-height:1.45;">
        <div style="font-weight:800;color:#334155;">${escapeHtml(item?.label || "Insight")}</div>
        <div style="margin-top:3px;color:#475467;">${escapeHtml(item?.value || "")}</div>
      </div>`).join("");
  }

  // ── Badge helpers ──────────────────────────────────────────────────────────
  const _laneLabels = {
    analytical_rule: "ANALYTICAL · semantic rule",
    analytical_llm:  "ANALYTICAL · LLM SQL",
    relational:      "RELATIONAL · KG context",
    comparative:     "COMPARATIVE · SQL + KG",
    fallback_verified_analysis: "FALLBACK · verified template",
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
  const _strategyConfidence = {
    "semantic_layer_rule":        { label: "Deterministic",     bg: "#f0fdf4", border: "#86efac", color: "#166534" },
    "verified_analysis":          { label: "Verified Template", bg: "#eff6ff", border: "#93c5fd", color: "#1e40af" },
    "fallback_verified_analysis": { label: "Verified Template", bg: "#eff6ff", border: "#93c5fd", color: "#1e40af" },
    "semantic_layer_llm":         { label: "Template-guided",   bg: "#f0f9ff", border: "#7dd3fc", color: "#075985" },
    "memory_direct_reuse":        { label: "Memory Reuse",      bg: "#f0f9ff", border: "#7dd3fc", color: "#075985" },
    "emergency_fallback":         { label: "LLM Fallback",      bg: "#fffbeb", border: "#fcd34d", color: "#92400e" },
    "validated_sql_unavailable":  { label: "LLM Fallback",      bg: "#fffbeb", border: "#fcd34d", color: "#92400e" },
    "llm_fallback":               { label: "LLM Fallback",      bg: "#fffbeb", border: "#fcd34d", color: "#92400e" },
  };

  function _chip(label, bg, border, color, title) {
    const titleAttr = title ? ` title="${escapeHtml(title)}"` : "";
    return `<span${titleAttr} style="display:inline-flex;align-items:center;padding:2px 8px;border-radius:999px;`
      + `background:${bg};border:1px solid ${border};color:${color};font-size:10px;font-weight:700;">`
      + `${escapeHtml(label)}</span>`;
  }

  // ── Explainability & Justification (Section 3) ────────────────────────────
  function renderExplainability(payload) {
    if (!explainContentEl) return;
    const provenance = payload?.query_provenance || payload?.structured_output?.query_provenance || {};
    const srcTables  = Array.isArray(payload?.source_tables) ? payload.source_tables : [];
    const rows       = payload?.rows || [];
    const structured = payload?.structured_output || {};

    const sections = [];

    // ── Tables queried ────────────────────────────────────────────────────
    sections.push({
      label: "Tables Queried",
      desc: "These are the database tables the system accessed to construct this answer. Each table holds a different category of estate records — unified person records, townland geography, census population counts, or clearance events.",
      value: srcTables.length ? srcTables.join(", ") : "Not recorded for this query.",
    });

    // ── Rows retrieved ────────────────────────────────────────────────────
    const kgRows = structured.processed_tables?.vrti_graph?.row_count ?? 0;
    sections.push({
      label: "Records Retrieved",
      desc: "The raw count of records returned before the language model summarised them. A count of zero may indicate that the filters were too narrow, or that no data was recorded for the specified criteria.",
      value: `${rows.length} row${rows.length !== 1 ? "s" : ""} from the estate database` +
             (kgRows ? ` · ${kgRows} row${kgRows !== 1 ? "s" : ""} from the VRTI Knowledge Graph` : " · Knowledge Graph not queried for this question"),
    });

    // ── Filters applied ───────────────────────────────────────────────────
    const sql = (structured.queries?.local_sqlite_query || payload.sql || "").toUpperCase();
    const filterParts = [];
    if (sql.includes("HAS_EMIGRATION_RECORD = 1"))  filterParts.push("emigrants only");
    if (sql.includes("HAS_EVICTION_RECORD = 1"))    filterParts.push("evictions only");
    if (sql.includes("HAS_TENANCY_RECORD = 1"))     filterParts.push("tenants only");
    if (sql.includes("GENDER = 'F'"))               filterParts.push("gender: female");
    if (sql.includes("GENDER = 'M'"))               filterParts.push("gender: male");
    const ageMatch = sql.match(/AGE\s*(>|<|>=|<=|=|BETWEEN)\s*(\d+)/);
    if (ageMatch)   filterParts.push(`age ${ageMatch[1]} ${ageMatch[2]}`);
    const yrMatch   = sql.match(/YEAR\s*=\s*(\d{4})/);
    if (yrMatch)    filterParts.push(`year: ${yrMatch[1]}`);
    const yrBetween = sql.match(/YEAR\s+BETWEEN\s+(\d{4})\s+AND\s+(\d{4})/);
    if (yrBetween)  filterParts.push(`year range: ${yrBetween[1]}–${yrBetween[2]}`);
    if (sql.includes("IS_WIDOW = 1"))               filterParts.push("widows only");
    if (sql.includes("IS_CANADA_DESTINATION = 1"))  filterParts.push("Canada destination");

    sections.push({
      label: "Filters Applied",
      desc: "Conditions used to narrow the dataset. If results are empty, consider whether any of these filters may be too restrictive — for example, gender is not recorded for a significant portion of estate records.",
      value: filterParts.length ? filterParts.join(" · ") : "No specific filters — estate-wide or unfiltered query.",
    });

    // ── Geographic scope ──────────────────────────────────────────────────
    const tr = payload?.townland_resolution;
    if (tr?.name) {
      const pct = tr.confidence != null ? `, ${Math.round(tr.confidence * 100)}% confidence` : "";
      sections.push({
        label: "Geographic Scope",
        desc: "The townland or area identified from your question and used to filter records geographically. If the match type is fuzzy, the system selected the closest name by phonetic and string similarity.",
        value: `${tr.name}${tr.civil_parish ? ", " + tr.civil_parish : ""}${tr.barony ? ", " + tr.barony : ""} — matched as ${tr.match_type || "unknown"}${pct}.`,
      });
    } else {
      sections.push({
        label: "Geographic Scope",
        desc: "Whether the query was limited to a specific townland or applied across the entire estate.",
        value: "All townlands — no location filter applied. Results span the full Coolattin estate.",
      });
    }

    // ── KG subgraph ───────────────────────────────────────────────────────
    if (provenance?.subgraph_node_count != null) {
      const sources = Array.isArray(provenance.subgraph_sources)
        ? provenance.subgraph_sources.join(", ") : "VRTI";
      sections.push({
        label: "Knowledge Graph Context",
        desc: "Additional geographic and historical context retrieved from the Virtual Record Treasury of Ireland (VRTI) external knowledge graph to supplement the estate database.",
        value: `${provenance.subgraph_node_count} triples retrieved from ${sources}.`,
      });
    }

    // ── Query strategy ────────────────────────────────────────────────────
    const stratDescriptions = {
      "semantic_layer_rule":        "Deterministic rule — the answer was computed directly from a pre-defined template with no language model involvement. This is the most reliable path.",
      "verified_analysis":          "Verified template — a pre-approved query pattern was used and cross-checked for numeric consistency.",
      "fallback_verified_analysis": "Verified template — a pre-approved query pattern was used and cross-checked for numeric consistency.",
      "memory_direct_reuse":        "Approved memory reuse — this question was answered before and its SQL pattern was saved as approved. The language model was bypassed entirely for SQL generation.",
      "semantic_layer_llm":         "Template-guided generation — the language model wrote SQL constrained by slot-filled parameters derived from your question.",
      "emergency_fallback":         "Language model fallback — no template matched, so the model generated SQL directly from the question text. Results should be verified.",
      "validated_sql_unavailable":  "Language model fallback — generated SQL could not be validated; raw LLM output was used.",
      "llm_fallback":               "Language model fallback — no template matched, so the model generated SQL directly from the question text.",
    };
    if (provenance?.strategy) {
      sections.push({
        label: "Query Strategy",
        desc: "How the system decided to construct and execute the query for this question. Higher-confidence strategies use pre-approved patterns; lower-confidence ones rely more on live language model generation.",
        value: stratDescriptions[provenance.strategy] || provenance.strategy,
      });
    }

    // ── Memory reuse ──────────────────────────────────────────────────────
    if (provenance?.direct_memory_reuse) {
      sections.push({
        label: "Pattern Reuse",
        desc: "A previously approved SQL query was reused for this question, bypassing the language model for query generation entirely. This makes the answer faster and more deterministic.",
        value: `Reused approved pattern (id: ${provenance.reused_memory_id || "unknown"}).`,
      });
    }

    const row = (s) => `
      <div style="padding:10px 0 10px;border-bottom:1px solid #f1f5f9;">
        <div style="font-size:11px;font-weight:700;color:#374151;text-transform:uppercase;letter-spacing:.06em;margin-bottom:3px;">${escapeHtml(s.label)}</div>
        <div style="font-size:12px;color:#6b7280;line-height:1.55;margin-bottom:4px;">${escapeHtml(s.desc)}</div>
        <div style="font-size:12px;color:#111827;font-weight:600;line-height:1.5;">${escapeHtml(s.value)}</div>
      </div>`;

    explainContentEl.innerHTML = sections.map(row).join("") ||
      `<p style="font-size:12px;color:#94a3b8;margin:0;">No trace data available for this query.</p>`;
  }

  // ── Provenance (technical details section) ────────────────────────────────
  function renderProvenance(payload) {
    if (!provenanceEl) return;
    const provenance = payload?.query_provenance || payload?.structured_output?.query_provenance || {};
    const matches = Array.isArray(provenance?.approved_query_candidates) ? provenance.approved_query_candidates : [];
    const confidenceBadgeEl = $("askConfidenceBadge");
    const gateBadgeEl       = $("askGateBadge");
    const verifierBadgeEl   = $("askVerifierBadge");

    // Confidence tier badge (top bar)
    const strategy = provenance?.strategy || "";
    if (confidenceBadgeEl) {
      const conf = _strategyConfidence[strategy];
      if (conf) {
        confidenceBadgeEl.innerHTML = _chip(conf.label, conf.bg, conf.border, conf.color, `Query strategy: ${strategy}`);
        confidenceBadgeEl.style.display = "";
      } else {
        confidenceBadgeEl.innerHTML = ""; confidenceBadgeEl.style.display = "none";
      }
    }

    // Numeric gate badge (explainability section)
    if (gateBadgeEl) {
      const gate = provenance?.numeric_gate_outcome;
      if (gate && gate !== "not_applied") {
        const gateStyles = {
          pass:        { label: "✓ Gate pass",         bg: "#f0fdf4", border: "#86efac", color: "#166534" },
          regenerated: { label: "⚠ Gate: regenerated", bg: "#fffbeb", border: "#fcd34d", color: "#92400e" },
          fallback:    { label: "⚠ Gate: raw fallback", bg: "#fff1f2", border: "#fca5a5", color: "#9f1239" },
        };
        const gs = gateStyles[gate] || { label: gate, bg: "#f8fafc", border: "#cbd5e1", color: "#475467" };
        gateBadgeEl.innerHTML = _chip(gs.label, gs.bg, gs.border, gs.color, "Numeric-consistency gate outcome");
        gateBadgeEl.style.display = "";
      } else {
        gateBadgeEl.innerHTML = ""; gateBadgeEl.style.display = "none";
      }
    }

    // Cross-verifier badge
    if (verifierBadgeEl) {
      const verifier = provenance?.verifier;
      if (verifier && verifier.verdict !== "skip") {
        const vStyles = {
          agree:    { label: "✓ Verified",       bg: "#f0fdf4", border: "#86efac", color: "#166534" },
          disagree: { label: "⚠ Claims flagged", bg: "#fff1f2", border: "#fca5a5", color: "#9f1239" },
        };
        const vs = vStyles[verifier.verdict] || { label: verifier.verdict, bg: "#f8fafc", border: "#cbd5e1", color: "#475467" };
        const vtitle = verifier.verdict === "disagree" && verifier.unsupported_claims?.length
          ? `Unsupported: ${verifier.unsupported_claims.slice(0, 2).join("; ")}`
          : `Verifier: ${verifier.verdict} via ${verifier.provider || "llm"}`;
        verifierBadgeEl.innerHTML = _chip(vs.label, vs.bg, vs.border, vs.color, vtitle);
        verifierBadgeEl.style.display = "";
      } else {
        verifierBadgeEl.innerHTML = ""; verifierBadgeEl.style.display = "none";
      }
    }

    // Lane badge (top bar)
    if (retrievalLaneEl) {
      const route = provenance?.route;
      const lane  = provenance?.lane;
      if (route || lane) {
        const laneKey  = lane || route;
        const laneText = _laneLabels[laneKey] || laneKey;
        const palette  = _routeColors[route] || _routeColors.fallback;
        retrievalLaneEl.innerHTML = `
          <span style="display:inline-flex;align-items:center;gap:4px;padding:3px 10px;border-radius:999px;
            background:${palette.bg};border:1px solid ${palette.border};color:${palette.color};
            font-size:11px;font-weight:700;">${escapeHtml(laneText)}</span>`;
      } else {
        retrievalLaneEl.innerHTML = "";
      }
    }

    // Detail rows in technical section
    const srcTables = Array.isArray(payload?.source_tables) ? payload.source_tables : [];
    const rows = [
      `SQL source: ${provenance?.direct_memory_reuse ? "approved query memory" : payload?.llm?.mode || "generated"}.`,
      `Execution mode: ${provenance?.execution_mode || "executed_as_generated"}.`,
    ];
    if (provenance?.route) rows.unshift(`Retrieval route: ${provenance.route}.`);
    if (provenance?.lane) rows.splice(provenance?.route ? 1 : 0, 0, `Dispatch lane: ${provenance.lane}.`);
    if (srcTables.length) rows.push(`Source tables: ${srcTables.join(", ")}.`);
    const tr = payload?.townland_resolution;
    if (tr?.match_type) {
      const pct = tr.confidence != null ? ` · ${Math.round(tr.confidence * 100)}% match` : "";
      const fuzzyLabel = tr.match_type !== "exact" ? ` (fuzzy)` : "";
      rows.push(`Entity: "${tr.name || ""}" resolved via ${tr.match_type}${fuzzyLabel}${pct}.`);
    }
    if (provenance?.subgraph_node_count != null) {
      const sources = Array.isArray(provenance.subgraph_sources) ? provenance.subgraph_sources.join(", ") : "—";
      rows.push(`KG subgraph: ${provenance.subgraph_node_count} triples from [${sources}].`);
    }
    if (provenance?.phase6_todo) rows.push(`Phase 6: ${provenance.phase6_todo}`);
    if (provenance?.reused_memory_id) rows.push(`Approved memory id reused: ${provenance.reused_memory_id}.`);
    if (matches.length) {
      const preview = matches.slice(0, 3).map(item => `${item.question_text} (score ${item.match_score})`).join(" | ");
      rows.push(`Closest approved query patterns: ${preview}.`);
    }
    provenanceEl.innerHTML = rows
      .map(line => `<div style="padding:7px 9px;border:1px solid #e2e8f0;background:#f8fafc;border-radius:8px;">${escapeHtml(line)}</div>`)
      .join("");

    // Also hide the standalone source tables label (it's in explainability now)
    const srcTablesEl = $("askSourceTables");
    if (srcTablesEl) srcTablesEl.style.display = "none";
  }

  // ── Townland resolution banner ─────────────────────────────────────────────
  function renderTownlandResolution(resolution) {
    if (!townlandResEl) return;
    if (!resolution || (!resolution.warning && !resolution.matched && !(resolution.suggestions || []).length)) {
      townlandResEl.style.display = "none"; townlandResEl.innerHTML = ""; return;
    }
    const matchText = resolution.matched
      ? `Using townland: ${resolution.name}${resolution.civil_parish ? ` · ${resolution.civil_parish}` : ""}`
      : "Townland was not matched exactly.";

    let confidenceBadge = "";
    if (resolution.confidence != null && resolution.match_type && resolution.match_type !== "exact") {
      const pct = Math.round(resolution.confidence * 100);
      const badgeColor = pct >= 90 ? "#166534" : pct >= 70 ? "#854d0e" : "#9f1239";
      const badgeBg    = pct >= 90 ? "#dcfce7"  : pct >= 70 ? "#fef9c3"  : "#fff1f2";
      confidenceBadge = `<span style="margin-left:6px;padding:2px 7px;border-radius:12px;font-size:10px;font-weight:700;background:${badgeBg};color:${badgeColor};">${pct}% match</span>`;
    }

    townlandResEl.style.display = "block";
    townlandResEl.innerHTML = `
      <div style="padding:10px 12px;border:1px solid #bae6fd;background:#f0f9ff;border-radius:10px;color:#075985;font-size:12px;line-height:1.5;">
        <div style="display:flex;align-items:center;flex-wrap:wrap;gap:4px;">
          <strong>${escapeHtml(matchText)}</strong>${confidenceBadge}
        </div>
        ${resolution.warning ? `<div style="margin-top:4px;">${escapeHtml(resolution.warning)}</div>` : ""}
      </div>`;
  }

  // ── KG townland context ────────────────────────────────────────────────────
  function renderKg(kg) {
    if (!kgBlockEl || !kgContentEl) return;
    if (!kg || !Array.isArray(kg.townlands) || !kg.townlands.length) {
      kgBlockEl.style.display = "none"; kgContentEl.innerHTML = "";
      _updateKgSection();
      return;
    }
    const blocks = [];
    kg.townlands.forEach(t => {
      const gaelic = t.name_gaelic ? `<span style="color:#6b21a8;font-size:12px;"> · ${escapeHtml(t.name_gaelic)}</span>` : "";
      blocks.push(`
        <div style="padding:10px 12px;border:1px solid #e2e8f0;background:#fff;border-radius:10px;">
          <div style="font-weight:700;color:#0f172a;">${escapeHtml(t.name)}${gaelic}</div>
          <div style="font-size:12px;color:#475467;margin-top:3px;">
            Parish: ${escapeHtml(t.civil_parish || "-")} · Barony: ${escapeHtml(t.barony || "-")} · County: ${escapeHtml(t.county || "-")}
          </div>
          ${t.kg_uri ? `<a href="${escapeHtml(t.kg_uri)}" target="_blank" rel="noopener" style="display:inline-block;margin-top:6px;font-size:12px;color:#0f766e;">Open KG URI ↗</a>` : ""}
        </div>`);
    });
    kgBlockEl.style.display = "block";
    kgContentEl.innerHTML = blocks.join("");
    _updateKgSection();
  }

  function _updateKgSection() {
    if (!kgSectionEl) return;
    const hasKgTable = kgResultBlockEl?.style.display !== "none";
    const hasKgGeo   = kgBlockEl?.style.display !== "none";
    kgSectionEl.style.display = (hasKgTable || hasKgGeo) ? "block" : "none";
  }

  // ── Estate-wide overview panel ────────────────────────────────────────────
  async function renderEstateOverview(isEstateWide) {
    if (!estateOverviewEl || !estateOverviewBodyEl) return;
    if (!isEstateWide) { estateOverviewEl.style.display = "none"; return; }
    estateOverviewEl.style.display = "block";
    estateOverviewBodyEl.innerHTML = `<div style="font-size:12px;color:#64748b;padding:8px 0;">Loading County Wicklow overview…</div>`;
    try {
      const res  = await fetch("/api/ask/estate-overview");
      const data = await res.json();
      if (data.error) throw new Error(data.error);

      const n = (v) => escapeHtml(v == null ? "—" : String(v));

      // ── helpers ──────────────────────────────────────────────────────────
      const statCard = (label, value, sub) => `
        <div style="padding:12px 14px;border:1px solid #bfdbfe;background:#fff;border-radius:10px;">
          <div style="font-size:10px;font-weight:700;color:#1e40af;text-transform:uppercase;letter-spacing:.05em;margin-bottom:3px;">${escapeHtml(label)}</div>
          <div style="font-size:22px;font-weight:800;color:#1e3a8a;line-height:1.1;">${n(value)}</div>
          ${sub ? `<div style="font-size:11px;color:#64748b;margin-top:2px;">${escapeHtml(sub)}</div>` : ""}
        </div>`;

      const fullWidth = (label, content) => `
        <div style="grid-column:1/-1;padding:10px 14px;border:1px solid #bfdbfe;background:#fff;border-radius:10px;">
          <div style="font-size:10px;font-weight:700;color:#1e40af;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px;">${escapeHtml(label)}</div>
          ${content}
        </div>`;

      const pill = (text) =>
        `<span style="display:inline-block;padding:2px 8px;border-radius:999px;background:#eff6ff;border:1px solid #bfdbfe;color:#1e40af;font-size:11px;margin:2px 2px 2px 0;">${escapeHtml(text)}</span>`;

      const rankRow = (items, labelKey, valueKey, valueSuffix) =>
        items.map((r, i) =>
          `<div style="display:flex;justify-content:space-between;align-items:center;padding:4px 0;${i < items.length - 1 ? "border-bottom:1px solid #f1f5f9;" : ""}">
            <span style="font-size:12px;color:#334155;">${escapeHtml(r[labelKey])}</span>
            <span style="font-size:12px;font-weight:700;color:#1e3a8a;">${n(r[valueKey])}${valueSuffix ? " " + valueSuffix : ""}</span>
          </div>`
        ).join("");

      // ── Section 1: County Wicklow geography ──────────────────────────────
      const geoCards = [
        statCard("Townlands",       data.townland_count, "in County Wicklow"),
        statCard("Civil Parishes",  data.parish_count,   "administrative divisions"),
        statCard("Baronies",        data.barony_count,   "historic territorial units"),
        statCard("Irish Names",     data.gaelic_name_count, "townlands with Gaelic name recorded"),
        data.total_area_sqkm
          ? statCard("Total Area",  data.total_area_sqkm + " km²", "combined area of mapped townlands")
          : "",
        data.largest_townland
          ? statCard("Largest Townland", data.largest_townland.name, `${data.largest_townland.area_sqkm} km²`)
          : "",
      ].filter(Boolean).join("");

      // Baronies list
      const baroniesHtml = Array.isArray(data.baronies) && data.baronies.length
        ? data.baronies.map(pill).join("")
        : "<span style='font-size:12px;color:#94a3b8;'>No barony data in database</span>";

      // Top parishes by townland count
      const topParishHtml = Array.isArray(data.top_parishes_by_townlands) && data.top_parishes_by_townlands.length
        ? rankRow(data.top_parishes_by_townlands, "parish", "townland_count", "townlands")
        : "<span style='font-size:12px;color:#94a3b8;'>No parish data</span>";

      // ── Section 2: Census & population ───────────────────────────────────
      const censusCards = [];
      if (data.pop_1841) {
        censusCards.push(statCard("Population 1841", data.pop_1841.toLocaleString(), "pre-Famine count"));
      }
      if (data.pop_1851) {
        censusCards.push(statCard("Population 1851", data.pop_1851.toLocaleString(), "post-Famine count"));
      }
      if (data.pop_decline_pct != null) {
        censusCards.push(statCard("Famine Decline", data.pop_decline_pct + "%", "population fall 1841–1851"));
      }

      const topPopHtml = Array.isArray(data.top_pop_1841) && data.top_pop_1841.length
        ? rankRow(data.top_pop_1841, "name", "population", "people (1841)")
        : "<span style='font-size:12px;color:#94a3b8;'>No census data</span>";

      const topParishPopHtml = Array.isArray(data.top_parish_pop_1841) && data.top_parish_pop_1841.length
        ? rankRow(data.top_parish_pop_1841, "parish", "population", "people (1841)")
        : "<span style='font-size:12px;color:#94a3b8;'>No census data</span>";

      // ── Section 3: Coolattin estate records ──────────────────────────────
      const yrs = (data.year_min && data.year_max) ? `${data.year_min}–${data.year_max}` : "";
      const estateCards = [
        statCard("Estate Records", data.total_records, yrs ? `covering ${yrs}` : "total persons"),
        statCard("Emigrants",      data.emigrant_count, "recorded departures"),
        statCard("Evictions",      data.eviction_count, "clearance events"),
        statCard("Tenants",        data.tenant_count, "tenancy records"),
        statCard("To Canada",      data.canada_count, "Canada / Grosse Isle destination"),
      ].join("");

      const surnamesHtml = Array.isArray(data.top_surnames) && data.top_surnames.length
        ? data.top_surnames.map(s => pill(`${s.surname} (${s.count})`)).join("")
        : "<span style='font-size:12px;color:#94a3b8;'>No surname data</span>";

      const baroniesByRecordsHtml = Array.isArray(data.top_baronies_by_records) && data.top_baronies_by_records.length
        ? rankRow(data.top_baronies_by_records, "barony", "count", "records")
        : "<span style='font-size:12px;color:#94a3b8;'>No data</span>";

      // ── Assemble ──────────────────────────────────────────────────────────
      const sep = (title) =>
        `<div style="grid-column:1/-1;padding:6px 0 2px;border-top:2px solid #dbeafe;margin-top:4px;">
          <span style="font-size:11px;font-weight:700;color:#1e40af;text-transform:uppercase;letter-spacing:.07em;">${escapeHtml(title)}</span>
        </div>`;

      estateOverviewBodyEl.innerHTML = [
        sep("County Wicklow — Geographic Structure"),
        geoCards,
        fullWidth("Baronies of County Wicklow", baroniesHtml),
        fullWidth("Largest Civil Parishes by Number of Townlands", topParishHtml),
        ...(censusCards.length ? [
          sep("Population — Census Records"),
          censusCards.join(""),
          fullWidth("Most Populated Townlands in 1841", topPopHtml),
          fullWidth("Most Populated Civil Parishes in 1841", topParishPopHtml),
        ] : []),
        sep("Coolattin Estate — Historical Records"),
        estateCards,
        fullWidth("Most Common Surnames in Estate Records", surnamesHtml),
        fullWidth("Baronies by Estate Record Count", baroniesByRecordsHtml),
      ].join("");

    } catch (err) {
      estateOverviewBodyEl.innerHTML = `<div style="font-size:12px;color:#9f1239;">Could not load overview: ${escapeHtml(err.message)}</div>`;
    }
  }

  // ── Supporting context ─────────────────────────────────────────────────────
  function renderSupportingContext(payload) {
    if (!supportContextBlockEl || !supportContextEl) return;
    const items = payload?.structured_output?.supporting_context || [];
    if (!Array.isArray(items) || !items.length) {
      supportContextBlockEl.style.display = "none"; supportContextEl.innerHTML = ""; return;
    }
    supportContextBlockEl.style.display = "block";
    supportContextEl.innerHTML = items.map(item => `
      <div style="padding:9px 11px;border:1px solid #e2e8f0;background:#fff;border-radius:10px;font-size:12px;line-height:1.45;">
        <div style="font-weight:800;color:#334155;">${escapeHtml(item.label || "Context")}</div>
        <div style="margin-top:3px;color:#475467;">${escapeHtml(item.value || "")}</div>
      </div>`).join("");
  }

  // ── Data table renderer ────────────────────────────────────────────────────
  function renderTable(tableEl, columns, rows, emptyText) {
    if (!tableEl) return;
    const cols = Array.isArray(columns) ? columns : [];
    const rs   = Array.isArray(rows)    ? rows    : [];

    if (!cols.length || !rs.length) {
      tableEl.innerHTML = `<tbody><tr><td style="padding:10px 12px;color:#64748b;">${escapeHtml(emptyText || "No rows returned.")}</td></tr></tbody>`;
      return;
    }

    // Highlight ID columns for traceability
    const idCols = new Set(cols.filter(c => c === "record_id" || c === "id" || c.endsWith("_id")));

    const header = `<thead><tr>${cols.map(c =>
      `<th style="position:sticky;top:0;background:${idCols.has(c) ? "#fef9c3" : "#f8fafc"};text-align:left;padding:8px 10px;border-bottom:1px solid #e2e8f0;font-size:12px;color:${idCols.has(c) ? "#854d0e" : "#475467"};">${escapeHtml(c)}${idCols.has(c) ? " 🔑" : ""}</th>`
    ).join("")}</tr></thead>`;

    function renderCell(val, col) {
      const raw = String(val ?? "");
      const isId = idCols.has(col);
      const style = `padding:8px 10px;border-bottom:1px solid #f1f5f9;font-size:12px;vertical-align:top;${isId ? "color:#854d0e;font-weight:700;background:#fffbeb;" : "color:#0f172a;"}`;
      if (raw.length > 200 && raw.includes(",")) {
        const parts = raw.split(",").map(s => s.trim()).filter(Boolean);
        const preview = parts.slice(0, 5).map(escapeHtml).join(", ");
        const rest = parts.length - 5;
        const all = parts.map(escapeHtml).join("<br>");
        return `<td style="${style}">
          <span>${preview}</span>${rest > 0 ? `<span style="color:#94a3b8;"> …and ${rest} more</span>` : ""}
          ${rest > 0 ? `<details style="margin-top:4px;"><summary style="font-size:11px;cursor:pointer;color:#1d4ed8;">Show all ${parts.length}</summary><div style="max-height:200px;overflow-y:auto;font-size:11px;line-height:1.6;margin-top:4px;">${all}</div></details>` : ""}
        </td>`;
      }
      return `<td style="${style}">${escapeHtml(raw)}</td>`;
    }

    const body = `<tbody>${rs.map(row =>
      `<tr>${cols.map(c => renderCell(row[c], c)).join("")}</tr>`
    ).join("")}</tbody>`;

    tableEl.innerHTML = header + body;
  }

  // ── Structured output (Section 1 + Section 4) ─────────────────────────────
  function renderStructured(payload) {
    const structured = payload?.structured_output || {};
    const queries    = structured?.queries || {};
    const processed  = structured?.processed_tables || {};
    const summary    = structured?.summary || {};
    const chart      = payload?.chart || structured?.chart || null;

    // ── Section 1a: SQL result table ─────────────────────────────────────────
    const local = processed.local_database || {};
    const localCols = local.columns || payload.columns || [];
    const localRows = local.rows   || payload.rows   || [];
    renderTable(localTableEl, localCols, localRows, "No database rows returned.");
    if (sqlRowCountEl) {
      sqlRowCountEl.textContent = localRows.length
        ? `${localRows.length} row${localRows.length !== 1 ? "s" : ""} returned`
        : "0 rows returned";
    }

    // ── Section 1b: VRTI/KG result table (separate KG section) ──────────────
    const vrti     = processed.vrti_graph || {};
    const vrtiCols = vrti.columns || [];
    const vrtiRows = vrti.rows    || [];
    const hasVrtiRows = vrtiRows.length > 0;
    renderTable(vrtiTableEl, vrtiCols, vrtiRows, "No VRTI graph rows returned.");
    if (kgResultBlockEl) kgResultBlockEl.style.display = hasVrtiRows ? "block" : "none";
    if (kgRowCountEl) {
      kgRowCountEl.textContent = hasVrtiRows
        ? `${vrtiRows.length} row${vrtiRows.length !== 1 ? "s" : ""} returned`
        : "";
    }
    _updateKgSection();

    // ── Section 4: Generated queries ─────────────────────────────────────────
    const sqlText = queries.local_sqlite_query || payload.sql || "";
    if (sqliteQueryEl) sqliteQueryEl.textContent = sqlText;

    const sparqlText = queries.vrti_postgresql_query || queries.vrti_sparql_query || payload.vrti_query || "";
    if (sparqlQueryEl) sparqlQueryEl.textContent = sparqlText;
    if (sparqlBlockEl) sparqlBlockEl.style.display = sparqlText ? "block" : "none";

    // ── Summary (hidden section) ──────────────────────────────────────────────
    if (summaryEl) summaryEl.textContent = summary.final_summary_text || "Summary unavailable.";
    renderChart(chart);
  }

  // ── Chart ──────────────────────────────────────────────────────────────────
  function renderChart(chart) {
    if (!chartBlockEl || !chartEl) return;
    const labels = Array.isArray(chart?.labels) ? chart.labels.map(v => String(v ?? "")) : [];
    const values = Array.isArray(chart?.values) ? chart.values.map(v => Number(v ?? 0)) : [];
    if (!chart || !labels.length || labels.length !== values.length) {
      chartBlockEl.style.display = "none"; chartEl.innerHTML = ""; return;
    }

    const safeValues = values.map(v => Number.isFinite(v) ? v : 0);
    const maxValue = Math.max(...safeValues, 1);

    if (chart.type === "line") {
      const W = 720, H = 260, P = 36;
      const points = safeValues.map((value, idx) => ({
        x: labels.length === 1 ? W / 2 : P + (idx * (W - P * 2)) / (labels.length - 1),
        y: H - P - (value / maxValue) * (H - P * 2),
        value,
        label: labels[idx],
      }));
      const path = points.map((pt, i) => `${i === 0 ? "M" : "L"} ${pt.x} ${pt.y}`).join(" ");
      chartBlockEl.style.display = "block";
      chartEl.innerHTML = `
        <div style="font-size:12px;font-weight:700;color:#0f172a;margin-bottom:8px;">${escapeHtml(chart.title || "Chart")}</div>
        <svg viewBox="0 0 ${W} ${H}" style="width:100%;min-width:620px;height:auto;">
          <line x1="${P}" y1="${H-P}" x2="${W-P}" y2="${H-P}" stroke="#cbd5e1" stroke-width="1.5"></line>
          <line x1="${P}" y1="${P}" x2="${P}" y2="${H-P}" stroke="#cbd5e1" stroke-width="1.5"></line>
          <path d="${path}" fill="none" stroke="#0f766e" stroke-width="3"></path>
          ${points.map(pt => `
            <circle cx="${pt.x}" cy="${pt.y}" r="4" fill="#0f766e"></circle>
            <text x="${pt.x}" y="${pt.y - 10}" text-anchor="middle" font-size="11" fill="#0f172a">${escapeHtml(String(pt.value))}</text>
            <text x="${pt.x}" y="${H - 12}" text-anchor="middle" font-size="10" fill="#475467">${escapeHtml(pt.label)}</text>`).join("")}
        </svg>`;
      return;
    }

    const bars = labels.map((label, idx) => {
      const value = safeValues[idx];
      const widthPct = Math.max(2, (value / maxValue) * 100);
      return `
        <div style="display:grid;grid-template-columns:minmax(120px,220px) 1fr auto;gap:10px;align-items:center;">
          <div style="font-size:12px;color:#334155;font-weight:600;">${escapeHtml(label)}</div>
          <div style="height:18px;background:#e2e8f0;border-radius:999px;overflow:hidden;">
            <div style="height:100%;width:${widthPct}%;background:linear-gradient(90deg,#0f766e,#22c55e);border-radius:999px;"></div>
          </div>
          <div style="font-size:12px;color:#0f172a;font-weight:700;">${escapeHtml(String(value))}</div>
        </div>`;
    });
    chartBlockEl.style.display = "block";
    chartEl.innerHTML = `
      <div style="font-size:12px;font-weight:700;color:#0f172a;margin-bottom:8px;">${escapeHtml(chart.title || "Chart")}</div>
      <div style="display:grid;gap:8px;">${bars.join("")}</div>`;
  }

  // ── LLM meta (technical section) ──────────────────────────────────────────
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
      `Provider: ${provider}. Model: ${model}.`,
      `LLM rewrite: ${rewriteGenerated ? "generated" : "not generated"} (${rewrite.mode || "unknown"}).`,
      `Data path: read-only SQLite query returned ${payload?.row_count ?? 0} local row(s); KG context returned ${payload?.structured_output?.processed_tables?.vrti_graph?.row_count ?? 0} row(s).`,
      `SQL framing: ${sqlLlm.provider || "unknown"} / ${sqlLlm.model || "-"} / ${sqlLlm.mode || "-"}.`,
    ];
    if (payload?.townland_context) rows.push(`Townland context: ${payload.townland_context}.`);
    if (payload?.availability?.state) rows.push(`Availability: ${payload.availability.state}.`);
    if (status.detail) rows.push(`Technical connection detail: ${status.detail}.`);
    if (rewrite.error) rows.push(`LLM detail: ${rewrite.error}.`);
    llmMetaEl.innerHTML = rows
      .map(line => `<div style="padding:7px 9px;border:1px solid #e2e8f0;background:#f8fafc;border-radius:8px;">${escapeHtml(line)}</div>`)
      .join("");
  }

  // ── LLM status chip (top of page) ─────────────────────────────────────────
  function renderLlmStatus(payload) {
    if (!llmStatusEl) return;
    const ok = Boolean(payload?.available);
    const provider = payload?.provider || "llm";
    const model = payload?.active_model || payload?.model || "-";
    const bg = ok ? "#ecfdf3" : "#fff7ed";
    const border = ok ? "#86efac" : "#fed7aa";
    const color = ok ? "#166534" : "#9a3412";
    const label = ok ? "LLM Connected" : "LLM not connected";
    const hint = payload?.hint ? ` ${payload.hint}` : "";
    const detail = payload?.detail ? `
      <details style="margin-top:4px;">
        <summary style="font-size:11px;color:#94a3b8;cursor:pointer;">Technical detail</summary>
        <div style="margin-top:4px;font-size:11px;color:#94a3b8;line-height:1.45;">${escapeHtml(payload.detail)}</div>
      </details>` : "";
    llmStatusEl.innerHTML = `
      <div style="display:inline-flex;align-items:center;gap:8px;padding:7px 10px;border-radius:999px;background:${bg};border:1px solid ${border};color:${color};font-size:12px;font-weight:700;">
        <span>${escapeHtml(label)}</span>
        <span style="font-weight:600;">${escapeHtml(provider)} · ${escapeHtml(model)}</span>
      </div>
      ${hint ? `<div style="margin-top:5px;font-size:12px;color:#64748b;">${escapeHtml(hint)}</div>` : ""}
      ${detail}`;
  }

  // ── Progress tracker ───────────────────────────────────────────────────────
  function renderProgress() {
    if (!progressEl) return;
    progressEl.innerHTML = progressOrder.map(stage => {
      const s = progressMap.get(stage.key);
      const status = s?.status || "pending";
      if (status === "pending") return "";
      const icon = status === "completed" ? "✓"
        : status === "in_progress" ? '<span style="animation:spin 1s linear infinite;display:inline-block">⟳</span>' : "○";
      const bg     = status === "completed" ? "#ecfdf3" : "#eff6ff";
      const border = status === "completed" ? "#86efac" : "#93c5fd";
      const timeChip = s?.duration_ms
        ? `<span style="margin-left:6px;padding:1px 6px;border-radius:4px;background:#f1f5f9;color:#64748b;font-size:10px;">${s.duration_ms} ms</span>` : "";
      const detail = s?.detail
        ? `<div style="margin-top:2px;font-size:11px;color:#64748b;">${escapeHtml(s.detail)}</div>` : "";
      return `<div style="padding:7px 10px;border:1px solid ${border};background:${bg};border-radius:8px;font-size:12px;color:#334155;">
        <div style="display:flex;align-items:center;gap:4px;">${icon} <strong>${escapeHtml(stage.label)}</strong>${timeChip}</div>
        ${detail}
      </div>`;
    }).join("");
  }

  function setStage(evt) {
    if (!evt || !evt.stage) return;
    const existing = progressMap.get(evt.stage) || {};
    const isCompleted = evt.status !== "started";
    progressMap.set(evt.stage, {
      status: isCompleted ? "completed" : "in_progress",
      duration_ms: isCompleted ? (evt.duration_ms || existing.duration_ms || null) : null,
      detail: evt.detail || existing.detail || "",
    });
    renderProgress();
    const stageLabel = evt.label || (progressOrder.find(p => p.key === evt.stage) || {}).label || "";
    if (evt.detail) setStatus(evt.detail, isCompleted ? "" : stageLabel);
  }

  // ── SSE stream consumer ────────────────────────────────────────────────────
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
      const parts = buffer.split("\n\n");
      buffer = parts.pop() || "";
      for (const part of parts) {
        const lines = part.split("\n").map(l => l.trim()).filter(l => l.startsWith("data:"));
        if (!lines.length) continue;
        const dataText = lines.map(l => l.slice(5).trim()).join("\n");
        if (!dataText) continue;
        try { onEvent(JSON.parse(dataText)); } catch { /* ignore malformed */ }
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
      latestLlmStatus = { available: false, provider: "llm", active_model: "-", hint: "Could not check LLM status." };
      renderLlmStatus(latestLlmStatus);
    }
  }

  // ── Main query runner ──────────────────────────────────────────────────────
  async function runQuery() {
    const question = (questionEl?.value || "").trim();
    const townlandHint = hintEl?.dataset.isAll ? null : (hintEl?.value || "").trim() || null;

    if (!question) { showError("Please enter a question."); return; }

    closeDropdown();
    showError("");
    renderWarnings([]);
    progressMap.clear();
    renderProgress();
    if (loadingBannerEl) { loadingBannerEl.style.display = "flex"; }
    if (loadingBannerTextEl) loadingBannerTextEl.textContent = "Starting query…";
    if (inlineStatusEl) { inlineStatusEl.style.display = "flex"; }
    setStatus("Starting query…");
    if (resultEl) resultEl.style.display = "none";
    if (submitEl) submitEl.disabled = true;
    latestResultPayload = null;
    setFeedbackStatus("", "muted");
    if (provenanceEl) provenanceEl.innerHTML = "";
    if (retrievalLaneEl) retrievalLaneEl.innerHTML = "";
    const _cbEl = $("askConfidenceBadge"); if (_cbEl) { _cbEl.innerHTML = ""; _cbEl.style.display = "none"; }
    const _gbEl = $("askGateBadge");       if (_gbEl) { _gbEl.innerHTML = ""; _gbEl.style.display = "none"; }
    const _vbEl = $("askVerifierBadge");   if (_vbEl) { _vbEl.innerHTML = ""; _vbEl.style.display = "none"; }
    if (explainContentEl) explainContentEl.innerHTML = "";
    if (suggestionsBlockEl) suggestionsBlockEl.style.display = "none";
    if (insightsBlockEl)    insightsBlockEl.style.display    = "none";
    if (chartBlockEl)       chartBlockEl.style.display       = "none";
    if (estateOverviewEl)   estateOverviewEl.style.display   = "none";

    let finalPayload = null;

    try {
      await consumeSSEPost(
        "/api/ask/query",
        { question, townland_hint: townlandHint },
        (evt) => {
          if (!evt || !evt.type) return;
          if (evt.type === "progress") { setStage(evt); return; }
          if (evt.type === "error") throw new Error(evt.message || "Unknown stream error.");
          if (evt.type === "result") finalPayload = evt;
        }
      );

      if (!finalPayload) throw new Error("No final result received.");

      const payload = finalPayload;

      // Primary: LLM plain-English summary — rendered as formatted HTML
      if (llmAnswerEl) {
        const rewrite = payload.llm_rephrased_answer || "";
        llmAnswerEl.innerHTML = rewrite
          ? markdownToHtml(rewrite)
          : `<span style="color:#94a3b8;font-style:italic;">No LLM summary available — see database result below.</span>`;
      }
      // Compact raw answer below table
      if (actualAnswerEl) {
        const raw = payload.actual_answer || payload.answer || "";
        actualAnswerEl.textContent = raw ? `Raw computed answer: ${raw}` : "";
        actualAnswerEl.style.display = raw ? "" : "none";
      }

      const isEstateWide = !payload.townland_context && !payload.townland_resolution?.name;
      renderWarnings(payload.warnings || []);
      renderKg(payload.kg_context || null);
      renderStructured(payload);
      renderEstateOverview(isEstateWide);
      renderLlmMeta(payload);
      renderProvenance(payload);
      renderExplainability(payload);
      renderTownlandResolution(payload.townland_resolution || null);
      renderSupportingContext(payload);
      renderSuggestions(payload);
      renderInsights(payload);

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
      if (loadingBannerEl) loadingBannerEl.style.display = "none";
      if (inlineStatusEl) inlineStatusEl.style.display = "none";
      if (submitEl) submitEl.disabled = false;
    }
  }

  // ── Feedback ───────────────────────────────────────────────────────────────
  function setFeedbackStatus(message, tone = "muted") {
    if (!feedbackStatusEl) return;
    const colors = tone === "success" ? { color: "#166534" } : tone === "error" ? { color: "#b42318" } : { color: "#475467" };
    feedbackStatusEl.style.color = colors.color;
    feedbackStatusEl.textContent = message || "";
  }

  async function sendFeedback(feedback) {
    if (!latestResultPayload) { setFeedbackStatus("Run a query first so there is something to rate.", "error"); return; }
    const structured  = latestResultPayload?.structured_output || {};
    const queries     = structured?.queries || {};
    const summary     = structured?.summary || {};
    const provenance  = latestResultPayload?.query_provenance || structured?.query_provenance || {};

    if (feedbackUpEl)   feedbackUpEl.disabled   = true;
    if (feedbackDownEl) feedbackDownEl.disabled = true;
    setFeedbackStatus("Saving feedback…", "muted");

    try {
      const res = await fetch("/api/ask/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question:          latestResultPayload.question,
          townland_hint:     latestResultPayload.townland_context || hintEl?.value || null,
          sql_text:          queries.local_sqlite_query || latestResultPayload.sql || "",
          vrti_postgres_sql: queries.vrti_postgresql_query || "",
          feedback,
          note:              (feedbackNoteEl?.value || "").trim() || null,
          result_row_count:  latestResultPayload.row_count || 0,
          availability_state: latestResultPayload?.availability?.state || null,
          llm_meta:          latestResultPayload.llm || {},
          reused_memory_id:  provenance?.reused_memory_id || null,
          sample_answer:     latestResultPayload.actual_answer || latestResultPayload.answer || "",
          summary_json:      summary || {},
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.error || `Feedback failed (${res.status})`);
      if (feedbackNoteEl) feedbackNoteEl.value = "";
      if (data?.stored_in_memory) {
        setFeedbackStatus("Thumbs up saved. This SQL pattern is now part of approved query memory.", "success");
      } else if (feedback === "down") {
        setFeedbackStatus("Thumbs down recorded. The system will treat this query pattern more cautiously.", "success");
      } else {
        setFeedbackStatus("Feedback saved.", "success");
      }
    } catch (err) {
      setFeedbackStatus(err?.message || "Could not save feedback.", "error");
    } finally {
      if (feedbackUpEl)   feedbackUpEl.disabled   = false;
      if (feedbackDownEl) feedbackDownEl.disabled = false;
    }
  }

  // ── Event listeners ────────────────────────────────────────────────────────
  if (submitEl)   submitEl.addEventListener("click", () => runQuery());
  if (questionEl) questionEl.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") runQuery();
  });

  if (feedbackUpEl)   feedbackUpEl.addEventListener("click",   () => sendFeedback("up"));
  if (feedbackDownEl) feedbackDownEl.addEventListener("click", () => sendFeedback("down"));

  document.querySelectorAll(".ask-example").forEach(btn => {
    btn.addEventListener("click", () => {
      const q = btn.getAttribute("data-q") || "";
      if (questionEl) { questionEl.value = q; questionEl.focus(); }
    });
  });

  // Close dropdown when clicking outside
  document.addEventListener("click", (e) => {
    if (hintEl && !hintEl.contains(e.target) && hintDropdownEl && !hintDropdownEl.contains(e.target)) {
      closeDropdown();
    }
  });

  // Initial checks
  checkLlmStatus();
});
