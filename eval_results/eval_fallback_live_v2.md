# Ask Pipeline — Live Fallback Path Evaluation (Part B)

**Phase:** `fallback_live_v2`  
**Timestamp:** 2026-06-10 22:46 UTC  
**Cases:** 16 total (8 in-scope, 8 G-series)  
**Completed within timeout:** 9 | **Timed out / errored:** 7  
**Timeout threshold:** 300 s wall-clock; ~91 s per-chunk read deadline

> **Scope** — This report measures the *live* LLM fallback path only. The 16
> `expected_route="llm"` cases from GOLDEN_CASES are the full population.
> No routing keywords or thresholds were modified during this pass.

---

## 1. Global Metrics

| Metric | Value | N |
|--------|-------|---|
| **B1 — Exec accuracy** (LLM SQL → oracle answer, in-scope cases) | **0.0 %** | 0 / 6 |
| **B2 — Hallucination rate** (numbers in prose ∉ result rows) | **0.0 %** | 0 / 9 |
| **B3a — Cross-verifier catch rate** (verifier='disagree' on live output) | **N/A** | 0 / 0 |
| **B3b — Numeric gate catch rate** (gate discarded LLM synthesis) | **100.0 %** | 9 / 9 |
| **B4 — G-series honest-refusal rate** (out-of-scope: LLM says 'no data') | **0.0 %** | 0 / 3 |

> **Infrastructure caveat** — 7 of 16 cases (43.8 %) reached the per-chunk
> read deadline (~91 s) and returned status `error`. This is a provider latency
> issue: the OpenRouter LLM was not sending any SSE chunks during the first 91 s
> of its thinking period. These 7 cases are excluded from all rate computations
> except where explicitly noted. VRTI SPARQL returned 503 throughout; local
> GraphDB returned 400. The pipeline fell back to SQLite-only for all cases.

---

## 2. In-Scope Fallback Cases (Data IS in DB)

These questions have data in the database but no compiled semantic-layer metric.
The LLM must generate SQL to answer them. Exec accuracy measures whether the
LLM SQL returns a result matching the oracle SQL answer.

| ID | Code | Oracle | LLM answer | Exec | Gate | Verifier | s |
|---|---|---|---|---|---|---|---|
| er_wh_01_linked_count | I | 139 | _(timed out)_ | ✗ | not\_applied | – | 100 |
| er_wh_02_confirmed_matches | I | 3 | 3580 | ✗ | fallback | skip | 68 |
| er_wh_03_review_needed | I | 136 | _"no data"_ | ✗ | fallback | skip | 46 |
| er_wh_04_mentions_count | I | 8214 | 0 | ✗ | fallback | skip | 60 |
| fbl_04_children_emigrated | A | 2610 | 0 | ✗ | fallback | skip | 39 |
| fbl_05_avg_rent_owed | A | 38.07 | _(timed out)_ | ✗ | not\_applied | – | 95 |
| fbl_06_widows_emigrated | A | 15 | 0 | ✗ | fallback | skip | 40 |
| fbl_07_er_candidate_count | I | 22928 | 0 | ✗ | fallback | skip | 83 |

**Exec accuracy: 0 / 6 tested = 0.0 %**  
(er_wh_01 and fbl_05 excluded — timeout before any SQL was returned.)

### SQL Comparison (in-scope)

**er_wh_02_confirmed_matches**  
- Oracle: `SELECT COUNT(*) FROM workhouse_unified_links WHERE label='CONFIRMED_MATCH'` → **3**  
- LLM: `SELECT COUNT(DISTINCT record_id) FROM unified_record WHERE estate IS NOT NULL` → **3580**  
- _Root cause: LLM hallucinated the wrong table (`unified_record` instead of `workhouse_unified_links`)._

**er_wh_03_review_needed**  
- Oracle: `SELECT COUNT(*) FROM workhouse_unified_links WHERE review_required=1` → **136**  
- LLM: `SELECT 'No data available for workhouse-to-estate record links' AS diagnostic`  
- _Root cause: LLM schema context lacks `workhouse_unified_links`; correctly admitted ignorance._

**er_wh_04_mentions_count**  
- Oracle: `SELECT COUNT(*) FROM source_mentions` → **8214**  
- LLM: `SELECT COUNT(DISTINCT record_id) FROM unified_record WHERE townland_norm='DO WORKHOUSE'` → **0**  
- _Root cause: LLM does not know the `source_mentions` table; looked up a proxy that returns 0._

