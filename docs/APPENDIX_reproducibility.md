# Appendix — Reproducibility Record

Provenance record for the submitted Coolattin Estate Records Explorer. Every value below
was read from the repository, the database file, or the evaluation artefacts themselves;
none is estimated. Where an artefact does not record something, that is stated explicitly
rather than inferred.

Repository: `https://github.com/pranjal-y4/coolattin-estate-explorer`

---

## 1. Summary table

| Item | Value |
|---|---|
| Final Git commit | `6f5fc4a01f6a53a18adb946c87818d38f99a7942` |
| Git release/tag | `v1.0-dissertation` (annotated; tag object `fe29ee5eb7e6aefcac1834683a158b9d3769f221`) |
| Database checksum (SHA-256) | `e719a158bec8fe51b1160ed9370140579b3c64405ac34ca1465ecc49b1d765ea` |
| Database size | 188,264,448 bytes — 179.54 MiB (188.26 MB) |
| Database snapshot date | Data last written **2026-08-10 08:40:17 IST (UTC+05:30)**; WAL checkpointed for submission 2026-08-11 14:51:38 IST |
| Graph snapshot | Built **2026-08-07 09:43:16**; **49,081** nodes; **69,302** edges; 3,501 communities; verdict `BUILD CLEAN` |
| Graph source-DB checksum | **Not recorded by the build report** — see §5 |
| Final Ask SQL model | Anthropic **`claude-sonnet-4-6`** (first tier of the cascade Claude → Grok → OpenRouter → Ollama) |
| Synthesis model | Anthropic **`claude-sonnet-4-6`** (`ASK_SYNTHESIS_MODEL=claude`), with documented fallbacks on 11/30 questions — see §4 |
| GraphRAG state | **Enabled** (`GRAPHRAG_ENABLED=true`, k=2 hops, max 120 nodes). Embeddings were **not required** on the path taken — see §6 |
| VRTI state during evaluation | **Partially available** — 2 of 30 questions recorded an explicit unavailable-fallback — see §7 |
| Evaluation timestamp | **2026-08-03, 23:48 IST (UTC+05:30)** (run completion) |
| `eval/er_gold.csv` checksum | `74d92061b796fd617de30124a702796f1ff639213c3cc3a023726391faa2d2c2` |

---

## 2. Code snapshot

| Field | Value |
|---|---|
| Commit hash | `6f5fc4a01f6a53a18adb946c87818d38f99a7942` |
| Tag | `v1.0-dissertation` |
| Branch | `main` |
| Author | pranjal-y4 `<pranjal13y@gmail.com>` |
| Parent | `33eb7da131d1ce059928454ede5d2d5f42865613` |

Retrieve the exact submitted state with:

```bash
git clone https://github.com/pranjal-y4/coolattin-estate-explorer.git
cd coolattin-estate-explorer
git checkout v1.0-dissertation
git lfs pull          # required — the database is stored via Git LFS
```

`git-lfs` is a prerequisite. `coolattin.db` exceeds GitHub's 100 MB per-blob limit and is
stored as an LFS object; a plain clone without `git lfs pull` yields a 134-byte pointer
file rather than the database.

---

## 3. Database

| Field | Value |
|---|---|
| File | `coolattin.db` (repository root) |
| SHA-256 | `e719a158bec8fe51b1160ed9370140579b3c64405ac34ca1465ecc49b1d765ea` |
| Size | 188,264,448 bytes = 179.54 MiB = 188.26 MB |
| SQLite page size / count | 4,096 bytes × 45,963 pages |
| Journal mode | WAL (checkpointed and truncated before submission) |
| `PRAGMA integrity_check` | `ok` |
| Data last written | 2026-08-10 08:40:17 IST (UTC+05:30) |
| Checkpointed for submission | 2026-08-11 14:51:38 IST (UTC+05:30) |

Selected row counts, read from the submitted file:

| Table | Rows |
|---|---|
| `unified_record` | 13,707 |
| `townland` | 4,334 |
| `graph_nodes` | 49,081 |
| `graph_edges` | 69,302 |
| `graph_nodes` with non-null `embedding` | 28,078 |
| `ask_query_memory` | 5 |

**Note on the checksum and the WAL.** Before submission the database carried a 4.2 MB
write-ahead log, meaning the authoritative logical database was the main file *plus* its
WAL, and a checksum of the main file alone would not have described the data. The WAL was
therefore checkpointed (`PRAGMA wal_checkpoint(TRUNCATE)`) so that one file contains the
whole database and one checksum is meaningful. This folds already-committed data into the
main file; it does not add, remove, or alter any record. For completeness, the main file's
checksum *before* checkpointing was
`8b7936468e29e9c2450169af9d40e1ef6e0e767d50228fc18b4aa11d6d6efeda` — that value is
superseded and should not be cited, as it described an incomplete file.

