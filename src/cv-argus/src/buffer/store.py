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

**Live first, dead-letter on failure.** Every row has a `state`:
- `outbox`: just produced, waiting for the ESP32's next poll. Written to disk at once so a Pi
  reboot can't lose it, but this is the live path, not the queue: rows normally leave it within
  seconds.
- `dlq`: the ESP32 tried and said it couldn't deliver it (`FAIL <reason>`: no credentials, no
  network, ...), or never picked it up in time (`expire_outbox`). Kept with the ESP32's reason and
  the backend message it composed (`dlq_body`: GPS position and time of the event), and replayed
  oldest first once the backend is reachable again (`fetch_dlq`). This is the dead-letter queue.
- `sent`: the ESP32 delivered it, or it had nothing to send (a keepalive). Pruned after a day.
`sent` (0/1) is kept in step with `state` for older readers of this file.
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

# Columns added after the first release; `_init_schema` adds any that an older file lacks.
_ADDED_COLUMNS = {
    "mono_ms": "INTEGER",
    "boot_id": "TEXT",
    "clock_synced": "INTEGER",
    # The dead-letter queue (see the module docstring).
    "state": "TEXT",
    "dlq_error": "TEXT",
    "dlq_body": "TEXT",
    "dlq_at_ms": "INTEGER",
    "attempts": "INTEGER NOT NULL DEFAULT 0",
}

OUTBOX = "outbox"
DLQ = "dlq"
SENT = "sent"

