# 06 — Ask Pipeline: The Legacy Path and Intent Routing

**Scope.** The pipeline that runs when `ASK_USE_NEW_PIPELINE=false` — the inline body of
`answer_question_stream()` (`backend/services/ask_service.py:3669–4392`) — plus the complete contents
of `backend/services/intent_router.py` (141 lines).

**Companion docs.** `05_ask_pipeline_default.md` (default path), `07_ask_pipeline_safety_execution_streaming.md`
(safety/execution/streaming/PDF/feedback), `08_*` (semantic layer), `09_*` (embedding index),
`10_*` (subgraph engine / GraphRAG), `11_*` (VRTI + GraphDB SPARQL clients).

**Status.** This path is off by default. It is retained because it contains the four fast lanes, the
intent router, the semantic-layer slot-fill compiler, and the subgraph engine — none of which run in
the default pipeline. It is the pipeline the dissertation's routing/fast-lane evaluation was built
against.

---

## 0. Verification note

`CLAUDE.md` §"Legacy pipeline" reproduces the fast-lane thresholds and the four keyword lists. Read
against the source:

- **Semantic-layer 0.80** — correct.
- **Phase 4 template 0.68 cosine** — correct.
- **Verified analysis** — correct in spirit; the actual gate is more specific (§3.3).
- **Direct memory reuse "token-sort-ratio + cosine ≥ 0.55"** — **wrong**. See §3.4.
- **Keyword lists** — every one of the five lists in `CLAUDE.md` is missing entries. Full corrected
  lists in §4.2, with an explicit diff in §4.7.
- **`output_mode` set for ANALYTICAL** — `CLAUDE.md` says `{count, aggregate, trend}`; the code uses
  `{count, aggregate, trend, list, grouped}`.
- **Per-route dispatch table** — `CLAUDE.md` claims ANALYTICAL "never" uses free-form LLM SQL. It
  does, on three separate fall-through conditions. See §5.

Corrections are marked **[DRIFT]** inline.

---

## 1. Entry and setup

```python
if ASK_USE_NEW_PIPELINE:
    yield from _orchestrated_pipeline_stream(clean_q, townland_hint, include_sql, force_llm)
    return
# ── everything below is the legacy pipeline ──
```

Setup is identical to the default pipeline (`_ensure_unified_table_seeded`,
`_ensure_heritage_feature_seeded`, `_ensure_query_memory_schema`, then
`_resolve_townland_context` → `_analyse_question`), with one difference: the legacy path collects
only `townland_resolution["warning"]` and `_question_data_coverage_warnings()`. It does **not** add
`analysis["name_correction_warnings"]`, does not run the `_needs_townland` nudge, and does not run
person identity resolution.

---

## 2. Pre-computation — all four candidate sources are evaluated up front

Before any branching, the legacy pipeline computes every fast-lane candidate:

```python
# Phase 2 — rule-based semantic slot fill (no LLM)
from backend.services.semantic_layer import (
    try_rule_based_fill, compile_sql, compile_sparql,
    slot_fill_meta, build_slot_fill_prompt, parse_slot_fill)
_semantic_slot_fill = try_rule_based_fill(clean_q, analysis, townland_resolution)

verified_analysis   = _try_verified_analysis(clean_q, canonical_townland, analysis)
approved_matches    = _find_similar_approved_queries(clean_q, analysis, canonical_townland)
direct_memory_match = approved_matches[0] if _can_reuse_memory_directly(
                          clean_q, analysis, canonical_townland,
                          approved_matches[0] if approved_matches else None) else None

# Phase 4 — hybrid retrieval over templates + metrics + memory
_raw_memory = _load_approved_query_memory()
_p4_template, _p4_memory = _phase4_retrieve(clean_q, canonical_townland, _raw_memory)
```

Note the cost implication: even when lane 1 wins, `_find_similar_approved_queries`,
`_try_verified_analysis` and `_phase4_retrieve` have already run. All are local/CPU-only (no network),
so the waste is milliseconds.

The whole semantic-layer import is inside a try/except; if `semantic_layer` fails to import,
`_semantic_slot_fill` stays `None` and the ANALYTICAL slot-fill branch later degrades to free-form SQL
(§5.1).

---

## 3. The four fast lanes

Evaluated as one `if/elif/elif/elif/else` chain at lines 3778–3845. **Every lane is additionally gated
on `not force_llm`**, so `{"force_llm": true}` in the request body forces the router path.

| # | Lane | Guard | `strategy` |
|---|---|---|---|
| 1 | Semantic layer (rule-based) | `_semantic_routed` sentinel, set when confidence ≥ 0.80 **and** `compile_sql` returned non-None | `semantic_layer` |
| 2 | Phase 4 template fast lane | `_p4_template` is not None | `phase4_template_fast_lane` |
| 3 | Verified analysis | `verified_analysis` is not None | `verified_analysis` |
| 4 | Direct memory reuse | `direct_memory_match` is not None | `approved_query_memory` |
| — | else | — | intent router (§4) |

### 3.1 Lane 1 — semantic layer, threshold **0.80**

```python
_sl_confidence_threshold = 0.80
if _semantic_slot_fill is not None and _semantic_slot_fill.confidence >= _sl_confidence_threshold and not force_llm:
    _compiled = _compile_semantic_sql(_semantic_slot_fill, _clearances_count_column())
    if _compiled:
        sql = _compiled
        llm_meta = _slot_fill_meta(_semantic_slot_fill, sql)
        _semantic_routed = True
    else:
        _semantic_routed = False
```

