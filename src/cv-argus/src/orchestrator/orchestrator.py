"""`Orchestrator` — decides whether a `DetectionResult` is worth raising an Alert over, and
emits a periodic RouteStatus("OK") heartbeat otherwise. Owns its own thread, independent of the
`Pipeline` (see the approved alert-pipeline plan's "three independent peers" decision) — fed by
`OrchestratorBridgeOutputStage` (see `bridge.py`) via a plain `queue.Queue`.

**One decision loop, not two independent timers**: every wake — whether it's a real detection or
a poll timeout — checks both "should I alert" (debounce + cooldown) and "is a heartbeat due".
This matches the draw.io diagram's single "Alert/RouteStatus Orchestration (Decision Making)"
box producing both message kinds into one shared queue (see `alerts/models.py`'s module
docstring).

**Debounce is rising-edge only**: N consecutive Drowsy detections are required before an Alert
fires, but there's no symmetric falling-edge hysteresis counter to "clear" that state — a
deliberate simplification, not an oversight. The cooldown already prevents a continuing drowsy
episode from re-alerting on every debounce window, so a release counter would add complexity
without preventing anything the cooldown doesn't already prevent. A detection that's either
`None` (no face found this frame — see `FusedInferenceStage`'s no-op-on-miss behavior) or
`Not Drowsy` both reset the consecutive-Drowsy counter the same way, treating an uncertain/absent
reading as "not confirmed drowsy" rather than freezing the counter through it.
"""

import logging
import queue
import threading
import time
from typing import TYPE_CHECKING

from .. import constants
from ..alerts import Alert, AlertKind, route_status
from ..buffer import Buffer

if TYPE_CHECKING:
    # Type-hint only, never imported at runtime -- cv_argus.model eagerly imports tensorflow
    # (see model/layers.py), and this module only ever duck-types a detection's .level/
    # .class_name/.probabilities, so importing the real class here would needlessly pull in the
    # full stack. Mirrors pipeline/stage.py's identical TYPE_CHECKING-only import of the same
    # class, for the same reason.
    from ..model.detector import DetectionResult

logger = logging.getLogger(__name__)


class Orchestrator:
    """Consumes `(source_id, DetectionResult | None)` pairs from `input_queue` (fed by
    `OrchestratorBridgeOutputStage`) on its own thread, and calls `buffer.enqueue()` for both
    Alerts and RouteStatus heartbeats it decides to raise.

    `start()`/`stop()`/`join()`/`is_alive` deliberately mirror `pipeline.stage.Pipeline`'s own
    method names, even though this isn't a `Pipeline`/`Stage` subclass, so `main.py` can treat
    all three top-level components (Pipeline, Orchestrator, sender's `SenderServer`) uniformly.
    """

    def __init__(
        self,
        buffer: Buffer,
        *,
        debounce_frames: int = constants.ORCHESTRATOR_DEBOUNCE_FRAMES,
        cooldown_seconds: float = constants.ORCHESTRATOR_ALERT_COOLDOWN_SECONDS,
        heartbeat_interval_seconds: float = constants.ORCHESTRATOR_HEARTBEAT_INTERVAL_SECONDS,
        poll_seconds: float = constants.ORCHESTRATOR_LOOP_POLL_SECONDS,
        input_maxsize: int = 16,
    ) -> None:
        self.input_queue: queue.Queue = queue.Queue(maxsize=input_maxsize)
        self._buffer = buffer
        self._debounce_frames = debounce_frames
        self._cooldown_seconds = cooldown_seconds
        self._heartbeat_interval = heartbeat_interval_seconds
        self._poll_seconds = poll_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        # Decision-loop state -- owned exclusively by the loop thread (_run/_on_detection/
        # _maybe_heartbeat), never touched from outside it, so no lock is needed here.
        self._consecutive_drowsy = 0
        self._last_alert_at: float | None = None  # monotonic
        self._last_heartbeat_at: float | None = None  # monotonic

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
        self._last_heartbeat_at = time.monotonic()
        while not self._stop_event.is_set():
            try:
                source_id, detection = self.input_queue.get(timeout=self._poll_seconds)
            except queue.Empty:
                self._maybe_heartbeat(source_id="default")
                continue
            self._on_detection(source_id, detection)
            self._maybe_heartbeat(source_id)
        logger.info("orchestrator: stopped")

    def _on_detection(self, source_id: str, detection: "DetectionResult | None") -> None:
        if detection is None or detection.level != 2:  # not confirmed Drowsy this frame
            self._consecutive_drowsy = 0
            return
        self._consecutive_drowsy += 1
        if self._consecutive_drowsy < self._debounce_frames:
            return
        now = time.monotonic()
        if self._last_alert_at is not None and (now - self._last_alert_at) < self._cooldown_seconds:
            return
        alert = Alert.new(
            AlertKind.DROWSINESS,
            level=detection.level,
            source_id=source_id,
            payload={
                "class_name": detection.class_name,
                "probabilities": detection.probabilities.tolist(),
            },
        )
        self._buffer.enqueue(alert)
        self._last_alert_at = now
        # An Alert also resets the heartbeat clock -- no need for an "OK" to immediately follow
        # right when the heartbeat interval would otherwise have triggered one.
        self._last_heartbeat_at = now
        logger.info("orchestrator: raised %s alert (id=%s)", detection.class_name, alert.id)

    def _maybe_heartbeat(self, source_id: str) -> None:
        now = time.monotonic()
        if self._last_heartbeat_at is None or (now - self._last_heartbeat_at) >= self._heartbeat_interval:
            self._buffer.enqueue(route_status(source_id))
            self._last_heartbeat_at = now
