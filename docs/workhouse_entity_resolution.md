# Workhouse Entity Resolution

This document covers workhouse-to-unified-record matching only. It is separate
from Ask-page pgvector retrieval and does not depend on PostgreSQL or pgvector.

## Why this is separate from pgvector

Workhouse mapping is an entity-resolution problem, not a semantic retrieval
problem.

- Ask page: retrieve semantically relevant context for natural-language questions.
- Workhouse mapping: generate explicit, reviewable candidate identity links.

Because of that, workhouse matching uses deterministic normalisation, fuzzy
matching, and transparent scoring. It does not use pgvector or any LLM.

---

## Main modules

| Module | Role |
|---|---|
| `backend/services/workhouse_entity_resolution.py` | Pipeline orchestrator |
| `backend/services/entity_resolution/normalise.py` | Name + place normalisation |
| `backend/services/entity_resolution/candidates.py` | Blocking + candidate generation |
| `backend/services/entity_resolution/scoring.py` | Multi-signal scoring |
| `backend/services/workhouse_service.py` | Excel sheet loader |
| `backend/routes/unified.py` | API surface (links enrichment) |

---

## Persisted tables

Created by `extensions.py::ensure_schema()`. All four preserve source-level
evidence and keep uncertain links reviewable rather than silently merging records.

| Table | Contents |
|---|---|
| `source_mentions` | One row per name occurrence in a source record (150 workhouse rows → 150 mentions) |
| `entity_resolution_candidates` | Scored candidate links: mention → unified_record (up to 25 per mention) |
| `workhouse_unified_links` | Final accepted workhouse→estate record links |
| `entity_resolution_decisions` | Human review decisions on borderline candidates |

---

## Full Pipeline

### Step 1 — Load workhouse data

`workhouse_service.get_workhouse()` reads `workhouse_data_final.xlsx` (two sheets):
- Sheet "1-127": `Pauper Name` (surname-first format), `Number in Register`
- Sheet "from 128": 13 fields including `Names and Surnames of Paupers`, `Electoral division`, `Sex`, `Age`, `date when admitted or born in workhouse`, `Date when died or left workhouse`

### Step 2 — Normalise each mention

`normalise.normalise_person_fields(raw_name)`:
1. Unicode normalisation (NFKD decomposition)
2. Uppercase conversion
3. Remove editorial annotations: `[?]`, `[illegible]`, `(In Lease)`, `(Sic)`
4. Expand abbreviations: `JNO→JOHN`, `WM→WILLIAM`, `JAS→JAMES`, `THOS→THOMAS`, `RD→RICHARD`, `EDWD→EDWARD`, `SAML→SAMUEL`, `ELIZH→ELIZABETH`, `MARGT→MARGARET`
5. Surname variants: `MCDONNELL/MACDONNELL→MCDONNELL`, `OBRIEN/O'BRIEN→OBRIEN`, `MCCARTHY/MACCARTHY→MCCARTHY`
6. Remove accents
7. Phonetic encoding: `jellyfish.metaphone()`

`normalise.normalise_place_name(electoral_division)`:
- Applies the same normalisation pipeline to place names
- Matches against `townland_aliases.json` for canonical resolution

### Step 3 — Build unified index

`build_unified_index()` returns all 13,707 `unified_record` rows with normalised
fields, filtered by place match (electoral_division + townland_norm) as the first
blocking pass.

### Step 4 — Generate candidates

`candidates.generate_candidates(mention, unified_index)` applies blocking to
produce up to 25 ranked candidates per mention using:

- Exact normalised name match
- Surname + forename initial match
- Phonetic surname match (Metaphone)
- Place + name combination
- Fuzzy full-name similarity (rapidfuzz token_sort_ratio)
- Year compatibility (±1 year window around event_year)

### Step 5 — Score candidates

`scoring.score_candidate(mention, candidate)` applies a 7-signal weighted formula:

