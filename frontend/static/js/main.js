document.addEventListener("DOMContentLoaded", async () => {
  const $ = (id) => document.getElementById(id);

  const labelTips = {
    "Unique Id No": "Unique internal identifier assigned to each record.",
    "Record Id": "Original or source-specific identifier for the record.",
    "Nli Ref": "National Library of Ireland reference number for the source document.",
    "Year": "Year the record or event was created or recorded.",
    "Month": "Month the record or event was recorded.",
    "Court Session": "Court session during which the case or record was heard.",
    "Vol": "Volume number of the original source or register.",
    "Volume": "Volume number of the original source or register.",
    "Page": "Page number within the source volume.",
    "Estate Reference No": "Reference number assigned to the estate in the source records.",
    "Estate": "Name of the estate associated with the holding or tenant.",
    "Holding On Fitzw Estate": "Indicates whether the holding was on the Fitzwilliam estate.",
    "Holding On Estate": "Name or identifier of the estate on which the holding was located.",
    "Mountains In Common": "Indicates shared or common mountain land associated with the holding.",
    "Townland As Shown": "Townland name as it appears in the original record.",
    "Townland Official Name": "Standardised or modern official townland name.",
    "Townland": "Normalised townland name used for indexing or analysis.",
    "Parish": "Civil or ecclesiastical parish in which the townland is located.",
    "Surname": "Surname of the individual recorded.",
    "Forename": "Forename of the individual recorded.",
    "Canonical Name": "Standardised full name used for linking related records.",
    "Chief Tenant": "Normalised name of the chief tenant.",
    "Under Tenant": "Normalised name of the under-tenant.",
    "Chief Tenant Surname": "Normalised surname of the chief tenant.",
    "Chief Tenant Forename": "Normalised forename of the chief tenant.",
    "Under Tenant Surname": "Normalised surname of the under-tenant.",
    "Under Tenant Forename": "Normalised forename of the under-tenant.",
    "Chief Tenant Surname Original": "Chief tenant surname as written in the original source.",
    "Chief Tenant Forename Original": "Chief tenant forename as written in the original source.",
    "Under Tenant Surname Original": "Under-tenant surname as written in the original source.",
    "Under Tenant Forename Original": "Under-tenant forename as written in the original source.",
    "Household List": "List or summary of household members.",
    "Household List In Emigration Records": "Household members listed specifically in emigration records.",
    "Family Key": "Identifier linking individuals belonging to the same family unit.",
    "Role": "Individual's role within the household or record context.",
    "Relationship To Head Of Household": "Relationship of the individual to the household head.",
    "Gender": "Gender of the individual.",
    "Age": "Age of the individual at the time of the record.",
    "Age Head Of Household": "Age of the household head.",
    "Age Wife Widow Of Head Of Household": "Age of the wife or widow of the household head.",
    "Sons": "Number of sons in the household.",
    "Daughters": "Number of daughters in the household.",
    "Servants Male": "Number of male servants in the household.",
    "Servants Female": "Number of female servants in the household.",
    "Other Males In Household": "Number of other males living in the household.",
    "Other Famales In Household": "Number of other females living in the household.",
    "Occupation": "Occupation or trade of the individual.",
    "Legal Action": "Legal action associated with the individual (e.g., eviction, tenancy dispute).",
    "Acres": "Size of the holding in acres as recorded.",
    "Acres 2": "Alternate acreage figure from a secondary source.",
    "Acres Irish": "Size of holding measured in Irish acres.",
    "Acres English": "Size of holding measured in English statute acres.",
    "Rent Owed": "Rent amount owed by the tenant.",
    "Arrears": "Outstanding rent arrears at the time of the record.",
    "Name Of Ship": "Name of the ship used for emigration.",
    "Ship Name": "Name of the ship used for emigration.",
    "Place And Date Of Departure": "Port and date of departure.",
    "Departure": "Port and date of departure.",
    "Place And Date Of Arrival": "Port and date of arrival.",
    "Arrival": "Port and date of arrival.",
    "Has Emigration Record": "Indicates whether an emigration record exists for the individual.",
    "Has Eviction Record": "Indicates whether an eviction record exists.",
    "Has Tenancy Record": "Indicates whether a tenancy record exists.",
    "Flag Multiple Surnames": "Flags records containing multiple surnames.",
    "Has Workhouse Record": "Indicates whether possible workhouse records were found for this person.",
    "Workhouse Record Count": "Number of potential workhouse entries linked to this person.",
    "Comments": "Additional notes or contextual information from the source."
  };

  const state = {
    records: [],
    townlandIndex: {},
    familyGroups: {},
    activeGroups: []
  };

  const modal = $("oldRecordModal");
  const closeBtn = $("closeModal");
  const recordModal = $("recordDetailModal");
  const closeRecordBtn = $("closeRecordModal");

  const glossaryModal = $("glossaryModal");
  const closeGlossaryBtn = $("closeGlossaryModal");
  const openGlossaryBtn = $("openGlossaryBtn");

  if (openGlossaryBtn) {
    openGlossaryBtn.onclick = () => {
      const gBody = $("glossaryModalBody");
      gBody.innerHTML = Object.entries(labelTips).map(([term, desc]) => `
        <div style="margin-bottom:12px; border-bottom:1px solid #e2e8f0; padding-bottom:8px;">
          <div style="font-weight:700; color:#0f172a; margin-bottom:2px;">${term}</div>
          <div style="font-size:13px; color:#475569;">${desc}</div>
        </div>
      `).join("");
      openModal(glossaryModal);
    };
  }

  if (closeGlossaryBtn) closeGlossaryBtn.onclick = () => closeModal(glossaryModal);

  function openModal(el) {
    el.style.display = "flex";
    el.classList.add("active");
  }

  function closeModal(el) {
    el.style.display = "none";
    el.classList.remove("active");
  }

  if (closeBtn) closeBtn.onclick = () => closeModal(modal);
  if (closeRecordBtn) closeRecordBtn.onclick = () => closeModal(recordModal);
  window.addEventListener("click", (e) => {
    if (e.target === modal) closeModal(modal);
    if (e.target === recordModal) closeModal(recordModal);
    if (e.target === glossaryModal) closeModal(glossaryModal);
  });

  function titleCaseKey(key) {
    return key
      .replace(/_/g, " ")
      .replace(/\s+/g, " ")
      .trim()
      .split(" ")
      .map((p) => p.charAt(0).toUpperCase() + p.slice(1))
      .join(" ");
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function normalizeYear(v) {
    if (v === null || v === undefined || v === "") return null;
    const n = Number(v);
    if (Number.isNaN(n)) return null;
    return Math.trunc(n);
  }

  function canonicalTownland(townland) {
    if (!townland) return null;
    const raw = String(townland).trim();
    if (state.townlandIndex[raw]) return raw;
    const lower = raw.toLowerCase();
    const found = Object.keys(state.townlandIndex).find((k) => k.toLowerCase() === lower);
    return found || null;
  }

  function canonicalName(rec) {
    const forename = (rec.forename || "").trim();
    let surname = (rec.surname || "").trim();
    // Treat non-letter-only surnames (blank, numeric, punctuation, etc.) as Unknown
    if (!surname || !/^[A-Za-z\s'-]+$/.test(surname)) surname = "Unknown";
    if (rec.canonical_name && rec.canonical_name.trim()) return rec.canonical_name.trim();
    return `${forename} ${surname}`.trim() || "Unknown";
  }

  function setSurnameError(msg) {
    const el = $("surnameError");
    if (!el) return;
    if (!msg) {
      el.style.display = "none";
      el.textContent = "";
      return;
    }
    el.style.display = "block";
    el.textContent = msg;
  }

  function hasSurnameInContext(inputSurname, selectedTownland = "") {
    const s = String(inputSurname || "").trim().toLowerCase();
    const tl = String(selectedTownland || "").trim().toLowerCase();
    if (!s) return true;
    return state.records.some((r) => {
      if ((r.surname || "").toLowerCase() !== s) return false;
      if (!tl) return true;
      return (r.townland || "").toLowerCase() === tl;
    });
  }

  function tooltipFor(label) {
    return labelTips[label] || "Field from the unified Coolattin dataset.";
  }

  function formatRow(label, value) {
    const renderedValue = label === "Townland" ? `<strong>${value}</strong>` : value;
    return `
      <div style="grid-column: span 1;">
        <span class="field-label" style="font-size:11px;color:#64748b;text-transform:uppercase;cursor:default;display:inline-flex;align-items:center;gap:6px;">
          ${label}
          <span class="tooltip-icon" data-tooltip="${tooltipFor(label)}" aria-label="${tooltipFor(label)}">?</span>
        </span>
        <div style="font-size:13px;color:#1e293b;">${renderedValue}</div>
      </div>
    `;
  }

  function parseHouseholdGivenNames(value) {
    const raw = String(value || "").trim();
    if (!raw) return [];
    return raw
      .split(/[;,]|\band\b/gi)
      .map((s) => s.trim())
      .map((s) => s.replace(/\s+/g, " "))
      .filter(Boolean)
      .filter((s) => /^[A-Za-z .'-]+$/.test(s));
  }

  function buildHouseholdListLinks(rec) {
    const surname = (rec.surname || "").trim();
    const townland = (rec.townland || "").trim().toLowerCase();
    const items = parseHouseholdGivenNames(rec.household_list);
    if (!surname || !items.length) return [];

    const links = [];
    const seen = new Set();
    for (const givenName of items) {
      const full = `${givenName} ${surname}`.trim().toLowerCase();
      const match = state.records.find((r) => {
        const candidate = `${r.forename || ""} ${r.surname || ""}`.trim().toLowerCase();
        if (candidate !== full) return false;
        // Prefer same townland when available
        if (townland) return !!r.has_emigration_record && (r.townland || "").trim().toLowerCase() === townland;
        return !!r.has_emigration_record;
      }) || state.records.find((r) => !!r.has_emigration_record && `${r.forename || ""} ${r.surname || ""}`.trim().toLowerCase() === full);

      if (match && !seen.has(String(match.record_id))) {
        seen.add(String(match.record_id));
        links.push({
          record_id: String(match.record_id),
          label: `${givenName} ${surname}`,
        });
      }
    }
    return links;
  }

  function createRecordCard(rec) {
    const normalized = { ...rec };
    const chiefTenantName = `${rec.chief_tenant_forename || ""} ${rec.chief_tenant_surname || ""}`.trim();
    const underTenantName = `${rec.under_tenant_forename || ""} ${rec.under_tenant_surname || ""}`.trim();
    if (chiefTenantName) normalized.chief_tenant = chiefTenantName;
    if (underTenantName) normalized.under_tenant = underTenantName;

    const preferredOrder = [
      "record_id",
      "surname",
      "forename",
      "townland",
      "year",
      "month",
      "estate",
      "role",
      "chief_tenant",
      "under_tenant",
      "household_list",
      "mountains_in_common",
      "legal_action",
      "occupation",
      "parish",
      "age",
      "nli_ref",
      "volume",
      "page",
      "acres_irish",
      "acres_english",
      "rent_owed",
      "arrears",
      "ship_name",
      "departure",
      "arrival",
      "comments"
    ];

    const hiddenKeys = new Set([
      "family_key",
      "flag_multiple_surnames",
      "canonical_name",
      "chief_tenant_forename",
      "chief_tenant_surname",
      "under_tenant_forename",
      "under_tenant_surname"
    ]);

    const alwaysShowKeys = new Set([
      "record_id",
      "surname",
      "forename",
      "townland",
      "year",
      "month",
      "estate",
      "parish",
      "role",
      "chief_tenant",
      "under_tenant",
      "household_list",
      "mountains_in_common",
      "has_tenancy_record",
      "has_emigration_record",
      "has_eviction_record",
      "has_workhouse_record",
      "workhouse_record_count",
      "legal_action",
      "occupation",
      "gender",
      "age",
      "comments"
    ]);

    const dynamicRest = Object.keys(normalized)
      .filter((k) => !preferredOrder.includes(k) && !hiddenKeys.has(k))
      .sort((a, b) => a.localeCompare(b));

    const fullOrder = [...preferredOrder, ...dynamicRest];

    const rows = [];
    const seen = new Set();
    for (const key of fullOrder) {
      if (seen.has(key)) continue;
      if (!(key in normalized)) continue;
      let value = normalized[key];
      if (key === "year") value = normalizeYear(value);
      if (key === "townland" && (value === null || value === undefined || value === "")) {
        value = "Unknown";
      }

      if (key === "mountains_in_common") {
        if (value === null || value === undefined || value === "") {
          continue;
        } else if (value === true || value === "true" || value === 1 || value === "1") {
          // Find other townlands in the data that share this mountain common
          const thisTownland = (normalized.townland || "").trim();
          const mountainNeighbours = state.records
            .filter(r =>
              r.record_id !== normalized.record_id &&
              (r.mountains_in_common === true || r.mountains_in_common === "true" || r.mountains_in_common === 1)
            )
            .map(r => (r.townland || "").trim())
            .filter(t => t && t !== thisTownland);
          const uniqueNeighbours = [...new Set(mountainNeighbours)].slice(0, 5);
          value = uniqueNeighbours.length > 0
            ? `Yes — shared with: ${uniqueNeighbours.join(", ")}`
            : "Yes";
        } else {
          continue;
        }
      }

      if (["has_tenancy_record", "has_emigration_record", "has_eviction_record", "has_workhouse_record"].includes(key)) {
        value = value ? "Yes" : "No";
      }

      if (value === null || value === undefined || value === "" || value === "-") {
        continue;
      }
      seen.add(key);

      let label = titleCaseKey(key);
      if (label === "Chief Tenant") label = "Chief Tenant";
      if (label === "Under Tenant") label = "Under Tenant";

      if (key === "chief_tenant") {
        const ctSurname = (rec.chief_tenant_surname || "").trim();
        if (ctSurname && /^[A-Za-z\s'-]+$/.test(ctSurname)) {
          const safeSurname = ctSurname.replace(/\\/g, "\\\\").replace(/'/g, "\\'");
          const safeDisplay = escapeHtml(String(value));
          rows.push(formatRow(label, `<button style="background:none;border:none;color:#1d4ed8;font-weight:600;cursor:pointer;font-size:inherit;padding:0;text-decoration:underline;" onclick="window.searchSurname('${safeSurname}')">👤 ${safeDisplay}</button>`));
          continue;
        }
      }

      rows.push(formatRow(label, String(value)));
    }

    return `
      <div class="record-detail-card" style="background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:12px;margin-bottom:8px;">
        <div style="font-size:11px;color:#64748b;text-align:right;margin-bottom:6px;font-style:italic;">Only information known that is shown</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">${rows.join("")}</div>
      </div>
    `;
  }

  function sourceTagsFromRecords(records) {
    const hasTenancy = records.some((r) => !!r.has_tenancy_record);
    const hasEmigration = records.some((r) => !!r.has_emigration_record);
    const hasEviction = records.some((r) => !!r.has_eviction_record);
    const hasWorkhouse = records.some((r) => !!r.has_workhouse_record);
    const tags = [];
    if (hasTenancy) tags.push("Tenancy");
    if (hasEmigration) tags.push("Emigration");
    if (hasEviction) tags.push("Eviction");
    if (hasWorkhouse) tags.push("Workhouse");
    return tags;
  }

  function sourceTagHTML(records, groupIdx = null) {
    const tags = sourceTagsFromRecords(records);
    if (!tags.length) return `<span style="font-size:11px;color:#64748b;">Source: Unknown</span>`;
    return tags
      .map((tag) => {
        const color = tag === "Tenancy" ? "#1d4ed8" : tag === "Emigration" ? "#0f766e" : tag === "Eviction" ? "#b91c1c" : "#7c3aed";
        const clickable = (tag === "Emigration" || tag === "Workhouse") && Number.isInteger(groupIdx);
        if (clickable) {
          const sourceType = tag.toLowerCase();
          return `<button style="font-size:10px;font-weight:700;padding:2px 8px;border-radius:999px;background:#f8fafc;border:1px solid #dbeafe;color:${color};margin-left:6px;cursor:pointer;" title="Open ${tag} records" onclick="event.stopPropagation(); window.openSourceDetails(${groupIdx}, '${sourceType}')">${tag}</button>`;
        }
        return `<span style="font-size:10px;font-weight:700;padding:2px 8px;border-radius:999px;background:#f8fafc;border:1px solid #dbeafe;color:${color};margin-left:6px;">${tag}</span>`;
      })
      .join("");
  }

  function emigrationSectionHTML(records) {
    const emRows = (records || []).filter((r) => !!r.has_emigration_record || !!r.ship_name || !!r.departure || !!r.arrival || !!r.household_list);
    if (!emRows.length) {
      return "<div style='padding:10px;color:#64748b;'>No emigration records in this selection.</div>";
    }
    return `
      <div style="margin-top:8px;">
        <div style="font-size:12px;color:#0f766e;font-weight:800;margin-bottom:8px;">Emigration Records (${emRows.length})</div>
        ${emRows.map((r) => `${createRecordCard(r)}${renderHouseholdLinks(r)}`).join("")}
      </div>
    `;
  }

  window.openSourceDetails = async function (idx, sourceType) {
    const group = state.activeGroups[idx];
    if (!group) return;

    const sourceLabel = sourceType === "workhouse" ? "Workhouse" : sourceType === "emigration" ? "Emigration" : "Source";
    $("recordModalTitle").innerText = `${group.label} - ${sourceLabel} Details`;
    $("recordModalBody").innerHTML = "<div style='padding:20px;text-align:center;color:#64748b;'>Loading source details...</div>";
    openModal(recordModal);

    if (sourceType === "emigration") {
      $("recordModalBody").innerHTML = emigrationSectionHTML(group.records);
      return;
    }

    if (sourceType === "workhouse") {
      const ordered = group.records
        .slice()
        .sort((a, b) => (normalizeYear(a.year) || 0) - (normalizeYear(b.year) || 0));

      const chunks = await Promise.all(
        ordered.map(async (r) => {
          const matchBundle = await fetchWorkhouseMatches(r.record_id);
          if (!(matchBundle.count || 0)) return "";
          return `
            <div style="margin-bottom:10px;">
              <div style="font-size:12px;font-weight:800;color:#334155;margin:6px 0;">${canonicalName(r)} (Record ${r.record_id})</div>
              ${workhouseSectionHTML(matchBundle)}
            </div>
          `;
        })
      );

      const html = chunks.join("");
      $("recordModalBody").innerHTML = html || "<div style='padding:20px;color:#64748b;'>No workhouse records in this selection.</div>";
      return;
    }

    $("recordModalBody").innerHTML = "<div style='padding:20px;color:#64748b;'>Unsupported source type.</div>";
  };

  function yearSummaryHTML(records) {
    const years = [...new Set((records || [])
      .map((r) => normalizeYear(r.year))
      .filter((y) => Number.isInteger(y)))].sort((a, b) => a - b);
    if (!years.length) return `<span style="font-size:11px;color:#64748b;margin-left:8px;">Year: -</span>`;
    if (years.length === 1) return `<span style="font-size:11px;color:#64748b;margin-left:8px;">Year: ${years[0]}</span>`;
    return `<span style="font-size:11px;color:#64748b;margin-left:8px;">Years: ${years[0]}-${years[years.length - 1]}</span>`;
  }

  async function fetchWorkhouseMatches(recordId) {
    try {
      const res = await fetch(`/api/workhouse/match/${encodeURIComponent(recordId)}`);
      if (!res.ok) return { count: 0, matches: [], linked_workhouse_records: [], please_check_records: [] };
      const js = await res.json();
      return {
        count: Number(js.count || 0),
        matches: Array.isArray(js.matches) ? js.matches : [],
        linked_workhouse_records: Array.isArray(js.linked_workhouse_records) ? js.linked_workhouse_records : [],
        possible_workhouse_matches: Array.isArray(js.possible_workhouse_matches) ? js.possible_workhouse_matches : [],
        please_check_records: Array.isArray(js.please_check_records) ? js.please_check_records : [],
        identity_is_ambiguous: !!js.identity_is_ambiguous,
        identity_disambiguation_note: js.identity_disambiguation_note || "",
      };
    } catch (_e) {
      return { count: 0, matches: [], linked_workhouse_records: [], please_check_records: [] };
    }
  }

  function workhouseSectionHTML(bundle) {
    const confirmed = Array.isArray(bundle?.linked_workhouse_records) ? bundle.linked_workhouse_records : [];
    const possible = Array.isArray(bundle?.please_check_records)
      ? bundle.please_check_records
      : Array.isArray(bundle?.possible_workhouse_matches)
        ? bundle.possible_workhouse_matches
        : [];
    const legacyMatches = Array.isArray(bundle?.matches) ? bundle.matches : [];
    const total = confirmed.length + possible.length || legacyMatches.length;
    if (!total) return "";

    const tierOrder = { High: 0, Medium: 1, Low: 2 };
    const tierLabel = { High: "High confidence", Medium: "Medium confidence", Low: "Low confidence" };

    if (!confirmed.length && !possible.length) {
      const matches = legacyMatches;
      // Group by confidence tier
      const groups = {};
      for (const m of matches) {
        const tier = m.confidence || "Low";
        if (!groups[tier]) groups[tier] = [];
        groups[tier].push(m);
      }

      const tiers = Object.keys(groups).sort((a, b) => (tierOrder[a] ?? 3) - (tierOrder[b] ?? 3));

      const rows = tiers.map((tier) => {
        const header = `<div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:#94a3b8;margin:8px 0 4px;">${tierLabel[tier] || tier}</div>`;
        const cards = groups[tier].map((m) => {
          const ed = m.electoral_division || "-";
          const basis = m.match_basis || "name";
          const scoreStr = m.name_score != null ? ` · Score: ${(m.name_score * 100).toFixed(0)}%` : "";
          return `
            <div style="padding:8px;border:1px solid #e2e8f0;border-radius:8px;background:#faf5ff;margin-bottom:6px;">
              <div style="display:flex;align-items:center;gap:6px;margin-bottom:2px;">
                <span class="wh-confidence wh-${tier}">${tier}</span>
                <span style="font-weight:700;color:#4c1d95;">${m.raw_name || "Unknown"}</span>
              </div>
              <div style="font-size:11px;color:#475569;">Sheet: ${m.source_sheet || "-"} · Match: ${basis}${scoreStr}</div>
              <div style="font-size:11px;color:#475569;">Electoral Division: ${ed}</div>
              <div style="font-size:11px;color:#475569;">Sex: ${m.sex || "-"} · Age: ${m.age || "-"} · Status: ${m.status || "-"}</div>
              <div style="font-size:11px;color:#475569;">Employment: ${m.employment || "-"} · Religion: ${m.religion || "-"}</div>
              <div style="font-size:11px;color:#475569;">Admitted/Born: ${m.admitted_or_born || "-"} · Left/Died: ${m.died_or_left || "-"}</div>
              ${m.register_number ? `<div style="font-size:11px;color:#475569;">Register #: ${m.register_number}</div>` : ""}
            </div>
          `;
        }).join("");
        return header + cards;
      }).join("");

      return `
        <div style="margin-top:10px;padding-top:10px;border-top:1px dashed #cbd5e1;">
          <div style="font-size:12px;color:#4c1d95;font-weight:800;">Workhouse Records (${matches.length} fuzzy match${matches.length !== 1 ? "es" : ""})</div>
          <div style="font-size:11px;color:#78350f;background:#fefce8;border:1px solid #fde68a;border-radius:6px;padding:6px 8px;margin:4px 0 8px;line-height:1.5;">
            <strong>Approximate matches only</strong> — these records were identified by name similarity (fuzzy matching) and cannot be guaranteed to refer to the same person. Confidence tiers indicate match quality. Always verify against the original source before drawing conclusions.
          </div>
          <div style="margin-top:6px;">${rows}</div>
        </div>
      `;
    }

    const groups = {};
    const renderCandidateCard = (m, accent, label) => {
      const evidence = Array.isArray(m.why_it_matched) ? m.why_it_matched.join("; ") : (m.match_basis || "Name-based candidate");
      const missing = Array.isArray(m.what_evidence_is_missing) && m.what_evidence_is_missing.length
        ? m.what_evidence_is_missing.join("; ")
        : "Further corroborating evidence needed";
      const conflicts = Array.isArray(m.conflicting_evidence) && m.conflicting_evidence.length
        ? m.conflicting_evidence.join("; ")
        : "None recorded";
      const scoreStr = m.confidence_score != null ? ` · Score: ${(Number(m.confidence_score) * 100).toFixed(0)}%` : "";
      return `
        <div style="padding:10px;border:1px solid #e2e8f0;border-radius:8px;background:${accent};margin-bottom:8px;">
          <div style="display:flex;align-items:center;gap:6px;margin-bottom:4px;">
            <span class="wh-confidence wh-${m.confidence || "Low"}">${m.confidence || label}</span>
            <span style="font-weight:700;color:#334155;">${m.name || m.raw_name || "Unknown"}</span>
          </div>
          <div style="font-size:11px;color:#475569;">Place: ${m.place || m.electoral_division || "-"} · Year: ${m.year || "-"} · Age: ${m.age || "-"}${scoreStr}</div>
          <div style="font-size:11px;color:#475569;">Why it matched: ${evidence}</div>
          <div style="font-size:11px;color:#475569;">What evidence is missing: ${missing}</div>
          <div style="font-size:11px;color:#475569;">Conflicting evidence: ${conflicts}</div>
          <div style="font-size:11px;color:#475569;">Source record: ${m.source_record_id || "-"}</div>
        </div>
      `;
    };

    return `
      <div style="margin-top:10px;padding-top:10px;border-top:1px dashed #cbd5e1;">
        <div style="font-size:12px;color:#4c1d95;font-weight:800;">Workhouse Resolution (${total} linked or reviewable record${total !== 1 ? "s" : ""})</div>
        <div style="font-size:11px;color:#78350f;background:#fefce8;border:1px solid #fde68a;border-radius:6px;padding:6px 8px;margin:4px 0 8px;line-height:1.5;">
          <strong>Review carefully</strong> — confirmed links have the strongest available evidence, while possible links are surfaced for manual checking rather than silently merged.
        </div>
        ${bundle.identity_is_ambiguous ? `<div style="font-size:11px;color:#9a3412;background:#fff7ed;border:1px solid #fdba74;border-radius:6px;padding:6px 8px;margin:0 0 8px;">${bundle.identity_disambiguation_note || "Multiple plausible workhouse matches were found for this identity."}</div>` : ""}
        ${confirmed.length ? `<div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:#15803d;margin:8px 0 4px;">Confirmed linked workhouse records</div>${confirmed.map((m) => renderCandidateCard(m, "#f0fdf4", "Confirmed")).join("")}` : ""}
        ${possible.length ? `<div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:#92400e;margin:10px 0 4px;">Please check these records</div>${possible.map((m) => renderCandidateCard(m, "#fff7ed", "Please check")).join("")}` : ""}
      </div>
    `;
  }

  function renderRecordWithLinks(rec) {
    return `${createRecordCard(rec)}${renderHouseholdLinks(rec)}`;
  }

  function buildIndexes(records) {
    state.townlandIndex = {};
    state.familyGroups = {};

    records.forEach((rec) => {
      const tl = rec.townland && String(rec.townland).trim();
      if (!tl) return;
      if (!state.townlandIndex[tl]) {
        state.townlandIndex[tl] = {
          chiefTenants: {},
          underTenants: {},
          families: {}
        };
      }
      const node = state.townlandIndex[tl];

      const surname = (rec.surname || "").trim();
      const forename = (rec.forename || "").trim();
      const personName = canonicalName(rec);

      const chiefName = `${rec.chief_tenant_forename || ""} ${rec.chief_tenant_surname || ""}`.trim();
      const underName = `${rec.under_tenant_forename || ""} ${rec.under_tenant_surname || ""}`.trim();
      const role = (rec.role || "").toLowerCase();

      const inferredChief = chiefName || (!underName ? personName : "Unknown Chief Tenant");
      if (!node.chiefTenants[inferredChief]) {
        node.chiefTenants[inferredChief] = { name: inferredChief, records: [], underTenants: {} };
      }
      node.chiefTenants[inferredChief].records.push(rec);

      if (underName || role.includes("under")) {
        const uName = underName || personName;
        if (!node.chiefTenants[inferredChief].underTenants[uName]) {
          node.chiefTenants[inferredChief].underTenants[uName] = { name: uName, records: [] };
        }
        node.chiefTenants[inferredChief].underTenants[uName].records.push(rec);
      }

      // For tenancy-survey records, surname is blank but chief_tenant_surname
      // holds the person's actual family name — use it as fallback so the
      // "Surnames" view is populated (e.g. Aghowle Lower 1842 survey).
      const skipCTNames = new Set(["common grazing", "house lot"]);
      const ctSn = (rec.chief_tenant_surname || "").trim();
      const ctFn = (rec.chief_tenant_forename || "").trim();
      const ctValid = ctSn
        && /^[A-Za-z\s'-]+$/.test(ctSn)
        && !skipCTNames.has(ctSn.toLowerCase())
        && ctFn && ctFn !== "-" && ctFn !== "Multiple Chief Tenants";
      const effectiveSurname = surname || (ctValid ? ctSn : "");

      if (effectiveSurname) {
        if (!node.families[effectiveSurname]) {
          node.families[effectiveSurname] = { surname: effectiveSurname, records: [], fromChiefTenant: !surname };
        }
        node.families[effectiveSurname].records.push(rec);
      }

      if (surname) {
        const famKey = `${surname.toLowerCase()}|${tl.toLowerCase()}`;
        if (!state.familyGroups[famKey]) state.familyGroups[famKey] = [];
        state.familyGroups[famKey].push(rec);
      }
    });
  }

  function renderHouseholdLinks(rec) {
    const householdLinks = buildHouseholdListLinks(rec);
    const surname = (rec.surname || "").trim();
    const townland = (rec.townland || "").trim();
    const famKey = surname && townland ? `${surname.toLowerCase()}|${townland.toLowerCase()}` : null;
    const linked = famKey ? (state.familyGroups[famKey] || []).filter((r) => r.record_id !== rec.record_id) : [];

    if (!linked.length && !householdLinks.length) return "";

    const householdListSection = householdLinks.length
      ? `
        <div style="margin-top:10px;padding-top:10px;border-top:1px dashed #cbd5e1;">
          <div style="font-size:12px;color:#334155;font-weight:700;" title="Names extracted from household list and linked to unified records.">Household List</div>
          <div style="font-size:11px;color:#64748b;margin-bottom:4px;">Source: Emigration record household list</div>
          <div style="margin-top:6px;display:flex;flex-wrap:wrap;gap:6px;">
            ${householdLinks.map((m) => `<button style="border:1px solid #3b82f6;background:#eff6ff;padding:4px 10px;border-radius:999px;font-size:11px;cursor:pointer;color:#1d4ed8;font-weight:600;" onclick="window.openRecordById('${m.record_id}')" title="Click to open full record">👤 ${m.label}</button>`).join("")}
          </div>
        </div>
      `
      : "";

    const familySection = linked.length
      ? `
      <div style="margin-top:10px;padding-top:10px;border-top:1px dashed #cbd5e1;">
        <div style="font-size:12px;color:#334155;font-weight:700;">Household Members / Family Links</div>
        <div style="font-size:11px;color:#64748b;margin-bottom:4px;">Source: Records sharing same surname and townland</div>
        <div style="margin-top:6px;display:flex;flex-wrap:wrap;gap:6px;">
          ${linked.slice(0, 12).map((r) => {
        const label = canonicalName(r);
        const sourceHint = r.has_emigration_record ? "Emigration" : r.has_tenancy_record ? "Tenancy" : r.has_eviction_record ? "Eviction" : "";
        return `<button style="border:1px solid #cbd5e1;background:#fff;padding:4px 8px;border-radius:999px;font-size:11px;cursor:pointer;" onclick="window.openRecordById('${r.record_id}')" title="${sourceHint ? 'Source: ' + sourceHint : ''}">👤 ${label}${sourceHint ? ' <span style=\'font-size:9px;color:#64748b;\'>[' + sourceHint + ']</span>' : ''}</button>`;
      }).join("")}
        </div>
      </div>
      `
      : "";

    return `
      ${familySection}
      ${householdListSection}
    `;
  }

  window.searchSurname = function (surname) {
    if (!surname) return;
    renderSurnameResults(surname);
  };

  window.openRecordById = function (recordId) {
    const rec = state.records.find((r) => String(r.record_id) === String(recordId));
    if (!rec) return;

    const forename = (rec.forename || "").trim().toLowerCase();
    const surname  = (rec.surname  || "").trim().toLowerCase();
    const hasName  = forename || (surname && surname !== "unknown");

    // Collect ALL records for the same person across every source
    const allForPerson = hasName
      ? state.records.filter(r => {
          const rf = (r.forename || "").trim().toLowerCase();
          const rs = (r.surname  || "").trim().toLowerCase();
          return rf === forename && rs === surname;
        })
      : [rec];

    const personLabel = canonicalName(rec);
    const body = $("recordModalBody");
    $("recordModalTitle").innerText = allForPerson.length > 1
      ? `${personLabel} — All Records (${allForPerson.length})`
      : `${personLabel} — Record Details`;

    body.innerHTML = "<div style='padding:20px;text-align:center;color:#64748b;'>Loading records…</div>";
    openModal(recordModal);

    const ordered = allForPerson.slice().sort((a, b) => (normalizeYear(a.year) || 0) - (normalizeYear(b.year) || 0));

    Promise.all(
      ordered.map(async (r) => {
        const matchBundle = await fetchWorkhouseMatches(r.record_id);
        return `${createRecordCard(r)}${renderHouseholdLinks(r)}${workhouseSectionHTML(matchBundle)}`;
      })
    ).then((chunks) => {
      const tags = sourceTagsFromRecords(allForPerson);
      const sourceList = tags.length ? tags.join(", ") : "Unknown";
      const headerNote = allForPerson.length > 1
        ? `<div style="padding:10px 14px;background:#f0fdf4;border-bottom:2px solid #bbf7d0;margin-bottom:10px;font-size:13px;color:#14532d;">
            <strong>${allForPerson.length} records</strong> found for <strong>${personLabel}</strong> across: <strong>${sourceList}</strong>
            <div style="font-size:11px;color:#15803d;margin-top:2px;">Records span all tenancies, emigrations, evictions and workhouse matches linked to this name.</div>
          </div>`
        : "";
      body.innerHTML = headerNote + (chunks.join("") || "<div style='padding:20px;text-align:center;'>No records found.</div>");
    });
  };

  window.openGroupDetails = async function (idx) {
    const group = state.activeGroups[idx];
    if (!group) return;
    $("recordModalTitle").innerText = `${group.label} (${group.records.length} Records)`;
    $("recordModalBody").innerHTML = "<div style='padding:20px;text-align:center;color:#64748b;'>Loading detailed records...</div>";
    openModal(recordModal);

    const ordered = group.records
      .slice()
      .sort((a, b) => (normalizeYear(a.year) || 0) - (normalizeYear(b.year) || 0));

    const chunks = await Promise.all(
      ordered.map(async (r) => {
        const matchBundle = await fetchWorkhouseMatches(r.record_id);
        return `${createRecordCard(r)}${renderHouseholdLinks(r)}${workhouseSectionHTML(matchBundle)}`;
      })
    );

    const cards = chunks.join("");
    $("recordModalBody").innerHTML = cards || "<div style='padding:20px;text-align:center;'>No records found.</div>";
  };

  window.toggleExpanded = function (el, groupIdx) {
    const panel = el.nextElementSibling;
    if (!panel) return;
    const isOpen = panel.style.display === "block";
    panel.style.display = isOpen ? "none" : "block";
    // Lazy rendering: only fill data-lazy-group placeholders on first open
    if (!isOpen && panel.hasAttribute("data-lazy-group") && panel.textContent.trim() === "") {
      const idx = groupIdx !== undefined ? groupIdx : Number(panel.getAttribute("data-lazy-group"));
      const group = state.activeGroups[idx];
      if (group) {
        panel.innerHTML = group.records.slice(0, 4).map(renderRecordWithLinks).join("");
      }
    }
  };

  function renderTownlandPanel(name) {
    const key = canonicalTownland(name);
    const box = $("detailsContent");
    if (!key || !state.townlandIndex[key]) {
      box.innerHTML = `<div style='color:#64748b;'>No data for <strong>${name}</strong>.</div>`;
      return;
    }
    const data = state.townlandIndex[key];
    const chiefs = Object.keys(data.chiefTenants).length;
    const families = Object.keys(data.families).length;
    const censusUrl = `/census?townland=${encodeURIComponent(key)}`;
    const safeKey = key.replace(/'/g, "\\'");

    box.innerHTML = `
      <h3 style="margin:0 0 3px 0;font-family:'Cormorant Garamond',Georgia,serif;font-size:22px;font-weight:700;color:var(--ink);line-height:1.2;">${key}</h3>
      <div style="font-size:11px;text-transform:uppercase;letter-spacing:1.2px;color:var(--brass);margin-bottom:16px;font-weight:600;">County Wicklow Townland</div>

      <div style="display:flex;gap:10px;margin-bottom:14px;">
        <button style="flex:1;background:var(--forest);color:#fff;border:0;border-radius:10px;padding:16px 10px;cursor:pointer;transition:opacity .15s,transform .15s;" onmouseover="this.style.opacity='.88';this.style.transform='translateY(-1px)'" onmouseout="this.style.opacity='1';this.style.transform='none'" onclick="window.openTownlandView('${safeKey}', 'tenants')">
          <div style="font-size:28px;font-weight:700;font-family:'Cormorant Garamond',serif;line-height:1;">${chiefs}</div>
          <div style="font-size:13px;font-weight:600;letter-spacing:.04em;margin-top:4px;">Chief Tenants</div>
        </button>
        <button style="flex:1;background:var(--moss);color:#fff;border:0;border-radius:10px;padding:16px 10px;cursor:pointer;transition:opacity .15s,transform .15s;" onmouseover="this.style.opacity='.88';this.style.transform='translateY(-1px)'" onmouseout="this.style.opacity='1';this.style.transform='none'" onclick="window.openTownlandView('${safeKey}', 'families')">
          <div style="font-size:28px;font-weight:700;font-family:'Cormorant Garamond',serif;line-height:1;">${families}</div>
          <div style="font-size:13px;font-weight:600;letter-spacing:.04em;margin-top:4px;">Surnames</div>
        </button>
      </div>

      <!-- Under-tenant guidance note -->
      <div style="margin-bottom:12px;padding:10px 12px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;font-size:11px;color:#475569;line-height:1.5;">
        <strong style="color:#334155;">Under Tenants:</strong>
        Where recorded, under-tenant names are listed inside the <em>Chief Tenants</em> view.
        Click <strong>Chief Tenants</strong> above, then expand any individual tenant row to see their sub-tenants.
        Under-tenant data is only available for holdings captured in the <strong>1842 Shillelagh estate survey</strong>.
      </div>

      <!-- Census summary — populated async -->
      <div id="rp-census-strip" style="margin-bottom:12px;"></div>

      <!-- Link to census page -->
      <a href="${censusUrl}"
         style="display:flex;align-items:center;justify-content:space-between;padding:12px 16px;background:rgba(35,75,58,.07);border:1px solid rgba(35,75,58,.22);border-radius:10px;color:var(--forest);font-size:14px;font-weight:700;text-decoration:none;transition:background .18s;"
         onmouseover="this.style.background='rgba(35,75,58,.14)'" onmouseout="this.style.background='rgba(35,75,58,.07)'">
        <span>View Census Data</span>
        <span style="font-size:17px;font-weight:400;">→</span>
      </a>

      <div class="heritage-mode-banner">
        Use <strong>Explore Historic Place</strong> to keep working on this same home-page map while switching from family
        records to nearby archaeological sites, holy wells, and monuments for ${escapeHtml(key)}.
      </div>

      <div class="heritage-actions">
        <button class="btn btn-soft btn-block" onclick="window.openHistoricLandscapeInMap('${safeKey}')">
          Explore Historic Place on This Map
        </button>
        <a href="/heritage?townland=${encodeURIComponent(key)}"
           class="btn btn-ghost"
           style="width:100%;text-align:center;">
          Open Dedicated Historic Landscape Page
        </a>
      </div>
    `;

    // Fetch and inject census summary asynchronously
    fetchCensusStripForPanel(key);
  }

  async function fetchCensusStripForPanel(townlandName) {
    const strip = $("rp-census-strip");
    if (!strip) return;
    try {
      const res = await fetch(`/api/census/?year=1841&townland=${encodeURIComponent(townlandName)}&limit=1`);
      if (!res.ok) return;
      const payload = await res.json();
      const rows = Array.isArray(payload) ? payload : (payload.data || []);
      if (!rows.length) {
        strip.innerHTML = `<div style="font-size:12px;color:var(--subtle);padding:8px 0;">No census records found for this townland.</div>`;
        return;
      }
      const r = rows[0];
      const total = r.total != null ? r.total : "–";
      const male  = r.male  != null ? r.male  : "–";
      const female = r.female != null ? r.female : "–";
      strip.innerHTML = `
        <div style="padding:12px 14px;background:var(--surface);border:1px solid var(--line-soft);border-radius:10px;">
          <div style="font-size:11px;text-transform:uppercase;letter-spacing:1.2px;color:var(--brass);font-weight:700;margin-bottom:10px;">Census 1841 · Population</div>
          <div style="display:flex;gap:8px;">
            <div style="flex:1;text-align:center;padding:8px 4px;background:rgba(35,75,58,.07);border-radius:8px;">
              <div style="font-size:22px;font-weight:700;font-family:'Cormorant Garamond',serif;color:var(--forest);line-height:1;">${total}</div>
              <div style="font-size:12px;font-weight:600;color:var(--ink-muted);margin-top:3px;">Total</div>
            </div>
            <div style="flex:1;text-align:center;padding:8px 4px;background:rgba(127,167,184,.10);border-radius:8px;">
              <div style="font-size:22px;font-weight:700;font-family:'Cormorant Garamond',serif;color:var(--mist);line-height:1;">${male}</div>
              <div style="font-size:12px;font-weight:600;color:var(--ink-muted);margin-top:3px;">Male</div>
            </div>
            <div style="flex:1;text-align:center;padding:8px 4px;background:rgba(176,141,87,.10);border-radius:8px;">
              <div style="font-size:22px;font-weight:700;font-family:'Cormorant Garamond',serif;color:var(--brass);line-height:1;">${female}</div>
              <div style="font-size:12px;font-weight:600;color:var(--ink-muted);margin-top:3px;">Female</div>
            </div>
          </div>
        </div>`;
    } catch (e) {
      // silently skip — census strip is supplementary
    }
  }

  window.openTownlandView = function (townland, mode) {
    const key = canonicalTownland(townland);
    if (!key) return;
    const data = state.townlandIndex[key];
    const body = $("modalBody");
    $("modalSurname").innerText = `${mode === "tenants" ? "Tenants" : "Surnames"} in ${key}`;

    let html = "";
    state.activeGroups = [];

    if (mode === "tenants") {
      const chiefs = Object.values(data.chiefTenants).sort((a, b) => a.name.localeCompare(b.name));
      html += `<div style="padding:12px 16px;background:#f1f5f0;border-bottom:2px solid #d4dfd1;font-size:14px;font-weight:700;color:#1a2e23;letter-spacing:.02em;"><strong>${chiefs.length}</strong> Chief Tenants</div>`;

      chiefs.forEach((chief) => {
        const chiefIdx = state.activeGroups.push({ label: `Chief Tenant: ${chief.name}`, records: chief.records }) - 1;
        const underTenants = Object.values(chief.underTenants).sort((a, b) => a.name.localeCompare(b.name));

        html += `
          <div style="margin:14px 0;border:1px solid #d4dfd1;border-radius:10px;overflow:hidden;">
            <div style="font-weight:700;font-size:15px;color:#1a2e23;cursor:pointer;padding:12px 14px;background:#f8fbf8;display:flex;align-items:center;flex-wrap:wrap;gap:6px;" onclick="window.toggleExpanded(this)">
              <span style="flex:1;min-width:0;">Chief Tenant: ${chief.name}</span>
              <span style="color:#5a7a5e;font-size:13px;font-weight:600;white-space:nowrap;">(${chief.records.length} records)</span>
              ${yearSummaryHTML(chief.records)}
              ${sourceTagHTML(chief.records, chiefIdx)}
              <button style="font-size:12px;padding:4px 10px;border:1px solid #3a6652;border-radius:6px;background:#fff;color:#234B3A;font-weight:600;cursor:pointer;white-space:nowrap;" onclick="event.stopPropagation(); window.openGroupDetails(${chiefIdx})">Full Details</button>
            </div>
            <div style="display:none;margin:0;padding:10px 14px;">
              ${chief.records.slice(0, 4).map(renderRecordWithLinks).join("")}
              <div style="margin:10px 0 8px;font-size:13px;font-weight:700;color:#3a4f5e;text-transform:uppercase;letter-spacing:.06em;">Under Tenants</div>
              ${underTenants.length > 0 ? underTenants.map((u) => {
          const uIdx = state.activeGroups.push({ label: `Under Tenant: ${u.name}`, records: u.records }) - 1;
          return `
                  <div style="margin:6px 0;padding:10px 12px;border:1px dashed #c5d0c7;border-radius:8px;background:#fafcfa;">
                    <div style="font-size:14px;font-weight:700;color:#2d4a38;cursor:pointer;display:flex;align-items:center;flex-wrap:wrap;gap:6px;" onclick="window.toggleExpanded(this, ${uIdx})">
                      <span style="flex:1;">${u.name}</span>
                      <span style="color:#5a7a5e;font-size:13px;font-weight:600;">(${u.records.length} records)</span>
                      ${yearSummaryHTML(u.records)}
                      ${sourceTagHTML(u.records, uIdx)}
                      <button style="font-size:12px;padding:4px 10px;border:1px solid #3a6652;border-radius:6px;background:#fff;color:#234B3A;font-weight:600;cursor:pointer;white-space:nowrap;" onclick="event.stopPropagation(); window.openGroupDetails(${uIdx})">Full Details</button>
                    </div>
                    <div style="display:none;margin-top:8px;" data-lazy-group="${uIdx}"></div>
                  </div>
                `;
        }).join("") : `<div style='padding:8px 0;'>
                  <div style='font-size:12px;color:#5a7a5e;font-style:italic;margin-bottom:4px;'>No under-tenants recorded for this holding.</div>
                  <div style='font-size:11px;color:#64748b;line-height:1.5;'>
                    Under-tenant data is only present where the original estate survey explicitly named a sub-tenant beneath the chief tenant.
                    This information comes exclusively from the <strong>1842 Shillelagh estate survey</strong> — earlier and later records do not capture sub-tenancies.
                    Townlands such as <em>Aghowle Upper</em>, <em>Ardoyne</em>, and <em>Askakeagh</em> have explicit under-tenant entries; most others do not.
                  </div>
                </div>`}
            </div>
          </div>
        `;
      });
    } else {
      const fams = Object.values(data.families).sort((a, b) => a.surname.localeCompare(b.surname));
      const hasChiefTenantFallback = fams.some(f => f.fromChiefTenant);
      html += `<div style="padding:12px 16px;background:#f1f5f0;border-bottom:2px solid #d4dfd1;font-size:14px;font-weight:700;color:#1a2e23;letter-spacing:.02em;"><strong>${fams.length}</strong> Surnames</div>`;
      if (hasChiefTenantFallback) {
        html += `
          <div style="margin:10px 14px 4px;padding:10px 12px;background:#fefce8;border:1px solid #fde68a;border-radius:8px;">
            <div style="font-size:12px;font-weight:700;color:#713f12;margin-bottom:3px;">Note: Surnames from estate survey records</div>
            <div style="font-size:11px;color:#78350f;line-height:1.5;">
              The original records for this townland (1842 Shillelagh estate survey) do not record individual surnames directly.
              The surnames shown here are taken from the <strong>Chief Tenant</strong> column of those tenancy records,
              not from the personal <em>Surname</em> field. Each entry represents a named holding rather than a single individual's biography.
            </div>
          </div>`;
      }

      fams.forEach((fam) => {
        const idx = state.activeGroups.push({ label: `Surname: ${fam.surname}`, records: fam.records }) - 1;
        const displayName = (!fam.surname || fam.surname === "Unknown" || !/^[A-Za-z\s'-]+$/.test(fam.surname)) ? "Unknown" : fam.surname;
        html += `
          <div style="margin:10px 0;border:1px solid #d4dfd1;border-radius:10px;overflow:hidden;">
            <div style="font-size:15px;font-weight:700;color:#1a2e23;cursor:pointer;padding:12px 14px;background:#f8fbf8;display:flex;align-items:center;flex-wrap:wrap;gap:6px;" onclick="window.toggleExpanded(this, ${idx})">
              <span style="flex:1;">${displayName}</span>
              <span style="color:#5a7a5e;font-size:13px;font-weight:600;">(${fam.records.length} records)</span>
              ${sourceTagHTML(fam.records, idx)}
              <button style="font-size:12px;padding:4px 10px;border:1px solid #3a6652;border-radius:6px;background:#fff;color:#234B3A;font-weight:600;cursor:pointer;white-space:nowrap;" onclick="event.stopPropagation(); window.openGroupDetails(${idx})">Full Details</button>
            </div>
            <div style="display:none;margin-top:0;padding:10px 14px;" data-lazy-group="${idx}"></div>
          </div>
        `;
      });
    }

    body.innerHTML = html || "<div style='padding:24px;text-align:center;font-size:15px;color:#475467;'>No records available.</div>";
    openModal(modal);
  };

  function renderSurnameResults(surname, selectedTownland = "") {
    const s = String(surname || "").trim().toLowerCase();
    if (!s) return;
    const tlFilter = String(selectedTownland || "").trim().toLowerCase();
    const rows = state.records.filter((r) => {
      if ((r.surname || "").toLowerCase() !== s) return false;
      if (!tlFilter) return true;
      return (r.townland || "").toLowerCase() === tlFilter;
    });
    $("modalSurname").innerText = `${surname} - Records`;
    const grouped = {};
    rows.forEach((r) => {
      const tl = r.townland || "";
      if (!tl) return;
      if (!grouped[tl]) grouped[tl] = [];
      grouped[tl].push(r);
    });

    state.activeGroups = [];
    const html = Object.keys(grouped)
      .sort()
      .map((tl) => {
        const idx = state.activeGroups.push({ label: `${surname} in ${tl}`, records: grouped[tl] }) - 1;
        return `
          <div style="margin:10px 0;padding:10px;border:1px solid #e2e8f0;border-radius:10px;">
            <div style="font-weight:800;cursor:pointer;" onclick="window.toggleExpanded(this)">
              ${tl} <span style="color:#64748b;font-size:12px;">(${grouped[tl].length} records)</span>
              ${sourceTagHTML(grouped[tl], idx)}
              <button style="margin-left:8px;font-size:11px;padding:3px 8px;border:1px solid #cbd5e1;border-radius:6px;background:#fff;cursor:pointer;" onclick="event.stopPropagation(); window.openGroupDetails(${idx})">Open Full Details</button>
            </div>
            <div style="display:none;margin-top:8px;">${grouped[tl].slice(0, 4).map(renderRecordWithLinks).join("")}</div>
          </div>
        `;
      })
      .join("");

    $("modalBody").innerHTML =
      `<div style="padding:10px;background:#f8fafc;border-bottom:1px solid #e2e8f0;"><strong>${rows.length}</strong> records${tlFilter ? ` in <strong>${selectedTownland}</strong>` : ""}</div>` +
      (html || "<div style='padding:20px;text-align:center;'>No records found.</div>");
    openModal(modal);
  }

  async function loadUnifiedData() {
    const res = await fetch("/api/unified/records");
    if (!res.ok) throw new Error("Failed to load unified records");
    const text = await res.text();
    let data;
    try {
      data = JSON.parse(text);
    } catch (err) {
      throw new Error("Unified records JSON parse failed");
    }
    state.records = data.map((r) => ({ ...r, year: normalizeYear(r.year) }));
    buildIndexes(state.records);
  }

  async function loadOptions() {
    const [townlandsRes, geoRes] = await Promise.all([
      fetch("/api/unified/townlands"),
      fetch("/static/data/townlands.json")
    ]);
    const unified = townlandsRes.ok ? await townlandsRes.json() : [];
    const geo = geoRes.ok ? await geoRes.json() : { features: [] };

    // Set geo data early so dropdown→map highlight works immediately
    townlandsGeoData = geo;

    const geoNames = new Set(
      (geo.features || []).map(f => String(f.properties?.TL_ENGLISH || "").trim().toLowerCase())
    );
    // Only show entries that correspond to actual estate polygons
    const filtered = unified.filter(t => geoNames.has(t.trim().toLowerCase()));

    const tSelect = $("townlandSelect");
    if (!tSelect) return;
    tSelect.innerHTML = '<option value="">All townlands</option>';
    filtered.forEach(t => {
      tSelect.innerHTML += `<option value="${t}">${t}</option>`;
    });
  }

  async function wireSurnameAutocomplete() {
    const input = $("surnameInput");
    const list = $("surnameSuggestions");
    if (!input || !list) return;

    function refreshSuggest() {
      const q = input.value.trim();
      const tl = $("townlandSelect")?.value || "";

      let pool = state.records;
      if (tl) {
        const tlNorm = tl.toLowerCase();
        pool = pool.filter((r) => (r.townland || "").toLowerCase() === tlNorm);
      }

      const qNorm = q.toLowerCase();
      const items = [...new Set(pool
        .map((r) => (r.surname || "").trim())
        .filter(Boolean)
        .filter((s) => (qNorm ? s.toLowerCase().startsWith(qNorm) : true))
      )].sort((a, b) => a.localeCompare(b)).slice(0, 50);

      list.innerHTML = "";
      items.forEach((s) => {
        const opt = document.createElement("option");
        opt.value = s;
        list.appendChild(opt);
      });
    }

    let _surnameTimer = null;

    function tryRenderSurname() {
      const val = input.value.trim();
      if (!val) { setSurnameError(""); return; }
      const tl = $("townlandSelect")?.value || "";
      if (!hasSurnameInContext(val, tl)) {
        setSurnameError("Surname not found — type a name and select from the suggestions, or click Apply Filters.");
        return;
      }
      setSurnameError("");
      renderSurnameResults(val, tl);
    }

    input.addEventListener("input", () => {
      setSurnameError("");
      refreshSuggest();
      // Datalist selection fires `input` but not always `change` on all browsers.
      // After a short delay, check whether the value now exactly matches a datalist
      // option and auto-open results if so (handles Safari / older browsers).
      clearTimeout(_surnameTimer);
      _surnameTimer = setTimeout(() => {
        const val = input.value.trim();
        if (!val) return;
        const optVals = Array.from(list.options).map(o => o.value);
        if (optVals.includes(val)) tryRenderSurname();
      }, 80);
    });
    input.addEventListener("change", tryRenderSurname);
    refreshSuggest();
    $("townlandSelect")?.addEventListener("change", () => {
      const tl = $("townlandSelect")?.value || "";
      const val = input.value.trim();
      if (val && !hasSurnameInContext(val, tl)) {
        input.value = "";
      }
      setSurnameError("");
      refreshSuggest();
    });
  }

  const applyBtn = $("applyFiltersBtn");
  if (applyBtn) {
    applyBtn.addEventListener("click", () => {
      const tl = $("townlandSelect").value;
      const sn = ($("surnameInput")?.value || "").trim();
      if (sn) {
        if (!hasSurnameInContext(sn, tl)) {
          setSurnameError("Surname not found — type a name and select from the suggestions.");
          return;
        }
        setSurnameError("");
        renderSurnameResults(sn, tl);
      } else if (tl) {
        setSurnameError("");
        renderTownlandPanel(tl);
      }
    });
  }

  $("townlandSelect")?.addEventListener("change", () => {
    const tl = $("townlandSelect").value;
    if (tl) {
      selectedTownlandName = tl;
      if (homeHeritageState.mode === "heritage") {
        renderHomeHeritageSummary(tl);
      } else {
        renderTownlandPanel(tl);
      }
      
      // Also highlight on map when selected from dropdown
      if (townlandsGeoData && tl) {
        const feature = townlandsGeoData.features.find(f => {
          const nm = String(f.properties?.TL_ENGLISH || "").trim();
          return nm.toLowerCase() === tl.toLowerCase();
        });
        if (feature) {
          highlightTownland(feature, null);
        }
      }
    } else {
      // Remove highlight when cleared
      if (selectedTownlandLayer) {
        map.removeLayer(selectedTownlandLayer);
        selectedTownlandLayer = null;
      }
      selectedTownlandName = null;
      clearHomeHeritageLayers();
      clearHomeHeritageRadiusLayers();
      if (homeHeritageState.mode === "heritage") {
        setDetailsPlaceholder("Select a townland to explore historic places on this same map.");
      } else {
        setDetailsPlaceholder("Select a Townland on the map to view tenants and families.");
      }
    }
  });

  const mapEl = $("wicklowMap");
  if (!mapEl) return;
  const map = L.map("wicklowMap", { minZoom: 8, maxZoom: 15 });
  L.tileLayer("https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", {
    attribution: "© OpenTopoMap contributors",
    opacity: 0.55
  }).addTo(map);
  L.rectangle([[52.65, -6.9], [53.35, -5.9]], {
    color: "transparent",
    fillColor: "#f3e6cf",
    fillOpacity: 0.55,
    interactive: false
  }).addTo(map);
  map.fitBounds(L.latLngBounds([52.70, -6.75], [53.25, -6.05]));

  // Store references for highlighting
  let townlandGeoLayer = null;
  let selectedTownlandLayer = null;
  let selectedTownlandName = null;
  let townlandsGeoData = null;
  const HOME_HERITAGE_CONFIG = {
    DEFAULT_RADIUS_M: 2000,
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
  };
  const homeHeritageState = {
    mode: "records",
    radius: HOME_HERITAGE_CONFIG.DEFAULT_RADIUS_M,
    rawData: { asi: null, holywells: null, monuments: null },
    layerGroups: { asi: null, holywells: null, monuments: null },
    visibleCounts: { asi: 0, holywells: 0, monuments: 0 },
    radiusLayers: [],
    activeFeature: null,
  };

  function displayTownlandLabel(value) {
    return String(value || "")
      .toLowerCase()
      .replace(/\b([a-z])/g, (match) => match.toUpperCase())
      .replace(/\bOr\b/g, "or");
  }

  function setDetailsPlaceholder(message) {
    const details = $("detailsContent");
    if (!details) return;
    details.innerHTML = `<div style="color:#666;margin-top:20px;">${message}</div>`;
  }

  function updateExploreMapMeta() {
    const title = $("exploreMapPanelTitle");
    const subtitle = $("exploreMapPanelSubtitle");
    if (!title || !subtitle) return;

    if (homeHeritageState.mode === "heritage") {
      title.textContent = selectedTownlandName
        ? `${displayTownlandLabel(selectedTownlandName)} — Historic Landscape`
        : "County Wicklow — Historic Landscape";
      subtitle.textContent = selectedTownlandName
        ? "The same map is now showing archaeology, holy wells, monuments, and bright 2 km / 5 km distance rings. Click any marker for more detail."
        : "Select a townland, then use this same map to explore archaeology, holy wells, monuments, and the highlighted 2 km / 5 km rings.";
      return;
    }

    title.textContent = "County Wicklow — Townlands & Families";
    subtitle.textContent =
      "Select a townland to view linked family records, then switch the same map into historic place mode when you want nearby archaeology, holy wells, and monuments.";
  }

  function findTownlandFeatureByName(name) {
    if (!townlandsGeoData || !name) return null;
    return (townlandsGeoData.features || []).find((feature) => {
      const featureName = String(feature.properties?.TL_ENGLISH || "").trim();
      return featureName.toLowerCase() === String(name).trim().toLowerCase();
    }) || null;
  }

  function polygonCentroid(geometry) {
    const ring =
      geometry?.type === "Polygon"
        ? geometry.coordinates[0]
        : geometry?.type === "MultiPolygon"
        ? geometry.coordinates[0][0]
        : [];
    let minLat = Infinity;
    let maxLat = -Infinity;
    let minLng = Infinity;
    let maxLng = -Infinity;
    for (const [lng, lat] of ring) {
      if (lat < minLat) minLat = lat;
      if (lat > maxLat) maxLat = lat;
      if (lng < minLng) minLng = lng;
      if (lng > maxLng) maxLng = lng;
    }
    return [(minLat + maxLat) / 2, (minLng + maxLng) / 2];
  }

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

  function haversineM(lat1, lng1, lat2, lng2) {
    const R = 6371000;
    const phi1 = (lat1 * Math.PI) / 180;
    const phi2 = (lat2 * Math.PI) / 180;
    const deltaPhi = ((lat2 - lat1) * Math.PI) / 180;
    const deltaLambda = ((lng2 - lng1) * Math.PI) / 180;
    const a =
      Math.sin(deltaPhi / 2) ** 2 +
      Math.cos(phi1) * Math.cos(phi2) * Math.sin(deltaLambda / 2) ** 2;
    return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  }

  function shortMonumentClass(label) {
    if (!label) return "Heritage Feature";
    return String(label).split(" - ")[0].trim();
  }

  function featureDisplayName(props, key) {
    if (key === "monuments" && props.name) return props.name;
    if (props.monument_class) return shortMonumentClass(props.monument_class);
    if (props.monument_type) return shortMonumentClass(props.monument_type);
    if (props.smrs) return props.smrs;
    return HOME_HERITAGE_CONFIG.DATASETS[key].label;
  }

  function formatCoord(value) {
    return Number(value).toFixed(5);
  }

  function googleMapsLink(type, lat, lng) {
    const coord = encodeURIComponent(`${formatCoord(lat)},${formatCoord(lng)}`);
    if (type === "directions") {
      return `https://www.google.com/maps/dir/?api=1&destination=${coord}`;
    }
    return `https://www.google.com/maps/search/?api=1&query=${coord}`;
  }

  function clearHomeHeritageLayers() {
    Object.keys(homeHeritageState.layerGroups).forEach((key) => {
      const layer = homeHeritageState.layerGroups[key];
      if (layer) {
        map.removeLayer(layer);
        homeHeritageState.layerGroups[key] = null;
      }
      homeHeritageState.visibleCounts[key] = 0;
    });
  }

  function clearHomeHeritageRadiusLayers() {
    homeHeritageState.radiusLayers.forEach((layer) => map.removeLayer(layer));
    homeHeritageState.radiusLayers = [];
  }

  async function loadHomeHeritageData() {
    const keys = Object.keys(HOME_HERITAGE_CONFIG.DATASETS);
    await Promise.all(
      keys.map(async (key) => {
        if (homeHeritageState.rawData[key] !== null) return;
        try {
          const res = await fetch(HOME_HERITAGE_CONFIG.DATASETS[key].file);
          if (!res.ok) {
            homeHeritageState.rawData[key] = [];
            return;
          }
          const geo = await res.json();
          homeHeritageState.rawData[key] = geo.features || [];
        } catch (_err) {
          homeHeritageState.rawData[key] = [];
        }
      })
    );
  }

  function filterHomeHeritageFeatures(features, geometry, centroid, radiusM) {
    if (!features?.length || !geometry || !centroid) return [];
    const [cLat, cLng] = centroid;
    return features.filter((feature) => {
      const coords = feature.geometry?.coordinates;
      if (!coords) return false;
      const [lng, lat] = coords;
      if (pointInGeoJSONPolygon(lng, lat, geometry)) return true;
      if (radiusM > 0) return haversineM(lat, lng, cLat, cLng) <= radiusM;
      return false;
    });
  }

  function drawHomeHeritageRadiusRings(centroid) {
    clearHomeHeritageRadiusLayers();
    if (!centroid) return;

    const [lat, lng] = centroid;
    const activeRadius = homeHeritageState.radius;
    const boundaryOnly = activeRadius === 0;
    const ringConfigs = [
      { radius: 2000, color: "#f59e0b", fillColor: "#fbbf24", dashArray: "10 6" },
      { radius: 5000, color: "#ef4444", fillColor: "#f87171", dashArray: "14 8" },
    ];

    ringConfigs.forEach((ring) => {
      const active = activeRadius === ring.radius;
      const emphasis = boundaryOnly ? 0.7 : active ? 1 : 0.55;
      const circle = L.circle([lat, lng], {
        radius: ring.radius,
        color: ring.color,
        weight: active ? 3.4 : 2.2,
        opacity: active ? 0.96 : 0.58 * emphasis,
        fillColor: ring.fillColor,
        fillOpacity: active ? 0.08 : boundaryOnly ? 0.03 : 0.025,
        dashArray: ring.dashArray,
        interactive: false,
      }).addTo(map);
      homeHeritageState.radiusLayers.push(circle);
    });
  }

  function updateHomeHeritageRadiusButtons() {
    const buttons = document.querySelectorAll(".heritage-radius-btn");
    buttons.forEach((button) => {
      const active = Number(button.dataset.radius) === homeHeritageState.radius;
      button.classList.toggle("active", active);
    });
  }

  function homeHeritageRadiusButtonsMarkup() {
    return `
      <div class="heritage-radius-row">
        <button class="heritage-radius-btn" data-radius="0" onclick="window.setHistoricLandscapeRadius(0)">
          <span class="heritage-radius-dot" style="background:#94a3b8;"></span>
          Inside townland
        </button>
        <button class="heritage-radius-btn" data-radius="2000" onclick="window.setHistoricLandscapeRadius(2000)">
          <span class="heritage-radius-dot" style="background:#f59e0b;"></span>
          Inside + 2 km
        </button>
        <button class="heritage-radius-btn" data-radius="5000" onclick="window.setHistoricLandscapeRadius(5000)">
          <span class="heritage-radius-dot" style="background:#ef4444;"></span>
          Inside + 5 km
        </button>
      </div>
    `;
  }

  function homeHeritageLegendMarkup() {
    return `
      <div class="heritage-radius-legend">
        <span class="heritage-legend-chip">
          <span class="heritage-radius-dot" style="background:#f59e0b;"></span>
          2 km ring
        </span>
        <span class="heritage-legend-chip">
          <span class="heritage-radius-dot" style="background:#ef4444;"></span>
          5 km ring
        </span>
      </div>
    `;
  }

  function buildHomeHeritageNarrative(name) {
    const counts = homeHeritageState.visibleCounts;
    const radiusLabel =
      homeHeritageState.radius === 0
        ? "inside the townland boundary"
        : `within ${homeHeritageState.radius / 1000} km of ${displayTownlandLabel(name)}`;
    const parts = [];
    if (counts.asi > 0) parts.push(`${counts.asi} archaeological site${counts.asi !== 1 ? "s" : ""}`);
    if (counts.holywells > 0) parts.push(`${counts.holywells} holy well${counts.holywells !== 1 ? "s" : ""}`);
    if (counts.monuments > 0) parts.push(`${counts.monuments} monument${counts.monuments !== 1 ? "s" : ""}`);
    if (!parts.length) {
      return `No mapped heritage features were found ${radiusLabel}. Try widening the radius to 2 km or 5 km.`;
    }
    const joined =
      parts.length === 1
        ? parts[0]
        : `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
    return `Showing ${joined} ${radiusLabel}. Click a marker on the map to open the full place detail here.`;
  }

  function homeHeritageStatMarkup() {
    return `
      <div class="heritage-chip-grid">
        ${Object.entries(HOME_HERITAGE_CONFIG.DATASETS).map(([key, cfg]) => `
          <div class="heritage-chip">
            <span class="heritage-chip-label">
              <span class="heritage-radius-dot" style="background:${cfg.color};"></span>
              ${cfg.label}
            </span>
            <span class="heritage-chip-value">${homeHeritageState.visibleCounts[key] || 0}</span>
          </div>
        `).join("")}
      </div>
    `;
  }

  function renderHomeHeritageLayer(key, features, centroid, townlandName) {
    const cfg = HOME_HERITAGE_CONFIG.DATASETS[key];
    const group = L.layerGroup();
    features.forEach((feature) => {
      const coords = feature.geometry?.coordinates || [];
      const [lng, lat] = coords;
      if (!Number.isFinite(lat) || !Number.isFinite(lng)) return;
      const props = feature.properties || {};
      const marker = L.circleMarker([lat, lng], {
        radius: cfg.radius,
        color: cfg.color,
        fillColor: cfg.fillColor,
        fillOpacity: 0.82,
        weight: 1.4,
        opacity: 1,
      });
      const label = featureDisplayName(props, key);
      marker.bindTooltip(label, { sticky: true, className: "townland-hover-tooltip", offset: [0, -4] });
      marker.on("click", () => {
        const distanceM = centroid ? haversineM(lat, lng, centroid[0], centroid[1]) : null;
        homeHeritageState.activeFeature = { feature, key, distanceM };
        renderHomeHeritageFeatureDetail(townlandName, feature, key, distanceM);
      });
      group.addLayer(marker);
    });
    homeHeritageState.layerGroups[key] = group;
    group.addTo(map);
  }

  async function renderHomeHeritageSummary(townlandName) {
    const details = $("detailsContent");
    const townlandFeature = findTownlandFeatureByName(townlandName);
    if (!details || !townlandFeature) {
      setDetailsPlaceholder("This townland could not be matched to the historic landscape data.");
      return;
    }

    selectedTownlandName = townlandName;
    homeHeritageState.mode = "heritage";
    homeHeritageState.activeFeature = null;
    updateExploreMapMeta();
    details.innerHTML = "<div style='color:#666;margin-top:20px;'>Loading historic place data…</div>";

    await loadHomeHeritageData();
    if (homeHeritageState.mode !== "heritage" || selectedTownlandName !== townlandName) {
      return;
    }

    const centroid = polygonCentroid(townlandFeature.geometry);
    clearHomeHeritageLayers();
    drawHomeHeritageRadiusRings(centroid);

    Object.keys(HOME_HERITAGE_CONFIG.DATASETS).forEach((key) => {
      const filtered = filterHomeHeritageFeatures(
        homeHeritageState.rawData[key] || [],
        townlandFeature.geometry,
        centroid,
        homeHeritageState.radius
      );
      homeHeritageState.visibleCounts[key] = filtered.length;
      renderHomeHeritageLayer(key, filtered, centroid, townlandName);
    });

    const safeTownland = displayTownlandLabel(townlandName).replace(/'/g, "\\'");
    details.innerHTML = `
      <h3 style="margin:0 0 3px 0;font-family:'Cormorant Garamond',Georgia,serif;font-size:22px;font-weight:700;color:var(--ink);line-height:1.2;">${escapeHtml(displayTownlandLabel(townlandName))}</h3>
      <div style="font-size:11px;text-transform:uppercase;letter-spacing:1.2px;color:var(--brass);margin-bottom:16px;font-weight:600;">Historic Landscape</div>
      <div class="heritage-mode-banner">
        The map is now focused on nearby historic places for this townland. The 2 km and 5 km rings are shown in brighter colours so you can judge distance more clearly.
      </div>
      ${homeHeritageRadiusButtonsMarkup()}
      ${homeHeritageLegendMarkup()}
      <p style="margin:0;color:#475467;line-height:1.7;">${escapeHtml(buildHomeHeritageNarrative(townlandName))}</p>
      ${homeHeritageStatMarkup()}
      <div class="heritage-actions">
        <button class="btn btn-primary btn-block" onclick="window.returnToFamilyRecords('${safeTownland}')">Back to Family Records</button>
        <a href="/census?townland=${encodeURIComponent(townlandName)}" class="btn btn-soft" style="width:100%;text-align:center;">View Census Data</a>
        <a href="/heritage?townland=${encodeURIComponent(townlandName)}" class="btn btn-ghost" style="width:100%;text-align:center;">Open Dedicated Historic Landscape Page</a>
      </div>
    `;
    updateHomeHeritageRadiusButtons();
  }

  function renderHomeHeritageFeatureDetail(townlandName, feature, key, distanceM) {
    const details = $("detailsContent");
    if (!details) return;
    const props = feature.properties || {};
    const cfg = HOME_HERITAGE_CONFIG.DATASETS[key];
    const [lng, lat] = feature.geometry?.coordinates || [];
    const safeTownland = displayTownlandLabel(townlandName).replace(/'/g, "\\'");
    const name = featureDisplayName(props, key);
    const fullClass = props.monument_class || props.monument_type || cfg.label;
    const notes = String(props.notes || "").trim();
    const sourceLink = String(props.source_link || props.external_link || "").trim();
    const locality = displayTownlandLabel(props.townland || props.locality || townlandName);
    const distanceText = distanceM != null
      ? `${(distanceM / 1000).toFixed(2)} km from ${displayTownlandLabel(townlandName)}`
      : "";

    details.innerHTML = `
      <button class="btn btn-ghost btn-block" onclick="window.returnToHistoricLandscapeSummary('${safeTownland}')">Back to Historic Place Summary</button>
      ${homeHeritageRadiusButtonsMarkup()}
      ${homeHeritageLegendMarkup()}
      <div class="heritage-feature-card">
        <div class="heritage-feature-type" style="color:${cfg.color};">${escapeHtml(cfg.label)}</div>
        <div class="heritage-feature-name">${escapeHtml(name)}</div>
        <div class="heritage-feature-meta">
          <div><strong>Class:</strong> ${escapeHtml(fullClass)}</div>
          <div><strong>Townland:</strong> ${escapeHtml(locality)}</div>
          ${props.smrs || props.id ? `<div><strong>Reference:</strong> ${escapeHtml(props.smrs || props.id)}</div>` : ""}
          ${distanceText ? `<div><strong>Distance:</strong> ${escapeHtml(distanceText)}</div>` : ""}
          <div><strong>Coordinates:</strong> ${formatCoord(lat)}, ${formatCoord(lng)}</div>
        </div>
        ${notes ? `<div class="heritage-feature-notes">${escapeHtml(notes.substring(0, 900))}${notes.length > 900 ? "…" : ""}</div>` : ""}
        <div class="heritage-actions">
          <a href="${googleMapsLink("view", lat, lng)}" class="btn btn-soft" target="_blank" rel="noopener" style="width:100%;text-align:center;">View Exact Coordinates</a>
          <a href="${googleMapsLink("directions", lat, lng)}" class="btn btn-soft" target="_blank" rel="noopener" style="width:100%;text-align:center;">Get Directions</a>
          ${sourceLink ? `<a href="${escapeHtml(sourceLink)}" class="btn btn-ghost" target="_blank" rel="noopener" style="width:100%;text-align:center;">Open Source Record</a>` : ""}
          <button class="btn btn-primary btn-block" onclick="window.returnToFamilyRecords('${safeTownland}')">Back to Family Records</button>
        </div>
      </div>
    `;
    updateHomeHeritageRadiusButtons();
  }

  function resetHomeHeritageMode() {
    homeHeritageState.mode = "records";
    homeHeritageState.activeFeature = null;
    clearHomeHeritageLayers();
    clearHomeHeritageRadiusLayers();
    updateExploreMapMeta();
  }

  window.setHistoricLandscapeRadius = function (radius) {
    const parsed = Number(radius);
    homeHeritageState.radius = Number.isFinite(parsed) ? parsed : HOME_HERITAGE_CONFIG.DEFAULT_RADIUS_M;
    if (homeHeritageState.mode === "heritage" && selectedTownlandName) {
      renderHomeHeritageSummary(selectedTownlandName);
      return;
    }
    updateHomeHeritageRadiusButtons();
  };

  window.returnToFamilyRecords = function (townlandName) {
    const resolved = canonicalTownland(townlandName || selectedTownlandName || "") || townlandName || "";
    resetHomeHeritageMode();
    if (resolved) {
      renderTownlandPanel(resolved);
      return;
    }
    setDetailsPlaceholder("Select a Townland on the map to view tenants and families.");
  };

  window.returnToHistoricLandscapeSummary = function (townlandName) {
    const resolved = canonicalTownland(townlandName || selectedTownlandName || "") || townlandName || "";
    if (!resolved) return;
    renderHomeHeritageSummary(resolved);
  };

  window.openHistoricLandscapeInMap = function (townlandName) {
    const resolved = canonicalTownland(townlandName || selectedTownlandName || $("townlandSelect")?.value || "");
    homeHeritageState.mode = "heritage";
    updateExploreMapMeta();

    if (!resolved) {
      clearHomeHeritageLayers();
      clearHomeHeritageRadiusLayers();
      setDetailsPlaceholder("Select a townland first, then use Explore Historic Place to show nearby archaeology, holy wells, and monuments on this map.");
      return;
    }

    const select = $("townlandSelect");
    if (select) {
      for (const option of select.options) {
        if (option.value.toLowerCase() === resolved.toLowerCase()) {
          select.value = option.value;
          break;
        }
      }
    }

    const feature = findTownlandFeatureByName(resolved);
    if (feature) {
      highlightTownland(feature, null);
    }
    selectedTownlandName = resolved;
    renderHomeHeritageSummary(resolved);
  };

  // Create glowing effect for selected townland
  function highlightTownland(feature, layer) {
    // Remove previous highlight
    if (selectedTownlandLayer) {
      map.removeLayer(selectedTownlandLayer);
    }
    
    const nm = String(feature.properties?.TL_ENGLISH || "").trim();
    selectedTownlandName = nm;
    
    // Create glowing overlay with outer glow
    const glowLayer = L.geoJSON(feature, {
      style: {
        color: '#60a5fa',
        weight: 16,
        fillColor: '#60a5fa',
        fillOpacity: 0.15,
        lineCap: 'round',
        lineJoin: 'round'
      }
    });
    
    // Create middle glow
    const midLayer = L.geoJSON(feature, {
      style: {
        color: '#3b82f6',
        weight: 8,
        fillColor: '#3b82f6',
        fillOpacity: 0.2,
        lineCap: 'round',
        lineJoin: 'round'
      }
    });
    
    // Create bright inner line
    const highlightLayer = L.geoJSON(feature, {
      style: {
        color: '#93c5fd',
        weight: 3,
        fillColor: '#3b82f6',
        fillOpacity: 0.25,
        lineCap: 'round',
        lineJoin: 'round'
      }
    });
    
    // Add all layers to a group
    selectedTownlandLayer = L.layerGroup([glowLayer, midLayer, highlightLayer]).addTo(map);
    
    // Add pulsing animation
    let pulseState = 0;
    function animatePulse() {
      if (!selectedTownlandLayer) return;
      
      pulseState += 0.05;
      const glowOpacity = 0.1 + Math.sin(pulseState) * 0.05;
      const midOpacity = 0.15 + Math.sin(pulseState) * 0.08;
      const weightChange = Math.sin(pulseState) * 2;
      
      selectedTownlandLayer.eachLayer(l => {
        if (l === glowLayer.getLayers()[0]) {
          l.setStyle({ fillOpacity: glowOpacity, weight: 16 + weightChange });
        } else if (l === midLayer.getLayers()[0]) {
          l.setStyle({ fillOpacity: midOpacity, weight: 8 + weightChange });
        }
      });
      
      requestAnimationFrame(animatePulse);
    }
    animatePulse();
    
    // Zoom to the selected townland
    const bounds = L.geoJSON(feature).getBounds();
    map.fitBounds(bounds, { padding: [40, 40], maxZoom: 14 });
  }

  async function loadTownlandsGeo() {
    // Reuse geo data already fetched by loadOptions; fetch only if needed
    if (!townlandsGeoData) {
      const res = await fetch("/static/data/townlands.json");
      townlandsGeoData = await res.json();
    }
    const geo = townlandsGeoData;
    
    townlandGeoLayer = L.geoJSON(geo, {
      style: { color: "#8b5e34", weight: 1, fillColor: "#f5efe6", fillOpacity: 0.35 },
      onEachFeature: (feature, layer) => {
        const nm = String(feature.properties?.TL_ENGLISH || "").trim();
        if (!nm) return;

        // Hover tooltip — name only, no permanent label on the map
        layer.bindTooltip(nm, {
          permanent: false,
          sticky: true,
          direction: "top",
          className: "townland-hover-tooltip",
          offset: [0, -4]
        });

        layer.on("click", () => {
          // Highlight selected townland with glowing effect
          highlightTownland(feature, layer);

          const keyRaw = canonicalTownland(nm) || nm;

          // Force match the dropdown options which might be uppercase
          const select = $("townlandSelect");
          let matchedVal = keyRaw;
          for (let opt of select.options) {
            if (opt.value.toLowerCase() === keyRaw.toLowerCase()) {
              matchedVal = opt.value;
              break;
            }
          }

          select.value = matchedVal;
          select.dispatchEvent(new Event("change"));
        });
      }
    }).addTo(map);

    // Centroid label markers removed — names are shown via hover tooltips above.
  }

  (async () => {
    // Load the map boundaries first so the visual is responsive immediately,
    // then fetch the 4.4 MB unified records in the background.
    try {
      await loadTownlandsGeo();
    } catch (e) {
      console.error("Townland map load failed:", e);
    }

    // Deferred: unified records + options (runs after map paint).
    // URL param handling also lives here because it depends on loaded options.
    queueMicrotask(async () => {
      try {
        await loadUnifiedData();
        await loadOptions();
        await wireSurnameAutocomplete();
      } catch (e) {
        console.error("Data load failed:", e);
        const details = $("detailsContent");
        if (details) {
          details.innerHTML = "<div style='color:#b42318;font-weight:700;'>Failed to load unified data. Please refresh.</div>";
        }
      }

      // If a townland was passed via ?townland= (e.g. linked from census page),
      // auto-select it in the dropdown and scroll the map explorer into view.
      const urlParams = new URLSearchParams(window.location.search);
      const tlParam = urlParams.get("townland");
      const viewParam = (urlParams.get("view") || "").toLowerCase();
      if (tlParam) {
        const select = $("townlandSelect");
        if (select) {
          // Match case-insensitively against loaded options
          let matched = "";
          for (const opt of select.options) {
            if (opt.value.toLowerCase() === tlParam.toLowerCase()) {
              matched = opt.value;
              break;
            }
          }
          if (matched) {
            select.value = matched;
            // Strip the param so a page refresh doesn't re-select the townland
            window.history.replaceState({}, "", window.location.pathname);
            select.dispatchEvent(new Event("change"));
            if (viewParam === "heritage") {
              window.openHistoricLandscapeInMap(matched);
            }
            // Scroll to the explore section smoothly
            document.getElementById("explore")?.scrollIntoView({ behavior: "smooth", block: "start" });
          }
        }
      }
    });
  })();

  updateExploreMapMeta();
  setDetailsPlaceholder("Select a Townland on the map to view tenants and families.");
});
