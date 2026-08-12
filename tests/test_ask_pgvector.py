from __future__ import annotations

import ast
import logging
import os
import time

import pytest

from backend.services import ask_pgvector
from backend.services import embedding_index


def _reset_sync_state() -> None:
    ask_pgvector._SYNC_STATE.update(
        {
            "status": "never",
            "reason": "not_started",
            "synced_at": 0.0,
            "chunk_count": 0,
            "dense_backend": "pgvector",
        }
    )


class _FakeCursor:
    def __init__(self, state: dict):
        self.state = state
        self._results = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql: str, params=None):
        self.state["queries"].append((sql, params))
        if "INSERT INTO ask_retrieval_chunks" in sql:
            chunk_id, source_type, source_table, source_record_id, title, text, metadata_json, content_hash, vector_literal = params
            embedding = ast.literal_eval(vector_literal)
            self.state["rows"][chunk_id] = (
                chunk_id,
                source_type,
                source_table,
                source_record_id,
                title,
                text,
                metadata_json,
                embedding,
                content_hash,
            )
            self._results = []
        elif "SELECT chunk_id, content_hash FROM ask_retrieval_chunks" in sql:
            self._results = [(row[0], row[8]) for row in self.state["rows"].values()]
        elif "DELETE FROM ask_retrieval_chunks WHERE chunk_id = %s" in sql:
            chunk_id = params[0]
            self.state["rows"].pop(chunk_id, None)
            self._results = []
        elif "FROM ask_retrieval_chunks" in sql and "<=>" in sql:
            query_embedding = ast.literal_eval(params[0])
            top_k = int(params[2])
            ranked = []
            for row in self.state["rows"].values():
                embedding = row[7]
                similarity = sum(a * b for a, b in zip(query_embedding, embedding))
                ranked.append((row[0], row[1], row[2], row[3], row[4], row[5], row[6], similarity))
            ranked.sort(key=lambda item: item[7], reverse=True)
            self._results = ranked[:top_k]
        else:
            self._results = []
        return self

    def fetchall(self):
        return list(self._results)


class _FakeConnection:
    def __init__(self, state: dict):
        self.state = state

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return _FakeCursor(self.state)


class _FakePsycopg:
    def __init__(self, state: dict):
        self.state = state

    def connect(self, *args, **kwargs):
        return _FakeConnection(self.state)


def test_pgvector_sync_and_dense_query_use_sql(monkeypatch):
    state = {"rows": {}, "queries": []}
    _reset_sync_state()
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/test")
    monkeypatch.setenv("VOYAGE_OUTPUT_DIMENSION", "2")
    monkeypatch.setattr(ask_pgvector, "psycopg", _FakePsycopg(state))
    monkeypatch.setattr(
        "backend.services.retrieval_chunks.build_retrieval_chunks",
        lambda: [
            {
                "id": "chunk:1",
                "chunk_id": "chunk:1",
                "source_type": "place_passport",
                "source_table": "townland",
                "source_record_id": "1",
                "title": "Coolboy",
                "text": "Coolboy townland summary",
                "metadata": {"name": "Coolboy"},
            },
            {
                "id": "chunk:2",
                "chunk_id": "chunk:2",
                "source_type": "workhouse_record",
                "source_table": "workhouse_excel",
                "source_record_id": "WH1",
                "title": "John Doe",
                "text": "John Doe workhouse record",
                "metadata": {"name": "John Doe"},
            },
        ],
    )
    monkeypatch.setattr(
        "backend.services.voyage_embeddings.embed_documents",
        lambda texts: [[1.0, 0.0], [0.0, 1.0]],
    )
    monkeypatch.setattr(
        "backend.services.voyage_embeddings.embed_query",
        lambda text: [1.0, 0.0],
    )

    sync_meta = ask_pgvector.sync_retrieval_chunks(force=True)
    assert sync_meta["status"] == "completed"
    assert sync_meta["chunk_count"] == 2
    assert set(state["rows"]) == {"chunk:1", "chunk:2"}

    dense_rows, dense_meta = ask_pgvector.dense_retrieve("Coolboy", top_k=1)
    assert dense_meta["status"] == "completed"
    assert dense_rows[0]["chunk_id"] == "chunk:1"
    assert any("<=>" in sql for sql, _ in state["queries"])


