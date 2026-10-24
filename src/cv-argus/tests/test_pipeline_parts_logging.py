"""The parts pipeline's report logs: per-frame timings, the readable log line, the JSONL metrics
sink (`OUTPUTS=metrics`) and `scripts/summarize_metrics.py`. Stub detector, no model files."""

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("cv2")

from cv_argus.model import parts_detector as pd_  # noqa: E402
from cv_argus.pipeline.output_stages import (  # noqa: E402
    MetricsLogOutputStage,
    format_parts_line,
    parts_frame_record,
)
from cv_argus.pipeline.parts_stages import PartsInferenceStage  # noqa: E402
from cv_argus.pipeline.stage import FrameContext  # noqa: E402

FACE = (300, 160, 120, 150)
EYE_NORM = [0.30, 0.35, 0.42, 0.42]


class _StubDetector:
    precision = "int8"
    closed_eye_threshold, head_down_threshold = 0.4, 0.3

    def __init__(self, cls):
        self.cls = cls
        self.last_phase_seconds = {"infer": 0.001, "decode": 0.0001}

    def detect(self, x):
        return pd_.PartDetections(np.array([EYE_NORM], np.float32), np.array([0.9], np.float32),
                                  np.array([self.cls]))


def _ctx(ts_ms, face=True):
    ctx = FrameContext(frame_bgr=np.zeros((480, 640, 3), np.uint8), timestamp_ms=ts_ms)
    if face:
        ctx.face_found = True
        ctx.features["face_bbox_raw"] = FACE
    return ctx


def _run(cls, n):
    stage = PartsInferenceStage(_StubDetector(cls))
    return [stage.process_item(_ctx(k * 200)) for k in range(n)]


def test_per_frame_timings_and_report_record():
    ctx = _run(pd_.CLOSED_EYE, 17)[-1]
    assert {"crop", "infer", "decode", "track", "rules", "parts_stage"} <= set(ctx.features["timing_ms"])
    assert ctx.features["timing_ms"]["infer"] == pytest.approx(1.0)
    rec = parts_frame_record(ctx)
    assert rec["severity"] == "critical" and rec["eyes"] == ["closed_eye"] and rec["eyes_closed"]
    assert rec["class_name"] == "Drowsy" and rec["timers"]["eyes_closed"] >= 3.0
    line = format_parts_line(rec)
    assert "criticality=CRITICAL (eyes_closed" in line and "eyes=closed" in line
    # Every pipeline step that ran, in order, plus queueing and end to end.
    assert "ms crop " in line and " infer 1.0 decode 0.1 track " in line and " rules " in line
    assert " queue " in line and " e2e " in line
    assert rec["queue_ms"] >= 0.0
    # The line reports criticality, not the ESP32's binary class (that stays in the record).
    assert "Drowsy" not in line and "p=" not in line


def test_report_record_for_a_faceless_frame():
    ctx = PartsInferenceStage(_StubDetector(pd_.OPEN_EYE)).process_item(_ctx(0, face=False))
    rec = parts_frame_record(ctx)
    assert rec["face"] is False and rec["class_name"] is None and rec["eyes"] == []
    line = format_parts_line(rec)
    assert line.startswith("[default] criticality=NONE | face=no | eyes=-")


def test_quiet_frame_says_criticality_none():
    line = format_parts_line(parts_frame_record(_run(pd_.OPEN_EYE, 1)[-1]))
    assert "criticality=NONE | face=yes | eyes=open" in line and "blazeface" not in line


def test_non_parts_frames_have_no_record():
    assert parts_frame_record(FrameContext(frame_bgr=np.zeros((4, 4, 3), np.uint8), timestamp_ms=0)) is None


def test_metrics_log_writes_one_json_line_per_frame(tmp_path):
    sink = MetricsLogOutputStage(path=tmp_path / "m.jsonl")
    for ctx in _run(pd_.OPEN_EYE, 3):
        sink.handle(ctx)
    sink.handle(FrameContext(frame_bgr=np.zeros((4, 4, 3), np.uint8), timestamp_ms=0))   # skipped
    sink.close()
    recs = [json.loads(line) for line in (tmp_path / "m.jsonl").read_text().splitlines()]
    assert [r["t_ms"] for r in recs] == [0, 200, 400]
    assert recs[0]["eyes"] == ["open_eye"] and "infer" in recs[0]["timing_ms"]


def _with_lock(ctx, **lock):
    ctx.features["face_lock"] = {"faces": 1, "ignored": 0, "switch_s": None, "takeover_s": None,
                                 "switched": False, "reacquired": False, **lock}
    return ctx


def test_frames_without_the_face_stage_have_no_face_lock_part():
    rec = parts_frame_record(_run(pd_.OPEN_EYE, 1)[-1])
    assert rec["face_lock"] is None
    assert " faces=" not in format_parts_line(rec)


