from __future__ import annotations

import json
import logging
import multiprocessing
import os
import sys
import time

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:pw@localhost:5432/postgres")
os.environ.setdefault("EMBEDDING_PROVIDER", "local")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

NUM_WORKERS: int = int(os.environ.get("BULK_NUM_WORKERS", "4"))
THREADS_PER_WORKER: int = int(os.environ.get("BULK_THREADS_PER_WORKER", "2"))
BGE_MODEL_NAME: str = "BAAI/bge-large-en-v1.5"
BGE_QUERY_INSTRUCTION: str = "Represent this sentence for searching relevant passages: "
MINI_BATCH: int = 2000


def _load_quantized_model(num_threads: int = 8):
    import torch
    from torch.quantization import quantize_dynamic
    from sentence_transformers import SentenceTransformer

    torch.set_num_threads(num_threads)
    model = SentenceTransformer(BGE_MODEL_NAME, device="cpu")
    model._first_module().auto_model = quantize_dynamic(
        model._first_module().auto_model,
        {torch.nn.Linear},
        dtype=torch.qint8,
    )
    model.eval()
    return model


def _worker_encode(args: tuple) -> list[list[float]]:
    texts, input_type, num_threads = args

    import warnings
    warnings.filterwarnings("ignore")

    import torch
    torch.set_num_threads(num_threads)

    from sentence_transformers import SentenceTransformer
    from torch.quantization import quantize_dynamic

    model = SentenceTransformer(BGE_MODEL_NAME, device="cpu")
    model._first_module().auto_model = quantize_dynamic(
        model._first_module().auto_model, {torch.nn.Linear}, dtype=torch.qint8
    )
    model.eval()

    if input_type == "query":
        to_encode = [BGE_QUERY_INSTRUCTION + t for t in texts]
    else:
        to_encode = list(texts)

    with torch.no_grad():
        vecs = model.encode(
            to_encode,
            normalize_embeddings=True,
            show_progress_bar=True,
            batch_size=128,
        )
    return [v.tolist() for v in vecs]


def _split(lst: list, n: int) -> list[list]:
    k, rem = divmod(len(lst), n)
    out = []
    start = 0
    for i in range(n):
        end = start + k + (1 if i < rem else 0)
        out.append(lst[start:end])
        start = end
    return out


def run_bulk_ingest() -> None:
    import warnings
    warnings.filterwarnings("ignore")

    from create_app import create_app
    app = create_app()

    from backend.services.ask_pgvector import (
        _connect,
        _content_hash,
        _fetch_existing_hashes,
        _vector_literal,
        ensure_pgvector_schema,
        rebuild_hnsw_index,
    )

    with app.app_context():
        from backend.services.retrieval_chunks import build_retrieval_chunks
        log.info("Building retrieval chunks from SQLite...")
        chunks = build_retrieval_chunks()
        log.info("Chunks built: %d", len(chunks))

    if not chunks:
        log.error("No chunks — aborting")
        return

    schema = ensure_pgvector_schema()
    if schema.get("status") != "completed":
        log.error("Schema setup failed: %s", schema)
        return

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("DROP INDEX IF EXISTS idx_ask_retrieval_chunks_embedding")
    log.info("HNSW index dropped for bulk insert")

    with _connect() as conn:
        with conn.cursor() as cur:
            existing = _fetch_existing_hashes(cur)

    chunk_hashes = {str(c["chunk_id"]): _content_hash(c) for c in chunks}
    changed = [c for c in chunks if existing.get(str(c["chunk_id"])) != chunk_hashes[str(c["chunk_id"])]]
    stale_ids = sorted(set(existing) - set(chunk_hashes))

    log.info("Total: %d | to embed: %d | stale: %d", len(chunks), len(changed), len(stale_ids))

    if stale_ids:
        with _connect() as conn:
            with conn.cursor() as cur:
                for cid in stale_ids:
                    cur.execute("DELETE FROM ask_retrieval_chunks WHERE chunk_id = %s", (cid,))
        log.info("Deleted %d stale rows", len(stale_ids))

    succeeded = failed = 0
    embed_elapsed = 0.0

    if changed:
        import torch
        from torch.quantization import quantize_dynamic
        from sentence_transformers import SentenceTransformer

        torch.set_num_threads(8)
        log.info("Loading int8-quantized bge-large-en-v1.5 (8 threads)…")
        model = SentenceTransformer(BGE_MODEL_NAME, device="cpu")
        model._first_module().auto_model = quantize_dynamic(
            model._first_module().auto_model, {torch.nn.Linear}, dtype=torch.qint8
        )
        model.eval()
        log.info("Model ready. Encoding %d chunks in mini-batches of %d…", len(changed), MINI_BATCH)

        t0 = time.time()
        for mb_start in range(0, len(changed), MINI_BATCH):
            mb = changed[mb_start: mb_start + MINI_BATCH]
            texts = [str(c.get("text") or "") for c in mb]
            with torch.no_grad():
                vecs = model.encode(
                    texts,
                    normalize_embeddings=True,
                    show_progress_bar=False,
                    batch_size=128,
                )
            vecs_list = [v.tolist() for v in vecs]

            with _connect() as conn:
                with conn.cursor() as cur:
                    for chunk, embedding in zip(mb, vecs_list):
                        if not embedding:
                            failed += 1
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
                                chunk_hashes[str(chunk["chunk_id"])],
                                _vector_literal(embedding),
                            ),
                        )
                        succeeded += 1

            pct = (mb_start + len(mb)) / len(changed) * 100
            log.info("Progress: %d/%d (%.0f%%) | succeeded=%d failed=%d",
                     mb_start + len(mb), len(changed), pct, succeeded, failed)

        embed_elapsed = time.time() - t0
        log.info("Encoding+insert done in %.0fs (%.1f min) | succeeded=%d failed=%d",
                 embed_elapsed, embed_elapsed / 60, succeeded, failed)

    idx = rebuild_hnsw_index()
    log.info("HNSW rebuild: %s", idx["status"])

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM ask_retrieval_chunks")
            total_rows = cur.fetchone()[0]

    print("\n=== BULK INGEST COMPLETE ===")
    print(f"status       : {'completed' if failed == 0 else 'completed_with_failures'}")
    print(f"total_chunks : {len(chunks)}")
    print(f"succeeded    : {succeeded}")
    print(f"failed       : {failed}")
    print(f"db_row_count : {total_rows}")
    print(f"embed_time   : {embed_elapsed:.0f}s ({embed_elapsed/60:.1f} min)")
    print(f"hnsw_index   : {idx['status']}")


if __name__ == "__main__":
    multiprocessing.set_start_method("spawn", force=True)
    run_bulk_ingest()
