"""Select a notifier from settings."""

from __future__ import annotations

import structlog

log = structlog.get_logger()


def get_notifier(settings):
    """Build the configured notifier. Falls back to console if the chosen channel
    isn't configured, so a run always delivers *somewhere* (and never crashes on a
    missing webhook)."""
    choice = (settings.notifier or "console").lower()

    if choice == "lark" and settings.lark_webhook_url:
        from radar.notify.lark import LarkNotifier

        return LarkNotifier(settings.lark_webhook_url, settings.lark_webhook_secret)

    if choice == "slack" and settings.slack_webhook_url:
        from radar.notify.webhook_channels import SlackNotifier

        return SlackNotifier(settings.slack_webhook_url)

    if choice == "discord" and settings.discord_webhook_url:
        from radar.notify.webhook_channels import DiscordNotifier

        return DiscordNotifier(settings.discord_webhook_url)

    if choice == "telegram" and settings.telegram_bot_token and settings.telegram_chat_id:
        from radar.notify.webhook_channels import TelegramNotifier

        return TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)

    if choice != "console":
        log.warning("notify.not_configured", channel=choice, note="falling back to console")

    from radar.notify.console import ConsoleNotifier

    return ConsoleNotifier()
