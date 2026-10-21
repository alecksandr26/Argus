"""Deterministic drowsiness rules for the "parts" pipeline: tracked eye, mouth and head states
in, a severity with a reason out.

This is the explainable replacement for the CNN+LSTM's learned window. Every alert can say
*why* ("eyes closed 3.2 s"), and every threshold is a named constant in `constants.py`, each
overridable by an env var of the same name.

**Signals per frame** (from `PartsTracker`; tracks hold their state through short misses):
- `eyes_closed`: at least one eye track, and **all** eye tracks are `closed_eye` (one eye is
  enough when only one is visible);
- `eyes_visible`: any eye track at all;
- `mouth_open`: a mouth track in `yawn`;
- `head_down`: a head track in `head_down`;
- `face`: BlazeFace found a face this frame.

**Timers** measure *continuous* duration from frame timestamps. A gap of no more than
`RULE_GRACE_S` (2 frames at 5 fps) doesn't reset a running timer; the duration just stops
growing during the gap. So one flickered frame never restarts the count. The head timer uses the
longer `RULE_HEAD_GRACE_S` (1 s), because `head_down` is missed on many frames.

**Severity rules.** The current severity is the most severe rule that fires:

| Rule | Fires when | Severity |
|---|---|---|
| eyes closed (ladder) | closed ≥ 1.0 / 2.0 / 3.0 s | low / medium / **critical** |
| … after a yawn | same ladder at 0.7 / 1.3 / **2.0** s, if mouth open now or a yawn ended < 10 s ago | low / medium / **critical** |
| head down + eyes closed or hidden | head down ≥ 3.0 s (2.0 s after a yawn) | **critical** |
| head down, eyes open (looking down) | head down ≥ 3.0 s | medium |
| yawning (fatigue) | ≥ 3 yawn events (mouth open ≥ 1.5 s) in 5 min | low (never higher) |
| driver not visible | no face ≥ 3 / 5 / 10 s, after a face was seen | low / medium / **critical** |

Closed eyes and head down are the risks. Yawning only adds weight: it makes the eye and head
rules fire sooner, and alone it never goes above LOW.

**Hold and recovery.** Severity escalates immediately. It only drops after the rules have stayed
below it for `RULE_RECOVERY_S`, so the alarm doesn't flicker off between frames of a closure.

**Contract mapping** (`to_detection`): the ESP32 firmware only reads `class_name` and
`probabilities`, and treats `p_drowsy ≥ 0.57` as drowsy.
- medium and critical → `Drowsy`, level 2;
- none and low → `Not Drowsy`, level 1;
- `p_drowsy` per severity comes from `RULE_P_DROWSY`.

`reason`, `severity` and the timers travel in `DetectionResult.extras`, so they reach the buffered
payload without breaking any reader.
"""

from __future__ import annotations

import os
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from .. import constants

SEVERITIES = ("none", "low", "medium", "critical")
_RANK = {s: i for i, s in enumerate(SEVERITIES)}
# Which timer measures each reason (yawning is counted in events, not a duration).
_REASON_TIMER = {"eyes_closed": "eyes_closed", "yawn_eyes_closed": "eyes_closed",
                 "head_down": "head_down", "head_down_eyes_closed": "head_down", "no_face": "no_face"}


def _cfg(name: str) -> float:
    raw = os.environ.get(name)
    try:
        return float(raw) if raw not in (None, "") else float(getattr(constants, name))
    except ValueError:
        return float(getattr(constants, name))


