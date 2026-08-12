from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[2]
_SUMMARIES_PATH = _ROOT / "data" / "seed" / "community_summaries.json"


_RELATIONAL_KEYWORDS = frozenset({
    "related to", "connected to", "connection between", "link between",
    "in the same parish", "same parish", "same barony", "same county",
    "part of", "belong to", "belongs to",
    "neighbouring", "neighboring", "adjacent to", "next to", "bordering",
    "how are", "relationship between", "linked to",
})

_HIERARCHY_KEYWORDS = frozenset({
    "which parish", "what parish", "civil parish", "in the parish",
    "in the barony", "which barony", "what barony", "barony of",
    "in the county", "which county", "what county",
    "townlands in", "in which", "where is", "where does",
    "located in", "situated in", "falls within",
})

_HERITAGE_KEYWORDS = frozenset({
    "heritage", "archaeological", "monument", "ring fort", "holy well",
    "history of", "tell me about", "describe", "what is the history",
    "historically", "historic", "fortification", "earthwork",
})

_SENSEMAKING_KEYWORDS = frozenset({
    "overview", "about the estate", "about coolattin", "what was",
    "describe the estate", "coolattin estate", "what kind of",
    "background", "summary of", "general context",
})


@dataclass
class SubgraphResult:
    seed_entities: list[dict] = field(default_factory=list)
    hierarchy: dict = field(default_factory=dict)
    siblings: list[str] = field(default_factory=list)
    external_links: dict = field(default_factory=dict)
    triples_vrti: list[tuple[str, str, str]] = field(default_factory=list)
    triples_graphdb: list[tuple[str, str, str]] = field(default_factory=list)
    community_summary: str | None = None
    linearized: str = ""
    sources_used: list[str] = field(default_factory=list)
    question_type: str = "relational"
    pruned: bool = False
    k_hops: int = 2


def is_subgraph_question(
    question: str,
    analysis: dict[str, Any],
    slot_fill: Any | None,
) -> bool:
    q = (question or "").lower()

    if slot_fill is not None and getattr(slot_fill, "confidence", 0.0) >= 0.80:
        return False

    if (
        analysis.get("output_mode") == "count"
        and not any(
            kw in q
            for kw in _RELATIONAL_KEYWORDS | _HIERARCHY_KEYWORDS | _HERITAGE_KEYWORDS
        )
    ):
        return False

    if analysis.get("primary_intent") == "geography":
        return True

    for kw_set in (_RELATIONAL_KEYWORDS, _HIERARCHY_KEYWORDS, _HERITAGE_KEYWORDS, _SENSEMAKING_KEYWORDS):
        if any(kw in q for kw in kw_set):
            return True

    return False


def _classify(question: str, analysis: dict) -> str:
    q = question.lower()
    if analysis.get("primary_intent") == "geography" or any(kw in q for kw in _HIERARCHY_KEYWORDS):
        return "hierarchy"
    if any(kw in q for kw in _RELATIONAL_KEYWORDS):
        return "relational"
    if any(kw in q for kw in _HERITAGE_KEYWORDS):
        return "heritage"
    if any(kw in q for kw in _SENSEMAKING_KEYWORDS):
        return "sensemaking"
    return "relational"


def _seed_entities(
    question: str,
    analysis: dict[str, Any],
    townland_resolution: dict[str, Any],
) -> list[dict]:
    seeds: list[dict] = []

    label = townland_resolution.get("name_norm") or townland_resolution.get("name")
    if label and townland_resolution.get("matched"):
        seeds.append({
            "label": label,
            "kg_uri": townland_resolution.get("kg_uri"),
            "sql_id": townland_resolution.get("sql_id"),
            "entity_type": "townland",
            "confidence": float(townland_resolution.get("confidence") or 1.0),
        })

    try:
        from backend.services.entity_resolver import _get_index
        q_stripped = re.sub(r"\b" + re.escape(label or "") + r"\b", "", question, flags=re.IGNORECASE).strip()
        idx = _get_index()
        if idx and q_stripped:
            hits = idx.search(q_stripped, entity_type="townland", k=3, min_score=0.65)
            for h in hits:
                h_label = h.get("label", "")
                if h_label and h_label.upper() != (label or "").upper():
                    seeds.append({
                        "label": h_label,
                        "kg_uri": h.get("kg_uri"),
                        "sql_id": h.get("sql_id"),
                        "entity_type": "townland",
                        "confidence": float(h.get("score", 0)),
                    })
    except Exception as exc:
        log.debug("subgraph_engine.secondary_seed_failed error=%s", exc)

    q_lower = (question or "").lower()
    if not seeds and any(kw in q_lower for kw in _SENSEMAKING_KEYWORDS):
        seeds.append({
            "label": "Coolattin Estate",
            "kg_uri": None,
            "sql_id": None,
            "entity_type": "estate",
            "confidence": 0.7,
        })

    return seeds[:3]


