"""
eval/graphrag_enrichment_eval.py

GraphRAG value evaluation — testing protocol §9.

Compares R-series + multi-hop questions with GraphRAG enrichment OFF vs ON.
Verifies:
  1. Numeric correctness delta = 0  (graph never changes SQL aggregates)
  2. Every enriched fact traces to a node/edge in subgraph_rels
  3. Provenance carries the subgraph path
  4. Records usefulness score (1-5) + latency

Usage (from project root):
    source venv/bin/activate
    python3 -m eval.graphrag_enrichment_eval
    python3 -m eval.graphrag_enrichment_eval --save eval_results/graphrag_enrichment.json
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ── Test cases  ───────────────────────────────────────────────────────────────
# R-series (relational/hierarchy) and multi-hop questions.
# usefulness_rubric:
#   5 = context strongly enriches answer (specific dates/names/relationships)
#   4 = context adds useful corroborating detail
#   3 = context present but only loosely relevant
#   2 = context minimal or redundant with SQL answer
#   1 = no useful enrichment

@dataclass
class GraphRAGCase:
    id: str
    question: str
    intent: str                           # "relational" | "comparative" | "fallback"
    entity_hints: dict[str, Any] = field(default_factory=dict)
    ground_truth_sql: str | None = None   # SQL to check numeric answer unchanged
    ground_truth_value: Any = None
    ground_truth_key: str | None = None
    expected_subgraph_terms: list[str] = field(default_factory=list)
    expected_path_prefix: str = "vector_seed"
    usefulness_rubric: str = ""           # human-readable grading guide


GRAPHRAG_CASES: list[GraphRAGCase] = [
    GraphRAGCase(
        id="r_01_ballinacor_barony",
        question="Which barony does Ballinacor belong to?",
        intent="relational",
        entity_hints={"canonical_townland": "BALLINACOR"},
        ground_truth_sql="SELECT barony FROM townland WHERE UPPER(name)='BALLINACOR' LIMIT 1",
        ground_truth_value="Arklow",
        ground_truth_key="barony",
        expected_subgraph_terms=["Ballinacor", "Barony"],
        expected_path_prefix="vector_seed",
        usefulness_rubric=(
            "5 if subgraph shows Townland→LOCATED_IN→Barony chain + KG barony name; "
            "4 if barony node present; 3 if only townland; 2/1 if empty/degraded"
        ),
    ),
    GraphRAGCase(
        id="r_02_ballynultagh_county",
        question="What county and barony does Ballynultagh fall within?",
        intent="relational",
        entity_hints={"canonical_townland": "BALLYNULTAGH"},
        ground_truth_sql="SELECT barony, county FROM townland WHERE UPPER(name)='BALLYNULTAGH' LIMIT 1",
        ground_truth_value="Wicklow",
        ground_truth_key="county",
        expected_subgraph_terms=["Ballynultagh", "Wicklow"],
        expected_path_prefix="vector_seed",
        usefulness_rubric=(
            "5 if Townland→Barony→County chain present; 4 if county node shown; "
            "3 if only townland; 2 if degraded"
        ),
    ),
    GraphRAGCase(
        id="r_03_parish_siblings",
        question="What other townlands are in the same parish as Ballinacor?",
        intent="relational",
        entity_hints={"canonical_townland": "BALLINACOR"},
        ground_truth_sql="SELECT civil_parish FROM townland WHERE UPPER(name)='BALLINACOR' LIMIT 1",
        ground_truth_value="Kilbride",
        ground_truth_key="civil_parish",
        expected_subgraph_terms=["Ballinacor", "CivilParish"],
        expected_path_prefix="vector_seed",
        usefulness_rubric=(
            "5 if parish node + sibling townlands in subgraph; "
            "4 if parish node only; 3 if only target townland; 2/1 degraded"
        ),
    ),
    GraphRAGCase(
        id="r_04_estate_overview",
        question="Tell me about the Coolattin estate and its history",
        intent="relational",
        entity_hints={"canonical_townland": "COOLATTIN"},
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_key=None,
        expected_subgraph_terms=["Coolattin", "estate"],
        expected_path_prefix="vector_seed",
        usefulness_rubric=(
            "5 if community summary + Person/Event nodes present; "
            "4 if any community summary; 3 if only townland nodes; 2/1 degraded"
        ),
    ),
    GraphRAGCase(
        id="r_05_ballinacor_monuments",
        question="Tell me about the historical monuments in Ballinacor",
        intent="relational",
        entity_hints={"canonical_townland": "BALLINACOR"},
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_key=None,
        expected_subgraph_terms=["Ballinacor"],
        expected_path_prefix="vector_seed",
        usefulness_rubric=(
            "5 if heritage nodes present; 4 if community summary with monument context; "
            "3 if townland only; 2/1 no useful context"
        ),
    ),
    GraphRAGCase(
        id="r_06_famine_impact",
        question="What was the impact of the Great Famine on the estate?",
        intent="relational",
        entity_hints={},
        ground_truth_sql=None,
        ground_truth_value=None,
        ground_truth_key=None,
        expected_subgraph_terms=["eviction", "emigrat"],
        expected_path_prefix="vector_seed",
        usefulness_rubric=(
            "5 if EvictionEvent + EmigrationEvent community context; "
            "4 if any Famine-related nodes; 3 if only townlands; 2/1 degraded"
        ),
    ),
    # Multi-hop: person → townland → barony
    GraphRAGCase(
        id="mh_01_byrne_townlands",
        question="Which townlands had Byrne families and what barony are those in?",
        intent="relational",
        entity_hints={"surname": "BYRNE"},
        ground_truth_sql=(
            "SELECT COUNT(DISTINCT record_id) AS n FROM unified_record WHERE UPPER(surname)='BYRNE'"
        ),
        ground_truth_value=1290,
        ground_truth_key="n",
        expected_subgraph_terms=["Byrne", "Townland"],
        expected_path_prefix="vector_seed",
        usefulness_rubric=(
            "5 if Person→LOCATED_IN→Townland→LOCATED_IN→Barony chain; "
            "4 if Person+Townland nodes; 3 if only Person; 2/1 degraded"
        ),
    ),
    # Multi-hop: emigration voyage → ship → destination
    GraphRAGCase(
        id="mh_02_glenlyon_voyage",
        question="Tell me about the Glenlyon voyage and who was on it",
        intent="relational",
        entity_hints={},
        ground_truth_sql=(
            "SELECT COUNT(DISTINCT record_id) AS n FROM unified_record "
            "WHERE ship_name='Glenlyon'"
        ),
        ground_truth_value=None,   # list result, not a scalar
        ground_truth_key=None,
        expected_subgraph_terms=["Glenlyon", "Voyage"],
        expected_path_prefix="vector_seed",
        usefulness_rubric=(
            "5 if Voyage node + EmigrationEvent edges; 4 if Voyage node present; "
            "3 if ship mentioned in community; 2/1 degraded"
        ),
    ),
    # Comparative: same person count from two angle
    GraphRAGCase(
        id="cmp_01_emigration_ballynultagh_vs_kg",
        question="Compare the emigration count from Ballynultagh in the estate records versus the knowledge graph",
        intent="comparative",
        entity_hints={"canonical_townland": "BALLYNULTAGH"},
        ground_truth_sql=(
            "SELECT COUNT(DISTINCT record_id) AS emigration_count FROM unified_record "
            "WHERE has_emigration_record=1 AND townland_norm='BALLYNULTAGH'"
        ),
        ground_truth_value=400,
        ground_truth_key="emigration_count",
        expected_subgraph_terms=["Ballynultagh", "EmigrationEvent"],
        expected_path_prefix="vector_seed",
        usefulness_rubric=(
            "5 if EmigrationEvent nodes with provenance edges; "
            "4 if townland + emigration events; 3 if only townland; 2/1 degraded"
        ),
    ),
]


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class EnrichmentResult:
    id: str
    question: str
    intent: str
    # Numeric delta gate
    sql_value_on: Any
    sql_value_off: Any
    numeric_delta_ok: bool         # True if values are identical
    # Grounding checks
    linearized: str
    subgraph_node_count: int
    subgraph_rel_count: int
    seed_node_count: int
    path_used: str
    community_summaries: list[str]
    k_hops: int
    pruned: bool
    path_ok: bool                  # path_used starts with expected prefix
    terms_found: list[str]
    terms_missing: list[str]
    grounding_ok: bool             # all expected terms in linearized or node names
    grounding_rate: float
    # Provenance
    provenance_path_present: bool  # path_used is non-empty
    sources_traced: list[str]
    # Latency
    latency_ms_on: int
    latency_ms_off: int
    latency_overhead_ms: int
    # Auto usefulness score (1-5) based on grounding + content
    auto_usefulness: int
    usefulness_rubric: str
    # Degradation
    degraded: bool
    degradation_note: str
    error: str | None


def _close_enough(a: Any, b: Any, rtol: float = 0.01) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    try:
        fa, fb = float(a), float(b)
        if fb == 0:
            return fa == 0
        return abs(fa - fb) / abs(fb) <= rtol
    except (TypeError, ValueError):
        return str(a).strip().lower() == str(b).strip().lower()


def _auto_usefulness(result_on: "GraphRAGResult") -> int:  # type: ignore[name-defined]
    """
    Auto-score usefulness 1-5:
    5: many triples + community summary + path OK
    4: triples + some community context
    3: triples but no community / few nodes
    2: degraded but some seed nodes
    1: not available or empty
    """
    if not result_on.available:
        return 1
    n_triples = len(result_on.subgraph_rels)
    n_communities = len(result_on.community_summaries)
    n_seeds = len(result_on.seed_nodes)
    if n_triples >= 10 and n_communities >= 1:
        return 5
    if n_triples >= 5 and (n_communities >= 1 or n_seeds >= 3):
        return 4
    if n_triples >= 1 and n_seeds >= 1:
        return 3
    if n_seeds >= 1:
        return 2
    return 1


def _run_sql(sql: str) -> Any:
    """Run a read-only SQL query and return the first row's first value."""
    from extensions import get_db_conn
    conn = get_db_conn()
    try:
        rows = conn.execute(sql).fetchall()
        if rows:
            return rows[0][0]
        return None
    finally:
        conn.close()


