# Copyright (c) 2026 Ravenkey LLC. All rights reserved.
from __future__ import annotations

import numpy as np

_MODEL = None
_MODEL_NAME = "all-MiniLM-L6-v2"


def _get_model():
    global _MODEL
    if _MODEL is None:
        from sentence_transformers import SentenceTransformer
        _MODEL = SentenceTransformer(_MODEL_NAME)
    return _MODEL


def generate_embedding(text: str) -> list[float]:
    model = _get_model()
    emb = model.encode(text, normalize_embeddings=True)
    return emb.tolist()


def generate_batch_embeddings(texts: list[str]) -> list[list[float]]:
    model = _get_model()
    embs = model.encode(texts, normalize_embeddings=True, show_progress_bar=True)
    return [e.tolist() for e in embs]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    return float(np.dot(a, b))
