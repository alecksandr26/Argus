"""Sink stages: what happens to a finished `FrameContext`.

For the `parts` pipeline, both sinks here report *what the detector saw and how the rules judged
it*, not only `Drowsy`/`Not Drowsy`:
- `LoggingOutputStage` logs one readable line per frame, led by the **criticality** (none / low /
  medium / critical) and its reason, then the eye, mouth and head states and the timings. The
  binary `Drowsy`/`Not Drowsy` class is left out of the line on purpose: it's only the ESP32
  contract's coarse view of the same criticality (see `orchestrator/parts_rules.to_detection`),
  and it's still in the metrics record and the buffered reading. Each line also carries the
  time of **every pipeline step** (BlazeFace, crop, infer, decode, track, rules, the queueing
  between stages, end to end), so the bottleneck is visible frame by frame, and the board's
  CPU per core / RAM (`resources.py`, re-sampled once a second), and the face lock's verdict
  (faces seen, faces ignored, the switch / take-over timers, the switch frame; contracts C1.7).
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
from .resources import shared_sampler
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
    timing = {k: round(v, 3) for k, v in ctx.features.get("timing_ms", {}).items()}
    e2e = 1000.0 * (time.monotonic() - ctx.created_at)
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
        "timing_ms": timing,
        "e2e_ms": round(e2e, 3),
        # Capture-to-here minus the measured work: time spent waiting in queues between stages.
        "queue_ms": round(max(0.0, e2e - timing.get("face", 0.0) - timing.get("parts_stage", 0.0)), 3),
        "res": shared_sampler().current(),
        "face_lock": _face_lock_record(ctx.features.get("face_lock")),
    }


def _round_or_none(v, nd=2):
    return None if v is None else round(float(v), nd)


def _face_lock_record(lock: dict | None) -> dict | None:
    """`FaceDetectorCropStage`'s face-lock verdict (contracts C1.7): faces seen, faces ignored
    (not the locked one), the missing-face switch timer, the take-over timer, and whether this
    is a switch frame. `None` when the face stage didn't run (tests, other pipelines)."""
    if lock is None:
        return None
    return {"faces": int(lock.get("faces", 0)), "ignored": int(lock.get("ignored", 0)),
            "switch_s": _round_or_none(lock.get("switch_s")),
            "takeover_s": _round_or_none(lock.get("takeover_s")),
            "switched": bool(lock.get("switched", False))}


def _format_face_lock(lock: dict | None) -> str:
    """` faces=2 ignored=1 switch=2.4s SWITCHED`, or "" without a face-lock verdict."""
    if lock is None:
        return ""
    out = f" faces={lock['faces']} ignored={lock['ignored']}"
    if lock["switch_s"] is not None:
        out += f" switch={lock['switch_s']:.1f}s"
    if lock["takeover_s"] is not None:
        out += f" takeover={lock['takeover_s']:.1f}s"
    if lock["switched"]:
        out += " SWITCHED"
    return out


# Pipeline steps in order, as (timing_ms key, label in the log line).
_STEPS = (("face", "blazeface"), ("crop", "crop"), ("infer", "infer"), ("decode", "decode"),
          ("track", "track"), ("rules", "rules"))


def format_parts_line(rec: dict) -> str:
    """The human-readable log line for one `parts_frame_record`.

    `[src] criticality=CRITICAL (no_face 5.0s) | face=no faces=1 ignored=1 switch=4.2s | eyes=-
    mouth=- head=- | eyes_closed 0.0s head_down 0.0s | ms blazeface 6.8 crop 0.6 infer 0.7
    decode 0.3 track 0.1 rules 0.0 queue 0.3 e2e 7.2 | cpu 23% [12 30 25 26] proc 45% | ram 412MB
    sys 18%`

    The face-lock part (`faces`, `ignored`, `switch=` while the locked face is missing and only
    bystanders are in view, `takeover=` while a larger face waits to take the lock, `SWITCHED` on
    the switch frame) is there when `FaceDetectorCropStage` ran, so "a second person in view has
    no effect" can be checked from the Pi's log.
    """
    sev = rec["severity"].upper()
    verdict = f"criticality={sev}"
    if rec["severity"] != "none":
        verdict += f" ({rec['reason']} {rec['trigger_s']:.1f}s)"
    eyes = "/".join(_short(e) for e in rec["eyes"]) or "-"
    tm = rec["timing_ms"]
    steps = " ".join(f"{label} {tm[k]:.1f}" for k, label in _STEPS if k in tm)
    line = (f"[{rec['source']}] {verdict} | face={'yes' if rec['face'] else 'no'}"
            f"{_format_face_lock(rec.get('face_lock'))} | "
            f"eyes={eyes} mouth={_short(rec['mouth'])} "
            f"head={_short(rec['head'])} | eyes_closed {rec['timers'].get('eyes_closed', 0):.1f}s "
            f"head_down {rec['timers'].get('head_down', 0):.1f}s | "
            f"ms {steps} queue {rec['queue_ms']:.1f} e2e {rec['e2e_ms']:.1f}")
    res = rec.get("res") or {}
    if res.get("cpu_cores") is not None:
        cores = " ".join(f"{c:.0f}" for c in res["cpu_cores"])
        line += f" | cpu {res['cpu_total']:.0f}% [{cores}]"
        if res.get("proc_cpu") is not None:
            line += f" proc {res['proc_cpu']:.0f}%"
    if res.get("rss_mb") is not None:
        line += f" | ram {res['rss_mb']:.0f}MB"
        if res.get("mem_used_pct") is not None:
            line += f" sys {res['mem_used_pct']:.0f}%"
    return line


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