@dataclass
class RuleConfig:
    eyes_low: float = field(default_factory=lambda: _cfg("RULE_EYES_LOW_S"))
    eyes_medium: float = field(default_factory=lambda: _cfg("RULE_EYES_MEDIUM_S"))
    eyes_critical: float = field(default_factory=lambda: _cfg("RULE_EYES_CRITICAL_S"))
    yawn_recent: float = field(default_factory=lambda: _cfg("RULE_YAWN_RECENT_S"))
    eyes_low_yawn: float = field(default_factory=lambda: _cfg("RULE_EYES_LOW_YAWN_S"))
    eyes_medium_yawn: float = field(default_factory=lambda: _cfg("RULE_EYES_MEDIUM_YAWN_S"))
    eyes_critical_yawn: float = field(default_factory=lambda: _cfg("RULE_EYES_CRITICAL_YAWN_S"))
    head_down: float = field(default_factory=lambda: _cfg("RULE_HEAD_DOWN_S"))
    head_down_yawn: float = field(default_factory=lambda: _cfg("RULE_HEAD_DOWN_YAWN_S"))
    yawn_event: float = field(default_factory=lambda: _cfg("RULE_YAWN_EVENT_S"))
    yawn_count: float = field(default_factory=lambda: _cfg("RULE_YAWN_COUNT"))
    yawn_window: float = field(default_factory=lambda: _cfg("RULE_YAWN_WINDOW_S"))
    no_face: float = field(default_factory=lambda: _cfg("RULE_NO_FACE_S"))
    no_face_medium: float = field(default_factory=lambda: _cfg("RULE_NO_FACE_MEDIUM_S"))
    no_face_critical: float = field(default_factory=lambda: _cfg("RULE_NO_FACE_CRITICAL_S"))
    grace: float = field(default_factory=lambda: _cfg("RULE_GRACE_S"))
    head_grace: float = field(default_factory=lambda: _cfg("RULE_HEAD_GRACE_S"))
    recovery: float = field(default_factory=lambda: _cfg("RULE_RECOVERY_S"))

    def eye_ladder(self, after_yawn: bool) -> tuple[float, float, float]:
        if after_yawn:
            return self.eyes_low_yawn, self.eyes_medium_yawn, self.eyes_critical_yawn
        return self.eyes_low, self.eyes_medium, self.eyes_critical


class ContinuousTimer:
    """How long a boolean has been continuously true, tolerating gaps ≤ `grace` seconds."""

    # Timestamps are float seconds (ms / 1000): a one-frame gap at 5 fps is "0.4" but computes
    # as 0.40000000000000036, which a bare `>` would treat as beyond a 0.4 s grace -- resetting
    # the timer on exactly the single flickered frame the grace exists to absorb.
    _EPS = 1e-6

    def __init__(self, grace: float):
        self.grace = grace
        self.start: float | None = None
        self.last_true: float | None = None

    def _gap_too_long(self, t: float) -> bool:
        return self.last_true is not None and t - self.last_true > self.grace + self._EPS

    def update(self, t: float, active: bool) -> float:
        if active:
            if self.start is None or self._gap_too_long(t):
                self.start = t
            self.last_true = t
        elif self.start is not None and self._gap_too_long(t):
            self.start = self.last_true = None
        return self.duration

    @property
    def duration(self) -> float:
        return 0.0 if self.start is None else self.last_true - self.start


@dataclass
class FrameSignals:
    face: bool
    eyes_visible: bool = False
    eyes_closed: bool = False
    mouth_open: bool = False
    head_down: bool = False


@dataclass
class RuleState:
    severity: str
    reason: str
    trigger_s: float           # longest duration of the held reason's signal (see update())
    timers: dict[str, float]
    eye_thresholds: tuple[float, float, float]
    head_threshold: float
    after_yawn: bool
    yawn_events: int

    def to_extras(self) -> dict:
        return {"severity": self.severity, "reason": self.reason, "trigger_s": round(self.trigger_s, 2),
                "timers": {k: round(v, 2) for k, v in self.timers.items()},
                "yawn_events": self.yawn_events, "model": "parts-rules-v1"}


def signals_from_tracks(face: bool, tracks) -> FrameSignals:
    from ..model.parts_detector import CLOSED_EYE, HEAD_DOWN, YAWN
    # A face has two eyes: judge only the two most trustworthy eye tracks (seen this frame first,
    # then most confident). A third "eye" is a false detection (seen live: a "closed eye" by the
    # ear), and counting it could mask a real closure or fake one.
    eyes = sorted((t for t in tracks if t.kind == "eye"), key=lambda t: (t.visible, t.score), reverse=True)[:2]
    return FrameSignals(
        face=face,
        eyes_visible=bool(eyes),
        eyes_closed=bool(eyes) and all(t.state == CLOSED_EYE for t in eyes),
        mouth_open=any(t.kind == "mouth" and t.state == YAWN for t in tracks),
        head_down=any(t.kind == "head" and t.state == HEAD_DOWN for t in tracks),
    )


