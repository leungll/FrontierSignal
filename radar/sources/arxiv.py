"""arXiv via the public Atom API — gated hard on our categories.

We never try to read all of arXiv. We query a handful of relevant categories
(cs.AI, cs.LG, cs.CL, cs.DC) sorted by submission date, take the newest N, and
let keyword + Claude filtering cut it down to what actually matters. The category
gate alone removes ~99% of arXiv before a single token is spent.

API docs: https://info.arxiv.org/help/api/user-manual.html
Etiquette: one request, sorted by date; arXiv asks for <=1 request/3s (we do 1).
"""

from __future__ import annotations

from datetime import datetime

import feedparser
import httpx
import structlog

from radar.models import RawItem

log = structlog.get_logger()

ENDPOINT = "https://export.arxiv.org/api/query"
SOURCE_ID = "arxiv"
SOURCE_NAME = "arXiv"

CATEGORIES = ["cs.AI", "cs.LG", "cs.CL", "cs.DC", "cs.SE"]


async def fetch(
    client: httpx.AsyncClient, *, since: datetime, cap: int = 60
) -> list[RawItem]:
    """Newest papers in our categories. Date filtering happens in the pipeline
    (arXiv's date filters are awkward); we just take the freshest `cap`."""
    query = " OR ".join(f"cat:{c}" for c in CATEGORIES)
    params = {
        "search_query": query,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": str(cap),
    }
    try:
        resp = await client.get(ENDPOINT, params=params)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("arxiv.failed", error=str(exc))
        return []

    feed = feedparser.parse(resp.content)
    items: list[RawItem] = []
    for entry in feed.entries:
        title = (entry.get("title") or "").strip().replace("\n", " ")
        link = entry.get("link")
        if not title or not link:
            continue
        published = None
        if entry.get("published_parsed"):
            published = datetime(*entry.published_parsed[:6])
            from datetime import UTC

            published = published.replace(tzinfo=UTC)
        if published and published < since:
            continue
        abstract = (entry.get("summary") or "").strip().replace("\n", " ")[:2000]
        items.append(
            RawItem(
                source_id=SOURCE_ID,
                source_name=SOURCE_NAME,
                title=title,
                url=link,
                summary=abstract,
                published_at=published,
            )
        )
    log.info("arxiv.ok", count=len(items))
    return items
