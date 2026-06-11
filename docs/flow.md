# GraphRAG Architecture — In-Process Graph (Neo4j Removed)

**Project:** Coolattin Estate Records Explorer
**Audience:** Claude coding agent.
**Supersedes:** `knowledge_graph_architecture_neo4j_graphrag.md`. Neo4j is removed; this is the canonical GraphRAG design.
**Companion docs:** `implementation_guide_er_graphrag_rag.md`, `ask_page_manual_testing_and_evaluation.md`.

---

## Decision recorded at the top

**Neo4j is removed.** The GraphRAG engine is now an **in-process property graph** materialised in the existing SQLite database and loaded into memory at startup. Rationale:

- **No extra infrastructure** — deploys on Azure App Service unchanged; no container, no Bolt endpoint, no credentials to secure.
- **Faster at this scale** — tens of thousands of nodes / ~100k edges traverse in-memory in milliseconds, with no network round-trip.
- **Single source of truth preserved** — the graph is a deterministic materialisation of SQLite; it rebuilds from one command.
- **Reproducible** — rebuilds with zero paid keys or external services.
- **ER unified with the graph** — resolution links are stored as edges in the same tables.

**What stays:** SQLite owns all exact counts (unchanged). The RDF/GraphDB store and `compile_sparql()` **remain for the RQ6 SQL-vs-SPARQL comparison and VRTI authority alignment** — Neo4j removal does not touch them. The in-process graph is the GraphRAG *enrichment engine*, not a comparison paradigm.

The iron rule holds: exact counts/aggregates come from SQL; the graph supplies relationships, paths, communities, and qualitative context.

---

## 1. Neo4j removal — cleanup checklist

Do this first, as one reviewable change. After it, `grep -ri "neo4j\|bolt://\|7687\|7474" .` should return only this doc and historical changelogs.

- Delete `backend/services/neo4j_graphrag.py`.
- Delete `scripts/neo4j_uplift.py`.
- `requirements.txt`: remove `neo4j>=5.0`. **Keep** `networkx>=3.0`.
- `config.py`: remove `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, `NEO4J_DATABASE`, `NEO4J_ENABLED`, `NEO4J_VECTOR_TOP_K`, `NEO4J_K_HOPS`. Replace with the in-process keys in §3.
- `.env.example` / `.env.local`: remove the Neo4j section and the Docker run instructions.
- `ask_service.py`: remove the Neo4j enrichment block, the `neo4j_comparison` field, and the three-paradigm fusion note. Replace the enrichment with the in-process graph call (§5). For COMPARATIVE intent, keep the SQL-vs-SPARQL comparison (RDF), drop the Cypher leg.
- Remove `eval_results/neo4j_uplift_report.md` and any references.
- Tests: delete/replace any Neo4j-specific tests; keep graph-behaviour tests, re-pointed at the in-process engine.
- Docs: remove Docker/Bolt/Azure-Neo4j instructions.

Acceptance: app boots and the full Ask pipeline runs with no Neo4j imports anywhere; the grep above is clean.

---

## 2. Storage design — the clean, intuitive graph

The whole graph is two tables. This is the canonical property-graph-on-SQLite pattern: neat, queryable, and a 1:1 mirror of the in-memory graph.

```sql
CREATE TABLE graph_nodes (
    node_id     TEXT PRIMARY KEY,    -- stable, human-readable: "person:CL2868", "townland:AGHOWLE_LOWER"
    label       TEXT NOT NULL,       -- Person | Townland | CivilParish | EmigrationEvent | ...
    name        TEXT,                -- display name
    props       TEXT,                -- JSON: the long tail (year, role, holding_acres, lat/lon, authority IDs)
    community   TEXT,                -- community id (set in §4)
    embedding   BLOB                 -- BGE-1024 passport vector (or store via existing embedding layer)
);
CREATE INDEX idx_gn_label ON graph_nodes(label);