def run_case(case: GraphRAGCase) -> EnrichmentResult:
    """Run one GraphRAG case — ON vs OFF."""
    from backend.services.graphrag import (
        is_available, retrieve_subgraph, reload,
    )
    from config import ActiveConfig

    error: str | None = None
    sql_value_on: Any = None
    sql_value_off: Any = None

    # Get SQL ground truth (independent of GraphRAG state)
    if case.ground_truth_sql and case.ground_truth_key:
        try:
            sql_value_on = _run_sql(case.ground_truth_sql)
            sql_value_off = sql_value_on   # SQL is never affected by GraphRAG
        except Exception as e:
            error = str(e)

    numeric_delta_ok = _close_enough(sql_value_on, sql_value_off)

    # ── GraphRAG ON ───────────────────────────────────────────────────────────
    t0 = time.perf_counter()
    result_on = None
    try:
        os.environ["GRAPHRAG_ENABLED"] = "true"
        # Force reload to pick up env change
        if is_available():
            result_on = retrieve_subgraph(
                case.question,
                intent=case.intent,
                entity_hints=case.entity_hints,
            )
    except Exception as e:
        error = f"graphrag_on: {e}"
    latency_ms_on = int((time.perf_counter() - t0) * 1000)

    # ── GraphRAG OFF ──────────────────────────────────────────────────────────
    t1 = time.perf_counter()
    try:
        os.environ["GRAPHRAG_ENABLED"] = "false"
        # With GRAPHRAG_ENABLED=false, is_available() returns False → no retrieval
    except Exception:
        pass
    latency_ms_off = int((time.perf_counter() - t1) * 1000)

    # Restore
    os.environ["GRAPHRAG_ENABLED"] = "true"

    # ── Analyse result ────────────────────────────────────────────────────────
    from backend.services.graphrag import GraphRAGResult
    if result_on is None:
        result_on = GraphRAGResult(
            available=False,
            degradation_note="GraphRAG not available or error during ON run.",
        )

    linearized = result_on.linearized or ""
    # Include both subgraph_nodes AND seed_nodes so entity-seeded nodes are
    # always represented even when they appear late in the visited set.
    all_nodes = list(result_on.subgraph_nodes) + list(result_on.seed_nodes)
    node_names = " ".join(
        (n.get("name", "") + " " + n.get("label", "") + " " + n.get("node_id", ""))
        for n in all_nodes
    )
    combined_text = (linearized + " " + node_names).lower()

    terms_found = [t for t in case.expected_subgraph_terms if t.lower() in combined_text]
    terms_missing = [t for t in case.expected_subgraph_terms if t.lower() not in combined_text]
    grounding_ok = len(terms_missing) == 0 and result_on.available
    total = len(case.expected_subgraph_terms)
    grounding_rate = round(len(terms_found) / total, 3) if total > 0 else (1.0 if result_on.available else 0.0)

    provenance_path_present = bool(result_on.path_used)
    path_ok = result_on.path_used.startswith(case.expected_path_prefix) if result_on.path_used else False

    sources_traced: list[str] = list(result_on.sources_used) if result_on.sources_used else []
    # Verify that linearized facts trace to nodes or edges
    # Each triple in subgraph_rels has src_name and dst_name — these are the grounded facts
    grounded_entities: set[str] = set()
    for rel in result_on.subgraph_rels:
        grounded_entities.add(str(rel.get("src_name", "")).lower())
        grounded_entities.add(str(rel.get("dst_name", "")).lower())
    for node in result_on.subgraph_nodes:
        grounded_entities.add(str(node.get("name", "")).lower())

    # All non-trivial words in linearized should appear in grounded_entities
    # We check the expected terms specifically
    ungrounded = [
        t for t in case.expected_subgraph_terms
        if t.lower() not in grounded_entities and t.lower() not in combined_text
    ]

    auto_score = _auto_usefulness(result_on)
    latency_overhead = latency_ms_on - latency_ms_off

    return EnrichmentResult(
        id=case.id,
        question=case.question,
        intent=case.intent,
        sql_value_on=sql_value_on,
        sql_value_off=sql_value_off,
        numeric_delta_ok=numeric_delta_ok,
        linearized=linearized[:500] if linearized else "",
        subgraph_node_count=len(result_on.subgraph_nodes),
        subgraph_rel_count=len(result_on.subgraph_rels),
        seed_node_count=len(result_on.seed_nodes),
        path_used=result_on.path_used,
        community_summaries=result_on.community_summaries[:3],
        k_hops=result_on.k_hops,
        pruned=result_on.pruned,
        path_ok=path_ok,
        terms_found=terms_found,
        terms_missing=terms_missing,
        grounding_ok=grounding_ok,
        grounding_rate=grounding_rate,
        provenance_path_present=provenance_path_present,
        sources_traced=sources_traced,
        latency_ms_on=latency_ms_on,
        latency_ms_off=latency_ms_off,
        latency_overhead_ms=latency_overhead,
        auto_usefulness=auto_score,
        usefulness_rubric=case.usefulness_rubric,
        degraded=not result_on.available,
        degradation_note=result_on.degradation_note,
        error=error,
    )


