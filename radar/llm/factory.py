"""Select an LLM provider from settings."""

from __future__ import annotations

import structlog

from radar.llm.base import LLMClient, NullClient

log = structlog.get_logger()


def get_client(settings) -> LLMClient:
    """Build the configured provider. Returns NullClient if nothing is usable, so
    the pipeline fails open (keyword ranking, feed summaries) instead of crashing.
    """
    provider = settings.llm_provider.lower()

    if not provider:
        return NullClient()

    if provider == "anthropic":
        from radar.llm.anthropic import AnthropicClient

        client: LLMClient = AnthropicClient(settings.anthropic_api_key)
    elif provider == "openai":
        from radar.llm.openai import OpenAIClient

        client = OpenAIClient(settings.openai_api_key, settings.openai_base_url)
    else:
        log.warning("llm.unknown_provider", provider=provider)
        return NullClient()

    if not client.available:
        log.warning("llm.not_configured", provider=provider)
        return NullClient()
    return client
