# 10 — Knowledge Graph Retrieval (GraphRAG)

Technical reference for the in-process knowledge-graph subsystem referred to
elsewhere in this project as **"C6 — In-Process GraphRAG over Historical
Knowledge Graph."** This document covers three distinct, coexisting KG
mechanisms in the codebase — they share the word "graph" but are built,
stored, and queried in completely different ways. Do not conflate them:

| Module | Storage | Query style | Used by |
|---|---|---|---|
| `backend/services/graphrag.py` | `graph_nodes` / `graph_edges` SQLite tables → loaded into an in-memory NetworkX `MultiDiGraph` | Local BFS + vector ANN, zero network calls | Default Ask pipeline (`ASK_USE_NEW_PIPELINE=true`) |
| `backend/services/subgraph_engine.py` | Nothing local — issues live SPARQL over HTTP | Remote SPARQL (VRTI) + remote SPARQL (local GraphDB server) | Legacy Ask pipeline (`ASK_USE_NEW_PIPELINE=false`), RELATIONAL/COMPARATIVE routes only |
| `backend/services/kg_service.py` | `data/coolattin_sample.ttl` parsed into an in-memory `rdflib.ConjunctiveGraph`, plus a request-time D3 hierarchy graph built straight from the `townland` SQL table | rdflib SPARQL engine (local, in-process) + plain SQL | KG Explore page (`/kg`) only — not the Ask pipeline |

For the exact DDL of `graph_nodes` / `graph_edges` (columns, indexes,
foreign-key-style composite primary key) see
`docs/02_database_schema.md` §1.12. This document focuses on how that table
pair is *built* (`scripts/build_graph.py`) and how it is *traversed at
runtime* (`graphrag.py`).

---

## 1. `scripts/build_graph.py` — offline graph construction

Run manually: `python3 scripts/build_graph.py` (incremental upsert) or
`python3 scripts/build_graph.py --wipe` (drop + rebuild both tables from
scratch). It is the sole writer of `graph_nodes` / `graph_edges` —
`extensions.py::ensure_schema()` only creates the two tables if absent; it
never populates them. The script is a **pure SQLite → SQLite job**: its only
external file input is `frontend/static/data/unified_processed.csv` (via
`ActiveConfig.STATIC_DATA_DIR`). It does **not** read `data/seed/coolattin.ttl`
or any VRTI SPARQL dump — the RDF Turtle files are a completely separate
artifact (see §6 below). Six sequential steps, each logged with a
`step<N>:` prefix and timed as a whole (`build_graph.py complete in %.1fs`):

