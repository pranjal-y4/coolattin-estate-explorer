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
  const townlandResolutionEl = $("askTownlandResolution");
  const warningsEl = $("askWarnings");
  const pdfLinkEl = $("askPdfLink");
  const kgBlockEl = $("askKgBlock");
  const kgContentEl = $("askKgContent");
  const localTableEl = $("askTable");
  const vrtiTableEl = $("askVrtiTable");
  const sqliteQueryEl = $("askSqliteQuery");
  const vrtiPgQueryEl = $("askVrtiPgQuery");
  const summaryEl = $("askSummary");
  const supportContextBlockEl = $("askSupportContextBlock");
  const supportContextEl = $("askSupportContext");
  const llmStatusEl = $("llmStatus") || $("ollamaStatus");

  const progressOrder = [
    { key: "contacting_llm", label: "Contacting LLM" },
    { key: "framing_query", label: "Framing Query" },
    { key: "querying_database", label: "Querying Database" },
    { key: "querying_vrti_graph", label: "Querying VRTI Graph" },
    { key: "preparing_output", label: "Preparing Output" },
  ];

  const progressMap = new Map();
  let latestLlmStatus = null;
  let townlandSuggestTimer = null;

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

    townlandResolutionEl.style.display = "block";
    townlandResolutionEl.innerHTML = `
      <div style="padding:10px 12px;border:1px solid #bae6fd;background:#f0f9ff;border-radius:10px;color:#075985;font-size:12px;line-height:1.5;">
        <strong>${escapeHtml(matchText)}</strong>
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

    const body = `<tbody>${rs
      .map((row) => {
        const cells = cols
          .map(
            (c) =>
              `<td style="padding:8px 10px;border-bottom:1px solid #f1f5f9;font-size:12px;color:#0f172a;vertical-align:top;">${escapeHtml(row[c])}</td>`
          )
          .join("");
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
        const icon = status === "completed" ? "✓" : status === "in_progress" ? "…" : "○";
        const bg = status === "completed" ? "#ecfdf3" : status === "in_progress" ? "#eff6ff" : "#f8fafc";
        const border = status === "completed" ? "#86efac" : status === "in_progress" ? "#93c5fd" : "#e2e8f0";
        const detail = s?.duration_ms ? ` (${s.duration_ms} ms)` : "";
        return `<div style="padding:7px 10px;border:1px solid ${border};background:${bg};border-radius:8px;font-size:12px;color:#334155;">
          <strong>${icon} ${escapeHtml(stage.label)}</strong>${escapeHtml(detail)}
        </div>`;
      })
      .join("");
  }

  function setStage(evt) {
    if (!evt || !evt.stage) return;
    progressMap.set(evt.stage, {
      status: evt.status === "started" ? "in_progress" : "completed",
      duration_ms: evt.duration_ms || null,
      detail: evt.detail || "",
    });
    renderProgress();
    if (evt.detail) setStatus(evt.detail);
  }

  function renderStructured(payload) {
    const structured = payload?.structured_output || {};
    const queries = structured?.queries || {};
    const processed = structured?.processed_tables || {};
    const summary = structured?.summary || {};

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
    llmStatusEl.innerHTML = `
      <div style="display:inline-flex;align-items:center;gap:8px;padding:7px 10px;border-radius:999px;background:${bg};border:1px solid ${border};color:${color};font-size:12px;font-weight:700;">
        <span>${escapeHtml(label)}</span>
        <span style="font-weight:600;">Provider: ${escapeHtml(provider)} | Model: ${escapeHtml(model)}</span>
      </div>
      ${hint ? `<div style="margin-top:5px;font-size:12px;color:#64748b;">${escapeHtml(hint)}</div>` : ""}
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
      if (actualAnswerEl) actualAnswerEl.textContent = payload.actual_answer || payload.answer || "";
      if (llmAnswerEl) {
        llmAnswerEl.textContent =
          payload.llm_rephrased_answer ||
          "LLM rewrite was not generated. The actual database answer and tables are still shown below.";
      }
      renderWarnings(payload.warnings || []);
      renderKg(payload.kg_context || null);
      renderStructured(payload);
      renderLlmMeta(payload);
      renderTownlandResolution(payload.townland_resolution || null);
      renderSupportingContext(payload);

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

  checkLlmStatus();
});
