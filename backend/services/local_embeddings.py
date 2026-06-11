"""
backend/services/local_embeddings.py

Local dense embedding provider using BAAI/bge-large-en-v1.5 (SentenceTransformers).

Model: BAAI/bge-large-en-v1.5 — 1024-dim, MIT-licensed, CPU, no API key required.

CRITICAL prefix asymmetry (verified from model card):
  queries  → prepend "Represent this sentence for searching relevant passages: "
  passages → NO prefix; encode raw text as-is
  Both paths use normalize_embeddings=True for unit-norm vectors (cosine = dot).

Hard assertion: output dim must equal BGE_OUTPUT_DIMENSION (1024).  Raises
RuntimeError loudly on first use if the model returns anything else.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

BGE_MODEL_NAME: str = "BAAI/bge-large-en-v1.5"
# Exact query instruction from the model card (for asymmetric retrieval).
BGE_QUERY_INSTRUCTION: str = "Represent this sentence for searching relevant passages: "
BGE_OUTPUT_DIMENSION: int = 1024

_model_lock = threading.Lock()
_model: Any = None


def _allow_model_download() -> bool:
    raw = os.environ.get("LOCAL_EMBEDDINGS_ALLOW_DOWNLOAD", "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _model_cache_exists(model_name: str = BGE_MODEL_NAME) -> bool:
    return _local_model_path(model_name) is not None


def _local_model_path(model_name: str = BGE_MODEL_NAME) -> Path | None:
    """
    Return a fully populated local model directory if one is available.

    We resolve to the actual snapshot path instead of just checking that some
    cache directory exists, because partial Hugging Face cache folders can
    still trigger slow online resolution attempts.
    """
    required = {
        "modules.json",
        "config_sentence_transformers.json",
        "config.json",
        "tokenizer_config.json",
    }

    st_path = Path.home() / ".cache" / "torch" / "sentence_transformers" / model_name
    if st_path.exists() and required.issubset({p.name for p in st_path.iterdir() if p.is_file()}):
        return st_path

    hub_key = f"models--{model_name.replace('/', '--')}"
    hub_roots: list[Path] = []
    hf_home = os.environ.get("HF_HOME", "").strip()
    if hf_home:
        hub_roots.append(Path(hf_home).expanduser() / "hub")
    hub_roots.append(Path.home() / ".cache" / "huggingface" / "hub")

    for root in hub_roots:
        model_root = root / hub_key
        ref_main = model_root / "refs" / "main"
        if ref_main.exists():
            snap_id = ref_main.read_text(encoding="utf-8").strip()
            snap = model_root / "snapshots" / snap_id
            if snap.exists() and required.issubset({p.name for p in snap.iterdir() if p.is_file()}):
                return snap

        snapshots_dir = model_root / "snapshots"
        if snapshots_dir.exists():
            for snap in sorted(snapshots_dir.iterdir(), reverse=True):
                if snap.is_dir() and required.issubset({p.name for p in snap.iterdir() if p.is_file()}):
                    return snap
    return None


def _get_model() -> Any:
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "sentence-transformers is not installed; run: pip install sentence-transformers torch"
            ) from exc

        local_only = not _allow_model_download()
        local_path = _local_model_path()
        if local_only and local_path is None:
            raise RuntimeError(
                f"local embedding model {BGE_MODEL_NAME} is not cached locally; "
                "pre-download it or set LOCAL_EMBEDDINGS_ALLOW_DOWNLOAD=true."
            )

        if local_only:
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

        log.info(
            "local_embeddings.loading model=%s device=cpu local_only=%s",
            BGE_MODEL_NAME,
            local_only,
        )
        model_source = str(local_path) if local_only and local_path is not None else BGE_MODEL_NAME
        try:
            model = SentenceTransformer(
                model_source,
                device="cpu",
                local_files_only=local_only,
            )
        except TypeError:
            model = SentenceTransformer(model_source, device="cpu")

        # Hard dimension assertion — fail loudly rather than silently inserting wrong-dim vectors.
        probe = model.encode(["dim-probe"], normalize_embeddings=True, show_progress_bar=False)
        actual_dim = int(len(probe[0]))
        if actual_dim != BGE_OUTPUT_DIMENSION:
            raise RuntimeError(
                f"local_embeddings.dimension_mismatch model={BGE_MODEL_NAME} "
                f"expected={BGE_OUTPUT_DIMENSION} actual={actual_dim}"
            )

        log.info("local_embeddings.ready model=%s dim=%d", BGE_MODEL_NAME, actual_dim)
        _model = model
        return _model


def embed_texts_local(
    texts: list[str],
    input_type: str = "document",
) -> list[list[float]]:
    """
    Embed texts using the local bge-large-en-v1.5 model.

    input_type "query"    → prepend BGE_QUERY_INSTRUCTION (asymmetric retrieval convention)
    input_type "document" → no prefix; encode raw text as-is

    Returns list of 1024-dim L2-normalised float vectors.  Returns empty inner
    lists on encode failure rather than raising (same contract as Cohere path).
    """
    if not texts:
        return []

    try:
        model = _get_model()
        if input_type == "query":
            texts_to_encode = [BGE_QUERY_INSTRUCTION + t for t in texts]
        else:
            texts_to_encode = list(texts)
        import torch
        with torch.no_grad():
            vecs = model.encode(
                texts_to_encode,
                normalize_embeddings=True,
                show_progress_bar=False,
                batch_size=128,
            )
        return [v.tolist() for v in vecs]
    except Exception as exc:
        log.warning("local_embeddings.encode_failed error=%s", exc)
        return [[] for _ in texts]
