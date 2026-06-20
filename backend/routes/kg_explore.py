"""
backend/routes/kg_explore.py

Routes for the D8 Explore Knowledge page.

GET  /api/kg/graph           — graph topology (nodes + edges) for D3.js
GET  /api/kg/scenarios       — canned SQL vs SPARQL comparison scenarios
POST /api/kg/compare         — run a single scenario: {id, custom_sql, custom_sparql}
GET  /api/kg/rdf-status      — rdflib graph health (triple count, file present)
"""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

bp = Blueprint("kg_explore", __name__)
log = logging.getLogger(__name__)


@bp.get("/graph")
def kg_graph():
    limit_raw = request.args.get("limit", "600")
    try:
        limit = min(int(limit_raw), 1000)
    except (ValueError, TypeError):
        limit = 600

    from backend.services.kg_service import build_graph
    data = build_graph(limit=limit)
    return jsonify(data)


@bp.get("/scenarios")
def kg_scenarios():
    from backend.services.kg_service import COMPARISON_SCENARIOS
    return jsonify({"scenarios": COMPARISON_SCENARIOS})


@bp.post("/compare")
def kg_compare():
    body = request.get_json(silent=True) or {}
    scenario_id = (body.get("id") or "").strip()
    custom_sql = (body.get("custom_sql") or "").strip() or None
    custom_sparql = (body.get("custom_sparql") or "").strip() or None

    from backend.services.kg_service import COMPARISON_SCENARIOS, run_sql, run_sparql

    scenario: dict | None = None
    if scenario_id:
        scenario = next((s for s in COMPARISON_SCENARIOS if s["id"] == scenario_id), None)

    sql_text = custom_sql or (scenario["sql"] if scenario else None)
    sparql_text = custom_sparql or (scenario["sparql"] if scenario else None)

    if not sql_text and not sparql_text:
        return jsonify({"error": "Provide either an id or custom_sql/custom_sparql."}), 400

    import time

    result: dict = {}
    DISPLAY_CAP = 500  # rows sent to browser

    if sql_text:
        t0 = time.perf_counter()
        sql_cols, sql_rows, sql_err = run_sql(sql_text, max_rows=DISPLAY_CAP)
        sql_ms = int((time.perf_counter() - t0) * 1000)
        result["sql"] = {
            "query": sql_text,
            "columns": sql_cols,
            "rows": sql_rows,
            "row_count": len(sql_rows),
            "capped": len(sql_rows) == DISPLAY_CAP,
            "error": sql_err,
            "duration_ms": sql_ms,
        }

    if sparql_text:
        t0 = time.perf_counter()
        sparql_cols, sparql_rows, sparql_err = run_sparql(sparql_text)
        sparql_ms = int((time.perf_counter() - t0) * 1000)
        # Cap to DISPLAY_CAP rows for browser performance
        capped = len(sparql_rows) > DISPLAY_CAP
        result["sparql"] = {
            "query": sparql_text,
            "columns": sparql_cols,
            "rows": sparql_rows[:DISPLAY_CAP],
            "row_count": len(sparql_rows[:DISPLAY_CAP]),
            "total_row_count": len(sparql_rows),
            "capped": capped,
            "error": sparql_err,
            "duration_ms": sparql_ms,
        }

    if scenario:
        result["scenario"] = {
            "id": scenario["id"],
            "label": scenario["label"],
            "description": scenario["description"],
        }

    return jsonify(result)


@bp.post("/explain-mismatch")
def kg_explain_mismatch():
    """
    POST body: {
      sql_query, sparql_query,
      sql_rows, sparql_rows,          (first few rows as list of dicts)
      sql_row_count, sparql_row_count
    }
    Returns LLM analysis of why the two result sets differ.
    """
    body = request.get_json(silent=True) or {}
    sql_query = (body.get("sql_query") or "").strip()
    sparql_query = (body.get("sparql_query") or "").strip()
    sql_rows = body.get("sql_rows") or []
    sparql_rows = body.get("sparql_rows") or []
    sql_row_count = int(body.get("sql_row_count") or 0)
    sparql_row_count = int(body.get("sparql_row_count") or 0)

    if not sql_query and not sparql_query:
        return jsonify({"error": "No queries provided."}), 400

    from backend.services.kg_service import explain_mismatch
    result = explain_mismatch(
        sql_query=sql_query,
        sparql_query=sparql_query,
        sql_rows=sql_rows,
        sparql_rows=sparql_rows,
        sql_row_count=sql_row_count,
        sparql_row_count=sparql_row_count,
    )
    return jsonify(result)


@bp.get("/graphdb-status")
def kg_graphdb_status():
    """Live connectivity check against the GraphDB SPARQL endpoint."""
    from backend.integrations import graphdb_sparql as _gdb
    available = _gdb.probe()
    triple_count = _gdb.triple_count() if available else -1
    from config import ActiveConfig
    return jsonify({
        "enabled": ActiveConfig.GRAPHDB_ENABLED,
        "endpoint": ActiveConfig.GRAPHDB_SPARQL_ENDPOINT,
        "available": available,
        "triple_count": triple_count,
        "data_loaded": triple_count > 0,
    })


@bp.get("/townland/<path:name>")
def kg_townland_persons(name: str):
    """Return person records for a specific townland (detail drill-down from graph)."""
    from backend.services.kg_service import get_townland_persons
    data = get_townland_persons(name)
    return jsonify(data)


@bp.get("/townland-rich/<path:name>")
def kg_townland_rich(name: str):
    """
    Return enriched geographical detail for a townland:
    local DB stats, VRTI KG data, LLM-generated SPARQL query, and a rich narrative.
    """
    from backend.services.kg_service import get_townland_rich_detail
    data = get_townland_rich_detail(name)
    return jsonify(data)


@bp.get("/rdf-status")
def kg_rdf_status():
    from backend.services.kg_service import _load_rdf_graph, _ttl_path
    path = _ttl_path()
    file_present = path.exists()
    file_size_mb = round(path.stat().st_size / (1024 * 1024), 1) if file_present else 0

    g = _load_rdf_graph()
    triple_count = len(g) if g is not None else -1

    return jsonify({
        "file_present": file_present,
        "file_path": str(path),
        "file_size_mb": file_size_mb,
        "triple_count": triple_count,
        "available": g is not None,
    })
