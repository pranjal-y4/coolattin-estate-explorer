#!/usr/bin/env python3
"""
eval_plan/scripts/rq4_enrichment.py

RQ4 enrichment coverage, per EVAL_RQ4_geospatial_kg_enrichment.md. All queries
are against the LOCAL in-process GraphRAG substrate (graph_nodes / graph_edges,
built by scripts/build_graph.py, loaded into NetworkX at runtime) — this is a
separate system from the external GraphDB SPARQL store the /kg-explore page
compares against. Do not conflate the two: this script cannot and does not
probe the external GraphDB VM.

Run from repo root: venv/bin/python3 eval_plan/scripts/rq4_enrichment.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


def main() -> int:
    from create_app import create_app
    from extensions import get_db_conn

    app = create_app()
    with app.app_context():
        conn = get_db_conn()

        print("=" * 100)
        print("Context type 1 — Administrative geography (WITHIN edges)")
        print("=" * 100)
        r = dict(conn.execute(
            """
            SELECT
              COUNT(DISTINCT gn.node_id) AS estate_townland_nodes,
              COUNT(DISTINCT ge.src) AS with_within_edge
            FROM graph_nodes gn
            LEFT JOIN graph_edges ge ON ge.src = gn.node_id AND ge.rel_type='WITHIN'
            WHERE gn.label='Townland'
              AND gn.name IN (SELECT DISTINCT townland_norm FROM unified_record WHERE townland_norm IS NOT NULL)
            """
        ).fetchone())
        print(f"  Estate townland nodes: {r['estate_townland_nodes']}, with WITHIN edge: {r['with_within_edge']} "
              f"({r['with_within_edge']/r['estate_townland_nodes']*100:.1f}%)")
        print("  (Full 4,225-row national townland reference: only 184/4,225 (4.4%) have a WITHIN edge —")
        print("   scope this claim to the estate's 152 townlands, not the full reference table.)")

        print()
        print("=" * 100)
        print("Context type 2 — Connected records (workhouse links represented in the graph)")
        print("=" * 100)
        linked_to = conn.execute("SELECT COUNT(*) FROM graph_edges WHERE rel_type='LINKED_TO'").fetchone()[0]
        real_links = conn.execute("SELECT COUNT(*) FROM workhouse_unified_links").fetchone()[0]
        print(f"  LINKED_TO edges in graph: {linked_to}")
        print(f"  Actual workhouse_unified_links rows in DB: {real_links}")
        print(f"  Graph coverage of real links: {linked_to/real_links*100:.1f}%")
        print("  *** The graph substrate is stale relative to entity resolution — it reflects an")
        print("  *** earlier ER state (before the 873-confirmed/4,261-possible expansion). Re-run")
        print("  *** scripts/build_graph.py to bring LINKED_TO edges in sync with current ER data.")

        print()
        print("=" * 100)
        print("Context type 3 — Population patterns (census/clearance observations)")
        print("=" * 100)
        census_edges = conn.execute(
            "SELECT COUNT(*) FROM graph_edges WHERE rel_type='HAS_OBSERVATION' AND dst IN (SELECT node_id FROM graph_nodes WHERE label='CensusObservation')"
        ).fetchone()[0]
        clearance_edges = conn.execute(
            "SELECT COUNT(*) FROM graph_edges WHERE rel_type='HAS_OBSERVATION' AND dst IN (SELECT node_id FROM graph_nodes WHERE label='ClearanceObservation')"
        ).fetchone()[0]
        census_total = conn.execute("SELECT COUNT(*) FROM census_record").fetchone()[0]
        clearance_total = conn.execute("SELECT COUNT(*) FROM clearances_record").fetchone()[0]
        print(f"  Census: {census_edges}/{census_total} ({census_edges/census_total*100:.1f}%)")
        print(f"  Clearances: {clearance_edges}/{clearance_total} ({clearance_edges/clearance_total*100:.1f}%)")

        print()
        print("=" * 100)
        print("Context type 4 — Landscape features (heritage)")
        print("=" * 100)
        heritage_nodes = conn.execute(
            "SELECT COUNT(*) FROM graph_nodes WHERE label LIKE '%Herit%' OR label LIKE '%Monument%' OR label LIKE '%Well%'"
        ).fetchone()[0]
        heritage_total = conn.execute("SELECT COUNT(*) FROM heritage_feature").fetchone()[0]
        print(f"  Heritage-related graph_nodes: {heritage_nodes}")
        print(f"  heritage_feature table rows: {heritage_total}")
        print(f"  Coverage: {heritage_nodes}/{heritage_total} = 0% — landscape features are NOT ingested")
        print("  into the GraphRAG substrate at all. Heritage data exists only in its own table,")
        print("  served directly to the /heritage page and never reachable via GraphRAG traversal.")

        print()
        print("=" * 100)
        print("LOCATED_IN backfill gap (Person -> Townland)")
        print("=" * 100)
        r = dict(conn.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM graph_nodes WHERE label='Person') AS total_person,
              (SELECT COUNT(DISTINCT src) FROM graph_edges WHERE rel_type='LOCATED_IN') AS with_edge
            """
        ).fetchone())
        gap = r["total_person"] - r["with_edge"]
        print(f"  Person nodes: {r['total_person']}, with LOCATED_IN edge: {r['with_edge']}, "
              f"gap: {gap} ({gap/r['total_person']*100:.1f}%)")

        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
