from radar.summarize.weekly import _link_known_titles, _payload


def _rows():
    return [
        {
            "source_name": "arXiv",
            "title": "On the Fragility of Self-Improving Agents",
            "url": "https://example.com/fragility",
            "summary": "A study of task-order sensitivity.",
        }
    ]


def test_weekly_payload_includes_stable_id_and_url():
    payload = _payload(_rows())
    assert "[item-0]" in payload
    assert "URL: https://example.com/fragility" in payload


def test_weekly_fallback_links_exact_titles():
    body = "- On the Fragility of Self-Improving Agents：揭示任务顺序敏感性。"
    linked = _link_known_titles(body, _rows())
    assert "[On the Fragility of Self-Improving Agents](https://example.com/fragility)" in linked
