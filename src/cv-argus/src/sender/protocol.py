"""Pure request/response grammar for the Pi<->ESP32 Bluetooth protocol — no I/O, no sockets,
fully unit-testable on its own. This is the exchange `server.py` implements:

    Client -> Server:  PULL <n>\\n
    Server -> Client:  one JSON line per unsent Alert (alerts.serialization.to_json), oldest
                       first, up to n
    Server -> Client:  END\\n                                (even if 0 rows)
    Client -> Server:  ACK <id1>,<id2>,...\\n                 (only if rows were sent)
    Server -> Client:  ACKED <n>\\n                           (n = rows actually marked sent)

An `ACK` may also open a session on its own (no PULL first): the ESP32 relays a pulled batch
over HTTP before acking it, and if that outlasts the Pi's ACK wait it acks on its next
connection instead (SND-01/02). A trailing `\\r` (a CRLF client) is tolerated, and `PULL n` is
clamped to `MAX_PULL_LIMIT` so one request can't make the Pi stream an unbounded batch (SND-05).

A session may instead carry one `CONFIG` request: the ESP32 fetching this truck's backend
credentials (see `device_config.py`), at boot and again whenever the backend answers 401:

    Client -> Server:  CONFIG\\n
    Server -> Client:  CONFIG {"truck_id":...,"device_api_key":...,"backend_url":...}\\n
                       or NOCONFIG\\n                         (none provisioned on this Pi)

**Live first, dead-letter on failure.** After the records, the ESP32 says per record whether it
reached the backend. `ACK` means delivered (or nothing to send); `FAIL` means it couldn't, with a
one-token reason and the backend message it composed, so the Pi keeps exactly that message (GPS
position, time) in its dead-letter queue (`buffer/store.py`). `END` carries how many dead letters
are waiting, and `DLQ <k>` asks for the oldest of them, answered the same way:

    Client -> Server:  PULL <n>\\n  |  DLQ <k>\\n
    Server -> Client:  JSON lines (a DLQ line also has "dlq": {error, body, failed_at_ms, attempts})
    Server -> Client:  END <dead letters waiting>\\n
    Client -> Server:  FAIL <id> <reason> <json body or ->\\n   (0..n lines)
    Client -> Server:  ACK <id1>,<id2>,...\\n                   (if any were delivered)
    Server -> Client:  ACKED <n>\\n
    Client -> Server:  DONE\\n
    Server -> Client:  OK acked=<a> failed=<f> dlq=<n>\\n

A client that stops after `ACK`/`ACKED` (the original exchange) still works.

Case-sensitive by design — simpler to reason about on constrained ESP32 firmware than a
case-insensitive parser would be.
"""

import json
import re

END_LINE = "END"
CONFIG_REQUEST = "CONFIG"
NOCONFIG_LINE = "NOCONFIG"
# Mirrors buffer.Buffer.fetch_unsent's own default -- kept in sync by hand, there's no shared
# constant between the two packages worth introducing for one number.
DEFAULT_PULL_LIMIT = 50
# The most rows one PULL can ask for, whatever n says.
MAX_PULL_LIMIT = 50

_PULL_RE = re.compile(r"^PULL (\d+)$")
# Ids are opaque tokens (Alert.id is a uuid4 hex in production, but the grammar itself doesn't
# assume that shape) -- anything but a comma or whitespace, comma-separated.
_ACK_RE = re.compile(r"^ACK ([^,\s]+(?:,[^,\s]+)*)$")


DONE_LINE = "DONE"
# The most dead letters one DLQ request can ask for: replay is meant to trickle, not flood.
MAX_DLQ_LIMIT = 10

_DLQ_RE = re.compile(r"^DLQ (\d+)$")
# FAIL <id> <reason> <body>: id and reason are single tokens; the body (compact JSON, which may
# contain spaces inside strings) is the rest of the line, or "-" when there is none.
_FAIL_RE = re.compile(r"^FAIL ([^\s,]+) ([A-Za-z0-9_.:-]+)(?: (.+))?$")


def format_end(dlq_waiting: int) -> str:
    return f"{END_LINE} {dlq_waiting}"


def parse_end(line: str) -> int | None:
    """`END` or `END <n>` -> n (0 when absent); `None` if it isn't an END line."""
    parts = line.rstrip("\r").split(" ")
    if parts[0] != END_LINE or len(parts) > 2:
        return None
    if len(parts) == 1:
        return 0
    return int(parts[1]) if parts[1].isdigit() else None


def parse_dlq(line: str) -> int | None:
    match = _DLQ_RE.match(line.rstrip("\r"))
    if match is None:
        return None
    return min(int(match.group(1)), MAX_DLQ_LIMIT)


def format_dlq(k: int) -> str:
    return f"DLQ {k}"


def parse_fail(line: str) -> tuple[str, str, dict | None] | None:
    """`FAIL <id> <reason> <json|->` -> `(id, reason, body or None)`; `None` if malformed. A body
    that isn't a JSON object counts as no body rather than rejecting the whole report."""
    match = _FAIL_RE.match(line.rstrip("\r"))
    if match is None:
        return None
    raw = match.group(3)
    body = None
    if raw and raw != "-":
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        body = parsed if isinstance(parsed, dict) else None
    return match.group(1), match.group(2), body


def format_fail(alert_id: str, reason: str, body: dict | None = None) -> str:
    payload = json.dumps(body, separators=(",", ":")) if body is not None else "-"
    return f"FAIL {alert_id} {reason} {payload}"


def format_result(acked: int, failed: int, dlq_waiting: int) -> str:
    return f"OK acked={acked} failed={failed} dlq={dlq_waiting}"


def parse_pull(line: str) -> int | None:
    """Returns the requested row count, or `None` if `line` isn't a well-formed `PULL <n>`."""
    match = _PULL_RE.match(line.rstrip("\r"))
    if match is None:
        return None
    return min(int(match.group(1)), MAX_PULL_LIMIT)


def format_pull(n: int) -> str:
    return f"PULL {n}"


def parse_ack(line: str) -> list[str] | None:
    """Returns the acked ids, or `None` if `line` isn't a well-formed `ACK <id,...>`."""
    match = _ACK_RE.match(line.rstrip("\r"))
    if match is None:
        return None
    return match.group(1).split(",")


def format_ack(ids: list[str]) -> str:
    return "ACK " + ",".join(ids)


def format_acked(n: int) -> str:
    return f"ACKED {n}"


def parse_config_request(line: str) -> bool:
    return line.rstrip("\r") == CONFIG_REQUEST


def format_config(payload: dict[str, str]) -> str:
    """`CONFIG <json>` -- compact and newline-free, like every other line of this protocol."""
    return f"{CONFIG_REQUEST} {json.dumps(payload, separators=(',', ':'))}"


def parse_config(line: str) -> dict[str, str] | None:
    """The client side of `format_config` (what the ESP32 does): the payload, or `None` for
    `NOCONFIG` or a malformed line."""
    prefix = CONFIG_REQUEST + " "
    line = line.rstrip("\r")
    if not line.startswith(prefix):
        return None
    try:
        payload = json.loads(line[len(prefix) :])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None
