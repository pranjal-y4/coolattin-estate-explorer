# 04 — Workhouse Entity Resolution

Technical reference for the subsystem that links **workhouse admission records**
(`workhouse_data_final.xlsx`) to **estate person records** (`unified_record`,
seeded from `unified_processed.csv`). This is explicitly called out in
`CLAUDE.md` as a subsystem separate from the Ask pipeline: it uses no LLM, no
pgvector, and no Ask-pipeline code path. It is deterministic, offline,
re-runnable, and fully auditable — every scored pair (not just accepted
matches) is persisted with its evidence trail.

For table DDL (`source_mentions`, `entity_resolution_candidates`,
`workhouse_unified_links`, `entity_resolution_decisions`, `match_review`) see
`02_database_schema.md` §1.8–1.11. This document covers the **algorithm and
code flow** that populates those tables, not their schema.

> **Correction to `02_database_schema.md`**: that document's §1.9 states the
> scoring model uses "seven signals: name similarity, place similarity,
> temporal plausibility, gender match, occupation overlap, family size
> consistency, household co-occurrence" with confidence-band thresholds of
> 0.85 / 0.70 / 0.50. Reading `scoring.py` directly (below) shows the actual
> implementation differs: it is a **seven-component weighted-points model**
> but the components are *full-name similarity, surname, forename, place,
> birth-year alignment, gender, and age-progression/timeline* — occupation
> overlap, family size, and household co-occurrence are **not** scored
> signals in this file (occupation overlap **is** used, but only in the
> separate, non-persisted `workhouse_service.py` matcher — see §7). The
> actual confidence-band cutoffs are **0.75 / 0.60 / 0.40**, not 0.85/0.70/0.50.
> This document documents the code as written, verified by direct reading of
> `backend/services/entity_resolution/scoring.py`.

## 1. Two independent matching systems — do not conflate them

The codebase contains **two separate** workhouse-matching implementations.
This document is primarily about the second one, but the distinction matters
because `backend/services/workhouse_service.py::get_matches_for_record()` can
silently fall back between them:

| | `workhouse_service.py` (legacy, in-memory) | `workhouse_entity_resolution.py` (this doc) |
|---|---|---|
| Persisted? | No — rebuilt in process memory every restart via `get_match_index()` | Yes — written to SQLite (`source_mentions`, `entity_resolution_candidates`, `workhouse_unified_links`) by an offline script |
| Blocking | Place-first (electoral division vs. townland/parish), then date window | Phonetic-surname / normalised-place bucket index (`build_unified_index`) |
| Name scoring | `difflib.SequenceMatcher` ratio over name-variant cross-product | `rapidfuzz.fuzz.token_sort_ratio` (falls back to `SequenceMatcher` if `rapidfuzz` is unavailable) |
| Extra signal | Occupation keyword bonus (+0.05, ranking-only, cannot change tier) | Full multi-signal weighted-points model (§4) |
| Confidence bands | High / Medium / Low, thresholds 0.80 / 0.60 / 0.60 (see §7) | CONFIRMED_MATCH / POSSIBLE_MATCH / WEAK_CANDIDATE / NO_MATCH, thresholds 0.75 / 0.60 / 0.40 |
| Used by | `/api/unified/records` **only when no persisted links exist** | `/api/unified/records` (primary path, since persisted links exist) |

`backend/routes/unified.py::api_unified_records()` has an explicit comment on
why the legacy path was retired as the primary path:

```python
# Use persisted entity-resolution links from the database.
# The legacy in-memory fuzzy index (get_match_index) is not used — it requires
# O(n×m) SequenceMatcher over 13k records and times out gunicorn workers.
```

`workhouse_service.get_matches_for_record()` itself still contains the
fallback logic (used by other call sites, e.g. any direct per-record lookup
outside the bulk `/records` listing):

```python
def get_matches_for_record(record_id: str) -> dict:
    try:
        from backend.services.workhouse_entity_resolution import (
            get_matches_for_record as _persisted_matches_for_record,
            has_persisted_links,
        )
        if has_persisted_links():
            return _persisted_matches_for_record(record_id)
    except Exception as exc:
        log.debug("workhouse_service.persisted_links_unavailable error=%s", exc)
    idx = get_match_index()
    matches = idx.get(str(record_id), [])
    return {"record_id": record_id, "count": len(matches), "matches": matches}
```

So: if `link_workhouse_records()` (§6) has never been run against the current
database, the app silently falls back to the O(n×m) legacy matcher. The rest
of this document covers the persisted pipeline.

## 2. Pipeline overview

`backend/services/workhouse_entity_resolution.py` orchestrates four stages,
calling into the `backend/services/entity_resolution/` subpackage
(`normalise.py`, `candidates.py`, `scoring.py`, re-exported via
`entity_resolution/__init__.py`):

