"""
scripts/validate_ann_scale.py

Task 6 — ANN-at-scale validation after full corpus ingest.

Covers:
  1. EXPLAIN ANALYZE — confirm planner auto-selects HNSW
  2. recall@10 — ANN (index) vs exact (seqscan) for 3 queries
  3. Relevance/correctness — ground-truth queries rank #1
  4. Latency — with vs without index

Run from project root after bulk_ingest_local.py completes:
    DATABASE_URL=postgresql://postgres:pw@localhost:5432/postgres \
    EMBEDDING_PROVIDER=local \
    python scripts/validate_ann_scale.py
"""
from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:pw@localhost:5432/postgres")
os.environ.setdefault("EMBEDDING_PROVIDER", "local")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import warnings
warnings.filterwarnings("ignore")

import psycopg

# ─── helpers ─────────────────────────────────────────────────────────────────

DSN = "postgresql://postgres:pw@localhost:5432/postgres"


def _embed_query(text: str) -> list[float]:
    import torch
    torch.set_num_threads(4)
    from sentence_transformers import SentenceTransformer
    from backend.services.local_embeddings import BGE_MODEL_NAME, BGE_QUERY_INSTRUCTION
    model = SentenceTransformer(BGE_MODEL_NAME, device="cpu")
    model.eval()
    with torch.no_grad():
        v = model.encode([BGE_QUERY_INSTRUCTION + text], normalize_embeddings=True, show_progress_bar=False)
    return v[0].tolist()


def _vec_literal(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.8f}" for x in v) + "]"


def _ann_query(cur, vec_lit: str, top_k: int = 10) -> list[tuple]:
    cur.execute(
        """
        SELECT chunk_id, title, 1-(embedding <=> %s::vector) AS sim
        FROM ask_retrieval_chunks
        ORDER BY embedding <=> %s::vector
        LIMIT %s
        """,
        (vec_lit, vec_lit, top_k),
    )
    return cur.fetchall()


def _exact_query(cur, vec_lit: str, top_k: int = 10) -> list[tuple]:
    cur.execute("SET enable_indexscan = off; SET enable_bitmapscan = off;")
    cur.execute(
        """
        SELECT chunk_id, title, 1-(embedding <=> %s::vector) AS sim
        FROM ask_retrieval_chunks
        ORDER BY embedding <=> %s::vector
        LIMIT %s
        """,
        (vec_lit, vec_lit, top_k),
    )
    rows = cur.fetchall()
    cur.execute("RESET enable_indexscan; RESET enable_bitmapscan;")
    return rows


# ─── main ────────────────────────────────────────────────────────────────────

