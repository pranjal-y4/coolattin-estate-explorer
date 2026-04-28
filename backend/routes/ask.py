"""
backend/routes/ask.py

Routes
------
POST /api/ask/query          — stream SSE pipeline events, then final result
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


@bp.post("/query")
def ask_query():
    body = request.get_json(silent=True) or {}
    question       = (body.get("question") or "").strip()
    townland_hint  = (body.get("townland_hint") or body.get("townland") or "").strip() or None
    include_sql    = bool(body.get("show_sql") or body.get("debug_sql"))
    force_llm      = bool(body.get("force_llm"))

    if not question:
        return jsonify({"error": "question is required"}), 400

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


@bp.get("/ollama-status")
def ollama_status():
    from backend.services.ask_service import check_llm_status
    status = check_llm_status()
    http_code = 200 if status.get("available") else 503
    return jsonify(status), http_code


@bp.get("/pdf/<path:filename>")
def ask_pdf_download(filename: str):
    from config import ActiveConfig
    safe_name = Path(filename).name
    pdf_path = ActiveConfig.EXPORTS_DIR / "ask" / safe_name
    if not pdf_path.exists():
        abort(404, description="Report not found.")
    return send_file(pdf_path, as_attachment=True, download_name=safe_name)