Confirmed: `0.80` is a local literal in `ask_service.py`, not imported from `semantic_layer`.

**Call boundary** (deep-dive in doc 08). `try_rule_based_fill(question, analysis, townland_resolution)`
returns a `SlotFill` dataclass or `None`. `ask_service` reads `.confidence`, `.metric`, `.dimensions`,
`.filters`. `compile_sql(slot_fill, clearances_col)` returns a deterministic SQL string or `None`.
The `clearances_col` argument is the runtime-detected metric column name — `semantic_layer.compile_sql`
defaults it to `"count"`, but `ask_service` always passes `_clearances_count_column()`.
`slot_fill_meta(sf, sql)` builds the `llm_meta` dict. **Zero LLM calls on this lane.**

SSE detail is unusually informative:

```
Semantic layer: emigration_count dims=['year'] filters=['townland_norm'] confidence=0.92
```

Note the sentinel dance: if `compile_sql` returns `None` (a fill above threshold that the compiler
cannot render), `_semantic_routed` is set `False` and the chain falls through to lane 2 — the slot fill
is not discarded, and remains available to the ANALYTICAL route later.

### 3.2 Lane 2 — Phase 4 template fast lane, cosine **≥ 0.68**

`_phase4_retrieve()` (2077):

```python
from backend.services.embedding_index import get_index, TEMPLATE_FAST_LANE_THRESHOLD
from backend.services.semantic_layer import METRIC_REGISTRY as _metrics

hits = get_index().retrieve(question, top_k=12,
                            templates=QUESTION_TEMPLATES, metrics=_metrics, memory_rows=approved_memory)
```

Verified in `backend/services/embedding_index.py:34`:

```python
TEMPLATE_FAST_LANE_THRESHOLD: float = 0.68
```

The lane fires on the first hit satisfying **all** of:

1. `hit.source in ("template", "metric")`
2. `hit.cosine_score >= 0.68`
3. **hard keyword gate** — `all(kw in question.lower() for kw in hit.required_keywords)`
4. `hit.source == "template"` (a `metric`-sourced hit passes checks 1–3 but has no branch, so it is
   silently skipped — see §3.2.1)
5. `_match_and_build_template_by_id(hit.key, question, canonical_townland)` returns a built SQL —
   i.e. the template's `requires_townland` / `requires_year` / `requires_surname` preconditions are met

Step 3 is important: a purely lexical hard filter sits on top of the vector score, so a high cosine on
a template whose required keyword is absent cannot fire.

`_phase4_retrieve` also returns re-ranked memory:

```python
memory_scores = {hit.key: (hit.cosine_score, hit.rrf_score) for hit in hits if hit.source == "memory"}
# rows present in memory_scores get match_score = round(cosine * 100, 2) and _p4_rrf,
# are sorted by _p4_rrf desc, and are placed ahead of unranked rows
return template_fast_lane, ranked + unranked
```

`_p4_memory` is used later as the few-shot block for free-form SQL generation (§5).
On any exception the whole function returns `(None, approved_memory)` — degradation is silent
(`log.debug`).

**Call boundary** (doc 09): `IndexHit` exposes `.source`, `.key`, `.cosine_score`, `.rrf_score`,
`.required_keywords`. RRF (`_RRF_K = 60`) fuses a dense TF-IDF cosine ranking with a sparse
keyword-overlap ranking.

#### 3.2.1 `metric`-sourced hits are unreachable

```python
for hit in hits:
    if hit.source not in ("template", "metric"): continue
    ...
    if hit.source == "template":
        tmpl, tmpl_sql = _match_and_build_template_by_id(...)
        if tmpl and tmpl_sql:
            template_fast_lane = {...}
            break
```

There is no `elif hit.source == "metric"` branch, and no `break` outside it. A metric hit passes the
filters, does nothing, and the loop continues. `METRIC_REGISTRY` is therefore only useful for how it
perturbs the RRF ranking, never as a direct fast-lane source.

#### 3.2.2 The template library

`QUESTION_TEMPLATES` (line 511) is a list of ~70 dicts spanning eight categories: emigration,
eviction/clearances, census/population, geography, people/names, tenancy, records overview, and
combined/analysis. Each has:

```python
{"id": ..., "category": ..., "description": ...,
 "required_keywords": [...],   # ALL must be substrings of the lowercased question
 "optional_keywords": [...],   # each present one adds 1 to the score
 "sql_template": "...",        # may contain {townland_norm}, {year}, {surname}
 "requires_townland": True,    # optional preconditions
 "requires_year": True,
 "requires_surname": True,
 "warning": "..."}             # optional methodological caveat
```

`_match_and_build_template()` (1962) — used by lane 3, not lane 2 — scores every template as
`len(required)*2 + count(optional present)` and requires `best_score >= 2`. Before scoring it applies
an **out-of-scope exclusion list** that returns `(None, None)` immediately:

```
workhouse · died of · religion/religious · political · approach ·
average rent / rent owed / rent paid · under the age · above age / below age ·
older than / younger than · age of / age > / age< · children under ·
other irish / other estate · weather / climate · crop / farming ·
entity resolution candidate
```

plus two regex/logic guards:

```python
if re.search(r'\bage\s*\d|\d+\s*(?:years?|yrs?)?\s*(?:old|of age)', q): return None, None
if "widow" in q and "emigra" in q:                                      return None, None
```