### Step 1 — `create_tables(conn, wipe)`
`DROP TABLE IF EXISTS` on `--wipe`, then `CREATE TABLE IF NOT EXISTS` for both
tables plus their four indexes (`idx_gn_label`, `idx_ge_src`, `idx_ge_dst`
and the edges' composite `PRIMARY KEY (src, dst, rel_type)`).

### Step 2 — `materialise_nodes(conn)`
Reads from four sources and writes six node labels via `_upsert_node()`
(`INSERT ... ON CONFLICT(node_id) DO UPDATE`):

| Source | Node label(s) | `node_id` format | Notes |
|---|---|---|---|
| `townland` table | `Townland` | `townland:{NAME_UPPER}` | props: `db_id`, `entity_id`, `civil_parish`, `barony`, `county`, `lat`, `lon`, `kg_uri` |
| derived from `townland.civil_parish/barony/county` | `CivilParish`, `Barony`, `County` | `parish:{P}`, `barony:{B}`, `county:{C}` | one node per distinct non-null value; empty `props` |
| `unified_processed.csv` (pandas) | `Person` | `person:{record_id}` | props include `surname`, `forename`, `canonical_name`, `townland`, `year`, `role`, `ship_name`, `has_emigration`, `has_eviction`, `occupation`, `family_key` |
| same CSV, conditional | `EmigrationEvent`, `EvictionEvent` | `event:emigration:{rid}`, `event:eviction:{rid}` | one per person row where the corresponding `has_*_record` flag is true |
| `source_mentions` table (if present) | `WorkhouseRecord` | `workhouse:{source_record_id}` | props: `mention_id`, `normalised_name`, `raw_place`, `event_year` |

`_norm(s)` upper-cases and strips every id-component string — node IDs are
case-insensitive keys built from `.strip().upper()`.

After committing, the function re-reads `SELECT node_id FROM graph_nodes`
into `node_set` — this becomes the **authoritative post-commit membership
set** used by step 3 to guard every edge endpoint.

### Step 2b — `write_reconciliation_gaps(conn)`
Appends (never overwrites, dedups by `townland_name`) rows to
`data/source_snapshots/reconciliation_gaps.csv` for every townland missing
`civil_parish`, `barony`, or `county`. Purely a data-quality audit trail —
does not affect the graph itself.

### Step 3 — `materialise_edges(conn, node_set)`
Writes edges through a `_safe_edge(src, dst, rel_type, props)` helper that
**refuses to write if either endpoint is absent from `node_set`**, instead
appending `{src, dst, rel_type, reason}` to a `skipped_edges` list that later
lands in the build report. This is the mechanism that guarantees
`graph_edges` never contains a dangling reference (step 6 asserts this with
a `dangling` count that must be 0 to pass).

Four edge groups, using a closed relationship-type vocabulary defined as
module-level string constants (never free text):
`HAS_EVENT, OCCURRED_IN, DEPARTED_VIA, MEMBER_OF, CO_RESIDENT_WITH,
CHIEF_TENANT_OF, UNDER_TENANT_OF, WITHIN, HAS_OBSERVATION, LOCATED_IN, NEAR,
REFERS_TO, SAME_AS, LINKED_TO, DERIVED_FROM, IN_COMMUNITY`.

1. **Place hierarchy (`WITHIN`)** — *nearest-available-ancestor* strategy.
   The canonical chain is `townland → parish → barony → county`. When an
   intermediate level is missing for a given townland row, the lowest
   present descendant links directly to the nearest present ancestor
   (e.g. no `civil_parish` but a `barony` present ⇒ `townland --WITHIN--> barony`
   directly). **No sentinel/placeholder nodes are ever created** — gaps are
   only logged to `reconciliation_gaps.csv` (step 2b). If a townland has none
   of parish/barony/county, it is left isolated in the hierarchy (may still
   be reachable via `Person`/`Observation` edges).
2. **Person → place + event edges** — from the same `unified_processed.csv`
   pass: `person --LOCATED_IN--> townland` (props: `{year}`); if
   `has_emigration_record`, `person --HAS_EVENT--> event:emigration:{rid}`,
   `event --OCCURRED_IN--> townland`, and (if a ship name is present) a
   `Voyage` node is lazily created (`voyage:{SHIP_NAME}`, inserted with
   `INSERT OR IGNORE` directly rather than via `_upsert_node`) with
   `event --DEPARTED_VIA--> voyage`. Eviction rows get the analogous
   `HAS_EVENT` / `OCCURRED_IN` pair without a voyage.
3. **Observation edges** — `census_record` and `clearances_record` rows are
   turned into synthetic `CensusObservation` / `ClearanceObservation` nodes
   (`obs:census:{TL}:{year}`, `obs:clearance:{TL}:{year}`) linked from their
   townland via `HAS_OBSERVATION`. These nodes are created inline in step 3,
   not in step 2, and are added to `node_set` on the fly.
4. **Entity-resolution links (`LINKED_TO`)** — if `workhouse_unified_links`
   exists, every row with `label IN ('CONFIRMED_MATCH','POSSIBLE_MATCH')`
   becomes `workhouse:{source_record_id} --LINKED_TO--> person:{unified_record_id}`
   with props `{confidence: score, band: label}`.

### Step 4 — `build_communities(conn)` — Louvain, not label propagation
Uses **`networkx.algorithms.community.louvain_communities`** (confirmed in
code, `seed=42` for determinism) — not label propagation, not
Leiden. The community-detection graph `G` is a plain undirected
`networkx.Graph` built *only* from edges with
`rel_type IN (LOCATED_IN, HAS_EVENT, MEMBER_OF)` — i.e. the signal is purely
**person↔townland / person↔event co-occurrence**; place-hierarchy (`WITHIN`)
and observation edges are excluded from the community signal.

For each detected partition `parts[idx]`:
- A `Community` node is upserted at `community:{idx}` with props
  `{summary, size}`. The `summary` string is generated **inline in Python**,
  not from an LLM and not from `community_summaries.json`:
  ```python
  summary = (
      f"Community {idx}: "
      + (f"{len(persons)} persons" if persons else "")
      + (f", {len(places)} places" if places else "")
      + f" ({len(members)} total nodes)"
  )
  ```
  where `persons`/`places` are just the first 5 members of each type found
  in the partition — a mechanical count, not a semantic digest.
- Every member node gets `graph_nodes.community = str(idx)` (a plain
  string column, not a foreign key) and an `IN_COMMUNITY` edge to its
  community node.

**Important distinction:** this auto-generated per-community `summary` prop
(read by `graphrag.py::_community_summaries_for()` at query time, see §2.6)
is a *different artifact* from `data/seed/community_summaries.json`, which is
a hand-authored, topic-keyed dictionary (`coolattin`, `estate`, `famine`, plus
4 more top-level keys observed) consumed only by `subgraph_engine.py`'s
`_get_community_summary()` for global "sensemaking" questions (§3). The two
never merge — `build_graph.py` neither reads nor writes
`community_summaries.json`.

### Step 5 — `build_embeddings(conn)` — passport embeddings, BGE-large, 1024-dim
Contrary to `docs/02_database_schema.md`'s note that embeddings are stored
"one per community summary node," the actual code embeds **individual
retrievable nodes**, not community nodes. `EMBED_LABELS` is the closed set:

```python
EMBED_LABELS = {"Person", "Townland", "CivilParish", "EmigrationEvent", "EvictionEvent"}
```

`Community`, `WorkhouseRecord`, `Barony`, `County`, `Voyage`, and the
observation node types are **never embedded**. Only rows where
`embedding IS NULL` are processed (incremental-safe), batched at 256 texts
per call. For each node a short "passport" string is built by
`_passport_text(node_id, label, name, props)`:

```python
# Person example
"Person: John Byrne; townland=AGHOWLE LOWER; year=1849; role=tenant; evicted"
# Townland example
"Townland: AGHOWLE LOWER; parish=Aghowle; barony=Shillelagh"
```

The batch is embedded via `backend.services.local_embeddings.embed_texts_local(texts, input_type="document")`
— the same **BAAI/bge-large-en-v1.5** SentenceTransformer model used
elsewhere in the retrieval stack (see `docs/09_retrieval_and_embeddings.md`
for `local_embeddings.py` internals). `local_embeddings.py` hard-asserts the
output dimension against `BGE_OUTPUT_DIMENSION = 1024` and raises loudly if
the model ever returns a different size — so every stored vector is
guaranteed 1024-dim float32. Each vector is packed with
`struct.pack(f"{len(vec)}f", *vec)` and written straight into the
`graph_nodes.embedding BLOB` column; there is no separate vector index —
retrieval reconstructs a dense matrix at load time (§2.1).

### Step 6 — `validate_and_report(...)`
Runs five integrity checks and writes a human-readable report to
`eval_results/graph_build_report.md` (git-ignored, regenerated every run):

1. **Orphan rate** — nodes with zero incident edges; warns if > 2%.
2. **Dangling edges** — `graph_edges` rows whose `src` or `dst` is absent
   from `graph_nodes`; any count > 0 is a **`[BLOCKING]` error**
   (should be structurally impossible given `_safe_edge`'s guard, so this
   check is a belt-and-braces regression detector).
3. **Hierarchy reachability** — `_check_reachability()` does a manual BFS
   (adjacency built from `WITHIN` edges) confirming every townland that
   *has* a `county` value can actually reach its `county:{C}` node via
   `WITHIN` hops; unreachable townlands are `[BLOCKING]`.
4. **Person→Townland integrity** — warns (does not block) if any `Person`
   node has no direct edge to a `Townland` node.
5. **Embedding coverage** — warns if any `Person`/`Townland`/`CivilParish`
   node is missing its passport embedding.

The report includes a full node-count-by-label table, a skipped-edges
breakdown grouped by reason prefix (first 50 rows itemised), and a final
`BUILD CLEAN` / `NEEDS FIXES` verdict. **Any `[BLOCKING]` error calls
`sys.exit(1)`** — the script fails the process, which matters for CI /
manual re-ingest workflows. Per `docs/02_database_schema.md` §1.12, the
graph stood at **49,081 nodes / 64,308 edges** at last recorded build.

At the very end of `main()`, after a successful (or even a failed-but-not-
exited) run, it best-effort calls `backend.services.graphrag.reload()` so
that if this script is invoked from *within* the same long-lived process
(e.g. a management command), the in-memory NetworkX cache picks up the new
rows immediately rather than waiting for the next process restart.

---

## 2. `backend/services/graphrag.py` — runtime NetworkX engine

Module docstring self-describes as replacing a former `neo4j_graphrag.py` —
"no external graph server required." Everything after `_load_graph()` is
pure in-memory Python; no SQLite or HTTP round-trips occur on the traversal
hot path.

### 2.1 Module-level singleton & thread safety

```python
_graph_lock = threading.Lock()
_GRAPH: Any = None          # networkx.MultiDiGraph — process-lifetime cache
_node_ids: list[str] = []   # parallel list of node_ids that have embeddings
_node_matrix: Any = None    # numpy float32 matrix of passport embeddings (N × 1024)
```

`_ensure_loaded()` takes the lock only to *read* the `_GRAPH is not None`
flag; the actual (expensive) `_load_graph()` call happens **outside** the
lock on first miss — so this is lazy init with a race window, not a
double-checked-locking pattern: two concurrent first-requests could both
call `_load_graph()`. Each call independently reassigns `_GRAPH`,
`_node_ids`, `_node_matrix` under the lock at the end, so the race is
benign (idempotent, no partial state visible to readers) but does mean the
graph could theoretically be built twice under concurrent cold-start load.
`reload()` (called by `build_graph.py` post-build, see §1 step 6) forces a
full re-read by nulling `_GRAPH` and calling `_load_graph()` again — no
graceful "swap" is performed; there's a brief window where `_GRAPH` is
`None` and `is_available()` would return `False`.

### 2.2 Graph type: `networkx.MultiDiGraph`

`_load_graph()` builds a **directed multigraph** (`nx.MultiDiGraph()`), not
a simple `DiGraph` — this matters because `graph_edges` has a composite
primary key of `(src, dst, rel_type)`, so two nodes can legitimately have
several parallel edges as long as `rel_type` differs (e.g. a person could in
principle have both a `LOCATED_IN` and a `HAS_EVENT`-adjacent edge to
related nodes with the same endpoints but different semantics). Node
attributes stored on each NetworkX node: `label`, `name`, `props` (parsed
JSON dict), `community` (string). Edge attributes: `rel_type` plus every key
from the edge's own `props` JSON, spread with `**ep`.

If the `graph_nodes`/`graph_edges` tables don't exist yet (schema created
but `build_graph.py` never run), `_load_graph()` logs
`graphrag.tables_not_found — run scripts/build_graph.py first` and sets
`_GRAPH` to an **empty** `MultiDiGraph()` rather than `None` — so
`is_available()` correctly reports `False` (0 nodes) without raising. Any
other exception during load is caught, logged as
`graphrag.load_failed error=%s`, and also degrades to an empty graph.

