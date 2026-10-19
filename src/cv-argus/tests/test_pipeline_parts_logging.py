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
