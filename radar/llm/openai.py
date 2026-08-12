"""OpenAI-compatible provider.

Covers OpenAI itself, a local Ollama server, and most Chinese/other vendors that
expose an OpenAI-style /chat/completions endpoint — all via `base_url`.

Examples of base_url:
  - OpenAI:  https://api.openai.com/v1   (default)
  - Ollama:  http://localhost:11434/v1   (api_key can be any non-empty string)
  - others:  whatever the vendor documents
"""

from __future__ import annotations


class OpenAIClient:
    def __init__(self, api_key: str, base_url: str = "") -> None:
        self._api_key = api_key
        self._base_url = base_url or None
        self._client = None

    @property
    def available(self) -> bool:
        # Local endpoints (Ollama) often accept any key, so a base_url alone is enough.
        return bool(self._api_key or self._base_url)

    def _sdk(self):
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(api_key=self._api_key or "not-needed", base_url=self._base_url)
        return self._client

    def complete(self, *, system: str, user: str, model: str, max_tokens: int) -> str:
        resp = self._sdk().chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return resp.choices[0].message.content or ""
