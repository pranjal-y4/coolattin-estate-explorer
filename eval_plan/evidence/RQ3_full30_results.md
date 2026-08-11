# RQ3 Part A — Full 30-Question Run (current main, C2 "LLM-SQL")

Maps to Section 6.4. Produced by `eval_plan/scripts/rq3_full30.py`, run 2026-08-03
against current `main` (`ASK_USE_NEW_PIPELINE=true`). Real LLM API calls, single pass,
no repeats. 30 questions, stratified round-robin across all 13 categories in
`eval/gold.csv` (which now has `geo_01_total_townlands`'s gold answer corrected from
4,225 to 152 — see `RQ2`/`RQ1` evidence for why). Raw output:
`eval_plan/evidence/RQ3_full30_raw_output.json` and `RQ3_full30_console_output.txt`.

**Read the scoring-methodology note below before citing any accuracy percentage.**

---

## Scoring methodology correction (important — read before citing a number)

The harness's automated scorer (`rq3_full30.py::score()`) does literal substring
matching of the `gold_answer` field against the answer text. This works for plain
numeric gold answers (`"7763"`) but **breaks on compound gold answers** like
`"SQLite=55"` or `"Arklow (SQLite) / Ballinacor South (KG)"` — the literal string
`"SQLite=55"` never appears verbatim in a real answer, even when the actual number
(55) is right there. The raw script output reports **12 correct / 6 wrong / 18
scalar-scorable = 66.7% accuracy**. That number is wrong. I hand-checked all 6 "wrong"
rows against the actual answer text:

| ID | Gold | Automated verdict | Hand-verified verdict |
|---|---|---|---|
| `rel_01_ballinacor_barony` | Arklow (SQLite) / Ballinacor South (KG) | wrong | **Actually correct** — answer states barony=Arklow, matching the SQLite half of the gold answer exactly. Scorer never checked the KG half was even attempted (it wasn't — see below). |
| `cmp_02_population_vs_kg` | SQLite=55 | wrong | **Actually correct** — answer states "total population=55" verbatim. |
| `cmp_03_eviction_agree` | SQLite=122 | wrong | **Actually correct** — answer states "(122)" for the SQLite-derived side (see mislabeling note below). |
| `evic_01_total` | 7763 | wrong | **Genuinely wrong** — see Finding 1. |
| `geo_01_total_townlands` | 152 | wrong | **Genuinely wrong** — see Finding 2. |
| `cmp_01_emigration_vs_kg` | SQLite=400 | wrong | **Partially wrong / nondeterministic** — see Finding 3. |

**Corrected tally: 15/18 scalar-scorable questions numerically correct (83.3%), 2
genuinely wrong (11.1%), 1 partially-broken/nondeterministic (5.6%).** Use 83.3%, not
66.7%, and cite this methodology note alongside it — don't present either number as a
bare percentage without this explanation, per the master eval plan's own instruction not
to report descriptive automation as if it were a validated result.

---

## Finding 1 — evic_01_total is a reproducible, systematic wrong answer

Confirmed identically in both the 14-question pilot and this 30-question run: gold
**7,763**, system consistently answers **4,108**
(`SELECT COUNT(DISTINCT record_id) FROM unified_record WHERE has_eviction_record=1`).
As established in the pilot write-up, the gold figure almost certainly sums
`clearances_record.count` (the townland×year aggregate table) rather than counting
person-level flagged records. **This is not a one-off — it reproduces every time**,
because the LLM consistently reaches for the same (wrong-for-this-question) table. This
is real, citable execution-accuracy evidence for §6.4.

## Finding 2 — geo_01_total_townlands: a genuine failure that directly evidences RQ2's argument

Gold (corrected) **152**; system answers **516**
(`SELECT COUNT(DISTINCT townland_norm) FROM unified_record`). This is the single most
useful cross-reference finding in this run: **516 is the count of raw, uncanonicalised
`townland_norm` string values** — spelling variants of the same real place that haven't
been resolved to a single canonical entity. 152 is the count of *canonical* townlands
(matching `townlands.json`'s 152 features, and the `townland` table's
entity_id-keyed rows once scoped to the estate). The system's wrong answer is a direct,
concrete illustration of exactly the problem RQ2 argues authority-ID keying solves —
when the LLM writes ad hoc SQL against raw text columns, it inherits the
name-collision/variant-proliferation problem the `townland`/`townland_xref` schema was
built to solve, because `_generate_sql()` has no way to route through that resolution
layer. **Use this as a direct, single concrete example connecting RQ2's design argument
to a measured RQ3 failure** — this is a stronger, more specific version of the
"generalizable design knowledge" claim in §7.2 than an abstract statement would be.

## Finding 3 — cmp_01_emigration_vs_kg: identical question, two different (one broken) SQL queries — real nondeterminism evidence

The **same question**, asked in both the pilot and this run:

- **Pilot run**: `UNION ALL` query correctly computing both sides — Estate Records
  (400) and a genuine KG-join count, both correctly showing 400/400.
- **This run**: a different, broken query — `CASE WHEN t.kg_uri IS NOT NULL THEN NULL
  ELSE 0 END AS emigration_count`, which computes nothing meaningful for the KG side
  (always NULL when a `kg_uri` exists) and only surfaces "Estate Records (400)" in the
  final answer, silently dropping the comparison the question asked for.

The SQLite-side number (400) is right both times, but the *comparison itself* — the
actual point of the question — only worked once out of two attempts. **This is direct,
concrete evidence of real run-to-run nondeterminism in the LLM-SQL generation step**,
exactly the concern the eval plan's §2 "Nondeterminism" section anticipates (temperature
isn't pinned to 0 in the current default config, or if it is, the model still varies
query structure across calls). This is exactly the kind of paired-comparison finding the
plan's Cochran's Q / McNemar protocol is designed to detect at scale — worth flagging as
a strong argument for actually running the x3-repeat protocol before finalizing any
RQ3 accuracy figure, since a single pass can silently get lucky or unlucky on
comparison-shaped questions.

