"""LLM providers behind one small interface.

The pipeline never imports a vendor SDK directly — it calls `LLMClient.complete()`.
Swapping Claude for an OpenAI-compatible endpoint (OpenAI, Ollama, or any
compatible API) is a config change, not a code change. See `get_client`.
"""

from radar.llm.base import LLMClient, NullClient
from radar.llm.factory import get_client

__all__ = ["LLMClient", "NullClient", "get_client"]