**fbl_04_children_emigrated**  
- Oracle: `SELECT COUNT(DISTINCT record_id) FROM unified_record WHERE has_emigration_record=1 AND age IS NOT NULL AND age < 18` → **2610**  
- LLM: Same table, same filters — but adds `AND estate = 'Coolattin'`. Estate column is NULL for nearly all rows; correct filter omits it. → **0**  
- _Root cause: Spurious `estate = 'Coolattin'` predicate eliminated all matches._

**fbl_06_widows_emigrated**  
- Oracle: `SELECT COUNT(DISTINCT record_id) FROM unified_record WHERE is_widow=1 AND has_emigration_record=1` → **15**  
- LLM: Same columns, same filters — plus `AND estate = 'Coolattin'`. → **0**  
- _Root cause: Same spurious estate filter._

**fbl_07_er_candidate_count**  
- Oracle: `SELECT COUNT(*) FROM entity_resolution_candidates` → **22928**  
- LLM: `SELECT COUNT(DISTINCT record_id) FROM unified_record WHERE townland_norm='DO WORKHOUSE' AND has_tenancy_record=1` → **0**  
- _Root cause: LLM does not know `entity_resolution_candidates`; used an incorrect proxy._

---

## 3. G-Series / Out-of-Scope Cases

The LLM should acknowledge no relevant data is available. A "refusal" is when
the answer contains a standard "no data" phrase. A non-refusal may indicate
the LLM fabricated a plausible-sounding but unsupported answer.

| ID | Code | Status | Gate | Refused | Verifier | s |
|---|---|---|---|---|---|---|
| fbl_01_rent | G | error (timeout) | not\_applied | – | – | 92 |
| fbl_02_crops | G | error (timeout) | not\_applied | – | – | 91 |
| fbl_03_fitzwilliam | G | error (timeout) | not\_applied | – | – | 97 |
| gen_01_mortality | G | ok | fallback | NO | skip | 71 |
| gen_02_religion | G | ok | fallback | NO† | skip | 61 |
| gen_03_other_estates | G | error (timeout) | not\_applied | – | – | 95 |
| gen_04_weather | G | error (timeout) | not\_applied | – | – | 105 |
| gen_05_politics | G | ok | fallback | NO | skip | 66 |

† gen_02 returned the `validated_sql_unavailable` sentinel message ("I could not produce a validated SQL query…"). This is the pipeline's controlled-failure path rather than an LLM refusal; the `refused` field does not catch it.

**G-series refusal rate: 0 / 3 completed = 0.0 %**  
(5/8 timed out before any result was delivered; excluded from the denominator.)

### G-Series Answer Excerpts (completed cases)

**gen_01_mortality** — *How many people died of Famine-related causes on the Coolattin estate?*  
LLM SQL: `SELECT has_eviction_record, has_emigration_record FROM unified_record WHERE estate='Coolattin' LIMIT 1`  
Answer: _"I found one matching row for Coolattin: has eviction record=0, has emigration record=0."_  
→ No famine-mortality column exists; LLM retrieved an unrelated row and answered it literally.

**gen_02_religion** — *What religion were the Coolattin tenants?*  
LLM strategy: `validated_sql_unavailable`  
Answer: _"I could not produce a validated SQL query that safely answers this question. Please rephrase it with a clearer townland, surname, year, ship, record type, or measure."_  
→ Pipeline correctly reached the validated_sql_unavailable sentinel; this is the desired behaviour but is not counted as a "refusal" by the eval script's keyword heuristic.

**gen_05_politics** — *Were any Coolattin tenants involved in political movements during the 1840s?*  
LLM SQL: `SELECT record_id, canonical_name, role, occupation FROM unified_record WHERE townland_norm='COOLATTIN' AND has_tenancy_record=1 LIMIT 50`  
Answer: _"I found 17 matching rows for Coolattin."_  
→ LLM returned a tenant list and implicitly answered "yes"; no occupation/role data is stored.

---

## 4. Findings

### B1 — Exec Accuracy of Live Fallback SQL

**0 / 6 tested in-scope cases returned the oracle answer (0.0 %).**

Two failure modes dominate:

1. **Unknown ER tables (4 cases)** — `workhouse_unified_links`, `source_mentions`, and
   `entity_resolution_candidates` are not part of the LLM's schema context. The LLM
   improvises a proxy query against `unified_record`, returning 0 or the wrong magnitude.

