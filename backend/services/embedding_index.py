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


# ── Part B: Voyage-powered hybrid chunk retrieval ────────────────────────────
# Separate from the template index above.  Retrieves over verbalised person /
# place / community chunks built by voyage_embeddings.build_*_passport().
#
# The sparse hard filter (required_keywords) is kept as a precision floor;
# Voyage dense scores add recall for variant phrasings.
#
# Chunk store: module-level list of {"id", "text", "embedding", "metadata"}.
# Rebuilt by refresh_chunk_index() which is called on the ingest cadence.

_chunk_lock = threading.Lock()
_CHUNKS: list[dict[str, Any]] = []
_CHUNKS_BUILT_AT: float = 0.0
_CHUNKS_TTL: float = 3600.0  # rebuild at most once per hour


def refresh_chunk_index(force: bool = False) -> int:
    """
    Build or refresh the Voyage chunk index from the live database.
    Returns the number of chunks indexed.
    Called lazily on first retrieve_chunks() call and forced on ingest.
    """
    global _CHUNKS_BUILT_AT

    now = time.time()
    with _chunk_lock:
        if not force and _CHUNKS and (now - _CHUNKS_BUILT_AT) < _CHUNKS_TTL:
            return len(_CHUNKS)

    try:
        from backend.services.voyage_embeddings import embed_documents
        from extensions import get_db_conn

        chunks: list[dict[str, Any]] = []
        conn = get_db_conn()
        try:
            # Place passports — one per townland
            tl_rows = conn.execute(
                """SELECT name, name_gaelic, civil_parish, barony, county,
                          description, id
                   FROM townland WHERE name IS NOT NULL LIMIT 500"""
            ).fetchall()
            for row in tl_rows:
                stats_row = conn.execute(
                    """SELECT SUM(cr.count) AS evictions,
                              (SELECT total FROM census_record
                               WHERE townland_id=? AND year=1851 LIMIT 1) AS pop_1851
                       FROM clearances_record cr WHERE cr.townland_id=?""",
                    (row["id"], row["id"]),
                ).fetchone()
                from backend.services.voyage_embeddings import build_place_passport
                stats = {}
                if stats_row:
                    if stats_row["pop_1851"]:
                        stats["population_1851"] = stats_row["pop_1851"]
                    if stats_row["evictions"]:
                        stats["total_evictions"] = stats_row["evictions"]
                text = build_place_passport(
                    name=row["name"],
                    civil_parish=row["civil_parish"],
                    barony=row["barony"],
                    county=row["county"],
                    description=row["description"],
                    stats=stats,
                )
                chunks.append({
                    "id": f"place:{row['name'].upper()}",
                    "text": text,
                    "metadata": {
                        "type": "place",
                        "name": row["name"],
                        "civil_parish": row["civil_parish"],
                    },
                })

            # Community summaries — one per civil_parish
            parish_rows = conn.execute(
                """SELECT t.civil_parish,
                          COUNT(DISTINCT t.id) AS tl_count,
                          SUM(cr.count) AS total_evictions,
                          MIN(cr.year) AS min_year, MAX(cr.year) AS max_year
                   FROM townland t
                   LEFT JOIN clearances_record cr ON cr.townland_id = t.id
                   WHERE t.civil_parish IS NOT NULL
                   GROUP BY t.civil_parish"""
            ).fetchall()
            for pr in parish_rows:
                emig_row = conn.execute(
                    """SELECT COUNT(DISTINCT u.record_id) AS n
                       FROM unified_record u
                       WHERE u.has_emigration_record=1 AND u.parish=?""",
                    (pr["civil_parish"],),
                ).fetchone()
                from backend.services.voyage_embeddings import build_community_summary
                yr = (pr["min_year"], pr["max_year"]) if pr["min_year"] else None
                text = build_community_summary(
                    parish=pr["civil_parish"],
                    townland_count=pr["tl_count"] or 0,
                    total_emigrants=(emig_row["n"] if emig_row else 0),
                    total_evictions=int(pr["total_evictions"] or 0),
                    year_range=yr,
                )
                chunks.append({
                    "id": f"community:{pr['civil_parish'].upper()}",
                    "text": text,
                    "metadata": {"type": "community", "civil_parish": pr["civil_parish"]},
                })
        finally:
            conn.close()

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


def retrieve_chunks(
    query: str,
    top_k: int = 8,
    required_keywords: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Hybrid chunk retrieval: Voyage dense + keyword sparse → RRF → top_k.

    required_keywords: if provided, chunks must contain all keywords (sparse
    hard filter; exact match against lowercase chunk text).
    Returns a list of chunk dicts with added "voyage_score" and "rrf_score".
    """
    try:
        from backend.services.voyage_embeddings import embed_query, rrf_fuse, cosine_similarity
    except Exception as exc:
        log.debug("embedding_index.retrieve_chunks: voyage unavailable error=%s", exc)
        return []

    # Lazy build
    with _chunk_lock:
        chunks = list(_CHUNKS)

    if not chunks:
        built = refresh_chunk_index()
        if not built:
            return []
        with _chunk_lock:
            chunks = list(_CHUNKS)

    if not chunks:
        return []

    q_vec = embed_query(query)
    if not q_vec:
        return []

    q_lower = query.lower()

    # Dense ranking
    dense_scored: list[tuple[float, dict]] = []
    for chunk in chunks:
        vec = chunk.get("embedding") or []
        if not vec:
            continue
        score = cosine_similarity(q_vec, vec)
        dense_scored.append((score, chunk))
    dense_scored.sort(key=lambda x: x[0], reverse=True)
    dense_ranked = [
        {"id": c["id"], **c, "voyage_score": s}
        for s, c in dense_scored
    ]

    # Sparse ranking: keyword count over chunk text
    sparse_scored: list[tuple[float, dict]] = []
    for chunk in chunks:
        text_lower = chunk["text"].lower()
        if required_keywords and not all(kw in text_lower for kw in required_keywords):
            continue
        kw_score = sum(1 for w in q_lower.split() if len(w) > 3 and w in text_lower)
        sparse_scored.append((float(kw_score), chunk))
    sparse_scored.sort(key=lambda x: x[0], reverse=True)
    sparse_ranked = [{"id": c["id"], **c, "kw_score": s} for s, c in sparse_scored]

    if not sparse_ranked and required_keywords:
        return []

    fused = rrf_fuse(dense_ranked, sparse_ranked)
    return fused[:top_k]
