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


def test_anthropic_skips_thinking_blocks():
    # Sonnet 5.5 / Opus 5.5 think by default: the reply opens with a thinking block.
    from types import SimpleNamespace as NS

    from radar.llm.anthropic import AnthropicClient

    resp = NS(
        stop_reason="end_turn",
        content=[NS(type="thinking", thinking=""), NS(type="text", text='[{"i":0}]')],
    )
    c = AnthropicClient(api_key="sk-x")
    c._client = NS(messages=NS(create=lambda **kw: resp))
    assert c.complete(system="s", user="u", model="m", max_tokens=10) == '[{"i":0}]'


def test_anthropic_fetch_page_returns_document_text():
    from types import SimpleNamespace as NS

    from radar.llm.anthropic import AnthropicClient

    doc = NS(source=NS(data="---\ncanonical: https://x\n---\nThe article body."))
    ok_result = NS(type="web_fetch_result", content=doc)
    err_result = NS(type="web_fetch_tool_error", error_code="url_not_accessible")
    ok = NS(content=[NS(type="web_fetch_tool_result", content=ok_result)])
    err = NS(content=[NS(type="web_fetch_tool_result", content=err_result)])
    c = AnthropicClient(api_key="sk-x")
    c._client = NS(messages=NS(create=lambda **kw: ok))
    assert c.fetch_page("https://x", model="m") == "The article body."
    c._client = NS(messages=NS(create=lambda **kw: err))
    assert c.fetch_page("https://x", model="m") == ""
