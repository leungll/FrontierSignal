"""All language-dependent strings in one place.

Two audiences, one bot: `language="zh"` (Chinese prose keeping English technical
terms) or `language="en"`. Every user-visible label and every prompt's
language-instruction lives here so adding a language is a single edit, not a hunt
across modules.

  LABELS[lang]       — fixed card chrome (section headings, "why it matters", …)
  PROMPT_LANG[lang]  — the paragraph appended to each LLM system prompt telling it
                       which language to write in.
"""

from __future__ import annotations

LABELS: dict[str, dict[str, str]] = {
    "zh": {
        "daily_title": "AI Engineering Daily",
        "weekly_title": "AI Engineering Weekly",
        "stat_found": "发现",
        "stat_filtered": "过滤",
        "stat_worth": "精选",
        "why": "为什么重要",
        "also_seen": "另见",
        "p0_head": "**P0 · 必读**",
        "p1_head": "**P1 · 延伸阅读 · {n} 条**",
        "empty": "_今天没有达到阈值的内容。_",
        "trend_head": "**今日趋势**",
        "reading_head": "**30 分钟阅读推荐**",
        "dead_head": "**失效源**",
        "dead_note": "_连续 3 次以上失败 — 请检查 feed URL。_",
        "weekly_more": "**更多：方向 · 高估 · 深入学习 · 阅读计划**",
    },
    "en": {
        "daily_title": "AI Engineering Daily",
        "weekly_title": "AI Engineering Weekly",
        "stat_found": "Found",
        "stat_filtered": "Filtered",
        "stat_worth": "Worth reading",
        "why": "Why it matters",
        "also_seen": "Also",
        "p0_head": "**P0 · Must-read**",
        "p1_head": "**P1 · Further reading · {n}**",
        "empty": "_Nothing cleared the bar today._",
        "trend_head": "**Today's trend**",
        "reading_head": "**If you have 30 minutes**",
        "dead_head": "**Dead sources**",
        "dead_note": "_3+ consecutive failures — check the feed URL._",
        "weekly_more": "**More: direction · overhyped · study · reading plan**",
    },
}

# Appended to each LLM system prompt. Keeps the CJK typography rules with the
# Chinese instruction so they travel together.
PROMPT_LANG: dict[str, str] = {
    "zh": (
        "WRITE IN SIMPLIFIED CHINESE (简体中文), keeping established English "
        "technical terms in English — do NOT force awkward translations (agent, "
        "inference, LLM serving, KV-cache, MCP, RAG, eval, context window, "
        "embedding, distillation, throughput, latency, rollout, GUI, prompt, "
        "token, benchmark, and product/library names like vLLM, SGLang, Llama). "
        "Mixed Chinese-English is expected. Write natural, concise engineering "
        "Chinese, not a literal translation. Put ONE space between Chinese "
        "characters and adjacent English/numbers. Use full-width Chinese "
        "punctuation (，。、；：). Do NOT stack terms with「+」. No emoji."
    ),
    "en": (
        "WRITE IN ENGLISH. Concise, technical, no marketing tone, no hype, "
        "no emoji. Write for a senior engineer who values signal over length."
    ),
}


def labels(language: str) -> dict[str, str]:
    return LABELS.get(language, LABELS["zh"])


def prompt_lang(language: str) -> str:
    return PROMPT_LANG.get(language, PROMPT_LANG["zh"])
