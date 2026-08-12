"""The Notifier interface + a shared plain-markdown renderer.

A Notifier turns a `Report` into a message on some channel. The protocol is tiny:
`send(report)` and `send_weekly(report)`. Channels that support rich layout (Lark)
override rendering; channels that don't (Telegram, Discord, Console) reuse
`render_markdown` / `render_weekly_markdown` here, which produce clean, scannable
plain markdown — no collapsing, no fancy layout, just the content.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from radar.i18n import labels
from radar.notify.cn_typography import normalize as _cn_normalize
from radar.report import Report, WeeklyReport


@runtime_checkable
class Notifier(Protocol):
    name: str

    def send_test(self, *, language: str = "zh") -> None: ...
    def send(self, report: Report, *, language: str = "zh") -> None: ...
    def send_weekly(self, report: WeeklyReport, *, language: str = "zh") -> None: ...


def _typo(text: str, language: str) -> str:
    return _cn_normalize(text) if language == "zh" else text


def _item_block(item, why_label: str, also_label: str, index: int, language: str) -> str:
    lines = [f"**{index}. {item.title}**", f"{item.source_name} · {item.url}"]
    summary = _typo(item.summary.strip(), language)
    if summary:
        lines.append(summary)
    if item.why_it_matters.strip():
        why = _typo(item.why_it_matters.strip(), language)
        if language == "zh":
            lines.append(f"**{why_label}**  {why}")
        else:
            lines.append(f"**{why_label}:** {why}")
    if item.merged_sources:
        also = " · ".join(f"{s['name']}: {s['url']}" for s in item.merged_sources[:4])
        lines.append(f"_{also_label}:_ {also}")
    return "\n".join(lines)


def render_markdown(report: Report, language: str = "zh") -> str:
    """Plain markdown for any channel without a native card format. No collapsing —
    P0 and P1 are just two headed sections. Content first, chrome minimal."""
    lb = labels(language)
    out: list[str] = [
        f"**{lb['daily_title']}** · {report.date}",
        f"{lb['stat_found']} {report.stats.found} · "
        f"{lb['stat_filtered']} {report.stats.filtered} · "
        f"{lb['stat_worth']} {report.stats.reported}",
    ]

    p0, p1 = report.p0, report.p1
    if p0:
        out.append("\n" + lb["p0_head"])
        for i, it in enumerate(p0, 1):
            out.append(_item_block(it, lb["why"], lb["also_seen"], i, language))
    if p1:
        out.append("\n" + lb["p1_head"].format(n=len(p1)))
        for i, it in enumerate(p1, len(p0) + 1):
            out.append(_item_block(it, lb["why"], lb["also_seen"], i, language))
    if not report.items:
        out.append(lb["empty"])

    if report.trend.strip():
        out.append("\n" + lb["trend_head"])
        out.append(_typo(report.trend.strip(), language))
    if report.reading.strip():
        out.append("\n" + lb["reading_head"])
        out.append(_typo(report.reading.strip(), language))

    return "\n\n".join(out)


def render_weekly_markdown(report: WeeklyReport, language: str = "zh") -> str:
    lb = labels(language)
    out = [f"**{lb['weekly_title']}** · {report.date}"]
    for key, heading in report.order:
        body = report.sections.get(key, "").strip()
        if body:
            out.append(f"\n{heading}\n\n{_typo(body, language)}")
    return "\n".join(out)


def test_message(language: str) -> str:
    if language == "zh":
        return "Frontier Signal 连接成功。日报将发送到这里。"
    return "Frontier Signal connection successful. Daily reports will be sent here."