# Rows the backend cares about: heartbeats and readings that changed the class or the severity.
# Keepalives only repeat a state the ESP32 already has, so they're never dead-lettered.
_BACKEND_BOUND = "(kind = 'route_status' OR json_extract(payload_json, '$.transition') = 1)"
_SELECT_ALERT = """
    SELECT id, kind, level, created_at_ms, source_id, payload_json, geolocation_json,
           mono_ms, boot_id, clock_synced
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
            existing = {row[1] for row in self._conn.execute("PRAGMA table_info(alerts)")}
            for column, sql_type in _ADDED_COLUMNS.items():
                if column not in existing:
                    self._conn.execute(f"ALTER TABLE alerts ADD COLUMN {column} {sql_type}")
            # Rows from before the dead-letter queue: unsent ones are simply still live.
            self._conn.execute(
                "UPDATE alerts SET state = CASE sent WHEN 1 THEN ? ELSE ? END WHERE state IS NULL",
                (SENT, OUTBOX),
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_alerts_state ON alerts (state, enqueued_at_ms)"
            )

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
                     sent, sent_at_ms, enqueued_at_ms, mono_ms, boot_id, clock_synced, state)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, NULL, ?, ?, ?, ?, ?)
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
                    data["mono_ms"],
                    data["boot_id"],
                    None if data["clock_synced"] is None else int(data["clock_synced"]),
                    OUTBOX,
                ),
            )

    def fetch_unsent(self, limit: int = _DEFAULT_FETCH_LIMIT) -> list[Alert]:
        """The live outbox, oldest first (rows are seconds old; order matters for an episode's
        medium -> critical). Never mutates anything -- see the module docstring. Safe to call
        repeatedly, which is exactly what makes "a not-yet-acked record is always safely
        re-served" possible in `sender/`. Dead-lettered rows are `fetch_dlq()`'s."""
        # Insertion order (rowid), not the wall clock: an NTP step backwards between two
        # enqueues must not serve the newer row first (CV-14).
        with self._lock:
            rows = self._conn.execute(
                _SELECT_ALERT + "FROM alerts WHERE state = ? ORDER BY rowid ASC LIMIT ?",
                (OUTBOX, limit),
            ).fetchall()
        return [_row_to_alert(row) for row in rows]

    def fetch_dlq(self, limit: int) -> list[tuple[Alert, dict]]:
        """Dead letters, oldest first, each with its `{error, body, failed_at_ms, attempts}`
        (`body` is the backend message the ESP32 composed when it failed, or `None`)."""
        with self._lock:
            rows = self._conn.execute(
                _SELECT_ALERT + """, dlq_error, dlq_body, dlq_at_ms, attempts
                FROM alerts WHERE state = ? ORDER BY rowid ASC LIMIT ?""",
                (DLQ, limit),
            ).fetchall()
        return [
            (
                _row_to_alert(row),
                {
                    "error": row[10],
                    "body": json.loads(row[11]) if row[11] else None,
                    "failed_at_ms": row[12],
                    "attempts": row[13],
                },
            )
            for row in rows
        ]

    def mark_dlq(self, alert_id: str, error: str, body: dict | None = None) -> bool:
        """The ESP32 couldn't deliver `alert_id`: move it to (or keep it in) the dead-letter
        queue with this reason. `body` replaces the stored message only when given, so a later
        failed replay without one keeps the original position and time. Returns whether a live
        or dead-lettered row with that id existed."""
        now_ms = int(time.time() * 1000)
        stored = json.dumps(body, separators=(",", ":")) if body is not None else None
        with self._lock:
            cur = self._conn.execute(
                """
                UPDATE alerts
                SET state = ?, dlq_error = ?,
                    dlq_body = COALESCE(?, dlq_body),
                    dlq_at_ms = COALESCE(dlq_at_ms, ?),
                    attempts = attempts + 1
                WHERE id = ? AND state IN (?, ?)
                """,
                (DLQ, error, stored, now_ms, alert_id, OUTBOX, DLQ),
            )
            return cur.rowcount == 1

    def expire_outbox(self, older_than_ms: int) -> tuple[int, int]:
        """Live rows the ESP32 didn't pick up before `older_than_ms` (epoch ms, by enqueue
        time): the link is down. Backend-bound ones (heartbeats, transitions) move to the
        dead-letter queue as `esp32_unreachable`, without a body (the ESP32 never saw them, so
        there's no position); keepalives are dropped. Returns `(dead_lettered, dropped)`."""
        now_ms = int(time.time() * 1000)
        with self._lock:
            moved = self._conn.execute(
                f"""
                UPDATE alerts
                SET state = ?, dlq_error = 'esp32_unreachable', dlq_at_ms = ?
                WHERE state = ? AND enqueued_at_ms < ? AND {_BACKEND_BOUND}
                """,
                (DLQ, now_ms, OUTBOX, older_than_ms),
            ).rowcount
            dropped = self._conn.execute(
                f"DELETE FROM alerts WHERE state = ? AND enqueued_at_ms < ? AND NOT {_BACKEND_BOUND}",
                (OUTBOX, older_than_ms),
            ).rowcount
        return moved, dropped

    def counts(self) -> dict:
        """`{outbox, dlq, sent, oldest_dlq_ms, dlq_reasons: {reason: n}}` for the logs."""
        with self._lock:
            by_state = dict(
                self._conn.execute("SELECT state, COUNT(*) FROM alerts GROUP BY state").fetchall()
            )
            oldest = self._conn.execute(
                "SELECT MIN(created_at_ms) FROM alerts WHERE state = ?", (DLQ,)
            ).fetchone()[0]
            reasons = dict(
                self._conn.execute(
                    "SELECT dlq_error, COUNT(*) FROM alerts WHERE state = ? GROUP BY dlq_error",
                    (DLQ,),
                ).fetchall()
            )
        return {
            OUTBOX: by_state.get(OUTBOX, 0),
            DLQ: by_state.get(DLQ, 0),
            SENT: by_state.get(SENT, 0),
            "oldest_dlq_ms": oldest,
            "dlq_reasons": reasons,
        }

    def mark_sent(self, ids: list[str]) -> int:
        """Mark exactly the given, not-yet-delivered ids (live or dead-lettered) as delivered.
        Returns how many rows were actually updated -- a caller can use a return smaller than
        `len(ids)` to notice an ACK for an id already marked sent by a previous run (not an
        error, just worth a debug log)."""
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        sent_at_ms = int(time.time() * 1000)
        with self._lock:
            cur = self._conn.execute(
                f"""
                UPDATE alerts
                SET sent = 1, sent_at_ms = ?, state = ?
                WHERE id IN ({placeholders}) AND sent = 0
                """,
                (sent_at_ms, SENT, *ids),
            )
            return cur.rowcount

    def unsent_count(self) -> int:
        """Rows not delivered yet: the live outbox plus the dead-letter queue."""
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM alerts WHERE sent = 0").fetchone()[0]

    def prune_sent(self, older_than_ms: int) -> int:
        """Delete rows the ESP32 already ACKed before `older_than_ms` (epoch ms). Sent rows are
        only kept for inspection; without pruning, the 2 s keepalives grow the file forever
        (BUF-01). Returns how many rows were deleted."""
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM alerts WHERE sent = 1 AND sent_at_ms < ?", (older_than_ms,)
            )
            return cur.rowcount

    def cap_unsent(self, max_rows: int) -> int:
        """If more than `max_rows` rows are waiting (live or dead-lettered: a long outage), drop
        the oldest *keepalive* readings first, since they only repeat a state already queued. If
        that isn't enough, drop the oldest of the rest too: the SD card is finite, and the newest
        rows describe the present (BUF-06). Returns how many rows were dropped."""
        with self._lock:
            excess = (
                self._conn.execute("SELECT COUNT(*) FROM alerts WHERE sent = 0").fetchone()[0]
                - max_rows
            )
            if excess <= 0:
                return 0
            cur = self._conn.execute(
                """
                DELETE FROM alerts WHERE rowid IN (
                    SELECT rowid FROM alerts
                    WHERE sent = 0 AND kind = 'drowsiness'
                      AND json_extract(payload_json, '$.transition') = 0
                    ORDER BY rowid ASC
                    LIMIT ?
                )
                """,
                (excess,),
            )
            dropped = cur.rowcount
            excess -= dropped
            if excess > 0:
                dropped += self._conn.execute(
                    """
                    DELETE FROM alerts WHERE rowid IN (
                        SELECT rowid FROM alerts WHERE sent = 0 ORDER BY rowid ASC LIMIT ?
                    )
                    """,
                    (excess,),
                ).rowcount
            return dropped

    def close(self) -> None:
        self._conn.close()


def _row_to_alert(row) -> Alert:
    return from_dict(
        {
            "id": row[0],
            "kind": row[1],
            "level": row[2],
            "created_at_ms": row[3],
            "source_id": row[4],
            "payload": json.loads(row[5]),
            "geolocation": json.loads(row[6]) if row[6] is not None else None,
            "mono_ms": row[7],
            "boot_id": row[8],
            "clock_synced": None if row[9] is None else bool(row[9]),
        }
    )


def open_buffer(db_path: Path | None = None) -> Buffer:
    """`db_path` defaults to `Path(BUFFER_DIR) / BUFFER_DB_FILENAME` (env vars, falling back to
    `constants.BUFFER_DIR_DEFAULT`/`BUFFER_DB_FILENAME_DEFAULT`)."""
    return Buffer(_buffer_path(db_path))