def test_face_lock_fields_in_the_record_and_the_line():
    stage = PartsInferenceStage(_StubDetector(pd_.OPEN_EYE))
    ctx = stage.process_item(_with_lock(_ctx(0), faces=2, ignored=1))
    rec = parts_frame_record(ctx)
    assert rec["face_lock"] == {"faces": 2, "ignored": 1, "switch_s": None, "takeover_s": None,
                                "switched": False, "reacquired": False}
    assert "| face=yes faces=2 ignored=1 | eyes=open" in format_parts_line(rec)


def test_face_lock_switch_timer_and_switch_frame_in_the_line():
    stage = PartsInferenceStage(_StubDetector(pd_.OPEN_EYE))
    ctx = stage.process_item(_with_lock(_ctx(0, face=False), faces=1, ignored=1, switch_s=4.2371))
    rec = parts_frame_record(ctx)
    assert rec["face_lock"]["switch_s"] == 4.24
    assert "| face=no faces=1 ignored=1 switch=4.2s | eyes=-" in format_parts_line(rec)
    ctx = _with_lock(_ctx(200, face=False), faces=1, ignored=1, switch_s=5.0, switched=True)
    ctx.features["face_switched"] = True
    line = format_parts_line(parts_frame_record(stage.process_item(ctx)))
    assert "face=no faces=1 ignored=1 switch=5.0s SWITCHED |" in line


def test_face_lock_takeover_timer_in_the_line():
    stage = PartsInferenceStage(_StubDetector(pd_.OPEN_EYE))
    ctx = stage.process_item(_with_lock(_ctx(0), faces=2, ignored=1, takeover_s=1.2))
    assert "face=yes faces=2 ignored=1 takeover=1.2s |" in format_parts_line(parts_frame_record(ctx))


