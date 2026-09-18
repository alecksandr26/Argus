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
  gear shift doesn't fire. This same window also confirms a *sudden worsening* of an
  already-open incident (e.g. an open `medium` incident where the very next reading maps
  straight to `critical`) — see `FusionOrchestrator.update()`'s docstring for why that's the
  same code path as a fresh incident, not the slower escalation timer below.
- **Escalation**: an open `medium` incident that hasn't returned to `low` within
  `escalation_seconds` escalates to `critical` — a *new*, linked `Alert` row referencing the
  original one via `related_alert_id`, per `Alert` being an append-only event record
  (`backend-argus`'s `app/models/alert.py`), never mutated in place.
- **Recovery**: once both signals return to good and hold for `recovery_seconds`, the open
  incident is marked resolved (`resolved_at`), rather than silently forgotten.

**Now implemented, state machine included** — `FusionOrchestrator` below actually tracks a
pending debounce candidate and an open incident's age, not just the shapes/matrix (it used to be
a stub; see `docs/roadmap.md` for when that changed). Two behaviors worth stating plainly here
since they're easy to miss re-deriving this from the matrix alone:

- **No automatic de-escalation.** Only a full recovery (both signals good, held
  `recovery_seconds`) clears an open incident. A *partial* improvement (e.g. `critical` →
  `medium`) never steps the reported severity back down on its own — it only resets the
  recovery-tracking clock, since the reading isn't fully good yet. This is deliberate, not an
  oversight: a driver who improves from `critical` to `medium` and stays there indefinitely
  keeps reporting `critical` (with no further re-alerting) until they either fully recover or
  worsen again. A **manual** override — a guardian who has actually talked to the driver and
  confirmed they're fine, stepping the alert back down themselves — is a real, separate need
  this state machine deliberately does not attempt to replace; that's a `backend-argus`/
  `ui-argus` feature (a guardian-facing action on `AlertTriage.tsx`), tracked in
  `docs/roadmap.md` as future work, not implemented here.
- **Only the latest/most-severe alert row gets `resolved_at` on recovery.** If an incident
  escalated, the original `medium` row's own `resolved_at` stays `null` forever — a dashboard
  needs to treat it as closed via its `related_alert_id` link to the resolved `critical` row,
  not by querying `resolved_at` on that row directly. See `FusionOrchestrator.
  record_alert_posted()`'s docstring for the mechanism that makes one field correctly serve both
  roles.

`escalation_seconds` specifically was reconsidered and deliberately kept at 20.0 — see
`FusionConfig`'s own docstring for the reasoning, confirmed directly with the user.
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

# `Severity` is a plain `str, Enum` with no native ordering -- this is the "how bad" ranking
# `update()` compares a reading's instantaneous BASE_MATRIX severity against the currently
# confirmed one with (worse / same / better), not a coincidence of declaration order above.
_SEVERITY_RANK: dict[Severity, int] = {
    Severity.LOW: 0,
    Severity.MEDIUM: 1,
    Severity.CRITICAL: 2,
}


