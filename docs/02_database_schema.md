# 02 — Database Schema

Full reference for every table in `coolattin.db`, where each is created,
what it stores, and which repository/service owns it. See
`01_architecture_overview.md` §5 for the connection singleton and PRAGMA
settings; this document is scope-limited to schema shape and ownership.

No ORM is used anywhere (`CLAUDE.md` explicitly forbids adding one). Every
table maps to hand-written SQL in `backend/repositories/*.py` or, for the
four tables noted in §3, inline SQL inside the service that owns them.

## 1. Tables created by `extensions.py::ensure_schema()`

These are created/migrated on **every process start**, unconditionally,
before any request is served (see `01_architecture_overview.md` §5.1).

### 1.1 `townland` — canonical place reference

The root entity of the whole system. Every census, clearances, heritage,
graph, and (indirectly, via `townland_norm` text matching) unified-record
row is scoped to a townland.

```sql
CREATE TABLE townland (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id           TEXT,                   -- UUID surrogate key
    name                TEXT NOT NULL,          -- canonical UPPERCASE English name
    qualifier           TEXT,                   -- locational qualifier: UPPER/LOWER/etc.
    logainm_id          TEXT,                   -- logainm.ie place identifier
    name_gaelic         TEXT,
    barony              TEXT,
    civil_parish        TEXT,
    electoral_division  TEXT,
    placename_theme     TEXT,
    description         TEXT,
    td_id               TEXT,                   -- TD_ID from estate GeoJSON
    guid                TEXT,                   -- GUID from estate GeoJSON
    area_sqm            REAL,
    kg_uri              TEXT,                   -- VRTI KG subject URI
    wkt_geometry        TEXT,                   -- boundary polygon, WKT
    centroid_lat        REAL,
    centroid_lon        REAL,
    county              TEXT,
    osm_id              TEXT,
    osi_id              TEXT,
    vrti_id             TEXT,
    images_json         TEXT DEFAULT '[]',
    links_json          TEXT DEFAULT '[]',
    geometry_flag       TEXT,                   -- quality flags from geometry validation
    source              TEXT DEFAULT 'json',    -- 'json' | 'kg' | 'manual'
    created_at          TEXT DEFAULT (datetime('now')),
    updated_at          TEXT DEFAULT (datetime('now'))
)
```

- `id` is the internal foreign-key target used by every other table
  (`census_record.townland_id`, etc.) — a plain autoincrement surrogate.
- `entity_id` is a separate UUID identity, introduced in the v2 migration
  (`01_architecture_overview.md` §5.2), used as the join key for
  cross-source reconciliation (`townland_xref.entity_id`,
  `field_provenance.entity_id`) so that identity is stable even if the
  `id` autoincrement sequence differs across a fresh ingest vs. an
  incrementally-migrated database.
- `name` has **no** `UNIQUE` constraint in the v2 schema (deliberately — see
  the migration note), so two different real-world townlands with identical
  names in different baronies are representable as two rows.
- `images_json` / `links_json` store JSON-encoded arrays as TEXT — SQLite
  has no native array/JSON type, so these are parsed/serialised in Python at
  the model boundary (`Townland.images`, `Townland.links` in
  `census_models.py`).
- Populated by: `backend/jobs/full_ingest.py` (GeoJSON pass +
  VRTI-enrichment pass) and `backend/repositories/townland_repository.py::upsert`.
  Full ingest flow is documented in `03_data_ingestion_and_refresh.md`.

Indexes: `civil_parish`, `barony`, `county`, `kg_uri`, `entity_id`.

### 1.2 `townland_xref` — cross-source identity map

```sql
CREATE TABLE IF NOT EXISTS townland_xref (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id        TEXT NOT NULL,
    source           TEXT NOT NULL,          -- 'geojson' | 'kg' | 'reference' | 'manual'
    source_record_id TEXT NOT NULL,          -- TD_ID, kg_uri, townlands.ie URL, etc.
    confidence       REAL,
    match_method     TEXT,                   -- 'exact_id' | 'name_geo' | 'manual'
    created_at       TEXT DEFAULT (datetime('now')),
    UNIQUE(source, source_record_id)
)
```