def test_pgvector_rows_survive_sync_state_reset(monkeypatch):
    state = {"rows": {}, "queries": []}
    embed_calls = {"count": 0}
    _reset_sync_state()
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/test")
    monkeypatch.setenv("VOYAGE_OUTPUT_DIMENSION", "2")
    monkeypatch.setattr(ask_pgvector, "psycopg", _FakePsycopg(state))
    monkeypatch.setattr(
        "backend.services.retrieval_chunks.build_retrieval_chunks",
        lambda: [
            {
                "id": "chunk:stable",
                "chunk_id": "chunk:stable",
                "source_type": "source_summary",
                "source_table": "system",
                "source_record_id": "summary",
                "title": "Summary",
                "text": "Stable summary chunk",
                "metadata": {},
            }
        ],
    )
    monkeypatch.setattr(
        "backend.services.voyage_embeddings.embed_documents",
        lambda texts: embed_calls.__setitem__("count", embed_calls["count"] + 1) or [[0.4, 0.6]],
    )

    ask_pgvector.sync_retrieval_chunks(force=True)
    assert "chunk:stable" in state["rows"]
    assert embed_calls["count"] == 1

    _reset_sync_state()
    second_sync = ask_pgvector.sync_retrieval_chunks(force=False)
    assert "chunk:stable" in state["rows"]
    assert second_sync["status"] == "completed"
    assert second_sync["updated_count"] == 0
    assert embed_calls["count"] == 1


def test_pgvector_missing_is_skipped(monkeypatch):
    _reset_sync_state()
    monkeypatch.setenv("DATABASE_URL", "sqlite:///tmp/test.db")
    monkeypatch.setattr(ask_pgvector, "psycopg", None)
    rows, meta = ask_pgvector.dense_retrieve("anything", top_k=3)
    assert rows == []
    assert meta["status"] == "skipped"
    assert meta["reason"] in {"database_url_not_postgres", "psycopg_not_installed"}


def test_retrieve_chunks_falls_back_to_in_memory_when_pgvector_skipped(monkeypatch):
    monkeypatch.setattr(
        "backend.services.ask_pgvector.dense_retrieve",
        lambda query, top_k=8: ([], {"status": "skipped", "reason": "pgvector_unavailable"}),
    )
    embedding_index._CHUNKS[:] = [
        {"id": "a", "text": "Coolboy workhouse note", "embedding": [1.0, 0.0], "metadata": {}},
        {"id": "b", "text": "Ballinacor place summary", "embedding": [0.0, 1.0], "metadata": {}},
    ]
    embedding_index._CHUNKS_BUILT_AT = time.time()
    monkeypatch.setattr(
        "backend.services.voyage_embeddings.embed_query",
        lambda query: [1.0, 0.0],
    )

    rows, meta = embedding_index.retrieve_chunks_with_meta("Coolboy workhouse", top_k=2)
    assert rows
    assert meta["dense_backend"] == "voyage_in_memory"
    assert meta["dense_status"] == "fallback"
    assert meta["pgvector_status"] == "skipped"


def test_retrieve_chunks_prefers_pgvector_when_available(monkeypatch):
    monkeypatch.setattr(
        "backend.services.ask_pgvector.dense_retrieve",
        lambda query, top_k=8: (
            [
                {
                    "id": "pg-1",
                    "chunk_id": "pg-1",
                    "source_type": "place_passport",
                    "source_table": "townland",
                    "source_record_id": "1",
                    "title": "Coolboy",
                    "text": "Coolboy place passport",
                    "metadata": {"name": "Coolboy"},
                    "similarity_score": 0.98,
                }
            ],
            {"status": "completed", "reason": None, "retrieved_count": 1},
        ),
    )
    embedding_index._CHUNKS[:] = [
        {"id": "pg-1", "text": "Coolboy place passport", "metadata": {"name": "Coolboy"}},
        {"id": "kw-1", "text": "coolboy keyword chunk", "metadata": {"name": "Coolboy"}},
    ]
    embedding_index._CHUNKS_BUILT_AT = time.time()

    rows, meta = embedding_index.retrieve_chunks_with_meta("Coolboy keyword", top_k=2)
    assert rows
    assert rows[0]["id"] == "pg-1"
    assert meta["dense_backend"] == "pgvector"
    assert meta["dense_status"] == "completed"
    assert meta["pgvector_status"] == "completed"


