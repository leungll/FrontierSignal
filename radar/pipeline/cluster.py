"""Semantic dedup + topic clustering over embedded items.

Two operations, both on the unit-normalized bge-small vectors (dot = cosine):

  dedupe_semantic — collapse items that are the SAME STORY seen through different
    sources (a vLLM release covered by the blog, HN, and an arXiv link). URL-level
    dedup already ran; this catches same-content-different-URL. The survivor keeps
    every merged source as extra links.

  cluster_topics — group the (deduped) items into topics via agglomerative
    clustering on cosine distance. Feeds P0/P1 (a story covered across many sources
    is more important) and the "today's trend" section.
"""

from __future__ import annotations

import numpy as np
import structlog

from radar.models import Item

log = structlog.get_logger()

DEDUP_THRESHOLD = 0.85    # cosine >= this ⇒ same story
CLUSTER_THRESHOLD = 0.62  # cosine >= this ⇒ same topic (looser than dedup)


def _rank_key(item: Item) -> tuple[float, float]:
    return (item.llm_relevance if item.llm_relevance is not None else -1, item.score)


def dedupe_semantic(
    items: list[Item], vectors: dict[str, np.ndarray], threshold: float = DEDUP_THRESHOLD
) -> list[Item]:
    """Merge near-duplicate items. Returns survivors, best-ranked kept as the
    representative with merged_sources populated.

    Greedy: walk items best-first; each either joins an existing survivor it's
    near-duplicate of (>= threshold) or becomes a new survivor.
    """
    from radar.pipeline.embed import content_hash

    ordered = sorted(items, key=_rank_key, reverse=True)
    survivors: list[Item] = []
    survivor_vecs: list[np.ndarray] = []

    for it in ordered:
        v = vectors.get(content_hash(it))
        if v is None:
            survivors.append(it)
            survivor_vecs.append(np.zeros(1, dtype=np.float32))
            continue

        merged = False
        for rep, rv in zip(survivors, survivor_vecs, strict=True):
            if rv.shape == v.shape and float(rv @ v) >= threshold:
                # Record the merged item's source as an extra link on the rep.
                rep.merged_sources.append({"name": it.source_name, "url": it.url})
                merged = True
                break
        if not merged:
            survivors.append(it)
            survivor_vecs.append(v)

    dropped = len(items) - len(survivors)
    if dropped:
        log.info("dedupe_semantic.done", kept=len(survivors), merged=dropped)
    return survivors


def cluster_topics(
    items: list[Item], vectors: dict[str, np.ndarray], threshold: float = CLUSTER_THRESHOLD
) -> list[Item]:
    """Assign each item a cluster_id (int). Mutates and returns items.

    Single item or missing vectors ⇒ each its own cluster. Uses agglomerative
    clustering with a cosine distance cutoff so we don't need to pick k.
    """
    from radar.pipeline.embed import content_hash

    vecs = [vectors.get(content_hash(i)) for i in items]
    valid = [(i, v) for i, v in zip(items, vecs, strict=True) if v is not None]

    if len(valid) < 2:
        for idx, it in enumerate(items):
            it.cluster_id = idx
        return items

    matrix = np.vstack([v for _, v in valid])
    from sklearn.cluster import AgglomerativeClustering

    labels = AgglomerativeClustering(
        n_clusters=None,
        metric="cosine",
        linkage="average",
        distance_threshold=1.0 - threshold,
    ).fit_predict(matrix)

    for (it, _), label in zip(valid, labels, strict=True):
        it.cluster_id = int(label)
    # Items without vectors get unique negative ids so they never merge.
    next_id = int(labels.max()) + 1 if len(labels) else 0
    for it in items:
        if it.cluster_id is None:
            it.cluster_id = next_id
            next_id += 1

    n_clusters = len({it.cluster_id for it in items})
    log.info("cluster_topics.done", items=len(items), clusters=n_clusters)
    return items


def cluster_sizes(items: list[Item]) -> dict[int, int]:
    """cluster_id -> number of items in it (cross-source coverage signal)."""
    sizes: dict[int, int] = {}
    for it in items:
        if it.cluster_id is not None:
            sizes[it.cluster_id] = sizes.get(it.cluster_id, 0) + 1
    return sizes
