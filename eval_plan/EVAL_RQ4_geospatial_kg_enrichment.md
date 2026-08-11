# Evaluation Spec — RQ4: Geospatial and Knowledge-Graph Enrichment

Maps to: Section 6.5. Shared metric definitions in `EVAL_00_master_plan.md`.

## 1. What this RQ claims
Enrichment adds correct context across four types (administrative geography,
connected records, population patterns, landscape features) with known coverage,
verified against authoritative sources.

## 2. Metrics

| Metric | Formula | Target band | Ground truth |
|--------|---------|-------------|--------------|
| Coverage (per type) | entities enriched / total entities | report honestly per type | the entity set |
| Precision (per type) | correct enrichments / checked | >90% | authoritative geo/census source |
| Edge completeness (LOCATED_IN) | records with edge / total records | report the gap | the record set |
| Link accuracy | correct townland-to-geometry links / checked | >95% | authoritative boundary source |

## 3. Dataset required
- The entity set per context type, with totals.
- An authoritative reference per type: administrative geography and boundaries
  from the geo authority, population figures from census, landscape features from
  the relevant source.
- A spot-check sample per type (15 to 30 each) for precision.
- A clear record of the GraphDB state used: empty at freeze or populated after
  loading. Every number below is tagged with which one produced it.

## 4. Procedure
1. Run the enrichment coverage report: per type, count entities with and without
   enrichment.
2. Draw the per-type sample; verify each enrichment against the authoritative
   source; record correct / incorrect.
3. Run the LOCATED_IN backfill audit: count records missing the edge.
4. Verify a sample of townland-to-geometry links against authoritative
   boundaries.

## 5. Results tables to fill

Coverage and precision:

| Context type | Total | Enriched | Coverage % | Precision (sample) | GraphDB state |
|--------------|-------|----------|-----------|--------------------|---------------|
| Administrative geography | | | | | |
| Connected records | | | | | |
| Population patterns | | | | | |
| Landscape features | | | | | |

Edge completeness: records with LOCATED_IN / total = __ / __ = __%.
Missing edges: __.

Link accuracy: correct / checked = __ / __.

## 6. Rating and interpretation
- Coverage and precision are separate. Thin coverage with high precision on what
  exists is honest and useful. High coverage with low precision is worse, since
  it means confident wrong enrichment.
- Report the LOCATED_IN gap as a number, not a hedge. It is a named future-work
  item, so quantifying it closes the loop with Section 8.3.

## 7. Honest-reporting notes
State plainly what ran end to end versus what was structurally present but
unvalidated. If GraphDB timed out and VRTI returned errors at freeze, the
frozen v1.0 enrichment was not fully validated. Present any numbers gathered from
the populated-after-loading state as a separate post-freeze validation, clearly
labelled, so they are not read as frozen v1.0 results.
