"""
backend/routes/ask.py

Routes
------
POST /api/ask/query          — stream SSE pipeline events, then final result
POST /api/ask/feedback       — thumbs up/down feedback + query memory persistence
GET  /api/ask/llm-status     — LLM provider health/config check
GET  /api/ask/townland-suggest — fuzzy townland suggestions
GET  /api/ask/ollama-status  — backward-compatible status alias
GET  /api/ask/pdf/<name>     — download generated PDF report
"""
from __future__ import annotations

import logging
from pathlib import Path

from flask import Blueprint, Response, abort, jsonify, request, send_file, stream_with_context

bp = Blueprint("ask_api", __name__)
log = logging.getLogger(__name__)


_MAX_QUESTION_LEN = 600
_MAX_TOWNLAND_HINT_LEN = 120


def _sanitize_input(raw: str, max_len: int) -> str:
    """Strip control characters and cap length.  User content only — never applied to system strings."""
    import unicodedata
    cleaned = "".join(
        ch for ch in (raw or "")
        if unicodedata.category(ch)[0] != "C" or ch in ("\n", "\t")
    )
    return cleaned.strip()[:max_len]


@bp.post("/query")
def ask_query():
    body = request.get_json(silent=True) or {}
    question       = _sanitize_input(body.get("question") or "", _MAX_QUESTION_LEN)
    townland_hint  = _sanitize_input(body.get("townland_hint") or body.get("townland") or "", _MAX_TOWNLAND_HINT_LEN) or None
    include_sql    = bool(body.get("show_sql") or body.get("debug_sql"))
    force_llm      = bool(body.get("force_llm"))

    if not question:
        return jsonify({"error": "question is required"}), 400

    # Audit log: IP + question length (not full text) for abuse detection.
    _ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()
    log.info("ask_api.query ip=%s q_len=%d townland_hint=%s", _ip, len(question), bool(townland_hint))

    from backend.services.ask_service import answer_question_stream

    @stream_with_context
    def generate():
        try:
            for event in answer_question_stream(
                question=question,
                townland_hint=townland_hint,
                include_sql=include_sql,
                force_llm=force_llm,
            ):
                yield event
        except Exception as exc:
            log.exception("ask_api.stream_failed")
            import json
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

    return Response(
        generate(),
        content_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # disable nginx / gunicorn buffering
            "Connection": "keep-alive",
        },
    )


