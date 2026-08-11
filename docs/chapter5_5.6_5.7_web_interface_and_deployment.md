# Chapter 5 (continued): Implementation

> **A note on scope before drafting into the thesis document.** Chapter 1 (`docs/introduction.md`) defines four research questions, RQ1–RQ4; there is no RQ5. The web interface and deployment work below implements **Aim 5** ("Deliver a production-quality web application…", Objectives 5.1–5.3), which is cross-cutting infrastructure supporting RQ1–RQ4 rather than a research question in its own right. §5.6/§5.7 should therefore be framed as "implementation of Aim 5," not "RQ5" — if the rest of the thesis uses an SRQ (sub-research-question) convention for chapter-level subdivisions, Aim 5's three objectives (5.1 pages/maps, 5.2 security, 5.3 analytics plugin architecture) map onto that slot; there is no independent research claim being tested here, only an engineering deliverable. Recommend resolving the RQ/SRQ terminology globally before final assembly rather than inventing a label here.

---

## 5.6 Web Interface Implementation

### 5.6.1 Frontend structure

The frontend is deliberately dependency-light: vanilla JavaScript, Jinja2 server-side templates, no bundler, no framework, no `import`/`export` module graph (Chapter 4 §*build/architecture rationale*). Structure is enforced by convention rather than tooling:

- **One template per page** (`frontend/templates/*.html`), all extending a single shared shell, `base.html`, whose only override point is `{% block content %}{% endblock %}`. Every page inherits the same `<head>`, navigation, footer, and script-load order from this one file.
- **One JavaScript file per page** for anything beyond trivial markup (`census.js`, `heritage.js`, `kg_explore.js`, `ask.js`, plus the general-purpose `main.js` for the home page and `analytics.js` for the dashboard), and one shared utility module (`map.js`) loaded on every page regardless of whether that page has a map.
- **No router.** Navigation is server-side — each page is a distinct Flask `GET` route rendering a distinct template; there is no client-side route table or history-API page-swapping.
- **State is a plain object**, not a store/reducer pattern. Each page-controller file declares a single module-scope `state` object and mutates it directly from event handlers and fetch callbacks. `census.js`'s is representative:

```js
const state = {
  year: 1841, selectedTownland: null, townlandDetails: null,
  workhouseData: null, unifiedSummary: null,
  recordsByTownlandYear: {},   // key(townland, year) -> record
  summaryByYear: {},           // year -> summary payload
  geoLayer: null, geoFeatureByName: {}, geoNameToCanonical: {},
  satelliteMap: null, satelliteOverlay: null, layerMap: {},
};
```

  `main.js`'s equivalent (`state.records`, `state.recordById`, `state.recordsByName`, `state.townlandIndex`, `state.familyGroups`, `state.activeGroups`) is built once from a single `/api/unified/records` fetch by `buildIndexes()`, giving O(1) person/townland/family lookups over the full 13,707-row dataset without a client-side database.
- **Backend communication is exclusively `fetch()`.** Ordinary pages issue plain `fetch(...).then(r => r.json())` calls against the JSON API documented in §5.6.6. The one exception is the Ask page, which needs a **streamed POST** — the browser's native `EventSource` API only supports `GET` with no body, so `ask.js` hand-rolls an SSE client on top of `fetch()` + `ReadableStream` instead (§5.6.8). Every other page-controller writes its own inline `try { await fetch(...); if (!res.ok) … } catch { … }` at each call site; there is no shared `apiFetch()` wrapper across the five page-controller files.

### 5.6.2 View inventory — resolving "eight vs six"

The application ships **eight distinct Jinja templates**, reached via **nine `GET` routes** (one template, `kg_explore.html`, is registered under two paths for backward compatibility):

| # | Template | Route(s) | Role |
|---|---|---|---|
| 1 | `index.html` | `/` | Home — map explorer, townland/surname/family browsing, three modal-based record viewers |
| 2 | `census.html` | `/census` | Census demographics — choropleth + detail panel |
| 3 | `heritage.html` | `/heritage` | Landscape features — monuments/wells/sites map |
| 4 | `ask.html` | `/ask` | Natural-language Q&A (search) |
| 5 | `about.html` | `/about` | Estate context — glossary, source description |
| 6 | `info.html` | `/info` | Estate context — Famine/clearances narrative essay |
| 7 | `analytics.html` | `/analytics` | KPI/chart dashboard |
| 8 | `kg_explore.html` | `/kg-explore`, `/explore-knowledge` | Knowledge-graph force-graph explorer |

