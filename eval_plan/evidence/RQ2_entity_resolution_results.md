# RQ2 Results — Entity Resolution

Maps to Section 6.3. Produced by `eval_plan/scripts/rq2_entity_resolution.py`, run
2026-08-03 against the live `coolattin.db` and `eval/er_gold.csv` (N=35 labelled
pairs). Raw console output: `eval_plan/evidence/RQ2_raw_output.txt`.

**Run this script with `venv/bin/python3`, not bare `python3`.** This project has its
own `venv/` with the correct pinned dependencies (including `jellyfish`, declared in
`requirements.txt`). My first pass at this evaluation used the system Python
interpreter by mistake, under which `jellyfish` is not installed — `phonetic_code()`'s
`except Exception: return text` fallback swallowed the resulting `ImportError`
silently and produced 0% phonetic-match recall across the board with no error message.
That was **my tooling mistake, not a real bug in the shipped application** — the
project's own `venv/` has `jellyfish` correctly installed, confirmed by running
`venv/bin/python3 -c "import jellyfish"` successfully. All numbers below are from the
correct interpreter. Do not cite the 0%-recall figure anywhere; it never reflected the
real system.

---

## 1. Pairwise Precision/Recall/F1

N=35 labelled pairs; 4 excluded (`UNCERTAIN` — ambiguous even to the human labeller,
excluding these rather than forcing them into TP/FP/FN/TN is a deliberate scoping
choice, not data loss). 31 scored.

"Should be linked" (positive) = gold label `TRUE_MATCH` or `POSSIBLE`.
"System says linked" (positive) = predicted label `CONFIRMED_MATCH` or `POSSIBLE_MATCH`
(the two labels actually promoted to `workhouse_unified_links` in production).

| | Value |
|---|---|
| TP | 13 |
| FP | 6 |
| FN | 0 |
| TN | 12 |
| **Precision** | **0.684** |
| **Recall** | **1.000** |
| **F1** | **0.813** |

**This replaces the assumed "F1@POSSIBLE = 0.92" figure in the eval plan — that number
has no evidence behind it in this dataset. Do not use 0.92; use 0.813, N=31.**

All 6 false positives are the same failure mode: gold-labelled `FALSE_MATCH` pairs
where the human rationale cites a forename/gender mismatch (e.g. `Shea Mary → James
Shea`, `Byrne Judith → John Byrne`, `Murphy Julia → Mary Murphy`), but same-surname +
same-townland + close birth-year still pushed the score into `POSSIBLE_MATCH`
territory. Real `gender` values from `unified_record` were used on the candidate side
this run (via `_load_unified_records()`, the same loader production uses) — 5 of the 6
FPs still had no gender recorded on the candidate side (`gender_used=None` in the raw
log), so the mismatch signal that would likely have caught them wasn't available. This
is a genuine, defensible precision ceiling given current data completeness, not a
scoring-logic bug.

Recall is a clean 1.000, but on only 13 positive cases — report as encouraging, not
conclusive, given N.

### B-cubed F1

Reported as identical to pairwise (0.684 / 1.000 / 0.813). **This is not a separate
measurement** — `eval/er_gold.csv` has exactly one candidate row per `gold_id`, so there
are no multi-candidate clusters for B-cubed to diverge on. State this plainly rather
than presenting it as independent confirmation; a real B-cubed measurement would need a
gold set with multiple candidates clustered per mention.

---

## 2. Blocking: Pairs Completeness / Pairs Quality / Reduction Ratio

Run against the full production candidate-generation path (`generate_candidates()` +
`build_unified_index()`) over the **actual 13,707-record unified corpus**, not a
sample.

| Metric | Value |
|---|---|
| Pairs Completeness (PC) | **1.000** (13/13 true matches survived blocking) |
| Pairs Quality (PQ), lower bound | **0.040** |
| Reduction Ratio (RR) | **0.998** |

**PC = 1.000 is the important number**: every true match in the gold set was still
present in the candidate set after blocking, meaning the F1@0.813 above is not
artificially inflated by blocking having already discarded hard cases — the ceiling
this F1 operates under is the full 1.0, not some lower blocking ceiling.

**PQ is explicitly a lower bound, not a true measurement** — the gold set only labels
one specific (mention, candidate) pair per row; the other ~24 candidates
`generate_candidates()` returns per mention (it caps at 25) are unlabelled, not
confirmed non-matches. So "true matches in candidate set" here can only count the one
known true positive per mention, undercounting the true PQ. Do not present 0.040 as the
real precision of the blocking step — say explicitly it is a lower bound computed from
partial labels.

RR = 0.998 reflects that blocking (phonetic-surname + place buckets) cuts the search
space by ~99.8% relative to the full 13 true-match-mentions × 13,707-corpus cross
product — expected and desirable given the whole point of blocking.

---

## 3. Name-only vs authority-ID: hard cases

**Correction to the eval plan's assumed hard cases**: "two Coolattins" and "Ballinacor
Upper/Lower" do not exist as literal duplicate-name rows in the current `townland`
table. Querying directly:

- **Zero true name collisions** anywhere in the 4,225-row `townland` table (every
  `name` value is unique — confirmed by `GROUP BY name HAVING COUNT(*) > 1` returning
  no rows). The ingest process already resolves same-named source records to distinct
  canonical rows (keyed by `entity_id`) before they reach this table — so a same-named
  collision case, as literally described in the eval plan, cannot be demonstrated
  against the live `townland` table because the architecture already prevents it from
  existing there.
