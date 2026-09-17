import json

from radar.summarize import item as item_summary
from tests.conftest import make_item


class _Client:
    available = True

    def __init__(self):
        self.system = ""
        self.user = ""
        self.max_tokens = 0

    def complete(self, *, system, user, model, max_tokens):
        self.system = system
        self.user = user
        self.max_tokens = max_tokens
        return json.dumps(
            [
                {
                    "i": 0,
                    "summary": "Detailed technical summary. " * 20,
                    "why": "Concrete engineering consequence.",
                }
            ]
        )


def test_summary_returned_as_list_is_joined_not_reprd():
    # The model sometimes returns "summary" as a JSON array of bullets. It must be
    # joined with newlines, never str()-ed into a Python repr ("['- a', '\\n- b']").
    class _ListClient:
        available = True

        def complete(self, *, system, user, model, max_tokens):
            return json.dumps([{
                "i": 0,
                "summary": ["- First bullet.", "\n- Second bullet.", "- Third."],
                "why": "Consequence.",
            }])

    it = make_item("Title", summary="feed excerpt with plenty of length " * 10)
    item_summary.apply([it], client=_ListClient(), model="model", language="en")

    assert it.summary == "- First bullet.\n- Second bullet.\n- Third."
    assert "['" not in it.summary and "\\n" not in it.summary


class _NullClient:
    available = False

    def complete(self, *, system, user, model, max_tokens):  # pragma: no cover
        raise AssertionError("should not be called when unavailable")


def test_placeholder_body_marked_unavailable_in_prompt():
    # HN item with only the metrics placeholder and no full_text: the model must be
    # told the body is unavailable, not handed "53 points · 4 comments" as content.
    hn = make_item("Some HN Story", source_id="hackernews", source_name="Hacker News",
                   summary="53 points · 4 comments on Hacker News.")
    client = _Client()

    item_summary.apply([hn], client=client, model="model", language="en")

    assert "[body unavailable]" in client.user
    assert "53 points" not in client.user


def test_placeholder_replaced_when_summarizer_unavailable():
    # No LLM: the bare metrics line must never render as the summary.
    hn = make_item("Some HN Story", source_id="hackernews",
                   summary="53 points · 4 comments on Hacker News.")

    item_summary.apply([hn], client=_NullClient(), model="model", language="zh")

    assert "points" not in hn.summary
    assert "未能获取正文" in hn.summary


def test_thin_excerpt_marked_unavailable_and_replaced():
    # A one-line feed teaser (no full_text) is too thin to summarize. It must be
    # flagged unavailable in the prompt, and if the model returns only a `why`
    # (no summary), the teaser must not survive as the rendered summary.
    class _WhyOnly:
        available = True
        user = ""

        def complete(self, *, system, user, model, max_tokens):
            self.user = user
            return json.dumps([{"i": 0, "summary": "", "why": "Consequence."}])

    it = make_item("AEF-1 standard", source_id="latentspace",
                   source_name="Latent Space", summary="Pacing gathers pace.")
    client = _WhyOnly()

    item_summary.apply([it], client=client, model="model", language="en")

    assert "[body unavailable]" in client.user
    assert "Pacing gathers pace" not in it.summary
    assert "could not be retrieved" in it.summary
    assert it.why_it_matters == "Consequence."


def test_real_summary_is_kept_over_fallback():
    # When the model DOES return a real summary for a no-body item, keep it — the
    # fallback must not clobber a genuine rewrite.
    it = make_item("Some HN Story", source_id="hackernews",
                   summary="53 points · 4 comments on Hacker News.")
    item_summary.apply([it], client=_Client(), model="model", language="en")
    assert "Detailed technical summary" in it.summary


def test_placeholder_replaced_when_summarizer_fails():
    class _Boom:
        available = True

        def complete(self, *, system, user, model, max_tokens):
            raise RuntimeError("api down")

    hn = make_item("Some HN Story", source_id="hackernews",
                   summary="53 points · 4 comments on Hacker News.")

    item_summary.apply([hn], client=_Boom(), model="model", language="en")

    assert "points" not in hn.summary
    assert "could not be retrieved" in hn.summary


def test_summary_uses_full_text_and_requests_standalone_briefing():
    report_item = make_item("Title", summary="feed excerpt")
    report_item.full_text = "Full article evidence with benchmark results."
    client = _Client()

    item_summary.apply([report_item], client=client, model="model", language="en")

    assert "Full article evidence" in client.user
    assert "feed excerpt" not in client.user
    assert "3-4 short markdown" in client.system
    assert "bullet points" in client.system
    assert "Do not try to" in client.system
    assert '"前沿模型", not "frontier model"' in client.system
    assert "without opening the link" in client.system
    assert client.max_tokens == 8192
    assert len(report_item.summary) > 280