The "six" figure in earlier planning corresponds to the five *conceptual* views this section's brief names by function — people/townland browsing, census demographics, landscape features, estate context, natural-language search — collapsing "estate context" to one slot and folding search in as a sixth. In the shipped application, "people/townland" is a single page (the home map explorer covers both), "estate context" is **two** separate templates (`about.html` and `info.html`, serving different registers — reference glossary vs. narrative essay), and two further pages exist outside that five-view framing entirely: the analytics dashboard and the knowledge-graph explorer. Objective 5.1 in Chapter 1 already commits to the correct figure ("eight distinct user-facing pages"); this section adopts that count as authoritative and the six-view framing should be read as a functional grouping, not a page count.

One page is worth flagging for a "what quality looks like" caveat: `/analytics` currently renders with `error` populated for every dataset — `analytics/registry.py`'s data-file path resolution has an off-by-one that resolves above the repository root, so every KPI/chart raises `FileNotFoundError` at request time (Chapter 4 covers the analytics-module contract; this is a runtime defect in that contract's current implementation, not a design gap). The route never 500s — `main.py::analytics()` catches the exception, sets `error = str(e)`, and still renders the page — so the honest-failure behaviour described in §5.6.4 below holds even here, but the dashboard is not currently demonstrable end-to-end.

### 5.6.3 Rendering a traceable answer

The Ask result panel (`#askResult` in `ask.html`) is the system's primary vehicle for traceability. It is not one component but **seven numbered, independently-toggled sections**, each populated by a dedicated render function called in a fixed order once the terminal SSE `"result"` frame arrives (§5.6.8):

1. **LLM Interpretation** (`renderWarnings`, then the Markdown answer) — amber boxes for every string in `payload.warnings`, then `payload.llm_rephrased_answer` parsed by `marked.js` into `#askLlmAnswer`.
2. **SQLite Database Result** — the executed-SQL result table (`renderStructured`, §5.6.7's sibling table renderer) plus, for estate-wide questions with no townland context, an async-fetched "All Townlands" overview panel.
3. **Knowledge Graph Result** — VRTI townland-metadata cards, hidden entirely unless KG rows are present.
4. **Explainability & Provenance** — `renderExplainability()` builds five to seven plain-English rows (tables queried, records retrieved, filters applied, geographic scope, KG context, query strategy, memory reuse) by combining the backend's `query_provenance` object with **client-side regex inspection of the generated SQL text** — filters such as `"widows only"` or `"year range: 1847–1856"` are recovered by pattern-matching `HAS_*_RECORD = 1`, `GENDER = 'F'`, `YEAR BETWEEN … AND …` directly out of the SQL string, not from structured filter metadata the backend sends. This is a real fragility: any SQL phrasing the regex doesn't anticipate silently produces no filter line rather than an error.
5. **Generated Queries** — the raw SQLite/SPARQL text in `<pre>` blocks with copy-to-clipboard buttons.
6. A collapsible technical block (LLM connection, query provenance, summarised statistics, context used).
7. **Feedback** — thumbs up/down, writing to `ask_query_memory` on a positive vote (Chapter 4).

Two dedicated badge rows sit above these sections and carry the pipeline's internal confidence machinery directly into the UI rather than only its narrative text — this is the loud-failure instrumentation referenced below (§5.7 rubric point on honesty). The numeric-consistency gate (Chapter 4 describes the gate's mechanism; here is its exact UI rendering) exposes three literal outcome strings verbatim as three visually distinct chips:

```js
const gateStyles = {
  pass:        { label: "✓ Gate pass",          bg: "#f0fdf4", border: "#86efac", color: "#166534" },
  regenerated: { label: "⚠ Gate: regenerated",  bg: "#fffbeb", border: "#fcd34d", color: "#92400e" },
  fallback:    { label: "⚠ Gate: raw fallback", bg: "#fff1f2", border: "#fca5a5", color: "#9f1239" },
};
```

