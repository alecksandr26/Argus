"""`Alert` — the one data-model record `orchestrator/`, `buffer/`, and `sender/` all share.

Design rationale (see the draw.io system diagram and `src/cv-argus/CLAUDE.md`'s "Planned module
layout"): the diagram shows a single "Alert/RouteStatus Model" box feeding a single "Queue
Message Local Buffer (SQLite)" box — one shared record shape, not two independently-serialized
classes. Alert payloads were expected to vary by type — MongoDB was chosen backend-side partly
for this reason (see `docs/document/borrador-proyecto-modular-argus.md`'s Parte 5). So this
module defines one envelope dataclass, `Alert`, with a `kind` discriminator and a flexible
`payload` dict, rather than a rigid dataclass per alert type.

Since the final tweaks (Bluetooth protocol v2, contract C1.4) the only kind is `drowsiness`: the
60 s `route_status` heartbeat and its `route_status()` factory are gone (the ESP32 sends a GPS
report every 5 s on its own). A legacy `route_status` row still in an old buffer file is moved to
the `archive` table when the buffer opens (`buffer/store.py`), so it is never read back as an
`Alert`.

No logic, no I/O, no persistence here — see `buffer/` for storage and `sender/` for the wire
protocol. This module deliberately has no dependency on numpy/mediapipe/sqlite/sockets, so
anything downstream can import it cheaply (callers building a `payload` from a numpy-bearing
`DetectionResult` are responsible for converting to plain Python types first, e.g.
`probabilities.tolist()` — see `orchestrator/orchestrator.py`).
"""

import enum
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


def _read_boot_id() -> str:
    """The kernel's per-boot id (shared by every process and container on this boot, so it
    matches the clock `time.monotonic()` counts from), else a random one for this process."""
    try:
        with open("/proc/sys/kernel/random/boot_id") as f:
            return f.read().strip()
    except OSError:
        return uuid.uuid4().hex


BOOT_ID = _read_boot_id()

# systemd-timesyncd creates this once the clock has been synchronized at least once this boot.
_TIMESYNC_FLAG = "/run/systemd/timesync/synchronized"


def clock_synced() -> bool:
    """Whether the wall clock has been NTP-synced this boot. A Pi has no battery-backed clock, so
    a cold boot with no network starts at the last-shutdown time. `CV_ARGUS_CLOCK_SYNCED=1`
    overrides it (e.g. a container that can't see the host's /run)."""
    override = os.environ.get("CV_ARGUS_CLOCK_SYNCED")
    if override is not None:
        return override == "1"
    return os.path.exists(_TIMESYNC_FLAG)


class AlertKind(str, enum.Enum):
    """Discriminates the one shared `Alert` envelope's current uses. A `str` subclass so a
    `kind` compares/serializes as its plain string value (`"drowsiness"`, not
    `AlertKind.DROWSINESS`) without extra conversion at the JSON boundary."""

    DROWSINESS = "drowsiness"
    # `route_status` (the heartbeat) was removed in protocol v2 (contract C1.4).
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
    level: int | None  # 1 (Not Drowsy) / 2 (Drowsy) from DetectionResult; None for No Face
    created_at_ms: int
    source_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    geolocation: dict[str, float] | None = None
    # Monotonic milliseconds on this device: what the ESP32 fuses readings by (an NTP step or an
    # unset clock can't reorder or stretch them), comparable only within one `boot_id`. Both are
    # None on rows queued by a release that predates them. `clock_synced` says whether
    # `created_at_ms` can be trusted as real time.
    mono_ms: int | None = None
    boot_id: str | None = None
    clock_synced: bool | None = None

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
            mono_ms=int(time.monotonic() * 1000),
            boot_id=BOOT_ID,
            clock_synced=clock_synced(),
        )

