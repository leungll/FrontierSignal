from collections import Counter

from radar.models import Interests, ScoreWeights
from radar.pipeline.score import (
    assign_priority,
    dedupe_in_batch,
    is_eligible,
    item_score,
    select_for_report,
)
from tests.conftest import make_item


def _interests(**kw):
    base = dict(max_report_items=8, floor_report_items=3)
    base.update(kw)
    return Interests(**base)


def test_item_score_is_weighted_sum():
    w = ScoreWeights(relevance=0.45, impact=0.35, depth=0.20)
    assert item_score(10, 10, 10, w) == 10.0
    assert item_score(8, 4, 7, w) == round(0.45 * 8 + 0.35 * 4 + 0.2 * 7, 2)


def test_big_launch_and_deep_writeup_both_qualify():
    # The rubric's point: an item qualifies EITHER by being a big development
    # (high impact, modest depth) OR by being substantive (modest impact, high
    # depth). Neither dimension alone should be required.
    w = ScoreWeights()
    launch = item_score(9, 8, 5, w)   # frontier model launch post
    writeup = item_score(9, 5, 8, w)  # detailed engineering write-up
    incremental = item_score(7, 3, 5, w)  # on-topic but minor and thin
    promo = item_score(6, 2, 2, w)
    floor = Interests().min_report_score
    assert launch >= floor and writeup >= floor
    assert incremental < floor and promo < floor


def test_score_gates_selection():
    items = [
        make_item("a", score=5.0, llm_score=3.0),
        make_item("b", score=0.5, llm_score=9.0),
        make_item("c", score=2.0, llm_score=7.0),
    ]
    sel = select_for_report(items, _interests(floor_report_items=0))
    assert [i.title for i in sel] == ["b", "c"]  # 3.0 excluded


def test_relevance_gate_with_milestone_override():
    it = _interests(min_relevance=5, milestone_impact=9)
    off_topic = make_item("off", llm_score=6.5, llm_relevance=3, llm_impact=8)
    milestone = make_item("ms", llm_score=6.5, llm_relevance=3, llm_impact=9)
    assert not is_eligible(off_topic, it)
    assert is_eligible(milestone, it)


def test_zero_type_cap_excludes_type():
    it = _interests(type_caps={"promo": 0})
    assert not is_eligible(make_item("p", llm_score=8.0, llm_type="promo"), it)


def test_floor_backfills_from_below_bar_but_not_junk():
    # Only one item clears 6.0; the floor of 3 takes the best items >= 5.0, and
    # never the 3.0 one.
    items = [
        make_item("top", llm_score=7.5),
        make_item("ok1", llm_score=5.8),
        make_item("ok2", llm_score=5.2),
        make_item("junk", llm_score=3.0),
    ]
    sel = select_for_report(items, _interests(min_report_score=6.0, floor_min_score=5.0))
    assert [i.title for i in sel] == ["top", "ok1", "ok2"]


def test_floor_never_takes_items_below_floor_min():
    items = [make_item("top", llm_score=7.5), make_item("junk", llm_score=3.0)]
    sel = select_for_report(items, _interests(min_report_score=6.0, floor_min_score=5.0))
    assert [i.title for i in sel] == ["top"]


def test_keyword_fallback_when_unjudged():
    items = [make_item("e", score=2.0), make_item("f", score=0.3), make_item("g", score=1.5)]
    sel = select_for_report(items, _interests(min_score=1.0))
    assert {i.title for i in sel} == {"e", "g"}


def test_source_cap_is_hard_wall():
    items = [make_item(f"ax{i}", source_id="arxiv", llm_score=8.0) for i in range(20)]
    items += [make_item(s, source_id=s, llm_score=7.0) for s in ("blogA", "blogB", "blogC")]
    sel = select_for_report(items, _interests(source_caps={"arxiv": 3}))
    c = Counter(i.source_id for i in sel)
    assert c["arxiv"] == 3  # capped, not padded with more papers
    assert len(sel) == 6


def test_type_cap_reserves_slots_for_other_types():
    # 5 strong launches + 4 engineering write-ups; cap releases at 2 so launches
    # don't crowd out the engineering content even though they score higher.
    items = [make_item(f"rel{i}", llm_score=9.0, llm_type="release") for i in range(5)]
    items += [make_item(f"eng{i}", llm_score=7.0) for i in range(4)]
    sel = select_for_report(items, _interests(type_caps={"release": 2}))
    c = Counter(i.llm_type for i in sel)
    assert c["release"] == 2
    assert c["engineering"] == 4


def test_type_cap_relaxes_to_reach_floor():
    items = [make_item(f"rel{i}", llm_score=9.0, llm_type="release") for i in range(5)]
    sel = select_for_report(items, _interests(type_caps={"release": 2}, floor_report_items=3))
    assert len(sel) == 3  # floor honored despite the type cap


def test_cap_floor_relaxation_prevents_empty():
    items = [make_item(f"a{i}", source_id="arxiv", llm_score=8.0) for i in range(15)]
    sel = select_for_report(items, _interests(source_caps={"arxiv": 3}, floor_report_items=3))
    assert len(sel) == 3


def test_priority_split_and_coverage_bonus():
    items = [
        make_item("a", llm_score=7.0, cluster_id=0),
        make_item("b", llm_score=9.0, authority=1.0, cluster_id=1),
        make_item("d", llm_score=8.0, cluster_id=3),
        make_item("e", llm_score=7.0, cluster_id=3),  # shares cluster 3
    ]
    ranked = assign_priority(items, p0_count=2, cluster_sizes={0: 1, 1: 1, 3: 2})
    p0 = [i.title for i in ranked if i.priority == "P0"]
    assert len(p0) == 2
    assert "b" in p0  # trusted + highest score tops


def test_no_body_items_sink_below_readable_ones():
    # A high-scoring item we couldn't fetch text for must not take a P0 slot over
    # lower-scoring items that have a real body.
    high_no_body = make_item("nobody", llm_score=10.0)
    low_a = make_item("a", llm_score=6.0)
    low_b = make_item("b", llm_score=5.0)
    items = [high_no_body, low_a, low_b]

    ranked = assign_priority(items, p0_count=2, no_body={id(high_no_body)})
    p0 = [i.title for i in ranked if i.priority == "P0"]

    assert "nobody" not in p0
    assert set(p0) == {"a", "b"}
    assert ranked[-1].title == "nobody"


def test_dedupe_prefers_publisher_over_aggregator_on_tie():
    hn = make_item("Sonnet 5.5", source_id="hackernews", score=0.0, authority=0.5,
                   url="https://anthropic.com/claude-sonnet-5-5")
    pub = make_item("Introducing Claude Sonnet 5.5", source_id="anthropic", score=0.0,
                    authority=1.0, url="https://anthropic.com/claude-sonnet-5-5")
    assert [i.source_id for i in dedupe_in_batch([hn, pub])] == ["anthropic"]