`fallback` is the honest-degradation case: the backend has already blanked `llm_rephrased_answer` and the frontend falls back to rendering `payload.actual_answer` (the deterministic, template-derived text) instead of a narrative summary — the failure is visible to the user as a change in answer register, not hidden behind a plausible-sounding sentence. A sibling badge renders the cross-verifier's `agree`/`disagree` verdict (`disagree` surfaces the specific unsupported claim text in the chip's tooltip); a `skip` verdict — the common case on the default pipeline's happy path, since the verifier only runs when SQL generation has already degraded — renders no badge at all rather than a neutral one, so its *absence* is itself informative to a reader who knows to look for it.

### 5.6.4 ER confidence bands in the UI

Two independent UI treatments exist for the same underlying four-label confidence scale (`CONFIRMED_MATCH` ≥ 0.75, `POSSIBLE_MATCH` 0.60–0.74, `WEAK_CANDIDATE` 0.40–0.59, `NO_MATCH` < 0.40 — Chapter 4 covers the scoring model; `WEAK_CANDIDATE`/`NO_MATCH` are never surfaced in either UI path, since only `CONFIRMED_MATCH` and `POSSIBLE_MATCH` are promoted to `workhouse_unified_links`):

| Context | Rendering | Colour treatment |
|---|---|---|
| Person-record cards (`main.js::_whMatchCard`, current schema) | "Confirmed" / "Possible" labelled cards, each with a percentage match score and an explicit evidence list | green border/background (`#86efac`/`#f0fdf4`) for Confirmed, purple (`#c4b5fd`/`#faf5ff`) for Possible |
| Census townland detail panel (`census.js::renderWorkhouseSection`) and a legacy `main.js` code path (`workhouseSectionHTML`, tiered `High`/`Medium`/`Low`) | Fixed CSS badge classes | `.wh-High{background:#d4edda}`, `.wh-Medium{background:#fff3cd}`, `.wh-Low{background:#f8f9fa}` |

Both treatments deliberately avoid green-means-certain framing: every linked card, confirmed or possible, carries the same fixed disclaimer text — *"⚠ Please verify: this workhouse record may or may not refer to the same person as the estate record."* — and confirmed/possible links are rendered in visually and structurally separate lists (`linked_workhouse_records` vs. `possible_workhouse_matches`/`please_check_records`), never merged into one undifferentiated result set. That separation is the UI-layer enforcement of the "never silently merged" guarantee Chapter 4 establishes at the data-model level: the two arrays are distinct API-response keys, so a frontend author would have to actively concatenate them to violate the guarantee, rather than it being enforced only by convention.

### 5.6.5 Honest "no records" vs. an empty table

Three distinct backend signals drive an honest-empty rendering, each surfaced differently:

- **Ask page, no viable SQL** — `query_provenance.strategy = "validated_sql_unavailable"` and `availability.available = false`. `renderSuggestions()` checks `availability.available` before showing anything; when false, it renders the backend's `suggestions` array as blue info cards instead of an empty results table — the user is told *why* nothing came back and what to try instead, rather than being shown a zero-row table indistinguishable from "the answer is zero."
- **Census townland panel, no population data** — when `getBestRecord`/`getRecord` return null for every year in `ALL_DATA_YEARS`, `renderTownlandPanel()` renders a red "No census population data" box in place of the timeline table, with a townland-specific explanation hard-coded for the one recurring case (`NEWTOWN`, whose data is filed under electoral-division name variants the alias-matching step cannot resolve automatically).
- **Workhouse-by-townland, missing parameter** — `GET /api/unified/workhouse-by-townland` with no `townland` returns `200 {"records": [], "linked": [], "unlinked": [], "error": "townland required"}` — the error is communicated in-band inside a `200` response rather than as an HTTP error status, which is a real API-contract weakness (Chapter 4 or a later limitations discussion should own the *why*; here it is simply the fact the frontend must — and does — branch on the presence of an `error` key rather than on response status).

In all three cases the distinguishing signal is a structured field (`availability.available`, an all-null year scan, an `error` string) checked *before* rendering, not the absence of DOM content after a failed fetch — an honest-empty state and a loading/broken state are never visually identical in this UI.

