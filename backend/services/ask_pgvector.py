"""
backend/services/ask_pgvector.py

Persistent pgvector retrieval backend for the Ask page.

This module is intentionally isolated from the rest of the application DB
layer: the main app continues to use SQLite, while Ask-page dense retrieval
optionally uses a PostgreSQL pgvector store through DATABASE_URL.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from typing import Any

from config import ActiveConfig

log = logging.getLogger(__name__)

try:  # pragma: no cover - optional dependency
    import psycopg  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    psycopg = None

_PSYCOPG_MISSING_WARNED = False


def _warn_if_psycopg_missing() -> None:
    global _PSYCOPG_MISSING_WARNED
    if psycopg is None and not _PSYCOPG_MISSING_WARNED:
        use_new = os.environ.get("ASK_USE_NEW_PIPELINE", "true").lower() not in (
            "false", "0", "no"
        )
        if use_new:
            _PSYCOPG_MISSING_WARNED = True
            log.warning(
                "ASK_USE_NEW_PIPELINE=true but psycopg not installed; "
                "falling back to legacy retrieval."
            )


_SYNC_LOCK = threading.Lock()
_SYNC_STATE: dict[str, Any] = {
    "status": "never",
    "reason": "not_started",
    "synced_at": 0.0,
    "chunk_count": 0,
    "dense_backend": "pgvector",
}
_SYNC_TTL_SECONDS = 3600.0


def _database_url() -> str:
    raw = os.environ.get("DATABASE_URL", "") or getattr(ActiveConfig, "DATABASE_URL", "")
    return str(raw or "").strip()


def _is_postgres_url(url: str) -> bool:
    return url.startswith("postgresql://") or url.startswith("postgres://")


def _connect():
    if psycopg is None:
        raise RuntimeError("psycopg_not_installed")
    dsn = _database_url()
    if not _is_postgres_url(dsn):
        raise RuntimeError("database_url_not_postgres")
    return psycopg.connect(dsn, autocommit=True)


def backend_status() -> dict[str, Any]:
    _warn_if_psycopg_missing()
    dsn = _database_url()
    if not dsn:
        return {
            "enabled": False,
            "available": False,
            "reason": "database_url_missing",
            "dense_backend": "pgvector",
        }
    if not _is_postgres_url(dsn):
        return {
            "enabled": False,
            "available": False,
            "reason": "database_url_not_postgres",
            "dense_backend": "pgvector",
        }
    if psycopg is None:
        return {
            "enabled": False,
            "available": False,
            "reason": "psycopg_not_installed",
            "dense_backend": "pgvector",
        }
    return {
        "enabled": True,
        "available": True,
        "reason": None,
        "dense_backend": "pgvector",
    }


def sync_state() -> dict[str, Any]:
    return dict(_SYNC_STATE)


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{float(v):.8f}" for v in values) + "]"


def _vector_dimension() -> int:
    raw = os.environ.get("VOYAGE_OUTPUT_DIMENSION", "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    try:
        from backend.services.voyage_embeddings import VOYAGE_OUTPUT_DIMENSION

        return max(1, int(VOYAGE_OUTPUT_DIMENSION))
    except Exception:
        return 1024


def _content_hash(chunk: dict[str, Any]) -> str:
    payload = {
        "title": chunk.get("title"),
        "text": chunk.get("text"),
        "metadata": chunk.get("metadata") or {},
        "source_type": chunk.get("source_type"),
        "source_table": chunk.get("source_table"),
        "source_record_id": chunk.get("source_record_id"),
    }
    return hashlib.sha1(
        json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def _row_value(row: Any, key: str, index: int) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[key]
    except Exception:
        try:
            return getattr(row, key)
        except Exception:
            return row[index]


def _fetch_existing_hashes(cur) -> dict[str, str]:
    cur.execute("SELECT chunk_id, content_hash FROM ask_retrieval_chunks")
    rows = cur.fetchall() or []
    existing: dict[str, str] = {}
    for row in rows:
        chunk_id = _row_value(row, "chunk_id", 0)
        content_hash = _row_value(row, "content_hash", 1)
        if chunk_id:
            existing[str(chunk_id)] = str(content_hash or "")
    return existing


def ensure_pgvector_schema() -> dict[str, Any]:
    status = backend_status()
    if not status["available"]:
        return {**status, "status": "skipped"}
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS ask_retrieval_chunks (
                      id BIGSERIAL PRIMARY KEY,
                      chunk_id TEXT NOT NULL UNIQUE,
                      source_type TEXT NOT NULL,
                      source_table TEXT,
                      source_record_id TEXT,
                      title TEXT NOT NULL,
                      text TEXT NOT NULL,
                      metadata_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                      content_hash TEXT NOT NULL,
                      embedding vector({_vector_dimension()}) NOT NULL,
                      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_ask_retrieval_chunks_source
                    ON ask_retrieval_chunks (source_type, source_table, source_record_id)
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_ask_retrieval_chunks_embedding
                    ON ask_retrieval_chunks
                    USING hnsw (embedding vector_cosine_ops)
                    WITH (m = 16, ef_construction = 64)
                    """
                )
        return {**status, "status": "completed"}
    except Exception as exc:
        log.warning("ask_pgvector.ensure_schema_failed error=%s", exc)
        return {
            **status,
            "available": False,
            "status": "skipped",
            "reason": f"schema_setup_failed:{exc}",
        }