```
build_source_mentions()          Stage 1 — mention building
        │  list[dict] (in-memory "mention" records)
        ▼
_load_unified_records()          loads + normalises unified_record rows
        │  list[dict]
        ▼
build_unified_index()            Stage 2a — blocking index (phonetic surname / place)
        │  dict[str, list[dict]]
        ▼
generate_candidates(mention, unified_records, unified_index=idx)   Stage 2b — per-mention candidate pool
        │  list[dict]  (≤ 25 candidates per mention)
        ▼
score_candidate(mention, candidate)   Stage 3 — per-pair scoring
        │  ScoreResult(score, label, evidence, conflicts, missing_evidence)
        ▼
_persist_candidate() / _insert_source_mention()   Stage 4 — persistence
        │
        ▼
source_mentions, entity_resolution_candidates, workhouse_unified_links (SQLite)
```

All four stages run inside `link_workhouse_records()`, the single public
entry point invoked by `scripts/link_workhouse_records.py`. There is no LLM
call, no network call, and no pgvector/embedding lookup anywhere in this
path — confirmed by `tests/test_workhouse_entity_resolution.py::
test_workhouse_resolution_does_not_require_pgvector`, which asserts
`build_source_mentions()` still works with `DATABASE_URL` pointed at a
nonexistent sqlite file and pgvector's `backend_status()["available"]` false.

## 3. Stage 1 — Mention building (`normalise.py` + `build_source_mentions`)

### 3.1 `build_source_mentions(limit=None)` — workhouse side

Located in `workhouse_entity_resolution.py`. For each row returned by
`workhouse_service.get_workhouse()` (the parsed Excel rows — two sheets,
`"1-127"` and `"from 128"`, see `workhouse_service.py` lines 153–226), it
builds one **mention** dict:

- `source_record_id` — `_workhouse_record_id(row, idx)`: `"{sheet}:{register_number}:{index+1}"`
  when a register number is present, else `"{sheet}:row:{index+1}"`.
- Name fields come from `normalise_person_fields(row["raw_name"], surname_first=True)`
  — workhouse raw names are stored `"Surname Forename"` (see
  `_split_workhouse_name` in `workhouse_service.py`), so `surname_first=True`
  tells the splitter which token is which.
- `normalised_place = normalise_place_name(row["electoral_division"])`.
- `canonical_townland_id = _canonical_townland_id(normalised_place)` — looked
  up against the `townland` table by exact `name`/`civil_parish` match, then
  a `LIKE '%...%'` fallback; results are memoised in a module-level
  `_TOWNLAND_ID_CACHE` dict keyed by the normalised place string (not reset
  between calls within a process).
- `event_year = _safe_int(row["_year_admitted"] or row["_year_left"])`.
- `age = _safe_int(row["age"])`; `inferred_birth_year = event_year - age`
  when both are present, else `None`.
- `household_fields` — a single space-joined string of `spouse`,
  `children_count`, `status` (non-null values only). Note this field is
  **stored** on the mention but, per §4 below, is **not** read by
  `scoring.py` — it exists for audit/display purposes and potential future
  scoring extensions, not as an active signal today.
- `occupation_norm = row["employment"].strip().upper()` — likewise stored but
  not consumed by `scoring.py`.
- `gender` — first uppercase character of `row["sex"]`, or `None`.
- `source_payload_json` — the entire original row dict, JSON-encoded
  (`json.dumps(row, ensure_ascii=True)`) — this is what later powers the
  "workhouse detail" panel via `_extract_workhouse_detail()` (§5).

### 3.2 `_load_unified_records()` — estate side

Loads every row of `unified_service.get_unified()` (13,707-row DataFrame from
`unified_processed.csv`), converts to `list[dict]`, and for each row calls
`normalise_person_fields(raw_name=clean_estate_name_field(row["canonical_name"]), forename=clean_estate_name_field(row["forename"]), surname=clean_estate_name_field(row["surname"]), surname_first=False)`.

Two differences from the workhouse side are important:

1. `clean_estate_name_field()` is applied **only** to estate records, never
   to workhouse `raw_name`. It strips editorial annotations that appear in
   the estate transcription but not the workhouse Excel: `[?]`,
   `[Next Word Illegible]`, `(In Lease)`, `(Sic)`, `(Junior)`, stray `?`
   characters, and collapses a field that is only dashes/punctuation to `""`
   (docstring example in `normalise.py` lines 44–51).
2. `surname_first=False` — the estate CSV already has separate `forename`/
   `surname` columns, so `normalise_person_fields` only falls back to
   `split_person_name(raw_name, ...)` when either `forename` or `surname` is
   blank after cleaning.

`event_year = _safe_int(row["year"])`; `age = _safe_int(row["age"] or row["age_head_of_household"])`
(falls back to household-head age when the person's own age column is
empty); `inferred_birth_year` computed the same way as the workhouse side.
`normalised_place = normalise_place_name(row["townland"] or row["townland_official_name"] or row["parish"])`.

### 3.3 Name normalisation details (`normalise.py`)

`normalise_text(value)`:
1. Unicode NFKD-normalises and strips combining marks (accent stripping).
2. Normalises curly quotes (`’`, `‘`) to straight `'`.
3. Strips everything except `[A-Za-z0-9\s'-]`.
4. Collapses whitespace, uppercases.

`_expand_forename(token)` looks up a fixed abbreviation table before
uppercasing is applied elsewhere:

