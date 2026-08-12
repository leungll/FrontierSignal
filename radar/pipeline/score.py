"""Phase 1 scoring: keyword weights + hard kill-list veto.

Phase 2 replaces the topic matching with embedding similarity against an interest
vector, and adds novelty (1 - max_sim over 30 days). The kill-list stays.
"""

from __future__ import annotations

import re
from functools import lru_cache
from urllib.parse import urlparse

from radar.canonical import canonicalize
from radar.models import Interests, Item, RawItem, Source


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
    """Collapse same-canonical-URL items within one run, keeping the highest score."""
    best: dict[str, Item] = {}
    for item in items:
        prev = best.get(item.canonical_url)
        if prev is None or item.score > prev.score:
            best[item.canonical_url] = item
    return list(best.values())


def _rank_key(item: Item) -> tuple[float, float]:
    """Rank by Claude relevance first, keyword score as tiebreak. Unjudged items
    (llm_relevance is None) sort last via -1."""
    return (item.llm_relevance if item.llm_relevance is not None else -1, item.score)


def assign_priority(
    items: list[Item], p0_count: int, cluster_sizes: dict[int, int] | None = None
) -> list[Item]:
    """Score importance and split into P0 / P1. Mutates and returns `items`.

    importance = relevance (0-10, primary signal)
               + authority bonus (trusted sources matter more)
               + cross-source coverage bonus (a story clustered across many
                 sources is a bigger deal than a lone paper).

    The top `p0_count` by importance become P0; the rest P1.
    """
    cluster_sizes = cluster_sizes or {}
    for it in items:
        rel = it.llm_relevance if it.llm_relevance is not None else it.score
        coverage = cluster_sizes.get(it.cluster_id, 1) if it.cluster_id is not None else 1
        it.importance = round(
            rel
            + 1.5 * it.authority          # 0.75-1.5 nudge for trusted sources
            + 0.8 * (coverage - 1)        # +0.8 per extra source covering the story
            + 0.5 * len(it.merged_sources),  # merged dups are also coverage
            3,
        )

    ranked = sorted(items, key=lambda i: i.importance, reverse=True)
    for idx, it in enumerate(ranked):
        it.priority = "P0" if idx < p0_count else "P1"
    return ranked


def _apply_source_caps(
    ranked: list[Item], caps: dict[str, int], limit: int, floor: int
) -> list[Item]:
    """Fill up to `limit` slots from `ranked` (already sorted best-first),
    honoring per-source caps as a HARD wall.

    Caps are a hard ceiling per source: arXiv can contribute at most its cap, full
    stop. This is deliberate — the whole point is that arXiv's 50 papers never
    dominate the report, so we'd rather ship a tighter 6-item report than pad it
    out to 8 with more papers. A source-diverse short report beats a paper-heavy
    long one.

    The one exception is the floor: if honoring caps leaves us below `floor`
    (a very quiet day where almost nothing but one capped source had content),
    we relax the caps just enough to reach `floor`, because an empty-ish report is
    worse than a temporarily source-heavy one.
    """
    chosen: list[Item] = []
    used: dict[str, int] = {}
    picked_ids: set[int] = set()

    for item in ranked:
        if len(chosen) >= limit:
            break
        cap = caps.get(item.source_id)
        if cap is not None and used.get(item.source_id, 0) >= cap:
            continue
        chosen.append(item)
        picked_ids.add(id(item))
        used[item.source_id] = used.get(item.source_id, 0) + 1

    # Floor relaxation only: never let caps push us below the floor.
    if len(chosen) < floor:
        for item in ranked:
            if len(chosen) >= floor:
                break
            if id(item) not in picked_ids:
                chosen.append(item)

    return chosen


def select_for_report(
    items: list[Item], interests: Interests, llm_min_relevance: int = 6
) -> list[Item]:
    """Rank and cut.

    When Claude has scored items, its relevance is the primary gate: only items at
    or above `llm_min_relevance` qualify. Items Claude never judged, or a run where
    Claude was skipped/failed, fall back to the keyword `min_score` threshold so
    the pipeline still produces a report.

    Per-source caps (interests.source_caps) then keep any single high-volume source
    from crowding out the rest. Floor guarantees a quiet day reports something.
    """
    alive = sorted(
        (i for i in items if not i.is_killed), key=_rank_key, reverse=True
    )

    judged = [i for i in alive if i.llm_relevance is not None]
    if judged:
        above = [i for i in judged if i.llm_relevance >= llm_min_relevance]
    else:  # Claude skipped/failed — degrade to keyword threshold
        above = [i for i in alive if i.score >= interests.min_score]

    if above:
        return _apply_source_caps(
            above,
            interests.source_caps,
            interests.max_report_items,
            interests.floor_report_items,
        )
    # Nothing cleared the bar — floor with the top-ranked alive items (caps still
    # apply so the floor stays diverse too).
    return _apply_source_caps(
        alive,
        interests.source_caps,
        interests.floor_report_items,
        interests.floor_report_items,
    )