Maps a `(source, source_record_id)` pair — e.g. `("kg",
"https://vrti.ie/place/123")` — to a single canonical `entity_id`. This is
what lets one estate townland be linked to multiple external identifiers
(a KG URI, a townlands.ie reference URL, a logainm ID) without duplicating
rows in `townland` itself, and is the mechanism `field_provenance` (§1.3)
and the entity resolution pipeline in `townland_service.py` rely on for
"have we already matched this external record to a townland?" lookups.
Owned by `backend/repositories/match_review_repository.py::add_xref` /
`get_xrefs_for_entity`.

### 1.3 `match_review` — human review queue for uncertain townland pairs

```sql
CREATE TABLE IF NOT EXISTS match_review (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    townland_id_a    INTEGER NOT NULL REFERENCES townland(id),
    townland_id_b    INTEGER NOT NULL REFERENCES townland(id),
    score            REAL NOT NULL,
    score_breakdown  TEXT,                   -- JSON feature vector
    status           TEXT DEFAULT 'pending', -- 'pending' | 'confirmed' | 'rejected'
    reviewer_note    TEXT,
    created_at       TEXT DEFAULT (datetime('now')),
    reviewed_at      TEXT
)
```

When the townland entity-resolution scorer (`townland_service.py`) produces
a candidate pair whose confidence falls in an ambiguous band (not
confidently the same townland, not confidently distinct), it is queued here
via `match_review_repository.enqueue()` rather than auto-merged. A human
reviewer calls `apply_decision(match_id, decision, note)`, and if the
decision is `"confirmed"`, `_link_confirmed_pair()` merges the two
townlands' identifiers into one `entity_id`. `quality_summary()` exposes
aggregate counts (pending/confirmed/rejected) for a data-quality dashboard.

### 1.4 `field_provenance` — field-level survivorship

```sql
CREATE TABLE IF NOT EXISTS field_provenance (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id        TEXT NOT NULL,
    field_name       TEXT NOT NULL,
    field_value      TEXT,
    source           TEXT NOT NULL,
    source_record_id TEXT,
    rule             TEXT,                   -- e.g. 'kg_authoritative' | 'first_non_null'
    created_at       TEXT DEFAULT (datetime('now')),
    UNIQUE(entity_id, field_name)
)
```

Replaces a naive "last write wins" or `COALESCE`-chain upsert with an
explicit record of *which source won, and why* for every individual field
on a townland. E.g. `centroid_lat` might be won by `source='kg'` under rule
`'kg_authoritative'` (KG geometry trusted over GeoJSON), while
`description` might be won by `source='geojson'` under rule
`'first_non_null'`. This is what lets the system answer "why does this
townland show this particular barony name?" — a direct implementation of
the provenance requirement called out in `CLAUDE.md`'s dissertation-grade
reproducibility goals. Written by `match_review_repository.record_provenance()`,
called from the ingest reconciliation logic in `full_ingest.py`.

### 1.5 `census_record`

```sql
CREATE TABLE IF NOT EXISTS census_record (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    townland_id     INTEGER NOT NULL REFERENCES townland(id),
    year            INTEGER NOT NULL,
    male            INTEGER,
    female          INTEGER,
    total           INTEGER,
    inhabited       INTEGER,
    uninhabited     INTEGER,
    source          TEXT DEFAULT 'json',
    kg_uri          TEXT,
    last_synced_at  TEXT DEFAULT (datetime('now')),
    created_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(townland_id, year)
)
```

One row per townland × year. `UNIQUE(townland_id, year)` makes every
`upsert_many` call in `census_repository.py` an `INSERT ... ON CONFLICT DO
UPDATE`-style operation (implemented as `INSERT OR REPLACE`, see the
repository for the exact SQL). Two distinct populations of rows share this
one table, distinguished by `source`:

- **Standard census years** (1841, 1851, 1861, 1871, 1881, 1891) — from
  VRTI KG, `source='kg'`, `male`/`female`/`inhabited`/`uninhabited` all
  populated, `kg_uri` set to the KG entity URI.