### 2.3 Availability gate

`is_available()` checks `ActiveConfig.GRAPHRAG_ENABLED` (env var
`GRAPHRAG_ENABLED`, default `"true"`) first, then lazily loads and checks
`_GRAPH.number_of_nodes() > 0`. Never raises — wrapped in a blanket
`except Exception: return False`.

### 2.4 Vector seeding — `vector_seed(question, *, top_k=None)`

Confirmed config-driven constant: `top_k` defaults to
`ActiveConfig.GRAPHRAG_VECTOR_TOP_K` = **8** (env `GRAPHRAG_VECTOR_TOP_K`).
Embeds the question via `local_embeddings.embed_texts_local([question], input_type="query")`
— using the BGE **query** prefix, asymmetric to the **document** prefix used
when building passport embeddings at build time (§1 step 5); this
query/document asymmetry is a documented BGE requirement (see
`docs/09_retrieval_and_embeddings.md`). Cosine similarity is computed as a
plain dot product (`matrix @ q`) because embeddings are stored unit-norm;
`scores.argsort()[::-1][:top_k]` selects the top-k node IDs. Returns `[]`
(never raises) if the matrix hasn't been built (e.g. `build_graph.py` never
ran, or no nodes in `EMBED_LABELS` exist).

### 2.5 Seeding — exact-match first, vector fallback

`retrieve_subgraph()` never goes straight to the embedding index. It first
calls `_seed_from_entity_hints(G, entity_hints, top_k)`:

1. **Exact townland** — if `entity_hints["canonical_townland"]` is set
   (passed in by the Ask pipeline's Phase 1 identity resolver, see
   `docs/05_*`/`06_*`), it's upper-cased and looked up directly as
   `townland:{NAME}`. If that node exists in `G`, it is the sole seed and
   `seed_modes = ["exact_townland"]`.
