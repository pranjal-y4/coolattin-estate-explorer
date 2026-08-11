# RQ3 Part A — Pilot Results (current main, C2 "LLM-SQL", N=14)

Maps to Section 6.4. Produced by `eval_plan/scripts/rq3_pilot.py`, run 2026-08-03
against current `main` (`ASK_USE_NEW_PIPELINE=true` — the direct-LLM-SQL default
pipeline, confirmed earlier to have no fast lanes). Real LLM API calls; single pass, no
repeats. Raw output: `eval_plan/evidence/RQ3_pilot_raw_output.json` and
`RQ3_pilot_console_output.txt`. 14 questions, stratified across all 13 categories in
`eval/gold.csv`.

**This is a pilot, not the full 83-question golden set** — scoped down deliberately per
your decision to validate the harness and check cost/behaviour before deciding whether
to scale up. It already surfaced real findings worth acting on before a full run.

---

## Per-question scoring

| ID | Category | Gold | System answer | Verdict |
|---|---|---|---|---|
| cen_01_estate_1841 | census | 119,300 | 119,300 | **Correct** |
| cmp_01_emigration_vs_kg | comparative | SQLite=400 | 400 (both sides) | **Correct** |
| emi_01_total | emigration | 6,016 | 6,016 | **Correct** |
| er_01_exact_ballinacor | entity | townland id=355 resolved | Correctly resolved to the one BALLINACOR row, returned 12 census years | Plausible (different answer shape, not strictly scorable) |
| evic_01_total | eviction | 7,763 | **4,108** | **WRONG — see finding 1 below** |
| fbl_01_rent | fallback | N/A — no verified template | Answered with holding-acres data, but the rephrased answer explicitly flagged "returned average holding size rather than a rent figure" | Honest partial mismatch — see finding 2 |
| gen_01_mortality | general (out-of-scope) | N/A — not in DB | SQL deliberately `WHERE 1=0`, answer correctly states no famine-mortality data exists | **Correct honest-empty** |
| geo_01_total_townlands | geography | 4,225 | **516** | **Mismatch — see finding 3, likely the gold answer itself is questionable** |
| her_01_holy_well_population | heritage | comparison (no fixed target) | Structured comparison: 62 townlands with holy well vs 1,204 without | Plausible |
| ov_01_famine_impact | overview | multi-source sensemaking | **Refused entirely** — "I could not produce a validated SQL query" | Real finding — see finding 4 |
| ppl_01_total_records | people | 13,707 | 13,707 | **Correct** |
| rel_01_ballinacor_barony | relational | Arklow (SQLite) / Ballinacor South (KG) | Arklow (SQLite side only — KG side not separately surfaced) | Partially correct — SQLite side right, KG discrepancy not captured |
| ten_01_total | tenancy | 5,247 | 5,247 | **Correct** |
| cen_02_estate_1851 | census | 91,860 | 91,860 | **Correct** |

**7/14 clean numeric matches, 2/14 clear wrong answers, 1/14 total refusal, 4/14
plausible-but-not-strictly-scorable (entity/comparison/relational question shapes).**
Do not round this into a single accuracy percentage for the dissertation — N=14 is a
pilot, and several rows aren't scalar-comparable. Use it to decide scope, not to cite as
"answer accuracy."

---

## Finding 1 — evic_01_total is a real wrong answer, and it's informative

Gold answer **7,763**; system answered **4,108**. The generated SQL was:

```sql
SELECT COUNT(DISTINCT record_id) AS total_evictions FROM unified_record WHERE has_eviction_record = 1
```

4,108 matches exactly the `has_eviction_record=1` person-level flag count in
`unified_record`. The gold figure of 7,763 almost certainly comes from summing
`clearances_record.count` (the separate townland×year aggregate eviction-count table,
1,211 rows) instead of, or in addition to, the person-level flag. **This is a genuine
schema-ambiguity bug class**: "how many evictions" is answerable two different correct
ways from two different tables (person-level flagged records vs. townland-level annual
counts), and the LLM picked one without checking the other or flagging the ambiguity.
This is exactly the kind of execution-accuracy failure RQ3 is supposed to measure — a
real, reproducible wrong answer on the shipped default pipeline, worth including as a
named example in the write-up rather than only reporting an aggregate percentage.

## Finding 2 — fbl_01_rent shows honest self-correction, not a clean refusal

The gold set marks this "N/A — no verified template" (implying it should be an honest
refusal), but the pipeline instead ran a plausible-looking substitute query (average
`holding_acres`) *and* explicitly told the user in the rephrased answer that this is
holding size, not rent, because rent data isn't queryable the way the question implies.
This is a middle case the eval plan's binary correct-empty/false-empty framing doesn't
quite capture — worth a category of its own ("substituted-metric with disclosure") if
you see more of these in a fuller run.

## Finding 3 — geo_01_total_townlands's gold answer (4,225) is itself questionable

