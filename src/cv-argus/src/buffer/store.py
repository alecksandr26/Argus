"""`Buffer` — the SQLite-backed local alert queue.

Why SQLite: it isn't a server (no daemon, no port) — it's a library that opens one file
directly from within whatever process calls it, which is exactly what a Pi that may lose
connectivity mid-trip needs: alerts persist to disk regardless of whether anything is around to
read them yet (see `src/cv-argus/CLAUDE.md`'s "`buffer/`'s SQLite file needs a volume, not a
container").

One shared table, one row shape (`alerts/`'s `Alert` envelope) — matching the draw.io diagram's
single "Queue Message Local Buffer (SQLite)" box, not a table per alert kind. A second table,
`archive`, keeps what was given up on (see below).

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

**Live first, dead-letter on failure** (Bluetooth protocol v2, contract C1.6). Every row has a
`state`:
- `outbox`: just produced, waiting for the ESP32's next poll. Written to disk at once so a Pi
  reboot can't lose it, but this is the live path, not the queue: rows normally leave it within
  seconds.
- `dlq`: the dead-letter queue. It holds **critical alerts only** (a *critical event row*: a
  severity transition into `critical` from anything lower). A critical goes there when the ESP32
  reports it couldn't deliver it (`FAIL <reason>`: no network, timeout, ...) or never picked it up
  in time (`expire_outbox`, reason `esp32_unreachable`). It keeps the ESP32's reason and the
  backend event it composed (`dlq_body`: GPS position and time of the event), and is replayed
  **newest first, round-robin** (`serve_dlq`), one row per session. Each failed attempt counts;
  the live failure that dead-letters it is attempt 1, and `BUFFER_DLQ_MAX_ATTEMPTS` (5) archive it.
- `sent`: the ESP32 delivered it, or it had nothing to send (a keepalive). Pruned after a day.
`sent` (0/1) is kept in step with `state` for older readers of this file.

**Lease** (`TAKEN`). The ESP32's sender task holds the event rows it took from a session and
reports each later with ACK or FAIL. `lease()` hides them for `BUFFER_LEASE_SECONDS` (120 s) from
`fetch_unsent`, `fetch_dlq`/`serve_dlq`, `expire_outbox` and the waiting counts, so they aren't
served twice or expired while in flight. An expired lease just makes the row visible again in the
state it had; it is not an attempt. ACK and FAIL clear it.

**Archive.** Everything given up on moves to the `archive` table (same columns plus
`archived_at_ms` and `archive_reason`), in one transaction under the write lock: a medium/OK the
ESP32 couldn't deliver in its retry window, a critical the backend rejected for good
(`BUFFER_ARCHIVE_NOW_REASONS`) or that used up its attempts, a non-critical event the ESP32 never
picked up, rows over `BUFFER_MAX_UNSENT_ROWS`. Archived rows are **never deleted, never served,
never resent**; `python -m cv_argus.buffer.status` shows them.
"""

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path

from .. import constants
from ..alerts import Alert, from_dict, to_dict

logger = logging.getLogger(__name__)

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
    # Protocol v2 (contract C1.6).
    "lease_until_ms": "INTEGER",
    "dlq_round": "INTEGER NOT NULL DEFAULT 0",
    "last_error": "TEXT",
}

# Every column of `alerts`, in order: the archive copies them all.
_ALERT_COLUMNS = (
    "id", "kind", "level", "created_at_ms", "source_id", "payload_json", "geolocation_json",
    "sent", "sent_at_ms", "enqueued_at_ms", *_ADDED_COLUMNS,
)
_COLUMN_LIST = ", ".join(_ALERT_COLUMNS)

