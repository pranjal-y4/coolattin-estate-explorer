from __future__ import annotations

import json

import pytest

from backend.services.ask_service import (
    _extract_numeric_tokens,
    _synthesis_allowed_numbers,
    _assert_rewrite_numbers_supported,
    _cross_verify_synthesis,
)


def test_extract_integers():
    tokens = _extract_numeric_tokens("There were 6016 emigrants and 7763 evictions.")
    assert "6016" in tokens
    assert "7763" in tokens


def test_extract_with_comma_separator():
    tokens = _extract_numeric_tokens("A total of 6,016 people emigrated.")
    assert "6016" in tokens


def test_extract_float():
    tokens = _extract_numeric_tokens("Average holding: 3.5 acres.")
    assert "3" in tokens or "3.5" in tokens


def test_extract_empty_text():
    assert _extract_numeric_tokens("") == set()


def test_allowed_numbers_from_rows():
    sql_result = {"rows": [{"emigration_count": 6016}]}
    allowed = _synthesis_allowed_numbers(sql_result, "")
    assert "6016" in allowed


def test_allowed_numbers_digit_subsequences():
    sql_result = {"rows": [{"n": 6016}]}
    allowed = _synthesis_allowed_numbers(sql_result, "")
    assert "6" in allowed
    assert "60" in allowed
    assert "601" in allowed


def test_allowed_numbers_from_graph_context():
    sql_result = {"rows": []}
    allowed = _synthesis_allowed_numbers(sql_result, "Population in 1841 was recorded.")
    assert "1841" in allowed


def test_gate_passes_when_numbers_match(tmp_path):
    _assert_rewrite_numbers_supported(
        rewrite_text="6016 people emigrated from the estate.",
        actual_answer="6016",
        summary_block={"stats": {"emigration_count": 6016}},
        data_context={"local_database": {"rows": [{"emigration_count": 6016}]}},
        supporting_context={},
        kg_context=None,
    )


def test_gate_raises_on_injected_wrong_number():
    with pytest.raises(RuntimeError, match="unsupported numeric"):
        _assert_rewrite_numbers_supported(
            rewrite_text="Remarkably, 99999 people emigrated according to our records.",
            actual_answer="6016",
            summary_block={"stats": {"emigration_count": 6016}},
            data_context={"local_database": {"rows": [{"emigration_count": 6016}]}},
            supporting_context={},
            kg_context=None,
        )


def test_gate_allows_digit_subsequences():
    _assert_rewrite_numbers_supported(
        rewrite_text="About 601 records fall in this range.",
        actual_answer="6016",
        summary_block={"stats": {"emigration_count": 6016}},
        data_context={"local_database": {"rows": [{"emigration_count": 6016}]}},
        supporting_context={},
        kg_context=None,
    )

    with pytest.raises(RuntimeError, match="unsupported numeric"):
        _assert_rewrite_numbers_supported(
            rewrite_text="Over 6,000 people emigrated.",
            actual_answer="6016",
            summary_block={"stats": {"emigration_count": 6016}},
            data_context={"local_database": {"rows": [{"emigration_count": 6016}]}},
            supporting_context={},
            kg_context=None,
        )


def test_cross_verifier_detects_injected_wrong_answer(monkeypatch):
    import backend.services.ask_service as svc

    injected_verdict = json.dumps({
        "verdict": "disagree",
        "unsupported_claims": ["99999 people emigrated"],
    })

    monkeypatch.setattr(
        svc,
        "_llm_generate",
        lambda *args, **kwargs: (injected_verdict, {"provider": "mock", "model": "mock"}),
    )
    monkeypatch.setattr(svc, "GROK_API_KEY", "")

    result = _cross_verify_synthesis(
        question="How many people emigrated?",
        synthesis_text="Remarkably, 99999 people emigrated according to our records.",
        sql_result={"rows": [{"emigration_count": 6016}]},
    )
    assert result["verdict"] == "disagree"
    assert len(result["unsupported_claims"]) > 0
    assert result["agreement_rate"] == 0.0


def test_cross_verifier_agrees_on_correct_answer(monkeypatch):
    import backend.services.ask_service as svc

    injected_verdict = json.dumps({
        "verdict": "agree",
        "unsupported_claims": [],
    })

    monkeypatch.setattr(
        svc,
        "_llm_generate",
        lambda *args, **kwargs: (injected_verdict, {"provider": "mock", "model": "mock"}),
    )
    monkeypatch.setattr(svc, "GROK_API_KEY", "")

    result = _cross_verify_synthesis(
        question="How many people emigrated?",
        synthesis_text="6016 people emigrated from the Coolattin estate.",
        sql_result={"rows": [{"emigration_count": 6016}]},
    )
    assert result["verdict"] == "agree"
    assert result["unsupported_claims"] == []
    assert result["agreement_rate"] == 1.0


def test_cross_verifier_skips_on_empty_rows():
    result = _cross_verify_synthesis(
        question="How many people emigrated?",
        synthesis_text="Some answer.",
        sql_result={"rows": []},
    )
    assert result["verdict"] == "skip"


def test_cross_verifier_skips_on_empty_synthesis():
    result = _cross_verify_synthesis(
        question="How many people emigrated?",
        synthesis_text="",
        sql_result={"rows": [{"n": 6016}]},
    )
    assert result["verdict"] == "skip"
