"""Opus summarization for the handful of items that make the report.

The filter (Haiku) decides WHAT gets in; this stage (Opus) decides how it's
PRESENTED. Only the ~5-8 survivors reach here, so Opus is affordable: one call
summarizes all of them together, keeping a consistent voice and letting the model
see the day's items as a set (useful once trend/clustering lands in Phase 2).

Each item gets a tightened one-line summary and a "why it matters" line aimed at
a senior engineer. Fails open: on any error we keep the feed's own summary.
"""

from __future__ import annotations

import json

import structlog

from radar.i18n import prompt_lang
from radar.llm import LLMClient
from radar.models import Item

log = structlog.get_logger()

_SYSTEM_TMPL = """\
You write a daily AI-engineering digest for a senior software engineer.
For each item you are given, produce:
  - "summary": ONE crisp sentence on what it actually is (no hype, no "this article").
  - "why": ONE sentence on why it matters to a frontier AI engineer specifically —
    the engineering insight, tradeoff, or capability shift. Skip if genuinely nothing.

Do NOT translate the article title; only summary/why follow the language below.
{lang}

Return ONLY a JSON array, same order as input:
[{{"i": <index>, "summary": "...", "why": "..."}}]
No prose, no code fence."""


def _system(language: str) -> str:
    return _SYSTEM_TMPL.format(lang=prompt_lang(language))


def _payload(items: list[Item]) -> str:
    rows = []
    for idx, it in enumerate(items):
        body = it.summary.strip().replace("\n", " ")[:600]
        rows.append(f"[{idx}] ({it.source_name}) {it.title}\n{body}")
    return "\n\n".join(rows)


def _parse(text: str, n: int) -> dict[int, tuple[str, str]]:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1].removeprefix("json").strip()
    out: dict[int, tuple[str, str]] = {}
    for row in json.loads(text):
        i = int(row["i"])
        if 0 <= i < n:
            out[i] = (str(row.get("summary", ""))[:400], str(row.get("why", ""))[:400])
    return out


def apply(
    items: list[Item], *, client: LLMClient, model: str, language: str = "zh"
) -> list[Item]:
    """Rewrite summaries + add 'why it matters'. Mutates and returns `items`."""
    if not items or not client.available:
        return items

    try:
        text = client.complete(
            system=_system(language), user=_payload(items), model=model, max_tokens=4096
        )
        parsed = _parse(text, len(items))
    except Exception as exc:  # fail open — keep feed summaries
        log.warning("summarize.failed", error=str(exc))
        return items

    for idx, it in enumerate(items):
        if idx in parsed:
            summary, why = parsed[idx]
            if summary:
                it.summary = summary
            it.why_it_matters = why

    log.info("summarize.done", count=len(parsed), model=model)
    return items