def test_rrf_combines_dense_and_sparse_results(monkeypatch):
    monkeypatch.setattr(
        "backend.services.ask_pgvector.dense_retrieve",
        lambda query, top_k=8: (
            [
                {
                    "id": "dense-1",
                    "chunk_id": "dense-1",
                    "source_type": "place_passport",
                    "source_table": "townland",
                    "source_record_id": "1",
                    "title": "Dense First",
                    "text": "semantic context",
                    "metadata": {},
                    "similarity_score": 0.99,
                }
            ],
            {"status": "completed", "reason": None, "retrieved_count": 1},
        ),
    )
    embedding_index._CHUNKS[:] = [
        {"id": "dense-1", "text": "semantic context", "metadata": {}},
        {"id": "sparse-1", "text": "coolboy keyword keyword keyword", "metadata": {}},
    ]
    embedding_index._CHUNKS_BUILT_AT = time.time()

    rows, meta = embedding_index.retrieve_chunks_with_meta("coolboy keyword", top_k=2)
    row_ids = [row["id"] for row in rows]
    assert "dense-1" in row_ids
    assert "sparse-1" in row_ids
    assert meta["fusion_backend"] == "rrf"


@pytest.mark.skipif(
    not os.environ.get("TEST_PGVECTOR_DSN"),
    reason="TEST_PGVECTOR_DSN not configured for live pgvector integration",
)
def test_live_pgvector_integration_placeholder():
    assert True


def _reset_warning_flag() -> None:
    ask_pgvector._PSYCOPG_MISSING_WARNED = False


def test_psycopg_missing_with_new_pipeline_emits_warning(monkeypatch, caplog):
    _reset_sync_state()
    _reset_warning_flag()
    monkeypatch.setattr(ask_pgvector, "psycopg", None)
    monkeypatch.setenv("ASK_USE_NEW_PIPELINE", "true")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/test")

    with caplog.at_level(logging.WARNING, logger="backend.services.ask_pgvector"):
        ask_pgvector.backend_status()
        ask_pgvector.backend_status()

    warning_lines = [r for r in caplog.records if "psycopg not installed" in r.message]
    assert len(warning_lines) == 1, (
        f"Expected exactly 1 warning; got {len(warning_lines)}: {[r.message for r in warning_lines]}"
    )


def test_psycopg_missing_new_pipeline_false_no_warning(monkeypatch, caplog):
    _reset_sync_state()
    _reset_warning_flag()
    monkeypatch.setattr(ask_pgvector, "psycopg", None)
    monkeypatch.setenv("ASK_USE_NEW_PIPELINE", "false")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/test")

    with caplog.at_level(logging.WARNING, logger="backend.services.ask_pgvector"):
        ask_pgvector.backend_status()

    warning_lines = [r for r in caplog.records if "psycopg not installed" in r.message]
    assert warning_lines == [], f"Expected no warning; got: {[r.message for r in warning_lines]}"


def test_psycopg_present_no_warning(monkeypatch, caplog):
    _reset_sync_state()
    _reset_warning_flag()
    state = {"rows": {}, "queries": []}
    monkeypatch.setattr(ask_pgvector, "psycopg", _FakePsycopg(state))
    monkeypatch.setenv("ASK_USE_NEW_PIPELINE", "true")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/test")

    with caplog.at_level(logging.WARNING, logger="backend.services.ask_pgvector"):
        ask_pgvector.backend_status()

    warning_lines = [r for r in caplog.records if "psycopg not installed" in r.message]
    assert warning_lines == [], f"Expected no warning; got: {[r.message for r in warning_lines]}"


def _make_chunks(n: int) -> list[dict]:
    return [
        {
            "id": f"chunk:{i}",
            "chunk_id": f"chunk:{i}",
            "source_type": "place_passport",
            "source_table": "townland",
            "source_record_id": str(i),
            "title": f"Place {i}",
            "text": f"Text for place {i}",
            "metadata": {},
        }
        for i in range(n)
    ]