def sync_retrieval_chunks(force: bool = False) -> dict[str, Any]:
    status = backend_status()
    if not status["available"]:
        _SYNC_STATE.update(
            {
                "status": "skipped",
                "reason": status["reason"],
                "chunk_count": 0,
            }
        )
        return sync_state()

    _CACHED_STATUSES = ("completed",)

    now = time.time()
    if (
        not force
        and _SYNC_STATE.get("status") in _CACHED_STATUSES
        and (now - float(_SYNC_STATE.get("synced_at") or 0.0)) < _SYNC_TTL_SECONDS
    ):
        return sync_state()

    with _SYNC_LOCK:
        now = time.time()
        if (
            not force
            and _SYNC_STATE.get("status") in _CACHED_STATUSES
            and (now - float(_SYNC_STATE.get("synced_at") or 0.0)) < _SYNC_TTL_SECONDS
        ):
            return sync_state()

        schema_status = ensure_pgvector_schema()
        if schema_status.get("status") != "completed":
            _SYNC_STATE.update(schema_status)
            return sync_state()

        try:
            from backend.services.retrieval_chunks import build_retrieval_chunks
            from backend.services.voyage_embeddings import embed_documents

            chunks = build_retrieval_chunks()
            if not chunks:
                _SYNC_STATE.update(
                    {
                        "status": "skipped",
                        "reason": "no_chunks_built",
                        "chunk_count": 0,
                        "synced_at": time.time(),
                    }
                )
                return sync_state()

            chunk_hashes = {
                str(chunk["chunk_id"]): _content_hash(chunk)
                for chunk in chunks
            }
            existing_hashes: dict[str, str] = {}
            with _connect() as conn:
                with conn.cursor() as cur:
                    existing_hashes = _fetch_existing_hashes(cur)

            stale_chunk_ids = sorted(set(existing_hashes) - set(chunk_hashes))
            changed_chunks = [
                chunk
                for chunk in chunks
                if force or existing_hashes.get(str(chunk["chunk_id"])) != chunk_hashes[str(chunk["chunk_id"])]
            ]

            embeddings: list[list[float]] = []
            if changed_chunks:
                embeddings = embed_documents([str(c.get("text") or "") for c in changed_chunks])
                if len(embeddings) != len(changed_chunks):
                    _SYNC_STATE.update(
                        {
                            "status": "skipped",
                            "reason": "embedding_count_mismatch",
                            "chunk_count": 0,
                            "synced_at": time.time(),
                        }
                    )
                    return sync_state()

            succeeded_count = 0
            failed_count = 0
            with _connect() as conn:
                with conn.cursor() as cur:
                    for chunk_id in stale_chunk_ids:
                        cur.execute(
                            "DELETE FROM ask_retrieval_chunks WHERE chunk_id = %s",
                            (chunk_id,),
                        )
                    for chunk, embedding in zip(changed_chunks, embeddings):
                        if not embedding:
                            failed_count += 1
                            continue
                        chunk_hash = chunk_hashes[str(chunk["chunk_id"])]
                        cur.execute(
                            """
                            INSERT INTO ask_retrieval_chunks (
                              chunk_id, source_type, source_table, source_record_id,
                              title, text, metadata_json, content_hash, embedding,
                              created_at, updated_at
                            ) VALUES (
                              %s, %s, %s, %s,
                              %s, %s, %s::jsonb, %s, %s::vector,
                              NOW(), NOW()
                            )
                            ON CONFLICT (chunk_id) DO UPDATE SET
                              source_type = EXCLUDED.source_type,
                              source_table = EXCLUDED.source_table,
                              source_record_id = EXCLUDED.source_record_id,
                              title = EXCLUDED.title,
                              text = EXCLUDED.text,
                              metadata_json = EXCLUDED.metadata_json,
                              content_hash = EXCLUDED.content_hash,
                              embedding = EXCLUDED.embedding,
                              updated_at = NOW()
                            """,
                            (
                                chunk["chunk_id"],
                                chunk["source_type"],
                                chunk.get("source_table"),
                                chunk.get("source_record_id"),
                                chunk.get("title") or chunk["chunk_id"],
                                chunk.get("text") or "",
                                json.dumps(chunk.get("metadata") or {}, ensure_ascii=True),
                                chunk_hash,
                                _vector_literal(embedding),
                            ),
                        )
                        succeeded_count += 1

            if failed_count == 0:
                sync_status = "completed"
                sync_reason = None
            elif succeeded_count == 0:
                sync_status = "failed"
                sync_reason = f"embedding_failed_count={failed_count}"
            else:
                sync_status = "completed_with_failures"
                sync_reason = f"embedding_failed_count={failed_count}"

            if sync_status == "failed":
                log.warning(
                    "ask_pgvector.sync_all_embeddings_failed changed=%d failed=%d",
                    len(changed_chunks), failed_count,
                )
            elif sync_status == "completed_with_failures":
                log.warning(
                    "ask_pgvector.sync_partial_failure succeeded=%d failed=%d",
                    succeeded_count, failed_count,
                )

            _SYNC_STATE.update(
                {
                    "status": sync_status,
                    "reason": sync_reason,
                    "synced_at": time.time(),
                    "chunk_count": len(chunks),
                    "updated_count": succeeded_count,
                    "succeeded_count": succeeded_count,
                    "failed_count": failed_count,
                    "deleted_count": len(stale_chunk_ids),
                }
            )
            return sync_state()
        except Exception as exc:
            log.warning("ask_pgvector.sync_failed error=%s", exc)
            _SYNC_STATE.update(
                {
                    "status": "skipped",
                    "reason": f"sync_failed:{exc}",
                    "synced_at": time.time(),
                    "chunk_count": 0,
                }
            )
            return sync_state()


