"""Claude relevance filter — the semantic gate the keyword pass can't be.

Keyword scoring (radar.pipeline.score) is a cheap pre-filter: free, fast, and
good at throwing out obvious noise. But it can't tell "a new KV-cache paper that
matters for LLM serving" from "a marketing post that happens to say 'inference'".

This stage sends the keyword-survivors to Haiku in one batched call and gets back
a 0-10 relevance score + one-line reason per item, judged against the engineer's
interest profile. Items below `llm_min_relevance` are dropped from the report.

Cost: one Haiku call per run over ~40 short items — fractions of a cent/day.
Fails open: if the API errors, we fall back to the keyword score so a bad key or
an outage still produces a (less precise) report instead of nothing.
"""

from __future__ import annotations

import json

import structlog

from radar.llm import LLMClient
from radar.models import Item

log = structlog.get_logger()

# The engineer's profile, verbatim from the project brief. Kept here (not in YAML)
# because it's a prompt, not config the user tunes source-by-source.
PROFILE = """\
You are triaging AI news for a SENIOR SOFTWARE ENGINEER who wants to stay current
on frontier AI ENGINEERING. They care about depth and production reality, not hype.

STRONGLY INTERESTED IN (score high):
- AI agents, agent runtimes, agentic loops/harnesses
- AI infrastructure, LLM serving, inference optimization (vLLM, SGLang, TensorRT)
- Evaluation / evals, context engineering, memory, MCP, tool calling
- AI systems, distributed systems for AI, AI coding, production AI
- Self-improving AI, long-running AI applications
- Technical blogs, research papers, real production experience, notable OSS releases

NOT INTERESTED IN (score low, near 0):
- Startup funding, valuations, acquisitions, IPOs
- Product announcements, consumer AI apps, general AI news
- AI investment news, AI politics/regulation, listicles, marketing

LANDMARK OVERRIDE (score high even if it's off the engineering topics above):
  If an item is a genuine MILESTONE from a frontier lab or top researcher — a major
  scientific result (e.g. a hard math/physics problem advanced by AI), a step-change
  in capability, a significant new model/system, or a clear industry turning point —
  score it 8-10. A landmark is worth the engineer's attention even when it doesn't
  match their day-to-day engineering keywords. This is NOT a license for hype:
  ordinary product launches, incremental papers, and marketing do NOT qualify.

Score 0-10 for how much THIS engineer should read it:
  8-10 = frontier engineering insight, OR a genuine landmark, they'd regret missing
  6-7  = solid, relevant, worth a look
  3-5  = tangential or shallow
  0-2  = noise / off-topic / marketing
"""

_SYSTEM = (
    PROFILE
    + "\n\nWrite \"reason\" in concise Simplified Chinese, keeping English technical"
    " terms in English (agent, inference, MCP, RAG, eval, KV-cache, …). Put a space"
    " between Chinese and English/numbers (如「agent 一致性」), use full-width"
    " Chinese punctuation, and don't stack terms with「+」.\n"
    "Return ONLY a JSON array, one object per item, same order as given:\n"
    '[{"i": <index>, "score": <0-10 int>, "reason": "<最多约20字>"}]\n'
    "No prose, no code fence."
)


def _payload(items: list[Item]) -> str:
    rows = []
    for idx, it in enumerate(items):
        summary = it.summary.strip().replace("\n", " ")[:400]
        rows.append(f"[{idx}] ({it.source_name}) {it.title}\n{summary}")
    return "\n\n".join(rows)


def _parse(text: str, n: int) -> dict[int, tuple[int, str]]:
    """Tolerant parse: strip a stray fence, clamp scores, ignore junk rows."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1].removeprefix("json").strip()
    out: dict[int, tuple[int, str]] = {}
    for row in json.loads(text):
        i = int(row["i"])
        if 0 <= i < n:
            out[i] = (max(0, min(10, int(row["score"]))), str(row.get("reason", ""))[:120])
    return out


def _pick_candidates(
    items: list[Item], max_candidates: int, trusted_authority: float
) -> list[Item]:
    """Choose which items Claude judges.

    Two lanes:
      1. Fast lane — every item from a TRUSTED source (authority >= threshold) is
         judged no matter its keyword score. This is why a landmark post from
         Anthropic/OpenAI/DeepMind that matches none of our engineering keywords
         (e.g. a Riemann-Hypothesis result) still reaches Claude instead of being
         buried under the keyword ranking.
      2. Keyword lane — the rest fill the remaining budget by keyword score.

    Cost stays bounded: trusted sources emit only a handful of posts/day.
    """
    trusted = [i for i in items if i.authority >= trusted_authority]
    trusted_urls = {i.canonical_url for i in trusted}

    rest = sorted(
        (i for i in items if i.canonical_url not in trusted_urls),
        key=lambda i: i.score,
        reverse=True,
    )
    budget = max(0, max_candidates - len(trusted))
    return trusted + rest[:budget]


def apply(
    items: list[Item],
    *,
    client: LLMClient,
    model: str,
    max_candidates: int,
    trusted_authority: float = 0.9,
) -> list[Item]:
    """Judge relevance in one batched LLM call. Mutates and returns `items`.

    Candidates = every trusted-source item (fast lane) + top keyword-scored items
    up to `max_candidates`. The rest keep llm_relevance=None and are excluded from
    the report downstream.
    """
    if not items:
        return items
    if not client.available:
        log.warning("llm_filter.no_llm", note="skipping semantic filter, using keyword score")
        return items

    candidates = _pick_candidates(items, max_candidates, trusted_authority)

    try:
        text = client.complete(
            system=_SYSTEM, user=_payload(candidates), model=model, max_tokens=2048
        )
        scores = _parse(text, len(candidates))
    except Exception as exc:  # fail open — a report beats no report
        log.warning("llm_filter.failed", error=str(exc))
        return items

    for idx, it in enumerate(candidates):
        if idx in scores:
            it.llm_relevance, it.llm_reason = scores[idx]

    judged = sum(1 for i in candidates if i.llm_relevance is not None)
    log.info("llm_filter.done", judged=judged, model=model)
    return items