def test_pgvector_all_embeddings_fail_returns_failed(monkeypatch):
    state = {"rows": {}, "queries": []}
    _reset_sync_state()
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/test")
    monkeypatch.setenv("VOYAGE_OUTPUT_DIMENSION", "2")
    monkeypatch.setattr(ask_pgvector, "psycopg", _FakePsycopg(state))
    monkeypatch.setattr(
        "backend.services.retrieval_chunks.build_retrieval_chunks",
        lambda: _make_chunks(3),
    )
    monkeypatch.setattr(
        "backend.services.voyage_embeddings.embed_documents",
        lambda texts: [[] for _ in texts],
    )

    result = ask_pgvector.sync_retrieval_chunks(force=True)

    assert result["status"] == "failed", (
        f"Expected 'failed' when all embeddings are empty, got '{result['status']}'"
    )
    assert result["succeeded_count"] == 0
    assert result["failed_count"] == 3
    assert not state["rows"], "No rows should have been written when all embeddings failed"


def test_pgvector_partial_embeddings_fail_returns_completed_with_failures(monkeypatch):
    state = {"rows": {}, "queries": []}
    _reset_sync_state()
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/test")
    monkeypatch.setenv("VOYAGE_OUTPUT_DIMENSION", "2")
    monkeypatch.setattr(ask_pgvector, "psycopg", _FakePsycopg(state))
    monkeypatch.setattr(
        "backend.services.retrieval_chunks.build_retrieval_chunks",
        lambda: _make_chunks(4),
    )
    monkeypatch.setattr(
        "backend.services.voyage_embeddings.embed_documents",
        lambda texts: [[1.0, 0.0] if i < 2 else [] for i in range(len(texts))],
    )

    result = ask_pgvector.sync_retrieval_chunks(force=True)

    assert result["status"] == "completed_with_failures", (
        f"Expected 'completed_with_failures', got '{result['status']}'"
    )
    assert result["succeeded_count"] == 2
    assert result["failed_count"] == 2
    assert len(state["rows"]) == 2, (
        f"Expected exactly 2 rows written, found {len(state['rows'])}"
    )


def test_embed_input_type_asymmetry(monkeypatch):
    from backend.services import voyage_embeddings

    monkeypatch.setenv("EMBEDDING_PROVIDER", "cohere")

    recorded: list[str] = []

    def fake_api_embed(texts: list[str], input_type: str) -> list[list[float]]:
        recorded.append(input_type)
        return [[0.1] * 1024 for _ in texts]

    monkeypatch.setattr(voyage_embeddings, "_api_embed", fake_api_embed)
    monkeypatch.setattr(voyage_embeddings, "COHERE_API_KEY", "fake-key")
    monkeypatch.setattr(voyage_embeddings, "_client", object())
    monkeypatch.setattr(voyage_embeddings, "_key_loaded", True)
    monkeypatch.setattr(voyage_embeddings, "_DOC_CACHE", {})

    voyage_embeddings.embed_documents(["Some corpus text about Coolattin tenants."])
    voyage_embeddings.embed_query("Who is Edward Dagg from Aghowle?")

    assert len(recorded) == 2, f"Expected 2 API calls, got {len(recorded)}: {recorded}"
    assert recorded[0] == "search_document", (
        f"embed_documents must use input_type='search_document'; got '{recorded[0]}'"
    )
    assert recorded[1] == "search_query", (
        f"embed_query must use input_type='search_query'; got '{recorded[1]}'"
    )
    assert recorded[0] != recorded[1], (
        "input_type must differ between corpus (search_document) and query (search_query) paths"
    )