def main() -> None:
    print("\n" + "═" * 70)
    print("TASK 6 — ANN-at-scale validation")
    print("═" * 70)

    conn = psycopg.connect(DSN)
    conn.autocommit = True
    cur = conn.cursor()

    # Row count
    cur.execute("SELECT COUNT(*) FROM ask_retrieval_chunks")
    total_rows = cur.fetchone()[0]
    print(f"\nTotal rows in ask_retrieval_chunks: {total_rows:,}")

    # ── 1. EXPLAIN ANALYZE ────────────────────────────────────────────────────
    print("\n─── 1. EXPLAIN ANALYZE (auto HNSW selection) ───")
    sample_vec_lit = _vec_literal([0.01] * 1024)
    cur.execute(
        f"EXPLAIN ANALYZE SELECT chunk_id FROM ask_retrieval_chunks "
        f"ORDER BY embedding <=> '{sample_vec_lit}'::vector LIMIT 10"
    )
    plan_rows = cur.fetchall()
    for r in plan_rows:
        print(r[0])

    # ── 2. recall@10: ANN vs exact for 3 queries ─────────────────────────────
    print("\n─── 2. Recall@10: ANN (HNSW) vs Exact (seqscan) ───")

    eval_queries = [
        ("Who is Edward Dagg from Aghowle?",         "person:EDWARD DAGG:AGHOWLE"),
        ("Edward Dagg estate survey in Aghold",      "person:EDWARD'S EXECUTORS DAGG:AGHOLD"),
        ("Ballindaggan townland place passport",     "place:3921"),
    ]

    ef_values = [40, 100, 200]
    final_ef = 40

    for query_text, expected_id in eval_queries:
        print(f"\n  Query: {query_text!r}")
        vec = _embed_query(query_text)
        vec_lit = _vec_literal(vec)

        # Exact top-10
        exact_rows = _exact_query(cur, vec_lit, top_k=10)
        exact_ids = [r[0] for r in exact_rows]

        # ANN top-10 with different ef values
        best_recall = 0.0
        best_ef = 40
        for ef in ef_values:
            cur.execute(f"SET hnsw.ef_search = {ef}")
            ann_rows = _ann_query(cur, vec_lit, top_k=10)
            ann_ids = [r[0] for r in ann_rows]
            recall = len(set(ann_ids) & set(exact_ids)) / 10.0
            print(f"    ef_search={ef:<4} recall@10={recall:.2f}  "
                  f"{'✓' if recall >= 0.95 else '⚠ below threshold'}")
            if recall > best_recall:
                best_recall = recall
                best_ef = ef
            if recall >= 0.95:
                final_ef = max(final_ef, ef)
                break

        # Report top-5 ANN at best_ef
        cur.execute(f"SET hnsw.ef_search = {best_ef}")
        top5 = _ann_query(cur, vec_lit, top_k=5)
        print(f"    Top-5 ANN (ef={best_ef}):")
        for rank, (cid, title, sim) in enumerate(top5, 1):
            marker = " ← EXPECTED" if cid == expected_id else ""
            print(f"      #{rank} sim={sim:.4f}  {cid!r}  {title!r}{marker}")

        # Relevance check
        ann_top5_ids = [r[0] for r in top5]
        if ann_top5_ids[0] == expected_id:
            print(f"    ✓ Expected chunk ranks #1")
        else:
            rank_in_ann = (ann_top5_ids.index(expected_id) + 1) if expected_id in ann_top5_ids else "not in top-5"
            print(f"    ✗ Expected chunk {expected_id!r} rank: {rank_in_ann}")

    print(f"\n  Final ef_search value used: {final_ef}")

    # ── 3. Latency: with index vs without ────────────────────────────────────
    print("\n─── 3. Latency: HNSW index vs seqscan (indicative) ───")
    bench_vec = _embed_query("How many people were evicted from Aghowle?")
    bench_lit = _vec_literal(bench_vec)

    cur.execute("SET hnsw.ef_search = 40")
    t0 = time.perf_counter()
    for _ in range(5):
        _ann_query(cur, bench_lit, top_k=10)
    ann_ms = (time.perf_counter() - t0) * 200  # avg per query

    t0 = time.perf_counter()
    for _ in range(5):
        _exact_query(cur, bench_lit, top_k=10)
    exact_ms = (time.perf_counter() - t0) * 200

    print(f"  HNSW (ef=40)  avg: {ann_ms:.1f} ms/query")
    print(f"  Seqscan exact avg: {exact_ms:.1f} ms/query")
    print(f"  Speedup: {exact_ms/ann_ms:.1f}×  (local container, indicative)")

    # ── 4. Final index definition ─────────────────────────────────────────────
    print("\n─── 4. Final HNSW index definition ───")
    cur.execute(
        "SELECT indexname, indexdef FROM pg_indexes "
        "WHERE tablename='ask_retrieval_chunks' AND indexname='idx_ask_retrieval_chunks_embedding'"
    )
    row = cur.fetchone()
    if row:
        print(f"  {row[0]}:")
        print(f"  {row[1]}")
    else:
        print("  ✗ HNSW index not found!")

    conn.close()
    print("\n" + "═" * 70)
    print("Validation complete.")
    print("═" * 70)


if __name__ == "__main__":
    main()
