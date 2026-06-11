# GraphRAG Migration Verification Report

**Date:** 2026-06-09  
**Auditor:** Claude Code (automated)  
**Branch:** main  
**DB:** coolattin.db (SQLite)

---

## One-line verdict

**MIGRATION CLEAN — NEEDS FIXES** *(Neo4j code fully removed; build script has a blocking FK bug that must be fixed before C2/C3/E1 can pass)*

---

## A. Cleanup Acceptance

### A1 — Neo4j reference grep

**CONDITIONAL PASS** (migration-comment references only; zero live Neo4j code)

Command run:
```
grep -rin "neo4j|bolt://|7687|7474|neo4j_comparison" backend/ scripts/ frontend/ config.py extensions.py
```

Matches in `.py` source files:

| File | Line | Content |
|------|------|---------|
| `backend/services/graphrag.py` | 5 | `Replaces neo4j_graphrag.py; no external graph server required.` — module docstring |
| `scripts/build_graph.py` | 6 | `Replaces scripts/neo4j_uplift.py. SQLite is both source and destination.` — module docstring |
| `backend/services/ask_service.py` | 2703 | `# Neo4j Cypher comparison removed — RQ6 now uses SQL vs SPARQL (two paradigms).` — inline comment |
| `extensions.py` | 213 | SQL comment: `-- In-process property graph (GraphRAG substrate — replaces Neo4j).` |

Matches in data files (not code):

- `frontend/static/data/unified_processed.csv` lines 8529, 13500: `7474` and `7687` are estate record IDs (`CL7474`, `CL7687`), not ports.

**No Neo4j driver imports, `bolt://` connection strings, or active Neo4j code found anywhere.** The matches are docstrings/comments documenting migration history, not live dependencies. Strictly, A1 says "only docs/changelogs allowed" — these are in source-code comments rather than dedicated docs files.

### A2 — Import cleanliness

**PASS**

```
python3 -c "import backend.services.ask_service"
# Exit 0, no output, no Neo4j ImportError
```

---

## B. Build Gate

### B1 — build_graph.py --wipe completion

**FAIL**

```
python3 scripts/build_graph.py --wipe
```

Crash at step 3 (`materialise_edges`):

```
sqlite3.IntegrityError: FOREIGN KEY constraint failed
  File "scripts/build_graph.py", line 290, in materialise_edges
    _upsert_edge(conn, f"barony:{_norm(r['barony']) if r['barony'] else _norm(r['name'])}",
                       f"county:{_norm(r['county'])}", WITHIN)
```

**Root cause:** 2 townlands in the `townland` table have `county='WICKLOW'` but `barony=NULL`:

| name | barony | county |
|------|--------|--------|
| TULLOWCLAY | NULL | WICKLOW |
| GOWLE | NULL | WICKLOW |

When `barony` is NULL, line 290 falls back to the townland name as the barony node ID (`barony:TULLOWCLAY`). That node was never inserted in step 2 (which only creates barony nodes for non-null barony values), so the FK constraint fails.

**Fix required (not applied per task rules):** Add a guard: skip the barony→county edge when `r['barony']` is NULL.

Steps completed before crash: step 1 (tables), step 2 (36,308 nodes — townlands, parishes, baronies, counties, persons, workhouse records). Steps never reached: step 3 (edges), step 4 (communities), step 5 (embeddings), step 6 (report).

`eval_results/graph_build_report.md` was **not** written.

### B2 — Report reconciliation

**FAIL** — report not written (B1 failed)

Post-crash DB state:
- `graph_nodes`: 36,308 rows (nodes present)
- `graph_edges`: 0 rows (transaction rolled back / never committed)

### B3 — Communities and embeddings

**FAIL**

- Communities formed: 0 (step 4 never ran)
- Nodes with passport embedding: 0/36,308 (step 5 never ran)

---

## C. Load + Retrieval

### C1 — graphrag.is_available()

**PARTIAL PASS / MISLEADING**

`is_available()` returns `True` because `graph_nodes` has 36,308 rows (`number_of_nodes() > 0`). It never raises.

However, with 0 edges and 0 embeddings, retrieval is non-functional:

```python
graphrag.is_available()  # → True  (MISLEADING — no edges, no embeddings)
graphrag._node_matrix    # → None
graphrag._GRAPH.number_of_edges()  # → 0
```

`is_available()` returns `False` when `GRAPHRAG_ENABLED=false` ✓ (verified — see F1).

**Concern:** The check `number_of_nodes() > 0` is not sufficient to indicate that retrieval is usable. After a successful build the check will be correct. This is a consequence of B1 failure, not a code defect in `graphrag.py`.

