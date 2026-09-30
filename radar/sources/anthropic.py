"""Anthropic's featured announcements, read straight from anthropic.com/news.

Anthropic has no official RSS. The community mirrors in sources.yaml parse only
the article *list* on the news page — but model launches ("Introducing Claude
Sonnet 5.5") are shown in the "Featured Grid" at the top and never reach that
list, so the mirrors silently miss exactly the most important announcements.

This fetcher reads the featured entries from the page's embedded data. Items use
the same source_id as the RSS mirror, so they share its authority and any
overlap is collapsed by canonical-URL dedupe.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urljoin

import httpx
import structlog

from radar.models import RawItem

log = structlog.get_logger()

PAGE = "https://www.anthropic.com/news"
SOURCE_ID = "anthropic"
SOURCE_NAME = "Anthropic"

# A featured entry in the page's (JSON-escaped) embedded data, e.g.
# {"_key":"…","_type":"featuredGridLink","date":"2026-09-28","subject":"…",
#  "summary":"…","title":"Introducing Claude Sonnet 5.5","url":"/claude-sonnet-5-5"}
_ENTRY_RE = re.compile(r'\{[^{}]*"_type":"featuredGridLink"[^{}]*\}')


def parse_featured(html: str) -> list[dict]:
    """Extract featured entries (title, url, date, summary) from the page HTML."""
    text = html.replace('\\"', '"')
    entries: list[dict] = []
    seen: set[str] = set()
    for m in _ENTRY_RE.finditer(text):
        try:
            entry = json.loads(m.group())
        except json.JSONDecodeError:
            continue
        url, title = entry.get("url"), entry.get("title")
        if not url or not title or url in seen:
            continue
        seen.add(url)
        entries.append(entry)
    return entries


async def fetch(client: httpx.AsyncClient, *, since: datetime) -> list[RawItem]:
    """Featured announcements published since `since`. Returns [] on failure."""
    try:
        resp = await client.get(PAGE)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("anthropic.failed", error=str(exc))
        return []

    # Entries carry a date, not a time: a post dated today may have gone up after
    # the last run, so look back one extra day. Cross-run dedupe drops repeats.
    cutoff = since - timedelta(days=1)
    items: list[RawItem] = []
    for entry in parse_featured(resp.text):
        try:
            published = datetime.fromisoformat(entry.get("date", "")).replace(tzinfo=UTC)
        except ValueError:
            continue  # undated promo tiles are not announcements
        if published < cutoff:
            continue
        items.append(
            RawItem(
                source_id=SOURCE_ID,
                source_name=SOURCE_NAME,
                title=entry["title"].strip(),
                url=urljoin(PAGE, entry["url"]),
                summary=(entry.get("summary") or "").strip(),
                published_at=published,
            )
        )
    if not items and "featuredGridLink" not in resp.text:
        # The page layout changed; surface it instead of silently returning nothing.
        log.warning("anthropic.no_featured_grid")
    log.info("anthropic.ok", count=len(items))
    return items
