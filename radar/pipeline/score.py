"""Phase 1 scoring: keyword weights + hard kill-list veto.

Phase 2 replaces the topic matching with embedding similarity against an interest
vector, and adds novelty (1 - max_sim over 30 days). The kill-list stays.
"""

from __future__ import annotations

import re
from functools import lru_cache
from urllib.parse import urlparse

from radar.canonical import canonicalize
from radar.models import Interests, Item, RawItem, ScoreWeights, Source


@lru_cache(maxsize=256)
def _compile(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


def score_item(raw: RawItem, interests: Interests, authority: float) -> Item:
    haystack = f"{raw.title}\n{raw.summary}"

    killed_by: str | None = None
    for pat in interests.kill_list.title_patterns:
        if _compile(pat).search(raw.title):
            killed_by = f"pattern:{pat[:40]}"
            break
    if killed_by is None:
        host = urlparse(raw.url).netloc.lower().removeprefix("www.")
        if any(host.endswith(d) for d in interests.kill_list.domains):
            killed_by = f"domain:{host}"

    matched: list[str] = []
    topic_score = 0.0
    if killed_by is None:
        for rule in interests.topics:
            if _compile(rule.pattern).search(haystack):
                topic_score += rule.weight
                matched.append(rule.pattern)

    # Authority is a multiplier, not an additive term: a trusted source should
    # amplify a relevant item, never manufacture relevance for an irrelevant one.
    score = 0.0 if killed_by else topic_score * (0.5 + authority)

    return Item(
        **raw.model_dump(),
        canonical_url=canonicalize(raw.url),
        score=round(score, 3),
        authority=authority,
        matched_topics=matched,
        killed_by=killed_by,
    )


def score_all(
    raws: list[RawItem], interests: Interests, sources: list[Source]
) -> list[Item]:
    authority = {s.id: s.authority for s in sources}
    return [score_item(r, interests, authority.get(r.source_id, 0.5)) for r in raws]


def dedupe_in_batch(items: list[Item]) -> list[Item]:
    """Collapse same-canonical-URL items within one run, keeping the highest score,
    then the most authoritative source.

    The authority tiebreak matters when an aggregator links straight to a
    publisher's post: an HN submission of "anthropic.com/claude-sonnet-5-5" and
    Anthropic's own entry share a URL and (with no keyword hits) a score of 0.
    Keeping the HN copy would leave the launch to compete for HN's small judging
    budget instead of being judged as a curated post."""
    best: dict[str, Item] = {}
    for item in items:
        prev = best.get(item.canonical_url)
        if prev is None or (item.score, item.authority) > (prev.score, prev.authority):
            best[item.canonical_url] = item
    return list(best.values())


def item_score(rel: int, imp: int, dep: int, weights: ScoreWeights) -> float:
    """Combine the LLM's three judged dimensions into one 0-10 score.

    A weighted sum, not a product: an item can earn its place either by being a
    big development (a frontier model launch: high impact, modest depth) or by
    being substantive engineering (a detailed write-up: modest impact, high
    depth). Relevance carries the most weight because the digest is for AI
    engineering; the relevance *gate* lives in `is_eligible`.
    """
    return round(
        weights.relevance * rel + weights.impact * imp + weights.depth * dep, 2
    )


def is_eligible(item: Item, interests: Interests) -> bool:
    """Whether a judged item may appear in the report at all (before ranking).

    Must be on-topic enough (relevance >= min_relevance) — except field-level
    milestones (impact >= milestone_impact), which are worth knowing about even
    when they're off the engineering topics. Types capped at 0 are excluded.
    """
    if item.llm_score is None or item.llm_relevance is None or item.llm_impact is None:
        return False
    if interests.type_caps.get(item.llm_type) == 0:
        return False
    return (
        item.llm_relevance >= interests.min_relevance
        or item.llm_impact >= interests.milestone_impact
    )


def _rank_key(item: Item) -> tuple[float, float]:
    """Rank by the LLM's combined score first, keyword score as tiebreak.
    Unjudged items (llm_score is None) sort last via -1."""
    return (item.llm_score if item.llm_score is not None else -1, item.score)


# An item we couldn't fetch real text for is worth less as a *must-read*: its
# summary is just "full text unavailable" from the title. Penalize importance so
# such items sink below any readable item, keeping P0 for entries the reader can
# actually act on. Sized to outweigh the score/authority/coverage spread (~0-15)
# so a body-less item never outranks one with a body.
NO_BODY_PENALTY = 100.0


def assign_priority(
    items: list[Item],
    p0_count: int,
    cluster_sizes: dict[int, int] | None = None,
    no_body: set[int] | None = None,
) -> list[Item]:
    """Score importance and split into P0 / P1. Mutates and returns `items`.

    importance = LLM combined score (0-10, primary signal)
               + authority bonus (trusted sources matter more)
               + cross-source coverage bonus (a story clustered across many
                 sources is a bigger deal than a lone paper)
               − a large penalty when we have no article body to summarize, so
                 title-only entries never take a P0 (must-read) slot.

    The top `p0_count` by importance become P0; the rest P1. `no_body` holds the
    ids of items whose full text couldn't be fetched (computed after enrichment).
    """
    cluster_sizes = cluster_sizes or {}
    no_body = no_body or set()
    for it in items:
        base = it.llm_score if it.llm_score is not None else it.score
        coverage = cluster_sizes.get(it.cluster_id, 1) if it.cluster_id is not None else 1
        it.importance = round(
            base
            + 1.5 * it.authority          # 0.75-1.5 nudge for trusted sources
            + 0.8 * (coverage - 1)        # +0.8 per extra source covering the story
            + 0.5 * len(it.merged_sources)  # merged dups are also coverage
            - (NO_BODY_PENALTY if id(it) in no_body else 0.0),
            3,
        )

    ranked = sorted(items, key=lambda i: i.importance, reverse=True)
    for idx, it in enumerate(ranked):
        it.priority = "P0" if idx < p0_count else "P1"
    return ranked


def _apply_caps(
    ranked: list[Item],
    source_caps: dict[str, int],
    type_caps: dict[str, int],
    limit: int,
    floor: int,
) -> list[Item]:
    """Fill up to `limit` slots from `ranked` (already sorted best-first),
    honoring per-source and per-type caps as a HARD wall.

    Source caps stop a high-volume source (arXiv's papers, HN's threads) from
    dominating; type caps do the same for a kind of content (papers, launches,
    news, opinion), whichever source it comes from. We'd rather ship a tighter,
    balanced 6-item report than pad it to 8 with more of the same.

    The one exception is the floor: if honoring caps leaves us below `floor` (a
    very quiet day where almost everything came from one capped source or type),
    we relax the caps just enough to reach `floor`, because an empty-ish report is
    worse than a temporarily lopsided one. A type capped at 0 is never relaxed —
    callers exclude those before ranking.
    """
    chosen: list[Item] = []
    by_source: dict[str, int] = {}
    by_type: dict[str, int] = {}
    picked_ids: set[int] = set()

    for item in ranked:
        if len(chosen) >= limit:
            break
        cap = source_caps.get(item.source_id)
        if cap is not None and by_source.get(item.source_id, 0) >= cap:
            continue
        tcap = type_caps.get(item.llm_type) if item.llm_score is not None else None
        if tcap is not None and by_type.get(item.llm_type, 0) >= tcap:
            continue
        chosen.append(item)
        picked_ids.add(id(item))
        by_source[item.source_id] = by_source.get(item.source_id, 0) + 1
        by_type[item.llm_type] = by_type.get(item.llm_type, 0) + 1

    # Floor relaxation only: never let caps push us below the floor.
    if len(chosen) < floor:
        for item in ranked:
            if len(chosen) >= floor:
                break
            if id(item) not in picked_ids:
                chosen.append(item)

    return chosen


def select_for_report(items: list[Item], interests: Interests) -> list[Item]:
    """Rank and cut.

    When the LLM has judged items, its combined score is the gate: eligible items
    (see `is_eligible`) scoring >= min_report_score qualify. If fewer than
    floor_report_items qualify, the best eligible items down to floor_min_score
    fill the gap — a quiet day still gets a short report of the best available,
    never junk. A run where the LLM was skipped/failed falls back to the keyword
    `min_score` threshold so the pipeline still produces a report.

    Source and type caps then keep the report balanced.
    """
    alive = sorted(
        (i for i in items if not i.is_killed), key=_rank_key, reverse=True
    )

    judged = [i for i in alive if i.llm_score is not None]
    if not judged:  # LLM skipped/failed — degrade to keyword threshold
        above = [i for i in alive if i.score >= interests.min_score]
        # Nothing cleared the bar — floor with the top-ranked alive items.
        limit = interests.max_report_items if above else interests.floor_report_items
        return _apply_caps(
            above or alive,
            interests.source_caps,
            {},
            limit,
            interests.floor_report_items,
        )

    eligible = [i for i in judged if is_eligible(i, interests)]
    above = [i for i in eligible if i.llm_score >= interests.min_report_score]
    chosen = _apply_caps(
        above,
        interests.source_caps,
        interests.type_caps,
        interests.max_report_items,
        0,
    )
    if len(chosen) < interests.floor_report_items:
        # Re-pick from everything down to the floor bar: best-first, caps honored
        # where possible and relaxed only to reach the floor.
        pool = [i for i in eligible if i.llm_score >= interests.floor_min_score]
        chosen = _apply_caps(
            pool,
            interests.source_caps,
            interests.type_caps,
            interests.floor_report_items,
            interests.floor_report_items,
        )
    return chosen