CREATE TABLE graph_edges (
    src         TEXT NOT NULL REFERENCES graph_nodes(node_id),
    dst         TEXT NOT NULL REFERENCES graph_nodes(node_id),
    rel_type    TEXT NOT NULL,       -- small controlled vocabulary (see below)
    props       TEXT,                -- JSON: {confidence, band, year, km, ...}
    PRIMARY KEY (src, dst, rel_type)
);
CREATE INDEX idx_ge_src ON graph_edges(src, rel_type);
CREATE INDEX idx_ge_dst ON graph_edges(dst, rel_type);
```

Conventions that keep it neat (enforce these in the builder):
- **Stable string IDs** prefixed by type (`person:`, `townland:`, `event:`, `family:`, `community:`). Self-documenting, debuggable, and stable across rebuilds.
- **`rel_type` is a closed vocabulary**, not free text: `HAS_EVENT`, `OCCURRED_IN`, `DEPARTED_VIA`, `MEMBER_OF`, `CO_RESIDENT_WITH`, `CHIEF_TENANT_OF`, `UNDER_TENANT_OF`, `WITHIN`, `HAS_OBSERVATION`, `LOCATED_IN`, `NEAR`, `REFERS_TO`, `SAME_AS`, `LINKED_TO`, `DERIVED_FROM`, `IN_COMMUNITY`. Define it as an enum in code.
- **Properties go in `props` JSON**, never as ever-growing columns. Keeps the schema two tables forever.

Node labels and the relationship model are the same rich model from the previous spec (Person, Mention, Family, Townland/Parish/Barony/County, the three Event types, Voyage, Census/Clearance observations, HeritageFeature, WorkhouseRecord, Community, Source) — only the storage changed. The richness (typed edges, place hierarchy as edges, identity links) is what makes traversal worthwhile.

---

## 3. Config (replaces the Neo4j keys)

```
GRAPHRAG_ENABLED      = true
GRAPHRAG_VECTOR_TOP_K = 8       # vector seeds per query
GRAPHRAG_K_HOPS       = 2       # traversal depth from each seed
GRAPHRAG_MAX_NODES    = 120     # subgraph size cap before linearisation
```

No URI, no credentials — the engine reads from the local SQLite file.

---

## 4. Build pipeline — `scripts/build_graph.py` (replaces the uplift)

Deterministic, idempotent, validated. SQLite is both source and store.

1. Create `graph_nodes` / `graph_edges` (or `--wipe` and rebuild).
2. Materialise nodes from existing tables: places + hierarchy → persons + mentions → events + voyages → census/clearance observations → heritage → workhouse records.
3. Materialise edges, including **ER outputs** (`SAME_AS`, `LINKED_TO` from the entity-resolution tables), families/co-residence/tenant relationships, and provenance (`DERIVED_FROM`).
4. **Communities:** run Louvain via NetworkX over the Person/Family/Townland co-occurrence subgraph; write `community` on nodes and create `:Community` nodes + `IN_COMMUNITY` edges. Precompute a short factual summary per community and store it (label it if LLM-glossed).
5. **Embeddings:** build a passport text per retrievable node, embed with local BGE-1024, store on the node (or via the existing embedding layer keyed by `node_id`). Reuse the existing ANN/pgvector — do **not** build a new vector store.
6. **Validate and report** → `eval_results/graph_build_report.md`: node/edge counts by type reconcile with SQLite; orphan rate; integrity rules (every Person reaches a Townland; every Townland has a parish; every HeritageFeature has `LOCATED_IN`). Fail loudly, never silently.

Run: `python3 scripts/build_graph.py [--wipe]`.

---

## 5. Runtime — `backend/services/graphrag.py` (replaces `neo4j_graphrag.py`)

Load the graph into memory once, cache for process lifetime (same pattern as `_UNIFIED_CACHE` / `_GRAPH_CACHE`).

```python
# process-lifetime in-memory graph, built from graph_nodes/graph_edges
_GRAPH = None   # networkx.MultiDiGraph

def is_available() -> bool: ...                 # graph loaded & non-empty; never raises
def vector_seed(question) -> list[node_id]: ... # BGE embed → ANN over node passports → top-k
def retrieve_subgraph(question) -> Subgraph: ... # seed → k-hop traversal → prune → linearise (+ community summary)
def comparison_subgraph(template_id) -> ...      # graph-side corroboration for relational templates
```

Retrieval flow:
1. Embed the question (BGE query prefix) → ANN over node passport vectors → top-`K` seeds.
2. From each seed, k-hop traversal over the in-memory graph (NetworkX `ego_graph` / BFS).
3. Prune to relevance; cap at `GRAPHRAG_MAX_NODES`.
4. Linearise to a compact triple table / prose block; attach the seed's community summary.
5. Hand to grounded synthesis (numeric-consistency gate from the implementation guide).
6. Emit the traversed subgraph + the node/edge path as **provenance** (this is the explainability story — the answer traces to a visible path).

Graceful degradation: `GRAPHRAG_ENABLED=false` or empty graph → pipeline answers exactly as before, enrichment omitted with a note. Never block the answer.

**Performance note:** NetworkX is more than fast enough at this scale (k-hop and Louvain are sub-second on tens of thousands of nodes). If profiling ever shows the graph stage as a bottleneck, `rustworkx` is a near-drop-in, Rust-backed replacement with the same model — swap it then, not now. Don't add it pre-emptively.

---

## 6. Entity resolution integration

The graph and ER are one system:
- The ER pipeline (Phase 3 of the implementation guide) writes its accepted links into `graph_edges` as `SAME_AS` (person↔person) and `LINKED_TO` (person↔workhouse) with `props={confidence, band}`.
- Identity is modelled as `:Mention -[:REFERS_TO]-> :Person`, so disambiguation is traversable and explainable.
- A person-passport answer can then walk `SAME_AS` / `LINKED_TO` / `MEMBER_OF` to assemble the full picture (e.g., Edward Dagg → emigration event → Dunbrody → Aghowle → parish → family → any workhouse link), with every hop traceable.
- Counts of *distinct people* still come from SQL (`COUNT(DISTINCT record_id)`); the graph supplies the relationships, not the totals.

---

## 7. Evaluation gates

**Build gate (after `build_graph.py`):** counts reconcile with SQLite; orphan rate below 2%; integrity rules pass; every retrievable node has a passport embedding.

**GraphRAG value gate (testing protocol §9):** R1–R4 + multi-hop questions, enrichment OFF vs ON — numeric correctness delta exactly zero; every enriched fact traces to a node/edge; provenance carries the subgraph + path; latency cost recorded; degradation path verified.

**Regression gate:** the full Ask scenario catalogue still passes after Neo4j removal (no path depended on Neo4j).

---

## 8. What NOT to do

- Do not reintroduce a separate graph server — the in-process graph is the design.
- Do not let the graph author or alter counts — SQL owns numbers; the graph corroborates.
- Do not grow `graph_nodes`/`graph_edges` beyond two tables — long-tail attributes go in `props` JSON.
- Do not build the graph or communities at query time — materialise and cache.
- Do not remove the RDF/GraphDB store — RQ6 and VRTI alignment depend on it.
- Do not embed with anything other than the system's BGE-1024 model.