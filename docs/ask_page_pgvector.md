# Ask Page pgvector Retrieval

This document covers only the Ask-page natural-language pipeline. It does not
cover workhouse-to-unified-record matching, which is a separate entity-resolution
feature.

## Why pgvector is limited to the Ask page

The Ask page needs semantic retrieval over rich narrative chunks before answer
generation. That is different from deterministic record linking.

- Ask page goal: find semantically relevant context for a natural-language question.
- Workhouse mapping goal: produce reviewable candidate identity links with explicit evidence.

Because those problems are different, pgvector is used only for Ask-page dense
retrieval and not for workhouse linking.

## Runtime path

The pgvector path is wired into the new Ask pipeline only.

- Entry point: `backend/services/ask_service.py`
- New-pipeline gate: `ASK_USE_NEW_PIPELINE`
- Dense+sparse retrieval call: `backend/services/embedding_index.py::retrieve_chunks_with_meta`
- pgvector backend: `backend/services/ask_pgvector.py`
- Chunk builder: `backend/services/retrieval_chunks.py`
- Embeddings provider: `backend/services/voyage_embeddings.py`

When `ASK_USE_NEW_PIPELINE=true`, routed Ask queries in the `RELATIONAL`,
`COMPARATIVE`, and fallback lanes call `retrieve_chunks_with_meta(...)`.

When `ASK_USE_NEW_PIPELINE=false`, the app keeps the legacy Ask path and does
not use the new pgvector chunk retrieval path.

## Chunk types

The Ask-page retrieval corpus is built from rich text chunks such as:

- person passport chunks
- place/townland passport chunks
- workhouse record chunks
- estate survey chunks
- census chunks
- emigration chunks
- eviction chunks
- community/place summaries
- source/table summaries

Each chunk stores descriptive text plus provenance fields like `source_type`,
`source_table`, `source_record_id`, `title`, and `metadata`.

## PostgreSQL and pgvector storage

The main application database remains SQLite. pgvector is an optional secondary
store used only for Ask-page dense retrieval.

Configuration:

- `DATABASE_URL` must point to PostgreSQL for pgvector to activate.
- `VOYAGE_API_KEY` enables Voyage embeddings.
- `VOYAGE_MODEL` defaults to `voyage-3`.
- `VOYAGE_OUTPUT_DIMENSION` defaults to `1024`.

Schema setup happens in `backend/services/ask_pgvector.py::ensure_pgvector_schema`
and uses:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

It creates:

```sql
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
  embedding vector(1024) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

Embeddings are persisted in PostgreSQL so they can be reused across workers and
app restarts. On later syncs, unchanged chunks are detected by `content_hash`
and are not re-embedded.

## Dense retrieval

Dense retrieval uses Voyage embeddings for both documents and the user question.

- Chunk embedding sync: `sync_retrieval_chunks(...)`
- Query embedding: `dense_retrieve(question, top_k=...)`

Dense lookup is done in SQL with pgvector similarity operators, not by scanning
all chunks in Python:

```sql
SELECT ...,
       1 - (embedding <=> %s::vector) AS similarity
FROM ask_retrieval_chunks
ORDER BY embedding <=> %s::vector
LIMIT %s;
```

## Sparse retrieval

Sparse retrieval on the chunk path is currently keyword-overlap scoring over the
chunk text. It is intentionally described as keyword overlap, not TF-IDF.

Implementation:

- `backend/services/embedding_index.py::retrieve_chunks_with_meta`

The older legacy Ask pipeline still has its own separate in-memory TF-IDF-based
template/query-memory retrieval path.

## RRF fusion

Ask-page chunk retrieval fuses:

- dense pgvector results
- sparse keyword-overlap results

Fusion uses Reciprocal Rank Fusion through
`backend/services/voyage_embeddings.py::rrf_fuse`.

The new Ask pipeline emits SSE progress stages that make the retrieval path
visible:

- `classifying_intent`
- `embedding_retrieval`
- `querying_fusion`
- `synthesizing_answer`

## Fallback behavior

If PostgreSQL or pgvector is unavailable:

- the Ask page does not crash
- pgvector retrieval is marked as skipped
- the retriever logs that pgvector was skipped
- dense retrieval falls back to the existing in-memory Voyage cosine path
- sparse keyword-overlap retrieval and RRF fusion still run

This fallback is handled in `backend/services/embedding_index.py`.

## Commands

Install dependencies:

```bash
pip install -r requirements.txt
```

Set environment variables:

```bash
export DATABASE_URL="postgresql://user:pass@host:5432/coolattin"
export VOYAGE_API_KEY="..."
export ASK_USE_NEW_PIPELINE=true
```

Run the focused Ask-page tests:

```bash
pytest -q tests/test_ask_pgvector.py tests/test_ask_pipeline_flags.py
```
