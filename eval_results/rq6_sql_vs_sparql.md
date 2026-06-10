# RQ6 — SQL vs SPARQL Competency Question Evaluation

**Research Question 6:** How does the deterministic SQL query layer compare to an
equivalent RDF/SPARQL representation for the same competency questions, and where
do the two approaches agree or diverge?

**Methodology:** Each competency question is expressed as both a compiled SQL query
(from `semantic_layer.compile_sql`) and an equivalent SPARQL query in two KG contexts:
(a) the local GraphDB `co:` repository loaded from `data/seed/coolattin.ttl`, and
(b) the external VRTI SPARQL endpoint (where relevant).  
Results are compared for numeric/value agreement, and discrepancies are classified as
**AGREEMENT**, **SEMANTIC** (open/closed-world, NULL handling, language tags, name
disambiguation), or **DATA-LEVEL** (genuine source disagreement with provenance).

**Date evaluated:** 2026-06-10  
**Database:** `coolattin.db` (SQLite) — 13,707 unified records, 8,033 census records,
1,211 clearance rows, 4,225 townland rows  
**GraphDB:** `localhost:7200/repositories/coolattin` — `co:` ontology, loaded from
`data/seed/coolattin.ttl` — **189,018 triples**  
**VRTI SPARQL:** `https://virtuoso.virtualtreasury.ie/sparql/` (external, read-only,
queried live — 4,460,845 total triples)

---

## Ontology namespace

All `co:` terms use `https://coolattin.ie/ontology#`.  
Person-event data: `co:Person` / `co:Event` / `co:eventType` (values: `"emigration"`,
`"eviction"`, `"tenancy"`) — loaded from `unified_record`.  
Spatial data: `co:Townland` / `co:civilParish` / `co:barony` / `co:county` — loaded
from `townland`.  
Aggregate data: `co:CensusRecord` / `co:totalPopulation` / `co:forTownland` — loaded
from `census_record`; `co:Clearance` / `co:count` — loaded from `clearances_record`.

---

## Competency Question Set

| # | Question | Category | SQL table(s) | SPARQL class |
|---|----------|----------|--------------|--------------|
| Q1 | How many people emigrated from the Coolattin estate? | A — aggregate | `unified_record` | `co:Person ; co:Event` |
| Q2 | How many people emigrated from Ballynultagh? | A — filtered | `unified_record` | `co:Person ; co:Event + FILTER` |
| Q3 | How many evictions were recorded in total? | A — aggregate | `clearances_record` | `co:Clearance` |
| Q4 | What was the estate population in 1841? | A — aggregate | `census_record JOIN townland` | `co:CensusRecord` |
| Q5 | What was the population of Ballinacor in 1841? | A — filtered | `census_record JOIN townland` | `co:CensusRecord ; co:forTownland` |
| Q6 | Which parish and barony is Ballinacor in? | R — attribute | `townland` | `co:Townland` (local); `P89_falls_within` (VRTI) |

---

## Compiled Queries and Results

### Q1 — Total Emigration

**SQL:**
```sql
SELECT COUNT(DISTINCT record_id) AS emigration_count
FROM unified_record
WHERE has_emigration_record = 1
```
**Result (SQLite):** `emigration_count = 6016`

**SPARQL (`co:` local GraphDB):**
```sparql
PREFIX co: <https://coolattin.ie/ontology#>
SELECT (COUNT(DISTINCT ?person) AS ?emigration_count)
WHERE {
  ?person a co:Person ;
          co:hasEvent ?ev .
  ?ev co:eventType "emigration" .
}
```
**Result (GraphDB):** `emigration_count = 6016`

**Classification: AGREEMENT**

