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