2. **Exact surname scan** — if `entity_hints["surname"]` is set, does a
   **linear scan** over `G.nodes(data=True)` for `Person` nodes whose
   `props["surname"]` matches, capped at 3 matches (`added >= 3`) and at
   `top_k` total. This is an O(N) scan over all ~49k nodes on every call
   that supplies a surname hint — no surname index exists.
3. Only if **neither** produces a seed does it fall back to
   `vector_seed(question, top_k=top_k)`, tagging `seed_modes = ["vector_seed"]`.

In the default Ask pipeline's actual call site (`ask_service.py`, §5 below),
`entity_hints` is always `{"canonical_townland": canonical_townland}` — the
surname-scan branch and the vector-seed fallback exist in the function but
are effectively unreachable from that call site since `retrieve_subgraph()`
is only invoked when `canonical_townland` is truthy and the exact-townland
node lookup will succeed whenever the townland resolved by Phase 1 exists in
the graph (which it will, since both are sourced from the same `townland`
table).

### 2.6 k-hop BFS — `_ego_edges(G, seed_ids, k)`

Not `nx.ego_graph` or `nx.bfs_edges` — a hand-rolled frontier expansion that
treats the multigraph as **undirected for traversal purposes** (both
`G.successors()` and `G.predecessors()` are unioned into the next frontier):

```python
def _ego_edges(G, seed_ids, k):
    valid_seeds = [n for n in seed_ids if G.has_node(n)]
    visited = set(valid_seeds)
    frontier = set(valid_seeds)
    for _ in range(k):
        next_frontier = set()
        for node in frontier:
            next_frontier.update(G.successors(node))
            next_frontier.update(G.predecessors(node))
        frontier = next_frontier - visited
        visited.update(frontier)
    # then collect every edge among `visited` nodes
```

`k_hops` defaults to `ActiveConfig.GRAPHRAG_K_HOPS` = **2** (env
`GRAPHRAG_K_HOPS`). After the node-visitation BFS completes, edges are
collected via `G.edges(visited, data=True)` filtered to `dst in visited` —
i.e. only edges with **both** endpoints inside the visited set are kept,
each formatted into a flat dict with `src_label`/`src_name`/`dst_label`/
`dst_name`/`rel_type`/`rel_props` for easy downstream linearisation.

### 2.7 Pruning — edges first, then nodes

Two independent caps, both enforced in `retrieve_subgraph()`:

- **`_MAX_TRIPLES = 200`** (module constant, not config-driven) — if the raw
  edge list from `_ego_edges` exceeds this, `_prune_edges()` ranks every
  edge by `_edge_priority(edge, question, seed_set)` (descending) and keeps
  the top 200 unique `(src, rel_type, dst)` triples. The priority function
  is a hand-tuned scoring heuristic, not a generic centrality measure:
  - `+100` if either endpoint is a seed node
  - `+200` if `rel_type == "WITHIN"` (place-hierarchy edges are strongly
    preferred)
  - `+120` if both endpoints are "place" nodes (`_is_place_node()` checks
    the `townland:`/`parish:`/`barony:`/`county:` id prefixes)
  - a further `+200` bonus when the question text contains `"same parish"`,
    `"same barony"`, or `"same county"` **and** the edge is a `WITHIN` edge
    matching that specific hierarchy level (keyword-gated re-ranking)
  - `-25` for `HAS_OBSERVATION`, `-40` for `HAS_EVENT`/`OCCURRED_IN`/
    `DEPARTED_VIA`, `-50` for `LOCATED_IN`/`IN_COMMUNITY` (these relation
    types are systematically deprioritised)
  - ties broken by `-len(rel_type)` (shorter relation names win)
- **`GRAPHRAG_MAX_NODES`** = **120** (env `GRAPHRAG_MAX_NODES`) — if the
  visited node set still exceeds this after edge pruning,
  `_prune_nodes_from_edges()` re-derives candidate nodes strictly from the
  surviving (already-pruned) edge endpoints, ranks them via
  `_node_priority()` (`+300` seed, `+200` place, `+50` `Community` label),
  and keeps the top 120; edges with either endpoint now outside that set are
  dropped in a final filter pass.

Both `edge_pruned` and `len(visited) > max_nodes` feed a single `pruned:
bool` flag on the result, surfaced in the Ask pipeline's SSE progress event
(`", pruned"` suffix, see §5).

### 2.8 Community summaries at query time

`_community_summaries_for(G, seed_ids)` walks each seed node's
`community` attribute, looks up the sibling `community:{id}` node, and pulls
its `props["summary"]` (the auto-generated Louvain summary string from
build step 4, §1 — *not* `community_summaries.json`). Deduplicates by
community id and caps at 5 summaries.

### 2.9 Linearisation — `_linearise(question, edges, community_summaries, seed_ids)`

Produces a Markdown-flavoured text block appended to `kg_context["subgraph_linearized"]`
downstream. Structure, in order:

1. **`### Community context`** — one bullet per community summary, e.g.
   `- Community 14: 42 persons, 3 places (58 total nodes)`.
2. **`### Place hierarchy`** — only emitted if at least one seed id starts
   with `townland:`. For the first such seed, it walks the `WITHIN` edges
   to find its parish, then the parish's barony, then the barony's county,
   emitting sentences like:
   ```
   ### Place hierarchy
   - AGHOWLE LOWER is in civil parish Aghowle.
   - Aghowle is in barony Shillelagh.
   - Shillelagh is in county Wicklow.
   ```
   If the question text contains `"same parish"`, it additionally lists
   sibling townlands sharing that parish id (capped at 30):
   `- Townlands in the same parish: BALLYKELLY, CORRAVANISH, ...`.
   Only the **first** matching townland seed is processed (`break` after the
   block) — multi-townland questions only get hierarchy prose for one seed.
