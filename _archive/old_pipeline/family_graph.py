from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class GraphNode:
    id: str
    kind: str
    label: str
    year: Optional[int] = None


@dataclass
class GraphEdge:
    source: str
    target: str
    relation: str


def build_family_graph(identity_key: str, links: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Build a small graph for one family (identity cluster).
    Goal:
      - show that emigrations connect to evictions/tenancies when they share same identity_key
      - keep it deterministic and easy to render in browser

    Graph shape:
      - center node: identity_key
      - one node per record link
      - edges: identity -> each record node
      - extra edges: tenancy -> eviction -> emigration (when years allow)
    """
    center_id = f"fam:{identity_key}"

    nodes: List[GraphNode] = [GraphNode(id=center_id, kind="family", label=identity_key, year=None)]
    edges: List[GraphEdge] = []

    # create nodes for each linked record
    rec_nodes: List[GraphNode] = []
    for i, l in enumerate(links):
        kind = str(l.get("kind") or "")
        rid = l.get("record_id")
        rid_str = str(rid) if rid is not None and str(rid) != "nan" else f"idx{i}"

        node_id = f"{kind}:{rid_str}"
        nm = (str(l.get("forename") or "") + " " + str(l.get("surname") or "")).strip()
        yr = l.get("year")
        label = f"{kind} · {nm}" if nm else f"{kind}"
        rec_nodes.append(GraphNode(id=node_id, kind=kind, label=label, year=yr))

        edges.append(GraphEdge(source=center_id, target=node_id, relation="linked"))

    nodes.extend(rec_nodes)

    # extra “timeline-ish” edges between record kinds
    # order by year when available
    def y(n: GraphNode) -> int:
        return int(n.year) if isinstance(n.year, int) else 999999

    ten = sorted([n for n in rec_nodes if n.kind == "tenancies"], key=y)
    ev  = sorted([n for n in rec_nodes if n.kind == "evictions"], key=y)
    em  = sorted([n for n in rec_nodes if n.kind == "emigrations"], key=y)

    # connect earliest tenancy -> earliest eviction -> earliest emigration where present
    if ten and ev:
        edges.append(GraphEdge(source=ten[0].id, target=ev[0].id, relation="possible_transition"))
    if ev and em:
        edges.append(GraphEdge(source=ev[0].id, target=em[0].id, relation="possible_transition"))
    if ten and em and not ev:
        edges.append(GraphEdge(source=ten[0].id, target=em[0].id, relation="possible_transition"))

    return {
        "nodes": [n.__dict__ for n in nodes],
        "edges": [e.__dict__ for e in edges],
    }