def rebuild_hnsw_index() -> dict[str, Any]:
    """
    Drop and recreate the HNSW index on ask_retrieval_chunks.embedding.

    Call AFTER a bulk insert to avoid per-row index maintenance overhead.
    Safe to call on an already-indexed table.
    """
    status = backend_status()
    if not status["available"]:
        return {**status, "status": "skipped"}
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DROP INDEX IF EXISTS idx_ask_retrieval_chunks_embedding")
                cur.execute(
                    """
                    CREATE INDEX idx_ask_retrieval_chunks_embedding
                    ON ask_retrieval_chunks
                    USING hnsw (embedding vector_cosine_ops)
                    WITH (m = 16, ef_construction = 64)
                    """
                )
        log.info("ask_pgvector.rebuild_hnsw_index completed")
        return {**status, "status": "completed"}
    except Exception as exc:
        log.warning("ask_pgvector.rebuild_hnsw_index_failed error=%s", exc)
        return {**status, "status": "failed", "reason": str(exc)}


def dense_retrieve(query: str, top_k: int = 8) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    sync_result = sync_retrieval_chunks()
    if sync_result.get("status") not in ("completed", "completed_with_failures"):
        meta = dict(sync_result)
        meta.update({"status": "skipped", "retrieved_count": 0})
        return [], meta

    try:
        from backend.services.voyage_embeddings import embed_query

        query_vector = embed_query(query)
        if not query_vector:
            return [], {
                **sync_result,
                "status": "skipped",
                "reason": "query_embedding_unavailable",
                "retrieved_count": 0,
            }

        literal = _vector_literal(query_vector)
        rows_out: list[dict[str, Any]] = []
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                      chunk_id, source_type, source_table, source_record_id, title, text,
                      metadata_json,
                      1 - (embedding <=> %s::vector) AS similarity
                    FROM ask_retrieval_chunks
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (literal, literal, int(top_k)),
                )
                rows = cur.fetchall()

        for row in rows:
            metadata = row[6]
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except Exception:
                    metadata = {}
            rows_out.append(
                {
                    "id": row[0],
                    "chunk_id": row[0],
                    "source_type": row[1],
                    "source_table": row[2],
                    "source_record_id": row[3],
                    "title": row[4],
                    "text": row[5],
                    "metadata": metadata or {},
                    "similarity_score": float(row[7] or 0.0),
                }
            )

        return rows_out, {
            **sync_result,
            "status": "completed",
            "retrieved_count": len(rows_out),
            "reason": None,
        }
    except Exception as exc:
        log.warning("ask_pgvector.dense_retrieve_failed error=%s", exc)
        return [], {
            **sync_result,
            "status": "skipped",
            "reason": f"dense_query_failed:{exc}",
            "retrieved_count": 0,
        }