### 5.6.6 Per-view endpoint and rendering summary

| View | Primary endpoint(s) | Rendering |
|---|---|---|
| Home / people & townland browsing | `GET /api/unified/records` (single ~4.4 MB payload, client-indexed) | Leaflet GeoJSON townland layer + three modal viewers built from `state` indexes, no server-side pagination |
| Census demographics | `GET /api/census/`, `/summary`, `/townland`; `GET /api/unified/workhouse-by-townland`, `/api/unified/records?townland=` | Leaflet choropleth (`L.geoJSON` + hand-coded 7-bucket colour scale) + a second, independent satellite sub-map instance |
| Landscape features | Static `/static/data/{asi,holywells,monuments}_wicklow.geojson` (**not** an `/api/*` endpoint — read directly by the browser) + `/static/data/townlands.json` | Leaflet `L.circleMarker` per feature, no clustering, hand-rolled point-in-polygon/radius filtering |
| Ask / search | `POST /api/ask/query` (SSE) + `/api/ask/{llm-status,townland-catalog,townland-suggest,estate-overview,feedback}` | Seven-section result panel (§5.6.3) + hand-written inline-SVG charts, no charting library |
| Estate context (about/info) | none — fully static template content | Plain HTML/CSS; `info.html` embeds a YouTube background player and ~310 lines of page-local vanilla JS for scroll-reveal/carousel/timeline widgets |
| Analytics | `analytics.registry.discover_modules()` (server-side, no client fetch) | Chart.js, config JSON serialised server-side into a non-executing `<script>` tag and picked up by a 13-line generic loader |
| Knowledge graph explorer | `GET /api/kg/graph`, `/townland-rich/<name>`; `POST /api/kg/compare`, `/explain-mismatch` | D3 v7 force-directed SVG (not Canvas), tiered link/charge/collide forces by hierarchy level |

Leaflet 1.9.4 (CDN), D3 v7 (CDN, KG-explorer page only), and Chart.js 4.4.1 (CDN, analytics page only) are the three charting/mapping libraries in use; no library is bundled or npm-installed into the served frontend — the `leaflet` npm package listed in `package.json` is not actually consumed as an ES module.

### 5.6.7 Leaflet map layer implementation

Basemap layer switching is centralised in one 178-line shared utility, `map.js`, deliberately *not* a map-controller — it creates no `L.map` instance itself, only builds/switches tile layers on a map instance the caller already created:

```js
async function initLayerSwitcher(mapInstance, containerId) {
  let config;
  try {
    const res = await fetch("/api/map/layers");
    config = res.ok ? await res.json() : _fallbackLayerConfig();
  } catch (e) {
    config = _fallbackLayerConfig();
  }
  const layerMap = buildLayerMap(config.layers);
  // restore localStorage preference, add default/saved layer, render switcher UI
  return { layerMap, overlayMap, config };
}
```

