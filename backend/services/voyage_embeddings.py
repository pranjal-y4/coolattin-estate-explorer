"""
backend/services/voyage_embeddings.py

Cohere Embed v3 dense embeddings client (replaces Voyage AI).

Model: embed-english-v3.0 (1024-dim; same column width as Voyage — no schema change).

CRITICAL — input_type asymmetry:
  corpus chunks → Cohere input_type "search_document"  (embed_documents / ingest path)
  query text    → Cohere input_type "search_query"     (embed_query / retrieval path)
  Passing the same type for both silently tanks retrieval quality.

Rate limits (trial key): 1,000 calls/month total; 5 calls/min on Embed endpoint.
Proactive inter-call sleep: 12 s (60 / 5). 429 backoff honours Retry-After as safety net.

Public API (identical signatures to the previous Voyage implementation):
  embed_texts(texts, input_type)  → list of float vectors
  embed_query(text)               → single float vector
  embed_documents(texts)          → list of float vectors
  cosine_similarity(a, b)         → float
  rrf_fuse(...)                   → fused list
  build_person_passport(...)      → str
  build_place_passport(...)       → str
  build_community_summary(...)    → str
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────


def _getenv(key: str, default: str = "") -> str:
    """Read from os.environ (already loaded by ask_service._load_local_env_files)."""
    return os.environ.get(key, default).strip()


def _get_embedding_provider() -> str:
    """Return the active embedding provider: 'local' | 'cohere' | 'voyage'."""
    raw = os.environ.get("EMBEDDING_PROVIDER", "local")
    p = (raw or "local").lower().strip()
    return p if p in ("local", "cohere", "voyage") else "local"


COHERE_API_KEY: str = ""
COHERE_MODEL: str = "embed-english-v3.0"
COHERE_OUTPUT_DIMENSION: int = 1024
COHERE_MAX_BATCH: int = 96       # Cohere Embed v3 per-request text limit
COHERE_CALL_INTERVAL: float = 12.0  # 60 s / 5 calls/min — proactive gap

# Keep VOYAGE_ aliases so ask_pgvector._vector_dimension() still resolves 1024
# without needing any changes to that module.
VOYAGE_API_KEY: str = ""
VOYAGE_MODEL: str = "voyage-3"
VOYAGE_OUTPUT_DIMENSION: int = COHERE_OUTPUT_DIMENSION

_init_lock = threading.Lock()
_key_loaded: bool = False
_client = None       # cohere.ClientV2 instance, populated by _init_key()
_voyage_client = None  # voyageai.Client instance, populated by _init_key()

# Maps the public-facing input_type names (kept compatible with Voyage API callers)
# to Cohere's required values.  Translation happens only inside _api_embed —
# all cache keys and caller contracts still use the Voyage-style names.
_COHERE_INPUT_TYPE: dict[str, str] = {
    "document": "search_document",
    "query": "search_query",
}


def _init_key() -> None:
    global COHERE_API_KEY, COHERE_MODEL, VOYAGE_API_KEY, _key_loaded, _client, _voyage_client
    if _key_loaded:
        return
    with _init_lock:
        if _key_loaded:
            return
        COHERE_API_KEY = _getenv("COHERE_API_KEY")
        model = _getenv("COHERE_MODEL")
        if model:
            COHERE_MODEL = model
        VOYAGE_API_KEY = _getenv("VOYAGE_API_KEY") or COHERE_API_KEY
        _key_loaded = True

        if COHERE_API_KEY:
            try:
                import cohere  # type: ignore
                _client = cohere.ClientV2(api_key=COHERE_API_KEY)
                log.info(
                    "voyage_embeddings.cohere_init model=%s dim=%d",
                    COHERE_MODEL,
                    COHERE_OUTPUT_DIMENSION,
                )
            except Exception as exc:
                log.warning("voyage_embeddings.cohere_init_failed error=%s", exc)
                _client = None

        if VOYAGE_API_KEY:
            try:
                import voyageai  # type: ignore
                _voyage_client = voyageai.Client(api_key=VOYAGE_API_KEY)
                log.info(
                    "voyage_embeddings.voyage_init model=%s dim=%d",
                    _getenv("VOYAGE_MODEL") or VOYAGE_MODEL,
                    VOYAGE_OUTPUT_DIMENSION,
                )
            except Exception as exc:
                log.warning("voyage_embeddings.voyage_init_failed error=%s", exc)
                _voyage_client = None


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
            evict = list(_DOC_CACHE.keys())[: _DOC_CACHE_MAX // 10]
            for k in evict:
                del _DOC_CACHE[k]
        _DOC_CACHE[text] = vec


# ── Rate limiting ─────────────────────────────────────────────────────────────
# Proactive: sleep to stay within 5 calls/min (12 s between calls).
# The 429 backoff in embed_texts is a separate safety net for burst scenarios.

_rate_lock = threading.Lock()
_last_call_at: float = 0.0


def _proactive_sleep() -> None:
    global _last_call_at
    with _rate_lock:
        elapsed = time.time() - _last_call_at
        gap = COHERE_CALL_INTERVAL - elapsed
        if gap > 0:
            time.sleep(gap)
        _last_call_at = time.time()


# ── API call counter ──────────────────────────────────────────────────────────

_api_call_count_lock = threading.Lock()
_api_call_count: int = 0


def get_api_call_count() -> int:
    with _api_call_count_lock:
        return _api_call_count


def reset_api_call_count() -> None:
    global _api_call_count
    with _api_call_count_lock:
        _api_call_count = 0


# ── Single-batch embedding call ───────────────────────────────────────────────


def _api_embed(texts: list[str], input_type: str) -> list[list[float]]:
    """
    One Cohere embed API call for a single pre-sized batch.

    input_type must be the Cohere native value: 'search_document' or 'search_query'.
    Increments the module-level API call counter.
    Raises on API error; the caller in embed_texts handles retries.

    Hard assertion: returned embedding dimension must equal COHERE_OUTPUT_DIMENSION (1024).
    """
    global _api_call_count
    _proactive_sleep()
    response = _client.embed(  # type: ignore[union-attr]
        model=COHERE_MODEL,
        texts=texts,
        input_type=input_type,
        embedding_types=["float"],
    )
    with _api_call_count_lock:
        _api_call_count += 1
    vecs: list[list[float]] = list(response.embeddings.float_ or [])
    if vecs and len(vecs[0]) != COHERE_OUTPUT_DIMENSION:
        raise RuntimeError(
            f"cohere_embed.dimension_mismatch expected={COHERE_OUTPUT_DIMENSION} "
            f"actual={len(vecs[0])} model={COHERE_MODEL}"
        )
    return vecs


# ── Core embedding call ───────────────────────────────────────────────────────


def embed_texts(
    texts: list[str],
    input_type: str = "document",
    *,
    use_cache: bool = True,
) -> list[list[float]]:
    """
    Embed a list of texts using the configured provider (EMBEDDING_PROVIDER env var).

    input_type: "query" | "document"  (Voyage-compatible names)
    provider=local  → BAAI/bge-large-en-v1.5 via SentenceTransformers (no API key)
    provider=cohere → Cohere Embed v3 (requires COHERE_API_KEY)
    provider=voyage → Voyage AI voyage-3 (requires VOYAGE_API_KEY)
    Returns empty list on API error.
    """
    provider = _get_embedding_provider()
    if provider == "local":
        from backend.services.local_embeddings import embed_texts_local
        return embed_texts_local(texts, input_type=input_type)

    if provider == "voyage":
        return _embed_voyage(texts, input_type, use_cache=use_cache)

    _init_key()
    if not COHERE_API_KEY or _client is None:
        log.debug("voyage_embeddings.no_key — dense embeddings disabled")
        return []
    if not texts:
        return []

    cohere_input_type = _COHERE_INPUT_TYPE.get(input_type, "search_document")

    results: list[list[float] | None] = [None] * len(texts)
    uncached_indices: list[int] = []

    # Fill from cache (documents only)
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
        fetched_vecs: list[list[float]] = []
        _RETRY_DELAYS = [2, 4, 8, 16, 32]

        for batch_start in range(0, len(uncached_texts), COHERE_MAX_BATCH):
            batch = uncached_texts[batch_start: batch_start + COHERE_MAX_BATCH]
            succeeded = False

            for attempt, retry_delay in enumerate([0] + _RETRY_DELAYS):
                if retry_delay:
                    time.sleep(retry_delay)
                try:
                    batch_vecs = _api_embed(batch, cohere_input_type)
                    fetched_vecs.extend(batch_vecs)
                    succeeded = True
                    break
                except Exception as exc:
                    exc_name = type(exc).__name__
                    is_rate_limit = "429" in str(exc) or "TooManyRequests" in exc_name
                    if is_rate_limit and attempt < len(_RETRY_DELAYS):
                        wait_secs = max(
                            retry_delay or COHERE_CALL_INTERVAL,
                            COHERE_CALL_INTERVAL,
                        )
                        try:
                            if hasattr(exc, "headers") and exc.headers:
                                ra = exc.headers.get("Retry-After")
                                if ra:
                                    wait_secs = int(ra)
                        except Exception:
                            pass
                        log.warning(
                            "voyage_embeddings.rate_limited attempt=%d wait=%ds",
                            attempt + 1,
                            wait_secs,
                        )
                        time.sleep(wait_secs)
                        continue
                    if attempt < len(_RETRY_DELAYS):
                        log.debug(
                            "voyage_embeddings.api_error_retrying attempt=%d error=%s",
                            attempt + 1,
                            exc,
                        )
                        continue
                    log.warning(
                        "voyage_embeddings.api_error model=%s batch_size=%d error=%s",
                        COHERE_MODEL,
                        len(batch),
                        exc,
                    )
                    fetched_vecs.extend([[] for _ in batch])
                    break

            if not succeeded and len(fetched_vecs) < batch_start + len(batch):
                fetched_vecs.extend(
                    [[] for _ in range(batch_start + len(batch) - len(fetched_vecs))]
                )

        for list_idx, orig_idx in enumerate(uncached_indices):
            vec = fetched_vecs[list_idx] if list_idx < len(fetched_vecs) else []
            results[orig_idx] = vec
            if use_cache and input_type == "document" and vec:
                _cache_set(texts[orig_idx], vec)

    return [v for v in results if v is not None]


def _embed_voyage(
    texts: list[str],
    input_type: str = "document",
    *,
    use_cache: bool = True,
) -> list[list[float]]:
    _init_key()
    if not VOYAGE_API_KEY or _voyage_client is None:
        log.debug("voyage_embeddings.voyage_no_key — dense embeddings disabled")
        return []
    if not texts:
        return []

    results: list[list[float] | None] = [None] * len(texts)
    uncached_indices: list[int] = []

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
        model = _getenv("VOYAGE_MODEL") or VOYAGE_MODEL
        uncached_texts = [texts[i] for i in uncached_indices]
        VOYAGE_MAX_BATCH = 128
        fetched_vecs: list[list[float]] = []
        for batch_start in range(0, len(uncached_texts), VOYAGE_MAX_BATCH):
            batch = uncached_texts[batch_start: batch_start + VOYAGE_MAX_BATCH]
            try:
                result = _voyage_client.embed(batch, model=model, input_type=input_type)
                batch_vecs = result.embeddings or []
                if batch_vecs and len(batch_vecs[0]) != VOYAGE_OUTPUT_DIMENSION:
                    log.warning(
                        "voyage_embeddings.voyage_dim_mismatch expected=%d actual=%d",
                        VOYAGE_OUTPUT_DIMENSION,
                        len(batch_vecs[0]),
                    )
                fetched_vecs.extend(batch_vecs)
            except Exception as exc:
                log.warning("voyage_embeddings.voyage_batch_failed error=%s", exc)
                fetched_vecs.extend([[] for _ in batch])

        for list_idx, orig_idx in enumerate(uncached_indices):
            vec = fetched_vecs[list_idx] if list_idx < len(fetched_vecs) else []
            results[orig_idx] = vec
            if use_cache and input_type == "document" and vec:
                _cache_set(texts[orig_idx], vec)

    return [v for v in results if v is not None]


def embed_query(text: str) -> list[float]:
    """Embed a single query string with input_type='search_query'. Returns [] on failure."""
    vecs = embed_texts([text], input_type="query", use_cache=False)
    return vecs[0] if vecs else []


def embed_documents(texts: list[str]) -> list[list[float]]:
    """Embed document chunks with input_type='search_document'. Results are cached."""
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
