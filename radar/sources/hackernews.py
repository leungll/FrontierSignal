"""Hacker News via the Algolia Search API — no key, no scraping.

We pull front-page-worthy stories from the last window and keep only those with
enough points to signal the community found them worth reading. Keyword + Claude
filtering downstream handles topical relevance; this fetcher's only job is to get
recent, non-trivial HN stories into the pipeline as RawItems.

API: https://hn.algolia.com/api  (search_by_date, numericFilters on points/time)
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import httpx
import structlog

from radar.models import RawItem

log = structlog.get_logger()

ENDPOINT = "https://hn.algolia.com/api/v1/search_by_date"
SOURCE_ID = "hackernews"
SOURCE_NAME = "Hacker News"

# HN gives us no article body — only vote/comment metrics. We store that as the
# item summary so there's *something*, but it is a placeholder, not real content:
# the summarizer must treat it as "body unavailable" and not parrot it back.
_METRICS_RE = re.compile(r"\d+\s+points\s+·\s+\d+\s+comments on Hacker News\.")


def metrics_summary(points: int, comments: int) -> str:
    return f"{points} points · {comments} comments on Hacker News."


def is_metrics_placeholder(text: str) -> bool:
    """True if `text` is (only) the HN metrics line, i.e. carries no real body."""
    return bool(_METRICS_RE.fullmatch(text.strip()))


async def fetch(
    client: httpx.AsyncClient, *, since: datetime, min_points: int = 40, cap: int = 40
) -> list[RawItem]:
    """Recent HN stories above a points floor. Returns [] on any failure."""
    after = int(since.timestamp())
    params = {
        "tags": "story",
        "numericFilters": f"created_at_i>{after},points>={min_points}",
        "hitsPerPage": str(cap),
    }
    try:
        resp = await client.get(ENDPOINT, params=params)
        resp.raise_for_status()
        hits = resp.json().get("hits", [])
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("hackernews.failed", error=str(exc))
        return []

    items: list[RawItem] = []
    for h in hits:
        title = (h.get("title") or "").strip()
        # url is the submitted link; fall back to the HN discussion page.
        url = h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}"
        if not title:
            continue
        created = h.get("created_at_i")
        published = datetime.fromtimestamp(created, UTC) if created else None
        points = h.get("points", 0)
        comments = h.get("num_comments", 0)
        items.append(
            RawItem(
                source_id=SOURCE_ID,
                source_name=SOURCE_NAME,
                title=title,
                url=url,
                summary=metrics_summary(points, comments),
                published_at=published,
            )
        )
    log.info("hackernews.ok", count=len(items))
    return items