Tile URLs are never hard-coded in the caller — `GET /api/map/layers` is the single source of truth (`map_service.MAP_LAYERS`: `standard` = OSM, `satellite` = Esri World Imagery, `terrain` = OpenTopoMap, plus a `labels_overlay` auto-composited on top of satellite tiles since raw satellite imagery carries no place names). Adding a new basemap requires one dict entry server-side and no frontend change. `census.js` and `heritage.js` both call `initLayerSwitcher()`; the home-page map is explicitly excluded ("managed exclusively by `main.js`" per the module's own header comment) and has no layer-switcher UI, despite an empty placeholder `<div>` existing in `index.html`'s markup for one.

GeoJSON layers are styled per page, not by a shared helper:

- **Census choropleth**: `L.geoJSON(geo, { style, onEachFeature })`, fill colour from a fixed six-threshold red scale (40/80/140/220/350 population, static values chosen by inspection, not a D3 quantile scale), selection outline drawn as a heavier stroke weight on click.
- **Heritage points**: three per-dataset `L.circleMarker` layer groups (Archaeological Sites `#9c8a6e`/`#c4b090`, Holy Wells `#2d7da0`/`#5aa5c8`, Monuments `#6d4e8c`/`#9b7abf`), spatially filtered client-side by a hand-written ray-casting point-in-polygon test plus a haversine-distance radius check — no Turf.js or server-side spatial query is involved.
- **Home-page townland polygons**: a three-layer animated "glow" highlight (`requestAnimationFrame`-driven pulsing opacity on outer/mid strokes around a bright inner outline) on click/hover, plus a parallel "Historic Landscape" overlay mode that reuses the same map instance and the same three static heritage GeoJSON files as the dedicated `/heritage` page, toggled in place rather than navigating away.

### 5.6.8 SSE consumption

`ask.js` cannot use the browser's native `EventSource` API because the endpoint is `POST` with a JSON body (`EventSource` only supports parameterless `GET`), so it hand-parses the SSE wire format over a `fetch()` `ReadableStream`:

```js
async function consumeSSEPost(url, body, onEvent) {
  const res = await fetch(url, { method: "POST",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");        // SSE frames end on a blank line
    buffer = parts.pop() || "";                 // keep incomplete trailing frame
    for (const part of parts) {
      const dataText = part.split("\n").filter(l => l.startsWith("data:"))
        .map(l => l.slice(5).trim()).join("\n");
      if (dataText) { try { onEvent(JSON.parse(dataText)); } catch {} }
    }
  }
}
```

Three `evt.type` values are handled: `"progress"` updates a live per-stage tracker; `"error"` throws synchronously, propagating to the caller's `try/catch`; `"result"` is the terminal payload driving §5.6.3's seven-section render. Progressive rendering is real but coarse-grained — the itemised stage tracker (`#askProgress`) updates live as `"progress"` frames arrive (`✓`/spinning `⟳`/hidden-pending per stage, with timing chips), but the result panel itself is populated **only once, after the stream closes**, from the single `"result"` frame — there is no incremental table/answer rendering mid-stream.

One documented gap worth reporting rather than eliding: `ask.js`'s `progressOrder` whitelist recognises nine stage keys, but the backend pipeline (`ask_service.py`) also emits `resolving_identity`, `querying_graphrag`, `querying_graphdb`, `synthesising_answer`, and `done` — stages outside the whitelist still update the free-text status line (`#askStatus`) but never get their own row in the itemised tracker. The frontend's picture of pipeline progress has drifted slightly out of sync with the backend it renders; this is a real, currently-uncorrected inconsistency, not a deliberate design choice.

### 5.6.9 Appendix E

Appendix E screenshots (home map explorer, census choropleth + detail panel, heritage spatial filter, Ask result panel showing the confidence/gate/verifier badge row, KG explorer force graph) are outstanding at time of writing. This section is written to stand independently of them — every UI element it describes is named precisely enough (element ID, badge label, colour) to be captured and referenced by figure number once produced, without requiring the prose above to change.

---

## 5.7 Deployment and Configuration

### 5.7.1 Deployment target and CI/CD

The application runs on **Azure App Service (Linux)** at `coolattin-app.azurewebsites.net`, resource group `coolattin-rg2`, region Italy North — a single instance, no autoscaling configuration observed in the repository. Deployment is push-to-deploy: every push to `main` triggers `.github/workflows/azure-deploy.yml`, which:

1. Authenticates to Azure via OIDC (managed identity `coolattin-gh-identity`; no long-lived service-principal secret is checked in or stored in GitHub — the workflow exchanges `AZURE_CLIENT_ID`/`AZURE_TENANT_ID`/`AZURE_SUBSCRIPTION_ID` repository secrets for a short-lived token).
2. Overwrites `requirements.txt` with `requirements-azure.txt` — a trimmed dependency set that drops `psycopg` (the optional pgvector backend, unused since the deployed instance runs SQLite) and never includes `torch`/`sentence-transformers` at all (§5.7.3).
3. Zips the repository, excluding `venv/`, `.env*`, database WAL/SHM sidecar files, `node_modules/`, `exports/`, `docs/`, `tests/`, `scripts/`, and `eval/` (`.webappignore` mirrors this exclusion list for manual `az webapp up` deploys).
4. Deploys via `az webapp deploy --type zip`; **Azure Oryx builds the Python virtual environment on the target machine** from the zipped `requirements.txt` — there is no pre-built container image, so the build step (and its failure modes — pip timeouts on large packages, §5.7.3) happens on every deploy, on Azure's infrastructure, not in CI.
5. Re-enforces the gunicorn startup command via `az webapp config set` on every deploy, because the Azure portal's stored startup command can be reset independently of source control:

```
gunicorn --bind=0.0.0.0:$PORT --timeout 600 --workers 2 --worker-class gthread --threads 4 \
  --access-logfile '-' --error-logfile '-' app:app
```

The `--worker-class gthread --threads 4` choice is load-bearing, not incidental: a single synchronous gunicorn worker blocks for the full duration of an Ask SSE stream, so a second concurrent request to any route would hang until the first stream closes. `Procfile` (`web: gunicorn app:app`, no explicit worker flags) exists only as an Oryx auto-detection fallback; the flag-bearing command above is the one actually enforced against the running App Service resource. `startup.sh` covers local/manual starts and additionally handles a lazy `pip install` on first boot if Oryx's `antenv` virtual environment is absent, reusing it on subsequent starts.

No `runtime.txt` or `.python-version` file is present in the repository, so the Python interpreter version served in production is whatever Azure's Linux Python stack default resolves to at the App Service resource's configuration (visible via `az webapp config show`, not in source control); `CLAUDE.md` states Python 3.12 as the target development version, but that is not independently pinned for the deployed instance in anything this documentation set has read.

### 5.7.2 Data and index provisioning

| Component | Provisioning |
|---|---|
| SQLite (`coolattin.db`) | Not baked into the deploy image — `DATABASE_PATH=/home/site/wwwroot/coolattin.db` is set as an App Setting, pointing at App Service's persistent local storage; `ensure_schema()` runs idempotently on every cold start (§*architecture, Chapter 4/5 cross-reference*), so the schema is always current but the *data* must be populated by a separate ingest run against the deployed instance, not by the zip deploy itself. |
| `coolattin_sample.ttl` (RDF, 225,362 lines) | Baked into the deploy image — it lives under `data/`, which is not in the zip's exclusion list, so it ships with every deploy and is read locally by `kg_service.py` (no network call). |
| GraphDB (SPARQL triple store) | **External, not co-hosted.** Deployed separately on an Azure VM at `51.120.71.162:7200`, configured via `GRAPHDB_SPARQL_ENDPOINT` as an App Setting. The App Service container has no GraphDB process of its own; `GET /api/kg/graphdb-status` is a live network probe against that external VM, not a local health check. |
| VRTI SPARQL endpoint | External, third-party (`virtuoso.virtualtreasury.ie`), not self-hosted at any point. |
| Ollama | **Dev-only.** `OLLAMA_BASE_URL` defaults to `http://127.0.0.1:11434` — there is no Ollama daemon in the Azure App Service container, and nothing in the deploy pipeline provisions one. The LLM provider cascade (Chapter 4 covers the cascade's fallback logic) unconditionally appends `ollama` as the last fallback regardless of whether a daemon is reachable, so in production that final cascade step is always attempted and always fails closed (connection refused) rather than being skipped — a real, currently-accepted inefficiency rather than a silent gap: the failure is logged and the cascade correctly reports no LLM available downstream, it just spends one avoidable timeout getting there. |

### 5.7.3 Resource constraints

The App Service plan tier is not recorded anywhere in the repository (visible only via the Azure portal/`az appservice plan show`, out of scope for what source control captures), but one concrete constraint *is* documented directly in `requirements.txt`: `sentence-transformers`/`torch` (the local BAAI/bge-large-en-v1.5 embedding provider, ~2 GB installed) are commented out specifically because they exceed what Oryx can reliably `pip install` within Azure App Service's build environment (pip timeout / OOM during the Oryx build step, per the inline comment and the README's troubleshooting section). Consequently the **documented default** embedding provider (`EMBEDDING_PROVIDER=local`, `config.py`) is not the **deployed** one — production explicitly overrides it to `EMBEDDING_PROVIDER=voyage` (routing through `voyage_embeddings.py`, which in current code actually calls the Cohere Embed API, not Voyage AI — a naming holdover the module's own docstring acknowledges) with a `COHERE_API_KEY`/`VOYAGE_API_KEY` App Setting supplying the credential. This is a real code-default vs. deployed-override gap, not a documentation error: a fresh clone run locally with no `EMBEDDING_PROVIDER` set will attempt to load the 2 GB local model; the deployed instance never does.

No cold-start timing measurement for the embedding-model load path exists in the repository (this document does not claim one) — the practical consequence documented is architectural (local embeddings are excluded from Azure entirely) rather than empirical.

### 5.7.4 Configuration and secrets

Configuration resolution order, from `config.py`'s hand-rolled dotenv loader (no `python-dotenv` dependency): **real process environment variables always win**, then `.env.local`, then `.env`, with each file only setting a key if it is not already present from an earlier, higher-priority source. This ordering is what lets Azure App Service Application Settings (injected into the process environment before `create_app()` runs) transparently override anything checked into `.env`/`.env.local` for local development, with no code branching on "am I running on Azure."

**Secrets are stored as plain Azure App Service Application Settings** (`az webapp config appsettings set`), not Azure Key Vault — every credential in the required-settings list below (`SECRET_KEY`, `OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`/`COHERE_API_KEY`, `GROK_API_KEY`, `ADMIN_API_KEY`) is set as an unencrypted-at-rest-in-the-portal-UI environment variable, visible to anyone with Contributor access to the App Service resource. No Key Vault reference (`@Microsoft.KeyVault(...)`) syntax appears anywhere in the deployment configuration this documentation set has read. This is reported here as the mechanism actually in place, not evaluated as a design choice (Chapter 4/6 territory).

### 5.7.5 Feature flags

| Flag | Default (`config.py`/`ask_service.py`) | Production value | Toggles |
|---|---|---|---|
| `ASK_USE_NEW_PIPELINE` | `true` (code default) | *(unset — inherits code default `true`)* | Orchestrated 7-phase default pipeline vs. legacy fast-lane/intent-router pipeline (§5.7.6) |
| `ASK_LLM_PROVIDER` | `auto` | `auto` | LLM provider selection strategy for SQL generation/synthesis calls |
| `LLM_ALLOW_PAID` | `true` | `true` | Whether Anthropic (Claude)/Grok paid API calls are permitted, vs. free OpenRouter/Ollama only |
| `ASK_SYNTHESIS_MODEL` | `claude` | `claude` | Which provider handles final answer synthesis specifically (independent of `ASK_LLM_PROVIDER`, which governs SQL generation) |
| `ASK_ALLOW_HEURISTIC_FALLBACK` | `0` (off) | *(unset — off)* | Whether a failed SQL generation/repair falls back to a heuristic guess vs. an honest "no validated SQL" message (§5.6.5) |
| `ASK_GENERATE_VRTI_SQL_WITH_LLM` | `0` (off) | *(unset — off)* | Whether VRTI SPARQL queries are LLM-generated (slower, experimental) vs. templated |
| `GRAPHRAG_ENABLED` | `true` | `true` | In-process NetworkX property-graph subsystem (k-hop BFS enrichment) |
| `GRAPHDB_ENABLED` | `true` | `true` | Whether the external GraphDB SPARQL probe/comparison endpoints are attempted at all |
| `EMBEDDING_PROVIDER` | `local` | `voyage` (Cohere-backed, §5.7.3) | Which embedding backend serves dense retrieval where used |

### 5.7.6 The deployment-default gotcha

Before commit `4d18308` ("Enable the new Ask pipeline by default", 2026-06-02), the flag's **code-level** default was the empty string:

```python
# Default FALSE so the existing pipeline is unaffected in production.
ASK_USE_NEW_PIPELINE = os.environ.get("ASK_USE_NEW_PIPELINE", "").strip().lower() in {"1","true","yes","on"}
```

Any deployment that did not explicitly set `ASK_USE_NEW_PIPELINE` as an App Setting — which was the case before this commit — silently served the **legacy** four-fast-lane/intent-router pipeline in production, even once the orchestrated pipeline was the one being actively developed and evaluated against. The fix was made in the code default itself, not only in `.env.example`'s documentation:

```python
# Default TRUE so the newer orchestrated pipeline is the standard runtime path.
ASK_USE_NEW_PIPELINE = os.environ.get("ASK_USE_NEW_PIPELINE", "true").strip().lower() in {"1","true","yes","on"}
```

This matters for reproducibility specifically because it closes the gap between "what the .env.example documents" and "what actually runs when an App Setting is absent" — after this commit, an operator (or an evaluator standing up a fresh instance) gets the intended pipeline by default rather than needing to know to set the flag explicitly. The commit predates the `v1.0-demo-freeze` tag (§5.7.7) by eight days, so the frozen configuration inherits the corrected default.

### 5.7.7 The v1.0 freeze — precise definition

`v1.0-demo-freeze` is a git tag on commit `bfd79e0` ("Complete RQ6 SQL-vs-SPARQL evaluation with real loaded-graph data"), dated 2026-06-10. It is a **commit tag**, not a separate config file or branch — reproducing the evaluated system means checking out that commit (`git checkout v1.0-demo-freeze`) and combining it with the specific runtime configuration below, since the tag alone does not capture App Settings (which live only in Azure, not in source control):

| Axis | Frozen value |
|---|---|
| Commit | `bfd79e0` (`v1.0-demo-freeze`, 2026-06-10) |
| `ASK_USE_NEW_PIPELINE` | `true` (code default, no override needed — §5.7.6) |
| `ASK_SYNTHESIS_MODEL` | `claude` |
| Embedding provider (deployed) | `voyage` → Cohere `embed-english-v3.0`, **1024-dimensional** |
| GraphDB RDF graph | `coolattin_sample.ttl` — 225,362 lines, **143,123 triples** (figure taken directly from `scripts/generate_report_docx.py`'s own reporting logic, which states this count in three separate places; the larger `data/seed/coolattin.ttl`, 311,654 lines, is a fuller uplift artefact that is **not read by any Python module in the running app** and should not be cited as the deployed graph's size) |
| Workhouse ER confidence bands | `CONFIRMED_MATCH` ≥ 0.75, `POSSIBLE_MATCH` 0.60–0.74, `WEAK_CANDIDATE` 0.40–0.59, `NO_MATCH` < 0.40 (`rapidfuzz.fuzz.token_sort_ratio`-based scoring; this corrects an earlier 0.85/0.70/0.50 figure that appears in `CLAUDE.md` and should not be repeated in the thesis) |
| GraphRAG node embeddings | 1024-dimensional, one per retrievable node (Person/Townland/CivilParish/EmigrationEvent/EvictionEvent) — **not** one-per-community |

The two triple-count and embedding-dimension figures above are each stated once in this section and should be the only citation of them in the thesis; both were verified against the code paths that produce/consume them (`kg_service._ttl_path()` and the `scripts/generate_report_docx.py` reporting logic for the triple count; `graphrag.py`'s node-embedding construction for the dimension), not carried forward from an earlier draft's ~189K estimate, which this documentation set found no code-level support for.

### 5.7.8 What is out-of-path in the freeze

Three subsystems are present in the frozen commit's source tree but **not exercised** by the default, evaluated pipeline (`ASK_USE_NEW_PIPELINE=true`), and should not be described in the results chapter as active unless the legacy pipeline is separately re-enabled and re-evaluated:

- **Legacy intent-routing** (`intent_router.py`'s COMPARATIVE→RELATIONAL→ANALYTICAL→FALLBACK classification, and its four fast lanes) — only reachable with `ASK_USE_NEW_PIPELINE=false`, which is not the frozen configuration.
- **GraphDB SPARQL comparison within the Ask pipeline** (Stage 4.5) — gated on `intent_route in (RELATIONAL, COMPARATIVE)`, but the default pipeline hardcodes `intent_route = "direct"`, so this ~80-line block never executes regardless of `GRAPHDB_ENABLED`. (The standalone `/kg-explore` page's SQL-vs-SPARQL comparison tool is unaffected — it calls GraphDB directly, outside the Ask pipeline.)
- **Embedding-index/Phase-4 template retrieval fast lane** — part of the legacy pipeline's fast-lane chain, inert for the same reason as legacy intent-routing.

All three remain fully present in the codebase (they are dead code paths, not removed code), which is why `EMBEDDING_PROVIDER`/`GRAPHDB_ENABLED`/`GRAPHRAG_ENABLED` remain configured and documented even though one of the three (GraphDB, specifically *within the Ask pipeline*) has no effect on any answer the frozen configuration actually produces.
