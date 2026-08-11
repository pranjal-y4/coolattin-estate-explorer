# Evaluation Spec — RQ3: Traceable, Grounded Question Answering

Maps to: Section 6.4. Shared metric definitions in `EVAL_00_master_plan.md`.
This is the heart of the trustworthiness claim and holds the head-to-head
comparison you asked for. It has two parts: Part A measures the shipped system,
Part B compares the deterministic path against LLM-SQL analytics and RAG.

---

## Part A — Shipped-system grounded QA

### A.1 What this claims
The system answers factual questions correctly, links answers to exact source
rows, stays grounded, refuses honestly when no data exists, and routes queries
accurately, with no LLM-authored SQL on the common path.

### A.2 Metrics

| Metric | Formula | Target band | Ground truth |
|--------|---------|-------------|--------------|
| Answer accuracy (aggregation) | correct numeric answers / total | ~100% | manual SQL against source |
| Answer accuracy (lookup) | exact match or entity F1 | high | manual SQL against source |
| Traceability rate | answers citing exact source rows / total | ~100% by design | answer provenance block |
| Faithfulness | supported claims / total claims | >= 0.95 | claim-level annotation |
| Hallucination rate (common path) | answers with >=1 unsupported claim / total | <= 0.02 | claim-level annotation |
| Hallucination rate (live fallback) | same, on forced fallback path | report honestly | claim-level annotation |
| Correct-empty rate | correct refusals / no-data queries | high | verified-empty query set |
| False-empty rate | wrong refusals / has-data queries | low | has-data query set |
| Routing accuracy (in-distribution) | correct route / total | 89.3% prior | labelled routes |
| Routing accuracy (held-out) | correct route / total on unseen phrasing | ~71% prior, a finding | labelled routes |
| LLM-authored-SQL count (common path) | count | 0 | SQL-path counter |

### A.3 Datasets required
- **Golden set** with the true answer per query and the query type recorded.
- **Verified-empty set**: queries whose correct answer is "no records," each
  confirmed against the source (for example male Ardoyne evictions). Confirm the
  data genuinely lacks them before calling an empty return correct.
- **Has-data set**: queries with non-empty correct answers, to measure
  false-empty.
- **Routing sets**: an in-distribution set and a held-out set of unseen
  paraphrases, each with the correct route labelled.
- **Annotation sample** for faithfulness and hallucination if annotating a
  subset rather than the full set.

### A.4 Procedure
1. Run the golden set; score answer accuracy by query type.
2. Assert a provenance reference on every answer; compute the traceability rate.
3. Increment and log the SQL-path counter on the common path; assert 0 after the
   run.
4. Force the fallback path (disable the primary provider and the template
   short-circuit); run the same set; annotate for hallucination.
5. Run the verified-empty and has-data sets; compute correct-empty and
   false-empty.
6. Run both routing sets; compute accuracy and a confusion matrix over route
   classes.

### A.5 Faithfulness and hallucination annotation protocol
1. Decompose each answer into atomic claims.
2. Mark each claim supported or unsupported by the retrieved evidence.
3. Use an LLM judge on the full set with a fixed rubric, then validate it against
   a human-labelled sample and report Cohen's kappa between judge and human. A
   kappa below about 0.6 means the judge is unreliable; fall back to human
   labelling on a sample and report the smaller N.

---

## Part B — Comparison: deterministic vs LLM-SQL vs RAG

This is the argument for the design. Run the same benchmark through three
configurations and compare on shared metrics, per query type.

### B.1 Configurations
- **C1 Deterministic (shipped, reference).** Pre-flight, template and fast-lane
  match, parameterized SQL, deterministic answer. This is optional if you only
  want the two you named, but including it is what lets you claim the design win.
- **C2 LLM-SQL analytics (text-to-SQL baseline).** Natural language to
  LLM-generated SQL against the same schema, execute, answer. No templates.
- **C3 RAG baseline.** Serialize records into a corpus, embed, retrieve top-k,
  synthesize the answer from retrieved passages. No SQL.

Control the variables: same database snapshot, same benchmark, and the same LLM
for C2 and C3 synthesis so the comparison isolates the strategy, not the model.

### B.2 Benchmark stratification
Split the benchmark into types so the trade-offs surface instead of averaging
out:
- **Lookup**: a single record fact.
- **Aggregation**: count, sum, average, group-by.
- **Multi-hop / relational**: a join across sources.
- **Out-of-scope**: no data exists (drives correct-empty).
- **Unseen phrasing**: paraphrases of covered queries (drives the routing and
  generalization view).

Record how many queries fall in each stratum; report per stratum and overall.

### B.3 Shared metrics for the comparison
Per configuration, per stratum, and overall:
- Answer accuracy (numeric or set match).
- Faithfulness.
- Hallucination rate.
- Traceability capability and measured rate. C1 and C2 can cite exact rows; C3
  cites retrieved passages only, so its traceability is approximate. State this.
- Execution accuracy and valid-SQL rate for C1 and C2 (N/A for C3).
- Correct-empty rate on the out-of-scope stratum.
- Latency p50 and cost per query.

### B.4 Nondeterminism and significance
- Run C2 and C3 at temperature 0, repeat 3 times, report mean and standard
  deviation. C1 is deterministic, so a single run suffices.
- Outcomes are paired (every query hits all three). Test with Cochran's Q across
  the three, then pairwise McNemar with Holm correction. Report bootstrap 95% CIs
  on each accuracy.

### B.5 Results tables to fill

Overall comparison:

| Metric | C1 deterministic | C2 LLM-SQL | C3 RAG |
|--------|------------------|-----------|--------|
| Answer accuracy | | mean ± sd | mean ± sd |
| Faithfulness | | | |
| Hallucination rate | | | |
| Traceability rate | | | approximate |
| Valid-SQL rate | | | N/A |
| Correct-empty rate | | | |
| Latency p50 (ms) | | | |
| Cost per query | ~0 | | |

Accuracy by stratum:

| Stratum | N | C1 | C2 | C3 |
|---------|---|----|----|----|
| Lookup | | | | |
| Aggregation | | | | |
| Multi-hop | | | | |
| Out-of-scope | | | | |
| Unseen phrasing | | | | |

Significance: Cochran's Q p = __. Pairwise McNemar (Holm-adjusted):
C1 vs C2 p = __, C1 vs C3 p = __, C2 vs C3 p = __.

### B.6 Expected trade-offs (hypotheses to test, not results)
State these as predictions and let the numbers confirm or refute them:
- Aggregation: C1 highest and exact; C2 strong but loses points to wrong joins
  and invalid SQL; C3 weakest, since retrieval cannot count reliably.
- Lookup: all three competitive; C3 closest to the others here.
- Multi-hop: C1 bounded by covered templates; C2 variable; C3 weak on precise
  joins.
- Out-of-scope: C1 refuses honestly by design; C2 and C3 more likely to invent.
- Cost and latency: C1 fastest and near-zero LLM cost; C2 and C3 pay per query.

The honest counterpoint that keeps this credible: C1 is bounded by coverage, so
on unseen phrasing its advantage narrows. That is the generalization gap, and it
belongs in the same table, not hidden.

## B.7 Rating and interpretation
The claim is not "C1 wins everything." It is "C1 wins correctness, traceability,
latency, and cost on the covered strata, and the price is coverage." A table that
shows C2 or C3 ahead on unseen phrasing strengthens that honest framing rather
than weakening it.