The system answered 516 (`COUNT(DISTINCT townland_norm) FROM unified_record`). Per the
main evidence doc's earlier finding, **4,225 is the size of the entire national
KG-sourced townland reference table**, not the Coolattin estate's own townland count —
only 152 of those 4,225 rows are actually referenced by estate records. Neither 516
(raw uncanonicalised name variants) nor 4,225 (the whole national table) is really "how
many townlands are in the estate" — the defensible answer is 152 (canonical townlands
matched to estate records). **Recommend correcting this gold_answer in `eval/gold.csv`
before using it in a full run**, rather than scoring the system against a questionable
target.

## Finding 4 — narrative/overview questions get a hard refusal, not a narrative answer

`ov_01_famine_impact` ("What was the impact of the Great Famine on the estate?") got a
flat refusal: *"I could not produce a validated SQL query that safely answers this
question."* This is a structural consequence of current `main`'s architecture: **every
question must resolve to SQL**, so broad sensemaking/narrative questions that the old
architecture would have routed to GraphRAG/subgraph retrieval (no SQL needed) now get
rejected outright on the default pipeline. This is a legitimate, reportable
architectural trade-off — current main trades narrative/overview capability for
SQL-grounded traceability everywhere else — but it means the "overview" and "heritage
narrative" categories in the golden set will likely fail systematically on a full run,
not occasionally. Worth deciding now whether that's a finding to report as-is or a gap
to fix before final evaluation.

## Finding 5 — the Grok (xAI) synthesis fallback is currently dead

Console output shows, on every question where the numeric-consistency gate flagged a
violation and the pipeline tried backup providers:

```
ask_service.grok_generate_failed model=grok-3-mini error=403 Client Error: Forbidden
ask_service.grok_generate_failed model=grok-3-mini-fast error=403 Client Error: Forbidden
ask_service.grok_generate_failed model=grok-beta error=400 Client Error: Bad Request
```

All three Grok model variants fail — two with `403 Forbidden` (likely an invalid/expired
`GROK_API_KEY` or account issue) and one with `400 Bad Request`. The documented
"Claude → Grok → OpenRouter/Ollama" synthesis cascade (`CLAUDE.md`) currently has a
**dead second tier** — every gate-triggered regeneration attempt burns time retrying
three doomed Grok calls before falling through. This is a real, live, currently-true
finding (checked 2026-08-03) worth citing directly as a §6.7.2 silent/degraded-failure
case study: the system still produces an answer (Claude's original or a later fallback
covers it), so the failure doesn't surface to the user, but it's real latency cost and
real infrastructure decay happening on every gate-triggered regeneration. Recommend
checking the `GROK_API_KEY` / xAI account status before citing the cascade as
three-tier-functional anywhere in the dissertation.

## Finding 6 — the numeric-consistency gate is confirmed live and firing

`numeric_gate_fallback violations=[...]` appeared for 4 of the 14 questions, each
triggering a regeneration/backup-provider attempt. This is a real, positive
confirmation that the documented gate mechanism (§5.6.3 of the chapter draft) is
actually active and functioning on current main, not just described in code — useful
corroborating evidence for the "loud failure" / honesty claims in §6.7.2 and §7.4.

---

## Cost/time observed

14 questions, single pass: **~4.5 minutes wall time** (per-question latency ranged
6.9s–68.1s, driven mostly by gate-triggered regeneration attempts burning through the
dead Grok cascade). Extrapolating linearly, the full 83-question golden set would be
roughly **~30-45 minutes single-pass**, and the eval plan's "x3 repeats at temp 0" for
nondeterminism reporting would roughly triple that — call it **1.5-2.5 hours** of real
wall time and a proportional number of paid Claude API calls if you want the full
protocol as specified. Held-out set adds another 35 questions on top.

---

## Recommendation before scaling up

1. **Fix or drop `geo_01_total_townlands`'s gold answer** (finding 3) before a full run
   — otherwise you'll score a correct-ish system answer as wrong.
2. **Check the Grok/xAI API key** (finding 5) — cheap to fix, and currently wastes time
   on every gate-triggered regeneration across the whole golden set.
3. **Decide how to treat narrative/overview questions** (finding 4) before running the
   full set — if current main structurally cannot answer them, running all of
   `eval/gold.csv`'s overview/heritage-narrative rows will produce a cluster of
   refusals that's a real finding, but you should decide in advance whether that's
   "correctly honest refusal on out-of-scope-shaped questions" or "a coverage gap,"
   since that framing changes how §6.4 reads.
4. Once those are addressed (or deliberately left as-is and reported honestly), scaling
   to the full 83 + 35 held-out is straightforward — same harness, just widen
   `pick_pilot_rows()`'s `n` or remove the stratified cap entirely.

I have not built the C1 (legacy pipeline) or C3 (RAG baseline) arms yet, per the
decision to validate C2 first — say when you want those built.