**Semantic note:** SQL operates under the closed-world assumption — a count of zero means
no matching rows exist. SPARQL under the open-world assumption treats a zero result as
"no matching triples in this graph", not necessarily "no emigrants exist". Because the
graph is fully loaded, both return 6,016 and the epistemically different assumptions
produce the same numeric answer here. The `co:eventType "emigration"` predicate maps
exactly to `has_emigration_record = 1` via the uplift logic in `rdf_uplift.py`.

---

### Q2 — Emigration from Ballynultagh

**SQL:**
```sql
SELECT COUNT(DISTINCT record_id) AS emigration_count
FROM unified_record
WHERE has_emigration_record = 1
  AND townland_norm = 'BALLYNULTAGH'
```
**Result (SQLite):** `emigration_count = 400`

**SPARQL (`co:` local GraphDB):**
```sparql
PREFIX co: <https://coolattin.ie/ontology#>
SELECT (COUNT(DISTINCT ?person) AS ?emigration_count)
WHERE {
  ?person a co:Person ;
          co:townland ?t ;
          co:hasEvent ?ev .
  ?ev co:eventType "emigration" .
  FILTER(UCASE(STR(?t)) = "BALLYNULTAGH")
}
```
**Result (GraphDB):** `emigration_count = 400`

**Classification: AGREEMENT**

**Semantic note:** SQL uses the normalised column `townland_norm` (pre-uppercased at
ingest). SPARQL applies `UCASE(STR(?t))` at query time to the raw literal `"Ballynultagh"`.
Both normalisation strategies produce the same set of 400 records. For names with
accented Irish characters, `STR()` strips RDF language tags before `UCASE()`, while
SQLite `UPPER()` applies locale-dependent collation — a potential divergence point for
Irish-language place names not present in this dataset.

---

### Q3 — Total Evictions (Clearances Ledger)

**SQL:**
```sql
SELECT SUM(cr.count) AS total_evictions
FROM clearances_record cr
LEFT JOIN townland t ON cr.townland_id = t.id
```
**Result (SQLite):** `total_evictions = 7763`

**SPARQL (`co:` local GraphDB):**
```sparql
PREFIX co: <https://coolattin.ie/ontology#>
SELECT (SUM(?count) AS ?eviction_count)
WHERE {
  ?ev a co:Clearance ;
      co:count ?count .
}
```
**Result (GraphDB):** `eviction_count = 7763`

**Classification: AGREEMENT**

**Semantic note:** The SQL `LEFT JOIN` retains all `clearances_record` rows regardless
of whether their `townland_id` resolves in the `townland` table; since 0 clearance rows
have missing townland references (verified), the LEFT vs INNER join makes no practical
difference. The SPARQL query over `co:Clearance` instances (derived from the same 1,211
`clearances_record` rows) sums identically. The SQL column `count` is a pre-aggregated
per-townland/per-year figure, not a count of individual events — the SPARQL `co:count`
predicate preserves this semantics exactly.

---

### Q4 — Estate Population in 1841

**SQL:**
```sql
SELECT SUM(c.total) AS population
FROM census_record c
JOIN townland t ON c.townland_id = t.id
WHERE c.year = 1841
```
**Result (SQLite):** `population = 119300`

**SPARQL (`co:` local GraphDB):**
```sparql
PREFIX co: <https://coolattin.ie/ontology#>
SELECT (SUM(?total) AS ?population)
WHERE {
  ?census a co:CensusRecord ;
          co:year 1841 ;
          co:totalPopulation ?total .
}
```
**Result (GraphDB):** `population = 119300`

**Classification: AGREEMENT**

**Semantic note:** The SQL `INNER JOIN townland` would exclude census rows with an
unresolvable `townland_id`. All 8,033 census rows carry valid townland references
(0 orphaned rows, verified at uplift time), so the join excludes nothing. The SPARQL
query does not require `co:forTownland` because the join constraint adds no additional
filtering in this dataset; including it would still return 119,300. The `co:totalPopulation`
predicate maps to `census_record.total` (the sum of males and females).

---

### Q5 — Population of Ballinacor in 1841

