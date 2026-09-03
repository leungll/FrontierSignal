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