- **Estate survey years** (1827, 1839, 1848, 1850, 1860, 1868) — from the
  estate GeoJSON, `source='json'`, only `total` populated (the estate
  surveys did not break population down by sex or dwelling occupancy).

`CensusRecord.__post_init__` (in `census_models.py`) recomputes `total =
male + female` whenever `total` is not explicitly set and at least one of
`male`/`female` is present — a defensive normalisation applied at the
dataclass boundary, not in SQL.

Indexes: `year`, `(townland_id, year)`.

### 1.6 `clearances_record`

```sql
CREATE TABLE IF NOT EXISTS clearances_record (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    townland_id     INTEGER NOT NULL REFERENCES townland(id),
    year            INTEGER NOT NULL,
    count           INTEGER,
    source          TEXT DEFAULT 'json',
    created_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(townland_id, year)
)
```

Estate eviction counts, one row per townland × year, years 1847–1856
(`Clearances_1847` … `Clearances_1856` columns in the source GeoJSON).
Single-source (`source='json'` always) — there is no KG equivalent of this
dataset, unlike census.

### 1.7 `refresh_state`

```sql
CREATE TABLE IF NOT EXISTS refresh_state (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_key     TEXT NOT NULL UNIQUE,
    last_synced_at  TEXT NOT NULL,
    source          TEXT,
    query_hash      TEXT,
    record_count    INTEGER DEFAULT 0,
    export_file     TEXT
)
```

One row per named dataset (e.g. `"wicklow_census"`,
`"wicklow_census_1851"`, `HERITAGE_SEED_KEY`, `UNIFIED_SEED_KEY`) tracking
when it was last refreshed from an external source and how many records
resulted. `dataset_key()` on `CensusFilters` (see `census_models.py`)
derives the key from the active filter scope, so a request for just year
1851 and a request for all years are tracked as independently-stale
datasets. `query_hash` lets a caller skip re-seeding when the source file's
content fingerprint (mtime + size, or an explicit hash) hasn't changed —
used by the lazy heritage/unified seeders (§3) to avoid re-parsing a 4+ MB
CSV/GeoJSON on every restart. `refresh_state_repository.get()` computes
`is_stale` (not stored — derived at read time) by comparing
`last_synced_at` against a caller-supplied `stale_after_days` threshold,
which is where `Config.CENSUS_STALE_AFTER_DAYS` /
`TOWNLAND_STALE_AFTER_DAYS` ultimately get applied.

### 1.8 `source_mentions` — workhouse entity-resolution input

```sql
CREATE TABLE IF NOT EXISTS source_mentions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    source_table        TEXT NOT NULL,
    source_record_id    TEXT NOT NULL UNIQUE,
    raw_name            TEXT,
    normalised_name     TEXT,
    forename            TEXT,
    surname             TEXT,
    phonetic_forename   TEXT,
    phonetic_surname    TEXT,
    raw_place           TEXT,
    normalised_place    TEXT,
    canonical_townland_id INTEGER REFERENCES townland(id),
    event_year          INTEGER,
    age                 INTEGER,
    inferred_birth_year INTEGER,
    occupation          TEXT,
    household_fields    TEXT,               -- JSON
    source_payload_json TEXT,               -- full original record, JSON
    created_at          TEXT DEFAULT (datetime('now')),
    updated_at          TEXT DEFAULT (datetime('now'))
)
```

One row per name occurrence in a workhouse source record — the "mention"
layer of the three-layer identity model (Mention → Person → Factoid)
referenced in `CLAUDE.md`. `phonetic_forename`/`phonetic_surname` (Metaphone
codes) are what the entity-resolution blocking step joins on before running
expensive pairwise scoring. Full detail in
`04_workhouse_entity_resolution.md`. Index: `(source_table,
source_record_id)`.

### 1.9 `entity_resolution_candidates`

