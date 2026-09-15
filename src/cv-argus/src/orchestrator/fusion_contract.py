"""`fusion_contract.py` — NOT part of the Pi's running pipeline.

The typed contract + reference decision logic for the drowsy+grip fusion that the ESP32
("Message Sender Orchestrator") is responsible for computing — see the root `CLAUDE.md` and
`src/esp32-argus/README.md`. `cv-argus`'s own `Orchestrator` (`orchestrator.py`) never
grip-fuses anything; it only ever sees the camera signal, exactly as before this file was added.

Why this lives here, in Python, inside `cv-argus`, even though the ESP32 will (re)implement it
in firmware (likely C/C++, not Python): so the fusion algorithm has one executable,
unit-testable reference definition instead of only living as prose in a README — a firmware
author translates this class's logic 1:1 rather than re-deriving the state machine from
scratch. Nothing in `main.py` calls this yet; it is a contract reference, not dead code destined
for deletion.

**The base matrix** (confirmed directly with the user, not guessed): both signals bad ->
critical, both good -> low, either one alone bad -> medium. See `BASE_MATRIX` below.

**The three timing windows** (debounce / escalation / recovery) mirror `orchestrator.py`'s own
`ORCHESTRATOR_DEBOUNCE_FRAMES`/`ORCHESTRATOR_ALERT_COOLDOWN_SECONDS` precedent, extended to a
joint two-signal state instead of the camera alone:

- **Debounce**: the combined `(drowsy, grip)` state must hold for `debounce_seconds` before it
  first triggers an alert at that severity, so one noisy frame or a momentary hand-off-wheel
  gear shift doesn't fire.
- **Escalation**: an open `medium` incident that hasn't returned to `low` within
  `escalation_seconds` escalates to `critical` — a *new*, linked `Alert` row referencing the
  original one via `related_alert_id`, per `Alert` being an append-only event record
  (`backend-argus`'s `app/models/alert.py`), never mutated in place.
- **Recovery**: once both signals return to good and hold for `recovery_seconds`, the open
  incident is marked resolved (`resolved_at`), rather than silently forgotten.

This module intentionally stops at defining the shapes and the matrix. `FusionOrchestrator`'s
body is left unimplemented (see the plan doc this shipped with) — the state machine itself
(tracking a pending debounce candidate and an open incident's age) is real work for whoever
first has ESP32 hardware to validate the three window defaults against.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class GripState(str, Enum):
    GOOD = "good"
    BAD = "bad"


class DrowsyState(str, Enum):
    NOT_DROWSY = "not_drowsy"
    DROWSY = "drowsy"


class Severity(str, Enum):
    """Must match `backend-argus`'s `app/models/common.py` `Severity` exactly — kept as a
    hand-synced second copy, the same pattern `GeometricRatioFeatureLayer` already uses across
    five verbatim copies elsewhere in this project (see the root `CLAUDE.md`). No automated
    check keeps this in sync; verify by hand against a change to the backend enum.
    """

    LOW = "low"
    MEDIUM = "medium"
    CRITICAL = "critical"


# Confirmed directly with the user, not guessed: both signals bad -> critical, both good -> low,
# either one alone bad -> medium.
BASE_MATRIX: dict[tuple[DrowsyState, GripState], Severity] = {
    (DrowsyState.NOT_DROWSY, GripState.GOOD): Severity.LOW,
    (DrowsyState.NOT_DROWSY, GripState.BAD): Severity.MEDIUM,
    (DrowsyState.DROWSY, GripState.GOOD): Severity.MEDIUM,
    (DrowsyState.DROWSY, GripState.BAD): Severity.CRITICAL,
}


@dataclass(frozen=True)
class FusionConfig:
    """Timing windows in seconds. Proposed defaults, the same order of magnitude as cv-argus's
    own `ORCHESTRATOR_ALERT_COOLDOWN_SECONDS` (30.0) — not tuned against a real drive yet."""

    debounce_seconds: float = 5.0
    escalation_seconds: float = 20.0
    recovery_seconds: float = 10.0


@dataclass
class FusionReading:
    """One instantaneous sample of both signals, as the ESP32 observes them."""

    drowsy: DrowsyState
    grip: GripState
    at: float  # monotonic seconds


@dataclass
class AlertToRaise:
    """The subset of `backend-argus`'s `AlertCreate` this engine decides on. `id_route`/
    `coordinates`/`speed_at_event`/`timestamp` are filled in by the ESP32's HTTP relay layer, not
    here — see `src/esp32-argus/README.md` section 2."""

    severity_level: Severity
    grip_status: GripState
    related_alert_id: Optional[str]  # set when this raise is an escalation of an open incident


@dataclass
class FusionDecision:
    """What the engine tells the caller to do this tick. `alert` is `None` when nothing new
    needs posting (still debouncing, or an unchanged ongoing incident)."""

    severity: Severity
    alert: Optional[AlertToRaise]
    resolved_incident_id: Optional[str] = None


class FusionOrchestrator:
    """Reference implementation of the ESP32's drowsy+grip decision loop. Mirrors cv-argus's own
    `Orchestrator` in spirit: one state machine, fed one reading at a time, deciding whether to
    raise/escalate/resolve an alert — but the timer/state-tracking body is intentionally left
    unimplemented here (foo, per the user's ask) rather than guessed at; the matrix and data
    shapes above are the settled part.

    Call `update()` once per new `FusionReading`; it returns what to do, if anything.
    """

    def __init__(self, config: Optional[FusionConfig] = None) -> None:
        self._config = config or FusionConfig()
        # TODO: track the pending (not-yet-debounced) candidate severity + its start time, and
        # the current open incident (if any): its alert id, severity, and how long it's been
        # at-or-above that severity — needed for the escalation/recovery checks in update().
        raise NotImplementedError("reference contract — fill in the state machine body")

    def update(self, reading: FusionReading) -> FusionDecision:
        """Feed one new reading; returns this tick's decision per `FusionConfig`'s three windows
        and `BASE_MATRIX` above."""
        raise NotImplementedError("foo — implement debounce/escalation/recovery per the plan doc")
