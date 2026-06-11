# Ask Pipeline — Live Fallback Path Evaluation (Part B)

**Phase:** `fallback_live`  
**Timestamp:** 2026-06-10 20:18 UTC  
**Cases:** 16 total (8 in-scope, 8 G-series)  

---

## 1. Global Metrics

| Metric | Value | N |
|--------|-------|---|
| **Exec accuracy** (in-scope: LLM SQL → oracle answer) | 0.0% | 0/8 |
| **Hallucination rate** (numbers in prose ∉ result rows) | None% | 0/0 |
| **Cross-verifier catch rate** (verifier='disagree' when hallucinates) | None% | 0/0 |
| **Honest-refusal rate** (G-series: LLM says 'no data') | None% | 0/0 |

---

## 2. In-Scope Fallback Cases (Data IS in DB)

These questions have data in the database but no compiled semantic-layer metric.
The LLM must generate SQL to answer them. Exec accuracy measures whether the
LLM SQL returns a result matching the oracle SQL answer.

| ID | Code | Oracle | LLM SQL rows | Exec | Hallucinates | Verifier | Gate | s |
|---|---|---|---|---|---|---|---|---|
| er_wh_01_linked_count | I | 139 | 0 rows | ✗ | – | – | not_applied | 67.09 |
| er_wh_02_confirmed_matches | I | 3 | 0 rows | ✗ | – | – | not_applied | 23.65 |
| er_wh_03_review_needed | I | 136 | 0 rows | ✗ | – | – | not_applied | 21.52 |
| er_wh_04_mentions_count | I | 8214 | 0 rows | ✗ | – | – | not_applied | 23.68 |
| fbl_04_children_emigrated | A | 2610 | 0 rows | ✗ | – | – | not_applied | 14.58 |
| fbl_05_avg_rent_owed | A | 38.07 | 0 rows | ✗ | – | – | not_applied | 30.48 |
| fbl_06_widows_emigrated | A | 15 | 0 rows | ✗ | – | – | not_applied | 4.05 |
| fbl_07_er_candidate_count | I | 22928 | 0 rows | ✗ | – | – | not_applied | 8.17 |

### In-Scope SQL Comparison

**er_wh_01_linked_count** — *How many workhouse records have been linked to estate records?*

  Oracle SQL: `SELECT COUNT(*) AS n FROM workhouse_unified_links`  
  LLM SQL:    `N/A`  
  Oracle answer: `139`  LLM rows: `[]`  
  Answer excerpt: __

**er_wh_02_confirmed_matches** — *How many workhouse-to-estate links are confirmed matches?*

  Oracle SQL: `SELECT COUNT(*) AS n FROM workhouse_unified_links WHERE label='CONFIRMED_MATCH'`  
  LLM SQL:    `N/A`  
  Oracle answer: `3`  LLM rows: `[]`  
  Answer excerpt: __

**er_wh_03_review_needed** — *How many workhouse-to-estate record links require human review?*

  Oracle SQL: `SELECT COUNT(*) AS n FROM workhouse_unified_links WHERE review_required=1`  
  LLM SQL:    `N/A`  
  Oracle answer: `136`  LLM rows: `[]`  
  Answer excerpt: __

**er_wh_04_mentions_count** — *How many individual name mentions were extracted from workhouse records for enti*

  Oracle SQL: `SELECT COUNT(*) AS n FROM source_mentions`  
  LLM SQL:    `N/A`  
  Oracle answer: `8214`  LLM rows: `[]`  
  Answer excerpt: __

**fbl_04_children_emigrated** — *How many children under the age of 18 emigrated from the Coolattin estate?*

  Oracle SQL: `SELECT COUNT(DISTINCT record_id) AS n FROM unified_record WHERE has_emigration_record=1 AND age IS NOT NULL AND age < 18`  
  LLM SQL:    `N/A`  
  Oracle answer: `2610`  LLM rows: `[]`  
  Answer excerpt: __

**fbl_05_avg_rent_owed** — *What was the average rent owed by Coolattin tenants?*

  Oracle SQL: `SELECT ROUND(AVG(rent_owed), 2) AS avg_rent FROM unified_record WHERE has_tenancy_record=1 AND rent_owed IS NOT NULL AND`  
  LLM SQL:    `N/A`  
  Oracle answer: `38.07`  LLM rows: `[]`  
  Answer excerpt: __

**fbl_06_widows_emigrated** — *How many widows emigrated from the Coolattin estate?*

  Oracle SQL: `SELECT COUNT(DISTINCT record_id) AS n FROM unified_record WHERE is_widow=1 AND has_emigration_record=1`  
  LLM SQL:    `N/A`  
  Oracle answer: `15`  LLM rows: `[]`  
  Answer excerpt: __

**fbl_07_er_candidate_count** — *How many entity resolution candidates were generated when matching workhouse rec*

  Oracle SQL: `SELECT COUNT(*) AS n FROM entity_resolution_candidates`  
  LLM SQL:    `N/A`  
  Oracle answer: `22928`  LLM rows: `[]`  
  Answer excerpt: __

---

## 3. G-Series / Out-of-Scope Cases

The LLM should acknowledge no relevant data is available. A 'refusal' is when
the answer contains a standard 'no data' phrase. A non-refusal may indicate
the LLM fabricated a plausible-sounding but unsupported answer.

| ID | Code | Refused | Hallucinates | Unsupported #s | Verifier | s |
|---|---|---|---|---|---|---|
| fbl_01_rent | G | NO | – | – | – | 143.16 |
| fbl_02_crops | G | NO | – | – | – | 120.53 |
| fbl_03_fitzwilliam | G | NO | – | – | – | 124.39 |
| gen_01_mortality | G | NO | – | – | skip | 62.55 |
| gen_02_religion | G | NO | – | – | skip | 52.23 |
| gen_03_other_estates | G | NO | – | – | – | 123.54 |
| gen_04_weather | G | NO | – | – | – | 133.07 |
| gen_05_politics | G | NO | – | – | skip | 60.55 |

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
  Answer: _(empty)_

**gen_02_religion** — *What religion were the Coolattin tenants?*  
  Refused: `False`  Verifier: `skip`  
  Answer: _(empty)_

**gen_03_other_estates** — *How did eviction rates at Coolattin compare to other Irish estates?*  
  Refused: `False`  Verifier: `None`  
  Answer: _(empty)_

**gen_04_weather** — *What was the weather like in County Wicklow during the 1840s?*  
  Refused: `False`  Verifier: `None`  
  Answer: _(empty)_

**gen_05_politics** — *Were any Coolattin tenants involved in political movements during the 1840s?*  
  Refused: `False`  Verifier: `skip`  
  Answer: _(empty)_

---

## 4. Findings

### B1 — Exec Accuracy of Live Fallback

For the **8 in-scope fallback cases**, the LLM generated SQL
that returned the oracle answer in **0.0%** of cases
(0/8 tested).

### B2 — Live Hallucination Rate

**None%** of answered cases (0/0)
contained at least one number in the prose answer that was not present in the
SQL result rows. This is the live hallucination rate (contrast with the offline
gate test in D10a).

### B3 — Cross-Verifier Catch Rate

The cross-verifier fired for **0** of the 0 answered cases.
Of the **0** hallucinated cases where the verifier ran,
it correctly flagged **0** (None%).
This is the live catch rate (previously unmeasured — gap noted in D10b).

### B4 — G-Series Honest Refusal (Live)

For the **8 out-of-scope G-series questions**, the LLM
refused to answer in **None%** of cases
(0/0).

_Generated by `scripts/eval_fallback_live.py --phase fallback_live`_