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
