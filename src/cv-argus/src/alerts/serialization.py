"""`Alert` <-> plain-dict/JSON conversion, shared by `buffer/` (SQLite storage) and `sender/`
(the Bluetooth wire format) so both stay byte-for-byte consistent with each other.

`to_json()` must stay compact and free of embedded newlines — `sender/`'s wire protocol is
newline-delimited JSON, one `Alert` per line (see `sender/protocol.py`).
"""

import json
import math
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
        "mono_ms": alert.mono_ms,
        "boot_id": alert.boot_id,
        "clock_synced": alert.clock_synced,
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
        # Absent on records from a release that predates them.
        mono_ms=data.get("mono_ms"),
        boot_id=data.get("boot_id"),
        clock_synced=data.get("clock_synced"),
    )


def _finite(value: Any) -> Any:
    """NaN/Infinity -> None, everywhere: json.dumps would write them as bare tokens, which aren't
    JSON, and the ESP32's ArduinoJson rejects the whole line (REC-19)."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _finite(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite(v) for v in value]
    return value


def to_json(alert: Alert) -> str:
    """Compact, newline-free — see the module docstring for why that matters to `sender/` — and
    strict JSON: a non-finite number becomes null."""
    return json.dumps(_finite(to_dict(alert)), separators=(",", ":"), allow_nan=False)


def from_json(s: str) -> Alert:
    return from_dict(json.loads(s))
