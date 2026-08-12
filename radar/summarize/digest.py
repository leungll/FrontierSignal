"""Daily wrap-up: today's trend + a 30-minute reading recommendation.

One Opus call over the selected items (with their clusters) produces two short
sections for the bottom of the report:

  trend  — what shifted today and WHY it matters as a direction, not a news recap.
  reading — if the engineer has only 30 minutes, which 2-3 items to read, by number.

Fails open: on any error both come back empty and the report simply omits them.
"""

from __future__ import annotations

import json

import structlog

from radar.i18n import prompt_lang
from radar.llm import LLMClient
from radar.models import Item

log = structlog.get_logger()

_SYSTEM_TMPL = """\
You close out a daily AI-engineering digest for a senior engineer. Given today's
selected items (numbered, with source and one-line summary), write two sections.

{lang}

  "trend": 2-4 sentences on what today's items collectively signal — a direction,
    a recurring theme, a shift. Explain WHY it matters to a frontier AI engineer.
    Do NOT just re-list the news. If items are unrelated, say the day was scattered
    and name the one thread worth watching.

  "reading": if the engineer has only 30 minutes, which 2-3 items (BY THEIR NUMBER)
    should they read, and one clause each on why. Format as a short markdown list
    like "**#2** — reason". Pick for depth and lasting value, not novelty.

Return ONLY JSON: {{"trend": "...", "reading": "..."}}  No prose, no code fence."""


def _system(language: str) -> str:
    return _SYSTEM_TMPL.format(lang=prompt_lang(language))


def _payload(items: list[Item]) -> str:
    rows = []
    for idx, it in enumerate(items, 1):
        s = it.summary.strip().replace("\n", " ")[:200]
        rows.append(f"#{idx} ({it.source_name}, {it.priority}) {it.title} — {s}")
    return "\n".join(rows)


def _parse(text: str) -> tuple[str, str]:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1].removeprefix("json").strip()
    obj = json.loads(text)
    return str(obj.get("trend", "")), str(obj.get("reading", ""))


def build(
    items: list[Item], *, client: LLMClient, model: str, language: str = "zh"
) -> tuple[str, str]:
    """Return (trend_md, reading_md). Empty strings on skip/failure."""
    if len(items) < 2 or not client.available:
        return "", ""

    try:
        text = client.complete(
            system=_system(language), user=_payload(items), model=model, max_tokens=1200
        )
        trend, reading = _parse(text)
        log.info("digest.done", model=model)
        return trend, reading
    except Exception as exc:  # fail open
        log.warning("digest.failed", error=str(exc))
        return "", ""
