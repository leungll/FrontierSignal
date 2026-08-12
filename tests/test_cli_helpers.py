from typer.testing import CliRunner

import radar.cli
from radar.cli import _cron_for, _layout_sample, _secret_prompt, app
from radar.config import REPO_ROOT, settings


def test_cron_beijing_morning():
    # 07:00 at UTC+8 -> 23:00 UTC the day before
    assert _cron_for(7, 8) == "0 23 * * *"


def test_cron_us_east():
    assert _cron_for(9, -5) == "0 14 * * *"


def test_cron_wraps_midnight():
    assert _cron_for(8, 8) == "0 0 * * *"
    assert _cron_for(0, 0) == "0 0 * * *"


def test_cron_half_hour_timezone():
    assert _cron_for(7, 5.5) == "30 1 * * *"


def test_workflows_expose_every_provider_and_notifier_setting():
    required = {
        "LLM_PROVIDER",
        "FILTER_MODEL",
        "SUMMARY_MODEL",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "NOTIFIER",
        "LARK_WEBHOOK_URL",
        "LARK_WEBHOOK_SECRET",
        "SLACK_WEBHOOK_URL",
        "DISCORD_WEBHOOK_URL",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "LANGUAGE",
    }
    for name in ("daily.yml", "weekly.yml"):
        text = (REPO_ROOT / ".github" / "workflows" / name).read_text()
        assert all(f"{key}:" in text for key in required)


def test_preview_uses_temporary_database_and_restores_setting(monkeypatch):
    original = settings.db_path
    observed = []

    def fake_run(*, dry_run, verbose):
        observed.append((dry_run, verbose, settings.db_path))
        assert settings.db_path != original
        assert "frontier-signal-preview-" in str(settings.db_path)

    monkeypatch.setattr(radar.cli, "run", fake_run)
    result = CliRunner().invoke(app, ["preview"])

    assert result.exit_code == 0
    assert observed and observed[0][:2] == (True, False)
    assert settings.db_path == original


def test_secret_prompt_explains_hidden_input(monkeypatch):
    messages = []
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: "secret-value")
    monkeypatch.setattr("typer.secho", lambda message, **kwargs: messages.append(message))
    value = _secret_prompt("API key")
    output = "\n".join(messages)
    assert value == "secret-value"
    assert "Input is hidden" in output
    assert "not displayed" in output
    assert "secret-value" not in output


def test_layout_sample_covers_open_and_collapsed_sections():
    report = _layout_sample("zh")
    assert len(report.p0) == 2
    assert len(report.p1) == 6
    assert report.stats.found == 131
    assert report.stats.reported == 8
    assert report.trend and report.reading


def test_layout_command_renders_console_sample():
    result = CliRunner().invoke(app, ["test-layout", "--channel", "console", "--language", "en"])
    assert result.exit_code == 0
    assert "Agentic Configuration Management" in result.output
    assert "Layout sample sent to console" in result.output
