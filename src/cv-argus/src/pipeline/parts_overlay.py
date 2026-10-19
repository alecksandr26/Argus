"""Demo overlay for the "parts" pipeline: what the detector, tracker and rules see, drawn on the
frame.

Everything is drawn in **frame pixels**. The part boxes reach this stage already remapped from
the 160×160 crop onto the frame (`model/parts_geometry.remap_crop_boxes`), and the frame drawn
on is the same `ctx.frame_bgr` inference ran on. So boxes sit on the eyes and mouth at any camera
resolution, including when the face is cut off at the frame edge.

What it draws:
- **Severity banner** (top): `CRITICAL  eyes closed 3.2 s`, coloured by severity.
- **Boxes:** the face box (thin white), the square crop the model actually saw (dashed grey), and
  every tracked part labelled `ID class conf` (E = eye, M = mouth, H = head):

  | Part | Colour |
  |---|---|
  | open eye | green |
  | closed eye | red |
  | yawn | magenta |
  | head straight | cyan |
  | head down | orange |

- **Timer panel** (bottom left): live bars for *eyes closed*, *head down* and *mouth open*. Each
  bar is scaled to its critical threshold and shows tick marks at the low / medium / critical
  thresholds currently in force. They move earlier after a yawn, which the panel says. It also
  shows the yawn-event count.
- **Footer:** precision (int8/float), model ms, stream fps.
"""

from __future__ import annotations

import cv2
import numpy as np

SEVERITY_COLORS = {"none": (0, 170, 0), "low": (0, 220, 255), "medium": (0, 140, 255), "critical": (0, 0, 255)}
CLASS_COLORS = {"open_eye": (0, 200, 0), "closed_eye": (0, 0, 255), "yawn": (255, 0, 255),
                "head_straight": (255, 200, 0), "head_down": (0, 140, 255)}
REASON_TEXT = {"ok": "OK", "eyes_closed": "eyes closed", "yawn_eyes_closed": "yawn + eyes closed",
               "head_down_eyes_closed": "head down, eyes closed", "head_down": "head down (looking down)",
               "yawning": "repeated yawning", "no_face": "driver not visible"}
_FONT = cv2.FONT_HERSHEY_SIMPLEX


def _dashed_rect(img, p0, p1, color, dash=8):
    (x0, y0), (x1, y1) = p0, p1
    for x in range(x0, x1, dash * 2):
        cv2.line(img, (x, y0), (min(x + dash, x1), y0), color, 1)
        cv2.line(img, (x, y1), (min(x + dash, x1), y1), color, 1)
    for y in range(y0, y1, dash * 2):
        cv2.line(img, (x0, y), (x0, min(y + dash, y1)), color, 1)
        cv2.line(img, (x1, y), (x1, min(y + dash, y1)), color, 1)


def _bar(img, x, y, w, h, label, value, marks, scale, color):
    """A horizontal bar for `value` seconds out of `scale`, with tick marks at `marks`."""
    cv2.rectangle(img, (x, y), (x + w, y + h), (60, 60, 60), -1)
    fill = int(w * min(value / scale, 1.0)) if scale > 0 else 0
    if fill:
        cv2.rectangle(img, (x, y), (x + fill, y + h), color, -1)
    for m, c in marks:
        mx = x + int(w * min(m / scale, 1.0))
        cv2.line(img, (mx, y - 2), (mx, y + h + 2), c, 2)
    cv2.putText(img, f"{label} {value:4.1f}/{scale:.1f}s", (x + w + 8, y + h - 2), _FONT, 0.45, (255, 255, 255), 1)


def draw_parts_overlay(frame_bgr: np.ndarray, ctx, fps: float, camera_label: str = "") -> None:
    parts = ctx.features.get("parts") or {}
    state = parts.get("rules")
    h, w = frame_bgr.shape[:2]

    face = ctx.features.get("face_bbox")
    if face is not None:
        cv2.rectangle(frame_bgr, tuple(map(int, face[:2])), tuple(map(int, face[2:])), (255, 255, 255), 1)
    sq = parts.get("crop_square")
    if sq is not None:
        x0, y0, side = sq
        _dashed_rect(frame_bgr, (max(0, int(x0)), max(0, int(y0))),
                     (min(w - 1, int(x0 + side)), min(h - 1, int(y0 + side))), (160, 160, 160))

    for t in parts.get("tracks", []):
        color = CLASS_COLORS.get(t.label, (255, 255, 255))
        bx0, by0, bx1, by1 = (int(v) for v in t.box)
        cv2.rectangle(frame_bgr, (bx0, by0), (bx1, by1), color, 2 if t.visible else 1)
        cv2.putText(frame_bgr, f"{t.id} {t.label.replace('_', ' ')} {t.score:.2f}",
                    (bx0, max(12, by0 - 4)), _FONT, 0.4, color, 1)

    # Severity banner.
    sev = state.severity if state else "none"
    banner = SEVERITY_COLORS[sev]
    cv2.rectangle(frame_bgr, (0, 0), (w, 34), banner, -1)
    if state is None:
        text = "waiting for frames"
    elif sev == "none":
        text = "OK"
    else:
        secs = f" {state.trigger_s:.1f} s" if state.trigger_s > 0 else ""
        text = f"{sev.upper()}  {REASON_TEXT.get(state.reason, state.reason)}{secs}"
    cv2.putText(frame_bgr, text, (10, 24), _FONT, 0.7, (0, 0, 0) if sev in ("low", "none") else (255, 255, 255), 2)

    # Timer panel.
    if state is not None:
        low, med, crit = state.eye_thresholds
        px, py, bw, bh = 10, h - 112, int(w * 0.35), 12
        cv2.rectangle(frame_bgr, (0, py - 22), (w, h), (0, 0, 0), -1)
        note = " (yawn: faster)" if state.after_yawn else ""
        cv2.putText(frame_bgr, f"timers{note}   yawns: {state.yawn_events}", (px, py - 6), _FONT, 0.45, (255, 255, 255), 1)
        eyes = state.timers["eyes_closed"]
        eye_color = SEVERITY_COLORS["critical" if eyes >= crit else "medium" if eyes >= med else "low" if eyes >= low else "none"]
        _bar(frame_bgr, px, py + 4, bw, bh, "EYES CLOSED", eyes,
             [(low, SEVERITY_COLORS["low"]), (med, SEVERITY_COLORS["medium"]), (crit, SEVERITY_COLORS["critical"])],
             crit, eye_color)
        head = state.timers["head_down"]
        _bar(frame_bgr, px, py + 26, bw, bh, "HEAD DOWN  ", head, [(state.head_threshold, SEVERITY_COLORS["critical"])],
             state.head_threshold, SEVERITY_COLORS["medium"] if head > 0 else SEVERITY_COLORS["none"])
        mouth = state.timers["mouth_open"]
        _bar(frame_bgr, px, py + 48, bw, bh, "MOUTH OPEN ", mouth, [], max(mouth, 1.5), CLASS_COLORS["yawn"])

    # Footer.
    footer = f"{parts.get('precision', '?')} | model {parts.get('infer_ms', 0.0):.1f} ms | {fps:.1f} fps"
    if camera_label:
        footer += f" | cam {camera_label}"
    cv2.putText(frame_bgr, footer, (10, h - 10), _FONT, 0.45, (200, 200, 200), 1)
