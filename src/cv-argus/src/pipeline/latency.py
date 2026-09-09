"""`StageStats` — per-stage timing / queue-depth / drop instrumentation for the pipeline.

Every `Stage` (see `stage.py`) owns one `StageStats`. It's fed, from the stage's own thread:
how long each `process_item()` took, how long the frame waited in the input queue first, how
full that queue was, how many frames the drop-oldest backpressure path shed, and (for stages
that break their work into sub-steps, e.g. `FusedInferenceStage`'s CNN-embed vs. LSTM-predict)
per-phase timings. Every `interval` seconds it emits one summary line so you can see, from real
numbers, which stage is the bottleneck and where the backlog builds up.

**What a frame's life looks like, and which number covers which part:**

    [ source.produce: proc = time to grab one frame (cap.read / capture_array) ]
      -> queue -> [ stage A: wait = time queued | proc = process_item ]
      -> queue -> [ stage B: wait ... | proc ... ]
      -> queue -> [ sink: wait | proc | e2e = grab-to-here, the whole trip ]

- **`proc`** — work done *inside* the stage: one `process_item()` call (or, on the source,
  one frame grab — `cap.read()` / `capture_array()`).
- **`wait`** — how long the frame sat in this stage's input queue before the stage picked it
  up. Add every stage's `wait` + `proc` and you get roughly the sink's `e2e`; the difference
  is queueing.
- **phase clauses** (e.g. `embed(...) lstm(...)` on `fused_inference`) — a `proc` broken into
  named sub-steps by the stage. `fused_inference` splits into `embed` (the frozen CNN) and
  `lstm` (the sequence model) so you can tell which half of the model is slow.
- **`inq(avg/max)`** — input-queue *depth* (capacity is 4). A stage pinned at `max=4` is the
  bottleneck, or is right behind it.
- **`drop=`** — frames shed by `Stage._emit`'s drop-oldest path (only live sources drop).
- **`e2e(mean=...)`** — sink stages only — capture-timestamp to here: the end-to-end latency,
  i.e. how stale the frame being acted on is. The "feels laggy" number.

Per-stage: `face_detector_crop.process` is MediaPipe BlazeFace + the crop; `face_landmarker_
crop.process` is MediaPipe FaceLandmarker + the geometric-feature math; `fused_inference.process`
is the CNN embed + LSTM predict (see its `embed`/`lstm` phase clauses); the sink is the output.

The stage with the highest `proc` is the compute bottleneck; the stage with the highest
`wait` is where frames are stacking up.

Stdlib only (`collections`, `time`, `logging`) — no `numpy`, so this stays importable on the
`stage.py`-only path that doesn't pull in `cv2`/`mediapipe`/`tensorflow` (see
`pipeline/__init__.py`'s lazy-import note).

Not thread-safe, by design: each `StageStats` is touched only by its owning stage's single
thread. The one cross-thread write is `set_interval()` (called by `Pipeline.__init__` before
any stage thread starts), which is a plain float assignment.
"""

import collections
import logging
import time

logger = logging.getLogger("cv_argus.pipeline.stats")

# Rolling window of recent samples each report summarizes. Big enough that a 10 s interval at a
# few-hundred fps still has headroom; bounded so a slow pipeline left running for hours doesn't
# grow this without limit.
DEFAULT_WINDOW = 512


def _percentile(sorted_samples: list[float], pct: float) -> float:
    """Nearest-rank percentile of an already-sorted list. `pct` in [0, 100]. Returns 0.0 for
    an empty list rather than raising — a report can fire before a stage has processed
    anything (e.g. a downstream stage during startup)."""
    if not sorted_samples:
        return 0.0
    if len(sorted_samples) == 1:
        return sorted_samples[0]
    rank = int(round((pct / 100.0) * (len(sorted_samples) - 1)))
    return sorted_samples[rank]


def _clause(name: str, samples: "collections.deque[float]", *, decimals: int = 1) -> str:
    """`name(mean=Xms p50=Y p95=Z max=W)` for a deque of millisecond samples."""
    ordered = sorted(samples)
    n = len(ordered)
    mean = sum(ordered) / n if n else 0.0
    return (
        f"{name}(mean={mean:.{decimals}f}ms p50={_percentile(ordered, 50):.{decimals}f} "
        f"p95={_percentile(ordered, 95):.{decimals}f} max={ordered[-1] if n else 0.0:.{decimals}f})"
    )


