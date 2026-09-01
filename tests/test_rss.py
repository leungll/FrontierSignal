import asyncio

from radar import db
from radar.models import Source
from radar.sources import rss


def test_dead_source_contains_latest_error_after_three_failed_runs(monkeypatch, tmp_path):
    async def fail(*args, **kwargs):
        raise rss.FetchError("ReadTimeout: request timed out")

    monkeypatch.setattr(rss, "fetch_source", fail)
    source = Source(
        id="bair",
        name="BAIR",
        url="https://bairblog.github.io/feed.xml",
    )

    with db.connect(tmp_path / "radar.db") as conn:
        db.migrate(conn)
        dead = []
        for _ in range(3):
            _, dead = asyncio.run(
                rss.fetch_all(
                    conn,
                    [source],
                    since=db.fetch_since(conn, 3),
                    timeout=1,
                    concurrency=1,
                    cap=10,
                )
            )

    assert dead == ["bair: ReadTimeout: request timed out"]
