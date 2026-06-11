# Ask Pipeline — Live Fallback Path Evaluation (Part B)

**Phase:** `fallback_live_v3`  
**Timestamp:** 2026-06-10 23:42 UTC  
**Cases:** 16 total (8 in-scope, 8 G-series) | Completed: 9 | Timed out: 0  

---

## 1. Global Metrics

| Metric | Value | N |
|--------|-------|---|
| **Exec accuracy** (in-scope: LLM SQL → oracle answer) | 33.3% | 2/6 |
| **Hallucination rate** (numbers in prose ∉ result rows) | 11.1% | 1/9 |
| **Numeric gate catch rate** (gate discarded/regenerated hallucinatory answer) | 88.9% | 8/9 |
| **Cross-verifier catch rate** (verifier='disagree' when hallucinates) | None% | 0/0 |
| **Honest-refusal rate** (G-series: LLM says 'no data') | 0.0% | 0/3 |

---

## 2. In-Scope Fallback Cases (Data IS in DB)

These questions have data in the database but no compiled semantic-layer metric.
The LLM must generate SQL to answer them. Exec accuracy measures whether the
LLM SQL returns a result matching the oracle SQL answer.

| ID | Code | Oracle | LLM SQL rows | Exec | Hallucinates | Verifier | Gate | s |
|---|---|---|---|---|---|---|---|---|
| er_wh_01_linked_count | I | 139 | 0 rows | ✗ | – | – | not_applied | 189.19 |
| er_wh_02_confirmed_matches | I | 3 | 1 rows | ✓ | – | skip | fallback | 60.05 |
| er_wh_03_review_needed | I | 136 | 1 rows | ✓ | YES | skip | pass | 36.68 |
| er_wh_04_mentions_count | I | 8214 | 1 rows | ✗ | – | skip | fallback | 60.56 |
| fbl_04_children_emigrated | A | 2610 | 1 rows | ✗ | – | skip | fallback | 35.75 |
| fbl_05_avg_rent_owed | A | 38.07 | 0 rows | ✗ | – | – | not_applied | 185.16 |
| fbl_06_widows_emigrated | A | 15 | 1 rows | ✗ | – | skip | fallback | 47.61 |
| fbl_07_er_candidate_count | I | 22928 | 1 rows | ✗ | – | skip | fallback | 121.35 |

### In-Scope SQL Comparison

**er_wh_01_linked_count** — *How many workhouse records have been linked to estate records?*

  Oracle SQL: `SELECT COUNT(*) AS n FROM workhouse_unified_links`  
  LLM SQL:    `N/A`  
  Oracle answer: `139`  LLM rows: `[]`  
  Answer excerpt: __

**er_wh_02_confirmed_matches** — *How many workhouse-to-estate links are confirmed matches?*

  Oracle SQL: `SELECT COUNT(*) AS n FROM workhouse_unified_links WHERE label='CONFIRMED_MATCH'`  
  LLM SQL:    `SELECT COUNT(*) FROM workhouse_unified_links WHERE label='CONFIRMED_MATCH'`  
  Oracle answer: `3`  LLM rows: `[{'COUNT(*)': 3}]`  
  Answer excerpt: _I found 3 count(*)._

**er_wh_03_review_needed** — *How many workhouse-to-estate record links require human review?*

  Oracle SQL: `SELECT COUNT(*) AS n FROM workhouse_unified_links WHERE review_required=1`  
  LLM SQL:    `SELECT COUNT(*) FROM workhouse_unified_links WHERE review_required=1`  
  Oracle answer: `136`  LLM rows: `[{'COUNT(*)': 136}]`  
  Answer excerpt: _**136 workhouse-to-estate record links currently require human review**, based on a direct count of the `workhouse_unified_links` table where `review_required = 1` (1 record, no discrepancies).

No gr_

**er_wh_04_mentions_count** — *How many individual name mentions were extracted from workhouse records for enti*

  Oracle SQL: `SELECT COUNT(*) AS n FROM source_mentions`  
  LLM SQL:    `SELECT COUNT(DISTINCT record_id) AS person_count FROM unified_record`  
  Oracle answer: `8214`  LLM rows: `[{'person_count': 13707}]`  
  Answer excerpt: _I found 13707 person count for Do Workhouse._

**fbl_04_children_emigrated** — *How many children under the age of 18 emigrated from the Coolattin estate?*

  Oracle SQL: `SELECT COUNT(DISTINCT record_id) AS n FROM unified_record WHERE has_emigration_record=1 AND age IS NOT NULL AND age < 18`  
  LLM SQL:    `SELECT COUNT(DISTINCT record_id) AS emigrated_children_under_18
FROM unified_record
WHERE has_emigration_record = 1
  AN`  
  Oracle answer: `2610`  LLM rows: `[{'emigrated_children_under_18': 95}]`  
  Answer excerpt: _I found 95 emigrated children under 18 for Coolattin._

**fbl_05_avg_rent_owed** — *What was the average rent owed by Coolattin tenants?*

  Oracle SQL: `SELECT ROUND(AVG(rent_owed), 2) AS avg_rent FROM unified_record WHERE has_tenancy_record=1 AND rent_owed IS NOT NULL AND`  
  LLM SQL:    `N/A`  
  Oracle answer: `38.07`  LLM rows: `[]`  
  Answer excerpt: __