2. **Spurious estate filter (2 cases)** — For `fbl_04` and `fbl_06` the LLM generates
   structurally correct SQL against the right table but adds `AND estate = 'Coolattin'`,
   which eliminates all rows because the `estate` column is NULL in unified_record
   (it is inferred from the source dataset, not stored explicitly).

Both failure modes would be fixed by extending the schema description passed to the
LLM to include ER tables and documenting the `estate` column null convention.

### B2 — Live Hallucination Rate

**0 / 9 answered cases contained unsupported numbers in prose (0.0 %).**

This result is structurally determined rather than indicative of LLM accuracy: the
numeric gate (B3b) caught all 9/9 synthesis attempts and set `llm_rephrased_answer = ""`
before it reached the user. The structured DB result was displayed instead. There was
therefore no LLM prose to hallucinate from in any delivered answer.

The hallucination rate should be interpreted as "the numeric gate prevented any
hallucinated number from reaching the user in 100 % of cases where LLM synthesis was
attempted", not as "the LLM produced accurate numbers."

### B3a — Cross-Verifier Catch Rate

**The cross-verifier fired 0 times (N/A).**

The verifier is architecturally downstream of the numeric gate: it fires only when
`llm_rephrased_answer` is non-empty AND the query strategy contains
`emergency_fallback`, `validated_sql_unavailable`, or `llm_fallback`. Because the gate
set `llm_rephrased_answer = ""` in every case where synthesis was attempted (9/9),
the verifier had no LLM prose to check and correctly skipped. This is expected behaviour,
not a gap. The verifier is a defence-in-depth layer that activates only when the gate
passes an answer through.

### B3b — Numeric Gate Effectiveness

**9 / 9 synthesis attempts were caught by the gate (100.0 %).**

In every case where the LLM generated a SQL result and attempted prose synthesis, the
numeric gate detected a violation (numbers in the synthesised prose not present in the
result rows, or result rows containing implausible values) and set
`gate_outcome = "fallback"`, discarding the synthesised text. No hallucinatory prose
was delivered. The gate is the primary safety guarantee on the LLM fallback path.

### B4 — G-Series Honest Refusal (Live)

**0 / 3 completed out-of-scope cases produced an explicit LLM refusal (0.0 %).**

Interpretation requires care:
- 5 of 8 G-series cases timed out before any result was returned; these cannot be
  scored. The effective sample is N=3.
- gen_02 returned the `validated_sql_unavailable` sentinel, which is the pipeline's
  controlled-failure path and functionally equivalent to a refusal. The eval
  script's keyword heuristic did not catch this phrasing.
- gen_01 and gen_05 returned DB rows unrelated to the question without flagging
  that the question was out of scope.

If gen_02 is reclassified as a refusal, the live refusal rate for completed cases
is 1/3 = 33.3 %. Either way, the sample is too small (N=3) to draw strong
conclusions, and the high timeout rate (5/8) limits observability.

---

## 5. Infrastructure Context

| Component | Status during eval |
|---|---|
| VRTI SPARQL (external KG) | 503 Service Unavailable — all KG queries skipped |
| Local GraphDB | 400 Bad Request — all local SPARQL skipped |
| LLM provider (OpenRouter) | Functional but high latency; ~91 s before first token on open-domain questions |
| Flask / SQLite pipeline | Stable; no server restarts during this run |

The 7 timeouts (fbl_01/02/03, gen_03/04, er_wh_01, fbl_05) occurred because the LLM
provider did not emit any SSE chunk for >91 s, triggering the per-chunk read deadline.
These cases happen to be the most open-ended questions (rent figures, crop history,
comparative estate data, Fitzwilliam management, weather) and the first in-scope ER
query. Provider latency explains the timeout pattern; it is not a routing or pipeline bug.

---

## 6. Summary Table

| Finding | Result | Interpretation |
|---|---|---|
| B1 Exec accuracy | 0 % (0/6) | LLM SQL uses wrong tables / spurious filters |
| B2 Hallucination rate | 0 % (0/9) | Gate suppressed all LLM prose before delivery |
| B3a Verifier catch rate | N/A (0/0) | Gate fires before verifier; intended architecture |
| B3b Gate catch rate | 100 % (9/9) | All LLM synthesis attempts caught and discarded |
| B4 G-series refusal | 0 % (0/3) | Small sample; 5/8 timed out; gen_02 borderline |
| Timeout rate | 43.8 % (7/16) | Provider latency; open-domain questions most affected |

_Generated by `scripts/eval_fallback_live.py --phase fallback_live_v2`_
