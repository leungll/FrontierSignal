"""arXiv via the public Atom API — gated hard on our categories and topics.

We never try to read all of arXiv. We query a handful of relevant categories
(cs.AI, cs.LG, cs.CL, cs.DC, cs.SE) AND a broad topic filter (agents, language
models, inference/serving), sorted by submission date, and let keyword + LLM
filtering cut it down to what actually matters.

The topic filter matters: these categories get well over a thousand submissions a
day, so "the newest N" in the categories alone was an hour-long, effectively
random slice. Filtering by topic first means the newest N covers about a day of
the papers we might actually report.

API docs: https://info.arxiv.org/help/api/user-manual.html
Etiquette: one request, sorted by date; arXiv asks for <=1 request/3s (we do 1).
"""

from __future__ import annotations

import asyncio
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
# Deliberately broad — this only narrows the sample; ranking happens downstream.
TOPIC_TERMS = [
    "agent", "agents", "agentic", '"tool use"', '"language model"',
    "LLM", "LLMs", "inference", "serving",
]
# ~a day of topic-matching submissions. The LLM judges only the best-keyword
# slice of these (interests.firehose_budget), so a larger sample costs no tokens.
DEFAULT_CAP = 300
RETRY_DELAY_S = 3.0  # arXiv asks for <= 1 request / 3 s


def build_query() -> str:
    cats = " OR ".join(f"cat:{c}" for c in CATEGORIES)
    topics = " OR ".join(f"abs:{t}" for t in TOPIC_TERMS)
    return f"({cats}) AND ({topics})"


async def fetch(
    client: httpx.AsyncClient, *, since: datetime, cap: int = DEFAULT_CAP
) -> list[RawItem]:
    """Newest on-topic papers in our categories. Date filtering happens here
    (arXiv's date filters are awkward); we take the freshest `cap` and drop
    anything older than `since`."""
    query = build_query()
    params = {
        "search_query": query,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": str(cap),
    }
    resp = None
    for attempt in range(2):  # arXiv often 429/503s shared CI runners; retry once
        try:
            resp = await client.get(ENDPOINT, params=params)
            resp.raise_for_status()
            break
        except httpx.HTTPError as exc:
            log.warning("arxiv.failed", error=str(exc), attempt=attempt + 1)
            resp = None
            if attempt == 0:
                await asyncio.sleep(RETRY_DELAY_S)
    if resp is None:
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