The checksum is independently verifiable from the repository without trusting this
document: Git LFS addresses objects by their SHA-256, so the OID recorded in the LFS
pointer for `coolattin.db` is byte-identical to the checksum above (`git lfs ls-files
--long`).

---

## 4. Ask pipeline models for the reported 30-question run

Configuration in force (`.env.local`, with `config.py` defaults where unset):

| Setting | Value |
|---|---|
| `ASK_USE_NEW_PIPELINE` | `true` (orchestrated pipeline; `intent_route` fixed at `"direct"`) |
| `ASK_LLM_PROVIDER` | `auto` |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-6` |
| `ASK_SYNTHESIS_MODEL` | `claude` |
| `GROK_MODEL` | `grok-3-mini` (base URL `https://api.x.ai/v1`) |
| `OPENROUTER_MODEL` | `openai/gpt-oss-20b:free` |
| `EMBEDDING_PROVIDER` | `local` (BAAI/bge-large-en-v1.5, 1024-dim) |

**SQL generation.** `_generate_sql()` calls the shared cascade
(`_llm_provider_order()`), which is ordered Claude → Grok → OpenRouter → Ollama.
`ASK_LLM_PROVIDER=auto` leaves that order unchanged, so SQL was generated by
**Anthropic `claude-sonnet-4-6`** whenever it succeeded.

**Synthesis.** `ASK_SYNTHESIS_MODEL=claude` pins the first synthesis attempt to
**Anthropic `claude-sonnet-4-6`**.

**Fallback behaviour actually observed — report this alongside the model IDs.** The run is
not cleanly "Claude for all 30 questions":

- The xAI tier was **dead for the entire run**. `grok-3-mini` and `grok-3-mini-fast`
  returned `403 Forbidden` and `grok-beta` returned `400 Bad Request` on every attempt.
  The second tier of the cascade therefore contributed nothing, and any fallback skipped
  straight from Claude to OpenRouter.
- On **9 of 30** questions the numeric-consistency gate rejected Claude's first synthesis
  for containing unsupported numbers and re-tried down the cascade.
- On **2 of 30** questions *all* providers (claude, grok, openrouter, ollama) were
  rejected by that gate, and the system returned guidance instead of a synthesised answer.
- **2 of 30** questions (`ov_01_famine_impact`, `ov_02_estate_summary`) failed SQL
  execution with an `OperationalError`, produced no safely validated SQL, and returned
  guidance — recorded as `strategy=validated_sql_unavailable`. These are the same two
  questions in each case, not separate failures. The remaining **28** ran as
  `strategy=llm_sql_direct`.

---

## 5. Graph snapshot

From `eval_results/graph_build_report.md`:

| Field | Value |
|---|---|
| Build timestamp | 2026-08-07 09:43:16 |
| Verdict | `BUILD CLEAN` |
| Total nodes | 49,081 |
| Total edges | 69,302 |
| Communities | 3,501 |
| Nodes embedded (as built) | 0 |
| Orphan rate | 22.9% (11,228 / 49,081) |
| Skipped edges | 7,960 (all `dst not in node set`) |
| Dangling edges | 0 |
| Source-DB checksum | **not recorded** |

Node counts by label: Person 13,707 · WorkhouseRecord 8,214 · CensusObservation 8,033 ·
EmigrationEvent 6,016 · Townland 4,225 · EvictionEvent 4,108 · Community 3,501 ·
ClearanceObservation 1,211 · Voyage 28 · CivilParish 22 · Barony 11 · County 5.

Three discrepancies to disclose rather than paper over:

1. **The build report does not record a checksum of the source database.** The graph lives
   inside `coolattin.db`, so the checksum in §3 identifies the container that holds this
   graph, but it is not evidence of which database state the graph was *built from*. If
   the appendix needs a true source-DB checksum, the build script must be amended to
   record one at build time and the graph rebuilt.
2. **Node and edge counts differ from `CLAUDE.md`**, which documents 49,081 nodes and
   64,308 edges. The submitted database and the 2026-08-07 build report agree on
   **69,302** edges; the 64,308 figure is stale documentation. Cite 69,302.
3. **The embedding count differs from the build report.** The report records 0 embedded
   nodes at build time, whereas the submitted database contains 28,078 nodes with
   embeddings — these were backfilled after the 2026-08-07 build.

---

## 6. GraphRAG state

`GRAPHRAG_ENABLED` defaults to `true` and was not overridden, so GraphRAG was **enabled**
for the reported run, with `GRAPHRAG_K_HOPS=2`, `GRAPHRAG_MAX_NODES=120` and
`GRAPHRAG_VECTOR_TOP_K=8`.

On the question of whether embeddings were *available*: the honest answer is that it did
not matter on the path the pipeline takes, and the artefacts do not record the embedding
state on 2026-08-03. `retrieve_subgraph()` seeds from resolved entity hints first
(`_seed_from_entity_hints`) and only calls `vector_seed()` — the sole consumer of node
embeddings — when no hint resolves. In the default pipeline the seed is the exactly
matched townland, so retrieval ran on exact seeding plus 2-hop BFS, and embeddings were a
fallback path rather than a dependency.

