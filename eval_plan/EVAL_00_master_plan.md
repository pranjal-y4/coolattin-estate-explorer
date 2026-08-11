# Evaluation Master Plan

One file per research question defines its evaluation. This master defines the
shared metrics used across those files, the statistical approach, and the final
summary matrix. Define a metric here once, reference it from the RQ files.

Principle: report what the system does, per query type, with a stated target
band and a source of ground truth for every number. A gap is a finding, not a
failure. Never present descriptive statistics (graph size, row counts) as if
they were evaluation results.

---

## 1. Shared metric definitions

### Correctness and retrieval
- **Answer accuracy** = correct answers / total. For aggregation and numeric
  answers use numeric exact match (allow a stated rounding tolerance). For
  lookups use exact match, or token/entity F1 when the answer is a short span.
- **Precision** = TP / (TP + FP). **Recall** = TP / (TP + FN).
  **F1** = 2·P·R / (P + R).

### Grounded QA (RAG-style, defined library-independently)
- **Faithfulness (groundedness)** = supported answer claims / total answer
  claims. Decompose each answer into atomic claims, mark each as supported or
  not by the retrieved evidence.
- **Hallucination rate** = answers with at least one unsupported claim / total
  answers. Report at answer level; faithfulness is the finer-grained view.
- **Answer relevance** = does the answer address the question. Score on a
  three-point rubric (fully / partially / not), report the fully-relevant rate.
- **Context precision** = relevant retrieved chunks / retrieved chunks.
  **Context recall** = relevant chunks retrieved / gold-relevant chunks.
  These need gold-labelled relevant chunks, so treat them as secondary and run
  only if labelling is feasible on a sample.

### Text-to-SQL
- **Execution accuracy (EX)** = queries where executing the generated SQL yields
  the correct result set (order-insensitive set comparison) / total. This is the
  standard metric; prefer it over SQL string match.
- **Valid-SQL rate** = generated SQL that parses and executes without error /
  total.
- **LLM-authored-SQL count** = number of times the model wrote SQL on the path
  under test. For the shipped common path the target is 0.

### Honest refusal
- **Correct-empty rate** = on queries whose correct answer is "no records," the
  fraction where the system returns empty rather than inventing an answer.
- **False-empty rate** = on queries where data exists, the fraction where the
  system wrongly returns empty. Both matter; report as a pair.

### Entity resolution
- **Pairwise P/R/F1** over labelled candidate pairs.
- **B-cubed P/R/F1** for cluster quality, computed per entity then averaged.
  This is more interpretable than pairwise when records cluster into entities.
- **Blocking metrics**: Pairs Completeness (PC) = true matches surviving
  blocking / all true matches; Pairs Quality (PQ) = true matches in candidate
  set / candidate pairs; Reduction Ratio (RR) = 1 - (candidate pairs / all
  possible pairs). PC is the ceiling that explains any `@POSSIBLE` figure.

### Data quality (RQ1)
- **Reproducibility rate** = tables with identical content checksum across
  independent rebuilds / total tables. Target 100% for a deterministic build.
- **Ingestion completeness** = rows loaded / rows in raw source, per source.
- **Alignment coverage** = entities matched to an authority ID / total.
- **Alignment precision** = correct matches / matches checked on a sample.

### Usability (RQ5)
- **SUS score** (0-100): odd items score (value - 1), even items score
  (5 - value), sum the ten, multiply by 2.5. Report the mean with N.
- **Task success rate** = successful task completions / attempts, per task.
- **Time-on-task** = median seconds per task, with the range.
- **Error rate** = errors per task attempt.

### Cross-cutting
- **Latency percentiles**: p50, p90, p95, p99 over a run of N queries, plus a
  per-phase breakdown of the pipeline.
- **Cost per query** = LLM tokens × unit price, per lane. The deterministic
  common path should show near-zero LLM cost.

---

## 2. Statistical approach

Raw percentages without uncertainty are weak at this level. Add:
- **Confidence intervals**: bootstrap 95% CI on every accuracy and rate (resample
  queries with replacement, e.g. 10,000 iterations).
- **Paired system comparison** (the RQ3 three-way): each query is answered by
  every configuration, so outcomes are paired. For k related binary outcomes use
  **Cochran's Q** to test whether the systems differ, then pairwise **McNemar's
  test** with **Holm correction** for the specific pairs. For two systems,
  McNemar alone.
- **Nondeterminism**: LLM lanes vary run to run. Run each LLM configuration at
  temperature 0 and repeat 3 times; report mean and standard deviation, not a
  single run.
- **Annotator agreement**: when humans label faithfulness or success, report
  **Cohen's kappa** for two raters (or Krippendorff's alpha for more). When an
  LLM judge scores the full set, validate it against a human-labelled sample and
  report the kappa between judge and human.

---

## 3. RQ to metric map

| RQ | Claim | Primary metrics | Ground truth | File |
|----|-------|-----------------|--------------|------|
| RQ1 | Reproducible, integrated data layer | reproducibility rate, ingestion completeness, alignment coverage and precision | authority boundaries, raw sources | EVAL_RQ1 |
| RQ2 | Entity resolution via authority IDs | pairwise and B-cubed P/R/F1, blocking PC/PQ/RR, name vs ID delta | labelled pairs | EVAL_RQ2 |
| RQ3 | Traceable, grounded QA; deterministic beats RAG and LLM-SQL | answer accuracy, execution accuracy, faithfulness, hallucination, traceability, correct-empty, routing; three-way comparison | golden set, source rows | EVAL_RQ3 |
| RQ4 | Geospatial and KG enrichment | coverage and precision per context type, edge completeness | authoritative geo/census sources | EVAL_RQ4 |
| RQ5 | Usable by non-technical users | SUS, task success, time-on-task, error rate, themes | participant data | EVAL_RQ5 |

---

## 4. Final summary matrix (fill after all files are run)

| RQ | Headline metric | Result | 95% CI | Target | Verdict | Evidence section |
|----|-----------------|--------|--------|--------|---------|------------------|
| RQ1 | | | | | | |
| RQ2 | | | | | | |
| RQ3 | | | | | | |
| RQ4 | | | | | | |
| RQ5 | | | | | | |

Verdict is one of met / partially met / not met, judged against the target band
in each RQ file. This matrix mirrors the Chapter 1 traceability matrix and
becomes Table 6.8.

---

## 5. File index
- `EVAL_RQ1_data_integration.md`
- `EVAL_RQ2_entity_resolution.md`
- `EVAL_RQ3_ask_pipeline_and_comparison.md`
- `EVAL_RQ4_geospatial_kg_enrichment.md`
- `EVAL_RQ5_usability_user_study.md`
