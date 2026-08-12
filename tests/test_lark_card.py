import json

from radar.models import RunStats
from radar.notify import lark
from tests.conftest import make_item


def _items():
    items = [
        make_item(
            "P0 item",
            llm_relevance=9,
            priority="P0",
            summary="内容 summary",
            url="https://a",
        ),
        make_item("P1 item", llm_relevance=7, priority="P1", summary="another", url="https://b"),
    ]
    items[0].why_it_matters = "重要原因"
    return items


def test_card_is_schema_2_with_p0_and_collapsible_p1():
    card = lark.build_card(_items(), RunStats(found=100, new=100, reported=2), "2026-08-12")
    assert card["msg_type"] == "interactive"
    assert card["card"]["schema"] == "2.0"
    tags = [e.get("tag") for e in card["card"]["body"]["elements"]]
    assert "collapsible_panel" in tags
    panel = next(e for e in card["card"]["body"]["elements"] if e.get("tag") == "collapsible_panel")
    assert panel["expanded"] is False
    p0_blocks = [
        e
        for e in card["card"]["body"]["elements"]
        if e.get("tag") == "markdown" and e.get("content", "").startswith("**1. [")
    ]
    assert len(p0_blocks) == 1
    assert "Src · relevance 9/10" in p0_blocks[0]["content"]
    assert "**为什么重要**" in p0_blocks[0]["content"]
    assert "<text_tag" not in p0_blocks[0]["content"]


def test_language_switches_labels():
    items = _items()
    zh = lark.build_card(items, RunStats(found=1, new=1, reported=2), "d", language="zh")
    en = lark.build_card(items, RunStats(found=1, new=1, reported=2), "d", language="en")
    zh_md = "\n".join(e.get("content", "") for e in zh["card"]["body"]["elements"])
    en_md = "\n".join(e.get("content", "") for e in en["card"]["body"]["elements"])
    assert "必读" in zh_md and "Must-read" in en_md


def test_sign_is_valid_base64():
    import base64

    sig = lark.sign("1699999999", "secret")
    base64.b64decode(sig)  # raises if invalid


def test_fit_to_limit_under_max():
    card = lark.build_card(_items(), RunStats(found=1, new=1, reported=2), "d")
    payload = lark.fit_to_limit(card)
    assert len(payload.encode()) < lark.MAX_BYTES
    json.loads(payload)  # still valid JSON


def test_empty_report_renders():
    card = lark.build_card([], RunStats(found=0, new=0, reported=0), "d")
    assert card["card"]["schema"] == "2.0"


def test_connection_test_card_has_no_report_stats():
    card = lark.build_test_card("en")
    payload = json.dumps(card)
    assert "Connection successful" in payload
    assert "Found" not in payload
    assert card["card"]["header"]["template"] == "green"


def test_daily_card_contains_no_emoji():
    card = lark.build_card(_items(), RunStats(found=2, new=2, reported=2), "d")
    payload = json.dumps(card, ensure_ascii=False)
    assert not any(symbol in payload for symbol in "🧠📊🔴🟡📈⏱️⚠️")


def test_daily_card_uses_plain_editorial_layout():
    card = lark.build_card(
        _items(),
        RunStats(found=24, new=24, reported=2),
        "d",
        trend="trend",
        reading="reading",
    )
    elements = card["card"]["body"]["elements"]
    assert elements[0]["tag"] == "markdown"
    assert elements[0]["content"] == "发现 24  ·  过滤 22  ·  精选 2"
    assert not any(e.get("tag") == "column_set" for e in elements)
    assert {e.get("element_id") for e in elements} >= {"trend_block", "reading_block"}
