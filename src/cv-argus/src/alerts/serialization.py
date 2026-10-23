"""`Alert` <-> plain-dict/JSON conversion, shared by `buffer/` (SQLite storage) and `sender/`
(the Bluetooth wire format) so both stay byte-for-byte consistent with each other.

`to_json()` must stay compact and free of embedded newlines — `sender/`'s wire protocol is
newline-delimited JSON, one `Alert` per line (see `sender/protocol.py`).
"""

import json
import math
import time
from typing import Any

from . import models
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


def age_seconds(
    alert: Alert,
    *,
    boot_id: str | None = None,
    mono_ms_now: int | None = None,
    wall_ms_now: int | None = None,
    synced_now: bool | None = None,
) -> float | None:
    """Seconds from the event to now, for the `age_s` serve-time field (contract C1.4), rounded to
    0.1 s, or `None` when it can't be known.

    - Same boot as now and a monotonic stamp: `(monotonic now - mono_ms) / 1000` (immune to NTP
      steps and an unsynced clock).
    - Else, when both the row's and the Pi's wall clock are synced: wall-clock difference,
      clamped to >= 0.
    - Else `None`: the ESP32 then answers `FAIL <id> too_old -` and the row is archived.

    The keyword arguments default to this process's boot id and clocks; tests pass them."""
    boot_id = models.BOOT_ID if boot_id is None else boot_id
    if alert.boot_id is not None and alert.boot_id == boot_id and alert.mono_ms is not None:
        now = int(time.monotonic() * 1000) if mono_ms_now is None else mono_ms_now
        return round(max(0, now - alert.mono_ms) / 1000.0, 1)
    synced = models.clock_synced() if synced_now is None else synced_now
    if alert.clock_synced and synced:
        now = int(time.time() * 1000) if wall_ms_now is None else wall_ms_now
        return round(max(0, now - alert.created_at_ms) / 1000.0, 1)
    return None
