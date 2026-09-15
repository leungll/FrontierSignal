from radar.pipeline.llm_filter import _parse


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