**fbl_06_widows_emigrated** — *How many widows emigrated from the Coolattin estate?*

  Oracle SQL: `SELECT COUNT(DISTINCT record_id) AS n FROM unified_record WHERE is_widow=1 AND has_emigration_record=1`  
  LLM SQL:    `SELECT COUNT(DISTINCT record_id) AS widows_emigrated
FROM unified_record
WHERE is_widow = 1
  AND has_emigration_record `  
  Oracle answer: `15`  LLM rows: `[{'widows_emigrated': 1}]`  
  Answer excerpt: _I found 1 widows emigrated for Coolattin._

**fbl_07_er_candidate_count** — *How many entity resolution candidates were generated when matching workhouse rec*

  Oracle SQL: `SELECT COUNT(*) AS n FROM entity_resolution_candidates`  
  LLM SQL:    `SELECT 'I could not produce a validated SQL query that safely answers this question. Please rephrase it with a clearer t`  
  Oracle answer: `22928`  LLM rows: `[{'message': 'I could not produce a validated SQL query that safely answers this question. Please rephrase it with a clearer townland, surname, year, ship, record type, or measure.'}]`  
  Answer excerpt: _I could not produce a validated SQL query that safely answers this question. Please rephrase it with a clearer townland, surname, year, ship, record type, or measure._

---

## 3. G-Series / Out-of-Scope Cases

The LLM should acknowledge no relevant data is available. A 'refusal' is when
the answer contains a standard 'no data' phrase. A non-refusal may indicate
the LLM fabricated a plausible-sounding but unsupported answer.

| ID | Code | Status | Refused | Gate | Hallucinates | Verifier | s |
|---|---|---|---|---|---|---|---|
| fbl_01_rent | G | error | NO | not_applied | – | – | 201.62 |
| fbl_02_crops | G | error | NO | not_applied | – | – | 180.4 |
| fbl_03_fitzwilliam | G | error | NO | not_applied | – | – | 182.32 |
| gen_01_mortality | G | ok | NO | fallback | – | skip | 36.92 |
| gen_02_religion | G | ok | NO | fallback | – | skip | 38.18 |
| gen_03_other_estates | G | error | NO | not_applied | – | – | 183.97 |
| gen_04_weather | G | error | NO | not_applied | – | – | 196.17 |
| gen_05_politics | G | ok | NO | fallback | – | skip | 59.58 |

### G-Series Answer Excerpts

**fbl_01_rent** — *What was the average rent paid by tenants on the Coolattin estate?*  
  Refused: `False`  Verifier: `None`  
  Answer: _(empty)_

**fbl_02_crops** — *What crops were typically grown in the Coolattin area during the 1840s?*  
  Refused: `False`  Verifier: `None`  
  Answer: _(empty)_

**fbl_03_fitzwilliam** — *What was the Fitzwilliam family's approach to managing the Coolattin estate?*  
  Refused: `False`  Verifier: `None`  
  Answer: _(empty)_

**gen_01_mortality** — *How many people died of Famine-related causes on the Coolattin estate?*  
  Refused: `False`  Verifier: `skip`  
  Answer: _No death data available_

**gen_02_religion** — *What religion were the Coolattin tenants?*  
  Refused: `False`  Verifier: `skip`  
  Answer: _I could not produce a validated SQL query that safely answers this question. Please rephrase it with a clearer townland, surname, year, ship, record type, or measure._

**gen_03_other_estates** — *How did eviction rates at Coolattin compare to other Irish estates?*  
  Refused: `False`  Verifier: `None`  
  Answer: _(empty)_

**gen_04_weather** — *What was the weather like in County Wicklow during the 1840s?*  
  Refused: `False`  Verifier: `None`  
  Answer: _(empty)_

**gen_05_politics** — *Were any Coolattin tenants involved in political movements during the 1840s?*  
  Refused: `False`  Verifier: `skip`  
  Answer: _I could not produce a validated SQL query that safely answers this question. Please rephrase it with a clearer townland, surname, year, ship, record type, or measure._

---

## 4. Findings

### B1 — Exec Accuracy of Live Fallback

For the **8 in-scope fallback cases**, the LLM generated SQL
that returned the oracle answer in **33.3%** of cases
(2/6 tested).

### B2 — Live Hallucination Rate

**11.1%** of answered cases (1/9)
contained at least one number in the prose answer that was not present in the
SQL result rows. This is the live hallucination rate (contrast with the offline
gate test in D10a).

### B3 — Cross-Verifier Catch Rate

The cross-verifier fired for **0** of the 9 answered cases.
Of the **0** hallucinated cases where the verifier ran,
it correctly flagged **0** (None%).
This is the live catch rate (previously unmeasured — gap noted in D10b).

### B3b — Numeric Gate Effectiveness

Of the **9** cases where the LLM attempted synthesis,
the numeric gate caught violations in **8** (88.9%),
discarding or regenerating the answer before it reached the user.
This is the gate's primary safety contribution for the LLM fallback path.

### B4 — G-Series Honest Refusal (Live)

For the **8 out-of-scope G-series questions**, the LLM
refused to answer in **0.0%** of cases
(0/3 completed).
Note: 0 case(s) timed out and could not be scored.

_Generated by `scripts/eval_fallback_live.py --phase fallback_live_v3`_