```sql
CREATE TABLE IF NOT EXISTS entity_resolution_candidates (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    mention_id            INTEGER NOT NULL REFERENCES source_mentions(id) ON DELETE CASCADE,
    candidate_source_table TEXT NOT NULL DEFAULT 'unified_record',
    candidate_record_id   TEXT NOT NULL,
    candidate_name        TEXT,
    candidate_place       TEXT,
    candidate_year        INTEGER,
    score                 REAL NOT NULL,
    label                 TEXT NOT NULL,     -- CONFIRMED_MATCH | POSSIBLE_MATCH | WEAK_CANDIDATE | NO_MATCH
    evidence_json         TEXT DEFAULT '[]',
    conflicts_json        TEXT DEFAULT '[]',
    missing_evidence_json TEXT DEFAULT '[]',
    review_required       INTEGER DEFAULT 0,
    created_at            TEXT DEFAULT (datetime('now')),
    updated_at            TEXT DEFAULT (datetime('now')),
    UNIQUE(mention_id, candidate_source_table, candidate_record_id)
)
```

Every scored candidate pair, not just accepted ones — this is the full
audit trail of the scoring model. **Correction:** an earlier draft of this
section stated seven signals with 0.85/0.70/0.50 thresholds; verified
against the actual `entity_resolution/scoring.py` code
(`04_workhouse_entity_resolution.md`), the real model scores **name
similarity (full/surname/forename via `rapidfuzz.fuzz.token_sort_ratio`,
not Jaro-Winkler), place, birth-year alignment, gender, and
timeline/age-progression** — occupation overlap, family-size consistency,
and household co-occurrence are stored as columns but **not** scored by
the current code. The real confidence-band thresholds are **0.75 /
0.60 / 0.40**, not 0.85/0.70/0.50. See `04_workhouse_entity_resolution.md`
for the full weighted-points formula. Index:
`(mention_id, label, review_required)`.

### 1.10 `workhouse_unified_links` — accepted matches

```sql
CREATE TABLE IF NOT EXISTS workhouse_unified_links (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    mention_id              INTEGER NOT NULL REFERENCES source_mentions(id) ON DELETE CASCADE,
    unified_record_id       TEXT NOT NULL,
    score                   REAL NOT NULL,
    label                   TEXT NOT NULL,
    review_required         INTEGER DEFAULT 0,
    supporting_evidence_json TEXT DEFAULT '[]',
    conflicting_evidence_json TEXT DEFAULT '[]',
    missing_evidence_json   TEXT DEFAULT '[]',
    created_at              TEXT DEFAULT (datetime('now')),
    updated_at              TEXT DEFAULT (datetime('now')),
    UNIQUE(mention_id, unified_record_id)
)
```

The final, promoted subset of `entity_resolution_candidates` — one row per
workhouse mention that has an accepted link to a `unified_record`. This is
what `/api/unified/records` reads to surface "this person also appears in
the workhouse admission book" cross-references in the UI. Index:
`(unified_record_id, label, review_required)`.

### 1.11 `entity_resolution_decisions` — human review audit log

```sql
CREATE TABLE IF NOT EXISTS entity_resolution_decisions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id  INTEGER NOT NULL REFERENCES entity_resolution_candidates(id) ON DELETE CASCADE,
    decision      TEXT NOT NULL,
    reviewer_note TEXT,
    decided_at    TEXT DEFAULT (datetime('now'))
)
```

Append-only log of human accept/reject decisions against individual
candidates in the `POSSIBLE_MATCH`/`WEAK_CANDIDATE` bands. Distinct from
`match_review` (§1.3), which is the *townland*-pair review queue —
this table is the *person*-pair review queue.

### 1.12 `graph_nodes` / `graph_edges` — GraphRAG substrate

```sql
CREATE TABLE IF NOT EXISTS graph_nodes (
    node_id     TEXT PRIMARY KEY,
    label       TEXT NOT NULL,
    name        TEXT,
    props       TEXT,      -- JSON
    community   TEXT,
    embedding   BLOB        -- raw float32 vector bytes
);
CREATE INDEX IF NOT EXISTS idx_gn_label ON graph_nodes(label);

CREATE TABLE IF NOT EXISTS graph_edges (
    src         TEXT NOT NULL REFERENCES graph_nodes(node_id),
    dst         TEXT NOT NULL REFERENCES graph_nodes(node_id),
    rel_type    TEXT NOT NULL,
    props       TEXT,       -- JSON
    PRIMARY KEY (src, dst, rel_type)
);
CREATE INDEX IF NOT EXISTS idx_ge_src ON graph_edges(src, rel_type);
CREATE INDEX IF NOT EXISTS idx_ge_dst ON graph_edges(dst, rel_type);
```

