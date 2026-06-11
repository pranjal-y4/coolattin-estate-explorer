# VRTI Authority-ID Consistency Audit

**Date:** 2026-06-10  
**Scope:** All 150 townland rows where both `vrti_id` and `kg_uri` are populated  
**Motivation:** Q6 surfaced a case where the two authority-ID fields pointed to different VRTI entities for the same townland row, raising the question of whether this is systemic or isolated.

---

## Method

The `kg_uri` field uses the canonical VRTI URI format:

```
https://kg.virtualtreasury.ie/place/present-day/townland/{name}/{id}
```

The trailing `{id}` token in that URI must equal `vrti_id` for both fields to be consistent.  
Each mismatch was then verified against the Virtuoso SPARQL endpoint to determine which entity
each ID actually resolves to, and cross-checked against the `civil_parish` and `barony` values
already stored in the same row.

---

## Result

| Metric | Count | % of 150 |
|---|---|---|
| Townlands with both fields populated | 150 | — |
| Consistent (IDs match) | 146 | 97.3% |
| **Inconsistent (IDs point to different entities)** | **4** | **2.7%** |

**4 of 150 townlands have conflicting VRTI pointers.**

---

## Inconsistent rows

| Townland | Civil parish (DB) | Barony (DB) | `vrti_id` | Entity resolved by `vrti_id` | URI-trailing ID | Entity resolved by URI ID |
|---|---|---|---|---|---|---|
| BALLINACOR | Kilbride | Arklow | `v12zgr6` | Ballinacor, Kilbride, Arklow (Logainm 55490) | `v18q4nn` | Ballinacor (ED Ballinacor), Ballinacor parish — *different barony* |
| BALLARD | Carnew | Scarawalsh | `v1c4qn4` | Ballard, Carnew, Wicklow (Logainm 55912) | `v15dbv3` | Ballard, Cloonbur, **County Galway** |
| BALLAGH | Kilpipe | Ballinacor South | `v14kry1` | Ballagh, Kilpipe, Ballinacor South, Wicklow | `v19gdj2` | Ballagh, Knockane, **County Kerry** |
| AGHOWLE UPPER | Rathnew | Newcastle | `v1f5wh1` | Aghowle Upper, Rathnew, Newcastle (Logainm 55318) | `v1skm45` | Aghowle Upper, Aghowle parish, Shillelagh barony (Logainm 55787) |

---

## Which field is correct?

**`vrti_id` is correct in all four cases.**

Cross-checking the `P89_falls_within` (parent parish) and `P71i_is_listed_in` (townlands.ie URL)
triples from the SPARQL endpoint against the DB's own `civil_parish` and `barony` columns
confirms that `vrti_id` resolves to the right Wicklow entity in every case:

- **BALLINACOR** — `vrti_id` → Kilbride parish, Arklow barony ✓ matches DB; `kg_uri` → Ballinacor parish (different barony, different Logainm entry)
- **BALLARD** — `vrti_id` → Carnew/Wicklow ✓ matches DB; `kg_uri` → Cloonbur/Galway (wrong county entirely)
- **BALLAGH** — `vrti_id` → Kilpipe/Ballinacor South ✓ matches DB; `kg_uri` → Knockane/Kerry (wrong county entirely)
- **AGHOWLE UPPER** — `vrti_id` → Rathnew/Newcastle ✓ matches DB; `kg_uri` → Aghowle/Shillelagh (same county, wrong barony/parish)

Additionally, the three SPARQL-richer `vrti_id` entities carry `owl:sameAs` links to Logainm
and OSI identifiers, consistent with being the primary (better-curated) KG records.

---

## Root cause

The `kg_uri` field was populated during ingest by **name-only matching**: the script constructed
a URI from `{name}/{first-match-id}` without validating that the returned entity's parent
geography agreed with the row's `civil_parish`/`barony`. Because common townland names like
"Ballard" and "Ballagh" occur in multiple Irish counties, the name lookup returned an
out-of-county homonym in 2 of 4 cases and a within-county homonym in the other 2.

The `vrti_id` field was populated by a separate, more constrained lookup (OSI/Logainm
cross-reference) and is correct in all affected rows.

---

## Interpretation

The inconsistency is **not systemic** — 97.3% of rows are clean — but it is also **not
purely anecdotal**: four separate ingest failures follow the same pattern (wrong homonym
assigned to `kg_uri`). Any query that routes through `kg_uri` for these four townlands will
retrieve geographic data (centroid, boundary, census) for the wrong place.

For the dissertation evaluation, Q6 is representative of a real, reproducible failure class:
name-only URI resolution without geographic disambiguation. The fix is a post-ingest
consistency check that compares the entity's resolved `P89_falls_within` parish against the
stored `civil_parish`, and flags or corrects mismatches.

---

## Recommended correction

For the 4 affected rows, set `kg_uri` to the URI derived from `vrti_id`:

```sql
UPDATE townland
SET kg_uri = 'https://kg.virtualtreasury.ie/place/present-day/townland/' || name || '/' || vrti_id
WHERE name IN ('BALLINACOR', 'BALLARD', 'BALLAGH', 'AGHOWLE UPPER')
  AND vrti_id IN ('v12zgr6', 'v1c4qn4', 'v14kry1', 'v1f5wh1');
```

(The name slug in the URI uses the capitalized form already present in the DB; the VRTI endpoint
also accepts the lowercase-hyphenated variant used in existing well-formed `kg_uri` values, but
a uniform fix via `vrti_id` is sufficient for entity resolution.)