3. **`### Subgraph triples`** — every deduplicated pruned edge rendered as a
   Cypher-like triple string:
   ```
   (Person:John Byrne)-[LOCATED_IN year=1849]->(Townland:AGHOWLE LOWER)
   (Townland:AGHOWLE LOWER)-[WITHIN]->(CivilParish:Aghowle)
   ```
   Relation properties (`rel_props`, up to 3 non-null keys) are rendered as
   a bracketed `key=value` suffix on the relation name.

The full function returns a single joined string (no hard length cap
in `graphrag.py` itself, unlike `subgraph_engine._linearize()`'s explicit
800-word truncation, §3.5) — length is naturally bounded by the 200-triple /
120-node caps upstream.

### 2.10 `retrieve_subgraph()` — orchestration and `comparison_subgraph()`

Public signature:
```python
def retrieve_subgraph(question, *, intent="relational",
                       entity_hints=None, k_hops=None) -> GraphRAGResult
```
Sequence: `is_available()` gate → resolve `k_hops`/`max_nodes`/`top_k` from
config → seed (§2.5) → `_ego_edges` (§2.6) → `_prune_edges` (§2.7) →
node-count re-check + `_prune_nodes_from_edges` if still over budget →
`_community_summaries_for` (§2.8) → `_linearise` (§2.9) → assemble
`GraphRAGResult`. The `path_used` field is a human-readable audit string,
e.g. `"exact_townland(1) → 2-hop BFS → 143 triples"`.

`comparison_subgraph(template_id)` is a one-line convenience wrapper:
`return retrieve_subgraph(template_id, intent="comparative")` — it treats
the template id string itself as the "question" text for seeding/keyword
purposes. It exists for COMPARATIVE-intent graph-side corroboration but is
not wired into the default pipeline's call sites found in `ask_service.py`
(the default pipeline only calls `retrieve_subgraph` directly with
`intent="relational"`, §5).

The dataclass returned, `GraphRAGResult`, carries `linearized`,
`subgraph_nodes`, `subgraph_rels`, `community_summaries`, `seed_nodes`,
`path_used`, `k_hops`, `pruned`, `sources_used` (always
`["in_process_graph"]` when populated), `available`, and
`degradation_note` — every failure path (unavailable graph, no seeds,
exception) returns a populated `degradation_note` instead of raising, per
the module's explicit design rule: *"Never raise; always degrade
gracefully."*

---

## 3. `backend/services/subgraph_engine.py` — live external SPARQL traversal

Confirmed distinct from `graphrag.py` in every respect: **no local graph
storage at all**. Every call either goes out over HTTP to the VRTI SPARQL
endpoint (`backend/integrations/vrti_sparql.py`) or to a locally-hosted
GraphDB SPARQL server (`backend/integrations/graphdb_sparql.py`, itself
still a network round-trip to `ActiveConfig.GRAPHDB_SPARQL_ENDPOINT`, just
not to an internet-hosted service). It is Phase 3 of the **legacy** Ask
pipeline only (`ASK_USE_NEW_PIPELINE=false`) and is dispatched for the
RELATIONAL/HERITAGE and COMPARATIVE routes per `CLAUDE.md`'s routing table —
confirmed by its own module docstring's "Core Rule 1" comment and by the
`is_subgraph_question()` gate (§3.2).

### 3.1 Activation keyword sets

Four frozensets of lowercase phrase literals gate whether this engine runs
at all, checked by simple substring containment (`kw in q`) — no NLP,
tokenisation, or stemming:

- `_RELATIONAL_KEYWORDS` — `"related to"`, `"connected to"`, `"connection between"`, `"link between"`, `"in the same parish"`, `"same parish"`, `"same barony"`, `"same county"`, `"part of"`, `"belong to"`, `"belongs to"`, `"neighbouring"`, `"neighboring"`, `"adjacent to"`, `"next to"`, `"bordering"`, `"how are"`, `"relationship between"`, `"linked to"`
- `_HIERARCHY_KEYWORDS` — `"which parish"`, `"what parish"`, `"civil parish"`, `"in the parish"`, `"in the barony"`, `"which barony"`, `"what barony"`, `"barony of"`, `"in the county"`, `"which county"`, `"what county"`, `"townlands in"`, `"in which"`, `"where is"`, `"where does"`, `"located in"`, `"situated in"`, `"falls within"`
- `_HERITAGE_KEYWORDS` — `"heritage"`, `"archaeological"`, `"monument"`, `"ring fort"`, `"holy well"`, `"history of"`, `"tell me about"`, `"describe"`, `"what is the history"`, `"historically"`, `"historic"`, `"fortification"`, `"earthwork"`
- `_SENSEMAKING_KEYWORDS` — `"overview"`, `"about the estate"`, `"about coolattin"`, `"what was"`, `"describe the estate"`, `"coolattin estate"`, `"what kind of"`, `"background"`, `"summary of"`, `"general context"`

(These are near-identical to, but a separately maintained superset/subset
of, the `intent_router.py` RELATIONAL keyword lists documented in
`CLAUDE.md` — the two are not the same Python objects.)

### 3.2 `is_subgraph_question()` — the entry gate

Returns `False` immediately (Core Rule 1) if: (a) the semantic layer already
produced a `slot_fill` with `confidence >= 0.80` (don't compete with a
high-confidence deterministic answer), or (b) `analysis["output_mode"] ==
"count"` **and** none of the four keyword sets match (a bare count question
gets no subgraph enrichment). Otherwise returns `True` if
`analysis["primary_intent"] == "geography"` or any keyword from any of the
four sets matches.

### 3.3 Seeding — `_seed_entities()`

