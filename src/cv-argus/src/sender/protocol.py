"""Pure request/response grammar for the Pi<->ESP32 Bluetooth protocol **v2** (contract C1 in
`docs/designs/final-tweaks-contracts.md`, which wins over this docstring) — no I/O, no sockets,
fully unit-testable on its own. v2 is not compatible with v1; there is no version negotiation.

The first line the ESP32 sends decides the session:

    C: CONFIG                       config session (backend credentials, see device_config.py)
    S: CONFIG {"truck_id":..,"device_api_key":..,"backend_url":..} | NOCONFIG

    C: PULL <n>                     live pull, n clamped to MAX_PULL_LIMIT (50)
    S: <record JSON line> x0..n     outbox rows, not leased, oldest first
    S: END <dlq_waiting> <outbox_waiting>
    C: <report line> x0..m          ACK / FAIL / TAKEN, any order
    C: DONE
    S: OK acked=<a> failed=<f> leased=<l> dlq=<d> outbox=<o>

    C: DLQ <k>                      dead-letter pull, k clamped to MAX_DLQ_LIMIT (1)
    S: <record JSON line with "dlq"> x0..1
    S: END <dlq_waiting> <outbox_waiting>
    C: <report line> x0..m
    C: DONE
    S: OK ...

    C: ACK <ids> | FAIL <id> <reason> <body>     report session: results for rows taken earlier
    C: <report line> x0..m
    C: DONE
    S: OK ...

Report lines:

    ACK <id1>,<id2>,...         -> ACKED <n>    delivered (or nothing to deliver)
    FAIL <id> <reason> <body>   (no reply)      couldn't deliver; body is compact JSON or "-"
    TAKEN <id1>,<id2>,...       -> LEASED <n>   the ESP32 holds these rows, reports them later

Report lines take effect the moment the Pi reads them; `DONE` -> `OK` is only a summary. A
session that breaks before `OK` keeps every line already applied, and resending them is safe (ACK
is idempotent, a repeated FAIL is ignored: `Buffer.apply_fail`).

`<dlq_waiting>`/`<outbox_waiting>` count rows that aren't leased. A trailing `\\r` (a CRLF client)
is tolerated. Case-sensitive by design — simpler to reason about on constrained ESP32 firmware
than a case-insensitive parser would be.
"""

import json
import re

END_LINE = "END"
CONFIG_REQUEST = "CONFIG"
NOCONFIG_LINE = "NOCONFIG"
DONE_LINE = "DONE"
# Mirrors buffer.Buffer.fetch_unsent's own default -- kept in sync by hand, there's no shared
# constant between the two packages worth introducing for one number.
DEFAULT_PULL_LIMIT = 50
# The most rows one PULL can ask for, whatever n says.
MAX_PULL_LIMIT = 50
# The most dead letters one DLQ request can ask for: one row per session (contract C1.2), so a
# live event never waits behind a batch of replays.
MAX_DLQ_LIMIT = 1

_PULL_RE = re.compile(r"^PULL (\d+)$")
_DLQ_RE = re.compile(r"^DLQ (\d+)$")
_END_RE = re.compile(r"^END (\d+) (\d+)$")
# Ids are opaque tokens (Alert.id is a uuid4 hex in production, but the grammar itself doesn't
# assume that shape) -- anything but a comma or whitespace, comma-separated.
_ACK_RE = re.compile(r"^ACK ([^,\s]+(?:,[^,\s]+)*)$")
_TAKEN_RE = re.compile(r"^TAKEN ([^,\s]+(?:,[^,\s]+)*)$")
# FAIL <id> <reason> <body>: id and reason are single tokens; the body (compact JSON, which may
# contain spaces inside strings) is the rest of the line, or "-" when there is none.
_FAIL_RE = re.compile(r"^FAIL ([^\s,]+) ([A-Za-z0-9_.:-]+)(?: (.+))?$")
_RESULT_RE = re.compile(
    r"^OK acked=(\d+) failed=(\d+) leased=(\d+) dlq=(\d+) outbox=(\d+)$"
)


def format_end(dlq_waiting: int, outbox_waiting: int) -> str:
    return f"{END_LINE} {dlq_waiting} {outbox_waiting}"


def parse_end(line: str) -> tuple[int, int] | None:
    """`END <dlq_waiting> <outbox_waiting>` -> the two counts; `None` if it isn't one."""
    match = _END_RE.match(line.rstrip("\r"))
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


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


def parse_taken(line: str) -> list[str] | None:
    """`TAKEN <id,...>` -> the ids; `None` if malformed."""
    match = _TAKEN_RE.match(line.rstrip("\r"))
    if match is None:
        return None
    return match.group(1).split(",")


def format_taken(ids: list[str]) -> str:
    return "TAKEN " + ",".join(ids)


def format_leased(n: int) -> str:
    return f"LEASED {n}"


def format_result(acked: int, failed: int, leased: int, dlq_waiting: int, outbox_waiting: int) -> str:
    return (f"OK acked={acked} failed={failed} leased={leased} dlq={dlq_waiting} "
            f"outbox={outbox_waiting}")


def parse_result(line: str) -> dict[str, int] | None:
    """The client side of `format_result`: `{acked, failed, leased, dlq, outbox}`."""
    match = _RESULT_RE.match(line.rstrip("\r"))
    if match is None:
        return None
    keys = ("acked", "failed", "leased", "dlq", "outbox")
    return {k: int(v) for k, v in zip(keys, match.groups())}


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
