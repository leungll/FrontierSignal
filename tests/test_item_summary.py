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