The persisted form of the in-process property graph — 49,081 nodes, 64,308
edges at last build. `embedding` stores a dense vector (BAAI/bge-large-en-v1.5)
as raw bytes. **Correction:** an earlier draft of this section stated
768-dim, one-per-community; verified against `local_embeddings.py`'s
`BGE_OUTPUT_DIMENSION` assertion and `scripts/build_graph.py`'s
`EMBED_LABELS` set, the real vectors are **1024-dim** and are stored **per
individual retrievable node** (Person, Townland, CivilParish,
EmigrationEvent, EvictionEvent) rather than one per community summary — see
`10_knowledge_graph_retrieval.md` for the full embedding/community
pipeline. Built entirely
offline by `scripts/build_graph.py`, which truncates and repopulates both
tables in one run; `extensions.py` only ensures the tables *exist* — it
never writes rows into them. At app startup, `graphrag.py` loads the full
contents of both tables into an in-memory NetworkX `MultiDiGraph` once
(module-level singleton, thread-safe lazy init) so that Ask-pipeline BFS
traversal never touches SQLite on the hot path.

### 1.13 Indexes created by `ensure_schema()`

```
idx_townland_civil_parish, idx_townland_barony, idx_townland_county,
idx_townland_kg_uri, idx_townland_entity_id,
idx_census_year, idx_census_townland_year,
idx_clearances_year, idx_clearances_tl_year,
idx_xref_entity_id, idx_match_review_status
```

Plus the ones embedded in `_SUPPORT_TABLES` (§1.8–1.10) and `graph_nodes`/
`graph_edges` (§1.12).

## 2. Repository layer — what queries which table

| Repository | Table(s) owned | Notable functions |
|---|---|---|
| `townland_repository.py` | `townland` | `find_by_name`, `find_by_entity_id`, `upsert` (insert-or-update keyed on `entity_id`/`name`), `upsert_many`, `save_kg_cache` (writes KG-sourced fields onto an existing row), `get_or_create` |
| `census_repository.py` | `census_record` | `find(filters: CensusFilters)`, `find_townland_detail`, `get_summary`, `upsert_many` |
| `clearances_repository.py` | `clearances_record` | `find_by_townland`, `get_summary_by_townland`, `upsert_many` |
| `match_review_repository.py` | `match_review`, `townland_xref`, `field_provenance`, `entity_resolution_decisions` (writes) | `enqueue`, `apply_decision` → `_link_confirmed_pair`, `add_xref`, `record_provenance`, `quality_summary` |
| `refresh_state_repository.py` | `refresh_state` | `get(dataset_key, stale_after_days)` (computes `is_stale`), `upsert` |

`backend/models/census_models.py` defines the dataclasses these repositories
return: `Townland`, `CensusRecord`, `ClearancesRecord`, `RefreshState`,
`CensusFilters`, `CensusMeta`, `CensusResponse`. Each has a `to_dict()` used
at the JSON-serialisation boundary in route handlers — repositories never
return raw `sqlite3.Row` objects to callers outside themselves (`_row_to_model`
is the private conversion helper in each file).

## 3. Lazily-created tables (not in `extensions.py`)

These four tables are **not** part of `ensure_schema()`. Each is created by
a `_ensure_*_schema()` / `CREATE TABLE IF NOT EXISTS` call inside the
service that owns it, the first time that service is actually used in a
process. This keeps `extensions.py` focused on the core
townland/census/clearances/entity-resolution schema and lets the
Ask-pipeline-specific tables evolve independently.

### 3.1 `unified_record` — the primary 13,707-row estate dataset

