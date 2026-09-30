"""Shared test helpers."""

from __future__ import annotations

from radar.models import Item


def make_item(
    title: str,
    *,
    source_id: str = "src",
    source_name: str = "Src",
    score: float = 1.0,
    authority: float = 0.5,
    llm_score: float | None = None,
    llm_relevance: int | None = None,
    llm_impact: int | None = None,
    llm_type: str = "engineering",
    cluster_id: int | None = None,
    priority: str = "P1",
    summary: str = "",
    url: str | None = None,
) -> Item:
    return Item(
        source_id=source_id,
        source_name=source_name,
        title=title,
        url=url or f"https://example.com/{title}",
        canonical_url=url or f"https://example.com/{title}",
        score=score,
        authority=authority,
        llm_score=llm_score,
        # A scored item defaults to on-topic, ordinary-impact dimensions so it
        # passes the eligibility gate unless a test overrides them.
        llm_relevance=llm_relevance if llm_relevance is not None or llm_score is None else 8,
        llm_impact=llm_impact if llm_impact is not None or llm_score is None else 5,
        llm_type=llm_type,
        cluster_id=cluster_id,
        priority=priority,
        summary=summary,
    )
