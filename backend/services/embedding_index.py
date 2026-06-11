"""
backend/services/embedding_index.py

Phase 4 — Hybrid semantic retrieval over templates, metrics, and approved memory.

Embeds canonical questions once using word unigram + bigram TF-IDF. At query time:
  1. Dense retrieval: cosine similarity, top-50 candidates
  2. Sparse signal: keyword overlap score; required_keywords are a HARD pre-filter
     for template/metric hits (discard if any keyword absent)
  3. Reciprocal rank fusion (RRF) combines dense + sparse ranked lists
  4. High-confidence template hit → caller can short-circuit routing (fast lane)
  5. Memory hits surface as embedding-ranked few-shot examples for Phase 7

Embeddings are cached in-process. The static tier (templates + metrics) is built
once per process; the memory tier rebuilds every 60 s when approved rows change.
No external dependency beyond the Python standard library.
"""
from __future__ import annotations

import logging
import math
import re
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# Minimum cosine score for a template hit to enter the fast lane.
# Calibrated so a thematically correct but slightly differently worded question
# still wins, while cross-category noise does not.
TEMPLATE_FAST_LANE_THRESHOLD: float = 0.68

# Memory rows must meet this cosine score to be surfaced for direct reuse
# (used by the caller alongside the existing token-sort-ratio check).
MEMORY_COSINE_THRESHOLD: float = 0.55

# RRF smoothing constant — higher values reduce the influence of rank position.
_RRF_K: int = 60

# Memory tier TTL in seconds.
_MEMORY_TTL: float = 60.0


