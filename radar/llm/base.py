"""The LLM interface the pipeline depends on.

One method: `complete(system, user, model, max_tokens) -> str`. Every stage that
uses an LLM (relevance filter, item summaries, daily digest, weekly synthesis)
speaks only this. Providers live in sibling modules and are selected by config.

`NullClient` is the no-API-key fallback: it raises, and callers already treat an
LLM error as "fail open" (keyword ranking, feed summaries), so the pipeline still
produces a report without any provider configured.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMClient(Protocol):
    def complete(
        self, *, system: str, user: str, model: str, max_tokens: int
    ) -> str:
        """Return the model's text response. Raises on failure (callers fail open)."""
        ...

    @property
    def available(self) -> bool:
        """True if this client can actually make calls (has a key / endpoint)."""
        ...


class NullClient:
    """Used when no provider is configured. Never callable; forces fail-open paths."""

    available = False

    def complete(self, *, system: str, user: str, model: str, max_tokens: int) -> str:
        raise RuntimeError("no LLM provider configured")
