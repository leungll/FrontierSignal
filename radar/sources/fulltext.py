"""Best-effort article text enrichment for final report candidates.

Only the handful of selected items are fetched. Failures are isolated and leave
the original RSS/Atom excerpt untouched.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from html.parser import HTMLParser

import httpx
import structlog

from radar.models import Item

log = structlog.get_logger()

MAX_TEXT_CHARS = 12_000
# A page must yield at least this much text to count as a real article body.
# Shared with the summarizer's threshold for "too thin to summarize".
MIN_BODY_CHARS = 200

# Article servers reject the feed-reader UA far more often than a browser one
# (403/429). Full-text fetch is a plain page GET, so present as a browser to cut
# needless denials; the RSS fetcher keeps its own polite feed-reader UA.
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class _ArticleParser(HTMLParser):
    """Extract readable paragraph and list text without another dependency."""

    _content_tags = {"h1", "h2", "h3", "p", "li", "blockquote", "pre"}
    _ignored_tags = {"script", "style", "svg", "nav", "footer", "form", "noscript"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self._capture_depth = 0
        self._current: list[str] = []
        self.blocks: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self._ignored_tags:
            self._ignored_depth += 1
        elif not self._ignored_depth and tag in self._content_tags:
            self._capture_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self._ignored_tags and self._ignored_depth:
            self._ignored_depth -= 1
        elif not self._ignored_depth and tag in self._content_tags and self._capture_depth:
            self._capture_depth -= 1
            if self._capture_depth == 0:
                text = " ".join("".join(self._current).split())
                if len(text) >= 30 and (not self.blocks or text != self.blocks[-1]):
                    self.blocks.append(text)
                self._current = []

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth and self._capture_depth:
            self._current.append(data)


def extract(html: str) -> str:
    parser = _ArticleParser()
    parser.feed(html)
    return "\n\n".join(parser.blocks)[:MAX_TEXT_CHARS]


# Statuses worth one retry: transient throttling / edge hiccups. A 404 or 401 is
# permanent (missing page / paywall), so we don't waste a second request on it.
_RETRY_STATUS = {429, 500, 502, 503, 504}
# Misses no fetcher can fix: the page is gone or behind a login. Everything else
# (bot challenges, JS-only shells, timeouts) is worth one fallback attempt.
_PERMANENT_STATUS = {401, 404, 410}

# Fallback fetcher: url -> page text ("" on failure). Blocking; run in a thread.
Fallback = Callable[[str], str]


async def enrich(
    items: list[Item],
    *,
    timeout: float,
    concurrency: int = 4,
    fallback: Fallback | None = None,
) -> list[Item]:
    """Attach extracted article text to items in place and return them.

    Fetch failures are expected and structural (paywalls, 404s, JS-only pages, bot
    blocks), not bugs. We retry once on transient statuses. When our own GET still
    fails for a reason other than a permanent miss, `fallback` (a fetcher that can
    get past what we can't, e.g. Claude's web_fetch) gets one try — a general rule
    for any site, not a per-publisher special case. Every miss is logged with its
    reason so the miss rate is observable — a spike means summaries silently
    degrade to "full text unavailable", which is worth noticing.
    """
    sem = asyncio.Semaphore(concurrency)
    headers = {"User-Agent": BROWSER_UA}
    misses: list[str] = []
    rescued: list[str] = []

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:

        async def fetch(url: str) -> httpx.Response:
            response = await client.get(url, headers=headers)
            if response.status_code in _RETRY_STATUS:
                response = await client.get(url, headers=headers)
            return response

        async def direct(item: Item) -> str | None:
            """Our own GET. Returns None on success, else the miss reason."""
            try:
                response = await fetch(item.url)
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                log.info("fulltext.skipped", url=item.url, status=exc.response.status_code)
                return f"http {exc.response.status_code}"
            except (httpx.HTTPError, ValueError) as exc:
                log.info("fulltext.skipped", url=item.url, error=str(exc))
                return type(exc).__name__
            content_type = response.headers.get("content-type", "").lower()
            if "html" not in content_type:
                return f"non-html: {content_type[:30]}"
            text = extract(response.text)
            if len(text) < MIN_BODY_CHARS:
                return f"thin: {len(text)} chars"
            item.full_text = text
            return None

        async def one(item: Item) -> None:
            async with sem:
                reason = await direct(item)
                if reason is None:
                    return
                permanent = any(reason == f"http {s}" for s in _PERMANENT_STATUS)
                if fallback is not None and not permanent:
                    try:
                        text = (await asyncio.to_thread(fallback, item.url)).strip()
                    except Exception as exc:  # the fallback is best-effort too
                        log.info("fulltext.fallback_failed", url=item.url, error=str(exc))
                        text = ""
                    if len(text) >= MIN_BODY_CHARS:
                        item.full_text = text[:MAX_TEXT_CHARS]
                        rescued.append(item.url)
                        return
                    reason += ", fallback failed"
                misses.append(f"{item.url} ({reason})")

        await asyncio.gather(*(one(item) for item in items))

    enriched = sum(bool(item.full_text) for item in items)
    log.info(
        "fulltext.done",
        enriched=enriched,
        total=len(items),
        missed=len(misses),
        rescued=rescued or None,
        misses=misses or None,
    )
    return items
