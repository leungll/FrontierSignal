from collections import Counter

from radar.models import Interests
from radar.pipeline.score import assign_priority, select_for_report
from tests.conftest import make_item


def _interests(**kw):
    base = dict(max_report_items=8, floor_report_items=3)
    base.update(kw)
    return Interests(**base)


def test_llm_relevance_gates_selection():
    items = [
        make_item("a", score=5.0, llm_relevance=3),
        make_item("b", score=0.5, llm_relevance=9),
        make_item("c", score=2.0, llm_relevance=7),
    ]
    sel = select_for_report(items, _interests(), llm_min_relevance=6)
    assert [i.title for i in sel] == ["b", "c"]  # rel 3 excluded


def test_keyword_fallback_when_unjudged():
    items = [make_item("e", score=2.0), make_item("f", score=0.3), make_item("g", score=1.5)]
    sel = select_for_report(items, _interests(min_score=1.0), llm_min_relevance=6)
    assert {i.title for i in sel} == {"e", "g"}


def test_source_cap_is_hard_wall():
    items = [make_item(f"ax{i}", source_id="arxiv", llm_relevance=8) for i in range(20)]
    items += [make_item(s, source_id=s, llm_relevance=7) for s in ("blogA", "blogB", "blogC")]
    sel = select_for_report(items, _interests(source_caps={"arxiv": 3}), llm_min_relevance=6)
    c = Counter(i.source_id for i in sel)
    assert c["arxiv"] == 3  # capped, not padded with more papers
    assert len(sel) == 6


def test_cap_floor_relaxation_prevents_empty():
    items = [make_item(f"a{i}", source_id="arxiv", llm_relevance=8) for i in range(15)]
    sel = select_for_report(
        items, _interests(source_caps={"arxiv": 3}, floor_report_items=3), llm_min_relevance=6
    )
    assert len(sel) == 3  # floor relaxes the cap so it isn't empty


def test_priority_split_and_coverage_bonus():
    items = [
        make_item("a", llm_relevance=7, cluster_id=0),
        make_item("b", llm_relevance=9, authority=1.0, cluster_id=1),
        make_item("d", llm_relevance=8, cluster_id=3),
        make_item("e", llm_relevance=7, cluster_id=3),  # shares cluster 3
    ]
    ranked = assign_priority(items, p0_count=2, cluster_sizes={0: 1, 1: 1, 3: 2})
    p0 = [i.title for i in ranked if i.priority == "P0"]
    assert len(p0) == 2
    assert "b" in p0  # trusted + rel 9 tops