Created by `ask_service.py::_ensure_unified_record_schema()`. Starts from a
minimal 5-column `CREATE TABLE IF NOT EXISTS` (`id`, `record_id`,
`unique_id_no`, `year`, `month`), then the function walks a dict of ~55
`required_columns` and issues one `ALTER TABLE ... ADD COLUMN` per column
missing from `PRAGMA table_info(unified_record)`. This additive-migration
pattern means the table can gain new columns across deployments without a
destructive rebuild. Columns fall into several groups:

- **Source reference**: `nli_ref`, `court_session`, `vol`, `page`, `estate_reference_no`, `estate`
- **Place**: `townland_as_shown`, `townland_official_name`, `townland`, `townland_norm`, `parish`
- **Person**: `surname`, `forename`, `canonical_name`, `occupation`, `age`, `gender`, `role`
- **Legal/tenancy**: `legal_action`, `acres`, `acres_2`, `acres_irish`, `acres_english`, `holding_acres`, `rent_owed`, `arrears`, `holding_on_fitzw_estate`, `holding_on_estate`, `mountains_in_common`
- **Household composition**: `sons`, `daughters`, `servants_male`, `servants_female`, `other_males_in_household`, `other_famales_in_household` *(sic — typo preserved from the source CSV header)*, `children_count`, `family_size_estimate`, `age_head_of_household`, `age_wife_widow_of_head_of_household`, `relationship_to_head_of_household`, `household_list`
- **Chief/under-tenant names** (both original-spelling and normalised variants): `chief_tenant_surname_original`, `chief_tenant_forename_original`, `under_tenant_surname_original`, `under_tenant_forename_original`, `chief_tenant_surname`, `chief_tenant_forename`, `under_tenant_surname`, `under_tenant_forename`
- **Emigration**: `ship_name`, `departure`, `arrival`
- **Derived flags** (computed at ingest, not from source): `is_widow`, `is_canada_destination`, `has_emigration_record`, `has_eviction_record`, `has_tenancy_record` — all `INTEGER DEFAULT 0`, used as fast boolean filters by the Ask pipeline's slot-fill compiler instead of re-deriving them from `role`/`legal_action`/`ship_name` on every query.
- `family_key` — a computed grouping key for reconstructing household units across rows.

Seeded from `frontend/static/data/unified_processed.csv` (13,707 rows) via
`_bulk_insert()`, which does one `executemany()` against a fixed 63-column
`INSERT`. Re-seeding is gated the same way as `heritage_feature` (§3.2): a
`fingerprint` of the source file is compared against `refresh_state`'s
stored `query_hash` under key `UNIFIED_SEED_KEY`, so a re-import only
happens when the CSV actually changes. Full seeding/ingest flow in
`03_data_ingestion_and_refresh.md`; how the Ask pipeline queries this table
is in `05_ask_pipeline_default.md` and `08_semantic_layer.md`.

### 3.2 `heritage_feature` — NMS heritage monuments

Created by `ask_service.py::_ensure_heritage_feature_seeded()`:

```sql
CREATE TABLE IF NOT EXISTS heritage_feature (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_dataset TEXT,
    feature_group TEXT,
    monument_class TEXT,
    townland_raw TEXT,
    townland_norm TEXT,
    feature_name TEXT,
    source_link TEXT
)
```

