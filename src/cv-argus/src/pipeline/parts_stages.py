"""The "parts" pipeline's inference stage, the model stage cv-argus runs.

One stage does crop → detect → remap → track → rules, because the steps are strictly
sequential, per camera stream, and each costs milliseconds; splitting them across threads
would only add queue hops. The model maths lives in `model/` (`parts_geometry`,
`parts_detector`, `parts_tracker`) and the decision logic in `orchestrator/parts_rules`. This
stage is the glue.

It reads `ctx.features["face_bbox_raw"]` from `FaceDetectorCropStage` (BlazeFace). No
FaceLandmarker: head pose comes from the detector's own `head_*` classes, a user decision.

It writes:
- `ctx.detection`: the rule engine's verdict in the existing `DetectionResult` contract, with
  severity, reason and timers in `.extras`. It is `None` when no face is seen **and** no rule is
  active. That keeps the orchestrator's "No Face" semantics; a driver who just dropped out of
  view while an alarm is held keeps the alarm.
- `ctx.features["parts"]`: everything the demo overlay draws. That covers the tracks (boxes
  already remapped to **frame pixels**), the crop square, the rule state and the precision.
- `ctx.features["timing_ms"]`: this frame's crop / infer / decode / track / rules times (ms),
  next to BlazeFace's `face`, for the metrics log.

Frames **without** a face still tick the rule engine, because
"face lost for 5 s" is itself a rule and timers need every timestamp.

**Face switch and take-over** (contracts C1.7). `FaceDetectorCropStage` marks
`ctx.features["face_switched"]` on two kinds of frame:
- a **switch** frame: the locked face was gone and someone else stayed in view for
  `FACE_SWITCH_AFTER_S`; the frame reads as no face;
- a **take-over** frame: the locked face is still visible, but another face at least
  `FACE_LOCK_TAKEOVER_SCALE` times its side stayed continuously in view for
  `FACE_SWITCH_AFTER_S`; the frame still reads the old locked face.
Either way the lock moves to the new face from the next frame. This stage feeds the flagged
frame to the rule engine **first** (as no face, or as the old face), then calls
`PartsRuleEngine.reset_signals()` (timers only; the held severity stays) and resets the
`PartsTracker`, so the new face starts with fresh timers and tracks.
"""

from __future__ import annotations

import logging
import time

from ..model.parts_detector import PartsDetector
from ..model.parts_geometry import model_input_from_frame, remap_crop_boxes
from ..model.parts_tracker import PartsTracker
from ..orchestrator.parts_rules import PartsRuleEngine, signals_from_tracks, to_detection
from .stage import FrameContext, Stage

logger = logging.getLogger(__name__)


class PartsInferenceStage(Stage):
    def __init__(self, detector: PartsDetector, rules: PartsRuleEngine | None = None,
                 tracker: PartsTracker | None = None, name: str = "parts_inference", **kwargs) -> None:
        super().__init__(name, **kwargs)
        self._detector = detector
        self._rules = rules or PartsRuleEngine()
        self._tracker = tracker or PartsTracker()

    def process_item(self, ctx: FrameContext) -> FrameContext:
        t0 = time.perf_counter()
        raw_box = ctx.features.get("face_bbox_raw") if ctx.face_found else None
        crop_square = None
        timing = ctx.features.setdefault("timing_ms", {})
        if raw_box is not None:
            x, crop_square = model_input_from_frame(ctx.frame_bgr, raw_box)
            t1 = time.perf_counter()
            dets = self._detector.detect(x)
            h, w = ctx.frame_bgr.shape[:2]
            boxes_px = remap_crop_boxes(dets.boxes, crop_square, (w, h))
            self.stats.record_phase("crop", t1 - t0)
            timing["crop"] = 1000.0 * (t1 - t0)
            for phase, seconds in self._detector.last_phase_seconds.items():
                self.stats.record_phase(phase, seconds)
                timing[phase] = 1000.0 * seconds
            t_track = time.perf_counter()
            tracks = self._tracker.update(boxes_px, dets.scores, dets.classes)
            timing["track"] = 1000.0 * (time.perf_counter() - t_track)
        else:
            # No face: no detections; tracks age out after PARTS_TRACK_MAX_MISSES frames.
            tracks = self._tracker.update([], [], [])

        t2 = time.perf_counter()
        state = self._rules.update(ctx.timestamp_ms / 1000.0, signals_from_tracks(raw_box is not None, tracks))
        self.stats.record_phase("rules", time.perf_counter() - t2)
        timing["rules"] = 1000.0 * (time.perf_counter() - t2)
        timing["parts_stage"] = 1000.0 * (time.perf_counter() - t0)

        if ctx.features.get("face_switched"):
            tracks = self._on_face_switch()

        ctx.detection = to_detection(state) if (raw_box is not None or state.severity != "none") else None
        ctx.features["parts"] = {
            "tracks": tracks,
            "crop_square": crop_square,
            "rules": state,
            "precision": self._detector.precision,
            "thresholds": (getattr(self._detector, "closed_eye_threshold", None),
                           getattr(self._detector, "head_down_threshold", None)),
            "infer_ms": 1000.0 * self._detector.last_phase_seconds.get("infer", 0.0),
        }
        return ctx

    def _on_face_switch(self) -> list:
        """After a switch or take-over frame went through the rules: reset the rule timers (never
        the held severity) and the tracker."""
        self._rules.reset_signals()
        self._tracker.reset()
        logger.info("face switch: rule timers and part tracks reset for the new face")
        return self._tracker.tracks
