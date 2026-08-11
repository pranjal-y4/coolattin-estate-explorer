# Evaluation Spec — RQ2: Entity Resolution

Maps to: Section 6.3. Shared metric definitions in `EVAL_00_master_plan.md`.

## 1. What this RQ claims
Authority-ID keying resolves entities more safely than name normalization for
non-unique historical place names, and the resolver achieves defensible
precision and recall within its candidate set.

## 2. Metrics

| Metric | Formula | Target band | Ground truth |
|--------|---------|-------------|--------------|
| Pairwise precision | TP / (TP + FP) | report with N | labelled pairs |
| Pairwise recall | TP / (TP + FN) | report with N | labelled pairs |
| Pairwise F1 | 2·P·R / (P + R) | report with N | labelled pairs |
| F1 @POSSIBLE | pairwise F1 within post-blocking candidate set | your 0.92 figure | labelled pairs after blocking |
| B-cubed F1 | per-entity P/R averaged | report with N | labelled clusters |
| Pairs Completeness (PC) | true matches after blocking / all true matches | high; this is the ceiling | labelled pairs |
| Pairs Quality (PQ) | true matches in candidates / candidate pairs | higher is cleaner | labelled pairs |
| Reduction Ratio (RR) | 1 - (candidates / all pairs) | high means efficient blocking | pair counts |
| Name vs ID delta | F1(authority-ID) - F1(name-based) | positive is the finding | same labelled set |
| Hard-case accuracy | correct resolutions / hard cases | report each case | curated probes |
| Spelling-variant recall | variants correctly matched / variant pairs | isolates Metaphone limit | curated Irish-name pairs |

## 3. Dataset required
- **Labelled candidate pairs**: N pairs each marked match / non-match, with a
  note on how the label was decided. This is the usual missing piece; build it
  first. State N clearly.
- **Hard-case probes**: the two "Coolattin" records, Ballinacor Upper/Lower, and
  any other non-unique name you know of, with their true identities.
- **Spelling-variant pairs**: 10 to 20 Irish-language name variant pairs, both
  ones Metaphone should catch and ones it will miss.

## 4. Procedure
1. Run `er_eval.py`: load the labelled pairs, run both the name-based resolver
   and the authority-ID resolver on the same set, emit two confusion matrices.
2. Compute pairwise P/R/F1 and B-cubed for each resolver.
3. Compute PC, PQ, RR for the blocking step; PC explains the `@POSSIBLE` ceiling.
4. Run the hard-case probes; record how each resolver keyed each record.
5. Run the spelling-variant set; record matches and misses.

## 5. Results tables to fill

Main comparison:

| Resolver | Precision | Recall | F1 | B-cubed F1 |
|----------|-----------|--------|-----|-----------|
| Name-based | | | | |
| Authority-ID | | | | |

Delta: F1(authority-ID) - F1(name-based) = __.

Blocking:

| Metric | Value |
|--------|-------|
| Pairs Completeness | |
| Pairs Quality | |
| Reduction Ratio | |

Hard cases:

| Case | Name-based result | Authority-ID result | Correct? |
|------|-------------------|---------------------|----------|
| Two Coolattins | | | |
| Ballinacor Upper/Lower | | | |

Spelling variants: matched / total = __ / __. List the misses.

## 6. Rating and interpretation
- The headline is the delta, not the absolute F1. A positive delta with the
  Coolattin case showing a name-based merge and an authority-ID separation is
  the direct evidence for the design choice.
- Report `@POSSIBLE` with its PC so a reader understands the F1 is conditional on
  what blocking retained. A high F1 with low PC means good decisions on a narrow
  candidate set, which is exactly the "ER LIMITED, data coverage" caveat.

## 7. Honest-reporting notes
State N for every metric. If the labelled set is small, call the F1 indicative.
The Metaphone misses on Irish-language variants belong here as concrete examples,
not as a footnote; they become a limitation in Section 7.3.