def test_face_lock_from_the_real_face_stage_reaches_the_metrics_jsonl(tmp_path):
    pytest.importorskip("mediapipe")
    from types import SimpleNamespace

    from cv_argus.pipeline.face_detector_stage import FaceDetectorCropStage

    def det(box):
        x, y, w, h = box
        return SimpleNamespace(bounding_box=SimpleNamespace(origin_x=x, origin_y=y, width=w, height=h),
                               categories=[SimpleNamespace(score=0.9)])

    passenger = (40, 180, 80, 100)
    frames = [[det(FACE), det(passenger)], [det(passenger)]]

    class _Fake:
        def detect_for_video(self, image, ts_ms):
            return SimpleNamespace(detections=frames[ts_ms // 200])

        def close(self):
            pass

    face = FaceDetectorCropStage(detector=_Fake())
    parts = PartsInferenceStage(_StubDetector(pd_.OPEN_EYE))
    sink = MetricsLogOutputStage(path=tmp_path / "m.jsonl")
    for k in range(2):
        sink.handle(parts.process_item(face.process_item(_ctx(k * 200, face=False))))
    sink.close()
    recs = [json.loads(line) for line in (tmp_path / "m.jsonl").read_text().splitlines()]
    assert recs[0]["face_lock"] == {"faces": 2, "ignored": 1, "switch_s": None, "takeover_s": None,
                                    "switched": False, "reacquired": False}
    assert recs[1]["face_lock"] == {"faces": 1, "ignored": 1, "switch_s": 0.0, "takeover_s": None,
                                    "switched": False, "reacquired": False}


def test_face_lock_reacquired_in_the_record_and_the_line():
    stage = PartsInferenceStage(_StubDetector(pd_.OPEN_EYE))
    rec = parts_frame_record(stage.process_item(_with_lock(_ctx(0), reacquired=True)))
    assert rec["face_lock"]["reacquired"] is True
    assert "| face=yes faces=1 ignored=0 REACQUIRED | eyes=open" in format_parts_line(rec)
    # A normally tracked frame carries no token.
    rec = parts_frame_record(stage.process_item(_with_lock(_ctx(200))))
    assert rec["face_lock"]["reacquired"] is False
    assert "REACQUIRED" not in format_parts_line(rec)


def test_face_lock_record_defaults_reacquired_to_false_for_older_dicts():
    # A face_lock dict without the key (an older/other producer) still records a bool.
    stage = PartsInferenceStage(_StubDetector(pd_.OPEN_EYE))
    ctx = _ctx(0)
    ctx.features["face_lock"] = {"faces": 1, "ignored": 0, "switch_s": None, "takeover_s": None,
                                 "switched": False}
    rec = parts_frame_record(stage.process_item(ctx))
    assert rec["face_lock"]["reacquired"] is False
    assert "REACQUIRED" not in format_parts_line(rec)


def test_lone_driver_box_jump_is_logged_as_reacquired_through_the_real_face_stage(tmp_path):
    """C1.7 rule 1 through the real `FaceDetectorCropStage`: a lone driver whose box jumps more
    than 0.6 x side is re-acquired on the same frame (face found, no no-face frame), and that
    frame is flagged `reacquired` in ctx.features, the metrics JSONL and the log line."""
    pytest.importorskip("mediapipe")
    from types import SimpleNamespace

    from cv_argus.pipeline.face_detector_stage import FaceDetectorCropStage

    def det(box):
        x, y, w, h = box
        return SimpleNamespace(bounding_box=SimpleNamespace(origin_x=x, origin_y=y, width=w, height=h),
                               categories=[SimpleNamespace(score=0.9)])

    jumped = (FACE[0] + 120, FACE[1], FACE[2], FACE[3])     # centre shift 120 px > 0.6 x 150
    frames = [[det(FACE)], [det(jumped)], [det(jumped)]]

    class _Fake:
        def detect_for_video(self, image, ts_ms):
            return SimpleNamespace(detections=frames[ts_ms // 200])

        def close(self):
            pass

    face = FaceDetectorCropStage(detector=_Fake())
    parts = PartsInferenceStage(_StubDetector(pd_.OPEN_EYE))
    sink = MetricsLogOutputStage(path=tmp_path / "m.jsonl")
    ctxs = []
    for k in range(3):
        ctx = parts.process_item(face.process_item(_ctx(k * 200, face=False)))
        ctxs.append(ctx)
        sink.handle(ctx)
    sink.close()
    assert [c.face_found for c in ctxs] == [True, True, True]
    assert [c.features["face_lock"]["reacquired"] for c in ctxs] == [False, True, False]
    assert ctxs[1].features["face_bbox_raw"] == jumped
    recs = [json.loads(line) for line in (tmp_path / "m.jsonl").read_text().splitlines()]
    assert recs[1]["face_lock"] == {"faces": 1, "ignored": 0, "switch_s": None, "takeover_s": None,
                                    "switched": False, "reacquired": True}
    assert [r["face_lock"]["reacquired"] for r in recs] == [False, True, False]
    lines = [format_parts_line(r) for r in recs]
    assert "face=yes faces=1 ignored=0 REACQUIRED |" in lines[1]
    assert "REACQUIRED" not in lines[0] and "REACQUIRED" not in lines[2]


def test_metrics_output_is_selectable(monkeypatch, tmp_path):
    from cv_argus import main as main_mod
    monkeypatch.setenv("OUTPUTS", "logging,metrics")
    monkeypatch.setenv("METRICS_LOG_PATH", str(tmp_path / "m.jsonl"))
    outputs = main_mod._build_outputs()
    assert isinstance(outputs[1], MetricsLogOutputStage) and outputs[1].path == tmp_path / "m.jsonl"
    outputs[1].close()


def test_summarize_metrics_script():
    recs = [parts_frame_record(c) for c in _run(pd_.CLOSED_EYE, 20)]
    path = Path(__file__).resolve().parents[1] / "scripts" / "summarize_metrics.py"
    if not path.exists():   # the Docker test stage copies src/ and tests/ only, not scripts/
        pytest.skip("scripts/ not present")
    spec = importlib.util.spec_from_file_location("summarize_metrics", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    latency, states, episodes, span_s = mod.summarize(json.loads(json.dumps(recs)))
    assert span_s == pytest.approx(3.8)
    assert {r["phase"] for r in latency} >= {"infer", "e2e"}
    assert states["eyes=closed"] == 20 and states["severity=critical"] >= 1
    assert episodes == {"medium:eyes_closed": 1, "critical:eyes_closed": 1}
    assert "queue" in {r["phase"] for r in latency}


def test_summarize_resources_dedupes_repeated_samples():
    path = Path(__file__).resolve().parents[1] / "scripts" / "summarize_metrics.py"
    if not path.exists():
        pytest.skip("scripts/ not present")
    spec = importlib.util.spec_from_file_location("summarize_metrics", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    a = {"cpu_cores": [10.0, 20.0], "cpu_total": 15.0, "proc_cpu": 30.0, "rss_mb": 400.0, "mem_used_pct": 20.0}
    b = {"cpu_cores": [30.0, 40.0], "cpu_total": 35.0, "proc_cpu": 50.0, "rss_mb": 420.0, "mem_used_pct": 22.0}
    rows = {r["metric"]: r for r in mod.summarize_resources([{"res": a}] * 5 + [{"res": b}] * 5)}
    assert rows["cpu_total_pct"]["n"] == 2 and rows["cpu_total_pct"]["mean"] == 25.0
    assert rows["cpu1_pct"]["max"] == 40.0 and rows["rss_mb"]["max"] == 420.0