**SQL:**
```sql
SELECT SUM(c.total) AS population
FROM census_record c
JOIN townland t ON c.townland_id = t.id
WHERE UPPER(t.name) = 'BALLINACOR'
  AND c.year = 1841
```
**Result (SQLite):** `population = 55`

**SPARQL (`co:` local GraphDB):**
```sparql
PREFIX co: <https://coolattin.ie/ontology#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT (SUM(?total) AS ?population)
WHERE {
  ?census a co:CensusRecord ;
          co:year 1841 ;
          co:totalPopulation ?total ;
          co:forTownland ?t .
  ?t rdfs:label ?name .
  FILTER(UCASE(STR(?name)) = "BALLINACOR")
}
```
**Result (GraphDB):** `population = 55`

**Classification: AGREEMENT**

**Semantic note:** The townland name is stored as `"BALLINACOR"` (uppercase) in the
SQLite `townland.name` column and as an `rdfs:label "BALLINACOR"` literal in the TTL.
Both `UPPER(t.name)` and `UCASE(STR(?name))` produce the same filter. The SPARQL
correctly joins via `co:forTownland` to the corresponding `co:Townland` node, mirroring
the SQL INNER JOIN. The local `co:` graph contains only the estate's Ballinacor (in
Kilbride civil parish), so there is no name-ambiguity issue here that would arise
in a KG with multiple Ballinacor entities (see Q6).

---

### Q6 — Ballinacor Parish and Barony (Attribute Lookup)

**SQL:**
```sql
SELECT civil_parish, barony, county
FROM townland
WHERE UPPER(name) = 'BALLINACOR'
LIMIT 1
```
**Result (SQLite):** `civil_parish=Kilbride, barony=Arklow, county=Wicklow`

**SPARQL (`co:` local GraphDB):**
```sparql
PREFIX co: <https://coolattin.ie/ontology#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?name ?parish ?barony ?county
WHERE {
  ?t a co:Townland ;
     rdfs:label ?name ;
     co:civilParish ?parish ;
     co:barony ?barony ;
     co:county ?county .
  FILTER(UCASE(STR(?name)) = "BALLINACOR")
}
```
**Result (local GraphDB):** `name=BALLINACOR, parish=Kilbride, barony=Arklow, county=Wicklow`

**SQL vs local GraphDB: AGREEMENT**

**VRTI SPARQL cross-check (via `kg_uri` stored in SQLite `townland.kg_uri`):**
```sparql
# Using SQLite's kg_uri = https://kg.virtualtreasury.ie/place/present-day/townland/Ballinacor/v18q4nn
SELECT ?label ?parish_label ?barony_label
WHERE {
  <https://kg.virtualtreasury.ie/place/present-day/townland/Ballinacor/v18q4nn>
      rdfs:label ?label ;
      <http://erlangen-crm.org/current/P89_falls_within> ?parish .
  ?parish rdfs:label ?parish_label .
  OPTIONAL { ?parish <http://erlangen-crm.org/current/P89_falls_within> ?barony .
             ?barony rdfs:label ?barony_label . }
  FILTER(LANG(?label) = "en" || LANG(?label) = "")
  FILTER(LANG(?parish_label) = "en" || LANG(?parish_label) = "")
}
```
**Result (VRTI via `kg_uri`):** `civil_parish=Ballinacor, barony=Ballinacor South`  
**SQL vs VRTI: DATA-LEVEL discrepancy** — `Kilbride` ≠ `Ballinacor` (civil parish);
`Arklow` ≠ `Ballinacor South` (barony)

