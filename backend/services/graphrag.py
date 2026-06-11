"""
backend/services/graphrag.py

In-process property-graph GraphRAG engine for the Coolattin Ask pipeline.
Replaces neo4j_graphrag.py; no external graph server required.

The graph is materialised in graph_nodes / graph_edges tables (SQLite)
and loaded into a NetworkX MultiDiGraph once at startup.

Public API
----------
is_available() -> bool
    True if the in-process graph is loaded and non-empty; never raises.

vector_seed(question, *, top_k) -> list[str]
    BGE embed of question → cosine ANN over node passport vectors → top-k node_ids.

retrieve_subgraph(question, *, intent, entity_hints, k_hops) -> GraphRAGResult
    Seed → k-hop traversal → prune → linearise + community summary.

comparison_subgraph(template_id) -> GraphRAGResult
    Graph-side corroboration for relational templates (COMPARATIVE intent).

reload() -> None
    Force re-load from SQLite (call after build_graph.py runs).

Design rules (flow.md §5):
  - Counts/aggregates always come from SQL; graph results are corroboration only.
  - If the graph is empty/unavailable, pipeline answers as before — enrichment omitted.
  - Never raise; always degrade gracefully.
"""
from __future__ import annotations

import json
import logging
import struct
import threading
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

_graph_lock = threading.Lock()
_GRAPH: Any = None          # networkx.MultiDiGraph — process-lifetime cache
_node_ids: list[str] = []   # parallel list of node_ids that have embeddings
_node_matrix: Any = None    # numpy float32 matrix of passport embeddings (N × 1024)


# ---------------------------------------------------------------------------
# Graph loading
# ---------------------------------------------------------------------------

def _load_graph() -> None:
    """Load graph_nodes / graph_edges from SQLite into the process-lifetime cache."""
    global _GRAPH, _node_ids, _node_matrix
    try:
        import networkx as nx
        import numpy as np
        from extensions import get_db_conn

        conn = get_db_conn()
        try:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            if "graph_nodes" not in tables or "graph_edges" not in tables:
                log.info("graphrag.tables_not_found — run scripts/build_graph.py first")
                with _graph_lock:
                    _GRAPH = nx.MultiDiGraph()
                return

            G = nx.MultiDiGraph()
            ids: list[str] = []
            vecs: list[list[float]] = []

            for r in conn.execute(
                "SELECT node_id, label, name, props, community, embedding FROM graph_nodes"
            ).fetchall():
                props = json.loads(r["props"] or "{}") if r["props"] else {}
                G.add_node(
                    r["node_id"],
                    label=r["label"] or "",
                    name=r["name"] or "",
                    props=props,
                    community=r["community"] or "",
                )
                raw = r["embedding"]
                if raw:
                    dim = len(raw) // 4
                    ids.append(r["node_id"])
                    vecs.append(list(struct.unpack(f"{dim}f", raw)))

            for e in conn.execute(
                "SELECT src, dst, rel_type, props FROM graph_edges"
            ).fetchall():
                ep = json.loads(e["props"] or "{}") if e["props"] else {}
                if G.has_node(e["src"]) and G.has_node(e["dst"]):
                    G.add_edge(e["src"], e["dst"], rel_type=e["rel_type"], **ep)

            with _graph_lock:
                _GRAPH = G
                _node_ids = ids
                _node_matrix = np.array(vecs, dtype=np.float32) if vecs else None

            log.info(
                "graphrag.loaded | nodes=%d edges=%d embedded=%d",
                G.number_of_nodes(), G.number_of_edges(), len(ids),
            )
        finally:
            conn.close()
    except Exception as exc:
        log.warning("graphrag.load_failed error=%s", exc)
        try:
            import networkx as nx
            with _graph_lock:
                _GRAPH = nx.MultiDiGraph()
        except Exception:
            pass


def _ensure_loaded() -> None:
    with _graph_lock:
        already = _GRAPH is not None
    if not already:
        _load_graph()


def reload() -> None:
    """Force a reload from SQLite (call after build_graph.py runs)."""
    global _GRAPH
    with _graph_lock:
        _GRAPH = None
    _load_graph()


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class GraphRAGResult:
    """Structured output of the in-process GraphRAG retrieval."""
    linearized: str = ""
    subgraph_nodes: list[dict[str, Any]] = field(default_factory=list)
    subgraph_rels: list[dict[str, Any]] = field(default_factory=list)
    community_summaries: list[str] = field(default_factory=list)
    seed_nodes: list[dict[str, Any]] = field(default_factory=list)
    path_used: str = ""
    k_hops: int = 0
    pruned: bool = False
    sources_used: list[str] = field(default_factory=list)
    available: bool = False
    degradation_note: str = ""


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------

