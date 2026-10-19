"""Sink stages: what happens to a finished `FrameContext`.

For the `parts` pipeline, both sinks here report *what the detector saw and how the rules judged
it*, not only `Drowsy`/`Not Drowsy`:
- `LoggingOutputStage` logs one readable line per frame: the severity and reason, the eye, mouth
  and head states, and the model's inference time.
- `MetricsLogOutputStage` (`OUTPUTS=...,metrics`) appends the same facts as one JSON object per
  frame to `METRICS_LOG_PATH`, along with every timing (BlazeFace, crop, infer, decode, track,
  rules, end to end). This is the measurement log for the report; `scripts/summarize_metrics.py`
  turns it into latency percentiles and state/severity counts.

`LoggingOutputStage` is the placeholder here until `orchestrator/` exists — it reuses the
project's own already-stated fallback for `sender/`'s not-yet-built transport ("a logging/
no-op implementation is fine to start", see `src/cv-argus/CLAUDE.md`'s
`orchestrator/`/`buffer/`/`sender/`/`alerts/` section) so `main()` has somewhere to route
predictions without blocking on that design.

This is also the extension point for anything added later: a `VideoOverlayOutputStage` would
draw `ctx.detection.class_name` (and, if available, the face crop's bounding box) onto
`ctx.frame_bgr` and write/display it — needing zero changes to any stage upstream, since
`FrameContext` already carries the raw frame all the way through. That's what this design is
for; it isn't built here since nothing has asked for it yet beyond "would be cool at some point".
"""

import json
import logging
import os
import time
from pathlib import Path

from .. import constants
from .stage import FrameContext, OutputStage

logger = logging.getLogger(__name__)


def _short(label: str | None) -> str:
    return "-" if label is None else label.replace("_eye", "").replace("head_", "")


def parts_frame_record(ctx: FrameContext) -> dict | None:
    """One frame of the `parts` pipeline as a flat, JSON-ready dict (None for other pipelines)."""
    parts = ctx.features.get("parts")
    if parts is None:
        return None
    state, tracks = parts["rules"], parts["tracks"]
    # Same "two best eyes" choice the rules make (see parts_rules.signals_from_tracks).
    eyes = sorted((t for t in tracks if t.kind == "eye"), key=lambda t: (t.visible, t.score), reverse=True)[:2]
    mouth = next((t for t in tracks if t.kind == "mouth"), None)
    head = max((t for t in tracks if t.kind == "head"), key=lambda t: (t.visible, t.score), default=None)
    det = ctx.detection
    return {
        "t_ms": ctx.timestamp_ms,
        "wall_ms": int(time.time() * 1000),
        "source": ctx.source_id,
        "face": bool(ctx.face_found),
        "severity": state.severity,
        "reason": state.reason,
        "trigger_s": round(state.trigger_s, 2),
        "class_name": det.class_name if det is not None else None,
        "p_drowsy": round(float(det.probabilities[-1]), 3) if det is not None else None,
        "eyes": [t.label for t in eyes],
        "eyes_closed": bool(eyes) and all(t.label == "closed_eye" for t in eyes),
        "mouth": mouth.label if mouth is not None else None,
        "head": head.label if head is not None else None,
        "parts": [{"id": t.id, "label": t.label, "score": round(t.score, 3), "visible": t.visible}
                  for t in tracks],
        "timers": {k: round(v, 2) for k, v in state.timers.items()},
        "yawn_events": state.yawn_events,
        "precision": parts["precision"],
        "timing_ms": {k: round(v, 3) for k, v in ctx.features.get("timing_ms", {}).items()},
        "e2e_ms": round(1000.0 * (time.monotonic() - ctx.created_at), 3),
    }


def format_parts_line(rec: dict) -> str:
    """The human-readable log line for one `parts_frame_record`."""
    sev = rec["severity"].upper()
    verdict = sev if rec["severity"] == "none" else f"{sev} ({rec['reason']} {rec['trigger_s']:.1f}s)"
    eyes = "/".join(_short(e) for e in rec["eyes"]) or "-"
    tm = rec["timing_ms"]
    timing = " ".join(f"{k} {tm[k]:.1f}ms" for k in ("face", "infer", "parts_stage") if k in tm)
    cls = f"{rec['class_name']} p={rec['p_drowsy']:.2f}" if rec["class_name"] else "no face"
    return (f"[{rec['source']}] {verdict} | {cls} | eyes={eyes} mouth={_short(rec['mouth'])} "
            f"head={_short(rec['head'])} | eyes_closed {rec['timers'].get('eyes_closed', 0):.1f}s "
            f"head_down {rec['timers'].get('head_down', 0):.1f}s | {timing} e2e {rec['e2e_ms']:.1f}ms")


class LoggingOutputStage(OutputStage):
    """Logs each finished frame's detection result. Frames with no detection this cycle (no
    face found, or the inference stage skipped it) are logged at debug level only, so a
    driver briefly looking away doesn't flood the log the way a missed drowsiness reading would
    be worth seeing.
    """

    def __init__(self, name: str = "logging_output", **kwargs) -> None:
        super().__init__(name, **kwargs)

    def handle(self, ctx: FrameContext) -> None:
        rec = parts_frame_record(ctx)
        if rec is not None:
            # parts pipeline: the full per-frame verdict, faceless frames included.
            logger.info("%s", format_parts_line(rec))
            return
        if ctx.detection is None:
            logger.debug("[%s] no detection this frame (face_found=%s)", ctx.source_id, ctx.face_found)
            return
        extras = ctx.detection.extras
        logger.info(
            "[%s] %s (level=%d, probabilities=%s)%s",
            ctx.source_id,
            ctx.detection.class_name,
            ctx.detection.level,
            ctx.detection.probabilities,
            # The parts pipeline's rule verdict, e.g. " severity=critical reason=eyes_closed".
            f" severity={extras['severity']} reason={extras['reason']}" if "severity" in extras else "",
        )


class MetricsLogOutputStage(OutputStage):
    """Appends one JSON line per frame (`parts_frame_record`) to `path` for later measurement.

    The default path, `$BUFFER_DIR/metrics.jsonl`, sits on the `buffer-data` volume, so the log
    survives container restarts. Copy it out with
    `docker compose cp cv-argus:/app/data/metrics.jsonl .`.
    """

    def __init__(self, path: str | os.PathLike | None = None, name: str = "metrics_log", **kwargs) -> None:
        super().__init__(name, **kwargs)
        default = Path(os.environ.get("BUFFER_DIR") or constants.BUFFER_DIR_DEFAULT) / "metrics.jsonl"
        self.path = Path(path or os.environ.get("METRICS_LOG_PATH") or default)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a", encoding="utf-8", buffering=1)   # line-buffered
        logger.info("metrics log: %s", self.path)

    def handle(self, ctx: FrameContext) -> None:
        rec = parts_frame_record(ctx)
        if rec is not None:
            self._fh.write(json.dumps(rec, separators=(",", ":")) + "\n")

    def close(self) -> None:
        self._fh.close()
        super().close()