class StageStats:
    """See the module docstring. One instance per `Stage`; `role` distinguishes the label a
    source uses (`produce` — inter-frame gap) from a consumer (`process` — `process_item`
    time)."""

    def __init__(
        self,
        stage_name: str,
        interval: float = 0.0,
        *,
        role: str = "process",
        window: int = DEFAULT_WINDOW,
    ) -> None:
        self._label = f"{stage_name}.{role}"
        self._interval = interval
        self._window = window

        self._proc: collections.deque[float] = collections.deque(maxlen=window)
        self._wait: collections.deque[float] = collections.deque(maxlen=window)
        self._e2e: collections.deque[float] = collections.deque(maxlen=window)
        # Named sub-step timings within one process_item() (e.g. "embed", "lstm"). Insertion
        # order preserved so the report clauses stay in the order the stage records them.
        self._phases: dict[str, collections.deque[float]] = {}
        self._qdepth_sum = 0.0
        self._qdepth_n = 0
        self._qdepth_max = 0
        self._drops = 0

        # Lifetime totals survive the per-report window reset.
        self._life_count = 0
        self._life_max_ms = 0.0

        self._last_report = time.monotonic()

    @property
    def enabled(self) -> bool:
        return self._interval > 0

    def set_interval(self, interval: float) -> None:
        """Called by `Pipeline.__init__` before threads start, so stages built without knowing
        the configured interval still pick it up."""
        self._interval = interval
        self._last_report = time.monotonic()

    def record_process(self, seconds: float) -> None:
        if not self.enabled:
            return
        ms = seconds * 1000.0
        self._proc.append(ms)
        self._life_count += 1
        if ms > self._life_max_ms:
            self._life_max_ms = ms

    def record_wait(self, seconds: float) -> None:
        if not self.enabled:
            return
        self._wait.append(seconds * 1000.0)

    def record_phase(self, name: str, seconds: float) -> None:
        """Record one named sub-step of this stage's `process_item()` (e.g. `"embed"`,
        `"lstm"`). Shows up as an extra `name(mean=... )` clause on the report line."""
        if not self.enabled:
            return
        bucket = self._phases.get(name)
        if bucket is None:
            bucket = self._phases[name] = collections.deque(maxlen=self._window)
        bucket.append(seconds * 1000.0)

    def record_e2e(self, seconds: float) -> None:
        if not self.enabled:
            return
        self._e2e.append(seconds * 1000.0)

    def record_qdepth(self, depth: int) -> None:
        if not self.enabled:
            return
        self._qdepth_sum += depth
        self._qdepth_n += 1
        if depth > self._qdepth_max:
            self._qdepth_max = depth

    def record_drop(self, k: int = 1) -> None:
        # Counted even when disabled is cheap and keeps the number honest if the interval is
        # raised mid-run; the guard is only to skip the (larger) deque work above.
        self._drops += k

    def maybe_report(self, *, force: bool = False) -> None:
        if not self.enabled:
            return
        now = time.monotonic()
        if not force and (now - self._last_report) < self._interval:
            return
        self._emit_report()
        self._reset_window()
        self._last_report = now

    def _emit_report(self) -> None:
        parts = [f"stats {self._label}: n={len(self._proc)}"]

        if self._wait:
            parts.append(_clause("wait", self._wait))

        parts.append(_clause("proc", self._proc))

        for name, samples in self._phases.items():
            parts.append(_clause(name, samples))

        if self._qdepth_n:
            parts.append(
                f"inq(avg={self._qdepth_sum / self._qdepth_n:.1f} max={self._qdepth_max})"
            )

        if self._e2e:
            e2e_sorted = sorted(self._e2e)
            e2e_mean = sum(e2e_sorted) / len(e2e_sorted)
            parts.append(
                f"e2e(mean={e2e_mean:.0f}ms p95={_percentile(e2e_sorted, 95):.0f})"
            )

        parts.append(f"drop={self._drops}")
        parts.append(f"life(n={self._life_count} max={self._life_max_ms:.1f}ms)")

        logger.info(" ".join(parts))

    def _reset_window(self) -> None:
        self._proc.clear()
        self._wait.clear()
        self._e2e.clear()
        for bucket in self._phases.values():
            bucket.clear()
        self._qdepth_sum = 0.0
        self._qdepth_n = 0
        self._qdepth_max = 0
        self._drops = 0
