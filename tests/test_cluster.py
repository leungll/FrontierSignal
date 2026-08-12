import numpy as np

from radar.pipeline import cluster
from radar.pipeline.embed import content_hash
from tests.conftest import make_item


def _orthonormal_pair():
    v1 = np.zeros(384, dtype=np.float32)
    v1[:192] = 1
    v1 /= np.linalg.norm(v1)
    v2 = np.zeros(384, dtype=np.float32)
    v2[192:] = 1
    v2 /= np.linalg.norm(v2)
    return v1, v2


def test_semantic_dedup_merges_and_keeps_best_rank():
    a = make_item("vLLM 1.0 release", source_name="blog", llm_relevance=9)
    b = make_item("vLLM version 1.0 out", source_name="hn", llm_relevance=7)
    c = make_item("Grothendieck paper", source_name="arxiv", llm_relevance=8)
    v1, v2 = _orthonormal_pair()
    vectors = {content_hash(a): v1, content_hash(b): v1, content_hash(c): v2}

    survivors = cluster.dedupe_semantic([a, b, c], vectors, threshold=0.85)
    assert len(survivors) == 2
    rep = next(s for s in survivors if s.title == "vLLM 1.0 release")
    assert [m["name"] for m in rep.merged_sources] == ["hn"]  # lower-rank dup merged in


def test_clustering_separates_distinct_topics():
    a = make_item("topic one")
    c = make_item("topic two")
    v1, v2 = _orthonormal_pair()
    vectors = {content_hash(a): v1, content_hash(c): v2}
    cluster.cluster_topics([a, c], vectors, threshold=0.62)
    assert a.cluster_id != c.cluster_id


def test_edge_cases():
    assert cluster.dedupe_semantic([], {}) == []
    one = make_item("solo")
    v1, _ = _orthonormal_pair()
    out = cluster.cluster_topics([one], {content_hash(one): v1})
    assert out[0].cluster_id == 0


def test_cluster_sizes_counts_coverage():
    items = [
        make_item("x", cluster_id=1),
        make_item("y", cluster_id=1),
        make_item("z", cluster_id=2),
    ]
    sizes = cluster.cluster_sizes(items)
    assert sizes == {1: 2, 2: 1}
