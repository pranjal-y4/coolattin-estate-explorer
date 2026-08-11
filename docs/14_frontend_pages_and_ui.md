# 14. Frontend Pages & UI

This document covers the page shell (`base.html`), each content-page template's
structure, and the two general-purpose JavaScript files (`main.js`, `i18n.js`)
plus the design system in `main.css`. It intentionally does **not** go deep on
the complex per-page interactive logic in `map.js`'s callers, `census.js`,
`heritage.js`, `kg_explore.js`, `ask.js`, or `marked.min.js` — those are
covered in `15_frontend_map_and_visualizations.md`. Where those pages are
described here, only their Jinja template structure (HTML skeleton, blocks,
IDs the JS hooks into) is documented, not their runtime behaviour.

For the exact Content-Security-Policy allow-list (`unpkg.com`,
`cdn.jsdelivr.net`, `fonts.googleapis.com`/`fonts.gstatic.com`,
`youtube.com`/`youtube-nocookie.com`) and the security headers applied to
every HTML response, see `01_architecture_overview.md` §2.7. This document
only notes *which* CDN scripts each template loads, not the header policy
that allows them.

All templates live in `frontend/templates/` and are rendered by
`backend/routes/main.py` (the `main` blueprint, no URL prefix). All static
assets live in `frontend/static/`. There is no build step or bundler —
every `<script>` and `<link>` tag in the templates references a file that is
served as-is by Flask's static handler, and cache-busting is done manually
via a `?v=N` query-string suffix on each `url_for('static', ...)` call that
the developer increments by hand when the file changes.

---

## 1. `base.html` — the shared page shell

Every other template begins with `{% extends "base.html" %}` and fills the
single `{% block content %}{% endblock %}` block that sits inside `<main>`.
Nothing else in `base.html` is overridable per-page — there is only one
block, so a child template cannot customise the `<head>`, nav, or footer
without editing `base.html` directly (which several pages route around by
injecting a page-scoped `<style>` block or extra `<script>` tags *inside*
their own `content` block instead).

### 1.1 `<head>`

| Element | Detail |
|---|---|
| `<title>` | `{{ title or "Coolattin Lineage \| Digital Estate Archive" }}` — every route in `backend/routes/main.py` passes an explicit per-page `title=` (e.g. `"About Coolattin Lives"`, `"Census Explorer"`, `"Ask the Archive"`, `"Historic Landscape · Coolattin"`, `"Explore Knowledge Graph · Coolattin"`), so the bare fallback string only appears if a future route forgets to set one. |
| Viewport meta | Standard `width=device-width, initial-scale=1`. |
| Web fonts | `Cormorant Garamond` (weights 400/500/600/700, italic 400/600), `Inter` (400/500/600/700), `Playfair Display` (600/700) — all loaded from `fonts.googleapis.com` in a single combined `<link>` with `preconnect` hints to both `fonts.googleapis.com` and `fonts.gstatic.com` (the latter with `crossorigin`). |
| Leaflet CSS | `https://unpkg.com/leaflet@1.9.4/dist/leaflet.css` — pinned to 1.9.4, loaded on **every** page regardless of whether that page has a map. |
| App CSS | `{{ url_for('static', filename='css/main.css') }}?v=20260622e` — the cache-bust token is a date-stamped string, not a simple integer, reflecting the "Bust CSS cache…" commits in the project history. |

### 1.2 Nav (`<header class="nav-wrap">`)

Structure: `.nav-wrap > nav.nav.container` containing three children:

1. **`.brand`** — a link to `main.home` with a `⛰️` emoji mark (`.brand-mark`) and two-line brand text (`.brand-name` "Coolattin Lineage" / `.brand-sub` "Digital Estate Archive").
2. **`#navBurger.nav-burger`** — a three-span hamburger button, `aria-expanded="false"`, only shown via CSS at `max-width: 720px`.
3. **`#navLinks.nav-links`** — the link list plus the language switcher plus the primary CTA:
   - Links to `main.home` (`#home`, `#records`, `#research`, `#contact` — all in-page anchors on the home page), `main.about`, `main.census`, `main.ask`, `main.heritage`, `main.explore_knowledge`, `main.info`.
   - Most links carry `data-i18n="…"` attributes (`home`, `about`, `records`, `research`, `contact`, `census`, `exploreMap`) so `i18n.js` can retranslate them; the Ask/Heritage/Knowledge-Graph/Info links do **not** have `data-i18n` keys and are always English.
   - `.lang-switcher` — a static `EN` label plus `.lang-toggle` (a two-segment pill showing `EN`/`GA`) that `i18n.js` wires up on `DOMContentLoaded`.
   - `.btn.btn-primary.nav-cta` — "Explore Map" (🔎), linking to `main.home` + `#explore`.

**Observed gap:** the CSS defines `.nav-links.open { display: flex; }` (see §5.7) as the mechanism for revealing the mobile menu, and `#navBurger` has `aria-expanded="false"` in the markup, but **no JavaScript file in the project ever adds/toggles the `.open` class or updates `aria-expanded`** — a repo-wide `grep` for `navBurger` / `nav-burger` finds only the CSS and this HTML. On a narrow viewport the hamburger button is present but inert; the nav links become inaccessible below the `720px` breakpoint unless the user's browser window is widened. Likewise, `.nav-wrap:is(.scrolled)` has a CSS rule for an on-scroll shadow, but nothing in `main.js` (or any other JS file) ever adds a `scrolled` class — that effect is dormant CSS.

### 1.3 `<main>`

Just `{% block content %}{% endblock %}` — the entire page body for every route lives here.

### 1.4 Institutional partners strip

A hard-coded section (inline `style=` attributes, not CSS classes) showing four logos, each wrapped in an `onerror="this.style.display='none'"` `<img>` so a broken/missing logo silently disappears rather than showing a broken-image icon: Trinity College Dublin (`images/tcd-logo.png`, local static), Virtual Record Treasury of Ireland (`images/vrti-logo.png`, local static), Courthouse Arts Centre and Wicklow County Council (both hot-linked from `https://coolattinlives.ie/img/...` — external, not proxied).

### 1.5 Footer