def is_available() -> bool:
    """Return True if the in-process graph is loaded and non-empty; never raises."""
    try:
        from config import ActiveConfig
        if not getattr(ActiveConfig, "GRAPHRAG_ENABLED", True):
            return False
        _ensure_loaded()
        with _graph_lock:
            return _GRAPH is not None and _GRAPH.number_of_nodes() > 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Vector seed
# ---------------------------------------------------------------------------

def vector_seed(question: str, *, top_k: int | None = None) -> list[str]:
    """
    Embed question with BGE-large (query prefix) and run cosine ANN over
    in-memory node passport vectors.  Returns node_ids, empty on error.
    """
    try:
        from config import ActiveConfig
        if top_k is None:
            top_k = int(getattr(ActiveConfig, "GRAPHRAG_VECTOR_TOP_K", 8))

        _ensure_loaded()
        with _graph_lock:
            matrix = _node_matrix
            ids = list(_node_ids)

        if matrix is None or not ids:
            return []

        import numpy as np
        from backend.services.local_embeddings import embed_texts_local
        vecs = embed_texts_local([question], input_type="query")
        if not vecs or not vecs[0]:
            return []

        q = np.array(vecs[0], dtype=np.float32)
        scores = matrix @ q            # dot product == cosine (unit-norm vectors)
        top_idx = scores.argsort()[::-1][:top_k]
        return [ids[i] for i in top_idx]
    except Exception as exc:
        log.warning("graphrag.vector_seed_failed error=%s", exc)
        return []


# ---------------------------------------------------------------------------
# k-hop BFS traversal
# ---------------------------------------------------------------------------

_MAX_TRIPLES = 200

_PLACE_PREFIXES = ("townland:", "parish:", "barony:", "county:")


def _is_place_node(node_id: str) -> bool:
    return node_id.startswith(_PLACE_PREFIXES)


def _seed_from_entity_hints(G: Any, entity_hints: dict[str, Any], top_k: int) -> tuple[list[str], list[str]]:
    """
    Resolve exact/local seed nodes from entity hints before any embedding call.
    """
    seed_ids: list[str] = []
    seed_modes: list[str] = []

    canonical_townland = str(entity_hints.get("canonical_townland") or "").strip().upper()
    if canonical_townland:
        tl_id = f"townland:{canonical_townland}"
        if G.has_node(tl_id):
            seed_ids.append(tl_id)
            seed_modes.append("exact_townland")

    surname = str(entity_hints.get("surname") or "").strip().upper()
    if surname:
        added = 0
        for nid, nd in G.nodes(data=True):
            if added >= 3 or len(seed_ids) >= top_k:
                break
            if nd.get("label") != "Person":
                continue
            if str(nd.get("props", {}).get("surname", "")).upper() != surname:
                continue
            if nid not in seed_ids:
                seed_ids.append(nid)
                added += 1
        if added:
            seed_modes.append("exact_surname_scan")

    return seed_ids[:top_k], seed_modes


def _ego_edges(G: Any, seed_ids: list[str], k: int) -> tuple[list[dict], set[str]]:
    """Collect all edges reachable within k hops from seed_ids."""
    valid_seeds = [n for n in seed_ids if G.has_node(n)]
    visited: set[str] = set(valid_seeds)
    frontier: set[str] = set(valid_seeds)

    for _ in range(k):
        next_frontier: set[str] = set()
        for node in frontier:
            next_frontier.update(G.successors(node))
            next_frontier.update(G.predecessors(node))
        frontier = next_frontier - visited
        visited.update(frontier)

    edges: list[dict] = []
    for src, dst, data in G.edges(visited, data=True):
        if dst not in visited:
            continue
        edges.append({
            "src": src,
            "src_label": G.nodes[src].get("label", ""),
            "src_name": G.nodes[src].get("name", src),
            "dst": dst,
            "dst_label": G.nodes[dst].get("label", ""),
            "dst_name": G.nodes[dst].get("name", dst),
            "rel_type": data.get("rel_type", "REL"),
            "rel_props": {k: v for k, v in data.items() if k != "rel_type"},
        })
    return edges, visited


