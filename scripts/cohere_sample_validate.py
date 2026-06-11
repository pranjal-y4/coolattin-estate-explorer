"""
scripts/cohere_sample_validate.py

Task 4 — Cohere provider swap validation.

Embeds a targeted 15-chunk sample (Edward Dagg ground-truth + hard-negative +
decoys) with Cohere, upserts into the local pgvec container, then runs the
query and reports the top-5 results.

Usage:
    COHERE_API_KEY=<key> DATABASE_URL=postgresql://postgres:pw@localhost:5432/postgres \\
        python3 scripts/cohere_sample_validate.py

The script truncates ask_retrieval_chunks before inserting so that all rows
share the same Cohere embedding space (required for valid cosine comparisons).
This is intentional for a targeted validation run — it does NOT run the full
~41,600-chunk ingest.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time

# ── path setup ────────────────────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Load .env.local if present (same logic as ask_service)
for env_file in (".env.local", ".env"):
    path = os.path.join(ROOT, env_file)
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip())

COHERE_API_KEY = os.environ.get("COHERE_API_KEY", "").strip()
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:pw@localhost:5432/postgres").strip()

if not COHERE_API_KEY:
    print("ERROR: COHERE_API_KEY is not set")
    sys.exit(1)

os.environ["DATABASE_URL"] = DATABASE_URL

# ── imports ───────────────────────────────────────────────────────────────────
import sqlite3
import psycopg  # type: ignore

from backend.services.voyage_embeddings import (
    embed_documents,
    embed_query,
    get_api_call_count,
    reset_api_call_count,
    COHERE_MODEL,
    COHERE_OUTPUT_DIMENSION,
)

# ── chunk construction helpers (inline, no Flask context needed) ──────────────

def _source_type(row: dict) -> str:
    if row.get("has_emigration_record"):
        return "emigration"
    if row.get("has_eviction_record"):
        return "eviction"
    if row.get("has_tenancy_record"):
        return "estate_survey"
    return "unified_record"


def _record_text(row: dict) -> str:
    related: list[str] = []
    if row.get("legal_action"):
        related.append(f"Legal action: {row['legal_action']}")
    if row.get("occupation"):
        related.append(f"Occupation: {row['occupation']}")
    if row.get("relationship_to_head_of_household"):
        related.append(f"Relationship: {row['relationship_to_head_of_household']}")
    if row.get("ship_name"):
        related.append(f"Ship: {row['ship_name']}")
    if row.get("departure"):
        related.append(f"Departure: {row['departure']}")
    if row.get("arrival"):
        related.append(f"Arrival: {row['arrival']}")
    if row.get("comments"):
        related.append(f"Comments: {row['comments']}")
    related_str = " | ".join(related) if related else "None recorded"
    st = _source_type(row).replace("_", " ").title()
    return (
        f"Name: {row.get('canonical_name') or 'Unknown'}\n"
        f"Place: {row.get('townland_norm') or row.get('townland') or row.get('parish') or 'Unknown'}\n"
        f"Source: {st}\n"
        f"Year: {row.get('year') or 'Unknown'}\n"
        f"Age: {row.get('age') or row.get('age_head_of_household') or 'Unknown'}\n"
        f"Related fields: {related_str}\n"
        f"Record id: {row.get('record_id') or 'Unknown'}"
    )


def _content_hash(chunk: dict) -> str:
    payload = {
        "title": chunk.get("title"),
        "text": chunk.get("text"),
        "metadata": chunk.get("metadata") or {},
        "source_type": chunk.get("source_type"),
        "source_table": chunk.get("source_table"),
        "source_record_id": chunk.get("source_record_id"),
    }
    return hashlib.sha1(
        json.dumps(payload, sort_keys=True, ensure_ascii=True).encode()
    ).hexdigest()


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{float(v):.8f}" for v in values) + "]"


# ── fetch target records from SQLite ─────────────────────────────────────────

TARGET_IDS = [
    "CL8037",   # Edward Dagg emigration — ground truth
    "CL53",     # Edward Dagg estate survey — hard negative (same name, different townland/type)
    # Dagg family on Dunbrody (related, some signal)
    "CL8038", "CL8039", "CL8040",
    # Unrelated decoys — mix of townlands and years
    "CL8767", "CL13027", "CL13208", "CL8602", "CL11127",
    "CL11229", "CL8058", "CL7783", "CL9722", "CL409",
]

print(f"\n=== Cohere sample validation — model={COHERE_MODEL} dim={COHERE_OUTPUT_DIMENSION} ===")
print(f"DATABASE_URL: {DATABASE_URL}")
print(f"Target records: {len(TARGET_IDS)}")

db_path = os.path.join(ROOT, "coolattin.db")
conn_sqlite = sqlite3.connect(db_path)
conn_sqlite.row_factory = sqlite3.Row

placeholders = ",".join("?" * len(TARGET_IDS))
rows = conn_sqlite.execute(
    f"""
    SELECT record_id, canonical_name, townland_norm, townland, parish, year,
           role, legal_action, occupation, age, age_head_of_household,
           relationship_to_head_of_household, ship_name, departure, arrival,
           comments, has_emigration_record, has_eviction_record, has_tenancy_record
    FROM unified_record
    WHERE record_id IN ({placeholders})
    """,
    TARGET_IDS,
).fetchall()
conn_sqlite.close()

if not rows:
    print("ERROR: No rows found in SQLite for target IDs")
    sys.exit(1)

chunks: list[dict] = []
for row in rows:
    r = dict(row)
    st = _source_type(r)
    chunk_id = f"{st}:{r['record_id']}"
    title = f"{r.get('canonical_name') or 'Unknown'} - {st.replace('_', ' ').title()} - {r.get('year') or 'Unknown'}"
    chunks.append({
        "chunk_id": chunk_id,
        "source_type": st,
        "source_table": "unified_record",
        "source_record_id": str(r.get("record_id") or ""),
        "title": title,
        "text": _record_text(r),
        "metadata": {
            "record_id": r.get("record_id"),
            "canonical_name": r.get("canonical_name"),
            "townland_norm": r.get("townland_norm") or r.get("townland"),
            "year": r.get("year"),
            "source_type": st,
        },
    })

found_ids = {r["record_id"] for r in rows}
missing = [id_ for id_ in TARGET_IDS if id_ not in found_ids]
if missing:
    print(f"WARNING: {len(missing)} target IDs not found in SQLite: {missing}")

print(f"\nBuilt {len(chunks)} chunks from SQLite:")
for c in chunks:
    print(f"  {c['chunk_id']:40s}  {c['title'][:60]}")

# ── embed with Cohere ─────────────────────────────────────────────────────────

print(f"\nEmbedding {len(chunks)} chunks via Cohere (input_type=search_document)...")
reset_api_call_count()
t0 = time.time()
texts = [c["text"] for c in chunks]
embeddings = embed_documents(texts)
elapsed = time.time() - t0
api_calls_used = get_api_call_count()

print(f"  Returned {len(embeddings)} embeddings in {elapsed:.1f}s")
print(f"  API calls used: {api_calls_used}")

succeeded = sum(1 for e in embeddings if e)
failed = sum(1 for e in embeddings if not e)
print(f"  Succeeded: {succeeded}  Failed: {failed}")

if not embeddings or not any(embeddings):
    print("ERROR: All embeddings failed — check COHERE_API_KEY and connectivity")
    sys.exit(1)

if len(embeddings) != len(chunks):
    print(f"ERROR: Embedding count mismatch: chunks={len(chunks)} embeddings={len(embeddings)}")
    sys.exit(1)

# Verify dimension
for i, emb in enumerate(embeddings):
    if emb and len(emb) != 1024:
        print(f"ERROR: Dimension mismatch at index {i}: expected 1024, got {len(emb)}")
        sys.exit(1)
print(f"  Dimension check: all embeddings are {len([e for e in embeddings if e][0])}-dim ✓")

# ── upsert into pgvec ─────────────────────────────────────────────────────────

print(f"\nConnecting to pgvec: {DATABASE_URL}")
pg = psycopg.connect(DATABASE_URL, autocommit=True)
cur = pg.cursor()

# Ensure schema
cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
cur.execute(
    """
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
    )
    """
)
cur.execute(
    """
    CREATE INDEX IF NOT EXISTS idx_ask_retrieval_chunks_embedding
    ON ask_retrieval_chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64)
    """
)

# Verify schema is unchanged before truncating
cur.execute(
    """
    SELECT column_name, data_type, udt_name
    FROM information_schema.columns
    WHERE table_name = 'ask_retrieval_chunks' AND column_name = 'embedding'
    """
)
col_info = cur.fetchone()
print(f"\nTask 3 — schema check:")
print(f"  embedding column: type={col_info[1]} udt={col_info[2]}")
cur.execute(
    """
    SELECT indexname, indexdef
    FROM pg_indexes
    WHERE tablename = 'ask_retrieval_chunks' AND indexname LIKE '%embedding%'
    """
)
idx_info = cur.fetchone()
if idx_info:
    print(f"  HNSW index: {idx_info[0]}")
    print(f"  Definition: {idx_info[1]}")

# Truncate and re-insert targeted sample
print(f"\nTruncating ask_retrieval_chunks for clean Cohere-only embedding space...")
cur.execute("TRUNCATE ask_retrieval_chunks")
print("  Truncated.")

upserted = 0
for chunk, embedding in zip(chunks, embeddings):
    if not embedding:
        print(f"  SKIP (no embedding): {chunk['chunk_id']}")
        continue
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
          embedding = EXCLUDED.embedding,
          content_hash = EXCLUDED.content_hash,
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
            _content_hash(chunk),
            _vector_literal(embedding),
        ),
    )
    upserted += 1

print(f"  Upserted {upserted} chunks into pgvec.")

# ── query validation ──────────────────────────────────────────────────────────

QUERY = "Who is Edward Dagg from Aghowle who emigrated on the Dunbrody in 1853?"
print(f"\nEmbedding query (input_type=search_query):\n  \"{QUERY}\"")
q_vec = embed_query(QUERY)
total_api_calls = get_api_call_count()

if not q_vec:
    print("ERROR: Query embedding failed")
    sys.exit(1)
print(f"  Query embedding: {len(q_vec)}-dim ✓")
print(f"  API calls so far: {total_api_calls}")

literal = "[" + ",".join(f"{float(v):.8f}" for v in q_vec) + "]"
cur.execute(
    """
    SELECT
      chunk_id, source_type, title,
      1 - (embedding <=> %s::vector) AS similarity
    FROM ask_retrieval_chunks
    ORDER BY embedding <=> %s::vector
    LIMIT 5
    """,
    (literal, literal),
)
results = cur.fetchall()
pg.close()

print(f"\nTop-5 results (pgvector cosine similarity):")
print(f"  {'Rank':<5} {'Score':<8} {'Chunk ID':<40} {'Title'}")
print(f"  {'-'*4} {'-'*7} {'-'*39} {'-'*50}")
for rank, row in enumerate(results, 1):
    marker = " ← GROUND TRUTH" if row[0] == "emigration:CL8037" else (
             " ← HARD NEGATIVE" if row[0] == "estate_survey:CL53" else ""
    )
    print(f"  {rank:<5} {float(row[3]):<8.4f} {row[0]:<40} {row[2][:50]}{marker}")

# Score gap analysis
if results:
    rank1_score = float(results[0][3])
    rank2_score = float(results[1][3]) if len(results) > 1 else 0.0
    gap = rank1_score - rank2_score
    gt_rank = next((i + 1 for i, r in enumerate(results) if r[0] == "emigration:CL8037"), None)
    print(f"\n  Score gap #1→#2: {gap:.4f}")
    print(f"  Ground truth (emigration:CL8037) rank: {gt_rank or 'NOT IN TOP-5'}")
    if gt_rank == 1:
        print("  PASS — ground truth ranks #1 ✓")
    else:
        print(f"  FAIL — ground truth not at #1 (rank {gt_rank})")

print(f"\n=== Budget summary ===")
print(f"  Total Cohere API calls this run: {total_api_calls}")
print(f"  Chunks embedded: {succeeded}  Failed: {failed}")
print(f"  Path taken: pgvector (dense cosine via <=> operator)")
print("Done.")
