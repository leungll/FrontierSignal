"""Slack, Discord, and Telegram notifiers.

All three are simple HTTP-webhook channels. None of them have Lark's collapsible
card, so they render the shared plain markdown (`render_markdown`) — content first,
no fancy layout, which is exactly the goal. Each just wraps that text in the shape
its API expects and POSTs it.

  Slack     — Incoming Webhook, JSON {"text": ...}, mrkdwn.
  Discord   — Webhook, JSON {"content": ...}, standard markdown, 2000-char limit.
  Telegram  — Bot API sendMessage, needs bot token + chat id, MarkdownV2 is
              fiddly so we send plain text with parse_mode omitted.

Long messages are split into chunks under each platform's limit rather than
truncated — a busy day should still deliver in full.
"""

from __future__ import annotations

import httpx
import structlog

from radar.notify.base import render_markdown, render_weekly_markdown, test_message
from radar.report import Report, WeeklyReport

log = structlog.get_logger()


def _chunks(text: str, limit: int) -> list[str]:
    """Split on blank lines so we never cut mid-item, staying under `limit`."""
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for block in text.split("\n\n"):
        piece = block + "\n\n"
        if len(cur) + len(piece) > limit and cur:
            parts.append(cur.rstrip())
            cur = ""
        cur += piece
    if cur.strip():
        parts.append(cur.rstrip())
    return parts


def _post_json(url: str, payload: dict, *, channel: str) -> None:
    resp = httpx.post(url, json=payload, timeout=15.0)
    resp.raise_for_status()
    log.info("notify.sent", channel=channel)


class SlackNotifier:
    name = "slack"
    LIMIT = 39000  # Slack text block soft limit; chunk well under it

    def __init__(self, webhook_url: str) -> None:
        self.webhook_url = webhook_url

    def _send_text(self, text: str) -> None:
        for chunk in _chunks(text, self.LIMIT):
            _post_json(self.webhook_url, {"text": chunk}, channel=self.name)

    def send_test(self, *, language: str = "zh") -> None:
        self._send_text(test_message(language))

    def send(self, report: Report, *, language: str = "zh") -> None:
        self._send_text(render_markdown(report, language))

    def send_weekly(self, report: WeeklyReport, *, language: str = "zh") -> None:
        self._send_text(render_weekly_markdown(report, language))


class DiscordNotifier:
    name = "discord"
    LIMIT = 1900  # Discord hard limit is 2000 chars per message

    def __init__(self, webhook_url: str) -> None:
        self.webhook_url = webhook_url

    def _send_text(self, text: str) -> None:
        for chunk in _chunks(text, self.LIMIT):
            _post_json(self.webhook_url, {"content": chunk}, channel=self.name)

    def send_test(self, *, language: str = "zh") -> None:
        self._send_text(test_message(language))

    def send(self, report: Report, *, language: str = "zh") -> None:
        self._send_text(render_markdown(report, language))

    def send_weekly(self, report: WeeklyReport, *, language: str = "zh") -> None:
        self._send_text(render_weekly_markdown(report, language))


class TelegramNotifier:
    name = "telegram"
    LIMIT = 3900  # Telegram hard limit is 4096 chars per message

    def __init__(self, bot_token: str, chat_id: str) -> None:
        self.url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self.chat_id = chat_id

    def _send_text(self, text: str) -> None:
        # parse_mode omitted: MarkdownV2 requires escaping many chars; plain text
        # is robust and still readable. Links render as bare URLs (clickable in TG).
        for chunk in _chunks(text, self.LIMIT):
            _post_json(
                self.url,
                {"chat_id": self.chat_id, "text": chunk, "disable_web_page_preview": True},
                channel=self.name,
            )

    def send_test(self, *, language: str = "zh") -> None:
        self._send_text(test_message(language))

    def send(self, report: Report, *, language: str = "zh") -> None:
        self._send_text(render_markdown(report, language))

    def send_weekly(self, report: WeeklyReport, *, language: str = "zh") -> None:
        self._send_text(render_weekly_markdown(report, language))
