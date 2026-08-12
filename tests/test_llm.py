from radar.config import Settings
from radar.llm import get_client
from radar.llm.base import NullClient


def test_null_client_unavailable_and_raises():
    n = NullClient()
    assert n.available is False
    try:
        n.complete(system="s", user="u", model="m", max_tokens=10)
        raise AssertionError("should have raised")
    except RuntimeError:
        pass


def test_factory_anthropic_with_key():
    c = get_client(Settings(llm_provider="anthropic", anthropic_api_key="sk-x"))
    assert type(c).__name__ == "AnthropicClient"
    assert c.available


def test_factory_openai_with_base_url_only():
    # local Ollama: base_url is enough, no key required
    c = get_client(Settings(llm_provider="openai", openai_base_url="http://localhost:11434/v1"))
    assert type(c).__name__ == "OpenAIClient"
    assert c.available


def test_factory_falls_back_to_null():
    no_key = get_client(Settings(llm_provider="anthropic", anthropic_api_key=""))
    assert isinstance(no_key, NullClient)
    assert isinstance(get_client(Settings(llm_provider="bogus")), NullClient)
