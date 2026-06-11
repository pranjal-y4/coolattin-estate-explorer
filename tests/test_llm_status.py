from __future__ import annotations

from backend.services import ask_service


def test_friendly_openrouter_connection_issue_dns():
    exc = Exception(
        'HTTPSConnectionPool(host="openrouter.ai", port=443): '
        'Failed to resolve openrouter.ai '
        '(Caused by NameResolutionError("failed to resolve"))'
    )

    hint, detail, issue_code = ask_service._friendly_openrouter_connection_issue(exc)

    assert issue_code == "dns_unreachable"
    assert "cannot currently reach OpenRouter" in hint
    assert "database answer" in hint.lower()
    assert "resolve" in detail.lower()


def test_friendly_openrouter_connection_issue_timeout():
    exc = Exception("ReadTimeout: request timed out")

    hint, detail, issue_code = ask_service._friendly_openrouter_connection_issue(exc)

    assert issue_code == "timeout"
    assert "timed out" in hint.lower()
    assert "timed out" in detail.lower()