def _edge_priority(edge: dict[str, Any], question: str, seed_set: set[str]) -> tuple[int, int]:
    q = (question or "").lower()
    src = edge.get("src", "")
    dst = edge.get("dst", "")
    rel = edge.get("rel_type", "")

    score = 0
    if src in seed_set or dst in seed_set:
        score += 100
    if rel == "WITHIN":
        score += 200
    if _is_place_node(src) and _is_place_node(dst):
        score += 120

    if "same parish" in q and rel == "WITHIN" and src.startswith("townland:") and dst.startswith("parish:"):
        score += 200
    if "same barony" in q and rel == "WITHIN" and src.startswith(("townland:", "parish:")) and dst.startswith("barony:"):
        score += 200
    if "same county" in q and rel == "WITHIN" and dst.startswith("county:"):
        score += 200

    if rel == "HAS_OBSERVATION":
        score -= 25
    elif rel in {"HAS_EVENT", "OCCURRED_IN", "DEPARTED_VIA"}:
        score -= 40
    elif rel in {"LOCATED_IN", "IN_COMMUNITY"}:
        score -= 50

    # Prefer shorter, more structural relations when scores tie.
    return score, -len(rel)


def _prune_edges(edges: list[dict], question: str, seed_ids: list[str], max_triples: int = _MAX_TRIPLES) -> tuple[list[dict], bool]:
    if len(edges) <= max_triples:
        return edges, False

    seed_set = set(seed_ids)
    ranked = sorted(
        edges,
        key=lambda edge: _edge_priority(edge, question, seed_set),
        reverse=True,
    )

    seen: set[tuple[str, str, str]] = set()
    pruned: list[dict] = []
    for edge in ranked:
        triple = (edge.get("src", ""), edge.get("rel_type", ""), edge.get("dst", ""))
        if triple in seen:
            continue
        seen.add(triple)
        pruned.append(edge)
        if len(pruned) >= max_triples:
            break
    return pruned, True


def _node_priority(node_id: str, seed_set: set[str], G: Any) -> tuple[int, str]:
    score = 0
    if node_id in seed_set:
        score += 300
    if _is_place_node(node_id):
        score += 200
    label = str(G.nodes[node_id].get("label", "")) if G.has_node(node_id) else ""
    if label == "Community":
        score += 50
    return score, node_id


def _prune_nodes_from_edges(
    G: Any,
    visited: set[str],
    edges: list[dict],
    seed_ids: list[str],
    max_nodes: int,
) -> set[str]:
    seed_set = set(seed_ids)
    candidate_nodes: set[str] = set(seed_ids)
    for edge in edges:
        candidate_nodes.add(edge["src"])
        candidate_nodes.add(edge["dst"])

    if not candidate_nodes:
        candidate_nodes = set(visited)

    if len(candidate_nodes) <= max_nodes:
        return candidate_nodes

    ranked = sorted(
        candidate_nodes,
        key=lambda node_id: _node_priority(node_id, seed_set, G),
        reverse=True,
    )
    return set(ranked[:max_nodes])


def _community_summaries_for(G: Any, seed_ids: list[str]) -> list[str]:
    summaries: list[str] = []
    seen: set[str] = set()
    for nid in seed_ids:
        if not G.has_node(nid):
            continue
        comm = G.nodes[nid].get("community", "")
        if not comm or comm in seen:
            continue
        seen.add(comm)
        comm_node = f"community:{comm}"
        if G.has_node(comm_node):
            s = G.nodes[comm_node].get("props", {}).get("summary", "")
            if s:
                summaries.append(s)
    return summaries[:5]


def _linearise(question: str, edges: list[dict], community_summaries: list[str], seed_ids: list[str]) -> str:
    lines: list[str] = []
    q = (question or "").lower()
    if community_summaries:
        lines.append("### Community context")
        lines.extend(f"- {s}" for s in community_summaries)
        lines.append("")

    # For place hierarchy questions, surface the hierarchy/sibling summary before raw triples.
    if edges and any(seed.startswith("townland:") for seed in seed_ids):
        within_lookup: dict[str, list[dict]] = {}
        for edge in edges:
            if edge.get("rel_type") != "WITHIN":
                continue
            within_lookup.setdefault(edge["src"], []).append(edge)

        for seed in seed_ids:
            if not seed.startswith("townland:"):
                continue
            parish_edge = next(
                (edge for edge in within_lookup.get(seed, []) if edge["dst"].startswith("parish:")),
                None,
            )
            if not parish_edge:
                continue
            parish_id = parish_edge["dst"]
            parish_name = parish_edge["dst_name"]
            lines.append("### Place hierarchy")
            lines.append(f"- {parish_edge['src_name']} is in civil parish {parish_name}.")

            barony_edge = next(
                (edge for edge in within_lookup.get(parish_id, []) if edge["dst"].startswith("barony:")),
                None,
            )
            if barony_edge:
                lines.append(f"- {parish_name} is in barony {barony_edge['dst_name']}.")
                county_edge = next(
                    (edge for edge in within_lookup.get(barony_edge["dst"], []) if edge["dst"].startswith("county:")),
                    None,
                )
                if county_edge:
                    lines.append(f"- {barony_edge['dst_name']} is in county {county_edge['dst_name']}.")

            if "same parish" in q:
                siblings = sorted(
                    edge["src_name"]
                    for edge in edges
                    if edge.get("rel_type") == "WITHIN"
                    and edge.get("src", "").startswith("townland:")
                    and edge.get("dst") == parish_id
                    and edge.get("src") != seed
                )
                if siblings:
                    lines.append(f"- Townlands in the same parish: {', '.join(siblings[:30])}.")
            lines.append("")
            break

    if edges:
        lines.append("### Subgraph triples")
        seen: set[str] = set()
        for e in edges:
            rp = e.get("rel_props") or {}
            rel_suffix = ""
            if rp:
                parts = [f"{k}={v}" for k, v in rp.items() if v is not None][:3]
                if parts:
                    rel_suffix = " [" + ", ".join(parts) + "]"
            triple = (
                f"({e['src_label']}:{e['src_name']})"
                f"-[{e['rel_type']}{rel_suffix}]->"
                f"({e['dst_label']}:{e['dst_name']})"
            )
            if triple not in seen:
                seen.add(triple)
                lines.append(triple)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main retrieval entry point
