import asyncio

import httpx
import respx

from radar.sources import fulltext
from tests.conftest import make_item


def test_extract_keeps_article_content_and_drops_navigation():
    html = """
    <html><body>
      <nav><p>This navigation text should not be included in the article.</p></nav>
      <article>
        <h1>Inference Engine</h1>
        <p>The engine batches requests continuously to improve GPU utilization.</p>
        <p>Tests report 2.4x higher throughput at the same latency target.</p>
      </article>
    </body></html>
    """
    text = fulltext.extract(html)
    assert "batches requests continuously" in text
    assert "2.4x higher throughput" in text
    assert "navigation text" not in text


def test_enrich_attaches_html_text_to_selected_item():
    item = make_item("Engine", summary="short feed excerpt", url="https://example.com/post")
    paragraphs = "".join(
        f"<p>Technical paragraph {i} with enough useful detail.</p>" for i in range(8)
    )

    with respx.mock:
        respx.get("https://example.com/post").mock(
            return_value=httpx.Response(
                200,
                text=f"<article>{paragraphs}</article>",
                headers={"content-type": "text/html"},
            )
        )
        asyncio.run(fulltext.enrich([item], timeout=1))

    assert "Technical paragraph 0" in item.full_text
    assert len(item.full_text) >= 200


def test_enrich_retries_once_on_transient_status():
    item = make_item("Engine", url="https://example.com/flaky")
    paragraphs = "".join(
        f"<p>Technical paragraph {i} with enough useful detail.</p>" for i in range(8)
    )

    with respx.mock:
        route = respx.get("https://example.com/flaky")
        route.side_effect = [
            httpx.Response(503),  # transient — should trigger one retry
            httpx.Response(
                200, text=f"<article>{paragraphs}</article>",
                headers={"content-type": "text/html"},
            ),
        ]
        asyncio.run(fulltext.enrich([item], timeout=1))

    assert route.call_count == 2
    assert "Technical paragraph 0" in item.full_text


def test_enrich_does_not_retry_permanent_404():
    item = make_item("Gone", url="https://example.com/missing")
    with respx.mock:
        route = respx.get("https://example.com/missing").mock(
            return_value=httpx.Response(404)
        )
        asyncio.run(fulltext.enrich([item], timeout=1))

    assert route.call_count == 1  # no wasted retry on a permanent miss
    assert item.full_text == ""


def test_enrich_skips_thin_page():
    item = make_item("Thin", url="https://example.com/thin")
    with respx.mock:
        respx.get("https://example.com/thin").mock(
            return_value=httpx.Response(
                200, text="<article><p>Too short.</p></article>",
                headers={"content-type": "text/html"},
            )
        )
        asyncio.run(fulltext.enrich([item], timeout=1))

    assert item.full_text == ""  # below MIN_BODY_CHARS, not attached


BODY = "Recovered article body with enough technical detail. " * 10


def test_enrich_falls_back_when_direct_fetch_is_blocked():
    item = make_item("Launch", url="https://example.com/blocked")
    calls: list[str] = []

    def fallback(url: str) -> str:
        calls.append(url)
        return BODY

    with respx.mock:
        respx.get("https://example.com/blocked").mock(return_value=httpx.Response(403))
        asyncio.run(fulltext.enrich([item], timeout=1, fallback=fallback))

    assert calls == ["https://example.com/blocked"]
    assert item.full_text.startswith("Recovered article body")


def test_enrich_skips_fallback_for_permanent_miss_and_success():
    gone = make_item("Gone", url="https://example.com/missing")
    ok = make_item("Ok", url="https://example.com/ok")
    calls: list[str] = []
    paragraphs = "".join(
        f"<p>Technical paragraph {i} with enough useful detail.</p>" for i in range(8)
    )

    with respx.mock:
        respx.get("https://example.com/missing").mock(return_value=httpx.Response(404))
        respx.get("https://example.com/ok").mock(
            return_value=httpx.Response(
                200, text=f"<article>{paragraphs}</article>", headers={"content-type": "text/html"}
            )
        )
        asyncio.run(
            fulltext.enrich([gone, ok], timeout=1, fallback=lambda u: calls.append(u) or BODY)
        )

    assert calls == []  # 404 is unfixable; a direct success needs no fallback
    assert gone.full_text == ""


def test_enrich_survives_failing_or_thin_fallback():
    a = make_item("A", url="https://example.com/a")
    b = make_item("B", url="https://example.com/b")

    def fallback(url: str) -> str:
        if url.endswith("/a"):
            raise RuntimeError("api down")
        return "too short"

    with respx.mock:
        respx.get("https://example.com/a").mock(return_value=httpx.Response(403))
        respx.get("https://example.com/b").mock(return_value=httpx.Response(403))
        asyncio.run(fulltext.enrich([a, b], timeout=1, fallback=fallback))

    assert a.full_text == "" and b.full_text == ""
