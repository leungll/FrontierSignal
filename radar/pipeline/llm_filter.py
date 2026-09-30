"""LLM relevance filter — the semantic gate the keyword pass can't be.

Keyword scoring (radar.pipeline.score) is a cheap pre-filter for the high-volume
"firehose" sources (arXiv, Hacker News): free, fast, and good at throwing out
obvious noise. But it can't tell "a new KV-cache paper that matters for LLM
serving" from "a marketing post that happens to say 'inference'", and it scores a
model launch ("Introducing Claude Sonnet 5.5") as zero.

This stage sends candidates to the cheap model in batches and gets back three
independent 0-10 judgments per item — relevance, impact, depth — plus a content
type. The combined score is computed in code (radar.pipeline.score.item_score),
not by the model, so the ranking rule is explicit and tunable in interests.yaml
instead of hidden in one holistic number.

Why three dimensions: a single "how much should they read it" score conflates
*what it's about* with *how big it is* with *how substantive the text is*. That
made a major model launch (short post, huge impact) lose to an incremental paper
(long abstract, on-topic keywords). Scoring them separately lets both kinds of
must-know items surface on their own merits.

Cost: a few cheap-model calls per run over ~60-80 short items — cents per day.
Fails open: if the API errors, we fall back to the keyword score so a bad key or
an outage still produces a (less precise) report instead of nothing.
"""

from __future__ import annotations

import json
import re

import structlog

from radar.llm import LLMClient
from radar.models import Item, ScoreWeights
from radar.pipeline.score import item_score

log = structlog.get_logger()

# The rubric. Kept here (not in YAML) because it's a prompt, not config the user
# tunes source-by-source. The anchors describe *classes* of content, never
# specific products, so the rubric generalizes to things we haven't seen yet.
RUBRIC = """\
You are triaging AI news for a SENIOR SOFTWARE ENGINEER who builds AI systems in
production (agents, LLM applications, AI infrastructure). Their daily digest must
tell them what changed in the field that affects their work, and point them to the
best technical material. They care about depth and production reality, not hype.

Judge each item on THREE INDEPENDENT dimensions (0-10 integers). Score each one on
its own anchors — do not let one dimension inflate or deflate another.

rel — RELEVANCE: how directly does the subject bear on building, running, or
evaluating AI systems?
  9-10 core: agents and agent harnesses, AI coding tools, LLM serving/inference,
       evals, context engineering/memory, tool use/MCP, and the frontier models and
       developer platforms engineers build on (capabilities, APIs, pricing, limits)
  6-8  adjacent: training/post-training methods, retrieval, AI systems/hardware,
       multimodal models, safety/security ENGINEERING (red-teaming methods,
       monitoring, sandboxing)
  3-5  AI, but remote from engineering practice: domain applications (biology,
       robotics, math), theory, AI policy/safety/ethics discourse, consumer features
  0-2  not about AI systems: business, funding, general tech, off-topic

imp — IMPACT: how much does the underlying development change what practitioners
know or can do, and how widely? Judge the development, not the article: a short
post about a major launch still has high impact.
  9-10 field-level shift, a few times a year: a new frontier model generation from
       a leading lab, a result that changes what AI can do
  7-8  notable: a significant model or platform release from a major lab, a major
       release of a widely used tool/framework, a result likely to change common
       practice
  4-6  useful but incremental: a typical good paper, a notable open model or
       tool, a meaningful product update
  0-3  minor or local: small updates, niche results, recaps with nothing new
  Calibrate: impact is scarce. A single paper is imp 4-6 unless it clearly shows
  a large, broadly applicable result; being on-topic is relevance, not impact. In
  a typical batch most items should land at imp 5 or below.

dep — DEPTH: how much concrete technical substance does THIS text carry?
  9-10 methods, architecture, data, code and quantitative results a reader can
       learn from or reproduce
  6-8  real specifics: benchmarks, API/pricing details, design decisions, measured
       lessons
  3-5  some specifics mixed with announcement
  0-2  announcement only, promo, a quote, opinion without evidence
  When the snippet is thin, judge rel/imp from title and source, and keep dep low.

type — what kind of content it is:
  "release"     a new model, product, API, or open-source version being launched
  "research"    a paper or research result
  "engineering" a technical write-up: how something was built, benchmark analysis,
                post-mortem, in-depth tutorial
  "news"        industry/business/policy events, outages, event recaps
  "opinion"     essays, commentary, quotes, interviews, podcasts
  "promo"       marketing, contests, programs, customer stories and case
                studies ("X does Y faster with <model>", even when it names a
                new model), partnerships, sponsorships, hiring

Hard rules:
  - "promo" items: imp <= 2.
  - "opinion" with no new facts: imp <= 4.
"""

_SYSTEM = (
    RUBRIC
    + "\nWrite \"reason\" in concise Simplified Chinese, keeping English technical"
    " terms in English (agent, inference, MCP, RAG, eval, KV-cache, …). Put a space"
    " between Chinese and English/numbers (如「agent 一致性」), use full-width"
    " Chinese punctuation, and don't stack terms with「+」.\n"
    "Return ONLY a JSON array, one object per item, same order as given:\n"
    '[{"i": <index>, "type": "<type>", "rel": <0-10>, "imp": <0-10>, "dep": <0-10>,'
    ' "reason": "<最多约20字>"}]\n'
    "No prose, no code fence."
)


def _payload(items: list[Item]) -> str:
    rows = []
    for idx, it in enumerate(items):
        summary = it.summary.strip().replace("\n", " ")[:400]
        rows.append(f"[{idx}] ({it.source_name}) {it.title}\n{summary}")
    return "\n\n".join(rows)


