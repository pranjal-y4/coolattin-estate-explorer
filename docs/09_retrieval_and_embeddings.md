# 09 — Retrieval and Embeddings Subsystem

Technical reference for every file that builds, embeds, indexes, or retrieves text for the
Ask pipeline. This is ground truth read directly from source on 2026-07-27/28, not a
restatement of `CLAUDE.md` — every discrepancy found between `CLAUDE.md`'s summary and the
actual code is called out explicitly below.

Files covered:

| File | Role |
|---|---|
| `backend/services/embedding_index.py` | Two independent retrieval systems in one module: (A) a hand-rolled TF-IDF hybrid index over SQL templates/metrics/memory rows; (B) Ask-page chunk retrieval (pgvector-first, in-memory-fallback) |
| `backend/services/local_embeddings.py` | Local dense embedding provider — BAAI/bge-large-en-v1.5 via SentenceTransformers |
| `backend/services/voyage_embeddings.py` | Dense embedding client — **actually calls Cohere Embed v3 by default**, despite the filename (see §3) |
| `backend/services/ask_pgvector.py` | Optional persistent Postgres/pgvector retrieval backend |
| `backend/services/retrieval_chunks.py` | Builds the shared chunk corpus (person/place/census/workhouse/summary) consumed by both the pgvector and in-memory retrieval paths |
| `scripts/cohere_sample_validate.py`, `scripts/validate_ann_scale.py` | One-off validation scripts for the Cohere swap and HNSW recall/latency |

Two important scoping notes established while reading the code:

1. **`embedding_index.py`'s two halves are unrelated retrieval systems that happen to share a
   file.** "Part A" (`EmbeddingIndex` class, lines 1–348) is the Phase 4 template/metric/memory
   fast-lane index described in `CLAUDE.md`. "Part B" (`retrieve_chunks_with_meta` etc., lines
   350–583) is the Ask-page chunk retrieval used for LLM context (pgvector/Voyage). They use
   different vectorizers, different RRF implementations, and different fusion constants — see
   §1 and §6.
2. **Part A (the template fast lane, `TEMPLATE_FAST_LANE_THRESHOLD`) is legacy-pipeline-only.**
   `_phase4_retrieve()` in `backend/services/ask_service.py` is called at line 3710, *after* the
   `ASK_USE_NEW_PIPELINE` branch at line 3665 has already returned for the default pipeline. So
   in the current default configuration (`ASK_USE_NEW_PIPELINE=true`), Part A never runs. This
   matches `CLAUDE.md`'s "Not active in the default pipeline: ... embedding index (Phase 4)"
   statement — confirmed correct against the code.

---

## 1. `embedding_index.py` — Part A: hybrid template/metric/memory index

### 1.1 Purpose and scope

Legacy-pipeline-only (see scoping note above). Retrieves over three item types built into one
in-memory index:

- **templates** — the ~81 entries of `QUESTION_TEMPLATES` in `ask_service.py` (see §7)
- **metrics** — `semantic_layer.METRIC_REGISTRY`
- **memory** — approved (thumbs-up) rows from `ask_query_memory`

### 1.2 TF-IDF vectorizer — hand-rolled, no external library

`_TFIDFVectorizer` (lines 70–114) is pure Python/stdlib — **not** scikit-learn, not gensim, not
any external package. Confirmed: `embedding_index.py` has zero imports beyond `logging, math,
re, threading, time, collections, dataclasses, typing`.

**Tokenizer** (`_tokenize`, lines 64–67):
```python
toks = re.findall(r"[a-z0-9]+", text.lower())
bigrams = [f"{toks[i]}_{toks[i + 1]}" for i in range(len(toks) - 1)]
return toks + bigrams
```
Word **unigrams + bigrams** (adjacent-pair, underscore-joined), lowercased, alphanumeric-only
regex split. No stopword removal, no stemming.

**Document vectors** — sublinear TF × smooth IDF, L2-normalised:
- `tf = 1 + log(count)` (sublinear term frequency scaling)
- `idf = log((1 + N) / (1 + df[term])) + 1.0` (smoothed IDF, `+1` floor prevents zero weight)
- vector = `tf * idf` per term, then divided by its L2 norm

**Query vectors** (`encode_query`) — **raw TF only, no IDF re-application**:
```python
vec[idx] = 1.0 + math.log(cnt)   # no × idf
```
The docstring explains why: *"so dot product == cosine"* — because document vectors are
pre-multiplied by IDF and L2-normalised, applying IDF a second time on the query side would
double-count it. Cosine similarity is then just `_dot(query_vec, doc_vec)` since both sides are
unit-norm (query vectors are also L2-normalised at the end of `encode_query`).

### 1.3 Fast-lane threshold — verified against CLAUDE.md

```python
TEMPLATE_FAST_LANE_THRESHOLD: float = 0.68
MEMORY_COSINE_THRESHOLD: float = 0.55
```
`CLAUDE.md`'s claim of "cosine ≥ 0.68" for the template fast lane is **confirmed correct**
against `embedding_index.py:34`, and "token-sort-ratio + cosine ≥ 0.55" for memory reuse is
confirmed against `MEMORY_COSINE_THRESHOLD` at line 38 (the token-sort-ratio half of that check
lives in `ask_service.py`, not this file).

### 1.4 Reciprocal Rank Fusion (Part A) — exact formula

