"""`Buffer` — the SQLite-backed local alert queue.

Why SQLite: it isn't a server (no daemon, no port) — it's a library that opens one file
directly from within whatever process calls it, which is exactly what a Pi that may lose
connectivity mid-trip needs: alerts persist to disk regardless of whether anything is around to
read them yet (see `src/cv-argus/CLAUDE.md`'s "`buffer/`'s SQLite file needs a volume, not a
container").

One shared table, one row shape (`alerts/`'s `Alert` envelope) — matching the draw.io diagram's
single "Queue Message Local Buffer (SQLite)" box, not a table per alert kind.

**Concurrency**: this file is written by `orchestrator/`'s decision-loop thread (`enqueue`) and
read-then-written by `sender/`'s accept-loop thread (`fetch_unsent` then, only after an ACK,
`mark_sent`) — two different threads touching the same file, which is the project's actual
"concurrencia real" argument (see the thesis draft's Parte 7). `PRAGMA journal_mode=WAL` lets
readers proceed without blocking on the writer, but WAL alone doesn't make two Python threads'
*writes* atomic with respect to each other — a `threading.Lock` around every write method
handles that. One `Buffer` instance should be constructed once (in `main.py`) and shared by both
callers, rather than each opening its own connection to the same file, so the lock is actually
shared and effective.

**Sent tracking**: `fetch_unsent()` is a pure read — it never flips `sent`. The only method that
does is `mark_sent()`, which `sender/` calls exclusively after an actual ACK from the ESP32
(never right after handing rows over) — see `sender/server.py`. This structurally enforces
"mark sent only on ACK" rather than relying on every caller to remember the rule.
"""

import json
import sqlite3
import threading
import time
from pathlib import Path

from .. import constants
from ..alerts import Alert, from_dict, to_dict

_SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    id               TEXT PRIMARY KEY,
    kind             TEXT NOT NULL,
    level            INTEGER,
    created_at_ms    INTEGER NOT NULL,
    source_id        TEXT NOT NULL,
    payload_json     TEXT NOT NULL,
    geolocation_json TEXT,
    sent             INTEGER NOT NULL DEFAULT 0,
    sent_at_ms       INTEGER,
    enqueued_at_ms   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alerts_unsent ON alerts (sent, enqueued_at_ms);
"""

_DEFAULT_FETCH_LIMIT = 50


def _buffer_path(db_path: Path | None) -> Path:
    if db_path is not None:
        return db_path
    import os

    directory = os.environ.get("BUFFER_DIR", constants.BUFFER_DIR_DEFAULT)
    filename = os.environ.get("BUFFER_DB_FILENAME", constants.BUFFER_DB_FILENAME_DEFAULT)
    return Path(directory) / filename


class Buffer:
    """One connection to the alert-queue SQLite file. Construct via `open_buffer()`, not
    directly, so the `BUFFER_DIR`/`BUFFER_DB_FILENAME` env-var defaults stay in one place."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(db_path),
            # orchestrator's thread opens this Buffer; sender's thread calls into the same
            # instance -- see the module docstring's concurrency section.
            check_same_thread=False,
            isolation_level=None,  # autocommit; each statement below is already one transaction
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")  # standard WAL pairing
        # Let SQLite's own lock-wait retry absorb transient contention (e.g. a concurrent
        # `sqlite3` CLI inspection during a demo) instead of raising immediately.
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(_SCHEMA)

    def enqueue(self, alert: Alert) -> None:
        """Insert one row. Idempotent on `alert.id` (`INSERT OR IGNORE`) -- a caller that
        accidentally re-submits the same `Alert` object doesn't create a duplicate row."""
        data = to_dict(alert)
        # Python-side wall clock, not SQLite's strftime('%s','now') -- that only has
        # second-level resolution, which would make ordering ties among alerts enqueued within
        # the same second effectively arbitrary (a real risk during a burst of detections, not
        # just in fast test loops).
        enqueued_at_ms = int(time.time() * 1000)
        with self._lock:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO alerts
                    (id, kind, level, created_at_ms, source_id, payload_json, geolocation_json,
                     sent, sent_at_ms, enqueued_at_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, NULL, ?)
                """,
                (
                    data["id"],
                    data["kind"],
                    data["level"],
                    data["created_at_ms"],
                    data["source_id"],
                    json.dumps(data["payload"]),
                    json.dumps(data["geolocation"]) if data["geolocation"] is not None else None,
                    enqueued_at_ms,
                ),
            )

    def fetch_unsent(self, limit: int = _DEFAULT_FETCH_LIMIT) -> list[Alert]:
        """Oldest-first, never mutates `sent` -- see the module docstring. Safe to call
        repeatedly/idempotently, which is exactly what makes "a not-yet-acked record is always
        safely re-served" possible in `sender/`."""
        rows = self._conn.execute(
            """
            SELECT id, kind, level, created_at_ms, source_id, payload_json, geolocation_json
            FROM alerts
            WHERE sent = 0
            ORDER BY enqueued_at_ms ASC, rowid ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [
            from_dict(
                {
                    "id": row[0],
                    "kind": row[1],
                    "level": row[2],
                    "created_at_ms": row[3],
                    "source_id": row[4],
                    "payload": json.loads(row[5]),
                    "geolocation": json.loads(row[6]) if row[6] is not None else None,
                }
            )
            for row in rows
        ]

    def mark_sent(self, ids: list[str]) -> int:
        """Flip `sent` for exactly the given, still-unsent ids. Returns how many rows were
        actually updated -- a caller can use a return smaller than `len(ids)` to notice an ACK
        for an id already marked sent by a previous run (not an error, just worth a debug log)."""
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        sent_at_ms = int(time.time() * 1000)
        with self._lock:
            cur = self._conn.execute(
                f"""
                UPDATE alerts
                SET sent = 1, sent_at_ms = ?
                WHERE id IN ({placeholders}) AND sent = 0
                """,
                (sent_at_ms, *ids),
            )
            return cur.rowcount

    def unsent_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM alerts WHERE sent = 0").fetchone()[0]

    def close(self) -> None:
        self._conn.close()


def open_buffer(db_path: Path | None = None) -> Buffer:
    """`db_path` defaults to `Path(BUFFER_DIR) / BUFFER_DB_FILENAME` (env vars, falling back to
    `constants.BUFFER_DIR_DEFAULT`/`BUFFER_DB_FILENAME_DEFAULT`)."""
    return Buffer(_buffer_path(db_path))