Two independent facts bear on availability and pull in opposite directions: the graph was
rebuilt on 2026-08-07 with 0 embedded nodes (i.e. *after* the evaluation), and the
submitted database now holds 28,078 embedded nodes. Neither establishes what was present
on 2026-08-03. The defensible claim is therefore: **GraphRAG enabled; retrieval seeded by
exact entity match, not by vector search; embedding availability at run time not recorded
and not required for the path taken.**

---

## 7. VRTI state during evaluation

| Field | Value |
|---|---|
| Endpoint | `https://virtuoso.virtualtreasury.ie/sparql/` |
| Request timeout | 30 s |
| State during the run | Partially available |

The endpoint was reachable for most of the run, but **2 of 30** questions recorded the
warning *"VRTI Knowledge Graph unavailable, using local townland reference data"*, so the
system degraded to local townland reference data for those. This is best described as
**intermittently available / degraded**, not as a clean "available" or "unavailable". Note
also that one comparative question (`cmp_02_population_vs_kg`) returned a VRTI URI from
the local `townland.kg_uri` column rather than from a live SPARQL call — the presence of a
KG URI in an answer is not by itself evidence that VRTI was queried live.

---

## 8. Evaluation run and artefact checksums

| Field | Value |
|---|---|
| Run date | 2026-08-03 |
| Completion time | 23:48 IST (UTC+05:30) — raw output and console log written |
| Write-up finalised | 23:51 IST (UTC+05:30) |
| Questions | 30, stratified round-robin across all 13 categories of `eval/gold.csv` |
| Sampling | Single pass, no repeats, live LLM API calls |
| Harness | `eval_plan/scripts/rq3_full30.py` |

| Artefact | Rows / size | SHA-256 |
|---|---|---|
| `eval/er_gold.csv` | 35 rows, 7,008 bytes | `74d92061b796fd617de30124a702796f1ff639213c3cc3a023726391faa2d2c2` |
| `eval/gold.csv` | 83 rows, 15,190 bytes | `9e4a704a876d19cd230671d5cae1828f2d3387f3223bc0674392fba68ce24aa0` |
| `eval_plan/evidence/RQ3_full30_raw_output.json` | 30 cases | `1d82437b03ee12e39a58d7498780d2d400d6f7ff79cf06d827481e19fe43096f` |
| `eval_plan/evidence/RQ3_full30_console_output.txt` | — | `d5e61549664d0d345426acc267dfa722390f96dda418d3b6045c5ad51f385949` |

Raw verdict distribution as recorded in the JSON: 12 `correct`, 6 `wrong`,
12 `not_scalar` (non-scalar gold answers, not machine-scorable), 0 harness errors.

**Accuracy — do not cite a bare percentage.** The harness's automated scorer does literal
substring matching and breaks on compound gold answers such as `"SQLite=55"`. It reports
12 correct / 6 wrong of 18 scalar-scorable questions (66.7%). Hand-verification of all six
"wrong" rows found three to be actually correct, giving **15/18 = 83.3%**, with 2 genuine
failures and 1 nondeterministic case. Cite 83.3% together with the methodology note in
`eval_plan/evidence/RQ3_full30_results.md` §"Scoring methodology correction".

---

## 9. Reproducibility limitations

Stated plainly so the appendix does not overclaim:

1. **The tagged commit is not the exact code state that produced the evaluation.** At the
   time of the 2026-08-03 run, the repository's `HEAD` was `40a856d` (2026-07-11) and all
   subsequent work — including the evaluation harness itself — existed only as an
   uncommitted working tree. The submitted commit `6f5fc4a` is the first to capture that
   work, and it also contains changes made *after* the run (the 2026-08-07 graph rebuild
   and the 2026-08-10 database write). The evaluation artefacts in `eval_plan/evidence/`
   are the primary record of the run; the tagged tree reproduces the *system*, not the run
   byte-for-byte.
2. **The submitted database post-dates the evaluation.** The graph in `coolattin.db` was
   rebuilt on 2026-08-07 and the database last written on 2026-08-10, both after the
   2026-08-03 run. Re-executing the harness against the submitted database will not
   necessarily reproduce the reported numbers.
3. **Live LLM calls are nondeterministic.** The run made real API calls with no seed
   pinning and no caching. `RQ3_full30_results.md` Finding 3 documents an instance of the
   same question producing two different SQL queries across runs. Exact reproduction of
   individual answers should not be expected; the raw JSON is the citable record.
4. **Third-party service state is not reproducible.** The xAI tier was returning
   403/400 throughout the run and VRTI was intermittently unavailable. Both are properties
   of external services on 2026-08-03 and cannot be recreated.