def _expand_vrti(seeds: list[dict]) -> dict[str, Any]:
    from backend.integrations import vrti_sparql

    out: dict[str, Any] = {
        "hierarchy": {},
        "siblings": [],
        "external_links": {},
        "triples": [],
    }

    for seed in seeds:
        uri = seed.get("kg_uri")
        if not uri:
            continue
        label = seed.get("label", "?")

        try:
            hier = vrti_sparql.get_place_hierarchy(uri)
            if hier:
                out["hierarchy"].update(
                    {k: v for k, v in hier.items() if v and k != "townland_name"}
                )
                if hier.get("parish"):
                    out["triples"].append((label, "falls within parish", hier["parish"]))
                if hier.get("barony"):
                    out["triples"].append(
                        (hier.get("parish") or label, "falls within barony", hier["barony"])
                    )
                if hier.get("county"):
                    out["triples"].append(
                        (hier.get("barony") or hier.get("parish") or label,
                         "falls within county", hier["county"])
                    )
        except Exception as exc:
            log.debug("subgraph_engine.vrti_hierarchy uri=%s error=%s", uri, exc)

        try:
            sibs = vrti_sparql.get_sibling_townlands(uri, limit=20)
            out["siblings"].extend(s["name"] for s in sibs if s.get("name"))
        except Exception as exc:
            log.debug("subgraph_engine.vrti_siblings uri=%s error=%s", uri, exc)

        try:
            links = vrti_sparql.get_external_links(uri)
            out["external_links"].update({k: v for k, v in links.items() if v})
        except Exception as exc:
            log.debug("subgraph_engine.vrti_links uri=%s error=%s", uri, exc)

    seen: set[str] = set()
    unique: list[str] = []
    for s in out["siblings"]:
        if s not in seen:
            seen.add(s)
            unique.append(s)
    out["siblings"] = sorted(unique)[:20]

    return out


def _expand_graphdb(seeds: list[dict]) -> list[tuple[str, str, str]]:
    triples: list[tuple[str, str, str]] = []
    try:
        from backend.integrations import graphdb_sparql as _gdb
        if not _gdb.probe():
            return []
        for seed in seeds:
            name = seed.get("label", "")
            if not name:
                continue
            triples.extend(_gdb.get_entity_neighborhood(name, k=2, max_nodes=40))
    except Exception as exc:
        log.debug("subgraph_engine.graphdb_expand_failed error=%s", exc)
    return triples[:80]


def _prune(
    triples: list[tuple[str, str, str]],
    max_triples: int = 40,
) -> tuple[list[tuple[str, str, str]], bool]:
    if len(triples) <= max_triples:
        return triples, False
    ranked = sorted(triples, key=lambda t: len(t[1]))
    return ranked[:max_triples], True


def _linearize(
    seeds: list[dict],
    vrti_data: dict[str, Any],
    gdb_triples: list[tuple[str, str, str]],
    community_summary: str | None,
) -> str:
    lines: list[str] = []

    seed_labels = [s.get("label") for s in seeds if s.get("label")]
    if seed_labels:
        lines.append(f"[Subgraph for: {', '.join(seed_labels)}]")

    hier = vrti_data.get("hierarchy") or {}
    if hier:
        lines.append("")
        lines.append("Administrative Hierarchy (VRTI Knowledge Graph):")
        primary = seed_labels[0] if seed_labels else "entity"
        parts: list[str] = [primary]
        if hier.get("parish"):
            parts.append(f"→ civil parish: {hier['parish']}")
        if hier.get("barony"):
            parts.append(f"→ barony: {hier['barony']}")
        if hier.get("county"):
            parts.append(f"→ county: {hier['county']}")
        lines.append("  " + " ".join(parts))

    vrti_triples = vrti_data.get("triples") or []
    hier_triples = {(t[0], t[1], t[2]) for t in vrti_triples if "falls within" in t[1]}
    extra_triples = [t for t in vrti_triples if t not in hier_triples]
    if extra_triples:
        lines.append("")
        lines.append("Additional VRTI Relationships:")
        for s, p, o in extra_triples[:10]:
            lines.append(f"  {s} — {p} — {o}")

    siblings = vrti_data.get("siblings") or []
    if siblings:
        lines.append("")
        displayed = siblings[:12]
        extra = len(siblings) - len(displayed)
        sib_str = ", ".join(displayed)
        if extra > 0:
            sib_str += f" [+{extra} more]"
        lines.append(f"Townlands in the same civil parish: {sib_str}")

    ext = vrti_data.get("external_links") or {}
    id_parts: list[str] = []
    if ext.get("osm_id"):
        id_parts.append(f"OSM: {ext['osm_id']}")
    if ext.get("osi_id"):
        id_parts.append(f"OSI: {ext['osi_id']}")
    if ext.get("vrti_id"):
        id_parts.append(f"VRTI: {ext['vrti_id']}")
    for lnk in (ext.get("links") or [])[:2]:
        id_parts.append(f"Link: {lnk}")
    if id_parts:
        lines.append("")
        lines.append("External Identifiers: " + " | ".join(id_parts))

    if gdb_triples:
        lines.append("")
        lines.append("GraphDB (co: ontology) Relationships:")
        for s, p, o in gdb_triples[:15]:
            lines.append(f"  {s} — {p} — {o}")

    if community_summary:
        lines.append("")
        lines.append("Context Summary:")
        lines.append(community_summary)

    text = "\n".join(lines).strip()
    words = text.split()
    if len(words) > 800:
        text = " ".join(words[:800]) + "\n[truncated]"
    return text


