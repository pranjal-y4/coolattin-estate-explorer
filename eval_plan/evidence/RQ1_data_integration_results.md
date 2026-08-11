# RQ1 Results — Data Layer Integration and Reproducibility

Maps to Section 6.2. Produced by `eval_plan/scripts/rq1_data_integration.py`, run
2026-08-03. Raw console output: `eval_plan/evidence/RQ1_raw_output.txt`. All read-only —
row counts and a `dry_run=True` ingest pass that writes nothing to `coolattin.db`.

**Do not run a real (`dry_run=False`) full rebuild against this database before reading
§3 below.** A dry run just proved it would silently shrink `census_record`.

---

## 1. Ingestion completeness

| Source | Raw rows | Loaded | Status |
|---|---|---|---|
| `unified_processed.csv` | 13,707 | `unified_record`: 13,707 | **Exact match — 100% complete** |
| `workhouse_data_final.xlsx` (2 sheets: "1-127" + "from 128") | 3,920 + 4,294 = 8,214 | `source_mentions`: 8,214 | **Exact match — 100% complete** |
| `townlands.json` | 152 features | all 152 processed by `full_ingest.py` | 100% processed; **151/152 KG-enriched** (1 `kg_errors`) |

`census_record` and `clearances_record` are **not** simple one-file-to-one-table
mappings — don't try to compute a completeness ratio against `unified_census.csv` (165
rows) or `wicklow-census-data.csv` (1,539 rows) directly; neither is the sole source.
See §3.

---

## 2. Townland alignment coverage (repeats and confirms the earlier figure)

Scoped to the 152 townlands the estate's own unified records actually reference:

| Metric | Value |
|---|---|
| Estate townland universe | 152 (exact match to `townlands.json` feature count) |
| KG-enriched (any geometry/centroid returned) | 151 / 152 (99.3%) |
| OSM-matched (`osm_id` populated) | 132 / 152 (86.8%) |
| VRTI-matched (`vrti_id` populated) | 132 / 152 (86.8%) |
| OSI-matched (`osi_id` populated) | 83 / 152 (54.6%) |
| Logainm-matched | 0 / 152 (0%) |

**Two distinct coverage figures, both legitimate, for different claims**: 99.3% of
townlands get *some* KG enrichment (geometry/centroid — enough for the map), but only
86.8% get a specific external authority ID (`osm_id`/`vrti_id`) recorded. If the
dissertation cites "alignment coverage," specify which of these two you mean — they
support different strengths of claim.

---

## 3. Critical finding: the live VRTI census KG endpoint currently returns zero rows

Running `full_ingest.run_full_ingest(dry_run=True)` today (2026-08-03) against the
live VRTI SPARQL endpoint:

```
census_records_kg (live VRTI fetch, right now): 0
census_records_json (GeoJSON-embedded): 683
census_records_csv_seed (estate fallback, used because KG returned 0): 779
Fresh-rebuild census total: 1,462   vs   currently stored: 8,033
```

The system's own log line confirms this is a known, handled fallback path, not a
crash: `full_ingest.census_kg_empty_using_csv_seed | estate_seed_rows=779` — a WARNING,
not silent. But the practical consequence is serious for reproducibility: **a fresh
rebuild run today would only produce ~1,462 census rows, an 82% reduction from the
8,033 currently stored.** The 8,033 rows currently in `coolattin.db` must have been
populated by an earlier, successful VRTI census KG fetch (or by `townlands_ingest.py`'s
separate all-Wicklow KG pull contributing additional rows) at some point when the
endpoint returned real data — that state is **not currently reproducible** against the
live external dependency.

**This is the reason I did not run a real rebuild.** Doing so would have destructively
replaced the current, richer `census_record` table with the impoverished
fallback-only version, and that action is not easily reversible (the KG-sourced rows,
once overwritten, can't be regenerated until/unless the VRTI endpoint's census data
comes back). If you want the two-independent-rebuild reproducibility test the eval plan
asks for, it needs to run against a **copy** of the database (or be deferred until the
VRTI census endpoint is confirmed working again), not the live `coolattin.db`.

**This also directly informs RQ4's "tag every figure with the GraphDB/VRTI state that
produced it" requirement (§6.5)** — any census-derived enrichment figure from today
onward should be tagged "VRTI census endpoint empty as of 2026-08-03," separate from
whatever state produced the currently-stored 8,033 rows.

**Recommended next step, your call**: either (a) investigate why
`vrti_sparql.get_census_records_for_county(county="Wicklow")` now returns 0 — could be
a genuinely empty upstream dataset, a query/schema change on VRTI's end, or a transient
outage — before treating this as permanent, or (b) accept the current `coolattin.db`
state as the authoritative snapshot for the dissertation and document that a live
rebuild is not currently reproducible, as a named finding rather than something to fix.

---

## 4. Clearances (evictions) — small but real drift

| | Value |
|---|---|
| Fresh dry-run clearances_records | 1,231 |
| Currently stored `clearances_record` | 1,211 |
| Delta | +20 (1.7%) |

Clearances come entirely from GeoJSON-embedded per-townland fields in `townlands.json`
(no external KG dependency), so this small delta is not explained by an external
endpoint going empty — it's a genuine minor reproducibility gap worth a one-line
mention (not necessarily investigating further unless you want 100% reproducibility
claimed exactly).

---

## 5. Reproducibility rate — not measured as originally specified, and why

The eval plan's original ask ("run `rebuild.sh` twice, diff checksums") is **not safe to
run against the live database right now**, for the reason in §3. What I can report
honestly instead:

- `unified_record` and `source_mentions` are trivially, exactly reproducible: they load
  1:1 from static files with no external dependency (100% match confirmed in §1). Any
  rebuild will reproduce these two tables exactly.
- `townland` is reproducible for the static/GeoJSON-derived fields but not for KG
  enrichment fields, since `full_ingest.py` calls the live VRTI SPARQL endpoint
  per-townland at ingest time — 151/152 succeeded just now, so this is close to
  reproducible today, but is not guaranteed deterministic across runs by design (it
  depends on an external service's availability at run time).
- `census_record` is **not currently reproducible** — see §3. This is the honest,
  reportable answer to "is the build deterministic": **no, not for this table, because
  of a live external dependency currently in a degraded state.** State this plainly in
  §6.2 rather than claiming 100% reproducibility across all five tables.

## Summary table for §6.8 / master plan matrix

| Metric | Result | Target | Verdict |
|---|---|---|---|
| Ingestion completeness — unified_record | 100% (13,707/13,707) | 100% | Met |
| Ingestion completeness — source_mentions | 100% (8,214/8,214) | 100% | Met |
| Alignment coverage (OSM/VRTI) | 86.8% (132/152) | >90% strong | Partially met — report honestly |
| Alignment coverage (any KG enrichment) | 99.3% (151/152) | >90% strong | Met |
| Alignment coverage (Logainm) | 0% (0/152) | — | Not met — field entirely unpopulated |
| Reproducibility — unified_record, source_mentions | 100% deterministic | 100% | Met |
| Reproducibility — census_record | **Not reproducible today** (live VRTI census KG returns 0; fresh rebuild would shrink 8,033→~1,462) | 100% | **Not met — named finding, not a bug to silently fix** |
| Reproducibility — clearances_record | 1,211 vs 1,231 fresh (1.7% drift) | 100% | Mostly met, minor unexplained drift |