## Finding 4 — honest-refusal behaviour is inconsistent on out-of-scope questions

Three `is_out_of_scope=Y` questions appeared in this run:

| ID | Question | Gold | Behaviour |
|---|---|---|---|
| `gen_01_mortality` | Famine-related deaths | N/A — not in DB | **Correct**: SQL deliberately `WHERE 1=0`, answer states no such data exists |
| `fbl_02_crops` | Crops grown in the 1840s | N/A — not in DB | **Substituted**: ran a real query pulling `occupation`/`household_list` for 1840s Coolattin records, returned "112 matching rows" as if responsive |
| `gen_02_religion` | Tenant religion | N/A — not in DB | **Substituted**: ran a real tenancy query, returned "17 matching rows" as if responsive |

**2 of 3 out-of-scope questions got a tangentially-related real answer instead of an
honest refusal.** This is a softer failure mode than hallucinating a specific false
number — the rows returned are real — but it directly contradicts the "honest
refusal" claim in the checklist and eval plan: a user asking about 1840s crops gets
back "112 matching rows" of occupation/household data with no signal that the question
itself (crop types) was never actually answered. **This 1-in-3 correct-empty rate on
out-of-scope questions (N=3, too small to generalize) is a concrete, measured gap worth
a full-scale run to confirm** — if it holds at N=30+ out-of-scope questions, "the system
refuses honestly when no data exists" needs qualifying, not stating flatly.

## Finding 5 — spelling-variant resolution: one clean pass, one truncation bug, one inconclusive

Two entity-resolution/spelling-variant questions:

- `er_03_spelling_ballynultach` ("Ballynultach" → should resolve to BALLYNULTAGH): the
  LLM correctly wrote `townland_norm = 'BALLYNULTAGH'` (successfully normalising the
  misspelled input) and returned "200 matching rows." **But the SQL had `LIMIT 200`**,
  and `emi_02` in this same run independently confirms Ballynultagh has exactly 400
  emigration records — so this answer is silently truncated to half the true count.
  Worth flagging: `LIMIT` clauses on aggregate-shaped questions (this one implicitly
  asks "how many," even though phrased as "emigration from X") are a real, distinct
  failure mode from wrong-table errors.
- `er_02_spelling_variant` ("Ballinacour" → should resolve to BALLINACOR id=355): the
  LLM sidestepped the misspelling entirely with `townland_norm LIKE 'BALLINACOR%'`
  (a wildcard, not an actual normalisation of "Ballinacour"), returning 0. Inconclusive
  whether 0 is correct (no emigration records exist for any Ballinacor-family
  townland) or wrong — I did not independently verify. Worth checking directly against
  `unified_record` if you want a clean verdict.

## Finding 6 — cmp_03's mislabeling: a real traceability/description error, separate from numeric correctness

The generated SQL for `cmp_03_eviction_agree` labels `clearances_record` as
`'clearances_record (knowledge graph)'` — but `clearances_record` is populated from
`townlands.json` (a local GeoJSON file), **not from a live knowledge-graph query**, per
the RQ1 evidence doc's ingestion-completeness findings. The number returned (122) is
correct, but the system's own description of *where that number came from* is wrong.
This matters directly for §6.4's traceability claim: a correct number with an
incorrect provenance label is a real defect in "grounded, traceable" answering, even
though it wouldn't show up in a numeric-accuracy metric at all. Worth citing alongside
Finding 3 as a second example of comparative-question fragility.

## Finding 7 — the Grok/xAI outage persists

Identical failure signature to the pilot, confirmed again on 2026-08-03 (4 separate
gate-triggered retry attempts in this run, all three Grok model variants failing the
same way). Not re-explained here — see the pilot results doc, Finding 5. Still
unresolved; still worth fixing before any latency/cost figures are finalized, since
every gate-triggered regeneration burns time on three doomed calls first.

---

## Summary table for §6.8 / master plan matrix

| Metric | Result | N | Method | Verdict |
|---|---|---|---|---|
| Scalar answer accuracy (hand-corrected) | 83.3% (15/18) | 18 scalar-scorable of 30 | manual verification after automated scorer proved unreliable on compound answers | Indicative — single pass, N=30 total |
| Scalar answer accuracy (naive automated) | 66.7% (12/18) | 18 | **Do not cite** — scorer artifact on compound gold answers | N/A |
| Reproducible wrong-answer bugs found | 2 (evic_01 eviction-table ambiguity, geo_01 canonical-vs-raw townland count) | — | confirmed across both pilot and full30 runs | Real findings for §6.4/§7.3 |
| Nondeterminism evidence | 1 clean example (cmp_01, same question, two different SQL, one broken) | — | cross-run comparison, pilot vs full30 | Supports running the x3-repeat protocol before finalizing |
| Correct-empty rate (out-of-scope) | 1/3 (33%) | 3 | manual check | Too small to generalize — needs a larger out-of-scope-only run |
| LLM-authored-SQL count (common path) | 30/30 (100%) | 30 | every question generated via `_generate_sql()`, confirmed by `strategy=llm_sql_direct` in every non-refused row | **Contradicts the checklist's "target: 0" — this is expected and already flagged in the main evidence doc's §0 pipeline-drift finding, not new** |