| Signal | Max points | Scoring rule |
|---|---|---|
| Full name similarity (token_sort_ratio) | 10 | ≥90%: 10 pts; ≥75%: 7 pts; ≥60%: 4 pts; else: 0 |
| Exact surname | 10 | Exact match: 10 pts; Metaphone match: 7 pts; else: 0 |
| Forename match | 10 | Either side missing: 5 pts (neutral); exact: 10; ≥80%: 7; ≥60%: 4; else: 0 + conflict |
| Townland normalisation | 10 | Exact match: 10 pts; variant match: 6 pts; else: 0 |
| Birth-year alignment | 5 | Gap ≤3 yrs: 5 pts; ≤8 yrs: 3 pts; else: 0 |
| Gender | 10 | Both missing: 5 pts (neutral); exact match: 10; mismatch: 0 + conflict |
| Timeline alignment | 5 | Age-progression consistency |
| **TOTAL** | **60** | Normalised: `raw_points / 60.0 → 0.0–1.0` |

### Confidence band assignment

```
score ≥ 0.75 → CONFIRMED_MATCH   (high confidence; auto-accepted)
score ≥ 0.50 → POSSIBLE_MATCH    (medium; flagged for review)
score  < 0.50 → WEAK_CANDIDATE   (low; requires explicit review)
all signals missing → NO_MATCH
```

Hard negative rules block unsafe auto-linking:
- Impossible age/date conflict
- Incompatible gender evidence
- Irreconcilable timeline

### Step 6 — Persist

```
source_mentions table   ← one row per workhouse mention (150 total)
entity_resolution_candidates ← all scored candidates
workhouse_unified_links ← CONFIRMED_MATCH and above-threshold decisions
entity_resolution_decisions ← full audit trail
```

### Step 7 — Review

`match_review_repository.py` provides CRUD for the `match_review` table. Borderline
candidates have `review_required=1`. No web UI is currently wired up; the data is
accessible directly via the SQLite tables.

---

## No silent merge

The pipeline does not overwrite unified records. It does not collapse conflicting
facts just because names are similar. Each candidate stores:

- `supporting_evidence_json` — signals that support the match
- `missing_evidence_json` — signals that could not be evaluated (field absent)
- `conflicting_evidence_json` — signals where evidence contradicts

---

## Evidence surfaced in the unified search API

Unified search responses include workhouse enrichment:

```json
{
  "linked_workhouse_records": [...],
  "possible_workhouse_matches": [...],
  "please_check_records": [...],
  "identity_is_ambiguous": false,
  "identity_disambiguation_note": "...",
  "supporting_evidence": [...],
  "conflicting_evidence": [...]
}
```

Each candidate shows: source, source record ID, name, place, year, age,
confidence score, why it matched, what evidence is missing, conflicting evidence.

---

## Running the pipeline

Build persisted workhouse links:

```bash
python scripts/link_workhouse_records.py
```

Limited run with audit output:

```bash
python scripts/link_workhouse_records.py --limit 200 --audit-dir exports/workhouse_er
```

Validate persisted results:

```bash
python scripts/validate_workhouse_er.py
```

The validation script reports:
- Records processed / source mentions created
- Confirmed links / possible links / weak candidates / unresolved
- Records requiring review
- Top ambiguous names
- Example confirmed matches and "Please check" cases

If no golden set exists, precision/recall is reported as unavailable.

---

## Tests

```bash
pytest -q tests/test_workhouse_entity_resolution.py
```

---

## Scoring worked example

**Workhouse mention:** "Jno Murphy, Aghowle, 1851, age 35, male"
**Candidate:** John Murphy, AGHOWLE LOWER, 1851, age 37, male

| Signal | Raw calculation | Points |
|---|---|---|
| Full name similarity | token_sort_ratio("JOHN MURPHY", "JOHN MURPHY") = 100% | 10 |
| Exact surname | "MURPHY" = "MURPHY" | 10 |
| Forename | "JOHN" = "JOHN" (after JNO expansion) | 10 |
| Townland | "AGHOWLE" variant matches "AGHOWLE LOWER" | 6 |
| Birth-year | |1851-35 - 1851-37| = 2 yrs ≤ 3 | 5 |
| Gender | male = male | 10 |
| Timeline | age progression consistent | 5 |
| **Total** | | **56 / 60 = 0.93** |
| **Label** | | **CONFIRMED_MATCH** |