**VRTI via `vrti_id` (SQLite `townland.vrti_id = v12zgr6`):**
```sparql
SELECT ?parish_label ?barony_label
WHERE {
  <https://kg.virtualtreasury.ie/place/present-day/townland/Ballinacor/v12zgr6>
      <http://erlangen-crm.org/current/P89_falls_within> ?parish .
  ?parish rdfs:label ?parish_label .
  OPTIONAL { ?parish <http://erlangen-crm.org/current/P89_falls_within> ?barony .
             ?barony rdfs:label ?barony_label . }
  FILTER(LANG(?parish_label) = "en" || LANG(?parish_label) = "")
}
```
**Result (VRTI via `vrti_id`):** `civil_parish=Kilbride, barony=Arklow` — AGREEMENT with SQL

**Discrepancy explanation (SQL vs VRTI via `kg_uri`):**
The `townland` table stores two VRTI identifiers for the estate's Ballinacor:
`vrti_id = "v12zgr6"` (VRTI entity in Kilbride/Arklow) and `kg_uri` pointing to
`v18q4nn` (a distinct VRTI entity labelled "Ballinacor (ED Ballinacor)" in the civil
parish of Ballinacor and barony of Ballinacor South). Ireland has several townlands
named Ballinacor; the KG ingest aligned the estate's Ballinacor to a different VRTI
entity than the `vrti_id` field suggests, introducing a self-contradictory alignment
within the `townland` table itself.

This is a DATA-LEVEL discrepancy when comparing SQL administrative values against the
VRTI entity referenced by `kg_uri`. It is not a discrepancy when comparing against the
VRTI entity referenced by `vrti_id`. The disagreement originates in entity alignment,
not in a difference between the Fitzwilliam survey and the VRTI administrative boundary
data.

**Additional semantic note:** A label-based SPARQL query against VRTI for `"Ballinacor"`
returns three distinct townland entities (v12zgr6, v18q4nn, v13j2gy) with different
civil parishes (Kilbride, Ballinacor, Kilcommon) and baronies (Arklow, Ballinacor South,
Ballinacor South). The SQL `LIMIT 1` silently returns one row; a SPARQL query without
`LIMIT` returns all matching nodes — semantically more correct but ambiguous without
URI-level disambiguation. This is a genuine open-world vs closed-world behavioural
difference for ambiguous place names.

---

## Summary Table

| # | Question | SQL Result | SPARQL Result (local co:) | Agreement | Classification |
|---|----------|-----------|---------------------------|-----------|----------------|
| Q1 | Total emigration | 6016 | 6016 | ✓ | AGREEMENT |
| Q2 | Emigration Ballynultagh | 400 | 400 | ✓ | AGREEMENT |
| Q3 | Total evictions | 7763 | 7763 | ✓ | AGREEMENT |
| Q4 | Population 1841 | 119300 | 119300 | ✓ | AGREEMENT |
| Q5 | Pop. Ballinacor 1841 | 55 | 55 | ✓ | AGREEMENT |
| Q6 | Ballinacor parish/barony | Kilbride/Arklow | Kilbride/Arklow | ✓ | AGREEMENT (local); DATA-LEVEL (VRTI via kg_uri) |

---

## Analysis: Sources of Disagreement

### 1. Zero empty-graph discrepancies
All six competency questions return non-empty SPARQL results. The `co:` repository
contains 189,018 triples loaded from `data/seed/coolattin.ttl`, which uplifts all four
relevant SQLite tables: `unified_record` (13,707 rows → `co:Person` + `co:Event`),
`townland` (4,225 rows → `co:Townland`), `census_record` (8,033 rows →
`co:CensusRecord`), and `clearances_record` (1,211 rows → `co:Clearance`). The previous
evaluation artefact that showed 5/6 "discrepancies" was an unloaded-graph artifact and
has been discarded.

### 2. Open vs closed world assumption (SEMANTIC — all questions)
SQL (closed world): absence of a row means the fact does not exist. A `WHERE` clause
with no matches returns zero. `SUM()` of zero rows returns NULL (coerced to 0 by the
pipeline). The database is the complete and authoritative record.

