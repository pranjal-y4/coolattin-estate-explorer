# Evaluation Spec — RQ1: Data Layer Integration and Reproducibility

Maps to: Section 6.2. Shared metric definitions in `EVAL_00_master_plan.md`.

## 1. What this RQ claims
A clean rebuild reproduces the data layer exactly, the five sources integrate
with known and explainable coverage, and townlands align to authoritative
boundaries.

## 2. Metrics

| Metric | Formula | Target band | Ground truth |
|--------|---------|-------------|--------------|
| Reproducibility rate | identical-checksum tables / total tables across 2 rebuilds | 100% (deterministic build) | rebuild output |
| Ingestion completeness (per source) | rows loaded / rows in raw file | 100% or fully explained | raw source files |
| Rejection rate (per source) | rows rejected / rows in raw file | low, with categorized reasons | raw source files |
| Alignment coverage | townlands matched to authority ID / ~152 | report honestly; >90% strong | VRTI / OSM / OSI |
| Alignment precision | correct matches / matches checked | >95% | manual check vs authority boundary |
| Cross-source referential integrity | records whose townland key resolves / total | ~100% or explained | the canonical townland table |

## 3. Dataset required
- The five raw source files with their original row counts recorded.
- The authority boundary reference for townlands (VRTI / OSM / OSI).
- A spot-check sample of 20 to 30 townlands for the precision measurement,
  drawn to include known hard cases (non-unique names, Upper/Lower splits).

## 4. Procedure
1. Run `rebuild.sh` twice from a clean state. Capture per-table row counts and a
   stable content checksum (hash of sorted rows) each time. Diff the two reports.
2. For each source, compare raw row count to loaded row count; log rejected rows
   with a reason code.
3. Left-join townlands to the authority table; bucket each into matched /
   unmatched / ambiguous.
4. Manually verify the sample of matches against the authority boundary; record
   correct / incorrect.

## 5. Results tables to fill

Reproducibility:

| Table | Rows (build 1) | Rows (build 2) | Checksum match |
|-------|----------------|----------------|----------------|
| | | | |

Ingestion completeness:

| Source | Raw rows | Loaded | Rejected | Rejection reasons |
|--------|----------|--------|----------|-------------------|
| Emigration | | | | |
| Evictions | | | | |
| Workhouse | | | | |
| Census | | | | |
| Townland/geo | | | | |

Alignment:

| Bucket | Count | % of ~152 |
|--------|-------|-----------|
| Matched to authority ID | | |
| Unmatched | | |
| Ambiguous | | |

Alignment precision: correct / checked = __ / __ = __%.

## 6. Rating and interpretation
- Reproducibility below 100% means the build is not deterministic. Investigate
  before reporting anything else, since every downstream number depends on it.
- Report alignment coverage exactly. Partial coverage with a listed set of
  unmatched townlands is a legitimate finding and shows honest data handling.
- Distinguish rejected rows that are genuine data-quality issues in the source
  from rows dropped by a bug. Only the first is a finding; the second is fixed.

## 7. Honest-reporting notes
If authority-ID coverage is partial, enumerate the unmatched and ambiguous cases
rather than rounding them away. This directly supports the RQ2 argument that
name-based keys are unsafe for these places.
