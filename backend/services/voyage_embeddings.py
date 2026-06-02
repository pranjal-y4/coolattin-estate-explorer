"""
backend/services/voyage_embeddings.py

Part B — Voyage AI dense embeddings client.

Uses the Voyage REST API (no SDK dependency — just requests).
Model: voyage-3 (general-purpose; handles historical Irish-English text well).

Hybrid retrieval fuses Voyage dense scores with the existing TF-IDF sparse
scores via Reciprocal Rank Fusion (RRF).  The sparse hard filter
(required_keywords) is preserved as the precision floor; Voyage adds recall
for semantically similar but differently worded queries.

Public API
----------
embed_texts(texts, input_type)  → list of float vectors
embed_query(text)               → single float vector
embed_documents(texts)          → list of float vectors
rrf_fuse(dense_ranked, sparse_ranked, k=60) → fused list

The module degrades gracefully when VOYAGE_API_KEY is unset or the API
returns an error: it logs at WARNING and returns empty vectors.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

import requests

log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

def _getenv(key: str, default: str = "") -> str:
    """Read from os.environ (already loaded by ask_service._load_local_env_files)."""
    return os.environ.get(key, default).strip()

VOYAGE_API_KEY: str = ""  # populated on first use via _init_key()
VOYAGE_MODEL: str = "voyage-3"
VOYAGE_BASE_URL = "https://api.voyageai.com/v1"
VOYAGE_TIMEOUT = 30  # seconds
VOYAGE_MAX_BATCH = 128  # Voyage hard limit per request

_init_lock = threading.Lock()
_key_loaded = False


def _init_key() -> None:
    global VOYAGE_API_KEY, VOYAGE_MODEL, _key_loaded
    if _key_loaded:
        return
    with _init_lock:
        if _key_loaded:
            return
        VOYAGE_API_KEY = _getenv("VOYAGE_API_KEY")
        model = _getenv("VOYAGE_MODEL")
        if model:
            VOYAGE_MODEL = model
        _key_loaded = True


# ── In-process document cache ─────────────────────────────────────────────────
# text → vector; bounded to 4 096 entries to cap memory.

_doc_cache_lock = threading.Lock()
_DOC_CACHE: dict[str, list[float]] = {}
_DOC_CACHE_MAX = 4096


def _cache_get(text: str) -> list[float] | None:
    with _doc_cache_lock:
        return _DOC_CACHE.get(text)


def _cache_set(text: str, vec: list[float]) -> None:
    with _doc_cache_lock:
        if len(_DOC_CACHE) >= _DOC_CACHE_MAX:
            # Evict 10 % oldest entries (dict insertion order)
            evict = list(_DOC_CACHE.keys())[: _DOC_CACHE_MAX // 10]
            for k in evict:
                del _DOC_CACHE[k]
        _DOC_CACHE[text] = vec


# ── Core embedding call ───────────────────────────────────────────────────────

def embed_texts(
    texts: list[str],
    input_type: str = "document",
    *,
    use_cache: bool = True,
) -> list[list[float]]:
    """
    Embed a list of texts using the Voyage REST API.

    input_type: "query" | "document"
    Returns empty list on API error.
    """
    _init_key()
    if not VOYAGE_API_KEY:
        log.debug("voyage_embeddings.no_key — dense embeddings disabled")
        return []
    if not texts:
        return []

    results: list[list[float] | None] = [None] * len(texts)
    uncached_indices: list[int] = []

    # Fill from cache
    if use_cache and input_type == "document":
        for i, t in enumerate(texts):
            hit = _cache_get(t)
            if hit is not None:
                results[i] = hit
            else:
                uncached_indices.append(i)
    else:
        uncached_indices = list(range(len(texts)))

    if uncached_indices:
        uncached_texts = [texts[i] for i in uncached_indices]
        # Batch in chunks of VOYAGE_MAX_BATCH
        fetched_vecs: list[list[float]] = []
        for batch_start in range(0, len(uncached_texts), VOYAGE_MAX_BATCH):
            batch = uncached_texts[batch_start: batch_start + VOYAGE_MAX_BATCH]
            try:
                resp = requests.post(
                    f"{VOYAGE_BASE_URL}/embeddings",
                    headers={
                        "Authorization": f"Bearer {VOYAGE_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": VOYAGE_MODEL,
                        "input": batch,
                        "input_type": input_type,
                    },
                    timeout=VOYAGE_TIMEOUT,
                )
                resp.raise_for_status()
                data = resp.json()
                batch_vecs = [item["embedding"] for item in data["data"]]
                fetched_vecs.extend(batch_vecs)
            except Exception as exc:
                log.warning(
                    "voyage_embeddings.api_error model=%s batch_size=%d error=%s",
                    VOYAGE_MODEL, len(batch), exc,
                )
                fetched_vecs.extend([[] for _ in batch])

        for list_idx, orig_idx in enumerate(uncached_indices):
            vec = fetched_vecs[list_idx] if list_idx < len(fetched_vecs) else []
            results[orig_idx] = vec
            if use_cache and input_type == "document" and vec:
                _cache_set(texts[orig_idx], vec)

    return [v for v in results if v is not None]


def embed_query(text: str) -> list[float]:
    """Embed a single query string. Returns [] on failure."""
    vecs = embed_texts([text], input_type="query", use_cache=False)
    return vecs[0] if vecs else []


def embed_documents(texts: list[str]) -> list[list[float]]:
    """Embed document chunks. Results are cached."""
    return embed_texts(texts, input_type="document", use_cache=True)


# ── Cosine similarity ─────────────────────────────────────────────────────────

def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


# ── Reciprocal Rank Fusion ────────────────────────────────────────────────────

def rrf_fuse(
    dense_ranked: list[dict[str, Any]],
    sparse_ranked: list[dict[str, Any]],
    k: int = 60,
    dense_weight: float = 0.6,
    sparse_weight: float = 0.4,
) -> list[dict[str, Any]]:
    """
    Fuse two ranked lists using Reciprocal Rank Fusion.

    Each item dict must have a unique "id" key. Returns items sorted by
    descending RRF score with "rrf_score", "dense_rank", "sparse_rank" added.

    dense_weight + sparse_weight need not sum to 1.0; relative proportions
    determine the blend.
    """
    dense_rank: dict[str, int] = {item["id"]: i + 1 for i, item in enumerate(dense_ranked)}
    sparse_rank: dict[str, int] = {item["id"]: i + 1 for i, item in enumerate(sparse_ranked)}

    all_ids: set[str] = set(dense_rank) | set(sparse_rank)
    # Build a lookup for item metadata
    item_by_id: dict[str, dict[str, Any]] = {}
    for item in dense_ranked:
        item_by_id[item["id"]] = item
    for item in sparse_ranked:
        if item["id"] not in item_by_id:
            item_by_id[item["id"]] = item

    scores: list[tuple[float, str]] = []
    for id_ in all_ids:
        dr = dense_rank.get(id_, len(dense_ranked) + k)
        sr = sparse_rank.get(id_, len(sparse_ranked) + k)
        rrf = dense_weight / (k + dr) + sparse_weight / (k + sr)
        scores.append((rrf, id_))

    scores.sort(reverse=True)
    result: list[dict[str, Any]] = []
    for rrf_score, id_ in scores:
        item = dict(item_by_id[id_])
        item["rrf_score"] = round(rrf_score, 6)
        item["dense_rank"] = dense_rank.get(id_)
        item["sparse_rank"] = sparse_rank.get(id_)
        result.append(item)
    return result


# ── Chunk builders ────────────────────────────────────────────────────────────
# Produce verbalised text chunks for offline embedding at three granularities:
#   person_passports, place_passports, community_summaries.
# Called by the chunk refresh job (triggered on ingest cadence).

def build_person_passport(
    display_name: str,
    records: list[dict[str, Any]],
    disambiguation_note: str | None = None,
) -> str:
    """Verbalise a PersonCandidate into a chunk for embedding."""
    sources = list({r.get("source", "unknown") for r in records})
    years = sorted({r["year"] for r in records if r.get("year")})
    townlands = list({r.get("townland_norm") for r in records if r.get("townland_norm")})
    yr_str = f"{years[0]}–{years[-1]}" if len(years) > 1 else str(years[0]) if years else "unknown year"
    tl_str = ", ".join(t.title() for t in townlands[:3]) or "unknown townland"
    src_str = ", ".join(sources)
    note = f" {disambiguation_note}" if disambiguation_note else ""
    return (
        f"{display_name} appears in {len(records)} Coolattin estate record(s) "
        f"({src_str}) covering {yr_str}, associated with {tl_str}.{note}"
    )


def build_place_passport(
    name: str,
    civil_parish: str | None,
    barony: str | None,
    county: str | None,
    description: str | None,
    stats: dict[str, Any] | None,
) -> str:
    """Verbalise a townland entity into a chunk for embedding."""
    loc = ", ".join(filter(None, [civil_parish, barony, county]))
    stat_str = ""
    if stats:
        pop = stats.get("population_1851")
        evictions = stats.get("total_evictions")
        emigrants = stats.get("total_emigrants")
        parts: list[str] = []
        if pop:
            parts.append(f"population in 1851: {pop}")
        if evictions:
            parts.append(f"evictions 1847–1856: {evictions}")
        if emigrants:
            parts.append(f"emigrants: {emigrants}")
        stat_str = "; ".join(parts)
        if stat_str:
            stat_str = f" Statistics: {stat_str}."
    desc_str = f" {description}" if description else ""
    return (
        f"{name} is a townland in {loc} from the Coolattin Estate records."
        f"{desc_str}{stat_str}"
    )


def build_community_summary(
    parish: str,
    townland_count: int,
    total_emigrants: int,
    total_evictions: int,
    year_range: tuple[int, int] | None,
) -> str:
    """Verbalise a parish-level community summary for exploratory questions."""
    yr_str = f"{year_range[0]}–{year_range[1]}" if year_range else "the mid-19th century"
    return (
        f"{parish} parish contained {townland_count} townlands in the Coolattin estate. "
        f"During {yr_str}, {total_emigrants} people emigrated and "
        f"{total_evictions} evictions were recorded across this community."
    )