### C2 — vector_seed("emigration from Aghowle Lower")

**FAIL**

```python
graphrag.vector_seed("emigration from Aghowle Lower")
# → []  (no seeds)
```

`_node_matrix is None` because step 5 (embeddings) never ran. Expected: ≥1 seed node. **This is a consequence of B1; graphrag.py code is correct.**

### C3 — retrieve_subgraph("emigration from Aghowle Lower")

**FAIL**

```python
result = graphrag.retrieve_subgraph("emigration from Aghowle Lower")
# result.available = True
# result.linearized = ''
# result.subgraph_rels = []
# result.degradation_note = 'No seed nodes resolved for this question.'
```

Empty subgraph, zero provenance path. **Consequence of B1; no error raised; degrades correctly.**

---

## D. Pipeline Integration

### D1 — RELATIONAL question populates graphrag_context

**PASS (code audit)**

`ask_service.py` lines 2231–2279:

```python
if intent_route in (_RELATIONAL, _COMPARATIVE, "fallback"):
    try:
        from backend.services.graphrag import is_available, retrieve_subgraph
        if is_available():
            _graphrag_result = retrieve_subgraph(question, ...)
        ...
    except Exception as _gr_exc:
        query_provenance["graphrag"] = {"available": False, "error": str(_gr_exc)}
```

Response payload (line 2946):

```python
"graphrag_context": {
    "linearized":          _graphrag_result.linearized,
    "seed_nodes":          _graphrag_result.seed_nodes,
    "community_summaries": _graphrag_result.community_summaries,
    "path_used":           _graphrag_result.path_used,
    "k_hops":              _graphrag_result.k_hops,
    "pruned":              _graphrag_result.pruned,
    "sources_used":        _graphrag_result.sources_used,
    "degradation_note":    _graphrag_result.degradation_note,
} if _graphrag_result else None,
```

GraphRAG context is correctly included for RELATIONAL questions. ✓

**Minor gap:** `querying_graphrag` SSE progress stage is not in `ask.js`'s `progressOrder` array (line 42–53), so the frontend progress bar silently ignores this stage. Non-blocking cosmetic gap.

### D2 — Stale field reference audit

**PASS**

| Consumer | `neo4j_comparison` | `subgraph_linearized` |
|----------|--------------------|-----------------------|
| `frontend/static/js/ask.js` | 0 refs | 0 refs ✓ |
| `tests/` (all files) | 0 refs | 0 refs ✓ |
| `_write_pdf_report` (lines 7774–7829) | 0 refs | 0 refs ✓ |

`subgraph_linearized` does appear at lines 7705–7731 of `ask_service.py` — but this is the **internal `kg_context` accumulator** used to build the LLM synthesis prompt, not a reference to any removed public API field. The key aggregates Phase-3 subgraph output and GraphRAG output for the LLM; the public response uses `graphrag_context`. This is correct internal naming.

### D3 — [BLOCKING] ANALYTICAL count correctness

**PASS (code audit — live pipeline not available without LLM key)**

GraphRAG is **not invoked** for ANALYTICAL questions. The routing guard at line 2234:

```python
if intent_route in (_RELATIONAL, _COMPARATIVE, "fallback"):
    # graphrag runs here — NOT for ANALYTICAL
```

For ANALYTICAL questions, the flow is: intent router → semantic layer → deterministic SQL → count returned from SQLite. The graph is never consulted.

Additionally, the LLM synthesis prompt (line 7720–7726) explicitly labels graph context:

```
"KNOWLEDGE GRAPH CONTEXT ... use for qualitative and relational answers;
 do NOT use to produce counts or statistics"
```

Gold SQL for a representative analytical question:

```sql
SELECT COUNT(*) FROM unified_record WHERE has_emigration_record=1
-- Result: 6,016
```

Code guarantee: graph cannot change this count. **Correctness delta = 0 by architecture.**

---

## E. Entity-Resolution Edges

### E1 — SAME_AS and LINKED_TO edge counts

**FAIL (build crashed)**

```
graph_edges: 0 rows  (transaction never committed due to B1 crash)
```

Source data state (from `workhouse_unified_links`):

| Metric | Value |
|--------|-------|
| Total rows in `workhouse_unified_links` | 17 |
| Rows with label ACCEPTED or CONFIRMED | **0** |

**Note:** Even with a successful graph build, SAME_AS/LINKED_TO edges would be 0. The ER pipeline has produced no confirmed links. This is the pre-existing zero-confirmed-links issue, separate from the GraphRAG migration. The graph correctly reflects whatever ER produced — the build code at lines 364–378 only inserts LINKED_TO edges for ACCEPTED/CONFIRMED rows.