@dataclass(frozen=True)
class FusionConfig:
    """Timing windows in seconds. Proposed defaults, the same order of magnitude as cv-argus's
    own `ORCHESTRATOR_ALERT_COOLDOWN_SECONDS` (30.0) — not tuned against a real drive yet.

    `escalation_seconds` specifically was reconsidered and deliberately kept at 20.0, not
    guessed at once and left alone: a faster window was considered (and would shrink the total
    debounce+escalation exposure window before a genuinely dangerous `Drowsy + good grip` case
    gets flagged critical) but rejected because `medium` also fires for `Not Drowsy + bad grip`
    — a driver who's fully alert but has a hand off the wheel (adjusting the radio, reaching for
    coffee) — which is common and usually harmless. A single shared timer has to serve both
    causes; a faster one would escalate that benign case to `critical` routinely, training
    drivers to ignore alerts. Confirmed directly with the user, not a leftover unexamined
    default. A future revisit worth keeping in mind: splitting `escalation_seconds` by which
    signal made the incident `medium` (faster for the drowsy-driven case, slower for the
    grip-only case) would be more accurate to the actual risk, at the cost of more state to
    track — not adopted here, single shared timer stays the design.
    """

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
    raise/escalate/resolve an alert. Unlike `Orchestrator`, there's no thread and no queue here —
    `update()` is a plain synchronous call the ESP32's own main loop makes once per new
    `FusionReading`, using whatever timestamp source it has (`FusionReading.at` is documented as
    monotonic seconds) rather than this class ever reading a clock itself. That makes the whole
    state machine deterministic and testable with zero real waiting or clock-patching — every
    test just constructs `FusionReading`s with chosen `at` values.

    `FusionDecision.severity` always reports the *officially confirmed* severity as of this tick
    — never the raw instantaneous `BASE_MATRIX` mapping of the current reading. A worsening
    trend that hasn't finished debouncing yet is intentionally invisible through this field; if
    a caller ever needs that visibility, add a new field for it rather than repurposing this one.

    Call `update()` once per new `FusionReading`; call `record_alert_posted()` once per
    successful `POST /api/alerts` of an `AlertToRaise` this class returned. See each method's
    own docstring for exactly what they track/return.
    """

    def __init__(self, config: Optional[FusionConfig] = None) -> None:
        self._config = config or FusionConfig()

        # All six fields below are owned exclusively by update() -- _open_incident_id is also
        # written by record_alert_posted() -- there's no thread here (unlike Orchestrator), so
        # there's no concurrent caller and no lock is needed; ownership is still spelled out
        # explicitly, matching orchestrator.py's own convention.

        # The officially "in-effect" severity right now -- what FusionDecision.severity reports
        # every tick. LOW means no incident is open.
        self._confirmed_severity: Severity = Severity.LOW

        # The real backend id of the most recently *posted* alert row belonging to the
        # currently-open incident (set via record_alert_posted()). None while no incident is
        # open, or while one is open but the caller hasn't reported its posted id back yet.
        # Unconditionally overwritten on every record_alert_posted() call, so this always holds
        # "the id of the highest-severity row posted so far for this incident" -- see that
        # method's docstring for why one field correctly serves both escalation's
        # related_alert_id and recovery's resolved_incident_id.
        self._open_incident_id: Optional[str] = None

        # reading.at at which _confirmed_severity was last raised (a fresh incident, or an
        # escalation). Drives the slow "stuck without improving" escalation timer. None while
        # _confirmed_severity is LOW.
        self._incident_confirmed_at: Optional[float] = None

        # A worsening candidate currently being debounced -- serves both a fresh incident from
        # LOW and a sudden worsening of an already-open incident (see update()'s docstring).
        # _pending_severity is the instantaneous BASE_MATRIX value being confirmed;
        # _pending_since is when that exact value was first seen, continuously. Reset to None
        # whenever the instantaneous value stops being strictly worse than _confirmed_severity,
        # or changes to a *different* worse value mid-debounce (rising-edge, reset-on-any-
        # non-matching-reading -- the same shape as Orchestrator's own _consecutive_drowsy,
        # generalized from a frame count to elapsed time).
        self._pending_severity: Optional[Severity] = None
        self._pending_since: Optional[float] = None

        # reading.at since both signals were last seen continuously fully-good. Drives the
        # recovery timer. Reset to None on any reading that isn't fully good, or once resolved.
        self._recovering_since: Optional[float] = None

    def update(self, reading: FusionReading) -> FusionDecision:
        """Feed one new reading; returns this tick's decision per `FusionConfig`'s three windows
        and `BASE_MATRIX` above.

        Compares this reading's instantaneous `BASE_MATRIX` severity against the currently
        confirmed one and branches three ways:

        - **Worse** (covers both a fresh incident starting from `LOW` and a sudden worsening of
          an already-open incident, e.g. an open `medium` incident where this reading maps
          straight to `critical`) — debounced via `debounce_seconds` before being believed,
          exactly like a fresh incident. This is deliberate: an instantaneous reading that's
          already confirmed-bad per the matrix shouldn't have to wait out the much longer
          `escalation_seconds` window, which is meant for "stuck at medium without recovering,"
          not "got worse right now."
        - **Unchanged**: if stuck at `medium`, checks the slow escalation timer.
        - **Better**: a *partial* improvement only resets the recovery clock (no de-escalation —
          see the module docstring); a *full* recovery (both signals good) is timed via
          `recovery_seconds`.
        """
        instantaneous = BASE_MATRIX[(reading.drowsy, reading.grip)]
        confirmed_rank = _SEVERITY_RANK[self._confirmed_severity]
        instantaneous_rank = _SEVERITY_RANK[instantaneous]

        if instantaneous_rank > confirmed_rank:
            return self._handle_worsening(reading, instantaneous)
        if instantaneous_rank == confirmed_rank:
            return self._handle_steady(reading)
        return self._handle_improving(reading, instantaneous)

    def _handle_worsening(self, reading: FusionReading, instantaneous: Severity) -> FusionDecision:
        self._recovering_since = None  # this reading isn't fully good

        if self._pending_severity != instantaneous:
            self._pending_severity = instantaneous
            self._pending_since = reading.at

        assert self._pending_since is not None  # just set above, or from a prior tick
        elapsed = reading.at - self._pending_since
        if elapsed < self._config.debounce_seconds:
            return FusionDecision(severity=self._confirmed_severity, alert=None)

        # Debounce satisfied -- confirm it. An incident was already open iff we weren't at LOW,
        # which makes this an escalation (checked *before* overwriting _confirmed_severity).
        is_escalation = self._confirmed_severity is not Severity.LOW
        related_id = self._open_incident_id if is_escalation else None
        alert = AlertToRaise(
            severity_level=instantaneous,
            grip_status=reading.grip,
            related_alert_id=related_id,
        )
        self._confirmed_severity = instantaneous
        self._incident_confirmed_at = reading.at
        self._pending_severity = None
        self._pending_since = None
        return FusionDecision(severity=instantaneous, alert=alert)

    def _handle_steady(self, reading: FusionReading) -> FusionDecision:
        self._pending_severity = None
        self._pending_since = None
        # Not fully good unless already LOW, and LOW has nothing to recover from either way.
        self._recovering_since = None

        if self._confirmed_severity is Severity.MEDIUM:
            assert self._incident_confirmed_at is not None  # always set once non-LOW
            elapsed = reading.at - self._incident_confirmed_at
            if elapsed >= self._config.escalation_seconds:
                alert = AlertToRaise(
                    severity_level=Severity.CRITICAL,
                    grip_status=reading.grip,
                    related_alert_id=self._open_incident_id,
                )
                self._confirmed_severity = Severity.CRITICAL
                self._incident_confirmed_at = reading.at
                return FusionDecision(severity=Severity.CRITICAL, alert=alert)

        # Steady LOW, steady MEDIUM below the escalation threshold, or steady CRITICAL (which
        # has nowhere further to escalate to -- intentional, not an oversight).
        return FusionDecision(severity=self._confirmed_severity, alert=None)

    def _handle_improving(self, reading: FusionReading, instantaneous: Severity) -> FusionDecision:
        self._pending_severity = None
        self._pending_since = None

        if instantaneous is not Severity.LOW:
            # A partial improvement (e.g. critical -> medium). No de-escalation: severity stays
            # unchanged, and this reading isn't fully good, so no recovery progress either.
            self._recovering_since = None
            return FusionDecision(severity=self._confirmed_severity, alert=None)

        # Fully good.
        if self._recovering_since is None:
            self._recovering_since = reading.at

        elapsed = reading.at - self._recovering_since
        if elapsed < self._config.recovery_seconds:
            return FusionDecision(severity=self._confirmed_severity, alert=None)

        # Recovered -- resolve whatever the latest posted row was, and reset to a clean slate.
        resolved_id = self._open_incident_id
        self._confirmed_severity = Severity.LOW
        self._open_incident_id = None
        self._incident_confirmed_at = None
        self._recovering_since = None
        return FusionDecision(severity=Severity.LOW, alert=None, resolved_incident_id=resolved_id)

    def record_alert_posted(self, alert_id: str) -> None:
        """Call this once, immediately after successfully `POST`ing an `AlertToRaise` this class
        returned (both the original alert and, later, an escalation's, if any) — no
        special-casing needed for "is this the first one."

        No-op if no incident is currently open — a benign race (e.g. a slow POST completing
        after the driver already recovered), not caller error.

        Unconditionally overwrites the tracked id, which is exactly what lets one field serve
        two roles: read as `related_alert_id` on a later escalation (before this method's next
        call overwrites it), and read as `resolved_incident_id` at recovery (the *latest* posted
        row's id — the escalation's id if one occurred, else the original's).

        If escalation time arrives before this method has been called for the currently-open
        alert, `update()` proceeds anyway with `related_alert_id=None` rather than waiting on a
        possibly-slow or still-pending HTTP call — delaying a genuinely dangerous critical
        escalation to preserve a linkage field is the wrong tradeoff for a safety system.
        `escalation_seconds` (20s) is generous relative to any real HTTP round-trip, so this
        should be rare in practice.
        """
        if self._confirmed_severity is Severity.LOW:
            return
        self._open_incident_id = alert_id
