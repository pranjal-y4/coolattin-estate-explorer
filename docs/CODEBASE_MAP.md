# CODEBASE_MAP.md — Coolattin Estate Records Explorer

**Produced:** 2026-08-07 · **Task:** ORIENT (read-only orientation pass; no code changed)
**Method:** direct source reading + live queries against the root-level `coolattin.db` (opened read-only) + one execution of the analytics modules. Every claim below cites a file and line actually opened.

**Ground rule applied:** the existing 15-file reference set (`docs/00_index.md` – `15_*.md`) was treated as a set of *claims*, not as truth. Where the code disagrees with a doc, the code wins and the disagreement is recorded in §K. §K also records places where the docs are **right about the symptom but wrong about the cause or the file** — those matter most, because a fix prompt written from them sends you to the wrong module.

Where I could not verify something, it is marked **UNVERIFIED** with what would settle it.

---

## A. Request lifecycle

### Process boot

| Step | Location | Notes |
|---|---|---|
| Entry | [app.py:9](../app.py#L9) | `app = create_app()` at import time, so WSGI servers get a configured app |
| Debug gate | [app.py:16](../app.py#L16) | `debug` only when `FLASK_ENV=development`; defaults False |
| Config select | [config.py:134-137](../config.py#L134-L137) | **Defaults to `ProductionConfig`** when `FLASK_ENV` is unset or unrecognised — deliberately fail-safe for Azure |
| Env files | [config.py:14-38](../config.py#L14-L38) | `_load_local_env_files()` runs at *module import*, reading `.env.local` then `.env`. Process env always wins |
| App factory | [create_app.py:29](../create_app.py#L29) | Absolute template/static paths resolved from `__file__` so CWD does not matter |
| DB init | [create_app.py:71-73](../create_app.py#L71-L73) | `init_db()` then `ensure_schema()` — synchronous, before blueprints |
| Rate limiter | [create_app.py:79-88](../create_app.py#L79-L88) | `flask_limiter` with `memory://` storage; import is optional, degrades to `None` |
| Blueprints | [create_app.py:94-110](../create_app.py#L94-L110) | 8 blueprints, all registered here |
| Ask limits | [create_app.py:167-179](../create_app.py#L167-L179) | Applied *after* registration by reaching into `app.view_functions` |
| Security headers | [create_app.py:118-141](../create_app.py#L118-L141) | `after_request`; CSP only on `text/html` so SSE is not blocked |
| Legacy routes | [create_app.py:182-193](../create_app.py#L182-L193) | Two routes registered directly on `app`, not on a blueprint |
| Prewarm | [create_app.py:150-161](../create_app.py#L150-L161) | Daemon thread loads the unified-records CSV cache |

### Registered in an unusual way

1. **`_apply_ask_rate_limits`** ([create_app.py:167](../create_app.py#L167)) reaches into `app.view_functions["ask_api.ask_query"]` by string key. Renaming the `ask.py` view function or its blueprint name silently raises `KeyError` at boot.
2. **Two legacy routes bypass the blueprint layer entirely** — `/api/centroids` and `/api/workhouse/match/<record_id>` ([create_app.py:185-193](../create_app.py#L185-L193)). They are invisible to any audit that only walks `backend/routes/`.
3. **`/analytics` lives on the `main` blueprint**, not on its own ([backend/routes/main.py:24](../backend/routes/main.py#L24)). There is no analytics blueprint to deregister.

### DB connection singleton

`get_db_conn()` ([extensions.py:33-45](../extensions.py#L33-L45)) returns a **new** `sqlite3.Connection` per call — it is a *path* singleton, not a connection pool. Caller must close. PRAGMAs applied on every connection:

```
journal_mode=WAL   foreign_keys=ON   synchronous=NORMAL
cache_size=-65536 (64 MB)   temp_store=2 (memory)   mmap_size=268435456 (256 MB)
```

### Latent boot bug

[create_app.py:54](../create_app.py#L54) reads `config_class.DEBUG`, but the base `Config` class **does not define `DEBUG`** — only `DevelopmentConfig` and `ProductionConfig` do ([config.py:116-123](../config.py#L116-L123)). Verified: `hasattr(config.Config, 'DEBUG') == False`. Any custom config class that does not subclass one of those two crashes the factory with `AttributeError`. This is not hypothetical — it is currently failing a test (see §J).

---

## B. Data model

### Live row counts (root `coolattin.db`, read-only query, 2026-08-07)

| Table | Rows | Created by | Populated by |
|---|---|---|---|
| `townland` | 4,225 | `ensure_schema` [extensions.py:293](../extensions.py#L293) | `townland_repository` via ingest jobs |
| `townland_xref` | 6 | `ensure_schema` [extensions.py:99](../extensions.py#L99) | `match_review_repository` [:136,:162](../backend/repositories/match_review_repository.py#L136) |
| `field_provenance` | **0** | `ensure_schema` [extensions.py:126](../extensions.py#L126) | `match_review_repository:205` — only on reviewer decision |
| `match_review` | **0** | `ensure_schema` [extensions.py:112](../extensions.py#L112) | `match_review_repository:41` |
| `census_record` | 8,033 | `ensure_schema` [extensions.py:248](../extensions.py#L248) | `census_repository.upsert_many` |
| `clearances_record` | 1,211 | `ensure_schema` [extensions.py:264](../extensions.py#L264) | `clearances_repository` |
| `refresh_state` | 11 | `ensure_schema` [extensions.py:274](../extensions.py#L274) | `refresh_state_repository` |
| `source_mentions` | 8,214 | `ensure_schema` [extensions.py:139](../extensions.py#L139) | `workhouse_entity_resolution:221` |
| `entity_resolution_candidates` | 42,494 | `ensure_schema` [extensions.py:162](../extensions.py#L162) | `workhouse_entity_resolution:254` |
| `workhouse_unified_links` | 5,134 | `ensure_schema` [extensions.py:181](../extensions.py#L181) | `workhouse_entity_resolution:284` |
| `entity_resolution_decisions` | **0** | `ensure_schema` [extensions.py:196](../extensions.py#L196) | **NOTHING — no writer exists** |
| `graph_nodes` | 49,081 | `ensure_schema` + `build_graph.py:112` | `scripts/build_graph.py` |
| `graph_edges` | 64,308 | `ensure_schema` + `build_graph.py:122` | `scripts/build_graph.py` |
| `unified_record` | 13,707 | **lazily**, `ask_service:4754` | `ask_service._ensure_unified_table_seeded` from `unified_processed.csv` |
| `heritage_feature` | 366 | **lazily**, `ask_service:4870` | `ask_service._ensure_heritage_feature_seeded` from GeoJSON |
| `ask_query_memory` | 5 | **lazily**, `ask_service:2270` | `record_query_feedback` (thumbs-up) |
| `ask_query_feedback` | 5 | **lazily**, `ask_service:2296` | `record_query_feedback` (all feedback) |

### Tables created but never populated by any code path

- **`entity_resolution_decisions` — zero writers anywhere in the repository.** Verified by grep across all `*.py`: the only two references are the `CREATE TABLE` at [extensions.py:196](../extensions.py#L196) and a `DELETE FROM` at [workhouse_entity_resolution.py:215](../backend/services/workhouse_entity_resolution.py#L215). **This is a stronger finding than the "reruns destroy review decisions" framing in the fix pack** — there is no code path that ever creates a decision, so the human-review capability is not merely fragile, it is unimplemented. Any dissertation claim that this table is the persistence layer for reviewer confirm/reject describes a schema provision, not a working feature.
- `field_provenance` and `match_review` are both empty (0 rows) but *do* have writers in `match_review_repository`. They are reachable only through the reviewer-decision path, which has evidently never been exercised on this database.

### Tables with more than one writer

- `graph_nodes` / `graph_edges`: DDL exists in **both** [extensions.py:215-233](../extensions.py#L215-L233) and [scripts/build_graph.py:112-122](../scripts/build_graph.py#L112). Both are `CREATE TABLE IF NOT EXISTS`, so they agree in practice, but the schema is defined twice and could drift.

### Schema-ownership drift

`CLAUDE.md` states "All tables created/migrated by `extensions.py::ensure_schema()`". Four tables are **not** — `unified_record`, `heritage_feature`, `ask_query_memory`, `ask_query_feedback` are created lazily inside `ask_service.py` on first Ask request. A fresh database has no `unified_record` table until someone visits `/ask`.

---

## C. The Ask pipeline — both of them

Entry point: `answer_question_stream()` [ask_service.py:3641](../backend/services/ask_service.py#L3641). Flag `ASK_USE_NEW_PIPELINE` [ask_service.py:75-77](../backend/services/ask_service.py#L75-L77) defaults **true**; delegation at [:3665-3667](../backend/services/ask_service.py#L3665-L3667).

### Default path — `_orchestrated_pipeline_stream` [:2765](../backend/services/ask_service.py#L2765)

`intent_route` is assigned the literal `"direct"` at [:2895](../backend/services/ask_service.py#L2895) and is never reassigned anywhere in the function.

Emission order of SSE stages (verified by reading the function body end to end):

| # | Stage | Line | Conditional? |
|---|---|---|---|
| 1 | `resolving_identity` | [:2814](../backend/services/ask_service.py#L2814) | always |
| 2 | `contacting_llm` | [:2918](../backend/services/ask_service.py#L2918) | always |
| 3 | `querying_graphrag` | [:2984](../backend/services/ask_service.py#L2984) | only if `canonical_townland` **and** graph available |
| 4 | `framing_query` | [:3074](../backend/services/ask_service.py#L3074) | always |
| 5 | `querying_database` | [:3097](../backend/services/ask_service.py#L3097) | always |
| 6 | `querying_vrti_graph` | [:3135](../backend/services/ask_service.py#L3135) | always |
| 7 | `querying_graphdb` | [:3192](../backend/services/ask_service.py#L3192) | **never fires** — see below |
| 8 | `querying_fusion` | [:3274](../backend/services/ask_service.py#L3274) | always |
| 9 | `preparing_output` (started) | [:3304](../backend/services/ask_service.py#L3304) | always |
| 10 | `synthesising_answer` | [:3347](../backend/services/ask_service.py#L3347) | **nested inside** stage 9 |
| 11 | `preparing_output` (completed) | [:3574](../backend/services/ask_service.py#L3574) | always |
| 12 | `done` | [:3576](../backend/services/ask_service.py#L3576) | always |

Synthesis uses `_claude_synthesize_answer` [:3436](../backend/services/ask_service.py#L3436), **not** a function named `_synthesize_answer` (which does not exist).

### Unreachable in the default configuration

This is the list that matters. Each entry states *why* it cannot execute.

| Function / module | Why unreachable when `ASK_USE_NEW_PIPELINE=true` |
|---|---|
| **Stage 4.5 GraphDB block** [:3190-3267](../backend/services/ask_service.py#L3190) | Guard is `intent_route in (_RELATIONAL, _COMPARATIVE)`; `intent_route` is hardcoded `"direct"` at [:2895](../backend/services/ask_service.py#L2895) |
| `_generate_graphdb_sparql` [:5366](../backend/services/ask_service.py#L5366) | Only called from inside that dead block |
| `_explain_result_mismatch` [:5501](../backend/services/ask_service.py#L5501) | Same |
| `_fuse_lanes` discrepancy detection [:5618](../backend/services/ask_service.py#L5618) | **Runs, but always inert.** Called at [:3277](../backend/services/ask_service.py#L3277) with `graphdb_rows=graph_comparison.get("rows", [])`, which is always `[]` because the block above never populates it. Cross-source discrepancy detection can never fire |
| `intent_router.classify_intent` | Never imported or called in the orchestrated path |
| `semantic_layer.try_rule_based_fill` / `compile_sql` / `compile_sparql` | Imported only inside the legacy branch at [:3688-3695](../backend/services/ask_service.py#L3688) |
| `embedding_index._phase4_retrieve` [:2077](../backend/services/ask_service.py#L2077) | Called only at [:3710](../backend/services/ask_service.py#L3710), legacy branch |
| `subgraph_engine` (whole module) | `_phase3_result` is initialised `None` at [:2903](../backend/services/ask_service.py#L2903) and never assigned. The injection block at [:3152](../backend/services/ask_service.py#L3152) and the `subgraph_context` payload key at [:3615](../backend/services/ask_service.py#L3615) are permanent no-ops |
| `_try_verified_analysis` [:2168](../backend/services/ask_service.py#L2168) | Legacy only |
| `_find_similar_approved_queries` / `_can_reuse_memory_directly` | Legacy only. In the default path `approved_matches=[]` [:2906](../backend/services/ask_service.py#L2906) and `_generate_sql` is called with `approved_examples=None` [:2928](../backend/services/ask_service.py#L2928) — so `ask_query_memory` is **written** by thumbs-up but never **read** to influence an answer |
| `_mark_query_memory_used` [:2482](../backend/services/ask_service.py#L2482) | Guarded by `if direct_memory_match:` [:3123](../backend/services/ask_service.py#L3123), which is always `None` [:2907](../backend/services/ask_service.py#L2907) |
| `graphrag.vector_seed` [:177](../backend/services/graphrag.py#L177) | Reached only when `_seed_from_entity_hints` returns empty ([graphrag.py:519](../backend/services/graphrag.py#L519)). The default pipeline calls `retrieve_subgraph` **only when `canonical_townland` is truthy** and passes it as the sole hint [:2989-2993](../backend/services/ask_service.py#L2989), so an exact townland seed essentially always resolves. **The 256 stored node embeddings are effectively dead weight in the shipping path.** |
| `_seed_from_entity_hints` surname branch [graphrag.py:237-251](../backend/services/graphrag.py#L237) | The default pipeline never puts `surname` in `entity_hints` |

### Legacy path — inline in `answer_question_stream` [:3669+](../backend/services/ask_service.py#L3669)

Four fast lanes, checked in order: semantic rule-based fill (threshold `0.80`, [:3734](../backend/services/ask_service.py#L3734)) → Phase 4 template → verified analysis → direct memory reuse. Then `intent_router.classify_intent`. This matches `docs/06` and is not re-derived here.

---

## D. Entity resolution

Four stages in `link_workhouse_records()` [workhouse_entity_resolution.py:306](../backend/services/workhouse_entity_resolution.py#L306):
`build_source_mentions` → `generate_candidates` (blocked by `build_unified_index`) → `score_candidate` → persist.

### The seven signals — exact values from [entity_resolution/scoring.py](../backend/services/entity_resolution/scoring.py)

Max 60 raw points, normalised `raw / 60`.

| # | Signal | Points | Lines |
|---|---|---|---|
| 1 | Full-name similarity | 10 (≥90%) · 7 (≥75%) · 4 (≥60%) | [:56-66](../backend/services/entity_resolution/scoring.py#L56) |
| 2 | Surname | 10 exact · 7 phonetic | [:69-79](../backend/services/entity_resolution/scoring.py#L69) |
| 3 | Forename | 10 (≥90%) · 7 (≥80%) · 4 (≥60%) · **5 neutral** if either side missing | [:82-101](../backend/services/entity_resolution/scoring.py#L82) |
| 4 | Place | 10 exact · 6 substring-variant | [:104-115](../backend/services/entity_resolution/scoring.py#L104) |
| 5 | Birth year | 5 (gap ≤3) · 3 (gap ≤8) · conflict if >20 | [:118-131](../backend/services/entity_resolution/scoring.py#L118) |
| 6 | Gender | 10 match · **5 neutral** if missing · 0 + conflict on mismatch | [:134-144](../backend/services/entity_resolution/scoring.py#L134) |
| 7 | Timeline | age-progression: 5 (≤2) · 3 (≤5); year-only fallback: 5 (≤2) · **2.5 (≤10)** | [:155-178](../backend/services/entity_resolution/scoring.py#L155) |

**The 2.5-point band is real** ([:173](../backend/services/entity_resolution/scoring.py#L173)). It is omitted both from Table 5.4 of the dissertation *and* from this module's own docstring header at [:11](../backend/services/entity_resolution/scoring.py#L11).

Similarity function is `rapidfuzz.fuzz.token_sort_ratio` with a `difflib.SequenceMatcher` fallback ([:29-34](../backend/services/entity_resolution/scoring.py#L29)) — **not Jaro-Winkler**.

### Impossible-conflict override and bands

[:181-196](../backend/services/entity_resolution/scoring.py#L181): if either `"Impossible age/date conflict"` or `"Impossible timeline gap"` is present, `score = min(score, 0.39)` — which forces `NO_MATCH`. Bands: **≥0.75 CONFIRMED · ≥0.60 POSSIBLE · ≥0.40 WEAK · else NO_MATCH.**

Live distribution confirms the thresholds are what produced the stored data: 873 CONFIRMED + 4,261 POSSIBLE = 5,134 links; 37,360 WEAK.

### Fields computed and carried but never read by the scorer

| Field | Built at | Read by scorer? |
|---|---|---|
| `occupation_norm` | [:141,:202](../backend/services/workhouse_entity_resolution.py#L141) | **No** |
| `household_fields` | [:132,:203](../backend/services/workhouse_entity_resolution.py#L132) | **No** (persisted to DB, still unread) |
| `forename_initial` | [:149,:196](../backend/services/workhouse_entity_resolution.py#L149) | **No** (used by candidate blocking only) |

### A finding not in any existing doc

`gender` is scored (signal 6) and is present on the in-memory mention dict at [:162](../backend/services/workhouse_entity_resolution.py#L162), but **`source_mentions` has no `gender` column** ([extensions.py:139-160](../extensions.py#L139)) and `_insert_source_mention` does not persist it ([:221-251](../backend/services/workhouse_entity_resolution.py#L221)). Consequence: a 10-point signal exists only during a live run. Nothing can re-score a persisted mention later and reproduce the stored score. This weakens any reproducibility claim about the ER stage that rests on the persisted tables alone.

---

## E. Graph and retrieval

### Build and load

Built offline by [scripts/build_graph.py](../scripts/build_graph.py) into `graph_nodes`/`graph_edges`. Loaded by `graphrag._load_graph()` [graphrag.py:53](../backend/services/graphrag.py#L53) into a process-lifetime `networkx.MultiDiGraph`.

**Loading is lazy, not at startup.** `_ensure_loaded()` [:121](../backend/services/graphrag.py#L121) is called from `is_available()` / `vector_seed()` / `retrieve_subgraph()` — i.e. on the first Ask request that resolves a townland. The module docstring at [:8](../backend/services/graphrag.py#L8) says "loaded into a NetworkX MultiDiGraph once at startup", which is wrong. There is **no freshness check of any kind** against the source database.

### Live graph contents

| Node label | Count | | Edge type | Count |
|---|---|---|---|---|
| Person | 13,707 | | IN_COMMUNITY | 22,720 |
| WorkhouseRecord | 8,214 | | HAS_EVENT | 10,124 |
| CensusObservation | 8,033 | | HAS_OBSERVATION | 9,244 |
| EmigrationEvent | 6,016 | | LOCATED_IN | 9,095 |
| Townland | 4,225 | | OCCURRED_IN | 6,776 |
| EvictionEvent | 4,108 | | DEPARTED_VIA | 6,016 |
| Community | 3,501 | | WITHIN | 193 |
| ClearanceObservation | 1,211 | | **LINKED_TO** | **140** |
| Voyage / CivilParish / Barony / County | 28 / 22 / 11 / 5 | | | |

**Staleness confirmed:** 140 `LINKED_TO` edges against 5,134 rows in `workhouse_unified_links`. Also `heritage_feature` has 366 rows but **zero** heritage/monument nodes exist in the graph. `WITHIN` covers 184 distinct source nodes.

### Embeddings — the reality

| Claim | Reality |
|---|---|
| Dimension | **1,024 confirmed** — every stored blob is exactly 4,096 bytes = 1,024 × float32 |
| Coverage | **256 nodes out of 49,081 (0.52%)** |
| Which labels | **Townland only.** Person, CivilParish, EmigrationEvent, EvictionEvent all have **zero** embedded nodes |

`docs/00_index.md` §2 states embeddings are "one per individual retrievable node (Person, Townland, CivilParish, EmigrationEvent, EvictionEvent)". That is **false against this database** — only 256 Townland nodes are embedded. Combined with §C's finding that `vector_seed` is unreachable in the default path, the dense-retrieval component of GraphRAG is inert in the shipping configuration.

### Provider actually supplying vectors

- `vector_seed` calls `local_embeddings.embed_texts_local` ([graphrag.py:196](../backend/services/graphrag.py#L196)) → requires `sentence-transformers`, which is **commented out of `requirements.txt`** ([requirements.txt](../requirements.txt), last 4 lines).
- `voyage_embeddings.py` is a **Cohere** client — its own docstring says "replaces Voyage AI" ([:4](../backend/services/voyage_embeddings.py#L4)). Filename is legacy.
- `_get_embedding_provider()` [:44-48](../backend/services/voyage_embeddings.py#L44) reads `os.environ` directly and **silently coerces any unrecognised value to `"local"`** — no error, no warning.

---

## F. External integrations

| Call site | URL source | Timeout source | Escaping |
|---|---|---|---|
| `vrti_sparql._execute` [:150](../backend/integrations/vrti_sparql.py#L150) | module constant `SPARQL_ENDPOINT` | **hardcoded `REQUEST_TIMEOUT = 30`** [:36](../backend/integrations/vrti_sparql.py#L36) | n/a |
| `vrti_sparql.get_townland_details_by_name` [:309](../backend/integrations/vrti_sparql.py#L309) | as above | as above | `.replace('"','\\"')` [:336](../backend/integrations/vrti_sparql.py#L336) — **partial** |
| `vrti_sparql.get_census_records_for_county` [:544](../backend/integrations/vrti_sparql.py#L544) | as above | as above | **NONE** — `county` interpolated raw at [:576](../backend/integrations/vrti_sparql.py#L576) |
| `vrti_sparql.get_parish_names` [:627](../backend/integrations/vrti_sparql.py#L627) | as above | as above | **NONE** — `county` interpolated raw at [:647](../backend/integrations/vrti_sparql.py#L647) |
| `graphdb_sparql._execute` [:58](../backend/integrations/graphdb_sparql.py#L58) | `ActiveConfig.GRAPHDB_SPARQL_ENDPOINT` | `ActiveConfig.GRAPHDB_REQUEST_TIMEOUT` | n/a |
| `graphdb_sparql.get_entity_neighborhood` [:219](../backend/integrations/graphdb_sparql.py#L219) | as above | as above | `.replace('"','\\"')` [:238](../backend/integrations/graphdb_sparql.py#L238) — **partial** |
| `semantic_layer.compile_sparql` [:707](../backend/services/semantic_layer.py#L707) | n/a (string built) | n/a | **wrong grammar** — see below |

### Correction to the received account of the SPARQL injection gaps

**The unescaped `county` interpolation is in `vrti_sparql.py`, not `graphdb_sparql.py`.** `graphdb_sparql.py` contains no `county` parameter at all — its only occurrence of the word is a URI-to-label mapping at [:333](../backend/integrations/graphdb_sparql.py#L333). Both `docs/00_index.md` §2 and the C7 fix prompt name the wrong file; anyone following them would find nothing and could conclude the issue was already fixed.

Two real defects:

1. **Raw `county` interpolation** at [vrti_sparql.py:576](../backend/integrations/vrti_sparql.py#L576) and [:647](../backend/integrations/vrti_sparql.py#L647). Currently safe only because callers pass `None` or a hardcoded literal.
2. **`semantic_layer._esc` applies SQL escaping in a SPARQL context.** [:62-64](../backend/services/semantic_layer.py#L62) doubles `'` → `''` (a SQL rule). Its output is placed inside a SPARQL **double-quoted** literal at [:724-726](../backend/services/semantic_layer.py#L724): `FILTER(UCASE(STR(?name)) = "{norm}")`. It neither escapes `"` nor backslash, and it corrupts legitimate apostrophes — `O'BYRNE` becomes `O''BYRNE`. *Scope note:* `compile_sparql` is reachable only from the legacy pipeline and `ask_eval.py`, so this is latent in the shipping configuration.
3. The two "reference implementations" are themselves incomplete: `.replace('"','\\"')` does not escape backslash **first**, so a value ending in `\` still breaks the literal.

### Offline-cooldown state

Lives in `ask_service.py`, not in the integration modules: `_VRTI_STATUS_CACHE` / `_VRTI_UNAVAILABLE_COOLDOWN = 300` at [ask_service.py:144-145](../backend/services/ask_service.py#L144), with `_vrti_temporarily_unavailable()` [:7659](../backend/services/ask_service.py#L7659) and `_mark_vrti_temporarily_unavailable()` [:7664](../backend/services/ask_service.py#L7664). GraphDB has a separate probe cooldown inside `graphdb_sparql.probe()` [:170](../backend/integrations/graphdb_sparql.py#L170).

---

## G. Frontend

| Page | Route | Template | Controller |
|---|---|---|---|
| Home | `/` | `index.html` | `main.js` |
| About | `/about` | `about.html` | `main.js` |
| **Analytics** | `/analytics` | `analytics.html` | `analytics.js` |
| Census | `/census` | `census.html` | `census.js` |
| Info | `/info` | `info.html` | `main.js` |
| Ask | `/ask` | `ask.html` | `ask.js` |
| Heritage | `/heritage` | `heritage.html` | `heritage.js` |
| KG Explore | `/kg-explore` + `/explore-knowledge` | `kg_explore.html` | `kg_explore.js` |

### The Ask SSE client

`ask.js` maintains a hand-written `progressOrder` array [ask.js:55-65](../frontend/static/js/ask.js#L55). Diffed against the stages the default pipeline actually emits (§C):

| Frontend expects | Backend emits (default path)? |
|---|---|
| `classifying_intent` | **Never — not emitted anywhere in `ask_service.py`, in either pipeline** |
| `contacting_llm` | yes |
| `slot_filling` | **Never — not emitted anywhere in `ask_service.py`, in either pipeline** |
| `framing_query` | yes |
| `querying_database` | yes |
| `querying_subgraph` | legacy only |
| `querying_vrti_graph` | yes |
| `querying_fusion` | yes — but the frontend **mislabels it "Synthesising Answer"** [:63](../frontend/static/js/ask.js#L63) |
| `preparing_output` | yes |

Emitted but never rendered as a persistent card: **`resolving_identity`, `querying_graphrag`, `querying_graphdb`, `synthesising_answer`, `done`**.

Note `classifying_intent` and `slot_filling` are *phantom* stages — I grepped every `stage="..."` literal in `ask_service.py` and neither string appears. They are not merely legacy-only; no code has ever emitted them.

There is also **no `default`/`else` branch** for unrecognised event types, so future drift fails silently.

### innerHTML sinks

Raw counts: `ask.js` 43 · `main.js` 22 · `heritage.js` 12 · `kg_explore.js` 12 · `census.js` 11 · `map.js` 1 · `analytics.js` 0.

The one that reaches LLM-influenced text with no sanitiser:

```js
// frontend/static/js/ask.js:1110-1114
const rewrite = payload.llm_rephrased_answer || "";
llmAnswerEl.innerHTML = rewrite ? markdownToHtml(rewrite) : `<span …>No summary available…</span>`;
```

`markdownToHtml` [:130-133](../frontend/static/js/ask.js#L130) is `marked.parse(String(text))` with **no DOMPurify or equivalent** — grep confirms no sanitiser is vendored or referenced anywhere. The CSP set at [create_app.py:133](../create_app.py#L133) includes `'unsafe-inline'` in `script-src`, so CSP is not a compensating control. Many of the other sinks *do* pass through an `escapeHtml` helper (e.g. [:993-995](../frontend/static/js/ask.js#L993)); this one does not. **UNVERIFIED:** I did not audit all 101 sinks individually — that needs a per-site pass to confirm which carry API-derived values.

---

## H. Duplicate and divergent implementations

| Concept | Site A | Site B | Difference | Which is live |
|---|---|---|---|---|
| **Ask pipeline** | `_orchestrated_pipeline_stream` [:2765](../backend/services/ask_service.py#L2765) | legacy inline [:3669](../backend/services/ask_service.py#L3669) | routing, fast lanes, memory reuse | **A** (`ASK_USE_NEW_PIPELINE` defaults true) |
| **Reciprocal rank fusion** | `embedding_index._rrf` [:260](../backend/services/embedding_index.py#L260) | `voyage_embeddings.rrf_fuse` [:409](../backend/services/voyage_embeddings.py#L409) | separate implementations; `embedding_index` imports B at [:560](../backend/services/embedding_index.py#L560) **and** defines A | Both present in the same module; neither reachable in the default Ask path (§C) |
| **Reconciliation-gap writer** | `townland_service._write_reconciliation_gaps` [:691](../backend/services/townland_service.py#L691) | `build_graph.write_reconciliation_gaps` [:280](../scripts/build_graph.py#L280) | 1-column overwrite vs 5-column append-and-dedupe, same path | last writer wins |
| **Heritage→townland association** | `ask_service._heritage_townland_norm` [:10174](../backend/services/ask_service.py#L10174) — normalised string equality | `townland_service` shapely `.within()` [:190,:197,:597](../backend/services/townland_service.py#L190) | string match vs point-in-polygon | both live, different subsystems |
| **Name similarity** | `scoring._ratio` [:29](../backend/services/entity_resolution/scoring.py#L29) `token_sort_ratio` | `ask_service._fuzzy_match_forename` [:1102](../backend/services/ask_service.py#L1102) | different libraries/thresholds | both live |
| **Graph DDL** | [extensions.py:215-233](../extensions.py#L215) | [build_graph.py:112-122](../scripts/build_graph.py#L112) | duplicated `CREATE TABLE` | both run; `IF NOT EXISTS` masks drift |
| **SQL escape helper** | `ask_service._sql_escape` [:10055](../backend/services/ask_service.py#L10055) | `semantic_layer._esc` [:62](../backend/services/semantic_layer.py#L62) | identical logic, comment at [:61](../backend/services/semantic_layer.py#L61) admits the mirroring | both live; B is misapplied to SPARQL (§F) |
| **`ask_retrieval_chunks` DDL** | `ask_pgvector.py:178` | `scripts/cohere_sample_validate.py:234` | duplicated | Postgres-only, optional backend |

---

## I. Configuration

`config.py` declares **21** uppercase attributes. Reference counts outside `config.py`:

**Declared but never read anywhere (dead):**

| Attribute | Line | What actually happens |
|---|---|---|
| `VRTI_REQUEST_TIMEOUT` | [config.py:68](../config.py#L68) | **0 references.** `vrti_sparql.py` never imports `config`; it hardcodes `REQUEST_TIMEOUT = 30` at [:36](../backend/integrations/vrti_sparql.py#L36), which matches the default only by coincidence |
| `LOG_LEVEL` | [config.py:113](../config.py#L113) | **0 references.** No logging configuration reads it |
| `EMBEDDING_PROVIDER` | [config.py:108](../config.py#L108) | Referenced 8× but **never as `ActiveConfig.EMBEDDING_PROVIDER`** (verified by grep). Real selection is `os.environ.get` inside [voyage_embeddings.py:46](../backend/services/voyage_embeddings.py#L46) |

**Modules that bypass `config.py` entirely:**

- `backend/integrations/vrti_sparql.py` — no `config` import at all; endpoint and timeout are module constants.
- `backend/services/ask_service.py` — reads ~30 environment variables directly via `os.environ.get` at [:46-123](../backend/services/ask_service.py#L46) (provider keys, models, timeouts, retry counts, `ASK_USE_NEW_PIPELINE`, `DEFAULT_TOWNLAND`, three `RATE_LIMIT_*_RPM`). **None of these appear in `config.py`**, so the stated convention "all tunable values live here" does not hold for the largest module in the codebase.
- `backend/services/voyage_embeddings.py` — `_getenv` wrapper over `os.environ` [:39-41](../backend/services/voyage_embeddings.py#L39).

**Silent coercion:** an unrecognised `EMBEDDING_PROVIDER` becomes `"local"` with no warning ([voyage_embeddings.py:48](../backend/services/voyage_embeddings.py#L48)).

**Install-time contradiction:** `EMBEDDING_PROVIDER` defaults to `local` (BAAI/bge-large-en-v1.5), but `sentence-transformers` and `torch` are commented out of `requirements.txt` for the Azure App Service size limit. A stock install cannot serve the default provider.

---

## J. Test coverage

`python3 -m pytest tests/ -q` → **3 failed, 72 passed, 1 skipped** (32.4 s). These failures are pre-existing; I changed nothing.

| Test | Failure |
|---|---|
| `test_workhouse_entity_resolution.py::test_api_returns_please_check_records_and_ambiguity` | `AttributeError: type object 'TestConfig' has no attribute 'DEBUG'` — the §A boot bug, triggered via `create_app.py:54` |
| `test_same_parish_fast_path.py::test_analyse_question_marks_same_parish_townlands_as_list` | `assert 'people' == 'geography'` — intent classification drift |
| `test_ask_pgvector.py::test_pgvector_dense_retrieve_works_after_completed_with_failures` | `assert 2 == 1` — retrieval returns 2 chunks where 1 expected |

**Covered:** config env loading, GraphDB SPARQL, LLM status, local embeddings, numeric gate, townland resolution, workhouse ER, pgvector, ask pipeline flags, same-parish fast path.

**No test at all:**

- The **default orchestrated pipeline end-to-end** — `test_ask_pipeline_flags.py` covers flag behaviour, not the 12-stage path.
- **SSE protocol / stage-name contract** between `ask_service.py` and `ask.js` — precisely the surface that has already drifted (§G).
- **All 8 route blueprints** — no HTTP-level test of any endpoint; no test that `/api/townlands/refresh` is unauthenticated.
- **`analytics/`** — no test imports it; its total runtime failure was invisible to CI.
- **SPARQL escaping** — no injection or apostrophe test anywhere.
- **`scripts/build_graph.py`** and graph freshness.
- **XSS / sanitisation** — no frontend tests exist at all.

---

## K. Documentation drift register

Rows marked ⚠️ are cases where the existing docs identify a real symptom but **misattribute the cause or the file** — these are the dangerous ones, because a fix prompt written from them targets the wrong code.

| # | Claim | Source | What the code does | Evidence |
|---|---|---|---|---|
| 1 | "All tables created/migrated by `ensure_schema()`" | CLAUDE.md | 4 tables created lazily in `ask_service.py` | [ask_service.py:2270,2296,4754,4870](../backend/services/ask_service.py#L4754) |
| 2 | ⚠️ Analytics failure caused by `registry.py` `Path.parents[2]` | docs/00_index.md §2 | `registry.py` contains **no** `parents[]` call. The off-by-one is in the **dataset modules**, and there are **two distinct bugs**: `evictions`/`emigrations`/`townland_geo` escape above repo root; `unified`/`tenancies` resolve correctly but point at `data/` when the files live in `frontend/static/data/` | [registry.py](../analytics/registry.py) (full file); [evictions.py:8](../analytics/evictions.py#L8); verified by executing all 5 modules |
| 3 | ⚠️ SPARQL `county` injection is in `graphdb_sparql.py` | docs/00_index.md §2; C7 prompt | `graphdb_sparql.py` has no `county` parameter. The raw interpolation is in **`vrti_sparql.py`** | [vrti_sparql.py:576](../backend/integrations/vrti_sparql.py#L576), [:647](../backend/integrations/vrti_sparql.py#L647); [graphdb_sparql.py:333](../backend/integrations/graphdb_sparql.py#L333) is the only `county` hit |
| 4 | ⚠️ `/api/ask/estate-overview` "never returns a non-200 status" | docs/00_index.md §2; C8 prompt | It **does** return `500` on exception. Only `/api/unified/workhouse-by-townland` has the 200-error bug | [ask.py:144](../backend/routes/ask.py#L144) vs [unified.py:107](../backend/routes/unified.py#L107) |
| 5 | ⚠️ ER reruns "destroy human review decisions" | C6 prompt | True but understated: `entity_resolution_decisions` has **zero writers anywhere**. The feature is unimplemented, not merely fragile | grep: only [extensions.py:196](../extensions.py#L196) and [workhouse_entity_resolution.py:215](../backend/services/workhouse_entity_resolution.py#L215) |
| 6 | Embeddings are "one per retrievable node (Person, Townland, CivilParish, EmigrationEvent, EvictionEvent)" | docs/00_index.md §2 | **256 of 49,081 nodes**, all label `Townland`. Every other label has zero | live DB query, §E |
| 7 | Graph "loaded into NetworkX at startup" | graphrag.py:8 docstring; extensions.py:214 comment | Loaded **lazily** on first `is_available()` call; no freshness check | [graphrag.py:121](../backend/services/graphrag.py#L121) |
| 8 | ER thresholds 0.85 / 0.70 / 0.50; Jaro-Winkler; scores occupation, family size, household | CLAUDE.md | **0.75 / 0.60 / 0.40**; `token_sort_ratio`; those three fields are never read | [scoring.py:189-196](../backend/services/entity_resolution/scoring.py#L189), [:29-34](../backend/services/entity_resolution/scoring.py#L29) |
| 9 | Timeline signal is a flat 5 points | scoring.py docstring [:11](../backend/services/entity_resolution/scoring.py#L11); dissertation Table 5.4 | There is also a **2.5** band for year-gap ≤10 | [scoring.py:173](../backend/services/entity_resolution/scoring.py#L173) |
| 10 | `_synthesize_answer()` | CLAUDE.md | No such function. Default path uses `_claude_synthesize_answer` | [ask_service.py:6235](../backend/services/ask_service.py#L6235), called [:3436](../backend/services/ask_service.py#L3436) |
| 11 | `config.py` is "the single source of truth… never hard-code paths or timeouts" | CLAUDE.md; config.py:5 | `ask_service.py` reads ~30 env vars directly; `vrti_sparql.py` hardcodes its timeout and never imports config | [ask_service.py:46-123](../backend/services/ask_service.py#L46); [vrti_sparql.py:36](../backend/integrations/vrti_sparql.py#L36) |
| 12 | ANALYTICAL route "never" uses free-form LLM SQL | CLAUDE.md | Has fall-through conditions to free-form SQL (already noted in docs/06) | legacy branch, [ask_service.py:3700+](../backend/services/ask_service.py#L3700) |
| 13 | Directory layout omits `kg_explore.py` | CLAUDE.md | 8th blueprint, registered at [create_app.py:110](../create_app.py#L110) with 8 routes | [backend/routes/kg_explore.py](../backend/routes/kg_explore.py) |
| 14 | `census_service._ingest_from_kg_or_seed` "never falls back… unlike the jobs" | docs/00_index.md §2 | Accurate — but the docstring says the no-fallback is **deliberate** ("The caller should direct the user to run full_ingest instead of silently falling back to stale CSV data"). The defect is that the **function name promises `_or_seed`** and never touches a seed | [census_service.py](../backend/services/census_service.py), `_ingest_from_kg_or_seed` docstring |
| 15 | `ask.js` progressOrder "has drifted" | docs/00_index.md §2 | Accurate, and worse: `classifying_intent` and `slot_filling` are emitted by **no** code path in either pipeline | grep of every `stage="…"` literal, §G |
| 16 | `voyage_embeddings.py` calls Voyage AI | filename | Cohere `embed-english-v3.0`; docstring admits it | [voyage_embeddings.py:4](../backend/services/voyage_embeddings.py#L4) |
| 17 | VRTI cooldown in `vrti_sparql.py` | earlier draft of docs/11 | In `ask_service.py` | [ask_service.py:145](../backend/services/ask_service.py#L145) |

---

## Closing: the five things most likely to break if changed carelessly

1. **`intent_route = "direct"` at [ask_service.py:2895](../backend/services/ask_service.py#L2895).** It looks like a dead constant. It is the single switch gating Stage 4.5, `_fuse_lanes`' inputs, and two `phase6_fusion_note` branches. Changing it to make fusion "work" turns on an LLM-generated-SQL-plus-SPARQL path that nothing currently tests, on every request.
2. **`_apply_ask_rate_limits` string keys [create_app.py:174,178](../create_app.py#L174).** Renaming the `ask` blueprint or its view functions breaks boot with a `KeyError`, not a lint error.
3. **The lazy seeding in `ask_service.py`.** `unified_record` (13,707 rows) and `heritage_feature` (366) exist only because someone hit `/ask`. Any refactor that moves, reorders or guards `_ensure_unified_table_seeded` / `_ensure_heritage_feature_seeded` can leave a fresh database with no person data and no obvious error.
4. **`_clear_resolution_tables` [workhouse_entity_resolution.py:214](../backend/services/workhouse_entity_resolution.py#L214).** Deletion order respects FK cascade. Reordering it, or adding a table without considering `ON DELETE CASCADE`, silently orphans 42,494 candidate rows.
5. **The SSE stage-name contract.** It is a hand-synced string protocol across `ask_service.py` and `ask.js` with **no shared definition, no test, and no unknown-event branch**. It has already drifted twice. Any renamed stage fails invisibly in the browser.

## The three least trustworthy areas of the documentation

1. **`analytics/`** — `docs/12` and the `00_index.md` known-issues entry name the wrong file and describe one bug where there are two (drift #2). The whole subsystem is also untested and unreferenced by Chapters 1–8 of the dissertation.
2. **The integrations layer** — the injection finding points at the wrong module (drift #3), the cooldown was documented in the wrong module (#17), and `config.py`'s stated authority over timeouts is contradicted by the one integration that matters most (#11).
3. **GraphRAG / embeddings** — the dimension claim is right, but coverage (256 of 49,081), label distribution (Townland only), load timing (lazy, not startup), and reachability of `vector_seed` (unreachable in the default path) are all wrong or unstated. Any dissertation claim resting on "hybrid dense retrieval over the property graph" should be re-checked against §E before submission.