_ARCHIVE_SCHEMA = """
CREATE TABLE IF NOT EXISTS archive (
    id               TEXT PRIMARY KEY,
    kind             TEXT NOT NULL,
    level            INTEGER,
    created_at_ms    INTEGER NOT NULL,
    source_id        TEXT NOT NULL,
    payload_json     TEXT NOT NULL,
    geolocation_json TEXT,
    sent             INTEGER NOT NULL DEFAULT 0,
    sent_at_ms       INTEGER,
    enqueued_at_ms   INTEGER NOT NULL,
    mono_ms          INTEGER,
    boot_id          TEXT,
    clock_synced     INTEGER,
    state            TEXT,
    dlq_error        TEXT,
    dlq_body         TEXT,
    dlq_at_ms        INTEGER,
    attempts         INTEGER NOT NULL DEFAULT 0,
    lease_until_ms   INTEGER,
    dlq_round        INTEGER NOT NULL DEFAULT 0,
    last_error       TEXT,
    archived_at_ms   INTEGER NOT NULL,
    archive_reason   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_archive_when ON archive (archived_at_ms);
"""

OUTBOX = "outbox"
DLQ = "dlq"
SENT = "sent"

# Reason a row moved to the archive when it isn't a FAIL reason from the ESP32.
ARCHIVE_ESP32_UNREACHABLE = "esp32_unreachable"
ARCHIVE_MAX_ATTEMPTS = "max_attempts"
ARCHIVE_CAP_UNSENT = "cap_unsent"
ARCHIVE_MIGRATED = "migrated_v2"
# The FAIL reason for a DLQ replay a live event pre-empted: not an attempt (contract C1.6).
FAIL_PREEMPTED = "preempted"

# Outcomes of `apply_fail`.
FAIL_ARCHIVED = "archived"
FAIL_DEAD_LETTERED = "dlq"
FAIL_REQUEUED = "requeued"

# --- Which rows are backend events (contract C1.5; the ESP32 uses the same rule) --------------
_TRANSITION = "json_extract(payload_json, '$.transition') = 1"
_SEV = "json_extract(payload_json, '$.severity')"
_PREV = "COALESCE(json_extract(payload_json, '$.previous_severity'), 'none')"
# A raise into critical (from none/low) or an escalation (from medium). The only rows the DLQ holds.
_CRITICAL_EVENT = (
    f"(kind = 'drowsiness' AND {_TRANSITION} AND {_SEV} = 'critical' AND {_PREV} != 'critical')"
)
# Medium alert, critical alert, or driver OK (the recovery from medium/critical).
_EVENT = (
    f"(kind = 'drowsiness' AND {_TRANSITION} AND ("
    f"({_SEV} = 'medium' AND {_PREV} IN ('none', 'low'))"
    f" OR ({_SEV} = 'critical' AND {_PREV} != 'critical')"
    f" OR ({_SEV} IN ('none', 'low') AND {_PREV} IN ('medium', 'critical'))))"
)
_NOT_LEASED = "(lease_until_ms IS NULL OR lease_until_ms <= ?)"

_SELECT_ALERT = """
    SELECT id, kind, level, created_at_ms, source_id, payload_json, geolocation_json,
           mono_ms, boot_id, clock_synced
"""

_DEFAULT_FETCH_LIMIT = 50


def event_kind(payload: dict) -> str | None:
    """Contract C1.5 in Python: `"medium"`/`"critical"` for an alert record, `"ok"` for a driver
    OK (the recovery), `None` for anything that never reaches the backend (keepalives,
    `none<->low`, `critical->medium`, class-only changes)."""
    if payload.get("transition") is not True:
        return None
    sev = payload.get("severity")
    prev = payload.get("previous_severity") or "none"
    # Written exactly like the SQL predicates above, so both always agree.
    if sev == "medium" and prev in ("none", "low"):
        return "medium"
    if sev == "critical" and prev != "critical":
        return "critical"
    if sev in ("none", "low") and prev in ("medium", "critical"):
        return "ok"
    return None


def is_critical_event(alert: Alert) -> bool:
    """The only kind of row the dead-letter queue holds (contract C1.5)."""
    return alert.kind.value == "drowsiness" and event_kind(alert.payload) == "critical"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _buffer_path(db_path: Path | None) -> Path:
    if db_path is not None:
        return db_path
    import os

    directory = os.environ.get("BUFFER_DIR", constants.BUFFER_DIR_DEFAULT)
    filename = os.environ.get("BUFFER_DB_FILENAME", constants.BUFFER_DB_FILENAME_DEFAULT)
    return Path(directory) / filename