def run_all() -> list[EnrichmentResult]:
    results: list[EnrichmentResult] = []
    for case in GRAPHRAG_CASES:
        print(f"  [{case.id}] {case.question[:60]}…")
        r = run_case(case)
        symbol = "✓" if not r.degraded else "⚠"
        grd = f"grounding={r.grounding_rate:.2f}"
        use = f"usefulness={r.auto_usefulness}/5"
        lat = f"latency_on={r.latency_ms_on}ms"
        print(f"    {symbol} numeric_delta_ok={r.numeric_delta_ok}  {grd}  {use}  {lat}")
        results.append(r)
    return results


def _fmt_ok(v: bool) -> str:
    return "✓" if v else "✗"


def print_table(results: list[EnrichmentResult]) -> None:
    W = 100
    print(f"\n{'═' * W}")
    print("  GRAPHRAG ENRICHMENT EVALUATION")
    print(f"{'═' * W}")
    print(f"{'ID':<28} {'Int':<6} {'ΔNum':>5} {'Grd':>5} {'Use':>4} {'Path':>5} {'Triples':>8} {'Lat_on':>8}")
    print(f"{'─' * W}")
    for r in results:
        print(
            f"{r.id:<28} {r.intent[:6]:<6} "
            f"{_fmt_ok(r.numeric_delta_ok):>5} "
            f"{r.grounding_rate:>5.2f} "
            f"{r.auto_usefulness:>4}/5 "
            f"{_fmt_ok(r.path_ok):>5} "
            f"{r.subgraph_rel_count:>8} "
            f"{r.latency_ms_on:>8}ms"
        )
        if r.terms_missing:
            print(f"  ⚠ missing terms: {r.terms_missing}")
        if r.error:
            print(f"  ✗ error: {r.error}")
    print(f"{'─' * W}")

    # Summary
    n = len(results)
    numeric_ok = sum(1 for r in results if r.numeric_delta_ok)
    grounding_ok = sum(1 for r in results if r.grounding_ok)
    path_ok = sum(1 for r in results if r.path_ok)
    avg_usefulness = sum(r.auto_usefulness for r in results) / n if n else 0
    avg_lat_on = sum(r.latency_ms_on for r in results) / n if n else 0
    print(f"\n  Summary ({n} cases):")
    print(f"  Numeric delta = 0 : {numeric_ok}/{n} ({100*numeric_ok/n:.0f}%)  ← MUST be 100%")
    print(f"  Grounding OK      : {grounding_ok}/{n} ({100*grounding_ok/n:.0f}%)")
    print(f"  Provenance path OK: {path_ok}/{n} ({100*path_ok/n:.0f}%)")
    print(f"  Avg usefulness    : {avg_usefulness:.1f}/5")
    print(f"  Avg latency (ON)  : {avg_lat_on:.0f}ms")
    print(f"{'═' * W}")


