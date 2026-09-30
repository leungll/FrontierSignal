"""Anthropic (Claude) provider."""

from __future__ import annotations

import structlog

log = structlog.get_logger()


class AnthropicClient:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._client = None  # lazy: don't construct the SDK client without a key

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def _sdk(self):
        if self._client is None:
            from anthropic import Anthropic

            self._client = Anthropic(api_key=self._api_key)
        return self._client

    def complete(self, *, system: str, user: str, model: str, max_tokens: int) -> str:
        resp = self._sdk().messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        # Newer models (Sonnet 5.5, Opus 5.5) think by default, so the response can
        # open with a thinking block; the answer is the concatenated text blocks.
        # Thinking tokens also count toward max_tokens — surface truncation.
        if resp.stop_reason == "max_tokens":
            log.warning("anthropic.max_tokens", model=model, max_tokens=max_tokens)
        return "".join(b.text for b in resp.content if b.type == "text")

    def fetch_page(self, url: str, *, model: str) -> str:
        """Fetch a page's text through Claude's server-side web_fetch tool.

        The fallback when our own GET is refused (bot challenges on publisher
        sites). Anthropic's fetcher identifies itself openly; we don't evade bot
        protection. We read the fetched document directly and ask the model for no
        output of its own, so cost is roughly the capped page size in input tokens.
        Returns "" when the tool didn't run or reported an error.
        """
        resp = self._sdk().messages.create(
            model=model,
            max_tokens=1024,
            tools=[
                {
                    "type": "web_fetch_20250910",
                    "name": "web_fetch",
                    "max_uses": 1,
                    "max_content_tokens": 5000,
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Use web_fetch to fetch {url}. After fetching, reply with just: done"
                    ),
                }
            ],
        )
        for block in resp.content:
            if block.type != "web_fetch_tool_result":
                continue
            result = block.content
            if getattr(result, "type", "") != "web_fetch_result":
                error = getattr(result, "error_code", None)
                log.info("anthropic.web_fetch_error", url=url, error=error)
                return ""
            source = result.content.source
            data = getattr(source, "data", "")
            return _strip_front_matter(data) if isinstance(data, str) else ""
        return ""


def _strip_front_matter(text: str) -> str:
    """Drop the '---'-fenced metadata header web_fetch prepends to page text."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4 :].lstrip()
    return text
