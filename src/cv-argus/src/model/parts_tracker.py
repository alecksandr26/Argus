"""A small IoU tracker for the "parts" detector, giving stable IDs and smoothed states.

The detector sees each frame independently, so its output flickers: an eye detected one frame,
missed the next, `open` for a frame in the middle of a closure. The rule engine measures
*durations* ("eyes closed for 3 s"), and flicker would keep resetting them. The tracker fixes
that:

- **Stable IDs per part.** Eyes `E1`, `E2`…, the mouth `M1`…, the head `H1`…. A detection joins
  the track of the same part with the highest IoU ≥ `PARTS_TRACK_IOU`, matched greedily.
- **Short misses survive.** A track lives through `PARTS_TRACK_MAX_MISSES` unmatched frames
  (0.6 s at 5 fps) and keeps its last state, so one missed detection doesn't end a closure.
- **Smoothed state.** A track's state is the majority over its last `PARTS_TRACK_VOTE_FRAMES`
  observed classes (ties go to the most recent one), so one `open` frame inside a closure
  doesn't count.
- **Head down leans sensitive.** `head_down` is the detector's weakest class (caught on only
  about half the frames), but a straight head is almost never misread as down. So a head track
  is `head_down` once `PARTS_HEAD_DOWN_VOTES` of its recent frames were (default 1), not by
  majority.

Part kinds: eye (`open_eye` / `closed_eye`), mouth (`yawn`), head (`head_straight` /
`head_down`). Only the detector's classes exist, so a mouth track only appears while yawning.

`reset()` drops every track; `PartsInferenceStage` calls it when the face lock switches to a
different person.

Pure numpy and testable; boxes are in whatever coordinates the caller uses (frame pixels here).
"""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field

import numpy as np

from .. import constants
from .parts_detector import (CLASSES, CLOSED_EYE, HEAD_DOWN, HEAD_STRAIGHT, OPEN_EYE, YAWN, env_float,
                             iou_xyxy)

KIND_OF = {OPEN_EYE: "eye", CLOSED_EYE: "eye", YAWN: "mouth", HEAD_STRAIGHT: "head", HEAD_DOWN: "head"}
_PREFIX = {"eye": "E", "mouth": "M", "head": "H"}


@dataclass
class Track:
    id: str
    kind: str
    box: np.ndarray
    score: float
    history: deque = field(default_factory=lambda: deque(maxlen=constants.PARTS_TRACK_VOTE_FRAMES))
    misses: int = 0
    head_down_votes: int = constants.PARTS_HEAD_DOWN_VOTES

    @property
    def state(self) -> int:
        """Smoothed class id: majority of the recent history, ties broken by recency (a head is
        down once `head_down_votes` recent frames were)."""
        counts = Counter(self.history)
        if self.kind == "head" and counts[HEAD_DOWN] >= self.head_down_votes:
            return HEAD_DOWN
        best = max(counts.values())
        for cls in reversed(self.history):
            if counts[cls] == best:
                return cls
        return self.history[-1]

    @property
    def label(self) -> str:
        return CLASSES[self.state]

    @property
    def visible(self) -> bool:
        return self.misses == 0


class PartsTracker:
    def __init__(self, iou_threshold: float = constants.PARTS_TRACK_IOU,
                 max_misses: int = constants.PARTS_TRACK_MAX_MISSES,
                 head_down_votes: int | None = None):
        self._iou = iou_threshold
        self._max_misses = max_misses
        self._head_down_votes = int(env_float("PARTS_HEAD_DOWN_VOTES") if head_down_votes is None
                                    else head_down_votes)
        self._tracks: list[Track] = []
        self._next = Counter()

    @property
    def tracks(self) -> list[Track]:
        return list(self._tracks)

    def reset(self) -> None:
        """Forget every track and restart the IDs (`E1`, `M1`, `H1`…). Called on a face switch:
        the old face's eyes must not carry their state over to the new face."""
        self._tracks = []
        self._next = Counter()

    def update(self, boxes: np.ndarray, scores: np.ndarray, classes: np.ndarray) -> list[Track]:
        boxes = np.asarray(boxes, np.float32).reshape(-1, 4)
        unmatched = set(range(len(boxes)))
        for kind in ("eye", "mouth", "head"):
            tracks = [t for t in self._tracks if t.kind == kind]
            dets = [k for k in unmatched if KIND_OF[int(classes[k])] == kind]
            if tracks and dets:
                ious = iou_xyxy(np.stack([t.box for t in tracks]), boxes[dets])
                pairs = sorted(((ious[a, b], a, b) for a in range(len(tracks)) for b in range(len(dets))),
                               reverse=True)
                used_t, used_d = set(), set()
                for iou, a, b in pairs:
                    if iou < self._iou or a in used_t or b in used_d:
                        continue
                    used_t.add(a); used_d.add(b)
                    t, k = tracks[a], dets[b]
                    t.box, t.score, t.misses = boxes[k], float(scores[k]), 0
                    t.history.append(int(classes[k]))
                    unmatched.discard(k)
                for a, t in enumerate(tracks):
                    if a not in used_t:
                        t.misses += 1
            else:
                for t in tracks:
                    t.misses += 1
        for k in sorted(unmatched, key=lambda k: -float(scores[k])):
            kind = KIND_OF[int(classes[k])]
            self._next[kind] += 1
            t = Track(f"{_PREFIX[kind]}{self._next[kind]}", kind, boxes[k], float(scores[k]),
                      head_down_votes=self._head_down_votes)
            t.history.append(int(classes[k]))
            self._tracks.append(t)
        self._tracks = [t for t in self._tracks if t.misses <= self._max_misses]
        return self.tracks