TYPES = {"release", "research", "engineering", "news", "opinion", "promo"}

# One judged object: {"i": N, "type": "...", "rel": N, ...}. Used to salvage
# complete rows when the array is truncated (see _rows).
_OBJ_RE = re.compile(r"\{[^{}]*\}")

# Judge in batches this size. One giant call over 80 items is more likely to be
# truncated or to drift in calibration toward the end of the list.
BATCH_SIZE = 25


def _rows(text: str) -> list[dict]:
    """Parse the model's JSON array, tolerating a truncated tail.

    The happy path is a clean `json.loads`. But each call judges a batch of items,
    and if the model hits the token ceiling mid-array the JSON is
    unterminated — a single `json.loads` then throws and we'd lose every judgment,
    including the dozens of complete rows before the cut. So on failure we fall back
    to extracting each complete `{...}` object individually and keep those.
    """
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1].removeprefix("json").strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        rows: list[dict] = []
        for m in _OBJ_RE.finditer(text):
            try:
                rows.append(json.loads(m.group()))
            except json.JSONDecodeError:
                continue  # the truncated final object — skip it
        return rows


def _clamp(v: object) -> int:
    return max(0, min(10, int(v)))


def _parse(text: str, n: int) -> dict[int, tuple[int, int, int, str, str]]:
    """Tolerant parse: strip a stray fence, clamp scores, ignore junk rows.

    Returns {index: (rel, imp, dep, type, reason)}. A row missing any dimension is
    dropped rather than guessed — an unjudged item is safer than a fabricated score.
    """
    out: dict[int, tuple[int, int, int, str, str]] = {}
    for row in _rows(text):
        try:
            i = int(row["i"])
            rel, imp, dep = _clamp(row["rel"]), _clamp(row["imp"]), _clamp(row["dep"])
        except (KeyError, TypeError, ValueError):
            continue
        if not 0 <= i < n:
            continue
        kind = str(row.get("type", "engineering"))
        if kind not in TYPES:
            kind = "engineering"
        # Enforce the rubric's hard rules in code too; the cheap model doesn't
        # always follow them.
        if kind == "promo":
            imp = min(imp, 2)
        out[i] = (rel, imp, dep, kind, str(row.get("reason", ""))[:120])
    return out


def pick_candidates(items: list[Item], firehose_budget: dict[str, int]) -> list[Item]:
    """Choose which items the LLM judges.

    Curated sources (anything not in `firehose_budget`) publish a handful of posts
    a day, each deliberately chosen by a lab or a writer, so every one is judged —
    the keyword score never decides whether a curated post is seen. That's what
    lets a launch post with zero engineering keywords still be judged.

    Firehose sources (arXiv, Hacker News) produce dozens of items a run; for each,
    only the top `budget` by keyword score are judged, which keeps cost bounded and
    keeps one firehose from monopolizing the judge.
    """
    curated = [i for i in items if i.source_id not in firehose_budget]
    picked = list(curated)
    for source_id, budget in firehose_budget.items():
        pool = sorted(
            (i for i in items if i.source_id == source_id),
            key=lambda i: i.score,
            reverse=True,
        )
        picked.extend(pool[:budget])
    return picked


def apply(
    items: list[Item],
    *,
    client: LLMClient,
    model: str,
    firehose_budget: dict[str, int],
    weights: ScoreWeights,
) -> list[Item]:
    """Judge candidates in batched LLM calls. Mutates and returns `items`.

    Judged items get llm_relevance/impact/depth/type/reason and the combined
    llm_score. Unpicked items (low-keyword firehose items) keep llm_score=None and
    are excluded from the report downstream. A failed batch only loses that
    batch's judgments; the others still count.
    """
    if not items:
        return items
    if not client.available:
        log.warning("llm_filter.no_llm", note="skipping semantic filter, using keyword score")
        return items

    candidates = pick_candidates(items, firehose_budget)
    for start in range(0, len(candidates), BATCH_SIZE):
        _judge_batch(candidates[start : start + BATCH_SIZE], client, model, weights)

    judged = sum(1 for i in candidates if i.llm_score is not None)
    log.info("llm_filter.done", candidates=len(candidates), judged=judged, model=model)
    return items


def _judge_batch(
    batch: list[Item], client: LLMClient, model: str, weights: ScoreWeights
) -> None:
    # Size the output budget to the batch: each row is a small JSON object plus a
    # ~20-char Chinese reason, ~110 tokens with headroom. A fixed low ceiling
    # truncates the array; the cap stops a pathological batch from blowing up cost.
    # The 2048 base is headroom for thinking, which models like Sonnet 5.5 do by
    # default and which counts toward max_tokens.
    max_tokens = min(16000, 2048 + 110 * len(batch))
    try:
        text = client.complete(
            system=_SYSTEM, user=_payload(batch), model=model, max_tokens=max_tokens
        )
        judgments = _parse(text, len(batch))
    except Exception as exc:  # fail open — a report beats no report
        log.warning("llm_filter.failed", error=str(exc), batch=len(batch))
        return

    missing = [it.title for idx, it in enumerate(batch) if idx not in judgments]
    if missing:
        # Rows the model skipped or malformed; these items stay unjudged.
        log.warning("llm_filter.missing_rows", count=len(missing), titles=missing[:5])
    for idx, it in enumerate(batch):
        if idx not in judgments:
            continue
        rel, imp, dep, kind, reason = judgments[idx]
        it.llm_relevance, it.llm_impact, it.llm_depth = rel, imp, dep
        it.llm_type, it.llm_reason = kind, reason
        it.llm_score = item_score(rel, imp, dep, weights)