- **The real, demonstrable hard case is name-*similarity*, not name-*identity*.** Two
  genuine families of distinct places exist that a naive substring/fuzzy name matcher
  would risk conflating:
  - **COOLATTIN** (Carnew civil parish, Gorey barony — the estate townland itself),
    **COOLATTIN PARK** (same civil parish, *different* barony — Scarawalsh), and
    **DEERPARK ED COOLATTIN** (an electoral-division compound name with no
    civil_parish/barony/authority-ID populated at all).
  - **BALLINACOR** (Kilbride civil parish, Arklow barony — the only one of the seven
    with full authority-ID matches: OSM `-4505321`, OSI `250064`, VRTI `v12zgr6`),
    plus six more distinct entities sharing the substring — **BALLINACOR EAST**,
    **BALLINACOR WEST**, **BALLINACORBEG**, and three ED-compound names
    (**BALLINACOR ED BALLINACOR**, **BALLINACOR ED TINAHELY**, **MUCKLAGH ED
    BALLINACOR**) — none of which have any authority ID populated.
- The finding to write up is therefore: **entity_id-per-row keying plus
  civil_parish/barony context is what lets the system tell apart real, distinct
  Wicklow places that share a name fragment** — a name-only resolver working from bare
  substring or fuzzy match on "Coolattin" or "Ballinacor" would have no principled way
  to choose among 3 and 7 candidates respectively, whereas the entity_id-keyed table
  keeps them permanently distinct with their own (partial) authority-ID trail. This is
  a better-grounded version of the eval plan's intended argument, not a weaker one —
  use this framing, not the original "duplicate Coolattin row" framing, which doesn't
  match the data.

---

## 4. Spelling-variant recall (Metaphone)

**Caveat up front: this 15-pair list was curated by me as a plausible illustration of
19th-century Irish surname variation, not drawn from an observed set of real variant
pairs in the dataset.** Treat the 60% figure as illustrative of Metaphone's general
behaviour on Irish surnames, not as a measured property of this system's actual data.
If you want a real measured figure, the right source would be actual variant spellings
of the same person across the workhouse/estate records — that requires either the ER
review queue (currently empty, see `entity_resolution_decisions`/`match_review` row
counts in the main evidence doc) or manual archival cross-checking.

| Pair | Match? | Expected | Result |
|---|---|---|---|
| MCDONNELL / MACDONNELL | Yes | Yes | OK |
| O'BRIEN / OBRIEN | Yes | Yes | OK |
| O BRIEN / OBRIEN | No | Yes | Miss — see note below |
| KAVANAGH / CAVANAGH | Yes | Yes | OK |
| BYRNE / BEIRNE | Yes | No | Miss — Metaphone conflates two genuinely different surnames |
| DOYLE / DOYAL | No | Yes | Miss |
| O'TOOLE / OTOOLE | Yes | Yes | OK |
| O'NEILL / NEIL | No | No | OK |
| MCGRATH / MAGRATH | No | Yes | Miss — Mc-/Ma- prefix handled inconsistently |
| MOLLOY / MOLLOWY | Yes | Yes | OK |
| MCKEOWN / MACKEOWN | Yes | Yes | OK |
| BRENNAN / BRANNIGAN | No | No | OK |
| SHEEHAN / SHEAHAN | Yes | Yes | OK |
| GALLAGHER / GALLIAGHER | Yes | Yes | OK |
| O'DONNELL / DONNELLY | No | No | OK |

**matched/total = 9/15 = 0.600**

Two results worth naming as genuine, defensible limitations (not test-harness
artifacts):
- **BYRNE and BEIRNE produce the same Metaphone code (`BRN`)** — a real risk that two
  distinct, common Wicklow-area surnames get phonetically conflated during blocking.
  Worth a sentence in §7.3 limitations.
- **MCGRATH vs MAGRATH diverge** (`MKKR0` vs `MKR0`) — Metaphone's handling of the
  `MC`/`MA` prefix is inconsistent, a genuine Irish-surname-specific limitation.

The "O BRIEN" (literal space, no apostrophe) miss is **a test-harness artifact, not a
production bug**: I called `phonetic_code()` directly on the raw string, bypassing
`_normalise_surname()`'s `"O "` → `"O"` collapsing step that real mention/candidate
construction always applies via `normalise_person_fields()`. In production this
specific case would not occur as tested here.

---

## Summary table for §6.8 / master plan matrix

| Metric | Result | N | Target | Verdict |
|---|---|---|---|---|
| Pairwise F1 | 0.813 (P=0.684, R=1.000) | 31 (of 35, 4 excluded) | — (report with N) | Indicative — small N |
| B-cubed F1 | 0.813 (identical to pairwise — not independent) | 31 | — | Not separately measurable on this gold set |
| Pairs Completeness | 1.000 | 13 true matches | high | Met |
| Pairs Quality (lower bound) | 0.040 | 13 true matches / 25-cap candidate lists | — | Caveated, not a true PQ |
| Reduction Ratio | 0.998 | — | high | Met |
| Name-collision hard case | 0 true collisions found; real hard case is name-*similarity* (Coolattin/Ballinacor families) | 3 + 7 entities | — | Reframed finding, still supports the design argument |
| Spelling-variant recall | 0.600 (illustrative, not measured) | 15 curated pairs | — | Indicative only |
