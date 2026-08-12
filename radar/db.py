"""SQLite state. Schema is versioned via PRAGMA user_version."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from radar.models import Item

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    canonical_url TEXT PRIMARY KEY,
    source_id     TEXT NOT NULL,
    source_name   TEXT NOT NULL,
    title         TEXT NOT NULL,
    url           TEXT NOT NULL,
    summary       TEXT NOT NULL DEFAULT '',
    published_at  TEXT,
    first_seen_at TEXT NOT NULL,
    score         REAL NOT NULL DEFAULT 0,
    killed_by     TEXT,
    reported_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_first_seen ON items(first_seen_at);
CREATE INDEX IF NOT EXISTS idx_items_reported   ON items(reported_at);

-- HTTP conditional-request cache: a 304 costs nothing and saves ~70% fetch time.
CREATE TABLE IF NOT EXISTS source_state (
    source_id      TEXT PRIMARY KEY,
    etag           TEXT,
    last_modified  TEXT,
    last_fetch_at  TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_error     TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    ok          INTEGER NOT NULL DEFAULT 0,
    found       INTEGER NOT NULL DEFAULT 0,
    new_items   INTEGER NOT NULL DEFAULT 0,
    reported    INTEGER NOT NULL DEFAULT 0
);

-- Local-embedding cache, keyed by content hash. bge-small is cheap but not free
-- (CPU time); caching keeps re-runs and the cross-day window from recomputing.
CREATE TABLE IF NOT EXISTS embeddings (
    content_hash TEXT PRIMARY KEY,
    vector       TEXT NOT NULL,   -- JSON array of floats
    created_at   TEXT NOT NULL
);
"""


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        yield conn
        conn.commit()
    finally:
        conn.close()


def migrate(conn: sqlite3.Connection) -> None:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current >= SCHEMA_VERSION:
        return
    conn.executescript(SCHEMA)
    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")


def last_successful_run(conn: sqlite3.Connection) -> datetime | None:
    """Fetch window anchor. Deriving from this (not wall-clock) makes a missed
    run self-healing: the next run backfills instead of leaving a permanent gap."""
    row = conn.execute(
        "SELECT started_at FROM runs WHERE ok = 1 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return datetime.fromisoformat(row["started_at"])


def fetch_since(conn: sqlite3.Connection, cold_start_days: int) -> datetime:
    last = last_successful_run(conn)
    if last is None:
        return datetime.now(UTC) - timedelta(days=cold_start_days)
    return last


def start_run(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        "INSERT INTO runs (started_at) VALUES (?)", (datetime.now(UTC).isoformat(),)
    )
    return cur.lastrowid


def finish_run(
    conn: sqlite3.Connection, run_id: int, *, ok: bool, found: int, new: int, reported: int
) -> None:
    conn.execute(
        "UPDATE runs SET finished_at=?, ok=?, found=?, new_items=?, reported=? WHERE id=?",
        (datetime.now(UTC).isoformat(), int(ok), found, new, reported, run_id),
    )


def known_urls(conn: sqlite3.Connection, urls: list[str]) -> set[str]:
    """Which of these canonical URLs have we already stored?"""
    if not urls:
        return set()
    out: set[str] = set()
    # Chunk to stay under SQLite's variable limit.
    for i in range(0, len(urls), 500):
        chunk = urls[i : i + 500]
        placeholders = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"SELECT canonical_url FROM items WHERE canonical_url IN ({placeholders})",
            chunk,
        ).fetchall()
        out.update(r["canonical_url"] for r in rows)
    return out


def insert_items(conn: sqlite3.Connection, items: list[Item]) -> None:
    now = datetime.now(UTC).isoformat()
    conn.executemany(
        """INSERT OR IGNORE INTO items
           (canonical_url, source_id, source_name, title, url, summary,
            published_at, first_seen_at, score, killed_by)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        [
            (
                i.canonical_url,
                i.source_id,
                i.source_name,
                i.title,
                i.url,
                i.summary,
                i.published_at.isoformat() if i.published_at else None,
                now,
                i.score,
                i.killed_by,
            )
            for i in items
        ],
    )


def reported_since(conn: sqlite3.Connection, days: int) -> list[sqlite3.Row]:
    """Items that were pushed to a report in the last `days` days — the raw
    material for the weekly deep dive. Ordered best-first by score."""
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    return conn.execute(
        """SELECT source_id, source_name, title, url, summary, published_at, score
           FROM items
           WHERE reported_at IS NOT NULL AND reported_at >= ?
           ORDER BY score DESC""",
        (cutoff,),
    ).fetchall()


def mark_reported(conn: sqlite3.Connection, urls: list[str]) -> None:
    now = datetime.now(UTC).isoformat()
    conn.executemany(
        "UPDATE items SET reported_at=? WHERE canonical_url=?", [(now, u) for u in urls]
    )


def load_embeddings(conn: sqlite3.Connection, hashes: list[str]) -> dict[str, list[float]]:
    """Fetch cached vectors for these content hashes. Chunked to stay under the
    SQLite variable limit."""
    import json

    if not hashes:
        return {}
    out: dict[str, list[float]] = {}
    for i in range(0, len(hashes), 500):
        chunk = hashes[i : i + 500]
        placeholders = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"SELECT content_hash, vector FROM embeddings WHERE content_hash IN ({placeholders})",
            chunk,
        ).fetchall()
        for r in rows:
            out[r["content_hash"]] = json.loads(r["vector"])
    return out


def save_embeddings(conn: sqlite3.Connection, vectors: dict[str, list[float]]) -> None:
    import json

    now = datetime.now(UTC).isoformat()
    conn.executemany(
        "INSERT OR IGNORE INTO embeddings (content_hash, vector, created_at) VALUES (?,?,?)",
        [(h, json.dumps(v), now) for h, v in vectors.items()],
    )


def get_source_state(conn: sqlite3.Connection, source_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM source_state WHERE source_id=?", (source_id,)
    ).fetchone()


def save_source_success(
    conn: sqlite3.Connection, source_id: str, etag: str | None, last_modified: str | None
) -> None:
    conn.execute(
        """INSERT INTO source_state (source_id, etag, last_modified, last_fetch_at,
                                     consecutive_failures, last_error)
           VALUES (?,?,?,?,0,NULL)
           ON CONFLICT(source_id) DO UPDATE SET
             etag=excluded.etag, last_modified=excluded.last_modified,
             last_fetch_at=excluded.last_fetch_at,
             consecutive_failures=0, last_error=NULL""",
        (source_id, etag, last_modified, datetime.now(UTC).isoformat()),
    )


def save_source_failure(conn: sqlite3.Connection, source_id: str, error: str) -> int:
    """Returns the new consecutive-failure count so dead sources can be surfaced."""
    conn.execute(
        """INSERT INTO source_state (source_id, last_fetch_at, consecutive_failures, last_error)
           VALUES (?,?,1,?)
           ON CONFLICT(source_id) DO UPDATE SET
             last_fetch_at=excluded.last_fetch_at,
             consecutive_failures=source_state.consecutive_failures + 1,
             last_error=excluded.last_error""",
        (source_id, datetime.now(UTC).isoformat(), error),
    )
    row = conn.execute(
        "SELECT consecutive_failures FROM source_state WHERE source_id=?", (source_id,)
    ).fetchone()
    return row["consecutive_failures"] if row else 1