```python
_FORENAME_ABBREVIATIONS = {
    "JNO": "JOHN", "WM": "WILLIAM", "JAS": "JAMES", "THOS": "THOMAS",
    "MARGT": "MARGARET", "MICHL": "MICHAEL", "PATK": "PATRICK",
    "PAT": "PATRICK", "MATTW": "MATTHEW", "EDWD": "EDWARD",
}
```
Confirmed by test: `normalise_person_fields("Doe Jno", surname_first=True)` →
`forename == "JOHN"`.

`_normalise_surname(token)` handles Irish surname-prefix variants: collapses
`"MC "`/`"MAC "` spacing, rewrites a leading `MAC` to `MC` (so `MACDONNELL` →
`MCDONNELL`), collapses `"O "`/`"O'"` prefix spacing, then applies a small
fixed lookup table:

```python
_SURNAME_VARIANTS = {
    "MCDONNELL": "MCDONNELL", "MACDONNELL": "MCDONNELL",
    "MCCARTHY": "MCCARTHY", "MACCARTHY": "MCCARTHY",
    "OBRIEN": "OBRIEN", "O BRIEN": "OBRIEN", "O'BRIEN": "OBRIEN", "Ó BRIEN": "OBRIEN",
}
```

`phonetic_code(value)` uses the **Metaphone** algorithm via the `jellyfish`
library (`jellyfish.metaphone(text)`), not Soundex or Double Metaphone —
falls back to the raw normalised text if `jellyfish` import fails, so
blocking degrades to exact-string matching rather than crashing. This is the
source of `phonetic_forename`/`phonetic_surname` on `source_mentions`.

`split_person_name(raw_name, surname_first=False)` tokenises on whitespace,
drops placeholder tokens matching `^[-.]+$`, and:
- 0 tokens → `("", "")`
- 1 token → treated as surname only: `("", normalised_surname)`
- ≥2 tokens, `surname_first=True` (workhouse convention) → first token is
  surname, remaining tokens joined (each expanded via the abbreviation table)
  as forename.
- ≥2 tokens, `surname_first=False` (estate convention) → last token is
  surname, leading tokens are forename.

`normalise_place_name(raw_place)`:
1. `normalise_text()`.
2. Strips leading `"TOWNLAND OF "`, and the standalone tokens `"CIVIL
   PARISH"` / `"PARISH OF"`.
3. Collapses whitespace.
4. Delegates to `canonical_name(text)` from `townland_service.py`, falling
   back to `normalize_townland_name(text)` from the same module if
   `canonical_name` returns falsy. (This document does not re-derive
   `townland_service.py`'s canonicalisation rules — see whichever doc covers
   the townland/geography subsystem for that.)

Test-verified example: `normalise_place_name("Townland of Coolboy (Civil Parish)") == "COOLBOY"`.

## 4. Stage 2 — Candidate generation / blocking (`candidates.py`)

### 4.1 Blocking index — `build_unified_index()`

Building candidates naively would be O(13,707 workhouse mentions × 13,707
unified records) — far too slow. `build_unified_index(unified_records)`
pre-builds a single dict keyed by two kinds of bucket:

- `f"ps:{phonetic_surname}"` → list of unified records sharing that
  Metaphone-coded surname.
- `f"pl:{normalised_place}"` → list of unified records sharing that
  normalised place string.

This is built **once** per `link_workhouse_records()` run (not once per
mention), then reused for every mention via `generate_candidates(mention,
unified_records, unified_index=unified_idx)`.

### 4.2 Per-mention candidate pool — `generate_candidates()`

