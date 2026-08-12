"""Pydantic models validated at every pipeline stage boundary."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, HttpUrl


class Source(BaseModel):
    """A configured content source."""

    id: str
    name: str
    url: HttpUrl
    authority: float = Field(ge=0.0, le=1.0, default=0.5)


class TopicRule(BaseModel):
    pattern: str
    weight: float


class KillList(BaseModel):
    title_patterns: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)


class Interests(BaseModel):
    topics: list[TopicRule] = Field(default_factory=list)
    kill_list: KillList = Field(default_factory=KillList)
    min_score: float = 1.0
    max_report_items: int = 8
    floor_report_items: int = 3
    p0_count: int = 3
    # Per-source cap on how many items may appear in one report, keyed by
    # source_id. Prevents a high-volume source (arxiv, hackernews) from crowding
    # out sparser but valuable sources (engineering blogs). Sources not listed are
    # uncapped. After the capped pass, remaining slots are backfilled by rank so a
    # quiet-blog day still fills up rather than under-reporting.
    source_caps: dict[str, int] = Field(default_factory=dict)


class RawItem(BaseModel):
    """Straight out of a source, before any processing."""

    source_id: str
    source_name: str
    title: str
    url: str
    summary: str = ""
    published_at: datetime | None = None


class Item(RawItem):
    """A stored item: canonicalized and scored."""

    canonical_url: str
    score: float = 0.0
    authority: float = 0.5  # carried from the source; trusted sources get a fast lane
    matched_topics: list[str] = Field(default_factory=list)
    killed_by: str | None = None

    # Filled by the Claude filter stage (radar.pipeline.llm_filter).
    llm_relevance: int | None = None  # 0-10; None = not yet judged
    llm_reason: str = ""

    # Filled by the Opus summarizer (radar.summarize.item).
    why_it_matters: str = ""

    # Filled by the embedding/cluster stage (radar.pipeline.cluster).
    cluster_id: int | None = None
    # Other sources that covered the same story, merged during semantic dedup.
    merged_sources: list[dict[str, str]] = Field(default_factory=list)

    # Filled by the ranking stage (radar.pipeline.score.assign_priority).
    priority: str = "P1"  # "P0" (most important) or "P1"
    importance: float = 0.0

    @property
    def is_killed(self) -> bool:
        return self.killed_by is not None


class RunStats(BaseModel):
    """Summary of one pipeline run — the numbers in the report header."""

    found: int = 0
    new: int = 0
    killed: int = 0
    below_threshold: int = 0
    reported: int = 0
    dead_sources: list[str] = Field(default_factory=list)

    @property
    def filtered(self) -> int:
        return self.new - self.reported
