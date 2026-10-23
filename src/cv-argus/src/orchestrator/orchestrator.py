"""`Orchestrator` — turns per-frame `DetectionResult`s into the *readings* the ESP32 turns into
backend events and GPS-report vigilance. Owns its own thread, independent of the
`Pipeline` (see the approved alert-pipeline plan's "three independent peers" decision) — fed by
`OrchestratorBridgeOutputStage` (see `bridge.py`) via a plain `queue.Queue`.

**Readings, not alerts.** The decision whether to alert belongs to the ESP32, the only device
that has both the camera's class and the grip sensor (`fusion_contract.py`, ported to
`fusion.cpp`). Its debounce/escalation/recovery windows need to see the class *over time*, so
this loop reports the camera's class as a stream: a `drowsiness` record on every class change
(`transition: true`) and a keepalive every `keepalive_seconds` while it stays the same
(`transition: false`). Three classes: `Drowsy` (level 2), `Not Drowsy` (level 1) and `No Face`
(level None, no probabilities). Before this, it emitted only `Drowsy`, at most once per 30 s
cooldown, and never `Not Drowsy` -- so the ESP32 could raise an incident but never see it end.

**Hysteresis in both directions**: the reported class only changes after `debounce_frames`
consecutive frames of another class, so one bad frame (a blink, a head turn) neither raises nor
clears anything.

**No face is its own class**, not "not drowsy": a drowsy driver slumping out of frame must never
read as a recovery. The ESP32 decides what sustained `No Face` means (fusion treats it as
drowsy after a while).

**State is per camera source** (`source_id`), so two cameras can't reset each other.

**Severity changes are transitions too.** The parts rules give a severity (none/low/medium/
critical); the ESP32 alerts on it directly (no grip sensor yet). A change of severity is emitted
as a `transition: true` reading right away, even when the class hasn't changed (medium ->
critical are both `Drowsy`), with `previous_severity` and an `episode` id: one id from the moment
the severity reaches medium until it falls back to none/low, so the ESP32 can link an incident's
alert, its escalation and its recovery. The rules already have their own hysteresis, so this
isn't debounced again -- and a severity change also adopts this frame's class at once, so the
recovery record ("driver OK again") carries `Not Drowsy` right away instead of a stale `Drowsy`
followed by a class-only transition a few frames later (contract C1.5). A reason change at the
same severity emits nothing.

Which of these records become backend events (medium alert, critical alert or escalation, driver
OK) is decided by the ESP32 with the rule in contract C1.5; keepalives never reach the backend.

**One decision loop**: every wake -- a detection or a poll timeout -- checks for a class change,
for due keepalives (so a stalled pipeline still keeps the ESP32 informed) and for periodic buffer
housekeeping. There is no heartbeat any more (contract C1.5): the ESP32 reports GPS every 5 s on
its own, and the 2 s keepalives tell it the camera is alive.
"""
import logging
import math
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .. import constants
from ..alerts import Alert, AlertKind
from ..buffer import Buffer

if TYPE_CHECKING:
    from ..model.detector import DetectionResult

logger = logging.getLogger(__name__)

# Severities the ESP32 raises an alert for; an episode lasts while the severity is one of these.
_ALERTING = ("medium", "critical")

DROWSY = "Drowsy"
NOT_DROWSY = "Not Drowsy"
NO_FACE = "No Face"
_LEVELS = {DROWSY: 2, NOT_DROWSY: 1, NO_FACE: None}


@dataclass
class _SourceState:
    frames_seen: int = 0
    reported: str | None = None  # the class last sent to the buffer
    candidate: str | None = None  # a different class being debounced
    candidate_frames: int = 0
    last_reading_at: float | None = None  # monotonic
    # Latest probabilities per class, so a keepalive carries a fresh value of the class it reports.
    probabilities: dict[str, list[float]] = field(default_factory=dict)
    # Latest detector extras (DetectionResult.extras), merged into every reading's payload.
    extras: dict = field(default_factory=dict)
    # The rule severity last sent, and the open episode (see the module docstring).
    reported_severity: str | None = None
    current_severity: str | None = None  # this frame's, see _severity()
    episode: str | None = None
    latest_probabilities: list[float] | None = None