# ---------------------------------------------------------------------------

def retrieve_subgraph(
    question: str,
    *,
    intent: str = "relational",
    entity_hints: dict[str, Any] | None = None,
    k_hops: int | None = None,
) -> GraphRAGResult:
    """
    Full hybrid retrieval:
      1. Vector seed over node passport embeddings.
      2. k-hop BFS traversal from seed nodes.
      3. Prune to _MAX_TRIPLES and linearise.
      4. Attach community summaries.

    Returns GraphRAGResult. On any failure returns a degraded result;
    never raises.
    """
    try:
        if not is_available():
            return GraphRAGResult(
                available=False,
                degradation_note="In-process graph not available — run scripts/build_graph.py.",
            )

        from config import ActiveConfig
        if k_hops is None:
            k_hops = int(getattr(ActiveConfig, "GRAPHRAG_K_HOPS", 2))
        max_nodes = int(getattr(ActiveConfig, "GRAPHRAG_MAX_NODES", 120))
        top_k = int(getattr(ActiveConfig, "GRAPHRAG_VECTOR_TOP_K", 8))

        entity_hints = entity_hints or {}

        with _graph_lock:
            G = _GRAPH

        seed_ids, seed_modes = _seed_from_entity_hints(G, entity_hints, top_k)
        if not seed_ids:
            seed_ids = vector_seed(question, top_k=top_k)
            seed_modes = ["vector_seed"] if seed_ids else []

        if not seed_ids:
            return GraphRAGResult(
                available=True,
                degradation_note="No seed nodes resolved for this question.",
            )

        edges, visited = _ego_edges(G, seed_ids, k_hops)
        edges, edge_pruned = _prune_edges(edges, question, seed_ids, max_triples=_MAX_TRIPLES)

        pruned = edge_pruned or len(visited) > max_nodes
        if len(visited) > max_nodes:
            visited = _prune_nodes_from_edges(G, visited, edges, seed_ids, max_nodes)
            edges = [e for e in edges if e["src"] in visited and e["dst"] in visited]

        community_summaries = _community_summaries_for(G, seed_ids)
        linearized = _linearise(question, edges, community_summaries, seed_ids)

        return GraphRAGResult(
            linearized=linearized,
            subgraph_nodes=[
                {"node_id": nid, **G.nodes[nid]}
                for nid in list(visited)[:max_nodes]
                if G.has_node(nid)
            ],
            subgraph_rels=edges,
            community_summaries=community_summaries,
            seed_nodes=[
                {"node_id": nid, **G.nodes[nid]}
                for nid in seed_ids if G.has_node(nid)
            ],
            path_used=(
                f"{'+'.join(seed_modes) if seed_modes else 'no_seed'}({len(seed_ids)}) "
                f"→ {k_hops}-hop BFS "
                f"→ {len(edges)} triples"
            ),
            k_hops=k_hops,
            pruned=pruned,
            sources_used=["in_process_graph"],
            available=True,
        )

    except Exception as exc:
        log.warning("graphrag.retrieve_subgraph_failed error=%s", exc)
        return GraphRAGResult(
            available=False,
            degradation_note=f"GraphRAG retrieval failed: {exc}",
        )


def comparison_subgraph(template_id: str) -> GraphRAGResult:
    """Graph-side corroboration for relational templates (COMPARATIVE intent)."""
    return retrieve_subgraph(template_id, intent="comparative")
