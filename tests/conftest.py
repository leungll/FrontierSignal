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
    llm_relevance: int | None = None,
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
        llm_relevance=llm_relevance,
        cluster_id=cluster_id,
        priority=priority,
        summary=summary,
    )