Seeded from two GeoJSON files (`HOLYWELLS_GEOJSON_PATH`, `ASI_GEOJSON_PATH`
— National Monuments Service open data, under `extra_datasets/`), tagged
`source_dataset='holywells'` or the ASI equivalent per row. Same
fingerprint-gated re-seed pattern as `unified_record`, keyed under
`HERITAGE_SEED_KEY` with `stale_after_days=36500` (effectively "never
auto-expire — only re-seed if the file content changes"). Indexes:
`townland_norm`, `feature_group`.

### 3.3 `ask_query_memory` — approved question→SQL cache

Created by `ask_service.py::_ensure_query_memory_schema()`:

```sql
CREATE TABLE IF NOT EXISTS ask_query_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_text TEXT NOT NULL,
    question_signature TEXT NOT NULL,
    townland_norm TEXT,
    analysis_json TEXT,
    sql_text TEXT NOT NULL,
    vrti_postgres_sql TEXT,
    sample_answer TEXT,
    summary_json TEXT,
    source_mode TEXT,
    llm_provider TEXT,
    llm_model TEXT,
    approved_count INTEGER NOT NULL DEFAULT 0,
    rejected_count INTEGER NOT NULL DEFAULT 0,
    reuse_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_approved_at TEXT,
    last_used_at TEXT,
    feedback_note TEXT
)
```

Every question that receives a thumbs-up on the Ask page gets a row here
(or an existing row's `approved_count`/`reuse_count` incremented).
`question_signature` is a normalised fingerprint of the question text used
for the direct-memory-reuse fast lane (legacy pipeline only — see
`06_ask_pipeline_legacy_and_routing.md`): a new question whose signature is
similar enough (token-sort-ratio + cosine ≥ 0.55) to a stored one reuses
`sql_text` without any LLM call. Index on `question_signature`.

### 3.4 `ask_query_feedback` — all feedback (up + down)

Created alongside `ask_query_memory` in the same function:

```sql
CREATE TABLE IF NOT EXISTS ask_query_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_text TEXT NOT NULL,
    question_signature TEXT NOT NULL,
    townland_hint TEXT,
    townland_norm TEXT,
    sql_text TEXT,
    vrti_postgres_sql TEXT,
    feedback TEXT NOT NULL,           -- 'up' | 'down'
    note TEXT,
    result_row_count INTEGER,
    availability_state TEXT,
    llm_provider TEXT,
    llm_model TEXT,
    llm_mode TEXT,
    reused_memory_id INTEGER,
    created_at TEXT NOT NULL
)
```

Every feedback submission — thumbs up *and* thumbs down — is recorded here
unconditionally, even when a down-vote does **not** result in a
`ask_query_memory` row. This is the superset audit table; `ask_query_memory`
is the derived, curated subset used for reuse.

### 3.5 `ask_retrieval_chunks` — optional pgvector backend

Defined in `backend/services/ask_pgvector.py` (not SQLite — this table
lives in an optional external **Postgres** database, only relevant if
`DATABASE_URL` points at Postgres with the `pgvector` extension installed).
Covered in `09_retrieval_and_embeddings.md`; mentioned here only for
completeness of "every CREATE TABLE in the codebase."

## 4. Entity-relationship summary

```
townland (1) ──< census_record (N)         [townland_id FK]
townland (1) ──< clearances_record (N)      [townland_id FK]
townland (1) ──< townland_xref (N)          [entity_id, not a strict FK — text join]
townland (1) ──< field_provenance (N)       [entity_id, text join]
townland (1) ──< match_review (N, as A or B)[townland_id_a/b FK]
townland (1) ──< source_mentions (N)        [canonical_townland_id FK]

source_mentions (1) ──< entity_resolution_candidates (N)   [mention_id FK, CASCADE]
source_mentions (1) ──< workhouse_unified_links (N)         [mention_id FK, CASCADE]
entity_resolution_candidates (1) ──< entity_resolution_decisions (N) [candidate_id FK, CASCADE]

graph_nodes (1) ──< graph_edges (N, as src or dst)   [node_id FK]

unified_record            — standalone, joined to townland only via text
                             match on townland_norm (no FK — see below)
heritage_feature          — standalone, joined via townland_norm text match
ask_query_memory / feedback — standalone, no FK to any other table
```

Two deliberate **non-FK** relationships worth calling out: `unified_record`
and `heritage_feature` both carry a `townland_norm` / `townland_raw` text
column rather than a `townland_id` foreign key. This is because both
datasets were ingested from free-text place names that do not reliably
resolve 1:1 to a `townland.id` at ingest time (variant spellings,
sub-townland qualifiers) — joins against `townland` happen at *query* time
via normalised-text matching in the Ask pipeline's SQL generation, not at
*ingest* time via a hard FK. This is a conscious trade-off: it avoids
silently dropping or mis-linking rows during ingest at the cost of pushing
name-normalisation responsibility onto every query that needs the join
(see `identity_resolver.py`, covered in `05_ask_pipeline_default.md`).
