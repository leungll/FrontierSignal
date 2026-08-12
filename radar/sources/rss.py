"""Generic RSS/Atom fetching. Covers ~80% of sources.

Two things this handles that a naive implementation misses:
  1. Conditional requests (ETag/Last-Modified) — a 304 is free for both sides.
  2. Per-source failure isolation — one dead feed must never kill a run.
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime
from time import struct_time

import feedparser
import httpx
import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from radar import db
from radar.models import RawItem, Source

log = structlog.get_logger()

USER_AGENT = "Frontier-Signal/0.1 (+https://github.com/ll-leung/ai-research-radar)"


class FetchError(Exception):
    pass


def _to_datetime(parsed: struct_time | None) -> datetime | None:
    if not parsed:
        return None
    try:
        return datetime(*parsed[:6], tzinfo=UTC)
    except (TypeError, ValueError):
        return None


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    # Retry transport errors only. A 4xx is a real answer — retrying it is rude
    # and pointless; _raise_for_status below converts those to FetchError.
    retry=retry_if_exception_type(httpx.TransportError),
    reraise=True,
)
async def _get(
    client: httpx.AsyncClient, url: str, etag: str | None, last_modified: str | None
) -> httpx.Response:
    headers = {"User-Agent": USER_AGENT}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    return await client.get(url, headers=headers, follow_redirects=True)


async def fetch_source(
    client: httpx.AsyncClient,
    conn: sqlite3.Connection,
    source: Source,
    *,
    since: datetime,
    cap: int,
) -> list[RawItem]:
    """Fetch one source. Raises FetchError; the caller isolates the failure."""
    state = db.get_source_state(conn, source.id)
    etag = state["etag"] if state else None
    last_modified = state["last_modified"] if state else None

    try:
        resp = await _get(client, str(source.url), etag, last_modified)
    except httpx.HTTPError as exc:
        raise FetchError(f"{type(exc).__name__}: {exc}") from exc

    if resp.status_code == 304:
        log.info("source.not_modified", source=source.id)
        db.save_source_success(conn, source.id, etag, last_modified)
        return []

    if resp.status_code >= 400:
        raise FetchError(f"HTTP {resp.status_code}")

    feed = feedparser.parse(resp.content)
    # bozo means malformed XML. feedparser usually recovers, so only fail when
    # it recovered nothing at all.
    if feed.bozo and not feed.entries:
        raise FetchError(f"unparseable feed: {getattr(feed, 'bozo_exception', 'unknown')}")

    items: list[RawItem] = []
    for entry in feed.entries[:cap]:
        link = entry.get("link")
        title = (entry.get("title") or "").strip()
        if not link or not title:
            continue

        published = _to_datetime(
            entry.get("published_parsed") or entry.get("updated_parsed")
        )
        # Undated entries are kept: some feeds (e.g. Hugging Face) omit dates,
        # and the DB dedupes by URL anyway.
        if published and published < since:
            continue

        items.append(
            RawItem(
                source_id=source.id,
                source_name=source.name,
                title=title,
                url=link,
                summary=(entry.get("summary") or "").strip()[:2000],
                published_at=published,
            )
        )

    db.save_source_success(
        conn, source.id, resp.headers.get("etag"), resp.headers.get("last-modified")
    )
    log.info("source.ok", source=source.id, count=len(items))
    return items


async def fetch_all(
    conn: sqlite3.Connection,
    sources: list[Source],
    *,
    since: datetime,
    timeout: float,
    concurrency: int,
    cap: int,
) -> tuple[list[RawItem], list[str]]:
    """Fetch every source concurrently. Returns (items, dead_source_ids)."""
    sem = asyncio.Semaphore(concurrency)
    dead: list[str] = []
    all_items: list[RawItem] = []

    async with httpx.AsyncClient(timeout=timeout, http2=False) as client:

        async def one(src: Source) -> list[RawItem]:
            async with sem:
                try:
                    return await fetch_source(client, conn, src, since=since, cap=cap)
                except Exception as exc:  # isolation: never let one feed kill the run
                    fails = db.save_source_failure(conn, src.id, str(exc)[:300])
                    log.warning("source.failed", source=src.id, error=str(exc), streak=fails)
                    if fails >= 3:
                        dead.append(src.id)
                    return []

        for result in await asyncio.gather(*(one(s) for s in sources)):
            all_items.extend(result)

    return all_items, dead
