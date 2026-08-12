from radar.notify.cn_typography import normalize


def test_space_between_cjk_and_ascii():
    assert normalize("agent系统") == "agent 系统"
    assert normalize("Meta发布Muse") == "Meta 发布 Muse"


def test_halfwidth_comma_adjacent_to_cjk_becomes_fullwidth():
    assert "，" in normalize("方法,在异步 inference 中")
    assert "," not in normalize("方法,在异步")


def test_english_list_comma_preserved():
    # a half-width comma between latin tokens must stay half-width
    assert normalize("RLVR, RLHF 与 agentic") == "RLVR, RLHF 与 agentic"


def test_ratio_colon_in_english_preserved():
    assert normalize("ratio 8:1 here") == "ratio 8:1 here"


def test_drops_space_between_cjk():
    assert normalize("反思 内化") == "反思内化"


def test_url_untouched():
    u = "见 https://arxiv.org/abs/2601.12345 论文"
    assert "https://arxiv.org/abs/2601.12345" in normalize(u)


def test_idempotent():
    once = normalize("agent系统,重要")
    assert normalize(once) == once


def test_empty():
    assert normalize("") == ""