_SUMMARIES_CACHE: dict[str, str] | None = None


def _load_summaries() -> dict[str, str]:
    global _SUMMARIES_CACHE
    if _SUMMARIES_CACHE is not None:
        return _SUMMARIES_CACHE
    try:
        if _SUMMARIES_PATH.exists():
            _SUMMARIES_CACHE = json.loads(_SUMMARIES_PATH.read_text(encoding="utf-8"))
        else:
            _SUMMARIES_CACHE = {}
    except Exception as exc:
        log.warning("subgraph_engine.summaries_load_failed error=%s", exc)
        _SUMMARIES_CACHE = {}
    return _SUMMARIES_CACHE


def _get_community_summary(question: str) -> str | None:
    q = (question or "").lower()
    summaries = _load_summaries()
    if not summaries:
        return None
    for key, text in summaries.items():
        if key in q:
            return str(text)
    if any(kw in q for kw in _SENSEMAKING_KEYWORDS):
        return summaries.get("coolattin") or summaries.get("estate")
    return None


def retrieve_subgraph(
    question: str,
    analysis: dict[str, Any],
    townland_resolution: dict[str, Any],
    sources: tuple[str, ...] = ("vrti", "graphdb"),
) -> SubgraphResult:
    result = SubgraphResult()
    try:
        result.question_type = _classify(question, analysis)

        seeds = _seed_entities(question, analysis, townland_resolution)
        result.seed_entities = seeds

        has_kg_seed = any(s.get("kg_uri") for s in seeds)

        vrti_data: dict[str, Any] = {
            "hierarchy": {}, "siblings": [], "external_links": {}, "triples": [],
        }
        if "vrti" in sources and has_kg_seed:
            try:
                vrti_data = _expand_vrti(seeds)
                if any(vrti_data.get(k) for k in ("hierarchy", "siblings", "triples")):
                    result.sources_used.append("vrti")
            except Exception as exc:
                log.warning("subgraph_engine.vrti_expand_failed error=%s", exc)

        gdb_triples: list[tuple[str, str, str]] = []
        if "graphdb" in sources and seeds:
            try:
                gdb_triples = _expand_graphdb(seeds)
                if gdb_triples:
                    result.sources_used.append("graphdb")
            except Exception as exc:
                log.warning("subgraph_engine.graphdb_expand_failed error=%s", exc)

        all_triples = list(vrti_data.get("triples", [])) + gdb_triples
        pruned_all, was_pruned = _prune(all_triples, max_triples=40)
        vrti_set = set(map(tuple, vrti_data.get("triples", [])))
        gdb_set  = set(map(tuple, gdb_triples))
        result.triples_vrti    = [t for t in pruned_all if tuple(t) in vrti_set]
        result.triples_graphdb = [t for t in pruned_all if tuple(t) in gdb_set]
        result.pruned = was_pruned

        result.hierarchy       = vrti_data.get("hierarchy") or {}
        result.siblings        = vrti_data.get("siblings") or []
        result.external_links  = vrti_data.get("external_links") or {}
        result.community_summary = _get_community_summary(question)
        result.k_hops = 2

        result.linearized = _linearize(
            seeds=seeds,
            vrti_data={**vrti_data, "triples": result.triples_vrti},
            gdb_triples=result.triples_graphdb,
            community_summary=result.community_summary,
        )

    except Exception as exc:
        log.error("subgraph_engine.retrieve_subgraph_failed error=%s", exc)

    return result
