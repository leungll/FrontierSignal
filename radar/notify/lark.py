"""Lark webhook client.

Verified constraints from the Lark custom-bot docs (open.feishu.cn, 2026):
  - request body <= 30 KB (hard)
  - 100 calls/min, 5 calls/sec per bot
  - custom bots fully support Card JSON 2.0 (schema "2.0"), incl. collapsible_panel
  - HMAC-SHA256 signing: key is "{timestamp}\\n{secret}", message is EMPTY
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import httpx
import structlog

from radar.i18n import labels
from radar.models import Item, RunStats
from radar.notify.cn_typography import normalize as _cn_normalize

log = structlog.get_logger()

MAX_BYTES = 29_000  # 1KB headroom under Lark's 30KB limit


def _typo(text: str, language: str) -> str:
    """CJK typography normalization for Chinese; pass-through for other languages."""
    return _cn_normalize(text) if language == "zh" else text


def sign(timestamp: str, secret: str) -> str:
    """Note the unusual construction: the concatenated string is the HMAC *key*
    and the message is empty. This is what Lark specifies."""
    key = f"{timestamp}\n{secret}".encode()
    return base64.b64encode(hmac.new(key, b"", hashlib.sha256).digest()).decode()


def _md_item(item: Item, lb: dict[str, str], language: str, index: int) -> str:
    summary = _typo(item.summary.strip(), language)
    if len(summary) > 280:
        summary = summary[:277].rstrip() + "…"

    meta = item.source_name
    if item.llm_relevance is not None:
        meta += f" · relevance {item.llm_relevance}/10"

    lines = [
        f"**{index}. [{item.title}]({item.url})**",
        meta,
    ]
    if summary:
        lines.append(summary)
    if item.why_it_matters.strip():
        why = _typo(item.why_it_matters.strip(), language)
        separator = "  " if language == "zh" else " "
        lines.append(f"**{lb['why']}**{separator}{why}")
    if item.merged_sources:
        also = " · ".join(f"[{s['name']}]({s['url']})" for s in item.merged_sources[:4])
        lines.append(f"_{lb['also_seen']}:_ {also}")
    return "\n".join(lines)


def _md_section(items: list[Item], lb: dict[str, str], language: str, start: int = 1) -> str:
    return "\n\n---\n\n".join(
        _md_item(item, lb, language, index) for index, item in enumerate(items, start)
    )


def _collapsible(title: str, content: str, expanded: bool = False) -> dict:
    """A Card 2.0 collapsible_panel wrapping one markdown block."""
    return {
        "tag": "collapsible_panel",
        "expanded": expanded,
        "header": {"title": {"tag": "markdown", "content": title}},
        "elements": [{"tag": "markdown", "content": content}],
    }


def build_card(
    items: list[Item],
    stats: RunStats,
    date_str: str,
    *,
    trend: str = "",
    reading: str = "",
    language: str = "zh",
) -> dict:
    """Card JSON v2. P0 shown open, P1 collapsed, with restrained editorial layout."""
    lb = labels(language)
    p0 = [i for i in items if i.priority == "P0"]
    p1 = [i for i in items if i.priority != "P0"]

    stats_line = (
        f"{lb['stat_found']} {stats.found}  ·  "
        f"{lb['stat_filtered']} {stats.filtered}  ·  "
        f"{lb['stat_worth']} {stats.reported}"
    )
    elements: list[dict] = [{"tag": "markdown", "content": stats_line}, {"tag": "hr"}]

    if p0:
        elements.append({"tag": "markdown", "content": lb["p0_head"]})
        for index, item in enumerate(p0, 1):
            elements.append({"tag": "markdown", "content": _md_item(item, lb, language, index)})
            if index < len(p0):
                elements.append({"tag": "hr"})
    if p1:
        elements.append({"tag": "hr"})
        elements.append(
            _collapsible(
                lb["p1_head"].format(n=len(p1)),
                _md_section(p1, lb, language, start=len(p0) + 1),
            )
        )
    if not items:
        elements.append({"tag": "markdown", "content": lb["empty"]})

    if trend.strip():
        elements.append({"tag": "hr"})
        elements.append(
            {
                "tag": "markdown",
                "element_id": "trend_block",
                "content": lb["trend_head"] + "\n\n" + _typo(trend.strip(), language),
            }
        )
    if reading.strip():
        elements.append(
            {
                "tag": "markdown",
                "element_id": "reading_block",
                "content": lb["reading_head"] + "\n\n" + _typo(reading.strip(), language),
            }
        )

    if stats.dead_sources:
        elements.append({"tag": "hr"})
        elements.append(
            {
                "tag": "markdown",
                "content": (
                    lb["dead_head"]
                    + "\n"
                    + ", ".join(f"`{s}`" for s in stats.dead_sources)
                    + "\n\n"
                    + lb["dead_note"]
                ),
            }
        )

    return {
        "msg_type": "interactive",
        "card": {
            "schema": "2.0",
            "header": {
                "title": {"tag": "plain_text", "content": lb["daily_title"]},
                "subtitle": {"tag": "plain_text", "content": date_str},
                "template": "blue",
            },
            "body": {"elements": elements},
        },
    }


def build_weekly_card(
    sections: dict, section_order: list, date_str: str, *, language: str = "zh"
) -> dict:
    """Weekly deep-dive card. Each section is its own markdown block; the later,
    lower-priority ones go in a collapsible panel to keep the card scannable.

    section_order is a list of (key, heading) pairs (radar.summarize.weekly.sections()).
    """
    lb = labels(language)
    open_blocks: list[dict] = []
    collapsible_md: list[str] = []

    # First four sections open, the rest collapsed (they're the reflective ones).
    for idx, (key, heading) in enumerate(section_order):
        body = sections.get(key, "").strip()
        if not body:
            continue
        block_md = f"{heading}\n\n{_typo(body, language)}"
        if idx < 4:
            open_blocks.append({"tag": "markdown", "content": block_md})
        else:
            collapsible_md.append(block_md)

    elements: list[dict] = []
    for b in open_blocks:
        elements.append(b)
        elements.append({"tag": "hr"})
    if elements and elements[-1].get("tag") == "hr":
        elements.pop()
    if collapsible_md:
        elements.append(_collapsible(lb["weekly_more"], "\n\n".join(collapsible_md)))

    return {
        "msg_type": "interactive",
        "card": {
            "schema": "2.0",
            "header": {
                "title": {"tag": "plain_text", "content": lb["weekly_title"]},
                "subtitle": {"tag": "plain_text", "content": date_str},
                "template": "purple",
            },
            "body": {"elements": elements},
        },
    }


def build_test_card(language: str = "zh") -> dict:
    if language == "zh":
        title = "Frontier Signal · 连接成功"
        content = "配置有效。之后的日报和周报将发送到这里。"
    else:
        title = "Frontier Signal · Connection successful"
        content = "Configuration is valid. Daily and weekly reports will be sent here."
    return {
        "msg_type": "interactive",
        "card": {
            "schema": "2.0",
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": "green",
            },
            "body": {"elements": [{"tag": "markdown", "content": content}]},
        },
    }


def _shrink(card: dict) -> bool:
    """Drop the lowest-priority content. Returns False when nothing is left to cut.

    Degradation order: dead-source notice → trim P1 items → drop P1 panel entirely
    → trim P0 items. A busy day must never produce a failed push.
    """
    elements = card["card"]["body"]["elements"]

    # 1. Dead-source notice goes first.
    for idx in range(len(elements) - 1, -1, -1):
        content = elements[idx].get("content", "")
        if content.startswith("**失效源**") or content.startswith("**Dead sources**"):
            del elements[idx]
            if idx - 1 >= 0 and elements[idx - 1].get("tag") == "hr":
                del elements[idx - 1]
            return True

    # 2. Trim trailing items from the collapsible P1 panel, then drop it whole.
    for idx, el in enumerate(elements):
        if el.get("tag") == "collapsible_panel":
            inner = el["elements"][0]
            separator = "\n\n---\n\n"
            if separator in inner["content"]:
                inner["content"] = inner["content"].rsplit(separator, 1)[0]
                return True
            del elements[idx]
            return True

    # 3. Drop trend / reading recommendation (nice-to-have, not core).
    for idx in range(len(elements) - 1, -1, -1):
        if elements[idx].get("element_id") in ("trend_block", "reading_block"):
            del elements[idx]
            if idx - 1 >= 0 and elements[idx - 1].get("tag") == "hr":
                del elements[idx - 1]
            return True

    # 4. Last resort: remove the final P0 item while keeping at least one.
    item_indexes = [
        idx
        for idx, el in enumerate(elements)
        if el.get("tag") == "markdown"
        and el.get("content", "").startswith(tuple(f"**{n}. [" for n in range(1, 10)))
    ]
    if len(item_indexes) > 1:
        idx = item_indexes[-1]
        del elements[idx]
        if idx - 1 >= 0 and elements[idx - 1].get("tag") == "hr":
            del elements[idx - 1]
        return True
    return False


def fit_to_limit(card: dict) -> str:
    payload = json.dumps(card, ensure_ascii=False)
    while len(payload.encode("utf-8")) > MAX_BYTES:
        if not _shrink(card):
            log.warning("lark.cannot_shrink", size=len(payload.encode("utf-8")))
            break
        payload = json.dumps(card, ensure_ascii=False)
    return payload


def send(card: dict, webhook_url: str, secret: str = "", timeout: float = 15.0) -> None:
    fit_to_limit(card)  # mutates card in place

    if secret:
        ts = str(int(time.time()))
        card["timestamp"] = ts
        card["sign"] = sign(ts, secret)

    body = json.dumps(card, ensure_ascii=False).encode("utf-8")
    log.info("lark.sending", bytes=len(body))

    resp = httpx.post(
        webhook_url,
        content=body,
        headers={"Content-Type": "application/json"},
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    # Lark returns HTTP 200 even on logical failure — check the body.
    if data.get("code") not in (0, None):
        raise RuntimeError(f"Lark rejected the message: {data}")
    log.info("lark.sent")


class LarkNotifier:
    """Notifier for Lark/Feishu. Renders a Report as an interactive Card 2.0 with
    a collapsible P1 section — the one channel that uses the rich card format."""

    name = "lark"

    def __init__(self, webhook_url: str, secret: str = "") -> None:
        self.webhook_url = webhook_url
        self.secret = secret

    def send_test(self, *, language: str = "zh") -> None:
        send(build_test_card(language), self.webhook_url, self.secret)

    def send(self, report, *, language: str = "zh") -> None:
        card = build_card(
            report.items,
            report.stats,
            report.date,
            trend=report.trend,
            reading=report.reading,
            language=language,
        )
        send(card, self.webhook_url, self.secret)

    def send_weekly(self, report, *, language: str = "zh") -> None:
        card = build_weekly_card(report.sections, report.order, report.date, language=language)
        send(card, self.webhook_url, self.secret)
