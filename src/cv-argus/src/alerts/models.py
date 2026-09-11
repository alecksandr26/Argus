"""`Alert` — the one data-model record `orchestrator/`, `buffer/`, and `sender/` all share.

Design rationale (see the draw.io system diagram and `src/cv-argus/CLAUDE.md`'s "Planned module
layout"): the diagram shows a single "Alert/RouteStatus Model" box feeding a single "Queue
Message Local Buffer (SQLite)" box — one shared record shape, not two independently-serialized
classes. Alert payloads are also expected to vary by type (a drowsiness alert and a route-status
heartbeat don't share a rigid shape, and a future panic alert wouldn't either) — MongoDB was
chosen backend-side partly for this reason (see
`docs/document/borrador-proyecto-modular-argus.md`'s Parte 5). So this module defines one
envelope dataclass, `Alert`, with a `kind` discriminator and a flexible `payload` dict, rather
than a rigid dataclass per alert type. `route_status()` below is a convenience constructor, not
a second class — `buffer/`/`sender/` only ever have to know about one type.

No logic, no I/O, no persistence here — see `buffer/` for storage and `sender/` for the wire
protocol. This module deliberately has no dependency on numpy/mediapipe/sqlite/sockets, so
anything downstream can import it cheaply (callers building a `payload` from a numpy-bearing
`DetectionResult` are responsible for converting to plain Python types first, e.g.
`probabilities.tolist()` — see `orchestrator/orchestrator.py`).
"""

import enum
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


class AlertKind(str, enum.Enum):
    """Discriminates the one shared `Alert` envelope's current uses. A `str` subclass so a
    `kind` compares/serializes as its plain string value (`"drowsiness"`, not
    `AlertKind.DROWSINESS`) without extra conversion at the JSON boundary."""

    DROWSINESS = "drowsiness"
    ROUTE_STATUS = "route_status"
    # A panic alert is not produced by this module: the draw.io diagram wires the Panic Button
    # directly into the ESP32, bypassing the Pi's orchestrator entirely (see the root
    # CLAUDE.md). Left undefined here rather than reserved as a value nothing ever constructs.


@dataclass
class Alert:
    """One record bound for the `buffer/` queue and, eventually, the ESP32.

    `geolocation` is always `None` when built by this module — this repo's edge module has no
    GPS (the draw.io diagram wires the geolocation module directly to the ESP32, not the Pi);
    the ESP32 attaches its own live GPS reading only when it relays a pulled record onward to
    the backend over HTTP. Kept as a field (not omitted) so the wire/DB shape stays stable
    end-to-end rather than growing a field partway down the pipe.

    `created_at_ms` is wall-clock (`time.time()`), not `time.monotonic()` — unlike
    `pipeline.stage.FrameContext.created_at`, this value is serialized and eventually shown off
    this device, so it has to mean something to a reader elsewhere. Don't conflate the two.
    """

    id: str
    kind: AlertKind
    level: int | None  # 1 (Not Drowsy) / 2 (Drowsy) from DetectionResult; None for ROUTE_STATUS
    created_at_ms: int
    source_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    geolocation: dict[str, float] | None = None

    @staticmethod
    def new(kind: AlertKind, *, level: int | None, source_id: str, payload: dict[str, Any]) -> "Alert":
        """Construct an `Alert` with a fresh id and the current wall-clock time — the way
        production code should build one. Tests may still construct `Alert(...)` directly with
        fixed values when they need a deterministic id/timestamp."""
        return Alert(
            id=uuid.uuid4().hex,
            kind=kind,
            level=level,
            created_at_ms=int(time.time() * 1000),
            source_id=source_id,
            payload=payload,
            geolocation=None,
        )


def route_status(source_id: str, status: str = "OK") -> Alert:
    """Build a RouteStatus heartbeat — just an `Alert` with `kind=ROUTE_STATUS` and no level,
    not a second class (see the module docstring). `"OK"` is the only status value this project
    currently produces anywhere; nothing else is defined."""
    return Alert.new(AlertKind.ROUTE_STATUS, level=None, source_id=source_id, payload={"status": status})