class PartsRuleEngine:
    def __init__(self, config: RuleConfig | None = None):
        self.cfg = config or RuleConfig()
        g = self.cfg.grace
        self._eyes = ContinuousTimer(g)
        self._head = ContinuousTimer(self.cfg.head_grace)
        self._mouth = ContinuousTimer(g)
        self._noface = ContinuousTimer(g)
        self._yawn_counted = False
        self._yawn_events: deque[float] = deque()
        self._last_yawn_end: float | None = None
        self._seen_face = False
        self._held = ("none", "ok")
        self._below_since: float | None = None
        self._trigger = 0.0

    def update(self, t: float, s: FrameSignals) -> RuleState:
        cfg = self.cfg
        if s.face:
            self._seen_face = True
        eyes = self._eyes.update(t, s.face and s.eyes_closed)
        head = self._head.update(t, s.face and s.head_down)
        mouth_was = self._mouth.duration
        mouth = self._mouth.update(t, s.face and s.mouth_open)
        noface = self._noface.update(t, self._seen_face and not s.face)

        # Yawn events: count once when the mouth has been open long enough.
        if mouth >= cfg.yawn_event and not self._yawn_counted:
            self._yawn_events.append(t)
            self._yawn_counted = True
        if mouth == 0.0:
            if mouth_was > 0.0:
                self._last_yawn_end = t
            self._yawn_counted = False
        while self._yawn_events and t - self._yawn_events[0] > cfg.yawn_window:
            self._yawn_events.popleft()

        after_yawn = mouth > 0.0 or (self._last_yawn_end is not None and t - self._last_yawn_end <= cfg.yawn_recent)
        ladder = cfg.eye_ladder(after_yawn)
        head_thr = cfg.head_down_yawn if after_yawn else cfg.head_down

        fired: list[tuple[str, str]] = []
        eye_reason = "yawn_eyes_closed" if after_yawn else "eyes_closed"
        for level, thr in zip(("low", "medium", "critical"), ladder):
            if eyes >= thr:
                fired.append((level, eye_reason))
        if head > 0.0:
            if not s.eyes_visible or s.eyes_closed:
                if head >= head_thr:
                    fired.append(("critical", "head_down_eyes_closed"))
            elif head >= cfg.head_down:
                fired.append(("medium", "head_down"))
        if len(self._yawn_events) >= cfg.yawn_count:
            fired.append(("low", "yawning"))
        for level, thr in (("low", cfg.no_face), ("medium", cfg.no_face_medium),
                           ("critical", cfg.no_face_critical)):
            if noface >= thr:
                fired.append((level, "no_face"))
        current = max(fired, key=lambda f: _RANK[f[0]], default=("none", "ok"))
        timers = {"eyes_closed": eyes, "head_down": head, "mouth_open": mouth, "no_face": noface}

        # Escalate now; de-escalate only after RULE_RECOVERY_S below the held level, and only
        # while the face is visible: a driver who slumps out of frame mid-incident must not read
        # as recovered (the timers above stop without a face, so they'd all fall to zero).
        if _RANK[current[0]] >= _RANK[self._held[0]]:
            if current != self._held:
                self._trigger = 0.0
            self._held, self._below_since = current, None
        elif not s.face:
            self._below_since = None
        else:
            if self._below_since is None:
                self._below_since = t
            if t - self._below_since >= cfg.recovery:
                self._held, self._below_since, self._trigger = current, None, 0.0
        # The longest duration of the held reason's signal, so a reading written during the
        # hold (eyes already reopened) still says how long the eyes were actually closed.
        self._trigger = max(self._trigger, timers.get(_REASON_TIMER.get(self._held[1], ""), 0.0))

        return RuleState(
            severity=self._held[0], reason=self._held[1], trigger_s=self._trigger,
            timers=timers,
            eye_thresholds=ladder, head_threshold=head_thr, after_yawn=after_yawn,
            yawn_events=len(self._yawn_events),
        )


def to_detection(state: RuleState):
    """RuleState → the `DetectionResult` the orchestrator/ESP32 contract expects."""
    from ..model.detector import DetectionResult
    p = float(constants.RULE_P_DROWSY[state.severity])
    level = 2 if state.severity in ("medium", "critical") else 1
    return DetectionResult(level=level, probabilities=np.array([1.0 - p, p], np.float32),
                           extras=state.to_extras())