Up to 3 seeds, built in priority order:
1. The Phase-1-resolved townland (`townland_resolution["name_norm"]`), if
   `townland_resolution["matched"]` is truthy — carries `kg_uri`, `sql_id`,
   `confidence`.
2. Up to 3 secondary townland hits from
   `backend.services.entity_resolver._get_index().search(...)` run against
   the question text with the primary townland's name stripped out via
   regex, `entity_type="townland"`, `min_score=0.65` — a **fuzzy/vector**
   secondary search distinct from `graphrag.py`'s BGE-embedding seed.
3. If no seeds were found at all and the question matches a
   `_SENSEMAKING_KEYWORDS` phrase, a synthetic virtual seed
   `{"label": "Coolattin Estate", "kg_uri": None, "entity_type": "estate", "confidence": 0.7}`
   is injected so global questions still get *something* to expand from.

### 3.4 Expansion — three VRTI traversals + one GraphDB traversal

`_expand_vrti(seeds)` only runs for seeds carrying a real `kg_uri` (the
synthetic "Coolattin Estate" seed is skipped, since `uri` is `None`). For
each qualifying seed it issues **three separate SPARQL queries** against the
VRTI endpoint (all confirmed in `backend/integrations/vrti_sparql.py`,
querying the named graph `PRESENT_DAY_PLACES_GRAPH` =
`https://kg.virtualtreasury.ie/graph/present-day-places-v1`):

1. **`get_place_hierarchy(uri)`** — single SPARQL `SELECT` with nested
   `OPTIONAL` blocks walking `crm:P89_falls_within` from townland → parish
   → barony → county in one query (not three round trips), returning
   `{townland_name, parish, barony, county}`.
2. **`get_sibling_townlands(uri, limit=20)`** — the genuine 2-hop traversal
   promised in the module docstring: `<uri> crm:P89_falls_within ?parish`
   then `?sibling crm:P89_falls_within ?parish` with
   `FILTER(?sibling != <uri>)`, `ORDER BY ?siblingName LIMIT 20`.
3. **`get_external_links(uri)`** — `<uri> ?pred ?obj` filtered to a fixed
   `IN (...)` predicate allow-list (`vrti:OsmIdentifier`,
   `vrti:OsiIdentifier`, `vrti:VrtiIdentifier`,
   `crm:P67i_is_referred_to_by`, `crm:P71i_is_listed_in`), excluding blank
   nodes.

Results across all seeds are merged: `hierarchy` dict is `.update()`d per
seed (last seed wins on key collision), `siblings` and `external_links` are
accumulated then deduplicated/sorted (siblings capped at 20 after
dedup). Hierarchy edges are also captured as plain
`(subject_label, predicate_label, object_label)` triples, e.g.
`("Aghowle Lower", "falls within parish", "Aghowle")`, for the pruning/
linearisation stage.

`_expand_graphdb(seeds)` first calls `graphdb_sparql.probe()` (a cached
`/size` REST health check, TTL-based cache — success cached longer than
failure) and bails to `[]` immediately if the GraphDB server is
unreachable. If reachable, for every seed with a `label`, calls
`graphdb_sparql.get_entity_neighborhood(name, k=2, max_nodes=40)` — a k=2
hop expansion over the locally-hosted `co:` ontology repository, returning
up to 40 `(subject, predicate, object)` triples per seed with predicate URIs
converted to human-readable labels; skips blank nodes and geometry
literals. The combined pool across all seeds is capped at 80 triples
(`triples[:80]`).

### 3.5 Pruning and linearisation

`_prune(triples, max_triples=40)` — much smaller budget than `graphrag.py`'s
200, and a much simpler heuristic: sort by `len(predicate_label)` ascending
(shorter predicate ⇒ assumed more informative) and truncate. No
seed-proximity or place-node bonus scoring like `graphrag.py`'s
`_edge_priority`.

`_linearize(seeds, vrti_data, gdb_triples, community_summary)` builds a
plain-text (not Markdown-triple-syntax) block, **hard-capped at 800 words**
(`text.split()`, truncated with a literal `"[truncated]"` marker appended).
Section order: `[Subgraph for: <seed labels>]` header → `Administrative
Hierarchy (VRTI Knowledge Graph):` single-line breadcrumb (`primary → civil
parish: X → barony: Y → county: Z`) → `Additional VRTI Relationships:` (any
triples not already folded into the hierarchy line, capped at 10) →
`Townlands in the same civil parish: A, B, C [+N more]` → `External
Identifiers: OSM: ... | OSI: ... | VRTI: ... | Link: ...` → `GraphDB (co:
ontology) Relationships:` (capped at 15) → `Context Summary:` (the
`community_summary` text, if any). A plausible rendered example:

```
[Subgraph for: Aghowle Lower]

Administrative Hierarchy (VRTI Knowledge Graph):
  Aghowle Lower → civil parish: Aghowle → barony: Shillelagh → county: Wicklow

Townlands in the same civil parish: Ballykelly, Corravanish, Kilcavan [+4 more]

External Identifiers: OSM: way/12345 | VRTI: place_9981

GraphDB (co: ontology) Relationships:
  Aghowle Lower — co:hasEvent — event_CL2868
  Aghowle Lower — co:hasEvent — event_CL5948
```

### 3.6 Community summaries — the OTHER source