```python
_RRF_K: int = 60

rrf[hit.id] = rrf.get(hit.id, 0.0) + 1.0 / (k + rank + 1)   # for each of dense and sparse lists
```
This is the **textbook RRF formula** (`1 / (k + rank)`, using a 1-indexed rank via `rank + 1`
since Python's `enumerate` is 0-indexed) with `k = 60`, applied identically to the dense-ranked
list and the sparse-ranked list, then summed per item ID. No weighting term (compare to Part B's
weighted variant in §6, which has `dense_weight=0.6` / `sparse_weight=0.4`) — Part A's fusion is
unweighted, each list contributes equally.

Sort is descending by summed RRF score; cosine score is carried through separately (from the
dense list only) for the `TEMPLATE_FAST_LANE_THRESHOLD` gate, which checks **cosine**, not RRF
score.

### 1.5 Sparse signal — keyword overlap, hard + soft components

`_sparse_score()` (lines 242–255):
- `required_keywords` — **hard gate**: if any required keyword is absent from the lowercased
  question, sparse score is `0.0` for that hit (contributes nothing to the sparse ranking, but
  the item can still surface via the dense list alone).
- `optional_keywords` — soft signal: `score = n_required * 2 + n_optional_matched`.
- Memory rows have no `required_keywords`, so they always pass through to the sparse list
  (line 313: `if sp > 0.0 or hit.source == "memory"`).

### 1.6 Retrieval pipeline (`retrieve()`, lines 283–335)

1. `_ensure()` — rebuild check: static tier (templates+metrics) permanent once built; **memory
   tier TTL = 60 s** (`_MEMORY_TTL`), rebuilt on next `retrieve()` call after expiry, or
   immediately via `invalidate_memory()`.
2. `_dense_retrieve(question, k=50)` — cosine-score every indexed item, keep top 50 with
   score > 0.
3. Sparse-score each of those 50 dense hits (not the full corpus — sparse scoring only runs
   over the dense candidate set).
4. `_rrf(dense, sparse)` fuses the two ranked lists.
5. Return top `top_k` (caller in `ask_service._phase4_retrieve` passes `top_k=12`), each result
   re-wrapped as a fresh `IndexHit` with rounded `cosine_score` (4dp) and `rrf_score` (6dp).

### 1.7 Fast-lane hit vs. miss — what actually happens (traced in `ask_service.py`)

In `_phase4_retrieve()` (`ask_service.py` lines 2077–2161), after calling `get_index().retrieve()`:

**On a template/metric hit** (loop at line 2116, first qualifying hit wins):
```python
if hit.source not in ("template", "metric"): continue
if hit.cosine_score < TEMPLATE_FAST_LANE_THRESHOLD: continue
if hit.required_keywords and not all(kw in q_lower for kw in hit.required_keywords): continue
if hit.source == "template":
    tmpl, tmpl_sql = _match_and_build_template_by_id(hit.key, question, canonical_townland)
    ...
    template_fast_lane = {"template": tmpl, "sql": tmpl_sql, "template_id": hit.key,
                           "cosine_score": ..., "rrf_score": ..., "description": ...}
    break
```
`_match_and_build_template_by_id` re-looks-up the template by its exact `id` in
`QUESTION_TEMPLATES`, re-checks its `requires_townland`/`requires_year`/`requires_surname`
guards against currently-resolved entities, and substitutes `{townland_norm}` / `{year}` /
`{surname}` placeholders into `sql_template`. If that guard check fails (e.g. entity not yet
resolved), the hit is silently skipped and the loop tries the next candidate — a cosine-passing
hit is not guaranteed to become the fast lane.

**On a miss** (no hit clears both the cosine threshold and the keyword hard-gate, or none built
valid SQL): `template_fast_lane` stays `None`. The caller falls through the legacy pipeline's
next fast lanes (verified analysis, then direct memory reuse) and ultimately to
`intent_router.classify_intent()` if nothing fires.

**Memory re-ranking** always runs regardless of the fast-lane outcome: every hit with
`source == "memory"` re-ranks the caller-supplied `approved_memory` list by `rrf_score`, setting
`match_score = round(cosine_score * 100.0, 2)` on each row for downstream few-shot injection
into Phase 7 synthesis prompts. Memory rows that scored no hit in the index are appended
unranked at the end (`ranked + unranked`).

### 1.8 Failure mode

The entire `_phase4_retrieve()` call is wrapped in `try/except Exception` in `ask_service.py`;
on any exception it logs at `debug` level and returns `(None, approved_memory)` unchanged — i.e.
the whole Phase 4 fast lane degrades to a no-op rather than raising.

---

## 2. `embedding_index.py` — Part B: Ask-page chunk retrieval

This is a **separate system**, explicitly marked as such in the source (`"Separate from the
template index above"`, line 351). It retrieves `retrieval_chunks.py`-built chunks (person
passports, place passports, census rows, unified-record rows, workhouse rows, source-table
summary) for LLM context injection, not for SQL template matching.

### 2.1 Corpus cache

```python
_CHUNKS: list[dict[str, Any]] = []
_CHUNKS_TTL: float = 3600.0   # rebuild at most once per hour
```
`_ensure_chunk_corpus(force=False)` returns the cached list unless `force=True` or the TTL has
expired, in which case it calls `retrieval_chunks.build_retrieval_chunks()` (a full re-query of
the SQLite DB — see §5) and repopulates `_CHUNKS`. Thread-safe via `_chunk_lock`.

### 2.2 `refresh_chunk_index(force=False) -> int`

Rebuilds the corpus, then embeds every chunk's `text` field via
`voyage_embeddings.embed_documents()` (which — despite the module name — routes through
whichever `EMBEDDING_PROVIDER` is configured; see §3) and stores the resulting vector on each
chunk dict's `"embedding"` key. On an embedding-count mismatch it logs a warning and sets every
chunk's embedding to `[]` (fails safe rather than misaligning chunk/vector pairs). Returns the
chunk count indexed, or `0` on any exception.

### 2.3 `retrieve_chunks_with_meta(query, top_k=8, required_keywords=None)`

This is the actual entry point used by the Ask pipeline's context-building step. Behaviour,
traced end to end:

1. **Corpus.** `_ensure_chunk_corpus()`. If empty, returns `([], meta)` with
   `dense_reason="no_chunk_corpus"`.
2. **Dense retrieval, pgvector first.** Tries
   `from backend.services.ask_pgvector import dense_retrieve` and calls it with
   `top_k=max(top_k*4, 24)` (over-fetches 4× or a floor of 24, to give the RRF fusion step a
   wider candidate pool than the final `top_k`). If `ask_pgvector.dense_retrieve` returns
   status `"completed"` or `"completed_with_failures"`, those rows are used, each annotated with
   `voyage_score = similarity_score` for downstream fusion, and `meta["dense_backend"] =
   "pgvector"`.
3. **Fallback to in-memory Voyage/Cohere cosine search** — triggered whenever `dense_ranked` is
   still empty after step 2 (pgvector unavailable, `DATABASE_URL` unset, exception, or pgvector
   returned nothing). Imports `cosine_similarity, embed_query` from `voyage_embeddings`; if the
   in-memory `_CHUNKS` have no embeddings yet, calls `refresh_chunk_index()` to populate them
   on-demand (lazy, expensive — this is the path that triggers a live embedding API call, or a
   local-model load, mid-request). Embeds the query with `input_type="query"`, scores every
   chunk's cached embedding with the pure-Python `cosine_similarity()`, sorts descending, keeps
   the top `max(top_k*4, 24)`. `meta["dense_backend"] = "voyage_in_memory"`,
   `meta["dense_status"] = "fallback"`.
4. **Sparse retrieval** — plain keyword overlap over the **full** chunk corpus (not just the
   dense-ranked subset, unlike Part A): `kw_score = count of query words (len > 3) that appear
   in the lowercased chunk text`. `required_keywords`, if passed, is a hard pre-filter — chunks
   missing any required keyword are dropped entirely before scoring. Explicitly documented in
   the module docstring as "keyword overlap, not TF-IDF" — no IDF weighting here, unlike Part A.
5. **Fusion** — imports `rrf_fuse` from `voyage_embeddings.py` (the weighted RRF, §6.2) and fuses
   `dense_ranked` with `sparse_ranked`, returning `fused[:top_k]`.
6. Returns `(chunks, meta)` — `meta` is a rich diagnostic dict tracking
   `pgvector_status/reason`, `dense_backend/status/reason/count`, `sparse_backend/status/count`,
   `fusion_backend/status/fused_count` — every stage is independently observable, which matters
   because this function degrades gracefully through three backends (pgvector → in-memory dense
   → sparse-only) without ever raising.

`retrieve_chunks()` is a thin wrapper that discards the `meta` dict.

---

## 3. `voyage_embeddings.py` — provider identity resolved

### 3.1 The discrepancy, resolved definitively

**`CLAUDE.md` says**: `voyage_embeddings.py — Cohere Embed v3 dense embeddings client.`

**The code confirms this is correct, and self-documents it.** The module's own docstring reads:

> `Cohere Embed v3 dense embeddings client (replaces Voyage AI).`
> `Model: embed-english-v3.0 (1024-dim; same column width as Voyage — no schema change).`

So: **the filename `voyage_embeddings.py` is misleading/stale** — the module was originally a
Voyage AI client and was migrated to call Cohere's Embed v3 API by default, but the file was
never renamed. This is not a bug to fix as part of this documentation task, just a fact to
record: **the default/primary code path in this file calls Cohere, not Voyage**, despite the
module name and despite `import voyageai` still being wired up as a secondary, non-default
provider (`EMBEDDING_PROVIDER=voyage`).

Concretely, three providers are selectable via `_get_embedding_provider()` (reads
`os.environ.get("EMBEDDING_PROVIDER", "local")`, restricted to `{"local", "cohere", "voyage"}`,
defaulting anything else to `"local"`):

| `EMBEDDING_PROVIDER` | Branch in `embed_texts()` | Backing call |
|---|---|---|
| `local` (default) | `from backend.services.local_embeddings import embed_texts_local` | SentenceTransformers, local CPU, no API key |
| `cohere` | falls through to the main body of `embed_texts()` | `cohere.ClientV2.embed(model="embed-english-v3.0", ...)` |
| `voyage` | `_embed_voyage(...)` | `voyageai.Client.embed(model="voyage-3", ...)` |

Note the **default is `local`**, not Cohere — both here (`voyage_embeddings.py:46`) and in
`config.py:108` (`EMBEDDING_PROVIDER: str = os.environ.get("EMBEDDING_PROVIDER", "local")`).
Cohere/Voyage are opt-in via env var.

**A second, smaller discrepancy worth flagging**: `config.py` defines
`Config.EMBEDDING_PROVIDER`, but nothing in the codebase reads `ActiveConfig.EMBEDDING_PROVIDER`
or `Config.EMBEDDING_PROVIDER` (confirmed via repo-wide grep — zero matches outside the
definition itself). The actual runtime branch is decided exclusively by
`voyage_embeddings._get_embedding_provider()` re-reading `os.environ.get("EMBEDDING_PROVIDER",
...)` directly, every call. Both reads use the same env var name and the same default, so
behaviour is identical in practice — but the `config.py` field is presently a documentation-only
artifact, not a live control.

### 3.2 Cohere API integration details

```python
COHERE_MODEL: str = "embed-english-v3.0"
COHERE_OUTPUT_DIMENSION: int = 1024
COHERE_MAX_BATCH: int = 96        # per-request text limit
COHERE_CALL_INTERVAL: float = 12.0  # 60s / 5 calls/min — proactive gap
```

**Auth**: `COHERE_API_KEY` env var, read once via `_init_key()` (double-checked-locking
singleton pattern with `_init_lock`). Client: `cohere.ClientV2(api_key=COHERE_API_KEY)`.
`COHERE_MODEL` is overridable via `COHERE_MODEL` env var.

**`input_type` asymmetry** (the module's own "CRITICAL" callout, and the subject of a dedicated
regression test — see §7):
```python
_COHERE_INPUT_TYPE = {"document": "search_document", "query": "search_query"}
```
Public callers always pass the Voyage-style names `"document"`/`"query"`; `_api_embed()`
translates to Cohere's native `search_document`/`search_query` values only at the point of the
actual API call. `embed_documents(texts)` = `embed_texts(texts, input_type="document")`.
`embed_query(text)` = `embed_texts([text], input_type="query", use_cache=False)` (single string,
no cache — queries are one-off, not reused).

**Rate limiting** — two layers:
1. *Proactive*: `_proactive_sleep()` enforces a **12-second minimum gap** between successive
   Cohere API calls process-wide (`_rate_lock` + `_last_call_at`), derived from the trial-key
   limit of 5 calls/min.
2. *Reactive 429 backoff*: exponential retry ladder `_RETRY_DELAYS = [2, 4, 8, 16, 32]` seconds.
   On a detected rate-limit error (`"429" in str(exc)` or exception class name contains
   `TooManyRequests`), waits `max(retry_delay, COHERE_CALL_INTERVAL)` seconds, or honours a
   `Retry-After` response header if present, whichever is available. Non-rate-limit errors still
   retry through the same ladder (no distinction in retry count) but without the extended wait.
   After exhausting all 5 retries, that batch is marked failed and filled with `[]` vectors —
   `embed_texts` never raises on a single batch failure, it degrades per-item.

**Batching**: input texts are chunked into groups of `COHERE_MAX_BATCH = 96` before each API
call (Cohere Embed v3's per-request limit).

**Dimension assertion**: `_api_embed()` raises `RuntimeError` if the returned embedding width
`!= COHERE_OUTPUT_DIMENSION (1024)` — hard-fail rather than silently storing wrong-width
vectors.

**In-process document cache** (`_DOC_CACHE`, module-level dict, `text -> vector`): only applies
to `input_type == "document"` calls with `use_cache=True` (the default for
`embed_documents`); queries are never cached. Bounded to `_DOC_CACHE_MAX = 4096` entries; on
overflow, evicts the *oldest 10%* of keys by iteration order (`list(_DOC_CACHE.keys())[:409]`) —
this is insertion-order eviction (Python dicts preserve insertion order), not true LRU, since
cache hits don't move an entry.

### 3.3 Voyage AI path (`EMBEDDING_PROVIDER=voyage`, non-default)

`_embed_voyage()` — structurally similar (same document cache, same
document/query-uncached split) but simpler: no proactive rate-limit sleep, no exponential 429
retry ladder — a single `voyageai.Client.embed(batch, model="voyage-3", input_type=input_type)`
call per batch of `VOYAGE_MAX_BATCH = 128`, with the whole batch marked `[]` on any exception
(one `try/except` around the call, no retry). `VOYAGE_MODEL = "voyage-3"`, overridable via
`VOYAGE_MODEL` env var. `VOYAGE_API_KEY` falls back to `COHERE_API_KEY` if unset
(`VOYAGE_API_KEY = _getenv("VOYAGE_API_KEY") or COHERE_API_KEY`) — a convenience so a single key
env var can serve both if desired, though in practice they'd need to be different provider keys.

### 3.4 Shared utility functions (provider-agnostic)

- `cosine_similarity(a, b)` — plain Python dot-product / (norm(a)·norm(b)); returns `0.0` on
  empty/mismatched-length inputs.
- `rrf_fuse(dense_ranked, sparse_ranked, k=60, dense_weight=0.6, sparse_weight=0.4)` — this is
  **Part B's** RRF, distinct from Part A's in `embedding_index.py`. See §6.2 for the formula
  comparison.
- `build_person_passport`, `build_place_passport`, `build_community_summary` — text verbalisers
  called by `retrieval_chunks.py` (§5) to turn structured rows into embeddable prose. Despite
  living in `voyage_embeddings.py`, these are pure string builders with no embedding calls
  inside them.
- `get_api_call_count()` / `reset_api_call_count()` — module-level counter of Cohere API calls
  made (`_api_call_count`), used by validation scripts (§8) to report budget usage; not
  incremented by the Voyage path.

---

## 4. `local_embeddings.py` — local dense provider

### 4.1 Model — confirmed

```python
BGE_MODEL_NAME: str = "BAAI/bge-large-en-v1.5"
BGE_OUTPUT_DIMENSION: int = 1024
```
Confirmed as claimed in `CLAUDE.md`: MIT-licensed, CPU-only (`device="cpu"` hardcoded in the
`SentenceTransformer(...)` constructor — **no GPU/CUDA path exists in this module**), no API key
required. 1024-dim output, matching Cohere/Voyage's dimension exactly (intentional — see
`ask_pgvector.py`'s single shared `vector(1024)` column, §5.4).

### 4.2 Loading — lazy singleton with offline-first cache resolution

`_get_model()` (double-checked locking via `_model_lock`, module-level `_model` global):

1. Imports `sentence_transformers.SentenceTransformer` lazily inside the function — raises a
   descriptive `RuntimeError` ("run: pip install sentence-transformers torch") if not installed,
   rather than failing at module import time. This matters because `requirements.txt` **does
   not install these by default**:
   ```
   # ── Local embedding provider (BAAI/bge-large-en-v1.5) ────────────────────────
   # Too large for Azure App Service (~2 GB). Install locally only:
   #   pip install sentence-transformers torch
   # sentence-transformers
   # torch
   ```
   So on a stock `pip install -r requirements.txt`, `EMBEDDING_PROVIDER=local` (the default!)
   will fail loudly the first time an embedding is requested, unless the developer has manually
   installed `sentence-transformers` and `torch`. This is a deliberate deployment-size tradeoff
   (comment cites Azure App Service's ~2 GB constraint) — worth flagging for anyone deploying
   this app fresh.
2. `_allow_model_download()` reads `LOCAL_EMBEDDINGS_ALLOW_DOWNLOAD` (truthy values:
   `1/true/yes/on`); default `false`.
3. If downloads are disallowed (`local_only = not _allow_model_download()`), `_local_model_path()`
   must resolve a populated local cache directory or the function raises
   `RuntimeError("... is not cached locally; pre-download it or set
   LOCAL_EMBEDDINGS_ALLOW_DOWNLOAD=true.")`. This is the exact error path exercised by
   `tests/test_local_embeddings.py::test_get_model_fails_fast_when_cache_is_missing`.
4. `_local_model_path()` checks, in order: the legacy `sentence_transformers`-style cache
   (`~/.cache/torch/sentence_transformers/<model_name>/`) requiring
   `{modules.json, config_sentence_transformers.json, config.json, tokenizer_config.json}` all
   present as files; then the modern Hugging Face Hub cache layout
   (`$HF_HOME/hub/models--BAAI--bge-large-en-v1.5/snapshots/<ref>` or
   `~/.cache/huggingface/hub/...`), resolving `refs/main` to a snapshot hash, falling back to
   the most recent snapshot directory (sorted reverse) if `refs/main` is absent. The rationale
   given in the docstring: a *partial* HF cache dir can still exist and would otherwise trigger
   a slow silent online-resolution attempt — so it insists on the full required-file set being
   present locally before trusting the cache.
5. If `local_only`, sets `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` (via `setdefault`, so
   it won't clobber a caller's explicit setting) before constructing the model, and passes
   `local_files_only=local_only` to `SentenceTransformer(...)` (with a `TypeError` fallback for
   older `sentence-transformers` versions that don't accept that kwarg).
6. **Hard dimension assertion on load**: immediately encodes a probe string `["dim-probe"]` and
   asserts `len(probe[0]) == BGE_OUTPUT_DIMENSION (1024)`, raising `RuntimeError` with an
   explicit `expected=/actual=` message otherwise. Fails loudly at first-use time, not silently
   later at query time.

### 4.3 Encoding — prefix asymmetry (CRITICAL, per module docstring)

```python
BGE_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "
```
- `input_type="query"` → text is **prefixed** with `BGE_QUERY_INSTRUCTION` before encoding.
- `input_type="document"` → **no prefix**, raw text encoded as-is.

This mirrors the BGE model card's documented asymmetric-retrieval convention, and is guarded by
a dedicated regression test (`test_local_embed_input_type_asymmetry` in
`tests/test_ask_pgvector.py`, §7) that fails if the two paths ever produce identical input text.

**Batching / encode call**:
```python
with torch.no_grad():
    vecs = model.encode(texts_to_encode, normalize_embeddings=True,
                         show_progress_bar=False, batch_size=128)
```
`normalize_embeddings=True` on both query and document paths — vectors are unit-norm, so cosine
similarity reduces to a dot product (same convention as Cohere's normalized output and Part A's
TF-IDF vectors). Fixed `batch_size=128` (not configurable). `torch.no_grad()` context disables
gradient tracking (inference-only, saves memory).

**Failure contract**: `embed_texts_local()` wraps the whole encode call in `try/except
Exception`, logs a warning, and returns `[[] for _ in texts]` — i.e. one empty list per input
text — rather than raising. This matches the "same contract as Cohere path" note in the
docstring, and is exercised by
`test_embed_texts_local_returns_empty_vectors_when_model_load_fails`.

### 4.4 No caching layer inside `local_embeddings.py` itself

Unlike `voyage_embeddings.py`'s `_DOC_CACHE`, `local_embeddings.py` has **no text→vector cache**
of its own — every call to `embed_texts_local()` re-encodes every text passed to it. Caching for
the local provider, if any occurs, happens one layer up (e.g. `embedding_index.py`'s `_CHUNKS`
in-memory store, or `ask_pgvector`'s persisted table) rather than inside this module.

---

## 5. `ask_pgvector.py` — persistent Postgres/pgvector backend

### 5.1 When it's used vs. when it falls back

Entirely optional and isolated from the main SQLite app DB (module docstring: *"the main app
continues to use SQLite, while Ask-page dense retrieval optionally uses a PostgreSQL pgvector
store through `DATABASE_URL`"*).

`backend_status()` determines availability by checking, in order:
1. `DATABASE_URL` env var (or `ActiveConfig.DATABASE_URL` fallback) is non-empty →
   else `reason="database_url_missing"`.
2. URL scheme starts with `postgresql://` or `postgres://` →
   else `reason="database_url_not_postgres"` (so a SQLite-style `DATABASE_URL` — which the main
   app might have for other purposes — is explicitly rejected here, not silently coerced).
3. `psycopg` import succeeded at module load time (wrapped in a top-level `try/except`, so a
   missing `psycopg` package doesn't break the whole app) → else `reason="psycopg_not_installed"`.

If all three pass, `{"enabled": True, "available": True, "reason": None, "dense_backend":
"pgvector"}`.

**Loud-once warning**: `_warn_if_psycopg_missing()` logs a `WARNING` exactly once per process
(guarded by module-level `_PSYCOPG_MISSING_WARNED` flag) — but **only** when
`ASK_USE_NEW_PIPELINE` is truthy (default `true`). If the legacy pipeline is explicitly selected
(`ASK_USE_NEW_PIPELINE=false`), the warning is suppressed entirely, since pgvector retrieval is
considered a new-pipeline-relevant concern. Verified by three dedicated tests in
`tests/test_ask_pgvector.py` (`test_psycopg_missing_with_new_pipeline_emits_warning`,
`..._false_no_warning`, `test_psycopg_present_no_warning`).

**Fallback chain when pgvector is unavailable**: `embedding_index.retrieve_chunks_with_meta()`
(§2.3) catches any exception importing/calling `ask_pgvector.dense_retrieve`, and any
non-`completed*` status, and transparently falls back to the in-memory Voyage/Cohere cosine
search over `_CHUNKS`. So the observable behaviour with no `DATABASE_URL` set at all is: every
chunk retrieval silently uses the in-memory path, with `meta["dense_backend"] =
"voyage_in_memory"` recording that fact for diagnostics.

### 5.2 `ask_retrieval_chunks` table — full DDL (from `ensure_pgvector_schema()`)

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS ask_retrieval_chunks (
  id BIGSERIAL PRIMARY KEY,
  chunk_id TEXT NOT NULL UNIQUE,
  source_type TEXT NOT NULL,
  source_table TEXT,
  source_record_id TEXT,
  title TEXT NOT NULL,
  text TEXT NOT NULL,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  content_hash TEXT NOT NULL,
  embedding vector(1024) NOT NULL,     -- width from _vector_dimension()
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ask_retrieval_chunks_source
  ON ask_retrieval_chunks (source_type, source_table, source_record_id);

CREATE INDEX IF NOT EXISTS idx_ask_retrieval_chunks_embedding
  ON ask_retrieval_chunks
  USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);
```

`_vector_dimension()` resolves the `vector(N)` width at schema-creation time: first checks env
var `VOYAGE_OUTPUT_DIMENSION`, then imports `VOYAGE_OUTPUT_DIMENSION` from
`voyage_embeddings.py` (which is itself set to `= COHERE_OUTPUT_DIMENSION = 1024`, per the
"Keep VOYAGE_ aliases" comment in that file so this module needs zero changes across the
Voyage→Cohere provider swap), falling back to a hardcoded `1024` if both fail. In practice this
always resolves to `1024` for every currently supported provider (local BGE, Cohere, Voyage all
happen to be 1024-dim).

**Index tuning**: HNSW with `m=16` (max connections per node) and `ef_construction=64` (build-
time candidate list size) — standard pgvector defaults, not customised. `rebuild_hnsw_index()`
drops and recreates this exact index, intended to be called after a bulk insert to skip
per-row index maintenance overhead during ingest.

### 5.3 Sync logic (`sync_retrieval_chunks`) — content-hash diffing

```python
def _content_hash(chunk) -> str:
    payload = {"title": ..., "text": ..., "metadata": ..., "source_type": ...,
               "source_table": ..., "source_record_id": ...}
    return hashlib.sha1(json.dumps(payload, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
```
SHA-1 over a canonical JSON payload of the chunk's meaningful fields (excludes generated fields
like `id`/`chunk_id` itself, and the embedding).

Flow, guarded by `_SYNC_LOCK` and a **1-hour TTL** (`_SYNC_TTL_SECONDS = 3600.0`) unless
`force=True`:
1. Ensure schema exists (idempotent `CREATE ... IF NOT EXISTS`).
2. `build_retrieval_chunks()` (§6) generates the full current corpus.
3. Compute `chunk_hashes` for every generated chunk; fetch `existing_hashes` from the DB
   (`SELECT chunk_id, content_hash FROM ask_retrieval_chunks`).
4. **Stale chunk deletion**: any `chunk_id` present in the DB but absent from the freshly-built
   corpus is deleted (`stale_chunk_ids = existing - current`).
5. **Changed-chunk detection**: a chunk is re-embedded only if `force=True` or its hash differs
   from what's stored — unchanged chunks are skipped entirely, saving embedding API calls/CPU.
6. Embeds only `changed_chunks` via `embed_documents()`.
7. **Upsert**: `INSERT ... ON CONFLICT (chunk_id) DO UPDATE SET ...` — one statement per changed
   chunk, all fields including `embedding` refreshed, `updated_at = NOW()`.
8. **Status semantics** (exercised by dedicated tests): 
   - `failed_count == 0` → `"completed"`
   - `succeeded_count == 0 and failed_count > 0` → `"failed"` (no rows written for that run)
   - both > 0 → `"completed_with_failures"` (partial write; the successfully-embedded chunks
     ARE persisted)
9. **TTL-driven self-healing**: because step 5's "changed" check treats a chunk missing from
   `existing_hashes` (i.e. one that failed to embed and was therefore skipped in step 7) as
   "changed" on the next non-forced sync after TTL expiry, previously-failed chunks are
   automatically retried without any `force=True` call — confirmed by
   `test_failed_chunks_are_retried_after_ttl_without_force`.

### 5.4 Dense query (`dense_retrieve`)

```sql
SELECT
  chunk_id, source_type, source_table, source_record_id, title, text, metadata_json,
  1 - (embedding <=> %s::vector) AS similarity
FROM ask_retrieval_chunks
ORDER BY embedding <=> %s::vector
LIMIT %s
```
Uses pgvector's `<=>` **cosine distance** operator (not `<->` L2 or `<#>` inner product);
similarity is `1 - distance`. The query embedding is formatted as a pgvector literal string via
`_vector_literal()` (`"[" + ",".join(f"{v:.8f}" for v in values) + "]"`) and passed as a bound
parameter, cast with `::vector` in SQL. Always calls `sync_retrieval_chunks()` first (respecting
its TTL/force semantics) before querying, so a `dense_retrieve` call can itself trigger a sync.
If the sync doesn't reach `completed`/`completed_with_failures`, `dense_retrieve` short-circuits
and returns `([], meta)` without ever issuing the SQL query.

---

## 6. `retrieval_chunks.py` — chunk corpus builder

### 6.1 What a "chunk" is

A **chunk** is a JSON-safe dict with a fixed shape, uniform across every builder function:
```python
{
  "id": str,              # == chunk_id
  "chunk_id": str,        # stable, deterministic identifier (used as the pgvector PK / dedup key)
  "source_type": str,     # e.g. "emigration" | "eviction" | "estate_survey" | "unified_record"
                           #      | "person_passport" | "place_passport" | "community_summary"
                           #      | "census" | "workhouse_record" | "source_summary"
  "source_table": str | None,   # originating SQLite table, or "system" for synthetic chunks
  "source_record_id": str,
  "title": str,            # short human-readable label
  "text": str,             # the actual text that gets embedded
  "metadata": dict,        # structured fields carried alongside for filtering/display
}
```
This shape is consumed identically by both the pgvector sync path (`ask_pgvector.py`) and the
in-memory fallback path (`embedding_index._CHUNKS`) — the module docstring calls this out
explicitly ("intentionally storage-agnostic").

### 6.2 Five chunk-building functions, invoked by `build_retrieval_chunks()`

`build_retrieval_chunks()` (lines 419–436) opens one `get_db_conn()` connection, runs all five
builders against it in this order, concatenates, and closes the connection in a `finally`:

1. **`_build_place_chunks(conn)`** — two sub-kinds:
   - *Place passports*, one per `townland` row (guarded by `_table_exists(conn, "townland")`).
     For each townland, a correlated-subquery stats fetch pulls `population_1851` (from
     `census_record` at `year=1851`), `total_evictions` (`SUM(count)` from
     `clearances_record`), `total_emigrants` (`COUNT(DISTINCT record_id)` from `unified_record`
     where `has_emigration_record=1`, matched by `townland_norm` text-equality — not an FK, same
     caveat noted in `docs/02_database_schema.md`). Text built via
     `voyage_embeddings.build_place_passport(name, civil_parish, barony, county, description,
     stats)`; falls back to a bare one-line sentence if the import fails. `chunk_id =
     f"place:{townland_id}"`.
   - *Community summaries*, one per distinct `civil_parish` (only if `build_community_summary`
     imported successfully) — aggregates townland count, summed evictions, min/max clearance
     year, and a separate emigrant count query keyed by `parish` text match. Text via
     `voyage_embeddings.build_community_summary(...)`. `chunk_id =
     f"community:{parish.upper()}"`.
2. **`_build_census_chunks(conn)`** — one chunk per `census_record` row (joined to `townland`),
   guarded by both tables existing. Text is a fixed `Name/Place/Source/Year/Age/Related
   fields/Record id` template (population totals by sex, house counts in "Related fields").
   `chunk_id = f"census:{census_record.id}"`.
3. **`_build_unified_chunks(conn)`** — the largest source. One chunk per `unified_record` row
   with non-empty `canonical_name`, `source_type` derived via `_source_type_for_unified_row`
   (emigration > eviction > estate_survey > unified_record, first-match-wins on the boolean
   flag columns). Text template includes legal action, occupation, household relationship, ship
   name, departure/arrival, comments — whichever fields are present — via the same "Related
   fields: X | Y | Z" joined-string convention as census/workhouse chunks. **Plus**: groups rows
   by `(canonical_name, townland_norm)` and emits one additional **person-passport** chunk per
   distinct person/place pair via `voyage_embeddings.build_person_passport(display_name,
   records)`, `chunk_id = f"person:{name.upper()}:{townland.upper()}"` — this is the
   `source_type="person_passport"` granularity referenced by `CLAUDE.md`'s "person/place/event"
   description (there is no distinct "event" granularity as a literal `source_type`; per-record
   chunks from step 3 itself serve that role for individual emigration/eviction/survey events).
4. **`_build_workhouse_chunks()`** — no `conn` parameter; calls `workhouse_service.get_workhouse()`
   directly (imported lazily, `try/except` guarded — if the workhouse service is unavailable,
   returns `[]`). One chunk per workhouse row, `chunk_id = f"workhouse:{source_record_id}"`
   where `source_record_id` falls back through `register_number` then a synthetic
   `f"{source_sheet}-{idx+1}"`. Text lists employment, status, religion, spouse, children count,
   disability in "Related fields".
5. **`_build_source_summary_chunks(conn)`** — a single synthetic chunk (`chunk_id =
   "summary:source_tables"`) with row counts for `townland, census_record, clearances_record,
   unified_record, heritage_feature` — a coarse "what's in this database" chunk for
   sensemaking/overview questions. Skipped entirely if none of those tables exist.

### 6.3 Chunks vs. templates — relationship to Part A

Chunks (this module) and templates (`QUESTION_TEMPLATES` in `ask_service.py`, indexed by Part A
of `embedding_index.py`) are **entirely disjoint systems** feeding two different consumers:
templates produce ready-to-execute parameterised SQL for the fast lane; chunks produce free-text
passages injected into the LLM's context window for Phase 7 synthesis (or, in the legacy
pipeline, for FALLBACK-route free-form SQL generation prompts). Chunks are never matched against
`required_keywords`/`optional_keywords` template metadata — that vocabulary is specific to Part
A.

---

## 7. `EMBEDDING_PROVIDER` selection — full runtime trace

1. **Config surface**: `config.py:108`, `Config.EMBEDDING_PROVIDER = os.environ.get(
   "EMBEDDING_PROVIDER", "local")` — evaluated once at class-definition/import time. As noted in
   §3.1, **this value is never read anywhere else in the codebase.**
2. **Actual runtime decision point**: `voyage_embeddings._get_embedding_provider()`, called
   fresh (not cached) on every `embed_texts()` invocation:
   ```python
   raw = os.environ.get("EMBEDDING_PROVIDER", "local")
   p = (raw or "local").lower().strip()
   return p if p in ("local", "cohere", "voyage") else "local"
   ```
   Any value outside the three recognised strings silently coerces to `"local"` (no error, no
   log line at this point).
3. **Branch dispatch** inside `embed_texts()`:
   - `"local"` → delegates to `local_embeddings.embed_texts_local()` — a **different module
     entirely**, imported lazily inline (`from backend.services.local_embeddings import
     embed_texts_local`), bypassing all of `voyage_embeddings.py`'s Cohere-specific machinery
     (rate limiting, retry ladder, document cache — `local_embeddings.py` has none of these; see
     §4.4).
   - `"voyage"` → `_embed_voyage(...)` within the same file.
   - anything else (i.e. `"cohere"`, since that's the only other valid value) → falls through to
     the main body of `embed_texts()`, which is the Cohere implementation.
4. **Every call site that needs embeddings** — `embedding_index.py`'s chunk retrieval (§2),
   `ask_pgvector.py`'s sync and dense-query paths (§5) — imports from `voyage_embeddings.py`
   (`embed_documents`, `embed_query`), **never** imports `local_embeddings.py` directly. The
   `local` provider is only ever reached indirectly, through `voyage_embeddings.embed_texts()`'s
   branch. This means `voyage_embeddings.py` functions as the single provider-selection façade
   for the whole retrieval subsystem — every embedding request in the app funnels through it
   regardless of which of the three providers ends up serving it.
5. **`scripts/validate_ann_scale.py`** is the one exception: it imports
   `SentenceTransformer`/`BGE_MODEL_NAME`/`BGE_QUERY_INSTRUCTION` directly from
   `local_embeddings.py` and constructs its own model instance, bypassing the façade entirely —
   appropriate for a standalone validation script run with `EMBEDDING_PROVIDER=local` set
   explicitly in its usage instructions.

---

## 8. Template library — location and structure (as requested, resolving where the "100+
   verified SQL templates" actually live)

**Not** a JSON/data file — `QUESTION_TEMPLATES` is a **Python list-of-dicts literal**, defined
directly in `backend/services/ask_service.py`, starting at line 512:
```python
QUESTION_TEMPLATES: list[dict[str, Any]] = [
    {"id": "emigration_total",
     "category": "emigration", "description": "Total emigrated people",
     "required_keywords": ["emigra"],
     "optional_keywords": ["total", "how many", "count", "overall", "all"],
     "sql_template": "SELECT COUNT(DISTINCT record_id) AS total_emigrated_people FROM unified_record WHERE has_emigration_record = 1"},
    ...
]
```
**Actual count**: 81 templates (counted by `"id":` occurrences within the list, current repo
state) — modestly short of the "one-hundred-plus" figure in project docs/memory; treat that
figure as an approximate/historical characterisation rather than an exact current count.

**Per-template schema** (per the comment block immediately preceding the list definition, lines
505–510):
| Key | Meaning |
|---|---|
| `id` | stable string identifier, used by Part A's `IndexHit.key` and by `_match_and_build_template_by_id` |
| `category` | grouping label (`emigration`, and by inspection also `eviction`, `census`, `tenancy`, etc. — organised with `# ── CATEGORY ──` comment banners through the list) |
| `description` | canonical natural-language text that gets TF-IDF-embedded by Part A (`_build()` in `embedding_index.py` uses `tmpl.get("description")` as the text to index — **not** the SQL, not the keywords) |
| `required_keywords` | ALL must appear (lowercased substring match) in the question — both for Part A's sparse hard-gate and for the independent `_try_semantic_layer_fill`-style scoring loop at `ask_service.py:2010` |
| `optional_keywords` | soft-boost keywords |
| `sql_template` | raw SQL string, with optional `{townland_norm}` / `{year}` / `{surname}` placeholders substituted via simple `str.replace()` (not parameterised bind variables — substitution happens before the safety/read-only validation stage described in `CLAUDE.md`'s Stage 2) |
| `requires_townland` / `requires_year` / `requires_surname` | boolean guards — template is skipped entirely if the corresponding entity wasn't resolved for this question |

Two consumers of this same list exist in `ask_service.py`, independent of each other:
- `_try_rule_based_template_match` (loop at line ~2010, exact function boundary not separately
  named in a docstring at that point) — scores every template by `len(required)*2 +
  sum(optional matches)`, requires `best_score >= 2`, is gated by an explicit topical exclusion
  list (`workhouse`, `died of`, `religion`, `average rent`, age-range phrasing, etc.) and a
  regex age-filter guard — this is a **separate, simpler matching pass** from Part A's TF-IDF/RRF
  retrieval, used elsewhere in the legacy pipeline's fast-lane sequence (semantic-layer rule fill
  territory, not the embedding-index Phase 4 lane).
- `_phase4_retrieve` (§1.7) — Part A's embedding-based lookup, which re-validates the winning
  hit through `_match_and_build_template_by_id` before accepting it.

**A stale-code note surfaced during this review**: a file `backend/services/template_store.py`
exists **only inside a git worktree**
(`.claude/worktrees/agent-aa7edae71d22d33d8/backend/services/template_store.py`), not in the
main working tree. Its own docstring calls `QUESTION_TEMPLATES` "the 100-template library" and
describes seeding a store from it plus `ask_query_memory`. This appears to be in-progress work
from a different agent branch (plausibly related to the "RAG v2 Rebuild" effort noted in prior
session history) and is **not part of the current main-tree pipeline** — `template_store.py`
does not exist at `backend/services/template_store.py` in the primary working directory as of
this review, so it is out of scope for this document beyond this note.

---

## 9. Validation / precomputation scripts

Neither script is imported by application code — both are standalone, manually-run CLI tools.

### 9.1 `scripts/cohere_sample_validate.py` — "Task 4, Cohere provider swap validation"

Targeted (not full-corpus) validation of the Voyage→Cohere migration. Loads `.env.local`/`.env`
manually (duplicates `ask_service`'s env-loading logic rather than importing it, since it's
designed to run standalone). Selects a hand-picked 15-record sample from `unified_record` by
`record_id` (`TARGET_IDS`) including a documented "ground truth" (`CL8037`, Edward Dagg
emigration) and a "hard negative" (`CL53`, same surname/different record type/townland) plus
assorted decoys. Rebuilds chunk text using **inlined duplicate copies** of `_source_type` and
`_record_text` (not imported from `retrieval_chunks.py` — this script is intentionally
dependency-light so it can run without Flask app context). Embeds via
`voyage_embeddings.embed_documents`, **truncates** `ask_retrieval_chunks` (explicitly, so the
whole table shares one embedding space for valid cosine comparison — the docstring calls this
out as intentional and NOT the full ~41,600-chunk ingest), upserts, then runs one hardcoded
query ("Who is Edward Dagg from Aghowle who emigrated on the Dunbrody in 1853?") and reports
whether the ground-truth chunk ranks #1, plus the score gap between rank 1 and rank 2, plus a
running Cohere API call-budget count via `get_api_call_count()`.

### 9.2 `scripts/validate_ann_scale.py` — "Task 6, ANN-at-scale validation"

Run *after* a full corpus ingest, against a live `ask_retrieval_chunks` table (default DSN
`postgresql://postgres:pw@localhost:5432/postgres`, `EMBEDDING_PROVIDER=local` set by default in
its own `os.environ.setdefault` calls — this script exercises the **local BGE** provider path,
not Cohere, embedding queries directly via its own `SentenceTransformer` instantiation rather
than through `voyage_embeddings.py`'s façade, as noted in §7 point 5). Four checks:
1. `EXPLAIN ANALYZE` on a sample cosine-distance query — confirms the Postgres planner
   auto-selects the HNSW index rather than a sequential scan.
2. **Recall@10**: for three hand-picked eval queries with known expected chunk IDs, compares
   HNSW-index (`_ann_query`, with `enable_indexscan`/`enable_bitmapscan` left on) results
   against an exact/brute-force scan (`_exact_query`, which explicitly does
   `SET enable_indexscan = off; SET enable_bitmapscan = off;` around the same query to force a
   seqscan) across `ef_search` values `[40, 100, 200]`, escalating until recall ≥ 0.95 is
   reached; reports the final chosen `ef_search`.
3. **Relevance**: checks whether each eval query's expected chunk ranks #1 in the top-5 ANN
   results.
4. **Latency**: benchmarks 5 repeated queries with the HNSW index enabled vs. the forced-seqscan
   path, reporting average ms/query and the resulting speedup ratio.
Prints the final `idx_ask_retrieval_chunks_embedding` index definition at the end for a
paper-trail record of the tuning parameters actually in effect.

---

## 10. Cross-reference summary — constants at a glance

| Constant | Value | Location | Meaning |
|---|---|---|---|
| `TEMPLATE_FAST_LANE_THRESHOLD` | `0.68` | `embedding_index.py:34` | Part A cosine gate for template/metric fast-lane hit |
| `MEMORY_COSINE_THRESHOLD` | `0.55` | `embedding_index.py:38` | Part A cosine gate for memory-row reuse eligibility |
| `_RRF_K` (Part A) | `60` | `embedding_index.py:41` | Unweighted RRF smoothing constant, `1/(k+rank+1)` per list |
| RRF `k` (Part B) | `60` | `voyage_embeddings.py:412` (`rrf_fuse` default) | Weighted RRF smoothing constant |
| RRF `dense_weight` / `sparse_weight` (Part B) | `0.6` / `0.4` | `voyage_embeddings.py:413-414` | Blend weights, need not sum to 1.0 |
| `_MEMORY_TTL` | `60.0` s | `embedding_index.py:44` | Part A memory-tier rebuild interval |
| `_CHUNKS_TTL` | `3600.0` s | `embedding_index.py:366` | Part B in-memory chunk corpus rebuild interval |
| `_SYNC_TTL_SECONDS` | `3600.0` s | `ask_pgvector.py:54` | pgvector sync cache interval |
| `_DOC_CACHE_MAX` | `4096` | `voyage_embeddings.py:123` | Cohere/Voyage document-embedding cache size, insertion-order eviction |
| `COHERE_MAX_BATCH` | `96` | `voyage_embeddings.py:54` | Cohere Embed v3 texts-per-request limit |
| `VOYAGE_MAX_BATCH` | `128` | `voyage_embeddings.py:354` | Voyage AI texts-per-request limit |
| `COHERE_CALL_INTERVAL` | `12.0` s | `voyage_embeddings.py:55` | Proactive inter-call sleep (60s / 5 calls-per-min) |
| `BGE_OUTPUT_DIMENSION` | `1024` | `local_embeddings.py:29` | Local model output width, hard-asserted |
| `COHERE_OUTPUT_DIMENSION` | `1024` | `voyage_embeddings.py:53` | Cohere output width, hard-asserted |
| HNSW `m` / `ef_construction` | `16` / `64` | `ask_pgvector.py` DDL | pgvector index build parameters |
