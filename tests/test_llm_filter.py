import re

from radar.models import ScoreWeights
from radar.pipeline import llm_filter
from radar.pipeline.llm_filter import _parse, pick_candidates
from tests.conftest import make_item


def _row(i, rel=7, imp=5, dep=6, kind="engineering", reason="r"):
    return (
        f'{{"i":{i},"type":"{kind}","rel":{rel},"imp":{imp},"dep":{dep},'
        f'"reason":"{reason}"}}'
    )


def test_parse_extracts_dimensions_and_type():
    text = f'[{_row(0, 9, 8, 5, "release", "模型发布")},{_row(1, 8, 4, 7, "research")}]'
    out = _parse(text, 2)
    assert out[0] == (9, 8, 5, "release", "模型发布")
    assert out[1] == (8, 4, 7, "research", "r")


def test_parse_defaults_bad_type_to_engineering():
    out = _parse(f"[{_row(0, kind='weird')}]", 1)
    assert out[0][3] == "engineering"


def test_parse_drops_rows_missing_a_dimension():
    out = _parse('[{"i":0,"type":"news","rel":5,"imp":5,"reason":"x"}]', 1)
    assert 0 not in out  # no dep: unjudged beats a fabricated score


def test_parse_strips_fence_and_clamps():
    out = _parse(f"```json\n[{_row(0, rel=99, imp=-3)}]\n```", 1)
    assert out[0][:2] == (10, 0)


def test_parse_enforces_promo_impact_ceiling():
    out = _parse(f"[{_row(0, imp=8, kind='promo')}]", 1)
    assert out[0][1] == 2


def test_parse_salvages_truncated_array():
    text = f"[{_row(0, 8)},{_row(1, 6)}," + '{"i":2,"type":"research","rel":7,"imp'
    out = _parse(text, 3)
    assert out[0][0] == 8 and out[1][0] == 6
    assert 2 not in out  # the truncated object is skipped, not guessed


def test_curated_sources_are_always_judged_firehoses_are_budgeted():
    # A launch post has zero keyword score but must still reach the judge; the
    # firehose only sends its best-keyword slice.
    launch = make_item("Introducing Model X", source_id="lab", score=0.0)
    papers = [make_item(f"p{i}", source_id="arxiv", score=float(i)) for i in range(10)]
    picked = pick_candidates([launch, *papers], {"arxiv": 3})
    assert launch in picked
    assert [p.title for p in picked if p.source_id == "arxiv"] == ["p9", "p8", "p7"]


class _Client:
    """Judges every "[N]" index in the payload; records each call's batch size."""

    available = True

    def __init__(self):
        self.calls: list[tuple[int, int]] = []

    def complete(self, *, system, user, model, max_tokens):
        idxs = sorted({int(m) for m in re.findall(r"^\[(\d+)\]", user, re.M)})
        self.calls.append((len(idxs), max_tokens))
        return "[" + ",".join(_row(i, 8, 6, 6) for i in idxs) + "]"


def test_apply_batches_and_sets_combined_score():
    items = [make_item(f"t{i}", source_id="blog") for i in range(60)]
    client = _Client()
    w = ScoreWeights()
    llm_filter.apply(items, client=client, model="m", firehose_budget={}, weights=w)
    assert [n for n, _ in client.calls] == [25, 25, 10]
    assert all(mt > 256 + 100 * n for n, mt in client.calls)  # budget scales
    assert all(i.llm_score == round(0.45 * 8 + 0.35 * 6 + 0.2 * 6, 2) for i in items)


class _FlakyClient(_Client):
    def complete(self, **kw):
        if not self.calls:
            self.calls.append((0, 0))
            raise RuntimeError("boom")
        return super().complete(**kw)


def test_failed_batch_does_not_lose_other_batches():
    items = [make_item(f"t{i}", source_id="blog") for i in range(30)]
    llm_filter.apply(
        items, client=_FlakyClient(), model="m", firehose_budget={}, weights=ScoreWeights()
    )
    judged = [i for i in items if i.llm_score is not None]
    assert len(judged) == 5  # first batch of 25 failed open; second batch judged