class Buffer:
    """One connection to the alert-queue SQLite file. Construct via `open_buffer()`, not
    directly, so the `BUFFER_DIR`/`BUFFER_DB_FILENAME` env-var defaults stay in one place.

    Every method that depends on the time takes an optional `now_ms` (wall-clock epoch ms) so
    tests can drive leases and ages; production callers leave it out."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(db_path),
            # orchestrator's thread opens this Buffer; sender's thread calls into the same
            # instance -- see the module docstring's concurrency section.
            check_same_thread=False,
            isolation_level=None,  # autocommit; multi-statement moves use BEGIN/COMMIT below
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")  # standard WAL pairing
        # Let SQLite's own lock-wait retry absorb transient contention (e.g. a concurrent
        # `sqlite3` CLI inspection during a demo) instead of raising immediately.
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._init_schema()

    # --- schema + migration ---------------------------------------------------------------

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.executescript(_ARCHIVE_SCHEMA)
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
            self._migrate_v2()

    def _migrate_v2(self) -> None:
        """Protocol v2 (contract C1.6), idempotent: the heartbeat kind is gone and the DLQ holds
        criticals only, so unsent `route_status` rows and non-critical dead letters move to the
        archive (`migrated_v2`); a remaining dead letter already at the attempt limit is archived
        as `max_attempts`. Caller holds the lock."""
        now = _now_ms()
        self._archive_where(
            f"(kind = 'route_status' AND state != '{SENT}')"
            f" OR (state = '{DLQ}' AND NOT {_CRITICAL_EVENT})",
            (), ARCHIVE_MIGRATED, now,
        )
        self._archive_where(
            f"state = '{DLQ}' AND attempts >= ?",
            (constants.BUFFER_DLQ_MAX_ATTEMPTS,), ARCHIVE_MAX_ATTEMPTS, now,
        )

    def _archive_where(self, where: str, params: tuple, reason: str, now_ms: int) -> int:
        """Moves the matching `alerts` rows to `archive` in one transaction. Caller holds the
        lock. Returns how many moved."""
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            self._conn.execute(
                f"""
                INSERT OR REPLACE INTO archive ({_COLUMN_LIST}, archived_at_ms, archive_reason)
                SELECT {_COLUMN_LIST}, ?, ? FROM alerts WHERE {where} ORDER BY rowid
                """,
                (now_ms, reason, *params),
            )
            moved = self._conn.execute(f"DELETE FROM alerts WHERE {where}", params).rowcount
            self._conn.execute("COMMIT")
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        return moved

    def _next_dlq_round(self) -> int:
        """A row entering the DLQ joins the current round (contract C1.6): served ahead of
        older rows, but it can't take several attempts back to back. Caller holds the lock."""
        return self._conn.execute(
            "SELECT COALESCE(MIN(dlq_round), 0) FROM alerts WHERE state = ?", (DLQ,)
        ).fetchone()[0]

    # --- writes from the orchestrator -----------------------------------------------------

    def enqueue(self, alert: Alert) -> None:
        """Insert one row. Idempotent on `alert.id` (`INSERT OR IGNORE`) -- a caller that
        accidentally re-submits the same `Alert` object doesn't create a duplicate row."""
        data = to_dict(alert)
        # Python-side wall clock, not SQLite's strftime('%s','now') -- that only has
        # second-level resolution, which would make ordering ties among alerts enqueued within
        # the same second effectively arbitrary (a real risk during a burst of detections, not
        # just in fast test loops).
        enqueued_at_ms = _now_ms()
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

    # --- reads for the sender -------------------------------------------------------------

    def fetch_unsent(self, limit: int = _DEFAULT_FETCH_LIMIT, *, now_ms: int | None = None) -> list[Alert]:
        """The live outbox, oldest first (rows are seconds old; order matters for an episode's
        medium -> critical), leased rows skipped. Never mutates anything -- see the module
        docstring. Safe to call repeatedly, which is exactly what makes "a not-yet-acked record
        is always safely re-served" possible in `sender/`. Dead letters are `serve_dlq()`'s."""
        now = _now_ms() if now_ms is None else now_ms
        # Insertion order (rowid), not the wall clock: an NTP step backwards between two
        # enqueues must not serve the newer row first (CV-14).
        with self._lock:
            rows = self._conn.execute(
                _SELECT_ALERT
                + f"FROM alerts WHERE state = ? AND {_NOT_LEASED} ORDER BY rowid ASC LIMIT ?",
                (OUTBOX, now, limit),
            ).fetchall()
        return [_row_to_alert(row) for row in rows]

    def peek(self, ids: list[str]) -> list[Alert]:
        """The not-yet-delivered rows (`outbox`/`dlq`, leased or not) with these ids, in
        insertion order. Read-only; the sender uses it to log what a later ACK/FAIL is about."""
        if not ids:
            return []
        with self._lock:
            rows = self._conn.execute(
                _SELECT_ALERT + f"""FROM alerts WHERE id IN ({_marks(ids)}) AND state IN (?, ?)
                ORDER BY rowid ASC""",
                (*ids, OUTBOX, DLQ),
            ).fetchall()
        return [_row_to_alert(row) for row in rows]

    def fetch_dlq(self, limit: int = 1, *, now_ms: int | None = None) -> list[tuple[Alert, dict]]:
        """The dead letters `serve_dlq` would hand out next, without serving them: non-leased,
        smallest `dlq_round` first, then newest first. Each comes with the C1.4 `dlq` object
        `{error, body, failed_at_ms, attempts, queued_s}` (`body` is the backend event the ESP32
        composed when it first failed, or `None`)."""
        now = _now_ms() if now_ms is None else now_ms
        with self._lock:
            return self._select_dlq(limit, now)

    def serve_dlq(self, limit: int = 1, *, now_ms: int | None = None) -> list[tuple[Alert, dict]]:
        """`fetch_dlq`, and each row's `dlq_round` goes up by one at serve time (whether or not
        the ESP32 later takes it), so the next request gets the next row: newest first,
        round-robin (contract C1.6)."""
        now = _now_ms() if now_ms is None else now_ms
        with self._lock:
            letters = self._select_dlq(limit, now)
            if letters:
                ids = [alert.id for alert, _ in letters]
                self._conn.execute(
                    f"UPDATE alerts SET dlq_round = dlq_round + 1 WHERE id IN ({_marks(ids)})",
                    ids,
                )
            return letters

    def _select_dlq(self, limit: int, now: int) -> list[tuple[Alert, dict]]:
        rows = self._conn.execute(
            _SELECT_ALERT + f""", dlq_error, dlq_body, dlq_at_ms, attempts
            FROM alerts WHERE state = ? AND {_NOT_LEASED}
            ORDER BY dlq_round ASC, rowid DESC LIMIT ?""",
            (DLQ, now, limit),
        ).fetchall()
        return [
            (
                _row_to_alert(row),
                {
                    "error": row[10],
                    "body": json.loads(row[11]) if row[11] else None,
                    "failed_at_ms": row[12],
                    "attempts": row[13],
                    "queued_s": max(0, (now - row[12]) // 1000) if row[12] is not None else 0,
                },
            )
            for row in rows
        ]

    def list_dlq(self, limit: int = 20, *, now_ms: int | None = None) -> list[dict]:
        """Every dead letter (leased ones too) in serve order, for `buffer.status`: the `Alert`
        plus `error, body, failed_at_ms, attempts, round, leased_s` (seconds of lease left, 0
        when not leased)."""
        now = _now_ms() if now_ms is None else now_ms
        with self._lock:
            rows = self._conn.execute(
                _SELECT_ALERT + """, dlq_error, dlq_body, dlq_at_ms, attempts, dlq_round,
                       lease_until_ms
                FROM alerts WHERE state = ? ORDER BY dlq_round ASC, rowid DESC LIMIT ?""",
                (DLQ, limit),
            ).fetchall()
        return [
            {
                "alert": _row_to_alert(row),
                "error": row[10],
                "body": json.loads(row[11]) if row[11] else None,
                "failed_at_ms": row[12],
                "attempts": row[13],
                "round": row[14],
                "leased_s": max(0, (row[15] - now) // 1000) if row[15] and row[15] > now else 0,
            }
            for row in rows
        ]

    def list_archive(self, limit: int = 20) -> list[dict]:
        """The newest archived rows, raw (an archived row may be a kind this release no longer
        reads, e.g. a migrated `route_status`), for `buffer.status`."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT id, kind, created_at_ms, payload_json, archived_at_ms, archive_reason,
                          attempts, last_error, dlq_error
                   FROM archive ORDER BY archived_at_ms DESC, rowid DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [
            {
                "id": r[0], "kind": r[1], "created_at_ms": r[2],
                "payload": _loads_or_empty(r[3]), "archived_at_ms": r[4], "reason": r[5],
                "attempts": r[6], "last_error": r[7] or r[8],
            }
            for r in rows
        ]

    # --- what the ESP32 reports -----------------------------------------------------------

    def lease(self, ids: list[str], *, now_ms: int | None = None) -> int:
        """`TAKEN`: hide these `outbox`/`dlq` rows for `BUFFER_LEASE_SECONDS` while the ESP32's
        sender task holds them. Returns how many rows were leased."""
        if not ids:
            return 0
        now = _now_ms() if now_ms is None else now_ms
        until = now + int(constants.BUFFER_LEASE_SECONDS * 1000)
        with self._lock:
            return self._conn.execute(
                f"""UPDATE alerts SET lease_until_ms = ?
                    WHERE id IN ({_marks(ids)}) AND state IN (?, ?)""",
                (until, *ids, OUTBOX, DLQ),
            ).rowcount

    def mark_sent(self, ids: list[str]) -> int:
        """Mark exactly the given, not-yet-delivered ids (live or dead-lettered, leased or not)
        as delivered, clearing any lease. Returns how many rows were actually updated -- an ACK
        for an id already sent or archived is ignored (not counted)."""
        if not ids:
            return 0
        sent_at_ms = _now_ms()
        with self._lock:
            cur = self._conn.execute(
                f"""
                UPDATE alerts
                SET sent = 1, sent_at_ms = ?, state = ?, lease_until_ms = NULL
                WHERE id IN ({_marks(ids)}) AND sent = 0
                """,
                (sent_at_ms, SENT, *ids),
            )
            return cur.rowcount

    def apply_fail(self, alert_id: str, reason: str, body: dict | None = None,
                   *, now_ms: int | None = None) -> str | None:
        """`FAIL <id> <reason> <body>` for a row in `outbox` or `dlq` (contract C1.6):

        1. not a critical event row -> archived at once, with `reason`;
        2. a critical, `preempted` (a DLQ replay a live event pre-empted) -> stays where it was,
           lease cleared, **not** an attempt;
        3. a critical, `reason` in `BUFFER_ARCHIVE_NOW_REASONS` -> archived at once;
        4. otherwise one more attempt: an `outbox` row moves to the DLQ (current round),
           `dlq_error = last_error = reason`, `body` is kept only if none was stored yet (the
           first composed event, with the event-time position, wins); at
           `BUFFER_DLQ_MAX_ATTEMPTS` the row is archived as `max_attempts`.

        **Repeated-FAIL guard** (contract C1.6, sync 2): rule 4 applies only to an `outbox` row
        (leased or not) or to a *leased* `dlq` row. Every FAIL clears the lease, so a rule-4 FAIL
        for a non-leased `dlq` row is a resend of one already applied (the ESP32 resends outcomes
        it got no `OK` for, C1.2/C1.8): it is ignored, not counted, and logged at debug. Accepted
        cost: a DLQ replay whose lease lapsed before its FAIL arrived is an uncounted attempt.
        Rules 1-3 need no guard.

        Returns `"archived"`, `"dlq"` or `"requeued"`, or `None` when the id is unknown, sent or
        already archived, or the FAIL is a repeat (ignored)."""
        now = _now_ms() if now_ms is None else now_ms
        stored = json.dumps(body, separators=(",", ":")) if body is not None else None
        with self._lock:
            row = self._conn.execute(
                f"""SELECT state, {_CRITICAL_EVENT}, NOT {_NOT_LEASED}
                    FROM alerts WHERE id = ? AND state IN (?, ?)""",
                (now, alert_id, OUTBOX, DLQ),
            ).fetchone()
            if row is None:
                return None
            state, critical, leased = row[0], bool(row[1]), bool(row[2])
            where, params = "id = ?", (alert_id,)
            if not critical or reason in constants.BUFFER_ARCHIVE_NOW_REASONS:
                self._conn.execute(
                    "UPDATE alerts SET last_error = ?, lease_until_ms = NULL WHERE id = ?",
                    (reason, alert_id),
                )
                self._archive_where(where, params, reason, now)
                return FAIL_ARCHIVED
            if reason == FAIL_PREEMPTED:
                self._conn.execute(
                    "UPDATE alerts SET lease_until_ms = NULL WHERE id = ?", (alert_id,)
                )
                return FAIL_REQUEUED
            if state == DLQ and not leased:
                logger.debug(
                    "buffer: ignoring repeated FAIL %s for dead letter %s (not leased)",
                    reason, alert_id,
                )
                return None
            next_round = self._next_dlq_round() if state == OUTBOX else None
            self._conn.execute(
                """
                UPDATE alerts
                SET state = ?, dlq_error = ?, last_error = ?,
                    dlq_body = COALESCE(dlq_body, ?),
                    dlq_at_ms = COALESCE(dlq_at_ms, ?),
                    dlq_round = COALESCE(?, dlq_round),
                    attempts = attempts + 1,
                    lease_until_ms = NULL
                WHERE id = ?
                """,
                (DLQ, reason, reason, stored, now, next_round, alert_id),
            )
            if self._archive_where(
                "id = ? AND attempts >= ?",
                (alert_id, constants.BUFFER_DLQ_MAX_ATTEMPTS), ARCHIVE_MAX_ATTEMPTS, now,
            ):
                return FAIL_ARCHIVED
            return FAIL_DEAD_LETTERED

    # --- housekeeping ---------------------------------------------------------------------

    def expire_outbox(self, older_than_ms: int, *, now_ms: int | None = None) -> tuple[int, int, int]:
        """Live rows the ESP32 didn't pick up before `older_than_ms` (epoch ms, by enqueue
        time), leased rows skipped: the link is down. A critical event row moves to the DLQ as
        `esp32_unreachable` (no body: the ESP32 never saw it, so there's no position; no attempt
        was made, so `attempts` stays); any other event (a medium, a driver OK) is archived as
        `esp32_unreachable`; non-event rows (keepalives) are deleted. Returns
        `(dead_lettered, archived, dropped)`."""
        now = _now_ms() if now_ms is None else now_ms
        stale = f"state = '{OUTBOX}' AND enqueued_at_ms < ? AND {_NOT_LEASED}"
        with self._lock:
            next_round = self._next_dlq_round()
            moved = self._conn.execute(
                f"""
                UPDATE alerts
                SET state = ?, dlq_error = ?, last_error = ?, dlq_at_ms = COALESCE(dlq_at_ms, ?),
                    dlq_round = ?
                WHERE {stale} AND {_CRITICAL_EVENT}
                """,
                (DLQ, ARCHIVE_ESP32_UNREACHABLE, ARCHIVE_ESP32_UNREACHABLE, now, next_round,
                 older_than_ms, now),
            ).rowcount
            archived = self._archive_where(
                f"{stale} AND {_EVENT}", (older_than_ms, now), ARCHIVE_ESP32_UNREACHABLE, now
            )
            dropped = self._conn.execute(
                f"DELETE FROM alerts WHERE {stale} AND NOT {_EVENT}", (older_than_ms, now)
            ).rowcount
        return moved, archived, dropped

    def waiting(self, *, now_ms: int | None = None) -> tuple[int, int]:
        """`(dlq_waiting, outbox_waiting)` for `END`/`OK`: rows in each state that aren't
        leased."""
        now = _now_ms() if now_ms is None else now_ms
        with self._lock:
            counts = dict(
                self._conn.execute(
                    f"SELECT state, COUNT(*) FROM alerts WHERE state IN (?, ?) AND {_NOT_LEASED}"
                    " GROUP BY state",
                    (OUTBOX, DLQ, now),
                ).fetchall()
            )
        return counts.get(DLQ, 0), counts.get(OUTBOX, 0)

    def counts(self, *, now_ms: int | None = None) -> dict:
        """`{outbox, dlq, sent, leased, archived, oldest_dlq_ms, dlq_reasons: {reason: n},
        archive_reasons: {reason: n}}` for the logs and `buffer.status`."""
        now = _now_ms() if now_ms is None else now_ms
        with self._lock:
            by_state = dict(
                self._conn.execute("SELECT state, COUNT(*) FROM alerts GROUP BY state").fetchall()
            )
            leased = self._conn.execute(
                "SELECT COUNT(*) FROM alerts WHERE state IN (?, ?) AND lease_until_ms > ?",
                (OUTBOX, DLQ, now),
            ).fetchone()[0]
            oldest = self._conn.execute(
                "SELECT MIN(created_at_ms) FROM alerts WHERE state = ?", (DLQ,)
            ).fetchone()[0]
            reasons = dict(
                self._conn.execute(
                    "SELECT dlq_error, COUNT(*) FROM alerts WHERE state = ? GROUP BY dlq_error",
                    (DLQ,),
                ).fetchall()
            )
            archive_reasons = dict(
                self._conn.execute(
                    "SELECT archive_reason, COUNT(*) FROM archive GROUP BY archive_reason"
                ).fetchall()
            )
        return {
            OUTBOX: by_state.get(OUTBOX, 0),
            DLQ: by_state.get(DLQ, 0),
            SENT: by_state.get(SENT, 0),
            "leased": leased,
            "archived": sum(archive_reasons.values()),
            "oldest_dlq_ms": oldest,
            "dlq_reasons": reasons,
            "archive_reasons": archive_reasons,
        }

    def unsent_count(self) -> int:
        """Rows not delivered yet: the live outbox plus the dead-letter queue (the archive is not
        counted: it is never sent)."""
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM alerts WHERE sent = 0").fetchone()[0]

    def prune_sent(self, older_than_ms: int) -> int:
        """Delete rows the ESP32 already ACKed before `older_than_ms` (epoch ms). Sent rows are
        only kept for inspection; without pruning, the 2 s keepalives grow the file forever
        (BUF-01). Returns how many rows were deleted. Never touches the archive."""
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM alerts WHERE sent = 1 AND sent_at_ms < ?", (older_than_ms,)
            )
            return cur.rowcount

    def cap_unsent(self, max_rows: int, *, now_ms: int | None = None) -> int:
        """If more than `max_rows` rows are waiting (live or dead-lettered: a long outage), drop
        the oldest *keepalive* readings first (deleted: they only repeat a state already
        queued). If that isn't enough, the oldest of the rest are moved to the archive with
        reason `cap_unsent` -- never deleted: the SD card is finite, and the newest rows describe
        the present (BUF-06), but an alert is kept on record. **Leased rows are exempt**
        (contract C1.6, sync 2): they are neither deleted nor archived, since the ESP32 may
        already have delivered them and its ACK must still land (they still count towards the
        total). Returns how many rows were dropped or archived."""
        now = _now_ms() if now_ms is None else now_ms
        with self._lock:
            excess = (
                self._conn.execute("SELECT COUNT(*) FROM alerts WHERE sent = 0").fetchone()[0]
                - max_rows
            )
            if excess <= 0:
                return 0
            dropped = self._conn.execute(
                f"""
                DELETE FROM alerts WHERE rowid IN (
                    SELECT rowid FROM alerts
                    WHERE sent = 0 AND kind = 'drowsiness'
                      AND json_extract(payload_json, '$.transition') = 0
                      AND {_NOT_LEASED}
                    ORDER BY rowid ASC
                    LIMIT ?
                )
                """,
                (now, excess),
            ).rowcount
            excess -= dropped
            if excess > 0:
                ids = [r[0] for r in self._conn.execute(
                    f"SELECT id FROM alerts WHERE sent = 0 AND {_NOT_LEASED}"
                    " ORDER BY rowid ASC LIMIT ?",
                    (now, excess),
                ).fetchall()]
                if ids:
                    dropped += self._archive_where(
                        f"id IN ({_marks(ids)})", tuple(ids), ARCHIVE_CAP_UNSENT, now
                    )
            return dropped

    def close(self) -> None:
        self._conn.close()


def _marks(ids) -> str:
    return ",".join("?" for _ in ids)


def _loads_or_empty(text: str | None) -> dict:
    try:
        value = json.loads(text) if text else {}
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


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
