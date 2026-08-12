import httpx
import respx

from radar.config import Settings
from radar.models import RunStats
from radar.notify.base import render_markdown, render_weekly_markdown
from radar.notify.factory import get_notifier
from radar.notify.webhook_channels import (
    DiscordNotifier,
    SlackNotifier,
    TelegramNotifier,
    _chunks,
)
from radar.report import Report, WeeklyReport
from tests.conftest import make_item


def _report():
    return Report(
        date="2026-08-12",
        stats=RunStats(found=100, new=100, reported=2),
        items=[
            make_item("P0 one", llm_relevance=9, priority="P0", summary="内容", url="https://a"),
            make_item("P1 two", llm_relevance=7, priority="P1", summary="more", url="https://b"),
        ],
        trend="agent 更成熟。",
        reading="**#1** — 读它。",
    )


def test_render_markdown_has_sections_and_links():
    md = render_markdown(_report(), "zh")
    assert "P0 one" in md and "https://a" in md
    assert "P1 two" in md
    assert "今日趋势" in md and "阅读推荐" in md


def test_render_markdown_english():
    md = render_markdown(_report(), "en")
    assert "Must-read" in md and "Today's" in md


def test_weekly_markdown():
    wr = WeeklyReport(date="d", sections={"trends": "trend body"}, order=[("trends", "## Trends")])
    md = render_weekly_markdown(wr, "en")
    assert "## Trends" in md and "trend body" in md


def test_chunks_splits_long_text_on_blank_lines():
    text = "\n\n".join(f"block {i} " + "x" * 50 for i in range(20))
    parts = _chunks(text, 200)
    assert len(parts) > 1
    assert all(len(p) <= 200 for p in parts)
    # nothing lost
    assert "block 0" in parts[0] and "block 19" in parts[-1]


def test_factory_selects_each_channel():
    cases = {
        "console": ("console", {}),
        "lark": ("lark", {"lark_webhook_url": "https://open.feishu.cn/x"}),
        "slack": ("slack", {"slack_webhook_url": "https://hooks.slack.com/x"}),
        "discord": ("discord", {"discord_webhook_url": "https://discord.com/x"}),
        "telegram": ("telegram", {"telegram_bot_token": "t", "telegram_chat_id": "c"}),
    }
    for expected, (notifier, extra) in cases.items():
        n = get_notifier(Settings(notifier=notifier, **extra))
        assert n.name == expected


def test_factory_falls_back_to_console_when_unconfigured():
    n = get_notifier(Settings(notifier="slack", slack_webhook_url=""))
    assert n.name == "console"


def test_slack_posts_text():
    with respx.mock:
        route = respx.post("https://hooks.slack.com/x").mock(return_value=httpx.Response(200))
        SlackNotifier("https://hooks.slack.com/x").send(_report())
        assert '"text"' in route.calls[0].request.content.decode()


def test_slack_connection_test_is_not_an_empty_report():
    with respx.mock:
        route = respx.post("https://hooks.slack.com/x").mock(return_value=httpx.Response(200))
        SlackNotifier("https://hooks.slack.com/x").send_test(language="en")
        body = route.calls[0].request.content.decode()
        assert "connection successful" in body
        assert "Found" not in body


def test_discord_posts_content():
    with respx.mock:
        route = respx.post("https://discord.com/x").mock(return_value=httpx.Response(200))
        DiscordNotifier("https://discord.com/x").send(_report())
        assert '"content"' in route.calls[0].request.content.decode()


def test_telegram_posts_chat_id():
    url = "https://api.telegram.org/botTOK/sendMessage"
    with respx.mock:
        route = respx.post(url).mock(return_value=httpx.Response(200))
        TelegramNotifier("TOK", "CHAT").send(_report())
        body = route.calls[0].request.content.decode()
        assert "chat_id" in body and "CHAT" in body
