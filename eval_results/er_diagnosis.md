# Entity Resolution Diagnosis — Coolattin Workhouse ↔ Estate Records

**Date:** 2026-06-10  
**Pipeline version:** post-fix (forename mismatch penalty added to scoring.py)  
**Status before fix:** 0 confirmed links, 0 possible links  
**Status after fix:** 3 confirmed, 136 possible, 174 LINKED_TO graph edges

---

## 1. Stage-by-stage instrumented counts

| Stage | Count | Notes |
|---|---|---|
| Workhouse rows loaded | 8,214 | Sheet "1-127": 3,920 · Sheet "from 128": 4,294 |
| Mentions with `normalised_place` | 4,293 | All from sheet "from 128" (has Electoral Division) |
| Mentions with `event_year` | 2,652 | Sheet 2 subset with parsed admission/discharge date |
| Mentions with BOTH place AND year | 2,652 | Best-corroborated cohort |
| Mentions with NEITHER place NOR year | 3,921 | Entire sheet "1-127" — structurally no metadata |
| Unified records loaded | 13,707 | |
| Distinct workhouse Electoral Divisions | 783 | |
| Distinct unified normalised places | 514 | Mostly townland names |
| Exact ED ↔ townland name overlaps | 28 | e.g. AGHOLD, COOLATTIN, KILLINURE, COOLBOY |
| Candidates with score ≥ 0.60 before fix | 252 | (83 POSSIBLE, 3 CONFIRMED — simulation; not persisted) |
| Candidates with score ≥ 0.60 after fix | 139 | 136 POSSIBLE + 3 CONFIRMED — persisted |

### Where the count collapsed to zero (pre-fix)

The collapse was at the **scoring stage**, not candidate generation.

`generate_candidates()` returns non-empty lists for the majority of mentions — blocking works. The fix was at scoring:

1. **Sheet "1-127" (3,920 rows, 48%) has no metadata** — no place, no year, no age, no occupation. Maximum achievable score: name (25 pts) + surname (15 pts) = 0.40 → WEAK_CANDIDATE. These records are **structurally unmatchable** at any reasonable threshold.

2. **Electoral Division ≠ townland name** — only 28 of 783 EDs share an exact normalised string with a unified place name. Place scoring (up to 20 pts) therefore contributes 0 points for ~96% of candidate pairs. This pushes most true matches below the CONFIRMED threshold (0.75) and into POSSIBLE (0.60–0.74).

3. **No forename mismatch penalty** — before the fix, "Healy Peter" vs "Mary-Anne Healy" at Killinure with compatible birth years scored 0.66 (POSSIBLE_MATCH). The scoring had no mechanism to penalise a wrong forename, so entire family clusters with a shared surname, shared townland, and compatible birth years inflated the POSSIBLE count with false positives. This caused the pre-fix gold-set evaluation to show P=0.48 at the 0.60 threshold.

---

## 2. Normalisation symmetry — CONFIRMED SYMMETRIC

Both sides use identical normalisation from `backend/services/entity_resolution/normalise.py`:

| Step | Workhouse | Unified | Symmetric? |
|---|---|---|---|
| Unicode NFKD + diacritic strip | `normalise_text()` | `normalise_text()` | ✓ |
| Forename abbreviation expansion (Jno→John, Wm→William, Pat→Patrick) | `_expand_forename()` | `_expand_forename()` | ✓ |
| Surname Mc/Mac/O variant collapse | `_normalise_surname()` | `_normalise_surname()` | ✓ |
| Phonetic encoding (jellyfish.metaphone) | same function | same function | ✓ |
| Place normalisation | `normalise_place_name()` | `normalise_place_name()` | ✓ |
| surname_first flag | `True` (Surname Forename) | `False` (Forename Surname) | ✓ applied correctly |

No asymmetric normalisation was found. The prior zero-link result was **not** caused by a normalisation bug.

---

## 3. Place-first: Electoral Division → Townland

The workhouse register records only **Electoral Division** (e.g. "Killinure", "Carnew", "Coolkenna"). Estate records use **townland names** (more granular). Where the ED name equals a townland name the exact place bonus (20 pts) fires; in all other cases place contributes 0 pts.

**Design choice maintained:** place is a scoring signal, not a hard filter. `generate_candidates()` blocks on name/phonetic/fuzzy/year criteria and will include records regardless of place match. Compound EDs like "Carnew Kilcavan" correctly trigger the substring place bonus (12 pts) when the contained townland "Kilcavan" is found in the unified record.

**Parish/barony overlap** (future work): several workhouse EDs are civil parish names. Mapping ED → parish for scoring would improve recall for records at places like Aghold (ED) → Aghowle (unified estate townland in the same parish).

---

## 4. Gold set CL-ID correction

The pre-existing `eval/er_gold.csv` had **incorrect `u_record_id` values** — the CL IDs existed in the database but pointed to different people (e.g. `CL10148` → "John Byrne / Ballynultagh", not "John Bryan / Killinure" as intended). All 35 pairs have been corrected to the actual CL IDs found by name + townland + year lookup.

---

## 5. Fix applied — forename mismatch penalty

**File:** `backend/services/entity_resolution/scoring.py`

After the surname scoring block, a forename compatibility check was added:

> If both mention and candidate have non-empty forenames AND `fuzz.token_sort_ratio(forename_A, forename_B) < 60%`, subtract 15 points from `raw_points` and record a `"Forename mismatch"` conflict.

**Rationale:** The 15-pt deduction equals the surname-match bonus, preventing a same-surname, same-place match with a completely wrong forename from reaching POSSIBLE_MATCH on the strength of surname + place + birth-year alone. The 60% threshold preserves valid forename variants (Judy/Judith ≈ 73%, Pat/Patrick = 100% after abbreviation expansion) while rejecting clear mismatches (Peter/Mary-Anne ≈ 14%, John/James ≈ 22%).

---

## 6. Edward Dagg / Aghowle 1853 — note

The task specification named "Edward Dagg / Aghowle / Dunbrody 1853" as a proposed positive gold example. Edward Dagg appears in unified estate records (CL8037: Aghowle, 1853) but **does not appear in the workhouse register** (no row with "Dagg" in `workhouse_data_final.xlsx`). The Dunbrody emigration route is separate from the workhouse intake. This example cannot form a workhouse-to-estate link and was therefore excluded from the gold set.

---

## 7. Remaining data limitations (legitimate RQ3 findings)

1. **Sheet "1-127" (3,920 rows, 48%):** Only name + register number; no ED, no date, no age. Unmatchable at any threshold without external corroboration.

2. **Electoral Division granularity:** Wicklow EDs encompass 3–15 townlands each. Without townland-level data in the workhouse register, geographic corroboration is limited to cases where the ED name coincides with a townland name or is a substring of one.

3. **Missing age on unified side:** Many estate records lack age, preventing birth-year triangulation that would strengthen matches.

These are **data limitations of the primary sources**, not pipeline defects.