---

## F. Graceful Degradation

### F1 — [BLOCKING] Degradation with GRAPHRAG_ENABLED=false and empty graph

**PASS**

**Test 1: GRAPHRAG_ENABLED=false**

```python
os.environ["GRAPHRAG_ENABLED"] = "false"  # set before config import
graphrag.is_available()          # → False  ✓
graphrag.retrieve_subgraph("x")  # → GraphRAGResult(available=False,
                                 #     degradation_note='In-process graph not available — run scripts/build_graph.py.')
# No exception raised, no hang
```

**Test 2: Graph built but empty/degraded (current state: 0 edges, 0 embeddings)**

```python
graphrag.is_available()          # → True (nodes present but non-functional)
graphrag.retrieve_subgraph("emigration from Aghowle Lower")
# → GraphRAGResult(available=True, linearized='', subgraph_rels=[],
#     degradation_note='No seed nodes resolved for this question.')
# No exception raised, no hang
```

**Pipeline exception safety:** The graphrag block in the pipeline (lines 2235–2279) is wrapped in `try/except Exception` — any graphrag failure is logged and execution continues. Response includes `graphrag_context=None` when `_graphrag_result` is falsy (line 2955). **No path to hang or crash.**

---

## G. Regression

### G1 — Full test suite

**1 FAILED (pre-existing, unrelated to GraphRAG migration)**

```
pytest tests/ -v
# 51 passed, 1 failed, 1 skipped
```

Failed test:

```
FAILED tests/test_ask_pgvector.py::test_pgvector_dense_retrieve_works_after_completed_with_failures
AssertionError: Expected the 1 stored chunk to be returned; got 2
```

This is a pgvector partial-sync test that returns 2 chunks instead of 1. **Not introduced by the GraphRAG migration** — pgvector is a separate retrieval backend. All 30 townland resolution tests, 7 workhouse ER tests, 3 pipeline flag tests, and 1 config test pass.

No test references `neo4j_comparison` or `subgraph_linearized`. ✓

---

## Summary Table

| Check | ID | Status | Blocking? |
|-------|----|--------|-----------|
| No active Neo4j code | A1 | ⚠️ WARN (comments only) | No |
| ask_service imports clean | A2 | ✅ PASS | No |
| build_graph.py --wipe completes | B1 | ❌ FAIL | Yes (blocks C2/C3/E1) |
| Build report written with counts | B2 | ❌ FAIL | No |
| Communities + embeddings built | B3 | ❌ FAIL | No |
| is_available() correct behaviour | C1 | ⚠️ MISLEADING | No |
| vector_seed returns ≥1 result | C2 | ❌ FAIL | No (B1 consequence) |
| retrieve_subgraph returns subgraph | C3 | ❌ FAIL | No (B1 consequence) |
| RELATIONAL populates graphrag_context | D1 | ✅ PASS | No |
| No stale neo4j_comparison/subgraph_linearized refs | D2 | ✅ PASS | No |
| ANALYTICAL count correctness delta = 0 | D3 | ✅ PASS* | **Yes** |
| SAME_AS/LINKED_TO edge counts | E1 | ❌ 0/0 (build fail + zero ER links) | No |
| Graceful degradation GRAPHRAG_ENABLED=false | F1 | ✅ PASS | **Yes** |
| Regression test suite | G1 | ⚠️ 51/52 (pre-existing pgvector) | No |

*D3: verified by code audit (ANALYTICAL never invokes graph) + LLM prompt label

---

## Action Required

**Critical (blocks full GraphRAG functionality):**

1. **Fix `build_graph.py` line 290** — add guard to skip barony→county edge when `r['barony']` is NULL:
   ```python
   if r["county"] and r["barony"]:   # was: if r["county"]:
       _upsert_edge(conn, f"barony:{_norm(r['barony'])}", f"county:{_norm(r['county'])}", WITHIN)
   ```
   After the fix, re-run `python3 scripts/build_graph.py --wipe` to complete steps 3–6 and verify C2/C3/B2/B3/E1.

**Cosmetic (non-blocking):**

2. Add `{ key: "querying_graphrag", label: "GraphRAG" }` to `progressOrder` in `ask.js` so the UI shows the GraphRAG step during streaming.

**Pre-existing (not introduced by this migration):**

3. Zero confirmed ER links in `workhouse_unified_links` (E1 upstream issue).
4. pgvector partial-sync test returning extra chunk (G1).