def _classify(detection: "DetectionResult | None") -> str:
    if detection is None:
        return NO_FACE
    return DROWSY if detection.level == 2 else NOT_DROWSY


class Orchestrator:
    """Consumes `(source_id, DetectionResult | None)` pairs from `input_queue` (fed by
    `OrchestratorBridgeOutputStage`) on its own thread and enqueues readings into `buffer`.

    `start()`/`stop()`/`join()`/`is_alive` deliberately mirror `pipeline.stage.Pipeline`'s own
    method names, even though this isn't a `Pipeline`/`Stage` subclass, so `main.py` can treat
    all three top-level components (Pipeline, Orchestrator, sender's `SenderServer`) uniformly.
    """

    def __init__(
        self,
        buffer: Buffer,
        *,
        debounce_frames: int = constants.ORCHESTRATOR_DEBOUNCE_FRAMES,
        keepalive_seconds: float = constants.ORCHESTRATOR_KEEPALIVE_SECONDS,
        warmup_frames: int = constants.ORCHESTRATOR_WARMUP_FRAMES,
        poll_seconds: float = constants.ORCHESTRATOR_LOOP_POLL_SECONDS,
        input_maxsize: int = 16,
    ) -> None:
        self.input_queue: queue.Queue = queue.Queue(maxsize=input_maxsize)
        self._buffer = buffer
        self._debounce_frames = debounce_frames
        self._keepalive_seconds = keepalive_seconds
        self._warmup_frames = warmup_frames
        self._poll_seconds = poll_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._sources: dict[str, _SourceState] = {}
        self._last_maintenance_at = time.monotonic()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="orchestrator", daemon=False)
        self._thread.start()

    def stop(self) -> None:
        """Signal the decision loop to stop after its current wake. Does not join — see
        `join()`."""
        self._stop_event.set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    @property
    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                source_id, detection = self.input_queue.get(timeout=self._poll_seconds)
            except queue.Empty:
                self._tick("default")
                continue
            self._on_detection(source_id, detection)
            self._tick(source_id)
        logger.info("orchestrator: stopped")

    def _on_detection(self, source_id: str, detection: "DetectionResult | None") -> None:
        state = self._sources.setdefault(source_id, _SourceState())
        state.frames_seen += 1
        if state.frames_seen <= self._warmup_frames:
            return  # the model's window is still mostly padding

        if detection is not None and not all(
            math.isfinite(p) for p in detection.probabilities.tolist()
        ):
            # A NaN from the model (e.g. a zero eye width) says nothing about the driver: treat
            # the frame like one with no face instead of queueing NaN (REC-19).
            logger.debug("orchestrator: %s: non-finite probabilities, frame ignored", source_id)
            detection = None
        cls = _classify(detection)
        if detection is not None:
            state.probabilities[cls] = detection.probabilities.tolist()
            state.latest_probabilities = state.probabilities[cls]
        else:
            state.latest_probabilities = None
        state.extras = dict(getattr(detection, "extras", None) or {})
        state.current_severity = _severity(detection, state.extras)

        severity = state.current_severity
        if (
            state.reported is not None
            and severity is not None
            and severity != state.reported_severity
        ):
            # The rules debounce severity themselves; the class follows it at once, so the
            # record (a raise, an escalation or the recovery) carries this frame's class.
            state.reported = cls
            state.candidate, state.candidate_frames = None, 0
            self._emit_reading(source_id, state, transition=True, severity_change=True)
            return
        if cls == state.reported:
            state.candidate, state.candidate_frames = None, 0
        else:
            if cls == state.candidate:
                state.candidate_frames += 1
            else:
                state.candidate, state.candidate_frames = cls, 1
            if state.candidate_frames >= self._debounce_frames:
                state.reported = cls
                state.candidate, state.candidate_frames = None, 0
                self._emit_reading(source_id, state, transition=True)
                return
        self._maybe_keepalive(source_id, state)

    def _tick(self, source_id: str) -> None:
        """Poll-timeout work: keepalives for every source, buffer housekeeping."""
        for sid, state in self._sources.items():
            self._maybe_keepalive(sid, state)
        self._maybe_maintain_buffer()

    def _maybe_keepalive(self, source_id: str, state: _SourceState) -> None:
        if state.reported is None or state.last_reading_at is None:
            return
        if time.monotonic() - state.last_reading_at >= self._keepalive_seconds:
            self._emit_reading(source_id, state, transition=False)

    def _emit_reading(
        self, source_id: str, state: _SourceState, *, transition: bool, severity_change: bool = False
    ) -> None:
        cls = state.reported
        # A severity change can come before the debounced class catches up (critical while the
        # class still reads Not Drowsy for a few frames), so it carries this frame's probabilities.
        # Only a No Face reading may go without probabilities (the ESP32 drops any other one
        # that has none), so a face-less frame falls back to the reported class's last values.
        probabilities = None if cls == NO_FACE else state.probabilities.get(cls)
        if severity_change and state.latest_probabilities is not None:
            probabilities = state.latest_probabilities
        payload = {
            # Detector extras (the parts pipeline's severity/reason/timers) go in first, so they
            # can never overwrite the keys the ESP32 and cap_unsent() depend on.
            **state.extras,
            "class_name": cls,
            "probabilities": probabilities,
            "transition": transition,
        }
        severity = state.current_severity
        if severity is not None:
            payload.setdefault("severity", severity)
            previous = state.reported_severity
            if transition and severity in _ALERTING and state.episode is None:
                state.episode = uuid.uuid4().hex[:12]
            payload["previous_severity"] = previous if transition else severity
            if state.episode is not None:
                payload["episode"] = state.episode
            state.reported_severity = severity
            if transition and severity not in _ALERTING:
                state.episode = None  # this reading is the episode's recovery
        reading = Alert.new(AlertKind.DROWSINESS, level=_LEVELS[cls], source_id=source_id,
                            payload=payload)
        state.last_reading_at = time.monotonic()
        if self._enqueue(reading) and transition:
            if severity is None:
                logger.info("orchestrator: %s -> %s", source_id, cls)
            else:
                reason = state.extras.get("reason") or ""
                logger.info(
                    "orchestrator: %s -> %s, severity %s -> %s (%s)%s | queued for the ESP32",
                    source_id, cls, payload["previous_severity"], severity, reason,
                    f", episode {payload['episode']}" if "episode" in payload else "",
                )

    def _maybe_maintain_buffer(self) -> None:
        now = time.monotonic()
        if now - self._last_maintenance_at < constants.BUFFER_MAINTENANCE_INTERVAL_SECONDS:
            return
        self._last_maintenance_at = now
        # Test doubles of Buffer only implement enqueue().
        prune = getattr(self._buffer, "prune_sent", None)
        cap = getattr(self._buffer, "cap_unsent", None)
        try:
            if prune is not None:
                cutoff_ms = int((time.time() - constants.BUFFER_KEEP_SENT_SECONDS) * 1000)
                prune(older_than_ms=cutoff_ms)
            if cap is not None and (dropped := cap(max_rows=constants.BUFFER_MAX_UNSENT_ROWS)):
                logger.warning("orchestrator: buffer full, %d old row(s) dropped (keepalives) "
                               "or archived (cap_unsent)", dropped)
        except Exception:
            logger.exception("orchestrator: buffer maintenance failed")

    def _enqueue(self, alert: Alert) -> bool:
        """Never lets a storage error (disk full, locked database) end the thread: the record is
        lost, but the loop keeps running and the next one may succeed (CV-10, BUF-04)."""
        try:
            self._buffer.enqueue(alert)
            return True
        except Exception:
            logger.exception("orchestrator: failed to enqueue %s record", alert.kind.value)
            return False


def _severity(detection, extras: dict) -> str | None:
    """The rule severity of this frame. A frame with no detection at all (no face, no rule
    active) is `none`; a detector that reports no severity (not the parts rules) is `None`, and
    then severity changes aren't tracked."""
    if detection is None:
        return "none"
    return extras.get("severity")