Placeholders are substituted with `_sql_escape()` (single-quote doubling), `str(year)`, and the
extracted surname.

`_match_and_build_template_by_id()` (2049) — used by lane 2 — skips the scoring and exclusions
entirely: it looks up the id, checks only the three `requires_*` preconditions, and substitutes.

SSE detail: `Phase 4 fast lane: template=emigration_per_year cosine=0.731`.
`llm_meta` records `{"provider": "phase4_embedding", "model": "tfidf_rrf", "mode": "template_fast_lane",
"analysis_id", "description", "cosine_score", "rrf_score"}`, and `chart_hint` is looked up from
`VERIFIED_ANALYSIS_CHART_HINTS`.

### 3.3 Lane 3 — verified analysis

`_try_verified_analysis()` (2168) has three sub-paths, checked in order.

**Own exclusion list** first (a subset of the template list — note it lacks `age of`, `older than`,
`younger than`, `above age`, `below age`, and the `\bage\s*\d` regex):

```
workhouse · died of · religion · political · approach ·
average rent / rent owed / rent paid · other irish / other estate ·
weather / climate · crop / farming · under the age / children under
```
plus the same `"widow" and "emigra"` guard.

**Sub-path A — surname count.** `analysis["surname"]` set, `primary_intent == "people"`,
`output_mode == "count"`:

```sql
SELECT COUNT(DISTINCT record_id) AS matching_people
FROM unified_record WHERE UPPER(surname)='{SURNAME}'
```
`analysis_id: "people_named_surname_count"`.

**Sub-path B — surname list.** Same, `output_mode == "list"` → a DISTINCT person projection with
`ORDER BY year, person_name LIMIT 200`; `analysis_id: "people_named_surname_list"`.

**Sub-path C — curated template.** `_match_and_build_template()` produces a template whose `id` is in
the **`VERIFIED_ANALYSIS_TEMPLATE_IDS`** frozen set (line 217):

```
tenant_land_gender_average · widows_with_children_proportion · widows_eviction_proportion ·
widows_count · children_emigrated · eviction_family_size_range · most_populous_1841_vs_1861 ·
population_trend_1841_1861 · emigration_population_townland_trend · largest_latest_tenant_holdings ·
smallest_townland_plots · holy_well_population_relationship · ring_fort_population_relationship ·
canada_emigration_peak_period · ship_most_families_canada
```

