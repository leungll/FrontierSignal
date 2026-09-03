"""Best-effort article text enrichment for final report candidates.

Only the handful of selected items are fetched. Failures are isolated and leave
the original RSS/Atom excerpt untouched.
"""

from __future__ import annotations

import asyncio
from html.parser import HTMLParser

import httpx
import structlog

from radar.models import Item
from radar.sources.rss import USER_AGENT

log = structlog.get_logger()

MAX_TEXT_CHARS = 12_000


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


async def enrich(items: list[Item], *, timeout: float, concurrency: int = 4) -> list[Item]:
    """Attach extracted article text to items in place and return them."""
    sem = asyncio.Semaphore(concurrency)
    headers = {"User-Agent": USER_AGENT}

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:

        async def one(item: Item) -> None:
            async with sem:
                try:
                    response = await client.get(item.url, headers=headers)
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "").lower()
                    if "html" not in content_type:
                        return
                    text = extract(response.text)
                    if len(text) >= 200:
                        item.full_text = text
                except (httpx.HTTPError, ValueError) as exc:
                    log.info("fulltext.skipped", url=item.url, error=str(exc))

        await asyncio.gather(*(one(item) for item in items))

    enriched = sum(bool(item.full_text) for item in items)
    log.info("fulltext.done", enriched=enriched, total=len(items))
    return items
