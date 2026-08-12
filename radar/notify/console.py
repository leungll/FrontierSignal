"""Console notifier — prints the report to stdout. Zero config, zero deps.

The default when no channel is set up: lets anyone run the pipeline and see a real
report without a webhook. Also handy for local debugging.
"""

from __future__ import annotations

from radar.notify.base import render_markdown, render_weekly_markdown, test_message
from radar.report import Report, WeeklyReport


class ConsoleNotifier:
    name = "console"

    def send_test(self, *, language: str = "zh") -> None:
        print("\n" + test_message(language) + "\n")

    def send(self, report: Report, *, language: str = "zh") -> None:
        print("\n" + render_markdown(report, language) + "\n")

    def send_weekly(self, report: WeeklyReport, *, language: str = "zh") -> None:
        print("\n" + render_weekly_markdown(report, language) + "\n")
