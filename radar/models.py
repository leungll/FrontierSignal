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


class ScoreWeights(BaseModel):
    relevance: float = 0.45
    impact: float = 0.35
    depth: float = 0.20


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
    # How the LLM's three judged dimensions combine into one 0-10 item score (see
    # radar.pipeline.score.item_score). Weights should sum to 1.
    score_weights: ScoreWeights = Field(default_factory=ScoreWeights)
    # Items at/above this combined score qualify for the report.
    min_report_score: float = 6.0
    # On a quiet day the floor may dip below min_report_score, but never below this.
    floor_min_score: float = 5.0
    # An item must be at least this relevant to AI engineering to qualify — unless
    # its impact reaches milestone_impact (a field-level milestone is worth knowing
    # about even when it's off the engineering topics).
    min_relevance: int = 5
    milestone_impact: int = 9
    # Max items of one content type (release/research/engineering/news/opinion/
    # promo) per report, so no single kind of content crowds out the rest. Types
    # not listed are uncapped; 0 excludes the type outright.
    type_caps: dict[str, int] = Field(default_factory=dict)
    # High-volume "firehose" sources and how many of their items (best keyword
    # score first) the LLM judges per run. Every other source is curated and low
    # volume, so ALL its items are judged — keyword matching is only a pre-filter
    # for the firehoses, never a reason to skip a curated post.
    firehose_budget: dict[str, int] = Field(default_factory=dict)
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

    # Filled by the LLM filter stage (radar.pipeline.llm_filter). Three independent
    # 0-10 dimensions, combined into `llm_score` by radar.pipeline.score.item_score:
    #   relevance — how directly it bears on building/running/evaluating AI systems
    #   impact    — how much the underlying development changes what practitioners
    #               know or can do (a launch post is short but its launch is big)
    #   depth     — how much concrete technical substance this text carries
    llm_relevance: int | None = None
    llm_impact: int | None = None
    llm_depth: int | None = None
    llm_score: float | None = None  # combined 0-10; None = not judged
    llm_reason: str = ""
    # Content type: release | research | engineering | news | opinion | promo.
    # Selection caps each type (interests.type_caps) to keep the report balanced.
    llm_type: str = "engineering"

    # Filled by the Opus summarizer (radar.summarize.item).
    why_it_matters: str = ""
    # Best-effort article text fetched only for final report candidates. This is
    # transient enrichment for summarization and is not persisted in SQLite.
    full_text: str = ""

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
