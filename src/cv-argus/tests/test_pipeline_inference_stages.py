"""`pipeline/inference_stages.py` — `FusedInferenceStage`, the thin wrapper around
`FusedDrowsinessDetector`. It is deliberately dumb: translate skip/no-skip, call the detector,
copy its phase timings into `StageStats`. A stub detector is enough to pin all of that.
"""

import logging

import numpy as np

from cv_argus.model.detector import DetectionResult
from cv_argus.pipeline import FrameContext
from cv_argus.pipeline.inference_stages import FusedInferenceStage

LOGGER = "cv_argus.pipeline.inference_stages"


class _StubDetector:
    def __init__(self):
        self.calls = []
        self.last_phase_seconds = {"embed": 0.01, "lstm": 0.02}

    def predict_frame(self, crop, geo):
        self.calls.append((crop, geo))
        return DetectionResult(level=2, probabilities=np.array([0.1, 0.9]))


def _ctx(*, face_found, crop=True, geo=True):
    features = {}
    if crop:
        features["face_crop_rgb"] = np.zeros((10, 10, 3), dtype=np.uint8)
    if geo:
        features["fused_geo_features"] = np.zeros(10, dtype=np.float32)
    return FrameContext(
        frame_bgr=np.zeros((10, 10, 3), dtype=np.uint8),
        timestamp_ms=0,
        face_found=face_found,
        features=features,
    )


def test_no_face_skips_the_detector():
    det = _StubDetector()
    stage = FusedInferenceStage(det)
    out = stage.process_item(_ctx(face_found=False))
    assert det.calls == []
    assert out.detection is None


def test_face_found_but_no_crop_skips():
    det = _StubDetector()
    stage = FusedInferenceStage(det)
    stage.process_item(_ctx(face_found=True, crop=False))
    assert det.calls == []


def test_missing_geo_features_warns_and_skips(caplog):
    det = _StubDetector()
    stage = FusedInferenceStage(det)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        out = stage.process_item(_ctx(face_found=True, crop=True, geo=False))
    assert det.calls == []
    assert out.detection is None
    assert any("fused_geo_features missing" in r.message for r in caplog.records)


def test_crop_and_geo_present_runs_inference():
    det = _StubDetector()
    stage = FusedInferenceStage(det)
    out = stage.process_item(_ctx(face_found=True))
    assert len(det.calls) == 1
    assert isinstance(out.detection, DetectionResult)
    assert out.detection.class_name == "Drowsy"


def test_phase_timings_are_forwarded_to_stats():
    det = _StubDetector()
    stage = FusedInferenceStage(det, stats_interval=10.0)  # arm StageStats
    stage.process_item(_ctx(face_found=True))
    assert set(stage.stats._phases) == {"embed", "lstm"}
