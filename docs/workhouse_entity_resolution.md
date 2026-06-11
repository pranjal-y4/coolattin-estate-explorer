# Workhouse Entity Resolution

This document covers workhouse-to-unified-record matching only. It is separate
from Ask-page pgvector retrieval and does not depend on PostgreSQL or pgvector.

## Why this is separate from pgvector

Workhouse mapping is an entity-resolution problem, not a semantic retrieval
problem.

- Ask page: retrieve semantically relevant context for natural-language questions.
- Workhouse mapping: generate explicit, reviewable candidate identity links.

Because of that, workhouse matching uses deterministic normalisation, fuzzy
matching, and transparent scoring. It does not use pgvector.

## Main modules

- `backend/services/workhouse_entity_resolution.py`
- `backend/services/entity_resolution/normalise.py`
- `backend/services/entity_resolution/candidates.py`
- `backend/services/entity_resolution/scoring.py`
- `backend/services/workhouse_service.py`
- `backend/routes/unified.py`
- `frontend/static/js/main.js`

## Persisted tables

The persisted SQLite tables are created in `extensions.py`.

- `source_mentions`
- `entity_resolution_candidates`
- `workhouse_unified_links`
- `entity_resolution_decisions`

These tables preserve source-level evidence and keep uncertain links reviewable
instead of silently merging records.

## Normalisation

`backend/services/entity_resolution/normalise.py` handles:

- case and punctuation normalisation
- initials and common abbreviations such as `Jno -> John`, `Wm -> William`, `Jas -> James`
- `Mc` / `Mac` handling
- `O` / `Ó` surname variants
- place and townland normalisation
- phonetic encoding through `jellyfish.metaphone`

## Candidate generation

`backend/services/entity_resolution/candidates.py::generate_candidates(...)`
builds reviewable candidate sets using:

- exact normalised name match
- surname + forename initial match
- phonetic surname match
- fuzzy full-name similarity
- same canonical or normalised place
- nearby or variant place matching
- year compatibility

## Transparent scoring

`backend/services/entity_resolution/scoring.py::score_candidate(...)` applies
weighted evidence:

- name similarity: up to 25
- surname exact or phonetic evidence: up to 15
- place/townland evidence: up to 20
- age/birth-year compatibility: up to 15
- household overlap: up to 15
- occupation similarity: up to 5
- timeline consistency: up to 5

The final score is normalised to `0.0 - 1.0` and labelled as:

- `CONFIRMED_MATCH` for `>= 0.85`
- `POSSIBLE_MATCH` for `>= 0.65` and `< 0.85`
- `WEAK_CANDIDATE` for `>= 0.40` and `< 0.65`
- `NO_MATCH` for `< 0.40`

Hard negative rules block unsafe auto-linking, including impossible age/date
conflicts and incompatible timeline or gender evidence.

## No silent merge

The pipeline does not overwrite unified records and does not collapse
conflicting facts into one record just because names are similar.

Instead it stores:

- supporting evidence
- missing evidence
- conflicting evidence
- review-required flags

That is why uncertain candidates are surfaced to the UI instead of merged.

## “Please check these records”

Possible but unconfirmed workhouse links are returned in:

- `possible_workhouse_matches`
- `please_check_records`

The UI renders these under a dedicated section titled:

- `Please check these records`

Each candidate shows:

- source
- source record id
- name
- place
- year
- age
- confidence score
- why it matched
- what evidence is missing
- conflicting evidence

## APIs

Unified search responses now include:

- `linked_workhouse_records`
- `possible_workhouse_matches`
- `please_check_records`
- `identity_is_ambiguous`
- `identity_disambiguation_note`
- `supporting_evidence`
- `conflicting_evidence`

Legacy workhouse match lookups also reuse the persisted links when available.

## Linking and validation jobs

Build persisted workhouse links:

```bash
python scripts/link_workhouse_records.py
```

Optional limited run with audit output:

```bash
python scripts/link_workhouse_records.py --limit 200 --audit-dir exports/workhouse_er
```

Validate persisted results:

```bash
python scripts/validate_workhouse_er.py
```

The validation script prints:

- records processed
- source mentions created
- confirmed links
- possible links
- weak candidates
- unresolved records
- records requiring review
- top ambiguous names
- example confirmed matches
- example “Please check these records” cases

If no golden set exists, precision/recall is reported as unavailable rather than guessed.

## Focused tests

Run the workhouse entity-resolution tests with:

```bash
pytest -q tests/test_workhouse_entity_resolution.py
```