`<footer class="footer" id="contact">` — a four-column `.footer-grid` (brand block with social icon placeholders that link to `#`; Research, Resources, Support link columns, mostly `main.home` in-page anchors plus a couple of `#` placeholders) followed by `.footer-bottom` with a copyright line (`© {{ year or 2026 }} Coolattin Lineage Project`) and Privacy/Terms/Cookies mini-links that all point to `#`. No template passes a `year` context variable, so the footer always shows the literal fallback `2026`.

### 1.6 Scripts (end of `<body>`, in this exact order)

| Order | Script | Notes |
|---|---|---|
| 1 | `https://unpkg.com/leaflet@1.9.4/dist/leaflet.js` | Loaded globally even on pages with no map (about, analytics, ask), since `base.html` cannot vary this per page. |
| 2 | `js/i18n.js?v=4` | Must run before `main.js` because `main.js`'s DOM (townland select, etc.) doesn't itself call `t()`, but other inline page scripts (e.g. `exploreOnMap` on the home page) run independently of load order; i18n's own `DOMContentLoaded` listener runs `translatePage()` once fonts/DOM are ready. |
| 3 | `js/map.js?v=6` | A small shared Leaflet **utility** module (not a page controller) — see §6. |
| 4 | `js/main.js?v=9` | The general-purpose page script, documented in full in §2. |

Individual page templates append their own page-specific `<script>` tags (Chart.js + `analytics.js` on the analytics page; `marked.min.js` + `ask.js` on the Ask page; `census.js`, `heritage.js`, `kg_explore.js` on their respective pages) *inside* their `content` block, after this shared set has already been declared — so those scripts execute after Leaflet, i18n, map.js, and main.js have all run.

---

## 2. Page templates

### 2.1 `index.html` (`/`, route `main.home`)

The largest and most interactive template. Structure, top to bottom:

