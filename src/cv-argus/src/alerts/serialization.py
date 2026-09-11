"""`Alert` <-> plain-dict/JSON conversion, shared by `buffer/` (SQLite storage) and `sender/`
(the Bluetooth wire format) so both stay byte-for-byte consistent with each other.

`to_json()` must stay compact and free of embedded newlines — `sender/`'s wire protocol is
newline-delimited JSON, one `Alert` per line (see `sender/protocol.py`).
"""

import json
from typing import Any

from .models import Alert, AlertKind


def to_dict(alert: Alert) -> dict[str, Any]:
    return {
        "id": alert.id,
        "kind": alert.kind.value,
        "level": alert.level,
        "created_at_ms": alert.created_at_ms,
        "source_id": alert.source_id,
        "payload": alert.payload,
        "geolocation": alert.geolocation,
    }


def from_dict(data: dict[str, Any]) -> Alert:
    try:
        kind = AlertKind(data["kind"])
    except ValueError as exc:
        raise ValueError(f"unknown Alert kind {data['kind']!r}") from exc
    return Alert(
        id=data["id"],
        kind=kind,
        level=data["level"],
        created_at_ms=data["created_at_ms"],
        source_id=data["source_id"],
        payload=data.get("payload", {}),
        geolocation=data.get("geolocation"),
    )


def to_json(alert: Alert) -> str:
    """Compact, newline-free — see the module docstring for why that matters to `sender/`."""
    return json.dumps(to_dict(alert), separators=(",", ":"))


def from_json(s: str) -> Alert:
    return from_dict(json.loads(s))
