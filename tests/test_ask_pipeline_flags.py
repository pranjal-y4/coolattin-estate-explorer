from __future__ import annotations

from backend.services import ask_service


def test_answer_question_stream_uses_new_pipeline_when_flag_enabled(monkeypatch):
    monkeypatch.setattr(ask_service, "ASK_USE_NEW_PIPELINE", True)

    def _fake_new_pipeline(*args, **kwargs):
        yield "data: {\"type\":\"progress\",\"stage\":\"retrieving_vectors\"}\n\n"

    monkeypatch.setattr(ask_service, "_orchestrated_pipeline_stream", _fake_new_pipeline)
    events = list(ask_service.answer_question_stream("Tell me about Coolboy"))
    assert any("retrieving_vectors" in event for event in events)


def test_answer_question_stream_avoids_new_pipeline_when_flag_disabled(monkeypatch):
    monkeypatch.setattr(ask_service, "ASK_USE_NEW_PIPELINE", False)
    monkeypatch.setattr(
        ask_service,
        "_orchestrated_pipeline_stream",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("new pipeline should not run")),
    )
    monkeypatch.setattr(
        ask_service,
        "_ensure_unified_table_seeded",
        lambda: (_ for _ in ()).throw(RuntimeError("old_path_marker")),
    )
    events = list(ask_service.answer_question_stream("Tell me about Coolboy"))
    assert any("old_path_marker" in event for event in events)