SPARQL (open world): absence of a triple means unknown. A COUNT/SUM over no bindings
returns 0 or empty. A result of 0 could mean "zero entities have this property" OR
"this property is not asserted in the loaded graph". Because the graph is fully loaded
from the same SQLite source, both systems agree numerically. The epistemological
difference becomes operationally significant only when the graph is partially loaded or
the question refers to data outside the estate corpus.

### 3. NULL handling in JOIN operations (SEMANTIC — Q4, Q5)
SQL `INNER JOIN` excludes rows with NULL foreign keys implicitly. The equivalent SPARQL
pattern with required predicates (no OPTIONAL) behaves the same in practice: a
`co:CensusRecord` lacking `co:forTownland` does not match the required-triple pattern.
Since 0 census rows have missing townland references, both systems exclude the same
(empty) set of records.

### 4. Case normalisation for place name filters (SEMANTIC — Q2, Q5)
SQLite `UPPER()` operates on stored values (pre-normalised at ingest to uppercase for
`townland_norm`; raw mixed-case for `townland.name`). SPARQL `UCASE(STR(?name))` strips
RDF language tags via `STR()` and then uppercases. Both produce identical results for
the ASCII place names in this dataset. For Irish-language names with accented characters
(`Cill Bhríde`, etc.), language-tagged literals would require `LANG()` filtering in
SPARQL to match the expected behaviour of SQLite's locale collation.

### 5. Name ambiguity and open-world disambiguation (SEMANTIC — Q6)
A label-based SPARQL query for `"Ballinacor"` against VRTI returns three distinct
townland entities because Ireland contains multiple townlands with the same name. SQL's
`LIMIT 1` silently picks one; SPARQL without `LIMIT` returns all. URI-level
disambiguation (using the `vrti_id` stored in the `townland` table) resolves the
ambiguity and produces agreement with SQL values.

### 6. Entity alignment discrepancy (DATA-LEVEL — Q6)
The SQLite `townland` table for the estate's Ballinacor stores two different VRTI
identifiers: `vrti_id = "v12zgr6"` (→ Kilbride parish, Arklow barony — agrees with
SQLite) and `kg_uri = "…/v18q4nn"` (→ Ballinacor parish, Ballinacor South barony —
disagrees). This self-inconsistency within the `townland` table is a genuine data-level
finding: the KG ingest linked the estate's Ballinacor to a different VRTI entity than
the one canonically associated with the Kilbride/Arklow administrative hierarchy.
The discrepancy is in the entity alignment step, not in the underlying administrative
boundary data.

---

## Implications for RQ6

1. **Structural equivalence is demonstrated** — the `semantic_layer.py` compile_sql /
   compile_sparql dual-path architecture correctly generates structurally equivalent
   queries for all six competency questions. All six return matching numeric or
   attribute results when the graph is loaded.

2. **Open-world assumption is operationally inert for a complete load** — the
   philosophical difference between SQL closed-world and SPARQL open-world does not
   produce numeric divergence when the SPARQL graph is loaded from the same source.
   It becomes significant when the graph is partially loaded, when external KGs
   are queried, or when comparing absence-of-data against presence-of-zero.

3. **Name disambiguation is a real operational risk** — Q6 shows that label-based
   SPARQL queries for ambiguous Irish place names can match multiple VRTI entities.
   URI-level anchoring (via `vrti_id` or `kg_uri`) is required for deterministic
   results, and those two fields must be kept consistent.

4. **Entity alignment surfaces as a data-level finding** — the inconsistency between
   `kg_uri` and `vrti_id` for the estate's Ballinacor is a genuine source-of-truth
   disagreement that the pipeline's Phase 6 (multi-model synthesis) would surface as
   a cross-source discrepancy annotation.

---

_Generated by manual + automated analysis — 2026-06-10_  
_GraphDB load: 189,018 triples from data/seed/coolattin.ttl_  
_VRTI queries executed live against https://virtuoso.virtualtreasury.ie/sparql/_
