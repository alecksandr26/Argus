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
