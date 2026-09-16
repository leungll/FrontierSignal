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
