"""Local embeddings via bge-small — zero marginal cost, no API key.

Runs sentence-transformers/BAAI/bge-small-en-v1.5 (384-dim) on CPU. Used for
semantic dedup (merge the same story covered by blog + HN + arXiv) and topic
clustering. Embeddings are cached in SQLite by content hash so re-runs and the
cross-day window don't recompute.

The model is loaded lazily and once per process (it costs ~1-8s to load), so the
whole batch is encoded in a single call.
"""

from __future__ import annotations

import hashlib

import numpy as np
import structlog

from radar.models import Item

log = structlog.get_logger()

MODEL_NAME = "BAAI/bge-small-en-v1.5"
DIM = 384

_model = None  # lazy singleton


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        log.info("embed.loading_model", model=MODEL_NAME)
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def content_hash(item: Item) -> str:
    """Stable key for caching an item's embedding. Title carries most of the
    signal and is stable; summaries sometimes get rewritten, so key on title."""
    return hashlib.sha256(item.title.strip().lower().encode()).hexdigest()[:16]


def _embed_text(item: Item) -> str:
    # bge recommends a short instruction prefix for retrieval; for clustering/dedup
    # plain text is fine. Combine title + a little summary for richer signal.
    return f"{item.title.strip()}. {item.summary.strip()[:300]}"


def embed_items(
    items: list[Item], cache: dict[str, list[float]] | None = None
) -> dict[str, np.ndarray]:
    """Return {content_hash: unit-normalized vector} for every item.

    `cache` (hash -> vector) is consulted first; only cache-misses are encoded.
    Callers persist new vectors back to SQLite. Vectors are L2-normalized so a
    dot product IS cosine similarity.
    """
    cache = cache or {}
    out: dict[str, np.ndarray] = {}
    to_encode: list[Item] = []

    for it in items:
        h = content_hash(it)
        if h in out:
            continue
        if h in cache:
            out[h] = np.asarray(cache[h], dtype=np.float32)
        else:
            to_encode.append(it)

    if to_encode:
        model = _get_model()
        vecs = model.encode(
            [_embed_text(i) for i in to_encode],
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        for it, v in zip(to_encode, vecs, strict=True):
            out[content_hash(it)] = np.asarray(v, dtype=np.float32)
        log.info("embed.done", encoded=len(to_encode), cached=len(items) - len(to_encode))

    return out
