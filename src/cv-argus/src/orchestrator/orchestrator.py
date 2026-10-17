"""`Orchestrator` — turns per-frame `DetectionResult`s into the *readings* the ESP32 fuses with its
grip sensor, plus a periodic RouteStatus("OK") heartbeat. Owns its own thread, independent of the
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

**One decision loop**: every wake -- a detection or a poll timeout -- checks for a class change,
for due keepalives (so a stalled pipeline still keeps the ESP32 informed), for the heartbeat, and
for periodic buffer housekeeping. Readings don't reset the heartbeat clock: the heartbeat is
what tells the backend the route is alive, independent of the camera.
"""
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .. import constants
from ..alerts import Alert, AlertKind, route_status
from ..buffer import Buffer

if TYPE_CHECKING:
    from ..model.detector import DetectionResult

logger = logging.getLogger(__name__)

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


def _classify(detection: "DetectionResult | None") -> str:
    if detection is None:
        return NO_FACE
    return DROWSY if detection.level == 2 else NOT_DROWSY


class Orchestrator:
    """Consumes `(source_id, DetectionResult | None)` pairs from `input_queue` (fed by
    `OrchestratorBridgeOutputStage`) on its own thread and enqueues readings and heartbeats into
    `buffer`.

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
        heartbeat_interval_seconds: float = constants.ORCHESTRATOR_HEARTBEAT_INTERVAL_SECONDS,
        poll_seconds: float = constants.ORCHESTRATOR_LOOP_POLL_SECONDS,
        input_maxsize: int = 16,
    ) -> None:
        self.input_queue: queue.Queue = queue.Queue(maxsize=input_maxsize)
        self._buffer = buffer
        self._debounce_frames = debounce_frames
        self._keepalive_seconds = keepalive_seconds
        self._warmup_frames = warmup_frames
        self._heartbeat_interval = heartbeat_interval_seconds
        self._poll_seconds = poll_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._sources: dict[str, _SourceState] = {}
        self._last_heartbeat_at = time.monotonic()
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

        cls = _classify(detection)
        if detection is not None:
            state.probabilities[cls] = detection.probabilities.tolist()

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
        """Poll-timeout work: keepalives for every source, the heartbeat, buffer housekeeping."""
        for sid, state in self._sources.items():
            self._maybe_keepalive(sid, state)
        self._maybe_heartbeat(source_id)
        self._maybe_maintain_buffer()

    def _maybe_keepalive(self, source_id: str, state: _SourceState) -> None:
        if state.reported is None or state.last_reading_at is None:
            return
        if time.monotonic() - state.last_reading_at >= self._keepalive_seconds:
            self._emit_reading(source_id, state, transition=False)

    def _emit_reading(self, source_id: str, state: _SourceState, *, transition: bool) -> None:
        cls = state.reported
        reading = Alert.new(
            AlertKind.DROWSINESS,
            level=_LEVELS[cls],
            source_id=source_id,
            payload={
                "class_name": cls,
                "probabilities": None if cls == NO_FACE else state.probabilities.get(cls),
                "transition": transition,
            },
        )
        state.last_reading_at = time.monotonic()
        if self._enqueue(reading) and transition:
            logger.info("orchestrator: %s -> %s", source_id, cls)

    def _maybe_heartbeat(self, source_id: str) -> None:
        now = time.monotonic()
        if now - self._last_heartbeat_at >= self._heartbeat_interval:
            self._last_heartbeat_at = now
            self._enqueue(route_status(source_id))

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
                logger.warning("orchestrator: buffer full, dropped %d old keepalives", dropped)
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