@bp.post("/feedback")
def ask_feedback():
    body = request.get_json(silent=True) or {}
    question = (body.get("question") or "").strip()
    feedback = (body.get("feedback") or "").strip().lower()
    if not question:
        return jsonify({"error": "question is required"}), 400
    if feedback not in {"up", "down"}:
        return jsonify({"error": "feedback must be 'up' or 'down'"}), 400

    from backend.services.ask_service import record_query_feedback

    try:
        payload = record_query_feedback(
            question=question,
            townland_hint=(body.get("townland_hint") or "").strip() or None,
            sql_text=body.get("sql_text"),
            vrti_postgres_sql=body.get("vrti_postgres_sql"),
            feedback=feedback,
            note=(body.get("note") or "").strip() or None,
            result_row_count=int(body.get("result_row_count") or 0),
            availability_state=(body.get("availability_state") or "").strip() or None,
            llm_meta=body.get("llm_meta") or {},
            reused_memory_id=body.get("reused_memory_id"),
            sample_answer=body.get("sample_answer"),
            summary_json=body.get("summary_json") or {},
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify(payload), 200


@bp.get("/llm-status")
def llm_status():
    from backend.services.ask_service import check_llm_status
    status = check_llm_status()
    http_code = 200 if status.get("available") else 503
    return jsonify(status), http_code


@bp.get("/townland-suggest")
def townland_suggest():
    query = (request.args.get("q") or "").strip()
    from backend.services.ask_service import suggest_townlands
    return jsonify({
        "query": query,
        "suggestions": suggest_townlands(query, limit=8) if query else [],
    })


@bp.get("/townland-catalog")
def townland_catalog():
    """All Wicklow townlands with metadata — fetched once on page load for client-side filtering."""
    from backend.services.ask_service import _townland_catalog
    items = [
        {"name": i["name"], "civil_parish": i.get("civil_parish"), "name_gaelic": i.get("name_gaelic")}
        for i in _townland_catalog()
        if (i.get("county") or "").strip().lower() == "wicklow"
    ]
    resp = jsonify(items)
    resp.headers["Cache-Control"] = "public, max-age=300"
    return resp


@bp.get("/ollama-status")
def ollama_status():
    from backend.services.ask_service import check_llm_status
    status = check_llm_status()
    http_code = 200 if status.get("available") else 503
    return jsonify(status), http_code


@bp.get("/estate-overview")
def estate_overview():
    """Geographic + estate statistics for County Wicklow — used by the All-Townlands panel."""
    from extensions import get_db_conn
    try:
        conn = get_db_conn()
        cur = conn.cursor()

        def one(sql: str, params=()):
            cur.execute(sql, params)
            row = cur.fetchone()
            return row[0] if row else None

        def many(sql: str, params=()):
            cur.execute(sql, params)
            return cur.fetchall()

        W = "UPPER(county)='WICKLOW'"

        # ── County Wicklow geographic hierarchy ───────────────────────────────
        townland_count    = one(f"SELECT COUNT(*) FROM townland WHERE {W}") or 0
        parish_count      = one(f"SELECT COUNT(DISTINCT civil_parish) FROM townland WHERE {W} AND civil_parish IS NOT NULL") or 0
        barony_count      = one(f"SELECT COUNT(DISTINCT barony) FROM townland WHERE {W} AND barony IS NOT NULL") or 0
        gaelic_count      = one(f"SELECT COUNT(*) FROM townland WHERE {W} AND name_gaelic IS NOT NULL AND name_gaelic!=''") or 0
        with_coords       = one(f"SELECT COUNT(*) FROM townland WHERE {W} AND centroid_lat IS NOT NULL") or 0
        with_area         = one(f"SELECT COUNT(*) FROM townland WHERE {W} AND area_sqm IS NOT NULL AND area_sqm>0") or 0
        total_area_sqkm   = one(f"SELECT ROUND(SUM(area_sqm)/1000000.0,1) FROM townland WHERE {W} AND area_sqm IS NOT NULL")

        # Largest townland by area
        row = many(f"SELECT name, ROUND(area_sqm/1000000.0,2) FROM townland WHERE {W} AND area_sqm IS NOT NULL ORDER BY area_sqm DESC LIMIT 1")
        largest_townland  = {"name": row[0][0], "area_sqkm": row[0][1]} if row else None

        # Smallest townland by area (above 0)
        row = many(f"SELECT name, ROUND(area_sqm/1000000.0,4) FROM townland WHERE {W} AND area_sqm > 0 ORDER BY area_sqm ASC LIMIT 1")
        smallest_townland = {"name": row[0][0], "area_sqkm": row[0][1]} if row else None

        # All baronies
        baronies = [r[0] for r in many(f"SELECT DISTINCT barony FROM townland WHERE {W} AND barony IS NOT NULL ORDER BY barony")]

        # Parishes by townland count (top 5)
        top_parishes_by_townlands = [
            {"parish": r[0], "townland_count": r[1]}
            for r in many(f"""
                SELECT civil_parish, COUNT(*) AS c FROM townland
                WHERE {W} AND civil_parish IS NOT NULL
                GROUP BY civil_parish ORDER BY c DESC LIMIT 5
            """)
        ]

        # ── Census: most populated townlands in 1841 ─────────────────────────
        top_pop_1841 = [
            {"name": r[0], "population": r[1]}
            for r in many(f"""
                SELECT t.name, cr.total FROM census_record cr
                JOIN townland t ON t.id=cr.townland_id
                WHERE {W} AND cr.year=1841 AND cr.total IS NOT NULL
                ORDER BY cr.total DESC LIMIT 5
            """)
        ]

        # Most populated civil parish in 1841 (aggregate)
        top_parish_pop_1841 = [
            {"parish": r[0], "population": r[1]}
            for r in many(f"""
                SELECT t.civil_parish, SUM(cr.total) AS pop FROM census_record cr
                JOIN townland t ON t.id=cr.townland_id
                WHERE {W} AND cr.year=1841 AND cr.total IS NOT NULL AND t.civil_parish IS NOT NULL
                GROUP BY t.civil_parish ORDER BY pop DESC LIMIT 5
            """)
        ]

        # Population change 1841→1851 across Wicklow (famine decade)
        pop_1841 = one(f"""
            SELECT SUM(cr.total) FROM census_record cr
            JOIN townland t ON t.id=cr.townland_id WHERE {W} AND cr.year=1841
        """) or 0
        pop_1851 = one(f"""
            SELECT SUM(cr.total) FROM census_record cr
            JOIN townland t ON t.id=cr.townland_id WHERE {W} AND cr.year=1851
        """) or 0
        pop_decline_pct = round((pop_1841 - pop_1851) / pop_1841 * 100, 1) if pop_1841 else None

        # ── Coolattin estate records ──────────────────────────────────────────
        total_records  = one("SELECT COUNT(DISTINCT record_id) FROM unified_record") or 0
        emigrant_count = one("SELECT COUNT(DISTINCT record_id) FROM unified_record WHERE has_emigration_record=1") or 0
        eviction_count = one("SELECT COUNT(DISTINCT record_id) FROM unified_record WHERE has_eviction_record=1") or 0
        tenant_count   = one("SELECT COUNT(DISTINCT record_id) FROM unified_record WHERE has_tenancy_record=1") or 0
        canada_count   = one("SELECT COUNT(DISTINCT record_id) FROM unified_record WHERE is_canada_destination=1") or 0
        year_min       = one("SELECT MIN(year) FROM unified_record WHERE year IS NOT NULL")
        year_max       = one("SELECT MAX(year) FROM unified_record WHERE year IS NOT NULL")

        top_surnames = [
            {"surname": r[0], "count": r[1]}
            for r in many("""
                SELECT surname, COUNT(DISTINCT record_id) AS c FROM unified_record
                WHERE surname IS NOT NULL AND surname!=''
                GROUP BY surname ORDER BY c DESC LIMIT 10
            """)
        ]

        top_baronies_by_records = [
            {"barony": r[0], "count": r[1]}
            for r in many("""
                SELECT t.barony, COUNT(DISTINCT ur.record_id) AS c
                FROM unified_record ur
                JOIN townland t ON UPPER(ur.townland)=UPPER(t.name)
                WHERE t.barony IS NOT NULL
                GROUP BY t.barony ORDER BY c DESC LIMIT 5
            """)
        ]

        return jsonify({
            # County Wicklow geography
            "townland_count":           townland_count,
            "parish_count":             parish_count,
            "barony_count":             barony_count,
            "gaelic_name_count":        gaelic_count,
            "townlands_with_coords":    with_coords,
            "townlands_with_area":      with_area,
            "total_area_sqkm":          total_area_sqkm,
            "largest_townland":         largest_townland,
            "smallest_townland":        smallest_townland,
            "baronies":                 baronies,
            "top_parishes_by_townlands": top_parishes_by_townlands,
            # Census
            "pop_1841":                 int(pop_1841) if pop_1841 else None,
            "pop_1851":                 int(pop_1851) if pop_1851 else None,
            "pop_decline_pct":          pop_decline_pct,
            "top_pop_1841":             top_pop_1841,
            "top_parish_pop_1841":      top_parish_pop_1841,
            # Estate records
            "total_records":            total_records,
            "emigrant_count":           emigrant_count,
            "eviction_count":           eviction_count,
            "tenant_count":             tenant_count,
            "canada_count":             canada_count,
            "year_min":                 year_min,
            "year_max":                 year_max,
            "top_surnames":             top_surnames,
            "top_baronies_by_records":  top_baronies_by_records,
        })
    except Exception as exc:
        log.exception("estate-overview failed")
        return jsonify({"error": str(exc)}), 500


@bp.get("/pdf/<path:filename>")
def ask_pdf_download(filename: str):
    from config import ActiveConfig
    safe_name = Path(filename).name
    if not safe_name.lower().endswith(".pdf"):
        abort(400, description="Only PDF files may be downloaded from this endpoint.")
    pdf_path = ActiveConfig.EXPORTS_DIR / "ask" / safe_name
    if not pdf_path.exists():
        abort(404, description="Report not found.")
    return send_file(pdf_path, as_attachment=True, download_name=safe_name, mimetype="application/pdf")
