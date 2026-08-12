"""Weekly deep dive — synthesis, not a news recap.

Runs Sunday over everything that made it into a daily report in the last 7 days.
One Opus call turns the week's items into the sections the brief asks for:
top trends, key papers, key engineering blogs, interesting repos, industry
direction, what's overhyped, what deserves deeper study, next-week reading plan.

The value here is synthesis across the week — spotting the thread that no single
day made obvious. Fails open: on error, returns None and the caller skips the push.
"""

from __future__ import annotations

import json
import sqlite3

import structlog

from radar.i18n import prompt_lang
from radar.llm import LLMClient

log = structlog.get_logger()

_SYSTEM_TMPL = """\
You are writing a WEEKLY deep dive for a senior AI-engineering audience, from the
items that made this week's daily digests. This is SYNTHESIS, not a recap — find
the threads across the week that no single day made obvious.

{lang}

Return ONLY this JSON. Keys are fixed (English); each VALUE is markdown written in
the target language. Cite items by their title when useful.
{{
  "trends": "Top 5 technical trends, ordered markdown list, one line each on why it matters",
  "papers": "important papers this week, markdown list, one line each on the contribution",
  "blogs": "important engineering blogs this week, markdown list",
  "repos": "interesting OSS / framework moves, markdown list (if none, say so plainly)",
  "direction": "industry direction, 2-4 sentences on what the signals point to",
  "overhyped": "what is overhyped, 1-3 sentences, be blunt",
  "deep_study": "what deserves deeper study, 1-3 sentences, name concrete topics",
  "reading_plan": "next week's reading plan, markdown list of 3-5 concrete topics"
}}
No prose, no code fence."""


def _system(language: str) -> str:
    return _SYSTEM_TMPL.format(lang=prompt_lang(language))


# (key, zh_heading, en_heading) — the card builder picks the heading by language.
SECTION_SPECS = [
    ("trends", "## Top 5 技术趋势", "## Top 5 technical trends"),
    ("papers", "## 📄 重要论文", "## 📄 Important papers"),
    ("blogs", "## 重要工程博客", "## Engineering blogs"),
    ("repos", "## 有趣的开源项目", "## Interesting OSS"),
    ("direction", "## 🧭 行业方向", "## 🧭 Industry direction"),
    ("overhyped", "## 🫧 什么被高估", "## 🫧 What's overhyped"),
    ("deep_study", "## 🔬 值得深入学习", "## 🔬 Worth deeper study"),
    ("reading_plan", "## 📚 下周阅读计划", "## 📚 Next week's reading"),
]


def sections(language: str) -> list[tuple[str, str]]:
    """(key, heading) pairs for the given language."""
    i = 2 if language == "en" else 1
    return [(spec[0], spec[i]) for spec in SECTION_SPECS]


def _payload(rows: list[sqlite3.Row]) -> str:
    lines = []
    for r in rows[:80]:  # cap tokens; rows are score-ordered so we keep the best
        s = (r["summary"] or "").strip().replace("\n", " ")[:200]
        lines.append(f"- ({r['source_name']}) {r['title']} — {s}")
    return "\n".join(lines)


def build(
    rows: list[sqlite3.Row], *, client: LLMClient, model: str, language: str = "zh"
) -> dict[str, str] | None:
    """Return {section_key: markdown} or None on skip/failure."""
    if len(rows) < 3 or not client.available:
        log.warning("weekly.insufficient", items=len(rows))
        return None

    try:
        text = client.complete(
            system=_system(language), user=_payload(rows), model=model, max_tokens=4096
        ).strip()
        if text.startswith("```"):
            text = text.split("```")[1].removeprefix("json").strip()
        out = json.loads(text)
        log.info("weekly.done", sections=len(out), model=model)
        return {k: str(v) for k, v in out.items()}
    except Exception as exc:  # fail open
        log.warning("weekly.failed", error=str(exc))
        return None