`_get_community_summary(question)` (subgraph_engine's own, separate from
`graphrag.py`'s per-community Louvain summaries, §2.8) reads
`data/seed/community_summaries.json` once into a module-level
`_SUMMARIES_CACHE` dict. Matching is substring containment: if any
dictionary key (e.g. `"famine"`) literally appears in the lowercased
question, its value is returned. Failing that, if any
`_SENSEMAKING_KEYWORDS` phrase matched, it falls back to
`summaries.get("coolattin") or summaries.get("estate")`. This is the only
place in the codebase that reads `community_summaries.json` — it is a
**hand-authored, static JSON dict** (7 top-level keys observed, including
`coolattin`, `estate`, `famine`), not something regenerated by
`build_graph.py` or any other automated pipeline.

### 3.7 `retrieve_subgraph()` public entry point

`SubgraphResult` dataclass fields: `seed_entities`, `hierarchy`, `siblings`,
`external_links`, `triples_vrti`, `triples_graphdb`, `community_summary`,
`linearized`, `sources_used` (`"vrti"` / `"graphdb"` appended only if that
source actually returned non-empty data), `question_type` (one of
`hierarchy`/`relational`/`heritage`/`sensemaking`, from `_classify()`),
`pruned`, `k_hops` (always `2`, hardcoded, not config-driven — unlike
`graphrag.py`'s `GRAPHRAG_K_HOPS`). Wrapped in a top-level
`try/except Exception` that logs and returns the default (empty)
`SubgraphResult()` on any failure — never raises, matching `graphrag.py`'s
degradation contract but via `log.error` rather than `log.warning`.

---

## 4. `backend/services/kg_service.py` — geographic hierarchy + SPARQL-vs-SQL comparison

Third and last KG mechanism. Per its own docstring, the KG here "now
represents the pure geographic hierarchy of the Coolattin estate area:
County → Barony → CivilParish → Townland," enriching the Ask page's
townland context and powering the **KG Explore** visualisation
(`/kg` page, routes in `backend/routes/kg_explore.py`).

### 4.1 Thread-safe caches

Two independent module-level locks, mirroring the pattern in `graphrag.py`:

```python
_GRAPH_CACHE: dict | None = None
_GRAPH_CACHE_LOCK = threading.Lock()
_RDF_GRAPH: Any = None
_RDF_GRAPH_LOCK = threading.Lock()
_MAX_PERSONS = 600   # cap for D3.js performance (declared but unused in build_graph())
```

`build_graph(limit=_MAX_PERSONS)` (note: **same function name as, but a
completely different implementation from,** `scripts/build_graph.py`'s
module-level `main()` driver — do not confuse the two) takes the cache lock,
returns the cached `{nodes, edges, meta}` dict if already built, otherwise
calls `_build_geographic_graph()` once and caches the result for the rest of
the process lifetime. `reset_graph_cache()` clears it (nulls `_GRAPH_CACHE`
under the lock) for a forced rebuild on next request — there is no
file-watcher or automatic invalidation; a route/administrator action would
need to call this explicitly, though no such call site was found wired to
any route in `kg_explore.py`.

### 4.2 `_build_geographic_graph()` — request-time SQL, not the graph_nodes table

This function queries the **live `townland` table** directly (a single SQL
join with a `record_count` subquery against `unified_record`), filtered to
`UPPER(t.county) = 'WICKLOW'`, ordered by `barony, civil_parish, name`. It
builds a 4-level D3-ready node/edge structure from scratch on first request:

- `County` nodes (`county_{name}`, color `#0369a1`, size 28)
- `Barony` nodes (`barony_{name}`, color `#b45309`, size 20) with a
  `county_{}` → `barony_{}` `"contains"` edge
- `CivilParish` nodes (`parish_{name}`, color `#7c3aed`, size 14) with a
  `barony_{}` → `parish_{}` (or, if barony missing, `county_{}` →
  `parish_{}`) `"contains"` edge
- `Townland` nodes (`t_{name}`, color `#15803d`, size scaled by
  `record_count`: `min(8 + rec_cnt // 40, 14)`) carrying `name_gaelic`,
  `civil_parish`, `barony`, `county`, `electoral_division`,
  `placename_theme`, `centroid_lat/lon`, `kg_uri`, `record_count`

This graph is **entirely disjoint from `graph_nodes`/`graph_edges`** — it is
rebuilt from raw SQL every time the cache is cold, has no embeddings, no
communities, and no relationship to the NetworkX graph in `graphrag.py`.
`meta` reports `node_count`, `edge_count`, and per-level counts
(`county_count`, `barony_count`, `parish_count`, `townland_count`,
`with_gaelic`) plus `source: "geographic_hierarchy"`.

### 4.3 rdflib SPARQL-vs-SQL comparison engine

`_ttl_path()` resolves to `BASE_DIR / "data" / "coolattin_sample.ttl"` —
**not** `data/seed/coolattin.ttl`. These are two separate Turtle files
generated by the same underlying uplift logic
(`scripts/rdf_uplift.py`, which by default writes to
`data/seed/coolattin.ttl` from `unified_record`/`townland`/
`census_record`/`clearances_record`, and can also target a custom/limited
output path via `--limit`). `coolattin_sample.ttl` is the one actually
wired into the running app (225,362 lines / ~143,000 triples per code
comments in `scripts/generate_report_docx.py`), while
`data/seed/coolattin.ttl` (311,654 lines, the fuller uplift) is not read by
any Python module in the current codebase — it exists as a seed artifact
only. `_load_rdf_graph()` parses the file once into an
`rdflib.ConjunctiveGraph()` under `_RDF_GRAPH_LOCK`, logging
`kg_service.rdf_loaded | triples=%d ms=%d`; returns `None` gracefully (with
a `kg_service.ttl_missing` warning) if the file is absent, which `run_sparql()`
turns into a user-facing `"RDF graph not available — coolattin_sample.ttl not found."`
error string rather than raising.

`run_sparql(sparql_body)` prepends a fixed 6-line `PREFIX` block (`co:`,
`ex:`, `schema:`, `xsd:`, `rdf:`, `rdfs:`) and executes the query via
rdflib's built-in SPARQL engine — genuinely local, no HTTP. `run_sql(sql)`
is the SQL counterpart, hard-restricted to statements starting with
`SELECT` (case-insensitive check on `.strip().upper()`), executed against
the normal `extensions.get_db_conn()` SQLite connection, capped at
`max_rows` (default 500).

### 4.4 Comparison-scenario helpers

`COMPARISON_SCENARIOS` — a hardcoded list of 4 dicts (`id`, `label`,
`description`, `sql`, `sparql`), each a matched SQL/SPARQL pair over the
same logical question, intended for the KG Explore page's side-by-side
"technical details" panel per the module docstring:

1. `emigration_count_by_townland` — emigrants grouped by townland, with an
   explanatory note about NULL-townland filtering to keep the two query
   semantics comparable.
2. `eviction_count_by_year` — evictions grouped by year, similarly NULL-filtered.
3. `surname_frequency` — top-10 surnames; description notes this is "the
   cleanest scenario" where both stores agree exactly.
4. `person_event_detail` — person + event join, contrasting SPARQL's
   implicit graph-walk join against SQL's explicit self-join pattern.

These are served verbatim by `GET /api/kg/scenarios` and executed by
`POST /api/kg/compare` (either by `id` or with ad hoc `custom_sql`/
`custom_sparql` overrides), running both `run_sql()` and `run_sparql()` and
returning row counts, sampled rows (capped at `DISPLAY_CAP = 500`), and
timing for each side.

Two further LLM-backed helpers live in this file, both used by the KG
Explore page and unrelated to the Ask pipeline:

- **`get_townland_rich_detail(townland_name)`** — aggregates local DB stats
  (townland reference row, census trend, clearances, heritage features,
  people summary, top-5 surnames), optionally enriches with a live VRTI
  lookup (`vrti_sparql.get_townland_details_by_name`), then makes **two**
  sequential OpenRouter LLM calls: first asking the model to *write* an
  optimised VRTI SPARQL query for this townland (candidate models cascade
  through `openai/gpt-oss-20b:free` → `meta-llama/llama-3.3-70b-instruct:free`
  → `google/gemma-3-27b-it:free`), executing that LLM-generated query
  live against VRTI, then a second LLM call to write a 3–4 paragraph
  academic narrative grounded in both the local stats and the SPARQL
  results.
- **`explain_mismatch(sql_query, sparql_query, sql_rows, sparql_rows, ...)`**
  — takes a completed SQL/SPARQL comparison-scenario result pair and asks
  the same LLM cascade to produce a structured Markdown analysis (fixed
  section headers: Root Cause / Technical Explanation / All Possible
  Reasons / Data Evidence / Conclusion) of *why* the two result sets
  differ, framed around the closed-world (SQL/NULL-inclusive) vs
  open-world (SPARQL/triple-must-exist) assumption gap. Reasons are
  additionally regex/line-parsed out of the "All Possible Reasons" section
  into a `reasons: list[str]` for structured UI rendering.

---

## 5. Integration point with the Ask pipeline

The deep pipeline mechanics (SSE staging, synthesis, fusion) are documented
in the sibling Ask-pipeline docs; this section only anchors the call
boundary.

**Default pipeline** (`_orchestrated_pipeline_stream()` in `ask_service.py`,
~line 2975): once Phase 1 identity resolution has produced a
`canonical_townland`, and only if truthy, the pipeline imports
`graphrag.is_available` / `graphrag.retrieve_subgraph` and calls
`retrieve_subgraph(question, intent="relational", entity_hints={"canonical_townland": canonical_townland})`.
The result's `.linearized` text is appended (not replacing any existing
content) to `kg_context["subgraph_linearized"]` under an explicit
`"\n\n### Property-graph context\n"` header, additive alongside whatever
VRTI SPARQL enrichment (`_kg_context()`) already populated that key. An SSE
`progress` event (`stage="querying_graphrag"`) reports seed/triple/hop
counts and a `", pruned"` suffix to the frontend while this runs.

**Legacy pipeline**: `subgraph_engine.is_subgraph_question()` /
`retrieve_subgraph()` are invoked (around `ask_service.py` line 4025) only
when either the intent router forced `_force_subgraph=True`
(RELATIONAL/COMPARATIVE routes) or `is_subgraph_question()` independently
detects a relational/heritage/hierarchy/sensemaking signal. Its
`.linearized` text is written directly into
`kg_context["subgraph_linearized"]` (no additive header, since in this
pipeline it's the sole subgraph contributor — `graphrag.py` is not called
from the legacy code path at all).

In both pipelines, `kg_context["subgraph_linearized"]` ultimately feeds
Phase 7 synthesis as one block of context text alongside the SQL result
table and the VRTI townland/parish enrichment — see the sibling
synthesis-stage documentation for how the LLM is prompted with it.

---

## 6. `/api/kg/*` routes and the KG Explore page (brief — see `13_api_routes.md`)

`backend/routes/kg_explore.py` exposes: `GET /api/kg/graph` (serves
`kg_service.build_graph()`'s County→Barony→Parish→Townland D3 structure,
`limit` query param capped at 1000 but currently unused inside
`_build_geographic_graph()`), `GET /api/kg/scenarios` and
`POST /api/kg/compare` (the SQL-vs-SPARQL comparison panel, §4.3–4.4),
`POST /api/kg/explain-mismatch` (the LLM discrepancy analysis), `GET
/api/kg/graphdb-status` and `GET /api/kg/rdf-status` (health/diagnostics for
the GraphDB server and the local rdflib Turtle file respectively), and
`GET /api/kg/townland/<name>` / `GET /api/kg/townland-rich/<name>` (person
drill-down and the two-stage LLM rich-detail narrative, §4.4). None of these
routes touch `graph_nodes`/`graph_edges` or the NetworkX GraphRAG engine —
the KG Explore page is built entirely on `kg_service.py`'s independent
geographic-hierarchy graph and rdflib comparison engine.