For a given mention, the search pool is the union of records found in the
`ps:{mention_phonetic_surname}` bucket and the `pl:{mention_normalised_place}`
bucket (de-duplicated by `record_id`). **Only if both buckets are empty**
does it fall back to scanning the full `unified_records` list — this keeps
the typical per-mention candidate pool to "a few dozen" records rather than
13,707 (docstring: "typically reduces the search space from 13,707 to a few
dozen per mention").

Within the pool, each candidate record is tested against **six independent
strategies**; any one hit is enough to admit the candidate (the strategies
are cumulative, not exclusive — a record can match on multiple):

| Strategy tag | Condition |
|---|---|
| `exact_normalised_name` | `mention.normalised_name == candidate.normalised_name` (both non-empty) |
| `surname_plus_initial` | exact surname match **and** exact forename-initial match |
| `phonetic_surname` | exact Metaphone-surname match |
| `fuzzy_full_name` | `rapidfuzz.fuzz.token_sort_ratio(mention_name, candidate_name) >= 82.0` |
| `same_canonical_place` | exact normalised-place match |
| `variant_place` | one normalised place is a substring of the other (only checked if `same_canonical_place` did not fire) |
| `compatible_event_year` | `abs(mention.event_year - candidate.event_year) <= 10` |

A candidate with **zero** matched strategies is discarded outright (never
reaches scoring). Each surviving candidate gets `matched_strategies` (sorted
set of the tags above) and `blocking_name_ratio` (the raw fuzzy ratio,
rounded to 2 dp) attached, then the full candidate pool is:

1. Sorted descending by `(len(matched_strategies), blocking_name_ratio, 1 if "same_canonical_place" in strategies else 0)`.
2. Truncated to `max_candidates=25` (default parameter, not currently
   overridden anywhere in the codebase).

This ranking/truncation happens **before** scoring — `score_candidate` is
only ever called on the top-25 blocking-ranked candidates per mention, not
on the full match set. In practice mentions rarely produce anywhere near 25
candidates once phonetic/place blocking narrows the pool.

## 5. Stage 3 — Scoring (`scoring.py`)

`score_candidate(mention, candidate) -> ScoreResult` implements a
**seven-component additive points model**, capped at 60 raw points, then
normalised to `[0, 1]`. Component list, in the exact order the function
computes them, with the exact point values from the code:

| # | Signal | Max pts | Rule |
|---|---|---|---|
| 1 | Full name similarity | 10 | `rapidfuzz.fuzz.token_sort_ratio(normalised_name, normalised_name)`: ≥90 → 10, ≥75 → 7, ≥60 → 4, else 0 (adds "No strong full-name match" to `missing`) |
| 2 | Surname | 10 (phonetic fallback 7) | exact surname string match → 10; else exact phonetic-surname (Metaphone) match → 7; else 0 + "No surname match" |
| 3 | Forename | 10 (neutral 5 if either side blank) | if either forename is empty → +5, "Forename unknown on one side"; else ratio ≥90→10, ≥80→7, ≥60→4, else 0 + conflict `"Forename mismatch (...)"` |
| 4 | Townland/place | 10 (variant 6) | exact normalised-place match → 10; substring-contained variant → 6; either side blank → 0 + "Place data missing on one side"; both present but mismatched → 0 + "Place mismatch (Electoral Division vs townland may differ)" |
| 5 | Birth-year alignment | 5 | both `inferred_birth_year` present: gap ≤3 → 5, gap ≤8 → 3, gap >20 → conflict `"Impossible age/date conflict"` (0 pts); either side missing → "Missing age or birth-year evidence" |
| 6 | Gender | 10 (neutral 5 if either side unknown) | both present and equal → 10; both present and unequal → 0 + conflict `"Gender mismatch (...)"`; either missing → +5, "Gender unknown on one or both sides" |
| 7 | Timeline / age-progression | 5 | if year+age present on **both** sides: computes `expected_c_age = m_age + (c_year - m_year)`, compares to actual `c_age` — diff ≤2 → 5, ≤5 → 3, >30 → conflict `"Impossible timeline gap"`; else if only years present: gap ≤2 → 5, ≤10 → 2.5, >40 → conflict; else → "No event-year evidence for timeline check" |

**Note on what is *not* scored**: `occupation_norm` and `household_fields`
are present on both mention and candidate dicts (built in Stage 1) but
`scoring.py` never reads either field. There is no occupation-overlap or
household-co-occurrence signal in this scorer — that logic exists only in
the separate legacy `workhouse_service.py` matcher (§1, §7).

### 5.1 Score normalisation and confidence bands

```python
_MAX_POINTS = 60.0
score = max(0.0, min(raw / _MAX_POINTS, 1.0))
if impossible:          # any "Impossible age/date conflict" or "Impossible timeline gap" conflict
    score = min(score, 0.39)

if   score >= 0.75: label = "CONFIRMED_MATCH"
elif score >= 0.60: label = "POSSIBLE_MATCH"
elif score >= 0.40: label = "WEAK_CANDIDATE"
else:                label = "NO_MATCH"
```

The `impossible` guard is a hard ceiling: even a candidate that scores well
on every other signal is capped at 0.39 (WEAK_CANDIDATE-or-below) if it
carries an age/date impossibility — this guarantees `CONFIRMED_MATCH` and
`POSSIBLE_MATCH` can never contain a chronologically impossible pairing.
Verified by `tests/test_workhouse_entity_resolution.py::
test_age_date_contradiction_is_not_auto_linked` (mention age 70/birth 1780
vs. candidate age 5/birth 1845 → `NO_MATCH` with `"Impossible age/date
conflict"` in `conflicts`).

`ScoreResult` is a plain dataclass: `score: float, label: str, evidence:
list[str], conflicts: list[str], missing_evidence: list[str]`.

### 5.2 Confidence-band cutoffs — corrected reference table

| Label | Score range | Meaning |
|---|---|---|
| `CONFIRMED_MATCH` | `score >= 0.75` | Auto-accepted, written to `workhouse_unified_links` |
| `POSSIBLE_MATCH` | `0.60 <= score < 0.75` | Written to `workhouse_unified_links` **with `review_required=1`** |
| `WEAK_CANDIDATE` | `0.40 <= score < 0.60` | Persisted to `entity_resolution_candidates` only — **not** promoted to `workhouse_unified_links` |
| `NO_MATCH` | `score < 0.40` | Discarded entirely — not persisted anywhere (see `link_workhouse_records`: `if score_result.label == "NO_MATCH": continue`) |

These are the values actually implemented in code (0.75 / 0.60 / 0.40),
which supersede the 0.85 / 0.70 / 0.50 figures stated in
`02_database_schema.md` §1.9.

## 6. Stage 4 — Persistence (`link_workhouse_records()`)

`link_workhouse_records(*, limit=None, audit_dir=None)` in
`workhouse_entity_resolution.py` is the orchestrator:

1. `mentions = build_source_mentions(limit=limit)`.
2. `unified_records = _load_unified_records()`.
3. `unified_idx = build_unified_index(unified_records)` — built once for the
   whole run.
4. Opens one DB connection (`get_db_conn()`), then **`_clear_resolution_tables(conn)`** —
   `DELETE FROM entity_resolution_decisions`, `workhouse_unified_links`,
   `entity_resolution_candidates`, `source_mentions`, in that FK-safe order.
   This means every run of `link_workhouse_records()` is a **full rebuild**,
   not an incremental upsert — prior human review decisions in
   `entity_resolution_decisions` are wiped along with everything else. This
   is a real limitation (see §9).
5. For each mention:
   - `_insert_source_mention(conn, mention)` → `mention_id`.
   - `candidates = generate_candidates(mention, unified_records, unified_index=unified_idx)`.
   - If no candidates at all, mention counted as `unresolved_records`, loop continues.
   - For each candidate (already blocking-ranked, ≤25):
     - `score_result = score_candidate(mention, candidate)`.
     - `NO_MATCH` → skipped entirely, not persisted, not counted toward `strong_candidate_count`.
     - Anything else (`CONFIRMED_MATCH`/`POSSIBLE_MATCH`/`WEAK_CANDIDATE`) →
       `_persist_candidate(conn, mention_id, candidate, score_result)`.
     - Summary counters incremented (`confirmed_links`, `possible_links` +
       `requiring_review`, or `weak_candidates`).
     - An audit row appended to an in-memory `audit_rows` list.
   - After all candidates for this mention are scored: if zero candidates
     reached `CONFIRMED_MATCH`/`POSSIBLE_MATCH` (`strong_candidate_count ==
     0`), the mention is counted `unresolved_records`. If **more than one**
     candidate reached that tier, the mention's normalised name is tallied
     in `ambiguous_name_counter` (a `collections.Counter`), surfaced later as
     `top_ambiguous_names` (top 10 via `.most_common(10)`).
6. `conn.commit()`.
7. If `audit_dir` was passed, writes `workhouse_er_audit.csv` and
   `workhouse_er_audit.json` (only if `audit_rows` is non-empty) into that
   directory — one row per scored (non-`NO_MATCH`) candidate pair with
   `workhouse_record_id`, `candidate_unified_record_id`, `score`, `label`,
   semicolon-joined `evidence`/`conflicts`, and `review_required`.
8. Returns the `summary` dict plus `top_ambiguous_names`.

### 6.1 `_persist_candidate()` — promotion rule from candidate to link

Every scored non-`NO_MATCH` pair is inserted into
`entity_resolution_candidates` unconditionally (this is the full audit
trail — see `02_database_schema.md` §1.9). The `review_required` column on
that row is set to `1` **only** when `label == "POSSIBLE_MATCH"` (0 for
`CONFIRMED_MATCH` and `WEAK_CANDIDATE`).

Promotion into `workhouse_unified_links` (the "accepted subset" table) is a
single `if` inside `_persist_candidate`:

```python
if score_result.label in {"CONFIRMED_MATCH", "POSSIBLE_MATCH"}:
    conn.execute("""INSERT INTO workhouse_unified_links (...) VALUES (...)""", (...))
```

So **`WEAK_CANDIDATE` never reaches `workhouse_unified_links`** — it is
visible only via `entity_resolution_candidates` (e.g. for a future review UI
or manual SQL inspection), never surfaced to end users through
`/api/unified/records`. `CONFIRMED_MATCH` links get `review_required=0`;
`POSSIBLE_MATCH` links get `review_required=1`. There is no separate
"promotion" step or human-in-the-loop gate before a `CONFIRMED_MATCH`
reaches `workhouse_unified_links` — the score alone decides it at insert
time. `entity_resolution_decisions` (the human audit-log table) is not
written anywhere in this pipeline; it exists in the schema for a future/
manual review workflow but `link_workhouse_records()` never inserts into it.

### 6.2 Uniqueness

Both `entity_resolution_candidates` and `workhouse_unified_links` carry
`UNIQUE(mention_id, candidate_source_table, candidate_record_id)` /
`UNIQUE(mention_id, unified_record_id)` respectively (per
`02_database_schema.md`). Since `generate_candidates` already de-duplicates
by `record_id` within a single mention's pool, and each mention is only
processed once per run, these constraints are never actually exercised as
a conflict-resolution path in the current code — they function purely as
integrity guarantees.

## 7. `/api/unified/records` — how links surface to the frontend

`backend/routes/unified.py::api_unified_records()`:

1. Calls `unified_service.search_records(...)` for the base row set.
2. If `workhouse_entity_resolution.has_persisted_links()` is true (i.e. at
   least one row exists in `workhouse_unified_links`), calls
   `get_resolution_map([record_id, ...])` — one batched query across **all**
   requested record IDs (not one query per record; see §7.1) — else
   `resolution_map` stays `{}` and every record gets empty workhouse fields.
3. For each result row, attaches:

```python
r["linked_workhouse_records"]      # CONFIRMED_MATCH rows
r["possible_workhouse_matches"]    # POSSIBLE_MATCH rows
r["please_check_records"]          # alias of possible_workhouse_matches
r["identity_is_ambiguous"]         # bool
r["identity_disambiguation_note"]  # str | None
r["supporting_evidence"]           # sorted set of evidence strings across all matches
r["conflicting_evidence"]          # sorted set of conflict strings across all matches
r["has_workhouse_record"]          # bool(linked or possible)
r["workhouse_record_count"]        # len(linked) + len(possible)
```

The route file's module docstring lists a stale route,
`GET /api/workhouse/match/<id>`, that **does not exist** in this file (or
anywhere else `grep`-able in `backend/routes/`) — it appears to be dead
documentation left over from an earlier version of the endpoint surface.
Per-record workhouse detail is instead reached exclusively through the
`linked_workhouse_records`/`possible_workhouse_matches` fields embedded in
the bulk `/api/unified/records` response, consumed client-side by
`frontend/static/js/main.js` (`workhouseBundleFromRecord()`,
`workhouseSectionHTML()`) — no separate per-record HTTP round trip.

### 7.1 `get_resolution_map()` / `get_record_resolution()` — shape

Both functions (`workhouse_entity_resolution.py`) build the same per-record
payload shape; `get_resolution_map` batches it for N record IDs in one SQL
query (`JOIN workhouse_unified_links l ON ... JOIN source_mentions s ON
s.id = l.mention_id`, ordered by `unified_record_id, score DESC`, then
grouped in Python via `defaultdict`), explicitly to avoid "a huge IN clause"
or N+1 queries "when most records have no workhouse match" — this is what
makes bulk-loading the whole `/records` listing tractable. `get_record_resolution`
is the single-record version, used by `get_matches_for_record()`.

Per-match payload fields (identical in both functions):

```python
{
  "source": "workhouse",
  "source_record_id": ..., "name": ..., "place": ..., "year": ...,
  "age": ..., "occupation": ...,
  "confidence_score": <raw 0-1 score>,
  "confidence": "High" | "Medium" | "Low",   # via _LABEL_TO_CONFIDENCE
  "label": "CONFIRMED_MATCH" | "POSSIBLE_MATCH",
  "why_it_matched": [...evidence strings...],
  "what_evidence_is_missing": [...missing_evidence strings...],
  "conflicting_evidence": [...conflicts strings...],
  "review_required": bool,
  "workhouse_detail": {...non-null fields from source_payload_json...},
}
```

`_LABEL_TO_CONFIDENCE = {"CONFIRMED_MATCH": "High", "POSSIBLE_MATCH":
"Medium", "WEAK_CANDIDATE": "Low", "NO_MATCH": "Low"}` — note
`WEAK_CANDIDATE`/`NO_MATCH` never actually appear here in practice since
only `workhouse_unified_links` rows (which are always `CONFIRMED_MATCH` or
`POSSIBLE_MATCH`) are queried by `_rows_for_record`.

`_extract_workhouse_detail(source_payload_json)` decodes the mention's full
original Excel row and filters out: keys starting with `_` (the private
`_year_admitted`/`_year_left` parse fields), `{"raw_name",
"source_record_id", "forename", "surname"}` (already surfaced as top-level
fields), `None` values, and junk placeholder values (`""`, `'"'`, `"'"`,
`","`, `"."`, `"-"`, `"--"`).

### 7.2 Ambiguity detection

`ambiguous = len(set(mention_ids)) != len(mention_ids) or len(possible) > 1`.
The first clause (`set` size differs from list size) can only trigger if the
same `mention_id` appears twice among the joined rows, which given the
`UNIQUE(mention_id, unified_record_id)` constraint on `workhouse_unified_links`
should not happen for a single `unified_record_id` — in practice this
resolves to just `len(possible) > 1`: **two or more distinct workhouse
mentions both scored `POSSIBLE_MATCH` against the same estate record**. When
`ambiguous` is true, `identity_disambiguation_note` is set to a fixed string:
*"Multiple plausible workhouse links were found for this identity. Please
review the possible matches before treating them as the same person."*
This is asserted directly by
`test_api_returns_please_check_records_and_ambiguity`.

### 7.3 `/api/unified/workhouse-by-townland`

A separate, simpler endpoint (also in `unified.py`) for a townland-scoped
view: joins `source_mentions` → `entity_resolution_candidates` →
`unified_record` filtered to `UPPER(ur.townland_norm) = <townland>` and
`erc.label IN ('CONFIRMED_MATCH', 'POSSIBLE_MATCH')`, ordered by score
descending, `LIMIT 30`. It separately returns up to 20 **unlinked**
`source_mentions` whose `normalised_place`/`raw_place` text-contains the
requested townland (`LIKE '%TOWNLAND%'`), so a caller can see both resolved
and unresolved workhouse mentions for a place at once. This route queries
`entity_resolution_candidates` directly (not `workhouse_unified_links`), so
it is the only place in the codebase that can surface `WEAK_CANDIDATE`-tier
pairs if the `label IN (...)` filter were relaxed — as written it only
returns `CONFIRMED_MATCH`/`POSSIBLE_MATCH`, same as everywhere else.

## 8. Developer-facing scripts

### 8.1 `scripts/link_workhouse_records.py`

```
python scripts/link_workhouse_records.py [--limit N] [--audit-dir DIR]
```

Thin CLI wrapper: builds the Flask app via `create_app()`, enters its app
context, calls `link_workhouse_records(limit=args.limit or None,
audit_dir=Path(args.audit_dir))` (default `audit_dir="exports/workhouse_er"`),
and prints the returned summary dict as JSON. This is the **only** way the
persisted entity-resolution tables get populated — it is not invoked by
`backend/jobs/full_ingest.py` or any startup hook; a developer must run it
explicitly (or via a slash command) after (re)seeding `unified_record` and
whenever `workhouse_data_final.xlsx` changes. Because Stage 4 always calls
`_clear_resolution_tables()` first, re-running it is safe/idempotent for the
scored-links tables, but it **destroys** any rows previously written to
`entity_resolution_decisions` (human review decisions) — there is currently
no mechanism to preserve those across a re-run (see §9).

`--limit N` truncates the workhouse row list before mention-building — a
fast-iteration flag for testing on a subset rather than all ~thousands of
rows.

### 8.2 `scripts/validate_workhouse_er.py`

```
python scripts/validate_workhouse_er.py
```

Calls `validation_summary(example_limit=10)` (no arguments) and pretty-prints
counts and examples to stdout. `validation_summary()` (in
`workhouse_entity_resolution.py`) computes, straight from the already-
persisted tables (does **not** re-run scoring):

- `source_mentions_created`, `confirmed_links`, `possible_links`,
  `weak_candidates`, `requiring_review` — plain `COUNT(*)` queries with
  label/table filters.
- `unresolved_records = max(source_mentions_created - COUNT(DISTINCT
  mention_id FROM workhouse_unified_links), 0)`.
- `top_ambiguous_names` — mentions whose `entity_resolution_candidates`
  rows include more than one `CONFIRMED_MATCH`/`POSSIBLE_MATCH` candidate,
  grouped and counted, top 10.
- `confirmed_examples` / `please_check_examples` — up to `example_limit`
  sample rows each from `workhouse_unified_links` joined to
  `source_mentions`, ordered by score descending.

The script's final printed line is a literal string:
`"precision_recall: no golden set configured"` — **the gold-standard CSV at
`eval/er_gold.csv` (§8.3) is not wired into this script.** There is no
automated precision/recall computation against `er_gold.csv` anywhere in the
codebase as of this reading; the gold file exists but is not consumed by any
script or test found in this repository. This is a known gap (see §9).

### 8.3 `eval/er_gold.csv` — hand-labelled ground truth

35 data rows (36 lines including header). Columns:

```
gold_id, wh_raw_name, wh_place_ed, wh_year, wh_age, wh_birth_year,
u_record_id, u_canonical_name, u_townland, u_year, u_age, u_birth_year,
gold_label, confidence, labelling_rationale
```

`gold_label` values observed: `TRUE_MATCH`, `FALSE_MATCH`, `UNCERTAIN`.
`confidence` values observed: `HIGH`, `MEDIUM`. `labelling_rationale` is a
free-text justification written by the human labeller, e.g. (row `G04`):
*"Same forename (Patrick=Pat); phonetic surname match (KINSLA); same
townland; birth-year gap 7; occupation match"*, and a deliberately
hard-negative example (row `G31`): *"Different forename (Robert ≠ George);
same surname+place; no year/age on either side to confirm"* labelled
`FALSE_MATCH`. This is a manually curated stress-test set (true matches,
clear non-matches with matching surname+place as distractors, and genuinely
ambiguous cases) intended to validate the scorer's precision/recall — but as
noted in §8.2, nothing in the codebase currently runs the pipeline against
it automatically. A developer wanting to validate `scoring.py` against this
set would need to write that harness themselves (e.g. load each gold row,
build a synthetic mention/candidate pair, call `score_candidate()` directly,
and compare `label` against `gold_label`).

## 9. Known limitations / gaps (from direct code reading)

- **Full rebuild on every run** — `_clear_resolution_tables()` wipes
  `entity_resolution_decisions` along with the scored-candidate tables on
  every `link_workhouse_records()` invocation. Any human review decisions
  recorded in `entity_resolution_decisions` would be lost on re-run; the
  pipeline has no incremental-update or decision-preserving mode.
- **`entity_resolution_decisions` is never written by this pipeline.** The
  table exists in the schema (`02_database_schema.md` §1.11) but no function
  in `workhouse_entity_resolution.py`, `candidates.py`, or `scoring.py`
  inserts into it — it is schema-ready for a future manual-review UI that
  does not yet exist in this codebase.
- **`eval/er_gold.csv` is not wired into any automated check.**
  `scripts/validate_workhouse_er.py` explicitly prints "no golden set
  configured" rather than computing precision/recall against it.
- **Scoring ignores occupation and household fields.** Both `occupation_norm`
  and `household_fields` are computed and stored on every mention/candidate
  dict in Stage 1, but `scoring.py` never reads them — they are present for
  potential future scoring extensions or for display, not currently used as
  matching evidence in the persisted pipeline (contrast with the legacy
  `workhouse_service.py` matcher, which *does* apply a small occupation
  bonus — see §1).
- **Two divergent name-similarity libraries in play.** `candidates.py` and
  `scoring.py` both use `rapidfuzz.fuzz.token_sort_ratio` with a
  `difflib.SequenceMatcher`-ratio-×100 fallback if `rapidfuzz` is not
  importable — there is no Jaro-Winkler anywhere in this pipeline (the task
  brief's assumption of Jaro-Winkler is not borne out by the code).
- **Blocking can under-generate candidates for mentions with sparse
  phonetic/place data.** If a mention has neither a resolvable phonetic
  surname nor a normalised place (e.g. name and electoral-division fields
  both blank/unparseable), `generate_candidates` falls back to scanning the
  **entire** `unified_records` list for that one mention — a potential
  performance cliff for rows with missing data, though bounded by
  `max_candidates=25` for what gets scored/persisted afterward.
- **Ambiguity is name-driven, not identity-driven.** `strong_candidate_count
  > 1` (multiple `CONFIRMED_MATCH`/`POSSIBLE_MATCH` candidates for one
  mention) is tracked as "ambiguous" purely by count — there is no
  secondary disambiguation step (e.g. preferring the higher-scoring
  candidate and demoting the other), so a mention can legitimately end up
  linked to more than one `unified_record` simultaneously; this is
  surfaced to the end user as `identity_is_ambiguous` / `please_check_records`
  rather than resolved automatically (§7.2) — a deliberate design choice
  (favouring recall and transparency over forcing a single best guess) but
  worth flagging as a source of duplicate-looking UI entries.
- **Stale route reference.** `backend/routes/unified.py`'s module docstring
  lists `GET /api/workhouse/match/<id>`, which does not exist as a route in
  that file (§7) — leftover documentation from a prior implementation.
- **`_TOWNLAND_ID_CACHE` is process-global and never invalidated** — if the
  `townland` table changes mid-process (e.g. via a concurrent ingest), a
  stale `canonical_townland_id` could be cached for a given normalised place
  string until the process restarts.

## 10. Dependencies

- `rapidfuzz>=3.14` (`requirements.txt`) — `fuzz.token_sort_ratio` used in
  both `candidates.py` (blocking strategy `fuzzy_full_name`, threshold 82.0)
  and `scoring.py` (name/forename similarity signals). Falls back to
  `difflib.SequenceMatcher` (stdlib) if unavailable.
- `jellyfish>=1.0` (`requirements.txt`) — `jellyfish.metaphone()` for
  `phonetic_code()` in `normalise.py`. Falls back to the plain normalised
  string if unavailable (degrading blocking to exact-match only).
- No LLM client, no `voyage_embeddings`/`local_embeddings`, no
  `ask_pgvector`, no network I/O anywhere in `workhouse_entity_resolution.py`
  or the `entity_resolution/` subpackage — confirmed by direct reading of
  all four files and by
  `test_workhouse_resolution_does_not_require_pgvector`.

## 11. File map

| File | Role |
|---|---|
| `backend/services/workhouse_entity_resolution.py` | Orchestrator: mention building, unified-record loading, `link_workhouse_records()`, read APIs (`get_record_resolution`, `get_resolution_map`, `get_matches_for_record`, `validation_summary`, `has_persisted_links`) |
| `backend/services/entity_resolution/normalise.py` | Name/place normalisation, Metaphone phonetic coding, editorial-annotation stripping |
| `backend/services/entity_resolution/candidates.py` | Blocking index (`build_unified_index`) and per-mention candidate generation (`generate_candidates`) |
| `backend/services/entity_resolution/scoring.py` | Seven-signal weighted scoring (`score_candidate`), confidence-band labelling |
| `backend/services/entity_resolution/__init__.py` | Re-exports the above as the package's public surface |
| `backend/services/workhouse_service.py` | Legacy in-memory place-first matcher (`get_match_index`); Excel loader (`get_workhouse`); fallback path when no persisted links exist |
| `backend/services/unified_service.py` | `get_unified()` — source DataFrame for the estate side of entity resolution |
| `backend/routes/unified.py` | `/api/unified/records` (attaches resolution results), `/api/unified/workhouse-by-townland` |
| `scripts/link_workhouse_records.py` | CLI entry point to (re)build the persisted tables |
| `scripts/validate_workhouse_er.py` | CLI summary/inspection tool over already-persisted results |
| `eval/er_gold.csv` | 35-row hand-labelled ground truth (TRUE_MATCH/FALSE_MATCH/UNCERTAIN); not currently wired into any automated check |
| `tests/test_workhouse_entity_resolution.py` | Unit tests for normalisation, candidate generation, scoring thresholds, the conflict-capping rule, and the `/api/unified/records` ambiguity/please-check-records behaviour |
