from radar.pipeline import llm_filter
from radar.pipeline.llm_filter import _parse
from tests.conftest import make_item


def test_parse_extracts_category():
    text = '[{"i":0,"score":9,"cat":"landmark","reason":"数学突破"},'\
           '{"i":1,"score":7,"cat":"engineering","reason":"agent runtime"}]'
    out = _parse(text, 2)
    assert out[0] == (9, "数学突破", "landmark")
    assert out[1] == (7, "agent runtime", "engineering")


def test_parse_defaults_bad_category_to_engineering():
    out = _parse('[{"i":0,"score":5,"cat":"weird","reason":"x"}]', 1)
    assert out[0][2] == "engineering"


def test_parse_missing_category_defaults():
    out = _parse('[{"i":0,"score":5,"reason":"x"}]', 1)
    assert out[0][2] == "engineering"


def test_parse_strips_fence_and_clamps():
    out = _parse('```json\n[{"i":0,"score":99,"cat":"other","reason":"x"}]\n```', 1)
    assert out[0][0] == 10
    assert out[0][2] == "other"


def test_parse_salvages_truncated_array():
    # Model hit the token ceiling mid-array: two complete rows, then a cut-off one.
    # Every complete row must survive; the truncated tail is dropped, not the batch.
    text = (
        '[{"i":0,"score":8,"cat":"engineering","reason":"agent runtime"},'
        '{"i":1,"score":6,"cat":"other","reason":"tangential"},'
        '{"i":2,"score":7,"cat":"landmark","reason":"数学突'
    )
    out = _parse(text, 3)
    assert out[0] == (8, "agent runtime", "engineering")
    assert out[1] == (6, "tangential", "other")
    assert 2 not in out  # the truncated object is skipped, not guessed


class _Client:
    """Records max_tokens and returns a full valid array for the given candidates."""

    available = True

    def __init__(self):
        self.max_tokens = 0

    def complete(self, *, system, user, model, max_tokens):
        self.max_tokens = max_tokens
        # Judge every candidate index that appears as "[N]" in the payload.
        import re

        idxs = sorted({int(m) for m in re.findall(r"\[(\d+)\]", user)})
        rows = ",".join(
            f'{{"i":{i},"score":7,"cat":"engineering","reason":"r"}}' for i in idxs
        )
        return f"[{rows}]"


def test_max_tokens_scales_with_candidate_count():
    # A full ~40-item batch must request far more than the old fixed 2048 ceiling,
    # so the array isn't truncated in the first place.
    items = [make_item(f"t{i}", score=float(40 - i)) for i in range(40)]
    client = _Client()
    llm_filter.apply(items, client=client, model="m", max_candidates=40)
    assert client.max_tokens > 2048
    # Every candidate got judged (no truncation, salvage not even needed).
    assert sum(1 for i in items if i.llm_relevance is not None) == 40
