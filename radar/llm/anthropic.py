"""Anthropic (Claude) provider."""

from __future__ import annotations


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
        return resp.content[0].text
