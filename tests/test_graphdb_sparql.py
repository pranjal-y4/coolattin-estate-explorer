from __future__ import annotations

import requests

from backend.integrations import graphdb_sparql as gdb


class _Resp:
    def raise_for_status(self) -> None:
        return None


def test_probe_caches_success(monkeypatch):
    calls = {"n": 0}

    def fake_get(url, timeout):
        calls["n"] += 1
        return _Resp()

    monkeypatch.setattr(gdb.ActiveConfig, "GRAPHDB_ENABLED", True, raising=False)
    monkeypatch.setattr(gdb.requests, "get", fake_get)
    monkeypatch.setattr(gdb.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(gdb, "_probe_cache", {"checked_at": 0.0, "status": None})

    assert gdb.probe(force=True) is True
    assert gdb.probe() is True
    assert calls["n"] == 1


def test_probe_caches_failure(monkeypatch):
    calls = {"n": 0}

    def fake_get(url, timeout):
        calls["n"] += 1
        raise requests.exceptions.Timeout("boom")

    monkeypatch.setattr(gdb.ActiveConfig, "GRAPHDB_ENABLED", True, raising=False)
    monkeypatch.setattr(gdb.requests, "get", fake_get)
    monkeypatch.setattr(gdb.time, "monotonic", lambda: 200.0)
    monkeypatch.setattr(gdb, "_probe_cache", {"checked_at": 0.0, "status": None})

    assert gdb.probe(force=True) is False
    assert gdb.probe() is False
    assert calls["n"] == 1