def test_pgvector_dense_retrieve_works_after_completed_with_failures(monkeypatch):
    state = {"rows": {}, "queries": []}
    _reset_sync_state()
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/test")
    monkeypatch.setenv("VOYAGE_OUTPUT_DIMENSION", "2")
    monkeypatch.setattr(ask_pgvector, "psycopg", _FakePsycopg(state))
    monkeypatch.setattr(
        "backend.services.retrieval_chunks.build_retrieval_chunks",
        lambda: _make_chunks(2),
    )
    monkeypatch.setattr(
        "backend.services.voyage_embeddings.embed_documents",
        lambda texts: [[1.0, 0.0]] + [[] for _ in texts[1:]],
    )
    monkeypatch.setattr(
        "backend.services.voyage_embeddings.embed_query",
        lambda text: [1.0, 0.0],
    )

    sync_result = ask_pgvector.sync_retrieval_chunks(force=True)
    assert sync_result["status"] == "completed_with_failures"

    rows, meta = ask_pgvector.dense_retrieve("any query", top_k=5)
    assert meta["status"] == "completed", (
        "dense_retrieve should report completed when it successfully ran a query"
    )
    assert len(rows) == 1, f"Expected the 1 stored chunk to be returned; got {len(rows)}"


def test_failed_chunks_are_retried_after_ttl_without_force(monkeypatch):
    state = {"rows": {}, "queries": []}
    call_n: dict[str, int] = {"n": 0}
    _reset_sync_state()
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/test")
    monkeypatch.setenv("VOYAGE_OUTPUT_DIMENSION", "2")
    monkeypatch.setattr(ask_pgvector, "psycopg", _FakePsycopg(state))
    monkeypatch.setattr(
        "backend.services.retrieval_chunks.build_retrieval_chunks",
        lambda: _make_chunks(3),
    )

    def embed_by_call(texts: list[str]) -> list[list[float]]:
        call_n["n"] += 1
        if call_n["n"] == 1:
            return [[1.0, 0.0], [0.0, 1.0], []]
        return [[0.5, 0.5]] * len(texts)

    monkeypatch.setattr("backend.services.voyage_embeddings.embed_documents", embed_by_call)

    r1 = ask_pgvector.sync_retrieval_chunks(force=True)
    assert r1["status"] == "completed_with_failures", f"Unexpected initial status: {r1['status']}"
    assert len(state["rows"]) == 2
    assert "chunk:2" not in state["rows"], "chunk:2 should not be in DB after first sync"

    ask_pgvector._SYNC_STATE["synced_at"] = time.time() - ask_pgvector._SYNC_TTL_SECONDS - 1

    r2 = ask_pgvector.sync_retrieval_chunks(force=False)
    assert r2["status"] == "completed", (
        f"Expected 'completed' after TTL healing (force=False); got '{r2['status']}'"
    )
    assert len(state["rows"]) == 3, (
        f"Expected all 3 chunks after healing; found {len(state['rows'])}"
    )
    assert "chunk:2" in state["rows"], "chunk:2 must be healed by the post-TTL sync"
    assert call_n["n"] == 2, (
        f"Expected exactly 2 embed_documents calls (initial + TTL retry); got {call_n['n']}"
    )


def test_local_embed_input_type_asymmetry(monkeypatch):
    from backend.services import local_embeddings
    import numpy as np

    texts_seen: list[str] = []

    class _FakeModel:
        def encode(self, texts, *, normalize_embeddings=True, show_progress_bar=False, batch_size=64):
            texts_seen.extend(texts)
            return np.array([[0.1] * 1024 for _ in texts])

    monkeypatch.setattr(local_embeddings, "_model", _FakeModel())

    doc_text = "Edward Dagg held land in Aghowle Lower, Coolattin estate."
    query_text = "Who is Edward Dagg from Aghowle?"

    local_embeddings.embed_texts_local([doc_text], input_type="document")
    local_embeddings.embed_texts_local([query_text], input_type="query")

    assert len(texts_seen) == 2, f"Expected 2 encode calls; got {len(texts_seen)}"

    assert texts_seen[0] == doc_text, (
        f"Document must reach model as-is (no prefix); got: {texts_seen[0]!r}"
    )
    expected_query = local_embeddings.BGE_QUERY_INSTRUCTION + query_text
    assert texts_seen[1] == expected_query, (
        f"Query must have BGE_QUERY_INSTRUCTION prefix;\n"
        f"  expected: {expected_query!r}\n"
        f"  got:      {texts_seen[1]!r}"
    )
    assert texts_seen[0] != texts_seen[1], (
        "query and document texts sent to the model must NOT be identical — "
        "prefix asymmetry is broken"
    )