These 15 ids are the "pre-validated hard-coded SQL templates" from `CLAUDE.md`. They are the
statistically sensitive research questions where the module docstring says accuracy matters most; most
carry a `warning` string documenting the methodological caveat (e.g. *"Widows are identified from
widow-labelled names or notes in the source rows"*).

`VERIFIED_ANALYSIS_CHART_HINTS` (line 235) maps 7 of them to a chart type (`bar` or `line`).

`llm_meta` for all three sub-paths: `{"provider": "verified_analysis", "model": "curated_sql",
"mode": "verified_analysis", "analysis_id", "description"}`.

### 3.4 Lane 4 — direct memory reuse — **[DRIFT]: threshold is 92, not 0.55**

`CLAUDE.md`: *"Direct memory reuse — approved thumbs-up query (token-sort-ratio + cosine ≥ 0.55) →
reuse cached SQL."* Neither number is the reuse gate.

**What 55 actually is.** `_find_similar_approved_queries()` (2414) is a *candidate list* builder:

```python
for row in _load_approved_query_memory():
    score = _memory_similarity_score(question, analysis, townland_norm, row)
    if score < 55:            # ← floor for appearing in the few-shot / suggestion list
        continue
```

Rows above 55 are sorted and the top 5 are kept. These become `approved_matches`, used as few-shot
examples and displayed in `query_provenance["approved_query_candidates"]`. Passing 55 does **not**
authorise reuse.

**What 0.55 actually is.** `embedding_index.MEMORY_COSINE_THRESHOLD = 0.55` exists (line 38) with the
comment *"used by the caller alongside the existing token-sort-ratio check"* — but a repo-wide grep
finds **no importer**. It is a dead constant. Nothing in the reuse decision consults a cosine score.

**The real reuse gate** — `_can_reuse_memory_directly()` (2440), all four conditions required:

```python
if not match:                                          return False
if float(match.get("match_score") or 0.0) < 92:        return False      # ← threshold 92 / 100
if townland_norm and cand_tl and townland_norm != cand_tl: return False
return (cand_analysis.get("primary_intent") == analysis.get("primary_intent")
        and cand_analysis.get("output_mode") == analysis.get("output_mode"))
```

**The composite score** — `_memory_similarity_score()` (2369) is 0–100 and is only *partly* a
token-sort ratio:

```python
source = _question_signature(question)
target = candidate["question_signature"] or _question_signature(candidate["question_text"])
base = fuzz.token_sort_ratio(source, target)              # rapidfuzz, 0–100
     # or difflib.SequenceMatcher(...).ratio()*100 when rapidfuzz is unavailable
score = base
score += 8   if candidate primary_intent matches
score += 5   if output_mode matches
score += 3   if group_by matches
score += 4   if both have the same non-null year
score += 10  if both have a townland_norm and they are equal
score -= 16  if both have a townland_norm and they differ
score -= 4   if exactly one side has a townland_norm
score += min(6.0, approved_count * 1.5)
score -= min(8.0, rejected_count * 2.0)
return round(max(0.0, min(score, 100.0)), 2)
```

So the token-sort ratio is computed over **question signatures**, not raw questions (see doc 07 §7.2),
and up to +30 / −28 of metadata adjustment is layered on top before the 92 cut. Because the bonuses
alone can reach +30, a signature similarity around 70 with perfect metadata alignment can clear 92.

**Reuse bookkeeping.** After execution, `_mark_query_memory_used(direct_memory_match["id"])` (2482)
increments `reuse_count`, sets `last_used_at`/`updated_at`, and clears the memory cache.
`llm_meta` records `{"provider": "query_memory", "model": "approved_sql", "mode":
"approved_memory_reuse", "memory_id", "memory_similarity", "description"}`, and the special-case at
Stage 3 preserves this mode even when execution recovery fires.

SSE detail: `Reused approved query memory (similarity 96.5)`.

---

## 4. Phase 5 — `intent_router.classify_intent()`

Reached only when all four lanes miss (or `force_llm` is set).

```python
from backend.services.intent_router import classify_intent as _classify_intent_fn, ANALYTICAL, RELATIONAL, COMPARATIVE
_intent_route = _classify_intent_fn(clean_q, analysis, _semantic_slot_fill)
```

On import/classification error, `_intent_route` degrades to `"fallback"` (logged at DEBUG).

```python
query_provenance["intent_route"] = _intent_route
_force_subgraph = _intent_route in {"relational", "comparative"}
if _intent_route == "comparative":
    query_provenance["phase6_fusion"] = True
```

### 4.1 Signature and route constants

```python
ANALYTICAL  = "analytical"
RELATIONAL  = "relational"
COMPARATIVE = "comparative"
FALLBACK    = "fallback"

def classify_intent(question: str, analysis: dict[str, Any], slot_fill: Any | None) -> str
```

Inputs read: `question` (lowercased once into `q`), `analysis["primary_intent"]`,
`analysis["output_mode"]`, `analysis["surname"] / ["forename"] / ["canonical_name"]`, and whether
`slot_fill is not None` (the object itself is never inspected — only its presence).

All keyword tests are `kw in q` **substring** matches on the lowercased question. There is no
tokenisation, no word-boundary check, and no stemming. This means `"most"` matches inside `"almost"`,
`"describe"` matches inside `"describes"`, and `" vs "` requires literal surrounding spaces.

### 4.2 The complete keyword lists, verbatim from source

**`_COMPARATIVE_KEYWORDS`** (intent_router.py:30) — 15 entries:

```
compare · compared to · compared with · versus · " vs " (with surrounding spaces) · vs. ·
difference between · contrast · relative to · how does · how did ·
better than · worse than · more than · less than · higher than · lower than · against
```

**`_RELATIONAL_KEYWORDS`** (line 37) — 16 entries:

```
related to · connected to · connection between · link between ·
in the same parish · same parish · same barony ·
part of · belong to · belongs to · neighbouring · neighboring ·
adjacent to · bordering · relationship between · linked to
```

**`_HIERARCHY_KEYWORDS`** (line 44) — 15 entries:

```
which parish · what parish · civil parish · in the parish ·
in the barony · which barony · what barony ·
in the county · which county · what county ·
townlands in · where is · where does ·
located in · situated in · falls within
```

**`_HERITAGE_KEYWORDS`** (line 52) — 13 entries:

```
heritage · archaeological · monument · ring fort · holy well ·
history of · tell me about · describe · what is the history ·
historically · historic · fortification · earthwork
```

**`_SENSEMAKING_KEYWORDS`** (line 58) — 10 entries:

```
overview · about the estate · about coolattin · what was ·
describe the estate · coolattin estate · what kind of ·
background · summary of · general context
```

**`_ANALYTICAL_KEYWORDS`** (line 64) — 25 entries:

```
how many · how much · total · count of · number of · average · mean ·
proportion · percent · percentage · per year · by year · trend ·
over time · distribution · breakdown · most · least · highest ·
lowest · maximum · minimum · sum of · rate · ratio
```

**`_ANALYTICAL_INTENTS`** (line 71) — 5 entries:

```
population · eviction · emigration · tenancy · people
```

**`_STRONG_RELATIONAL`** (line 74) — `_RELATIONAL_KEYWORDS | _HIERARCHY_KEYWORDS`. Computed but
**never referenced** anywhere in the module or repo. Dead.

### 4.3 Priority 1 — COMPARATIVE

```python
if any(kw in q for kw in _COMPARATIVE_KEYWORDS):
    return COMPARATIVE
```

Unconditional, first, no exceptions. Because `"how does"` and `"how did"` are in the set, questions
like *"How did the population change between 1841 and 1851?"* route COMPARATIVE, not ANALYTICAL —
even though they are pure aggregate questions. This is the router's biggest practical mis-classifier.

### 4.4 Priority 2 — RELATIONAL, with two overrides

```python
has_relational   = any(kw in q for kw in _RELATIONAL_KEYWORDS)
has_hierarchy    = any(kw in q for kw in _HIERARCHY_KEYWORDS)
has_heritage     = any(kw in q for kw in _HERITAGE_KEYWORDS)
has_sensemaking  = any(kw in q for kw in _SENSEMAKING_KEYWORDS)
geography_intent = (analysis.get("primary_intent") == "geography")

if geography_intent or has_relational or has_hierarchy or has_heritage or has_sensemaking:
    if (has_heritage or has_sensemaking) and not (has_relational or has_hierarchy or geography_intent):
        # ── override window: ONLY soft signals fired ──
        pure_count = (output_mode in {"count", "aggregate"}
                      and any(kw in q for kw in _ANALYTICAL_KEYWORDS))
        if pure_count:
            return ANALYTICAL                                   # Core Rule 1
        if analysis.get("surname") or analysis.get("forename") or analysis.get("canonical_name"):
            return FALLBACK                                     # Person-detail guard
    return RELATIONAL
```

**Core Rule 1 override** — confirmed present, matching the memory note. Rationale in the source
comment: *"a pure count question with no genuine relational depth (e.g. 'how many ring forts are in
the parish?') stays on the SQL path. Only heritage/sensemaking keywords trigger this check; explicit
relational or hierarchy keywords always win."*

Note the asymmetry: `pure_count` tests `output_mode in {"count","aggregate"}`, but `_analyse_question`
never produces the literal `"aggregate"` — its only outputs are `grouped`, `list`, `count`, `detail`.
So in practice the override fires only on `output_mode == "count"`.

**Person-detail guard** — **[DRIFT]: entirely absent from `CLAUDE.md`.** Source comment: *"estate
tenants (people) live in SQLite, not the KG. If only heritage/sensemaking keywords fired and the
question names a specific person, SPARQL will return nothing — route straight to SQL."*
Note this returns **FALLBACK**, not ANALYTICAL — so it goes to free-form LLM SQL, not the semantic
compiler. Also note `analysis` never carries a `canonical_name` key (`_analyse_question` returns
`surname` and `forename` only), so that third disjunct is always falsy.

Both overrides live *inside* the soft-signal window. If any of `has_relational`, `has_hierarchy`, or
`geography_intent` is true, neither override can fire and RELATIONAL wins unconditionally.

### 4.5 Priority 3 — ANALYTICAL

```python
analytical = (
    primary_intent in _ANALYTICAL_INTENTS                              # {population, eviction, emigration, tenancy, people}
    or output_mode in {"count", "aggregate", "trend", "list", "grouped"}
    or any(kw in q for kw in _ANALYTICAL_KEYWORDS)
    or slot_fill is not None
)
if analytical: return ANALYTICAL
```

**[DRIFT] ×2:** `CLAUDE.md` omits `"people"` from the intent set and gives the output-mode set as
`{count, aggregate, trend}` — the code also accepts `list` and `grouped`. Since `_analyse_question`
emits `grouped`/`list`/`count`/`detail`, the practical effect is that **any** question producing a
group-by or a list intent routes ANALYTICAL. Only `output_mode == "detail"` questions can reach
FALLBACK, and only if they also fail the intent and keyword tests.

`slot_fill is not None` means a rule-based fill *of any confidence* — including 0.10 — forces
ANALYTICAL.

### 4.6 Priority 4 — FALLBACK

`return FALLBACK` — the default. In practice reachable only for `output_mode == "detail"` questions
with `primary_intent == "overview"` or `"geography"`… and `geography` is caught by RELATIONAL first.
So FALLBACK is a narrow band: `primary_intent == "overview"`, `output_mode == "detail"`, no analytical
keyword, no slot fill, no relational/heritage/sensemaking/comparative keyword. Plus the person-detail
guard escape hatch from §4.4.

### 4.7 `CLAUDE.md` keyword-list diff

| List | Missing from `CLAUDE.md` | Notes |
|---|---|---|
| COMPARATIVE | `compared to`, `compared with`, `vs.` | `CLAUDE.md` writes `vs`; source is `" vs "` — spaces are significant |
| RELATIONAL | `connection between`, `same parish`, `belong to`, `belongs to`, `neighboring` | US spelling of `neighbouring` is a separate entry |
| HIERARCHY | `in the parish`, `which barony`, `what barony`, `in the county`, `which county`, `what county` | `CLAUDE.md` lists 10 of 15 |
| HERITAGE | `what is the history`, `historic` | `historic` is a substring of `historical`/`historically` |
| SENSEMAKING | `what was`, `coolattin estate` | `what was` is very broad — catches most past-tense narrative questions |
| ANALYTICAL kw | `percentage` | |
| ANALYTICAL intents | `people` | |
| ANALYTICAL output modes | `list`, `grouped` | biggest behavioural gap |
| Overrides | the person-detail → FALLBACK guard | undocumented entirely |

---

## 5. Per-route dispatch

All four routes share the same `try` block (3872–3922) and diverge on two decisions.

### 5.1 ANALYTICAL — LLM slot fill, with three fall-throughs

```python
if _intent_route == "analytical" and _semantic_slot_fill is not None:
    _sf_prompt      = _build_slot_fill_prompt(clean_q, analysis, townland_resolution)
    _sf_raw, _sf_meta = _llm_generate(_sf_prompt, purpose="slot_fill", max_tokens=256, temperature=0.0)
    _sf_parsed      = _parse_slot_fill(_sf_raw, clean_q)
    if _sf_parsed and _sf_parsed.confidence >= 0.70:
        _llm_slot_sql = _compile_semantic_sql(_sf_parsed, _clearances_count_column())
        if _llm_slot_sql:
            sql = _llm_slot_sql; llm_meta = _slot_fill_meta(_sf_parsed, sql)
            llm_meta["llm_provider"] = _sf_meta.get("provider")
            llm_meta["llm_model"]    = _sf_meta.get("model")
            query_provenance["strategy"] = "semantic_layer_llm"
```

The whole block is inside its own try/except (DEBUG-logged), and the outer guard `if not _llm_slot_sql:`
then runs free-form SQL.

**[DRIFT]** `CLAUDE.md`: *"ANALYTICAL — Phase 2 semantic_layer LLM slot-fill → deterministic SQL
compiler. **Never free-form LLM SQL**."* This is false on **four** distinct paths:

1. `_semantic_slot_fill is None` — the rule-based fill found nothing, so the LLM slot-fill branch is
   never entered at all. (Note the router itself will only have chosen ANALYTICAL via the intent/
   keyword/output-mode tests in that case, since `slot_fill is not None` was false.)
2. `_parse_slot_fill` returned `None` (unparseable LLM output).
3. `_sf_parsed.confidence < 0.70`.
4. `compile_sql` returned `None` for an otherwise valid fill.

In all four the code drops to `_generate_sql()` free-form LLM SQL. The **0.70** LLM-slot-fill
threshold is itself undocumented in `CLAUDE.md`.

SSE detail on success: `Phase 2 slot-fill [analytical]: emigration_count confidence=0.86`.

### 5.2 RELATIONAL, COMPARATIVE, FALLBACK, and ANALYTICAL-miss

```python
if not _llm_slot_sql:
    _few_shot = _p4_memory if _p4_memory else approved_matches
    sql, llm_meta = _generate_sql(clean_q, _ANNOTATED_SCHEMA, canonical_townland,
                                  analysis=analysis, approved_examples=_few_shot)
    vrti_postgres_sql, vrti_query_meta = _generate_vrti_postgres_query(clean_q, canonical_townland)
    query_provenance["strategy"] = ("validated_sql_unavailable"
                                    if llm_meta.get("mode") == "no_validated_sql" else "llm_sql")
```

Two things differ from the default pipeline:

- **Few-shot memory is actually supplied here** — `_p4_memory` (embedding-re-ranked) preferred over
  `approved_matches` (token-sort-ranked). This is the only place approved memory influences SQL
  generation anywhere in the codebase.
- **`_generate_vrti_postgres_query()` is called** (5087) rather than the rule-based
  `_fallback_vrti_postgres_sql()`. It still short-circuits to the rule template unless
  `ASK_GENERATE_VRTI_SQL_WITH_LLM` is truthy, returning `{"provider": "heuristic", "model":
  "local_rule", "mode": "quota_saving_template"}`.

SSE detail: `Phase 7 [relational]: llm_sql | VRTI: quota_saving_template | Model: claude-sonnet-4-6`.

**[DRIFT]** `CLAUDE.md`: *"RELATIONAL / HERITAGE — Phase 3 subgraph_engine … for qualitative context;
SQL handles all counts."* The first half is right (§6.1); "SQL handles all counts" is not a special
mechanism — RELATIONAL gets exactly the same free-form LLM SQL as FALLBACK. The route flag's only
effect on SQL is that it is **not** eligible for the slot-fill compiler.

Similarly, *"COMPARATIVE — ANALYTICAL SQL + RELATIONAL subgraph"* is misleading: COMPARATIVE gets
free-form SQL, not slot-fill SQL. It does get the subgraph (via `_force_subgraph`) and the Phase 6
annotation.

### 5.3 Corrected dispatch table

| Route | SQL path | Subgraph (Phase 3) | Extra |
|---|---|---|---|
| **ANALYTICAL** | LLM slot-fill → `compile_sql`, **if** rule fill existed *and* parse succeeded *and* conf ≥ 0.70 *and* compile succeeded; otherwise free-form `_generate_sql` | only if `is_subgraph_question()` fires independently | `strategy = semantic_layer_llm` or `llm_sql` |
| **RELATIONAL** | free-form `_generate_sql` with `_p4_memory` few-shot | forced (`_force_subgraph = True`) | — |
| **COMPARATIVE** | free-form `_generate_sql` with `_p4_memory` few-shot | forced | `query_provenance["phase6_fusion"] = True`; `kg_context["phase6_fusion_note"]` set if subgraph produced text |
| **FALLBACK** | free-form `_generate_sql` with `_p4_memory` few-shot | only if `is_subgraph_question()` fires | — |

### 5.4 SQL-generation failure

Identical to the default pipeline: `ASK_ALLOW_HEURISTIC_FALLBACK` on → `_fallback_sql()` +
`strategy = emergency_fallback`; off → `_diagnostic_message_sql()` +
`strategy = validated_sql_unavailable`.

---

## 6. Stages 2–5 in the legacy pipeline

Stage 2 (`framing_query`) and Stage 3 (`querying_database`) are byte-for-byte equivalent to the default
pipeline (doc 05 §7, doc 07 §§1–3), except the Stage-3 recovery warning reads *"…after SQLite reported
an execution issue"* rather than *"…after an execution error"*. Stage 4 (`querying_vrti_graph`) is
also identical, `force=True`.

### 6.1 Phase 3 — subgraph retrieval (legacy only)

```python
from backend.services.subgraph_engine import is_subgraph_question as _is_subgraph_q, retrieve_subgraph as _retrieve_subgraph
if _force_subgraph or _is_subgraph_q(clean_q, analysis, _semantic_slot_fill):
    _phase3_result = _retrieve_subgraph(clean_q, analysis, townland_resolution, sources=("vrti", "graphdb"))
    if _phase3_result and _phase3_result.linearized:
        kg_context["subgraph_linearized"] = _phase3_result.linearized
```

Two activation paths — the router flag, or the engine's own detector — so a fast-lane question can
still get a subgraph. Note the source comment: *"Core Rule 1: never used to answer count/aggregate
questions."*

**Call boundary** (doc 10). `SubgraphResult` fields read by `ask_service`: `.linearized`,
`.hierarchy`, `.siblings`, `.external_links`, `.sources_used`, `.question_type`, `.k_hops`, `.pruned`.
`sources=("vrti","graphdb")` requests both the remote VRTI endpoint and the local GraphDB store. The
whole block is try/excepted at DEBUG.

SSE detail: `Subgraph from vrti, graphdb · 2 hop(s), pruned · type: hierarchy`.

The legacy result payload therefore carries a real `subgraph_context` object, unlike the default
pipeline where it is always `None`.

### 6.2 Phase 6 annotation for COMPARATIVE

```python
if _intent_route == "comparative" and _phase3_result and _phase3_result.linearized:
    kg_context["phase6_fusion_note"] = (
        "This is a comparative question. The SQLite estate records provide counts and statistics; "
        "the VRTI knowledge graph subgraph provides qualitative and relational context. "
        "Synthesise both in your answer.")
```

Unlike the default pipeline's version of this string, this one is reachable.

### 6.3 Stage 4.5 — GraphDB SPARQL (live here)

```python
if ActiveConfig.GRAPHDB_ENABLED:      # ← no intent_route condition
```

**This is the key structural difference from the default pipeline**, where the same block also
requires `intent_route in (RELATIONAL, COMPARATIVE)` and is therefore dead. Here it runs on every
request whenever GraphDB is enabled (`config.py:77`, default `true`).

Sub-steps:

1. **`_generate_graphdb_sparql(clean_q, safe_sql)`** (5366) — three-stage:
   - `_match_sparql_template()` (5195) tries eight deterministic patterns first (count emigration /
     count evictions / count tenants — each optionally townland-scoped; evictions per year; emigrations
     per year; emigration by townland; emigration by parish; list emigrants from a townland; people by
     surname). It extracts the townland, year, year-range, and surname **from the SQL text** via regex,
     not from `analysis`. A hit returns `mode: "template_match"` with zero LLM cost.
   - Otherwise an LLM call with `_load_kg_context()` — the formatted contents of `data/kg_context.yaml`
     (prefixes, classes with per-property coverage and required/optional status, canonical patterns,
     and a "COMMON MISTAKES" list) — plus 10 numbered strict rules (R1–R10). Output is de-fenced,
     `PREFIX` lines stripped, `#` comments stripped; must start with `SELECT` or it degrades to the
     generic listing fallback.
   - **Post-validation**: `_sparql_uses_forbidden_props()` (5190) rejects any query mentioning a
     SQLite column name used as an RDF predicate —
     `co:hasEmigrationRecord`, `co:hasEvictionRecord`, `co:hasTenancyRecord`, `co:totalFamilySize`,
     `co:adults`, `co:children`, `co:ship`, `co:destination`, `co:chief_tenant`, `co:townland_id`,
     `co:county`, `co:barony`, `co:record_id` (plus snake_case variants). A rejection retries the
     template matcher, then falls back to the generic listing query.
2. **`_gdb.probe()` / `_gdb.triple_count()` / `_gdb.query(sparql_text)`** — see doc 11. A zero triple
   count sets `setup_hint: "GraphDB is running but the repository is empty. Load data with:
   python3 scripts/rdf_uplift.py --import"`.
3. **Mismatch explanation** — when GraphDB is available *and* loaded, a row-count difference, or a
   single-row-each value difference detected via `_first_numeric`, triggers
   `_explain_result_mismatch()` (5501): an LLM call (`max_tokens=250, temperature=0.1`) asking for a
   2–3-sentence cause analysis, prompted to consider schema mismatch, scope, query semantics, and
   normalisation, and to call out SQL-column-as-RDF-predicate errors explicitly.

SSE detail on success: `12 row(s) · 143,209 triples loaded · SPARQL gen 840 ms · query 61 ms`;
otherwise `GraphDB offline — SPARQL generated, not executed` or `SPARQL generation failed`.

**Timing bug worth noting.** The legacy block reuses the name `t0` for both the sub-step timers and
computes `total_ms = int((time.perf_counter() - t0_stage) * 1000)` — but the sub-step reassignments of
`t0` mean the intermediate `sparql_gen_ms` / `graphdb_ms` are correct while `total_ms` is measured
from `t0_stage`, which is correct. The default pipeline's rewritten version uses distinct names
(`_gdb_stage_t0`, `_sparql_t0`, `_gdb_exec_t0`) and is cleaner.

### 6.4 Phase 6 — fusion (live here)

`_fuse_lanes()` receives the same arguments as in the default pipeline, but `graphdb_rows` is now
genuinely populated, so both comparison modes described in doc 05 §9.1 can fire and
`discrepancy_count` / `agreement_count` are meaningful. Detected discrepancies are injected as
`kg_context["phase6_discrepancies"]` and `kg_context["phase6_fusion_text"]`.

### 6.5 Stage 5 — synthesis via `_generate_rephrased_answer` (NOT `_claude_synthesize_answer`)

**[DRIFT] — the two pipelines use completely different synthesis functions.** `CLAUDE.md` describes
one Phase 7 for both.

```python
llm_rephrased_answer, llm_rewrite_meta = _generate_rephrased_answer(
    question=clean_q, actual_answer=actual_answer, summary_block=summary_block,
    data_context=llm_data_context, supporting_context=supporting_context, kg_context=kg_context)
```

`_generate_rephrased_answer()` (9015):

```python
prompt = _build_rephrase_prompt(...)
text, meta = _llm_generate(prompt, purpose="answer_rephrase", max_tokens=350, temperature=0.1)
cleaned = _strip_answer_formatting(text)
if not cleaned: raise RuntimeError("Empty answer rewrite from LLM.")
_assert_rewrite_numbers_supported(cleaned, actual_answer, summary_block, data_context, supporting_context, kg_context)
return cleaned, {**meta, "mode": "llm_rewrite"}
```

Differences from the default pipeline's synthesis:

| Aspect | Legacy (`_generate_rephrased_answer`) | Default (`_claude_synthesize_answer`) |
|---|---|---|
| Prompt | `_build_rephrase_prompt` — "Rephrase this historical archive result in 2–4 sentences" | `_SYNTHESIS_SYSTEM_PROMPT` — 5-section narrative, 5–8 sentences |
| Provider selection | `_llm_generate()` — straight down `_llm_provider_order()` | `ASK_SYNTHESIS_MODEL` primary, then cascade |
| System/user split | none (single prompt string) | preserved for Claude |
| `max_tokens` | 350 | 1000 |
| Numeric gate | `_assert_rewrite_numbers_supported()` — **raises `RuntimeError`** | `_gate_violations()` — regenerate, then switch provider, then blank |
| Allowlist source | `_allowed_rewrite_number_tokens()` over `{actual_answer, summary, data_context, supporting_context, townland_context}` | `_synthesis_allowed_numbers_from_input()` over the whole serialised input JSON + question |
| On failure | exception → `warnings.append(f"LLM rewrite unavailable: {exc}")`, `llm_rewrite_meta.mode = "not_generated"` | `gate_outcome="fallback"`, structured provenance |
| Cross-verifier | not run | run for degraded strategies |

Both allowlists use the same 3+-digit rule and the same digit-substring expansion; the legacy version
simply has no retry.

`_build_rephrase_prompt()` (9051) assembles a compact JSON payload (`question`,
`data_backed_answer`, `key_stats`, `townlands_mentioned`, `fuzzy_match_note`, optional
`townland_context`, optional `zero_result_diagnostics`) plus conditional rule blocks:

| Rule block | Condition |
|---|---|
| `list_note` — "do NOT list all of them" | `row_count > 10` |
| `townland_rule` — weave in census/eviction figures | townland deep context found |
| `kg_rule` + `kg_block` — use KG for relational answers, **never for counts** | `subgraph_linearized` present |
| `discrepancy_rule` — "you MUST state this in your answer: …" | `phase6_fusion_text` present |
| `fusion_rule` | `phase6_fusion_note` present and no fusion text |
| `zero_result_rule` — 4 numbered requirements + serialised diagnostics | `row_count == 0` |

The zero-result diagnostics are injected into `llm_data_context["local_database"]` (line 4264) before
the prompt is built — a different location from the default pipeline, which puts them in
`_sql_result_for_synthesis`.

### 6.6 Result payload

Identical key set to the default pipeline **minus** `graphrag_context` (GraphRAG is not called here)
and with `subgraph_context` genuinely populated. `query_provenance` carries `intent_route`,
`approved_query_candidates`, and possibly `phase6_fusion` / `p4_template_id` / `p4_cosine_score`, but
no `numeric_gate_outcome`, `verifier`, `graphrag`, `townland_summary`, or `new_pipeline` keys.

The legacy pipeline does **not** emit a `stage="done"` SSE event; `preparing_output/completed` is its
last progress event before `result`.

---

## 7. Summary of the two pipelines side by side

| Capability | Default (`true`) | Legacy (`false`) |
|---|---|---|
| Semantic layer rule-based fast lane | ✗ | ✓ (≥ 0.80) |
| Phase 4 embedding template fast lane | ✗ | ✓ (cosine ≥ 0.68 + keyword gate) |
| Verified analysis (15 curated ids) | ✗ | ✓ |
| Direct memory reuse | ✗ | ✓ (composite score ≥ 92 + intent/mode/townland match) |
| Approved memory as SQL few-shot | ✗ (`approved_examples=None`) | ✓ (`_p4_memory` / `approved_matches`) |
| Intent router | ✗ (`route = "direct"`) | ✓ |
| LLM slot fill (ANALYTICAL) | ✗ | ✓ (≥ 0.70) |
| Subgraph engine (Phase 3) | ✗ | ✓ |
| GraphRAG (in-process NetworkX) | ✓ | ✗ |
| Townland summary (5 queries) | ✓ | ✗ |
| Person identity resolution | ✓ | ✗ |
| GraphDB SPARQL (Stage 4.5) | ✗ (dead branch) | ✓ |
| Meaningful `_fuse_lanes` output | ✗ (always 0/0) | ✓ |
| Synthesis function | `_claude_synthesize_answer` | `_generate_rephrased_answer` |
| Numeric gate behaviour | regenerate → switch provider → blank | raise → warning |
| Cross-verifier | ✓ (degraded strategies only) | ✗ |
| `stage="done"` SSE event | ✓ | ✗ |
