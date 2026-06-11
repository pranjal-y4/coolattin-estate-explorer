from __future__ import annotations

import pytest

from backend.services import local_embeddings


def test_get_model_fails_fast_when_cache_is_missing(monkeypatch):
    monkeypatch.setattr(local_embeddings, "_model", None)
    monkeypatch.setattr(local_embeddings, "_allow_model_download", lambda: False)
    monkeypatch.setattr(local_embeddings, "_local_model_path", lambda model_name=None: None)

    with pytest.raises(RuntimeError, match="not cached locally"):
        local_embeddings._get_model()


def test_embed_texts_local_returns_empty_vectors_when_model_load_fails(monkeypatch):
    monkeypatch.setattr(local_embeddings, "_get_model", lambda: (_ for _ in ()).throw(RuntimeError("missing model")))
    assert local_embeddings.embed_texts_local(["hello"], input_type="query") == [[]]