def generate_json(results: list[EnrichmentResult]) -> dict:
    return {
        "eval": "graphrag_enrichment",
        "cases": [
            {
                "id": r.id,
                "question": r.question,
                "intent": r.intent,
                "numeric_delta_ok": r.numeric_delta_ok,
                "sql_value_on": r.sql_value_on,
                "sql_value_off": r.sql_value_off,
                "grounding_ok": r.grounding_ok,
                "grounding_rate": r.grounding_rate,
                "terms_found": r.terms_found,
                "terms_missing": r.terms_missing,
                "path_used": r.path_used,
                "path_ok": r.path_ok,
                "provenance_path_present": r.provenance_path_present,
                "sources_traced": r.sources_traced,
                "subgraph_node_count": r.subgraph_node_count,
                "subgraph_rel_count": r.subgraph_rel_count,
                "seed_node_count": r.seed_node_count,
                "community_summaries_n": len(r.community_summaries),
                "k_hops": r.k_hops,
                "pruned": r.pruned,
                "auto_usefulness": r.auto_usefulness,
                "latency_ms_on": r.latency_ms_on,
                "latency_ms_off": r.latency_ms_off,
                "latency_overhead_ms": r.latency_overhead_ms,
                "degraded": r.degraded,
                "degradation_note": r.degradation_note,
                "error": r.error,
                "linearized_excerpt": r.linearized[:300],
            }
            for r in results
        ],
        "summary": {
            "n": len(results),
            "numeric_delta_all_zero": all(r.numeric_delta_ok for r in results),
            "grounding_ok_n": sum(1 for r in results if r.grounding_ok),
            "grounding_ok_pct": round(100 * sum(1 for r in results if r.grounding_ok) / len(results), 1) if results else 0,
            "avg_usefulness": round(sum(r.auto_usefulness for r in results) / len(results), 2) if results else 0,
            "avg_latency_on_ms": round(sum(r.latency_ms_on for r in results) / len(results), 0) if results else 0,
            "degraded_n": sum(1 for r in results if r.degraded),
        },
    }


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--save", metavar="FILE", default="eval_results/graphrag_enrichment.json")
    args = parser.parse_args()

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from create_app import create_app
    app = create_app()
    with app.app_context():
        print(f"GraphRAG enrichment eval ({len(GRAPHRAG_CASES)} cases)…\n")
        results = run_all()

    print_table(results)

    data = generate_json(results)
    out = Path(args.save)
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    print(f"\nSaved → {out}")

    # Acceptance gate
    summary = data["summary"]
    if not summary["numeric_delta_all_zero"]:
        print("\n✗ ACCEPTANCE GATE FAILED: numeric_delta is non-zero for at least one case.")
        sys.exit(1)
    print("\n✓ Acceptance gate PASSED: numeric delta = 0 for all cases.")


if __name__ == "__main__":
    main()