@dataclass
class IndexHit:
    id: str                                    # "template:<id>" | "metric:<key>" | "memory:<id>"
    source: str                                # "template" | "metric" | "memory"
    key: str                                   # template id / metric key / memory row id
    text: str                                  # canonical text that was embedded
    cosine_score: float                        # raw cosine similarity (0–1)
    rrf_score: float                           # reciprocal rank fusion score
    payload: dict[str, Any]                    # original template/metric/memory row
    required_keywords: list[str] = field(default_factory=list)
    optional_keywords: list[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# TF-IDF vectorizer (word unigrams + bigrams, no external dependency)
# ─────────────────────────────────────────────────────────────────────────────

def _tokenize(text: str) -> list[str]:
    toks = re.findall(r"[a-z0-9]+", text.lower())
    bigrams = [f"{toks[i]}_{toks[i + 1]}" for i in range(len(toks) - 1)]
    return toks + bigrams


class _TFIDFVectorizer:
    """
    Sublinear TF × smooth IDF; L2-normalised document vectors.
    Query encoding uses raw TF (no IDF re-application) so dot product == cosine.
    """

    def __init__(self) -> None:
        self._vocab: dict[str, int] = {}
        self._idf: list[float] = []

    def fit(self, corpus: list[str]) -> None:
        N = len(corpus)
        if N == 0:
            return
        tokenized = [_tokenize(doc) for doc in corpus]
        df: Counter[str] = Counter()
        for toks in tokenized:
            df.update(set(toks))
        terms = sorted(df)
        self._vocab = {t: i for i, t in enumerate(terms)}
        self._idf = [math.log((1 + N) / (1 + df[t])) + 1.0 for t in terms]

    def transform(self, docs: list[str]) -> list[list[float]]:
        V = len(self._vocab)
        out: list[list[float]] = []
        for doc in docs:
            tf = Counter(_tokenize(doc))
            vec = [0.0] * V
            for term, cnt in tf.items():
                if term in self._vocab:
                    idx = self._vocab[term]
                    vec[idx] = (1.0 + math.log(cnt)) * self._idf[idx]
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            out.append([x / norm for x in vec])
        return out

    def encode_query(self, text: str) -> list[float]:
        V = len(self._vocab)
        tf = Counter(_tokenize(text))
        vec = [0.0] * V
        for term, cnt in tf.items():
            if term in self._vocab:
                vec[self._vocab[term]] = 1.0 + math.log(cnt)
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


# ─────────────────────────────────────────────────────────────────────────────
# EmbeddingIndex
# ─────────────────────────────────────────────────────────────────────────────

class EmbeddingIndex:
    """
    Thread-safe hybrid retrieval index.

    Built lazily on first call; the static tier (templates + metrics) is
    permanent for the process lifetime; the memory tier rebuilds every 60 s.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._vec = _TFIDFVectorizer()
        self._matrix: list[list[float]] = []
        self._items: list[IndexHit] = []
        self._memory_expires_at: float = 0.0

    # ── Index construction ────────────────────────────────────────────────────

    def _build(
        self,
        templates: list[dict[str, Any]],
        metrics: dict[str, dict[str, Any]],
        memory_rows: list[dict[str, Any]],
    ) -> None:
        texts: list[str] = []
        items: list[IndexHit] = []

        for tmpl in templates:
            tid = str(tmpl.get("id") or "")
            desc = str(tmpl.get("description") or tid)
            if not desc:
                continue
            texts.append(desc)
            items.append(IndexHit(
                id=f"template:{tid}",
                source="template",
                key=tid,
                text=desc,
                cosine_score=0.0,
                rrf_score=0.0,
                payload=dict(tmpl),
                required_keywords=list(tmpl.get("required_keywords") or []),
                optional_keywords=list(tmpl.get("optional_keywords") or []),
            ))

        for mk, mdef in metrics.items():
            label = str(mdef.get("label") or mk)
            kws = list(mdef.get("keywords") or [])
            canonical = f"{label} {' '.join(kws)}".strip()
            texts.append(canonical)
            items.append(IndexHit(
                id=f"metric:{mk}",
                source="metric",
                key=mk,
                text=canonical,
                cosine_score=0.0,
                rrf_score=0.0,
                payload=dict(mdef),
                required_keywords=kws,
                optional_keywords=[],
            ))

        for row in memory_rows:
            mid = str(row.get("id") or "")
            qt = str(row.get("question_text") or "")
            if not qt:
                continue
            texts.append(qt)
            items.append(IndexHit(
                id=f"memory:{mid}",
                source="memory",
                key=mid,
                text=qt,
                cosine_score=0.0,
                rrf_score=0.0,
                payload=dict(row),
            ))

        vec = _TFIDFVectorizer()
        vec.fit(texts)
        matrix = vec.transform(texts)

        self._vec = vec
        self._matrix = matrix
        self._items = items
        log.debug(
            "embedding_index.built templates=%d metrics=%d memory=%d vocab=%d",
            sum(1 for h in items if h.source == "template"),
            sum(1 for h in items if h.source == "metric"),
            sum(1 for h in items if h.source == "memory"),
            len(vec._vocab),
        )

    def _ensure(
        self,
        templates: list[dict[str, Any]],
        metrics: dict[str, dict[str, Any]],
        memory_rows: list[dict[str, Any]],
    ) -> None:
        now = time.time()
        if self._items and now < self._memory_expires_at:
            return
        with self._lock:
            if self._items and now < self._memory_expires_at:
                return
            self._build(templates, metrics, memory_rows)
            self._memory_expires_at = now + _MEMORY_TTL

    # ── Retrieval primitives ──────────────────────────────────────────────────

    def _dense_retrieve(self, question: str, k: int = 50) -> list[tuple[float, IndexHit]]:
        if not self._items:
            return []
        q = self._vec.encode_query(question)
        scored = [(_dot(q, self._matrix[i]), self._items[i]) for i in range(len(self._items))]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [(s, h) for s, h in scored[:k] if s > 0.0]

    def _sparse_score(self, question: str, hit: IndexHit) -> float:
        """
        Keyword overlap score used as the sparse signal for RRF.

        required_keywords act as a HARD gate — returns 0.0 if any is absent.
        optional_keywords contribute to the rank score.
        Memory rows have no required_keywords so always pass through.
        """
        q = question.lower()
        if hit.required_keywords and not all(kw in q for kw in hit.required_keywords):
            return 0.0
        n_req = len(hit.required_keywords)
        n_opt = sum(1 for kw in hit.optional_keywords if kw in q)
        return float(n_req * 2 + n_opt)

    # ── Reciprocal rank fusion ────────────────────────────────────────────────

    @staticmethod
    def _rrf(
        dense: list[tuple[float, IndexHit]],
        sparse: list[tuple[float, IndexHit]],
        k: int = _RRF_K,
    ) -> list[tuple[str, float, float]]:
        """
        Fuse two ranked lists; returns sorted (hit_id, rrf_score, cosine_score).
        """
        rrf: dict[str, float] = {}
        cosine_map: dict[str, float] = {}
        for rank, (cos, hit) in enumerate(dense):
            rrf[hit.id] = rrf.get(hit.id, 0.0) + 1.0 / (k + rank + 1)
            cosine_map[hit.id] = cos
        for rank, (_, hit) in enumerate(sparse):
            rrf[hit.id] = rrf.get(hit.id, 0.0) + 1.0 / (k + rank + 1)
        return sorted(
            [(hid, sc, cosine_map.get(hid, 0.0)) for hid, sc in rrf.items()],
            key=lambda x: x[1],
            reverse=True,
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def retrieve(
        self,
        question: str,
        top_k: int = 10,
        templates: list[dict[str, Any]] | None = None,
        metrics: dict[str, dict[str, Any]] | None = None,
        memory_rows: list[dict[str, Any]] | None = None,
    ) -> list[IndexHit]:
        """
        Hybrid retrieval: dense cosine + sparse keyword overlap → RRF → top_k.

        Pass templates/metrics/memory_rows so the index can detect staleness
        and rebuild when needed. All three default to [] / {} if not supplied.
        """
        self._ensure(
            templates or [],
            metrics or {},
            memory_rows or [],
        )
        if not self._items:
            return []

        id_to_item: dict[str, IndexHit] = {h.id: h for h in self._items}
        dense = self._dense_retrieve(question, k=50)

        # Sparse: score dense hits by keyword overlap (HARD filter applies here)
        sparse: list[tuple[float, IndexHit]] = []
        for cos, hit in dense:
            sp = self._sparse_score(question, hit)
            # Memory rows always pass through (they have no required_keywords)
            if sp > 0.0 or hit.source == "memory":
                sparse.append((sp, hit))
        sparse.sort(key=lambda x: x[0], reverse=True)

        fused = self._rrf(dense, sparse)

        result: list[IndexHit] = []
        for hit_id, rrf_score, cos_score in fused[:top_k]:
            base = id_to_item.get(hit_id)
            if base is None:
                continue
            result.append(IndexHit(
                id=base.id,
                source=base.source,
                key=base.key,
                text=base.text,
                cosine_score=round(cos_score, 4),
                rrf_score=round(rrf_score, 6),
                payload=base.payload,
                required_keywords=base.required_keywords,
                optional_keywords=base.optional_keywords,
            ))
        return result

    def invalidate_memory(self) -> None:
        """Force memory tier rebuild on next retrieve() call."""
        self._memory_expires_at = 0.0


# Module-level singleton — shared across all requests in the same worker.
_INDEX = EmbeddingIndex()


def get_index() -> EmbeddingIndex:
    return _INDEX


# ── Part B: Ask-page chunk retrieval ─────────────────────────────────────────
# Separate from the template index above.
#
# Dense retrieval prefers the persistent pgvector store when configured.
# If pgvector/PostgreSQL is unavailable, the code falls back to the existing
# in-memory Voyage cosine search.
#
# Sparse retrieval is explicit keyword-overlap scoring over chunk text. This is
# intentionally described as keyword overlap, not TF-IDF.
#
# Chunk store: module-level list of {"id", "text", "embedding", "metadata"}.
# The text/metadata corpus is shared by the pgvector and fallback dense paths.

_chunk_lock = threading.Lock()
_CHUNKS: list[dict[str, Any]] = []
_CHUNKS_BUILT_AT: float = 0.0
_CHUNKS_TTL: float = 3600.0  # rebuild at most once per hour


def _ensure_chunk_corpus(force: bool = False) -> list[dict[str, Any]]:
    global _CHUNKS_BUILT_AT

    now = time.time()
    with _chunk_lock:
        if not force and _CHUNKS and (now - _CHUNKS_BUILT_AT) < _CHUNKS_TTL:
            return list(_CHUNKS)

    try:
        from backend.services.retrieval_chunks import build_retrieval_chunks

        chunks = build_retrieval_chunks()
        with _chunk_lock:
            _CHUNKS.clear()
            _CHUNKS.extend(chunks)
            _CHUNKS_BUILT_AT = time.time()
            return list(_CHUNKS)
    except Exception as exc:
        log.warning("embedding_index.chunk_corpus_failed error=%s", exc)
        return []


def refresh_chunk_index(force: bool = False) -> int:
    """
    Build or refresh the in-memory Voyage chunk index from the live database.
    Returns the number of chunks indexed.
    Called lazily on fallback retrieval and may also be forced in tests.
    """
    try:
        from backend.services.voyage_embeddings import embed_documents
        chunks = _ensure_chunk_corpus(force=force)

        if not chunks:
            log.warning("embedding_index.chunk_refresh: no chunks built")
            return 0

        texts = [c["text"] for c in chunks]
        embeddings = embed_documents(texts)

        if len(embeddings) == len(chunks):
            for c, vec in zip(chunks, embeddings):
                c["embedding"] = vec
        else:
            log.warning(
                "embedding_index.chunk_refresh: embedding count mismatch chunks=%d embeddings=%d",
                len(chunks), len(embeddings),
            )
            for c in chunks:
                c["embedding"] = []

        with _chunk_lock:
            _CHUNKS.clear()
            _CHUNKS.extend(chunks)
            _CHUNKS_BUILT_AT = time.time()

        log.info("embedding_index.chunk_refresh: indexed %d chunks", len(chunks))
        return len(chunks)

    except Exception as exc:
        log.warning("embedding_index.chunk_refresh_failed error=%s", exc)
        return 0


def retrieve_chunks_with_meta(
    query: str,
    top_k: int = 8,
    required_keywords: list[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Hybrid chunk retrieval: pgvector/Voyage dense + keyword sparse → RRF → top_k.

    required_keywords: if provided, chunks must contain all keywords (sparse
    hard filter; exact match against lowercase chunk text).
    Returns (chunks, meta).
    """
    meta: dict[str, Any] = {
        "pgvector_status": "skipped",
        "pgvector_reason": "not_started",
        "dense_backend": "pgvector",
        "dense_status": "skipped",
        "dense_reason": "not_started",
        "dense_count": 0,
        "sparse_backend": "keyword_overlap",
        "sparse_status": "completed",
        "sparse_count": 0,
        "fusion_backend": "rrf",
        "fusion_status": "completed",
        "fused_count": 0,
    }

    corpus = _ensure_chunk_corpus()
    if not corpus:
        meta["dense_reason"] = "no_chunk_corpus"
        meta["sparse_status"] = "skipped"
        meta["fusion_status"] = "skipped"
        return [], meta

    dense_ranked: list[dict[str, Any]] = []
    dense_meta: dict[str, Any] = {}
    try:
        from backend.services.ask_pgvector import dense_retrieve as _dense_retrieve_pg

        dense_ranked, dense_meta = _dense_retrieve_pg(query, top_k=max(top_k * 4, 24))
        meta["pgvector_status"] = dense_meta.get("status", "skipped")
        meta["pgvector_reason"] = dense_meta.get("reason")
        if dense_meta.get("status") in ("completed", "completed_with_failures"):
            dense_ranked = [
                {**row, "voyage_score": row.get("similarity_score", 0.0)}
                for row in dense_ranked
            ]
            meta["dense_backend"] = "pgvector"
            meta["dense_status"] = dense_meta.get("status")
            meta["dense_reason"] = dense_meta.get("reason")
            meta["dense_count"] = len(dense_ranked)
        else:
            meta["dense_backend"] = "pgvector"
            meta["dense_status"] = "skipped"
            meta["dense_reason"] = dense_meta.get("reason")
    except Exception as exc:
        log.debug("embedding_index.retrieve_chunks pgvector_unavailable error=%s", exc)
        meta["pgvector_status"] = "skipped"
        meta["pgvector_reason"] = f"pgvector_exception:{exc}"
        meta["dense_backend"] = "pgvector"
        meta["dense_status"] = "skipped"
        meta["dense_reason"] = f"pgvector_exception:{exc}"

    if not dense_ranked:
        if meta.get("pgvector_status") != "completed":
            log.info(
                "embedding_index.retrieve_chunks pgvector_skipped reason=%s",
                meta.get("pgvector_reason") or meta.get("dense_reason"),
            )
        try:
            from backend.services.voyage_embeddings import (
                cosine_similarity,
                embed_query,
            )
        except Exception as exc:
            log.debug("embedding_index.retrieve_chunks: voyage unavailable error=%s", exc)
            meta["dense_backend"] = "voyage_in_memory"
            meta["dense_status"] = "skipped"
            meta["dense_reason"] = f"voyage_import_failed:{exc}"
            meta["sparse_status"] = "completed"
        else:
            with _chunk_lock:
                chunks = list(_CHUNKS)
            if not any((chunk.get("embedding") or []) for chunk in chunks):
                built = refresh_chunk_index()
                if built:
                    with _chunk_lock:
                        chunks = list(_CHUNKS)
            q_vec = embed_query(query)
            if q_vec:
                dense_scored: list[tuple[float, dict[str, Any]]] = []
                for chunk in chunks:
                    vec = chunk.get("embedding") or []
                    if not vec:
                        continue
                    score = cosine_similarity(q_vec, vec)
                    dense_scored.append((score, chunk))
                dense_scored.sort(key=lambda x: x[0], reverse=True)
                dense_ranked = [
                    {"id": c["id"], **c, "voyage_score": s}
                    for s, c in dense_scored[: max(top_k * 4, 24)]
                ]
                meta["dense_backend"] = "voyage_in_memory"
                meta["dense_status"] = "fallback"
                meta["dense_reason"] = meta.get("pgvector_reason") or "pgvector_unavailable"
                meta["dense_count"] = len(dense_ranked)
            else:
                meta["dense_backend"] = "voyage_in_memory"
                meta["dense_status"] = "skipped"
                meta["dense_reason"] = "query_embedding_unavailable"

    q_lower = query.lower()
    sparse_scored: list[tuple[float, dict[str, Any]]] = []
    for chunk in corpus:
        text_lower = str(chunk.get("text") or "").lower()
        if required_keywords and not all(kw in text_lower for kw in required_keywords):
            continue
        kw_score = sum(1 for w in q_lower.split() if len(w) > 3 and w in text_lower)
        sparse_scored.append((float(kw_score), chunk))
    sparse_scored.sort(key=lambda x: x[0], reverse=True)
    sparse_ranked = [{"id": c["id"], **c, "kw_score": s} for s, c in sparse_scored]
    meta["sparse_count"] = len(sparse_ranked)

    if not sparse_ranked and required_keywords:
        meta["fusion_status"] = "skipped"
        return [], meta

    try:
        from backend.services.voyage_embeddings import rrf_fuse
    except Exception as exc:
        log.debug("embedding_index.retrieve_chunks: rrf unavailable error=%s", exc)
        meta["fusion_status"] = "skipped"
        meta["fusion_reason"] = f"rrf_import_failed:{exc}"
        return dense_ranked[:top_k], meta

    fused = rrf_fuse(dense_ranked, sparse_ranked)
    meta["fused_count"] = len(fused[:top_k])
    return fused[:top_k], meta


def retrieve_chunks(
    query: str,
    top_k: int = 8,
    required_keywords: list[str] | None = None,
) -> list[dict[str, Any]]:
    chunks, _ = retrieve_chunks_with_meta(
        query=query,
        top_k=top_k,
        required_keywords=required_keywords,
    )
    return chunks