1. **`<section class="hero" id="home">`** — full-viewport (`height:100vh`) hero with a centred glass-morphic text card (`.hero-centre`): eyebrow ("County Wicklow · Coolattin Estate"), `<h1 data-i18n="heroTitle">`, subtitle `<p data-i18n="heroSubtitle">`, and `.hero-actions` containing the two redesigned CTA buttons — `.hero-btn-primary` ("Explore on the Map" → `#explore`) and `.hero-btn-ghost` ("Ask the Archive" → `main.ask`). See §5.3 for the CSS behind these (the "hero button redesign" / "solid orange Ask button" from recent commits). A scroll-hint SVG chevron sits at the bottom of the hero.
2. **Photo cards section** (`#about-teaser`'s sibling, unlabelled) — two `<figure>` cards (County Wicklow / Wicklow Mountains) with inline-styled hover transforms (`onmouseover`/`onmouseout` JS attributes directly in the HTML, not CSS `:hover` — this is one of several places the page uses inline event-handler attributes instead of a stylesheet rule).
3. **`#about-teaser`** — a single soft panel linking to `main.about`.
4. **Three `<div id="…Modal" class="modal-overlay">` blocks**, hidden until JS opens them (see §2.2 in `main.js`):
   - `#oldRecordModal` — townland-level tenant/family browsing (`#modalSurname`, `#modalBody`).
   - `#recordDetailModal` — single/grouped person record details (`#recordModalTitle`, `#recordModalBody`), wider (`max-width:1100px`).
   - `#glossaryModal` — static glossary of field labels (`#glossaryModalBody`), populated from the `labelTips` object in `main.js`.
5. **`#census-callout`** — a dark-green promo panel linking to `main.census`.
6. **`<section class="band" id="explore">`** — the Map Explorer, `.explore-layout` grid:
   - **`.filters-panel`** (aside) — `#townlandSelect` (populated async by `main.js`), `#surnameInput` with a `<datalist id="surnameSuggestions">` for autocomplete, `#surnameError` (hidden validation message), `#applyFiltersBtn`, and an "Open Glossary" button.
   - **`.map-panel`** — `#exploreMapPanelTitle` / `#exploreMapPanelSubtitle` (both text-content set by `main.js`), `#wicklowMap` (the Leaflet map container, `height:540px`), `#wicklow-map-layer-switcher` (an empty absolutely-positioned `<div>` that `map.js`'s `initLayerSwitcher()` — *not* called on this page, since the home map is "managed exclusively by main.js" per `map.js`'s own trailing comment — is **not** actually wired up here; the home map has no layer switcher UI despite the placeholder div existing in the markup), and `#detailsContent` (the "Data Explorer" story panel main.js rewrites on townland/marker click).
7. **`#records`** — three hard-coded `<article class="record-card">` example families (Byrne/Coolboy, Fox/Coolboy, Healy/Killinure) each with an inline `<script>`-free `onclick="exploreOnMap('Coolboy','Byrne')"` button. `exploreOnMap()` is defined in a page-local `<script>` block right after this section (not in `main.js`): it scroll-into-views `#explore`, then polls every 200 ms (up to 20 attempts) for `#townlandSelect` to have more than the placeholder option loaded, then sets the townland/surname inputs and synthetically clicks `#applyFiltersBtn`.
8. **`#faq`** — two `<details class="faq-item">` accordions (native HTML disclosure, no JS needed).

### 2.2 `about.html` (`/about`, route `main.about`)

Static content page, no page-specific `<script>`. Sections: hero band, a `.photo-strip` of five images hot-linked from `coolattinlives.ie` (each with `onerror="this.style.display='none'"`), an "Introduction to The Courthouse Arts Centre" block, a "What will you find here?" section with a hard-coded HTML `<table>` of the five source record sets (dates + NLI reference numbers), four `.panel.soft` cards describing each record set in prose, a full glossary of ~20 estate-terminology definitions (`a. r. p.`, Chief Tenant, Common, Undertenant, Workhouse, etc. — all inline-styled `.panel.soft` rows, not reusing `#glossaryModal`), and a closing callout promoting Jim Rees's book *Surplus People* with an external purchase link. Almost every text node carries a `data-i18n` key, but the Irish (`ga`) translations in `i18n.js` for this page are noticeably abbreviated/paraphrased versions of the English rather than full translations (see §3).

### 2.3 `analytics.html` (`/analytics`, route `main.analytics`)

Driven by `backend/routes/main.py::analytics()`, which calls `analytics.registry.discover_modules()` and `<module>.compute()` (see `CLAUDE.md`'s Analytics-are-pluggable note). Template context variables: `datasets` (list of `(id, name)` tuples for the pill selector), `current_dataset_id`, `result` (a dataclass-like object with `.dataset_name`, `.description`, `.kpis`, `.charts`, `.notes`), and `error` (a traceback string on failure).

Structure:
- `.dataset-picker` — one `<a class="dataset-pill">` per dataset, `?d=<id>` query param, `active` class on the current selection.
- `.kpi-grid` — one `.kpi-card` per `result.kpis[]` entry (`.value`, `.label`, optional `.hint`).
- `.charts-grid` — one `.panel.soft.chart-card` per `result.charts[]` entry: a `<canvas id="{{ c.chart_id }}">` plus an adjacent `<script type="application/json" id="{{ c.chart_id }}-config">` tag whose body is `{{ "type": c.type, "data": c.data|tojson, "options": c.options|tojson }}` — i.e. the chart spec is serialized server-side into a non-executing JSON `<script>` block, and picked up client-side by `analytics.js`.
- Notes list (`result.notes`) and a red-bordered error panel (`{{ error }}` in a `<pre>`) if `compute()` raised.
- Loads `https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js` from jsDelivr (pinned 4.4.1), then `js/analytics.js` (`defer`).

**`analytics.js` in full** (13 lines, the entire file):
```js
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("canvas[id]").forEach(canvas => {
    const configEl = document.getElementById(canvas.id + "-config");
    if (!configEl) return;
    try {
      const config = JSON.parse(configEl.textContent);
      new Chart(canvas, config);
    } catch (e) {
      console.warn("Chart init failed for", canvas.id, e);
    }
  });
});
```
It generically finds every `<canvas id="...">` on the page, looks for a sibling `<script id="{id}-config">`, parses its JSON text content, and constructs a `new Chart(canvas, config)`. There is no chart-type-specific logic here at all — all chart shaping (bar/line/pie, colours, axis config) happens server-side in the `analytics/*.py` modules, not in this file.

### 2.4 `info.html` (`/info`, route `main.info`)

A large (~1,430-line), fully self-contained "digital history essay" page about the Famine clearances, built as a single scrolling narrative with its own `<style>` block (all classes prefixed `.ip-` to scope them) and its own `<script>` block (~310 lines of vanilla JS, IIFE-wrapped `(function(){...})()`). It does not use `main.js` for any of its interactivity. Sections, in order: full-viewport hero with a YouTube background video, a five-card auto-advancing horizontal carousel ("Scroll Intro Cards"), an Estate/Fitzwilliam portrait two-column section, a clickable six-tier social-hierarchy pyramid, a year-by-year (1845–1856) timeline slider, a six-step "How the Scheme Worked" accordion, a two-sided "Philanthropy vs Profit" opinion slider, a six-card "Human Experience" pattern grid, a Legacy/Memory closing section, and a Sources & Further Reading bibliography grid.

Key `info.html`-local JS behaviours (none shared with other pages):
- **YouTube background** — loads `https://www.youtube.com/iframe_api`, defines `window.onYouTubeIframeAPIReady`, creates a muted, looping, chromeless `YT.Player` (video ID `L4_a80T4_1g`) inside `#ytPlayer`, and removes the static fallback background image (`#ipHeroBg.has-image`) once playback starts. `window.updateBgVideo()` is exposed globally to let a (currently absent from the rendered markup) video-ID input field swap the background video at runtime via regex-extracted YouTube ID.
- **Parallax** — a `scroll` listener translates `#ipHeroBg` vertically at `0.25×` scroll speed.
- **Scroll-reveal** — `IntersectionObserver` (`threshold: 0.12`) adds `.visible` to any `.reveal`-classed element once 12% visible, then unobserves it (one-shot).
- **Intro carousel** — `goToCard(n)` translateX's `#ipIntroTrack` by `-n*100%` and syncs dot indicators (`#ipIntroDots`); auto-advances every 4 s via `setInterval`, and clicking the track advances immediately and clears the auto-timer permanently.
- **Social hierarchy pyramid** — `TIERS` is a 6-entry array (Earl Fitzwilliam → Agricultural Labourers) rendered into `#ipPyramid`; clicking a tier toggles its `.ip-tier-detail.open` panel, closing any other open tier first (accordion behaviour, single-open).
- **Timeline slider** — `#ipRange` (1845–1856) drives `renderTimeline(yr)`, which filters a 12-entry `EVENTS` array to `year <= yr` and renders the last four as `.ip-tl-card`s into `#ipTlCards`.
- **Clearances step flow** — `STEPS` (6 entries) rendered into `#ipSteps`; click-to-expand accordion, first step open by default.
- **Philanthropy/Profit slider** — `#ipVerdictRange` (0–100) drives `updateVerdict(val)`, which fades `#ipPhilCol`/`#ipProfitCol` opacity inversely and updates percentage labels.
- **`window.loadVideo()`** — replaces `#ipVideoArea` with a "Video content coming soon" placeholder; there is a `.ip-video-section`/`.ip-video-wrap` CSS block styled for this feature but the corresponding HTML section (referenced in the CSS's numbered-comment scheme as "7. VIDEO SECTION") is not actually present in the rendered template body — the CSS and this JS function are vestigial/orphaned relative to the current markup.

### 2.5 `ask.html` (`/ask`, route `main.ask`) — structure only

All behaviour lives in `ask.js` (sibling doc). The template itself defines:
- A fixed top loading banner (`#askLoadingBanner`, `display:none` by default, teal→blue gradient) with a CSS `@keyframes spin` spinner.
- A page-local `<style>` block defining the `.ask-md` class family — scoped Markdown typography (headings, lists, tables with green-tinted zebra striping, blockquotes, code blocks) used to render the LLM's Markdown answer via `marked.min.js`; and `.tl-option`/`#askTownlandDropdown` styles for the custom townland-autocomplete widget.
- Input panel: `#askQuestion` (textarea), `#askTownlandHint` + `#askTownlandDropdown` (custom autocomplete, not a native `<select>`/`<datalist>`), `#askSubmit`, plus two rows of example-question buttons (`.ask-example[data-q="..."]`) — 8 general examples, 6 "research examples."
- Inline status widgets: `#askInlineStatus`/`#askInlineStatusText`/`#askInlineStage`, `#askStatus`, `#askProgress`, `#askError`.
- The result panel `#askResult` is subdivided into seven numbered sections, each a distinct `<div>` with a coloured left accent bar: **1** LLM Interpretation (`#askLlmAnswer`, `#askWarnings`, `#askInsightsBlock`), **2** SQLite Database Result (`#askTable`, `#askSourceTables`, `#askEstateOverview` for estate-wide queries), **2b** Knowledge Graph Result (`#askVrtiTable`, `#askKgContent` — hidden unless KG data is present), **3** Explainability & Provenance (`#askExplainContent`, `#askSuggestionsBlock`), **4** Generated Queries (`#askSqliteQuery` / `#askSparqlQuery` `<pre>` blocks with copy-to-clipboard buttons calling `navigator.clipboard.writeText` inline), **5** a chart block (`#askChartBlock`/`#askChart`, hidden unless present), a collapsible native `<details>` block for LLM Connection/Query Provenance/Summarised Statistics/Context Used, and **7** a feedback section (`#askFeedbackUp`/`#askFeedbackDown`/`#askFeedbackNote`/`#askFeedbackStatus`).
- Scripts: `js/marked.min.js` then `js/ask.js?v=26`.

### 2.6 `census.html` (`/census`, route `main.census`) — structure only

Two-column layout (`1fr 340px`): left column stacks the main choropleth map (`#censusMap`, a hard-coded 7-swatch population-density legend with a CSS `linear-gradient` underline, and a year `<input type="range" id="yearSlider" min="1841" max="1891" step="10">` + `#yearValue` readout) above a conditionally-shown satellite detail sub-map (`#censusTownlandSvgContainer` → `#townlandSatelliteMap`). Right column is a `position:sticky` sidebar (`#censusKpis`, `#censusTimeline`, `#censusTownlandDetail` for KG-sourced Gaelic name/coordinates, `#censusWorkhouseSection`). A `#census-data-source` badge (hidden by default) surfaces API freshness metadata. `#census-map-layer-switcher` is the mount point for `map.js`'s `initLayerSwitcher()`, called from `census.js`. Loads only `js/census.js?v=10` (relies on `map.js` already loaded by `base.html`).

### 2.7 `heritage.html` (`/heritage`, route `main.heritage`) — structure only

A three-column CSS Grid layout (`.hp-layout`: `minmax(280px,320px) minmax(420px,1fr) minmax(320px,360px)`, collapsing to a single column under `960px`) filling the viewport (`height: calc(100vh - 132px)`). Left panel (`.hp-left`): townland `<select id="hp-townland-select">`, a radius radiogroup (0/2 km/5 km), three heritage-layer checkboxes (Archaeological Sites / Holy Wells / Monuments — each with a coloured `.hp-toggle-dot`), and a Google Maps deep-link card (`#hp-gmaps-view`/`#hp-gmaps-directions`/`#hp-gmaps-sv`). Centre: `#heritageMap` (Leaflet mount) with `#hp-layer-switcher-container` and a loading overlay (`#hp-loading`, `.hp-spinner`). Right panel (`.hp-right`): three mutually-exclusive states — `#hp-right-empty` (default), `#hp-right-townland` (stats/narrative once a townland is chosen), `#hp-right-feature` (detail card once a marker is clicked). A below-the-fold `#hp-photos-section` grid is populated asynchronously. Loads only `js/heritage.js?v=3`.

### 2.8 `kg_explore.html` (`/kg`, route `main.explore_knowledge`) — structure only

Two-column layout (`#kg-wrap`: `1fr 360px`, stacking under `900px`) with a dark (`#0f172a`) SVG canvas panel (`#kg-svg`) for the force-directed County→Barony→Civil Parish→Townland graph, a search bar (`#kg-search`, `#kg-search-clear`, `#kg-reset-zoom`, `#kg-node-count`), an in-canvas colour-coded legend overlay, and a side panel with three cards (`#kg-stats-card`, `#kg-detail-card`, `#kg-search-card`). A fixed `#kg-tooltip` div is positioned by JS on hover. Loads `https://cdn.jsdelivr.net/npm/d3@7/dist/d3.min.js` from jsDelivr then `js/kg_explore.js?v=6`.

---

## 3. `i18n.js` — internationalization

**Mechanism:** entirely client-side, no server involvement and no URL-based locale routing. A single global `translations` object holds two flat key→string dictionaries, `en` and `ga` (Irish/Gaelic), covering roughly 130 keys spanning navigation, hero copy, record-card labels, the Research/Map-Explorer/Census/About page bodies, glossary, footer, FAQ, and stats. There is no nesting/namespacing — every page shares the same flat key pool, and only the subset of keys actually referenced by `data-i18n` attributes on the currently-rendered page has any visible effect.

**Runtime API:**
- `window.t(key)` — returns `translations[currentLang][key]`, falling back to `translations.en[key]`, falling back to the raw `key` string itself if not found in either. Available for any inline script to call (used nowhere in the codebase outside `i18n.js` itself, since all templates use the declarative `data-i18n` attribute approach instead).
- `translatePage()` — queries `[data-i18n]` and sets `.textContent`; separately queries `[data-i18n-placeholder]` and sets `.placeholder` (used for the surname `<input>` on the home page). Also sets `document.documentElement.lang = currentLang`.
- `toggleLang()` — flips `currentLang` between `'en'`/`'ga'`, toggles a `.gaelic` class on `.lang-toggle` (which the CSS in §5.9 uses to slide the pill knob and re-colour the EN/GA labels), calls `translatePage()`, then dispatches a `CustomEvent('languageChanged', { detail: { lang: currentLang } })` on `window` — this event has no listener anywhere else in the codebase (no page reacts to a language change beyond the text swap `i18n.js` itself performs), so JS-generated strings written by `main.js`, `census.js`, etc. (e.g. dynamically-built modal HTML, KPI labels) are **not** retranslated when the user switches language; only static `data-i18n`-tagged markup updates.
- On `DOMContentLoaded`: forces `currentLang = 'en'` (language choice does not persist across page loads — no `localStorage`/cookie — so every navigation resets to English), removes any stray `.gaelic` class, runs `translatePage()` once, and wires the `.lang-toggle` click listener.

**Coverage caveat:** the `ga` dictionary is present for every page section but is visibly abbreviated in several places relative to the `en` original — e.g. `courthouseOpened1996` in Irish is a much shorter paraphrase, and several About-page keys (`mapsCashawDesc`, `ejectmentBooksDesc`, `emigrationRecordsFullDesc`, `tenantListsFinalDesc`, `veryOftenEstateRecords`) are truncated to a single sentence versus multi-sentence English originals. This is a genuine (not cosmetic) translation-completeness gap, not a documentation simplification.

---

## 4. `main.js` — the general-purpose page script (~2,097 lines)

Everything in `main.js` runs inside one `document.addEventListener("DOMContentLoaded", async () => { ... })` closure using an `$ = (id) => document.getElementById(id)` shorthand. Nearly all of its logic only actually executes on the home page (`index.html`) — it exits early (`if (!mapEl) return;`) once past the modal-wiring code if `#wicklowMap` isn't present, so on every other page it is a comparatively inert script that just wires up the three modal close buttons and does nothing else.

### 4.1 Data model (`state`)

```js
const state = {
  records: [],              // full unified dataset, fetched from /api/unified/records
  recordById: new Map(),    // record_id -> record (O(1) lookup)
  recordsByName: new Map(), // "forename|surname" -> [records] (O(1) person grouping)
  townlandIndex: {},         // townland name -> { chiefTenants, underTenants, families }
  familyGroups: {},          // "surname|townland" -> [records]
  activeGroups: []           // currently-rendered modal groups, indexed for click handlers
};
```
`buildIndexes(records)` (§4.5) populates all of these in one pass after `loadUnifiedData()` fetches `/api/unified/records` (a single large JSON payload — the file comment elsewhere in the codebase notes this is a ~4.4 MB response).

### 4.2 Modal system

Three modals are wired generically: `openModal(el)` sets `display:flex` + adds `.active`; `closeModal(el)` reverses both. Each modal's own close (`×`) button is bound individually, and a single `window.addEventListener("click", ...)` closes any modal whose overlay (not its content) was the click target — i.e. clicking the dimmed backdrop dismisses the modal, clicking inside the card does not.

The **glossary modal** (`#glossaryModal`) is populated once, on first open, from a hard-coded `labelTips` object (~60 entries) mapping title-cased field names (e.g. `"Chief Tenant"`, `"Household List In Emigration Records"`) to one-sentence explanations — this is the same lookup `tooltipFor(label)` uses to power the `?` tooltip icons on every field in a record card (§4.3).

### 4.3 Record card rendering

`createRecordCard(rec)` is the single function responsible for turning one raw `unified_record` row into an HTML card. It:
- Normalises `chief_tenant`/`under_tenant` display strings by stripping uncertainty markers (`\s*\[\?[^\]]*\]`) and lower-casing bracketed annotations — this is the logic referenced by the recent commit "Strip uncertainty markers from chief tenant name display."
- Orders fields via a hard-coded `preferredOrder` array (record_id, surname, forename, townland, year, … down to comments), appends any remaining unknown keys alphabetically, and completely hides a `hiddenKeys` set (internal linkage fields like `family_key`, `possible_workhouse_matches`, `identity_is_ambiguous`) that exist in the API payload but are never meant to be shown as literal rows — they're rendered by dedicated sections instead (§4.4).
- Special-cases a handful of fields: `year` is passed through `normalizeYear()`; `mountains_in_common` — if truthy — looks up *other* records sharing the same flag to list neighbouring townlands with shared common grazing; the four `has_*_record` booleans render as literal "Yes"/"No"; blank/`"-"` values are skipped entirely (each card footer notes *"Only information known that is shown"*).
- Renders `chief_tenant` as a clickable button (`window.searchSurname(...)`) when the underlying surname is alphabetic, letting a user jump from a tenancy record straight into a cross-townland surname search.
- Produces a 2-column CSS grid of label/value pairs, each label carrying a `.tooltip-icon` (`?`) whose `data-tooltip` attribute is the `labelTips` description, shown via a pure-CSS `::after` hover popover (see §5.10 in the CSS section).

### 4.4 Linked-record sections (household, family, workhouse)

Several helper functions append supplementary blocks after the base card:
- `renderHouseholdLinks(rec)` — combines two link sources: (a) other unified records sharing the same `surname|townland` key (`familySection`, capped at 12, each a clickable `👤 Name [Source]` pill calling `window.openRecordById`), and (b) names parsed out of the free-text `household_list` field on emigration records (`parseHouseholdGivenNames` — splits on `;`, `,`, or the literal word "and") and resolved back to matching emigration records (`buildHouseholdListLinks`).
- `inlineWorkhouseHTML(rec)` / `workhouseSectionHTML(bundle)` / `_whMatchCard(m, isConfirmed)` — render the entity-resolution output already embedded on each unified record (`linked_workhouse_records`, `please_check_records`/`possible_workhouse_matches`) as colour-coded match cards: green border/background for "Confirmed" links, purple for "Please check" (possible) links, each showing a percentage match score, an evidence list (`why_it_matched`), and a missing-evidence list (`what_evidence_is_missing`) sourced directly from the `entity_resolution_candidates.evidence_json`/`conflicts_json` columns described in `CLAUDE.md`. A legacy fallback path (`workhouse_matches`, tiered by `confidence: High/Medium/Low`) is retained for records that predate the newer linked/possible-links schema.
- `window.openSourceDetails(idx, sourceType)` — opens `#recordDetailModal` scoped to just the Emigration or Workhouse records within one previously-rendered group (referenced by index into `state.activeGroups`).
- `window.openRecordById(recordId)` — the "show me everything about this specific person" entry point: looks up all records sharing the same forename/surname via `state.recordsByName` (O(1)), sorts by year, and renders every matching record's card + links + workhouse section inside `#recordDetailModal`.

### 4.5 Townland/tenant/surname browsing (`buildIndexes`, `renderTownlandPanel`, `openTownlandView`, `renderSurnameResults`)

- `buildIndexes(records)` builds, per townland, a `chiefTenants` map (each with its own nested `underTenants` map) and a `families` map (grouped by effective surname — falling back to the chief tenant's surname for 1842-survey rows that have no personal `surname` field, guarded by a `skipCTNames` set that excludes non-name chief-tenant values like `"common grazing"`/`"house lot"`).
- `renderTownlandPanel(name)` — the content injected into `#detailsContent` on the home page when a townland is picked from the dropdown or clicked on the map: two big stat buttons (Chief Tenants count / Surnames count, each opening `openTownlandView`), an under-tenant-data-availability note, an async-loaded 1841 census mini-strip (`fetchCensusStripForPanel`, hitting `/api/census/?year=1841&townland=...&limit=1`), a "View Census Data" link to `main.census`, and two "Explore Historic Place" affordances — one that switches the *same* map into heritage mode in place (`window.openHistoricLandscapeInMap`, §4.6) and one that navigates to the standalone `main.heritage` page.
- `openTownlandView(townland, mode)` — populates `#oldRecordModal` in either `"tenants"` mode (chief tenants, each expandable to show nested under-tenants — lazy-rendered via `data-lazy-group` placeholders filled only on first expand, `toggleExpanded`) or `"families"` mode (surnames, flat list). Every group pushed here is recorded into `state.activeGroups` so the "Full Details" button on each row can later reopen `#recordDetailModal` scoped to just that group (`openGroupDetails`).
- `renderSurnameResults(surname, selectedTownland)` — groups all matching records by townland into `#modalBody`, respecting an optional townland filter.
- `hasSurnameInContext()` / `setSurnameError()` — client-side validation shown inline (`#surnameError`) before allowing a surname search or "Apply Filters" click to proceed, rather than showing an empty result set.

### 4.6 The home-page Leaflet map and its "Historic Landscape" toggle mode

This is the single largest block of `main.js` (roughly the back third of the file) and is the one piece of genuinely map-related logic that lives outside `map.js`/`heritage.js`. It is documented here (rather than deferred entirely to the sibling doc) because it is physically part of `main.js`, but at a lighter level of detail than the sibling doc's treatment of the dedicated map pages, since its job is narrower: let the *same* home-page map switch between two mutually exclusive rendering modes without navigating away.

- **Map init**: `L.map("wicklowMap", { minZoom: 8, maxZoom: 15 })`, a single OpenStreetMap tile layer at `opacity: 0.7` (no `map.js` layer switcher is wired in on this page — see the §2.1 note), plus a decorative semi-transparent parchment-coloured `L.rectangle` behind the whole of County Wicklow. `map.invalidateSize()` is deferred 200 ms to fix sizing when the map's container is painted after `DOMContentLoaded`.
- **Townland polygons**: `loadTownlandsGeo()` fetches `/static/data/townlands.json` (a ~6.2 MB GeoJSON — reused, not re-fetched, if `loadOptions()` already pulled it), draws every feature with hover tooltips keyed on `TL_ENGLISH`, and on click either re-renders the family-records panel or the heritage panel depending on the current mode.
- **`highlightTownland(feature, layer)`** — a three-layer glow effect (outer `weight:16` translucent glow, mid `weight:8`, inner bright `weight:3` outline), animated via a `requestAnimationFrame` loop that sinusoidally pulses the outer two layers' opacity/weight indefinitely (`animatePulse` never stops itself once started — it is not cancelled when a *different* townland is subsequently selected, though the old `selectedTownlandLayer` group is removed from the map first, silently orphaning the previous animation loop, which then runs against a detached layer group).
- **Heritage/"Historic Landscape" overlay mode** (`homeHeritageState`) — a self-contained parallel feature set that reuses the *same* map instance and the *same* right-hand details panel:
  - Three GeoJSON point layers fetched lazily and cached (`asi_wicklow.geojson`, `holywells_wicklow.geojson`, `monuments_wicklow.geojson` from `/static/data/`), each rendered as `L.circleMarker`s in a dataset-specific colour.
  - Point-in-polygon (`pointInGeoJSONPolygon`/`pointInRing`, a standard ray-casting algorithm) plus a haversine great-circle distance helper (`haversineM`) jointly filter each dataset to features inside the selected townland's polygon **or** within a user-selected radius (0 / 2 km / 5 km, `window.setHistoricLandscapeRadius`) of its bounding-box centroid.
  - Two dashed radius rings are drawn (`drawHomeHeritageRadiusRings`) with the active radius emphasised.
  - Clicking a heritage marker calls `renderHomeHeritageFeatureDetail`, which replaces the details panel with a single feature's metadata (class, townland, SMR reference, distance, notes, Google Maps view/directions links, and a link to the source record if `source_link`/`external_link` is present).
  - `window.openHistoricLandscapeInMap(townlandName)` / `window.returnToFamilyRecords(townlandName)` / `window.returnToHistoricLandscapeSummary(townlandName)` are the mode-transition entry points invoked from inline `onclick` handlers embedded in the HTML strings this file generates.
- **URL param handling**: on load, `?townland=<name>` (case-insensitively matched against the loaded dropdown options) auto-selects that townland and smooth-scrolls to `#explore`; an additional `&view=heritage` param immediately switches into heritage mode for that townland. This is how `census.html`'s and `heritage.html`'s own "back to home"/"family records" links round-trip a townland selection back onto the home page map.
- **Load sequencing**: `loadTownlandsGeo()` and `loadUnifiedData()` are kicked off together (`Promise.all`-style, via a bare async IIFE) rather than sequentially, specifically to halve total wait time versus the previous sequential fetch of two large payloads (6.2 MB + 4.4 MB) — this optimisation is called out in an inline code comment.

### 4.7 Utility functions

`escapeHtml`, `normalizeYear`, `canonicalTownland` (case-insensitive lookup against `state.townlandIndex` keys), `canonicalName` (builds a display name from forename/surname, falling back to `"Unknown"` for non-alphabetic surnames), `titleCaseKey`, `displayTownlandLabel` (title-cases a townland name while special-casing the word "or"), `formatCoord` (5-decimal-place lat/lng string), `googleMapsLink(type, lat, lng)` (builds `google.com/maps/search` or `/dir` deep links), `sourceTagsFromRecords`/`sourceTagHTML` (renders the coloured Tenancy/Emigration/Eviction/Workhouse pills seen throughout the modals) — none of these are exported on `window`; they're only reachable from within the same closure, so genuinely page-global helpers are limited to the small `window.*` set enumerated above (`searchSurname`, `openRecordById`, `openSourceDetails`, `openGroupDetails`, `toggleExpanded`, `openTownlandView`, `setHistoricLandscapeRadius`, `returnToFamilyRecords`, `returnToHistoricLandscapeSummary`, `openHistoricLandscapeInMap`).

---

## 5. `main.css` — design system (2,798 lines)

The stylesheet is one file, organized into clearly commented sections (each preceded by a `/* ---- SECTION NAME ---- */` banner comment). It is a hand-written, non-preprocessed CSS file (no Sass/Less, no PostCSS build step) using native CSS custom properties for theming.

### 5.1 Design tokens (`:root`, lines 9–56)

| Group | Variables | Values |
|---|---|---|
| Core palette | `--bg`, `--surface`, `--ink`, `--muted`, `--subtle` | Warm parchment `#F7F4EE`, ivory card `#FCFBF8`, deep charcoal `#1F2933`, `#6B7A8A`, `#9AAAB8` |
| Accent palette | `--forest`, `--moss`, `--mist`, `--brass` | `#234B3A` (primary accent — "Wicklow forest green"), `#5F7C62`, `#7FA7B8`, `#B08D57` ("soft brass/gold — highlight") |
| Legacy/semantic aliases | `--blue-900`…`--blue-500`, `--accent` | Explicitly kept, per an inline comment, "for JS compatibility" — mapped onto the same forest/moss/mist/brass values rather than actual blues, i.e. these names are now misleading holdovers from an earlier blue-toned palette. |
| Surfaces/borders | `--card`, `--line`, `--line-soft` | `--card` aliases `--surface`; `--line` is `#D8D2C8` ("warm stone border"). |
| Shadows | `--shadow`, `--shadow-soft`, `--shadow-hover`, `--shadow-deep` | Layered `rgba(31,41,51,…)` soft shadows, deepest (`--shadow-deep`) used only on modals. |
| Radii | `--radius` (14px), `--radius-lg` (20px), `--radius-xl` (28px) | |
| Typography | `--font-display` (`'Cormorant Garamond', 'Playfair Display', Georgia, serif`), `--font-ui` (`'Inter', system-ui, -apple-system, sans-serif`) | Display font for headings/numerals, UI font for body/controls throughout every template. |
| Motion | `--ease` (`cubic-bezier(0.22,1,0.36,1)`), `--t-fast` (150ms), `--t-base` (220ms), `--t-slow` (350ms) | Used consistently across hover/focus transitions site-wide. |

**No dark-mode support exists.** A repository-wide search for `prefers-color-scheme` or `data-theme` in `main.css` returns nothing — the site is light-themed only, with no CSS hook for a future theme toggle.

### 5.2 Reset & base (lines 59–96)

Universal `box-sizing: border-box` + font-smoothing reset. `body` gets a subtle inline SVG `feTurbulence` noise texture as a `background-image` data-URI (opacity `0.018`) layered under `var(--bg)`, giving the "warm parchment" texture visible site-wide. `scroll-behavior: smooth` on `html, body` powers every in-page `#anchor` link (nav links, "Explore on the Map" hero CTA, footer links) without any JS.

### 5.3 Hero (lines 325–498) — including the recent "hero button redesign"

`.hero` is `height:100vh`, `background:#1a4a2e` (a solid flat forest-green fallback — the commit history shows this replaced an earlier gradient/photo-background approach: "Fix hero: solid green background, remove all overlays"). `.hero::before`/`::after` are explicitly `display:none` (dead pseudo-element rules kept from a prior design, not removed). `.hero-centre` is a glassmorphic card (`backdrop-filter: blur(4px)`, a diagonal green→dark→amber gradient background) holding the eyebrow, `<h1>`, subtitle, and `.hero-actions`.

The two CTA buttons — the subject of the "Enlarge and animate hero CTA buttons," "hero button redesign," and "Bust CSS cache to force solid orange Ask button to load" commits — are:

```css
.hero-btn-primary {              /* "Explore on the Map" */
  background: #ffffff; color: #1a4a2e;
  font-size: 1.15rem; font-weight: 800; padding: 20px 46px; border-radius: 60px;
  box-shadow: 0 0 0 5px rgba(255,255,255,0.20), 0 10px 40px rgba(0,0,0,0.40);
  transition: transform 200ms ease, box-shadow 200ms ease, background 150ms ease;
}
.hero-btn-primary:hover { transform: translateY(-4px) scale(1.04); }

.hero-btn-ghost {                /* "Ask the Archive" */
  background: #E07820; color: #fff;          /* solid Irish amber/orange */
  font-size: 1.15rem; font-weight: 800; padding: 20px 46px; border-radius: 60px;
  box-shadow: 0 0 0 5px rgba(224,120,32,0.30), 0 10px 40px rgba(224,120,32,0.50);
}
.hero-btn-ghost:hover { background: #c56918; transform: translateY(-4px) scale(1.04); }
```

Both buttons are large pill shapes (`border-radius:60px`), share the same enlarge-on-hover treatment (`translateY(-4px) scale(1.04)`), and use a glow-style `box-shadow` (a soft outer ring plus a diffuse drop shadow) rather than a hard border to read as "unmissable," per the code comments directly above each rule (`/* "Explore on the Map" — solid white, maximum contrast */`, `/* "Ask the Archive" — Irish amber/orange, unmissable */`). `.hero-actions` itself fades/slides in on load via a `heroActionsIn` keyframe with a `0.35s` delay, so the buttons animate into place after the rest of the hero has rendered.

### 5.4 Buttons (lines 222–323)

Base `.btn` is a flex pill with `padding:11px 22px`, `border-radius:var(--radius)` (14px, i.e. much less rounded than the hero buttons' 60px pill shape — the hero buttons are a deliberate one-off variant, not reusing the base radius token). Variants: `.btn-primary` (forest green fill), `.btn-ghost` (transparent, line border), `.btn-soft` (translucent forest tint, used heavily for secondary actions like "Explore on Map" record-card buttons and the Ask page's example-question chips), `.btn-light`/`.btn-outline-light` (for use on dark bands), `.btn-block` (100% width). All buttons have a uniform `:active { transform: scale(0.975); }` press-down micro-interaction.

### 5.5 Sections, panels, forms, tags (lines 736–1108)

`.panel` is the workhorse card component (ivory surface, `--radius-lg`, `--shadow-soft`) used for nearly every content box across every page (filters, KPI containers, glossary rows, chart cards). `.panel.soft` is a lower-contrast translucent variant. `.panel-title` renders as small-caps-style uppercase text with a `::before` 16px brass tick-mark rule (a recurring "cartographic" motif — the stylesheet's opening comment self-describes the whole system as *"A modern digital atlas of Wicklow's history"*). Form controls (`.select`, `.input`) share one rule block, with `.select` getting a hand-drawn SVG chevron background-image instead of relying on native OS styling. `.chip`/`.chip-row` provide pill-shaped filter tags.

### 5.6 Modals (lines 2097–2236)

`.modal-overlay` is `display:none` until JS adds `.active` (`display:flex`), a fixed full-viewport `rgba(31,41,51,0.45)` scrim with `backdrop-filter: blur(8px)`. `.old-record-modal` (the shared card class for all three modal types) animates in with a `modalUp` keyframe (`translateY(18px) scale(0.98)` → identity, 0.36s). `.tooltip-icon:hover::after` implements the field-label tooltip popover entirely in CSS via `content: attr(data-tooltip)` — no JS positioning logic is needed for the ~60 `labelTips` tooltips `main.js` generates.

### 5.7 Navigation & responsive (lines 108–221, 2646–2721)

Nav is `position: sticky; top: 0` with a `backdrop-filter: blur(16px) saturate(160%)` frosted-glass background. Two breakpoints:
- **`max-width: 980px`** — collapses most multi-column grids (`.hero-inner`, `.split`, `.cards-3`, `.footer-grid`, `.stats-4`, `.analytics-top`, `.kpi-grid`, `.charts-grid`, `.explore-layout`) to one or two columns, and shrinks the home-page map to `height:420px`.
- **`max-width: 720px`** — this is where `.nav-burger { display:flex; }` and `.nav-links` switches from an inline flex row to an absolutely-positioned dropdown panel (`.nav-links.open { display:flex; }`). As noted in §1.2, **no JS ever toggles `.open`**, so below 720px the nav becomes effectively non-functional for reaching any link beyond the brand mark, unless the user's browser/OS provides its own zoom-out.

### 5.8 Map-adjacent, language-switcher, and misc component CSS (lines 1173–1578, 2350–2495, 2723–2798)

- **Map Explorer band** (`.explore-layout`, `.filters-panel`, `.map-panel`, `.map-container`) — the dark band styling wrapping the home-page map.
- **Townland highlight animation** (lines 2350–2370) — supporting styles for the `highlightTownland()` glow effect described in §4.6 (mostly just ensures the injected SVG paths don't pick up unwanted default Leaflet styling).
- **Language switcher** (lines 2371–2438) — the pill-and-knob toggle switch: `.lang-toggle` is a 52×26px rounded track, `.lang-toggle::after` a 20px circular knob that translates `26px` right and the track's background switches from `--moss` to `--forest` when `.gaelic` is applied by `i18n.js`.
- **Map layer switcher** (lines 2440–2479) — `.layer-switcher`/`.layer-btn` styling for the button-group UI `map.js`'s `_renderSwitcherUI()` builds (used on the census/heritage pages, not the home page — see §2.1/§4.6).
- **Townland hover tooltip** (lines 2723–2745) — overrides Leaflet's default tooltip chrome (removes the arrow, restyles as a small serif-font pill) for every map's townland-name hover label.
- **Utility overrides** (lines 2747–2798) — Leaflet popup readability fixes (`!important`-qualified, since Leaflet injects its own inline-ish styles), the shared `@keyframes spin` used by both the Ask page's loading spinners and (separately) the heritage page's `.hp-spinner`, and the `.wh-confidence`/`.wh-High`/`.wh-Medium` workhouse-match confidence badge colours consumed by `main.js`'s `_whMatchCard()`/legacy `workhouseSectionHTML()` output.

### 5.9 Per-page scoped `<style>` blocks

Several templates (`info.html`, `ask.html`, `heritage.html`, `kg_explore.html`) additionally define their own large `<style>` blocks directly in the template rather than adding to `main.css`, each using a distinct class prefix to avoid collisions (`.ip-*` for Info, `.ask-*` for Ask, `.hp-*` for Heritage, `#kg-*`/`.kg-*` for the Knowledge Graph explorer). `main.css` itself is therefore not a complete description of every page's visual design — it covers the shared shell (nav/footer/buttons/panels/hero) plus the home/census/analytics pages, while the four pages above layer substantial page-local CSS on top.

---

## 6. `map.js` — shared Leaflet layer-switcher utility

Loaded globally by `base.html` on every page (see §1.6), but it is a small (172-line), page-agnostic **utility module** — not a page controller — so it is only briefly noted here rather than covered by the sibling map/visualization doc as a "page." Its own header comment states its scope precisely: *"Main map initialisation — shared across all map pages… Tile URLs are NEVER hardcoded here — always from the backend config."*

It exposes three plain (non-namespaced, `window`-implicit) functions:
- `async initLayerSwitcher(mapInstance, containerId)` — fetches `/api/map/layers`, falls back to a hard-coded `_fallbackLayerConfig()` (OpenStreetMap standard / Esri satellite / OpenTopoMap terrain, plus an Esri labels overlay) if the fetch fails or 404s, builds `L.tileLayer` instances for each, restores the user's last-picked layer from `localStorage["coolattin_map_layer"]`, auto-adds the labels overlay when satellite is active, and renders the `.layer-switcher`/`.layer-btn` button group into the given container.
- `buildLayerMap(layers)` — array of layer configs → `{ id: L.TileLayer }`.
- `switchLayer(mapInstance, layerMap, layerId)` — removes whichever layer is currently active and adds the requested one, persisting the choice to `localStorage`.

Per its own trailing comment, the **home page map is explicitly excluded** from this module ("managed exclusively by `main.js`" — see §4.6, which is also why `index.html` has an `#wicklow-map-layer-switcher` placeholder `<div>` that is never actually filled in); `initLayerSwitcher()` is called only from `census.js` and `heritage.js`.

---

## 7. `marked.min.js`

A third-party, minified Markdown-to-HTML rendering library (~36 KB), loaded only by `ask.html` immediately before `ask.js`. It is used by `ask.js` to render the LLM's Markdown-formatted answer text into the `.ask-md`-styled `#askLlmAnswer` container. Its internals are not analysed here — see `15_frontend_map_and_visualizations.md` for how `ask.js` invokes it.
