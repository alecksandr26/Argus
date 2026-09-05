"""The abstract `Stage`/`Pipeline` runner every camera-loop stage is built from.

A `Stage` is a unit of work that owns its own thread: it pulls items from an `input_queue`,
calls `process_item()` on each one, and pushes whatever comes back onto every queue in
`output_queues`. Subclasses implement `process_item()` (or, for a source/sink, `produce()`/
`handle()` — see `SourceStage`/`OutputStage` below) only; the threading, queueing, backpressure,
and shutdown plumbing here is shared by every concrete stage in this package
(`FaceDetectorCropStage`, `FusedInferenceStage`, `LoggingOutputStage`, ...), which is the whole
point of having this base class: a new stage (a video-overlay output stage, a future model
family's stages, ...) only has to write its own `process_item()`.

**Threads, not multiprocessing** — a deliberate choice, not the default: MediaPipe's and
TensorFlow's native inference calls release the GIL, so threads still get real parallelism on
the actual bottleneck work; stages pass large numpy/tensor objects directly between each other
instead of paying IPC serialization per frame; and start/stop/join lifecycle management is
simpler to get right on a resource-constrained Pi than a multiprocessing supervisor would be.

One `FrameContext` object flows through an entire pipeline, accumulating fields as it passes
through stages, rather than each stage inventing its own input/output shape — so a stage added
later (e.g. a video-overlay output stage) can read whatever an earlier stage produced (the raw
frame, a face crop, a `DetectionResult`, ...) without any upstream stage needing to change.
"""

import logging
import queue
import threading
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from ..model.detector import DetectionResult

logger = logging.getLogger(__name__)

# Poison pill: a stage pushes this onto every output queue when it's done producing/consuming,
# so downstream stages know "no more items are coming" rather than blocking on get() forever.
_SENTINEL = object()

# Frames are memory-heavy (a full BGR image per item) — don't buffer many in flight. Overridable
# per-stage via the `maxsize` constructor argument.
DEFAULT_QUEUE_MAXSIZE = 4

# How long a consumer stage's queue.get() waits before re-checking its stop event. Short enough
# that stop() is responsive, long enough not to busy-loop.
DEFAULT_QUEUE_GET_TIMEOUT = 0.5


@dataclass
class FrameContext:
    """The one item type that flows through every pipeline in this package.

    `features` is a deliberately open bag for stage-specific intermediate values (a MediaPipe
    stage's `"face_crop_rgb"` or `"landmarks_xy"`/`"rotation_matrix"`/`"blendshape_scores"`) —
    new stages don't require editing this shared class. `detection` stays a first-class typed
    field instead, since *every* pipeline in this package converges on the same
    `DetectionResult` shape (see `model/detector.py`) — a deliberate middle ground between
    "everything typed" and "everything a dict", not an oversight.
    """

    frame_bgr: np.ndarray
    timestamp_ms: int
    source_id: str = "default"
    face_found: bool = False
    features: dict[str, Any] = field(default_factory=dict)
    detection: "DetectionResult | None" = None


class Stage(ABC):
    """One node in a pipeline graph. See the module docstring for the overall design.

    A stage with `is_source=True` has no `input_queue` — it's a `SourceStage` (below), which
    produces items rather than consuming them. Every other stage gets its own bounded
    `input_queue`, created here so callers never have to construct one by hand.
    """

    def __init__(
        self,
        name: str,
        *,
        is_source: bool = False,
        maxsize: int = DEFAULT_QUEUE_MAXSIZE,
        drop_oldest_when_full: bool = False,
        queue_get_timeout: float = DEFAULT_QUEUE_GET_TIMEOUT,
    ) -> None:
        self.name = name
        self.input_queue: queue.Queue | None = None if is_source else queue.Queue(maxsize=maxsize)
        self.output_queues: list[queue.Queue] = []
        self.drop_oldest_when_full = drop_oldest_when_full
        self._queue_get_timeout = queue_get_timeout
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def connect(self, downstream: "Stage") -> "Stage":
        """Wire this stage's output to `downstream`'s input queue. Returns `downstream`, so
        calls chain: `source.connect(a).connect(b).connect(c)`.

        Calling `connect()` twice on the same upstream stage fans its output out to two
        downstream chains for free (e.g. running the CNN and LSTM pipelines off one camera
        simultaneously) — there's no separate "fan-out stage" needed for that.
        """
        if downstream.input_queue is None:
            raise ValueError(
                f"{downstream.name!r} has no input queue to connect to (is it a SourceStage?)"
            )
        self.output_queues.append(downstream.input_queue)
        return downstream

    @property
    def is_alive(self) -> bool:
        """True while this stage's thread is running. Lets a caller (see `Pipeline.is_alive`)
        notice a pipeline that finished on its own — e.g. a video-file source reaching EOF —
        without needing an external stop signal."""
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name=self.name, daemon=False)
        self._thread.start()

    def stop(self) -> None:
        """Signal this stage to stop after its current item/iteration. Does not join — see
        `join()`, or use `Pipeline.stop()` to stop and join a whole graph in the right order."""
        self._stop_event.set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def close(self) -> None:
        """Release any resource this stage holds beyond its thread (e.g. a MediaPipe detector
        handle) — called by `Pipeline.stop()` once every stage has been joined. No-op by
        default; override in a subclass that owns something worth releasing explicitly."""

    def _emit(self, item: Any) -> None:
        for q in self.output_queues:
            if not self.drop_oldest_when_full:
                q.put(item)  # blocking -> backpressure; the correct default off a live source
                continue
            # Live sources (see sources.py) prefer bounded latency over completeness: rather
            # than block the whole pipeline behind a slow downstream stage, drop the oldest
            # queued item and keep going, so processing never falls further and further behind
            # real time.
            try:
                q.put_nowait(item)
            except queue.Full:
                try:
                    q.get_nowait()
                except queue.Empty:
                    pass
                try:
                    q.put_nowait(item)
                except queue.Full:
                    logger.debug("%s: downstream still full after dropping oldest, dropping this item", self.name)

    def _run(self) -> None:
        try:
            if self.input_queue is None:
                self._run_source()
            else:
                self._run_consumer()
        except Exception:
            # A bug in a stage's own control flow (not process_item(), which is already
            # guarded below) shouldn't leave downstream stages blocked on a queue that will
            # never get another item or a shutdown signal.
            logger.exception("%s: stage thread crashed", self.name)
        finally:
            for q in self.output_queues:
                try:
                    q.put_nowait(_SENTINEL)
                except queue.Full:
                    try:
                        q.get_nowait()
                    except queue.Empty:
                        pass
                    q.put_nowait(_SENTINEL)
            logger.info("%s: stopped", self.name)

    def _run_consumer(self) -> None:
        while not self._stop_event.is_set():
            try:
                item = self.input_queue.get(timeout=self._queue_get_timeout)
            except queue.Empty:
                continue
            if item is _SENTINEL:
                break
            try:
                result = self.process_item(item)
            except Exception:
                # One bad frame shouldn't kill the pipeline -- log it and keep consuming.
                logger.exception("%s: error processing item, skipping", self.name)
                continue
            if result is not None:
                self._emit(result)

    def _run_source(self) -> None:
        raise NotImplementedError(f"{type(self).__name__} has input_queue=None but doesn't override _run_source()")

    @abstractmethod
    def process_item(self, item: Any) -> Any | None:
        """Do this stage's work on one item. Return the item to pass downstream (typically the
        same `FrameContext`, with new fields filled in) or `None` to drop it."""


class SourceStage(Stage):
    """A stage with no input queue: it *produces* items instead of consuming them. Subclasses
    implement `produce()` — a generator — instead of `process_item()`.
    """

    def __init__(self, name: str, **kwargs: Any) -> None:
        super().__init__(name, is_source=True, **kwargs)

    def process_item(self, item: Any) -> Any | None:
        raise NotImplementedError(f"{type(self).__name__} produces items via produce(), not process_item()")

    def _run_source(self) -> None:
        for item in self.produce():
            if self._stop_event.is_set():
                break
            self._emit(item)

    @abstractmethod
    def produce(self) -> Iterator[Any]:
        """Yield items (typically `FrameContext`) until exhausted or `stop()` is called. Must
        check `self._stop_event` periodically for a long-running/live source so shutdown stays
        responsive — see `sources.py`'s `VideoCaptureSource` for the pattern."""


class OutputStage(Stage):
    """A sink: consumes items but produces nothing further. Subclasses implement `handle()`
    instead of `process_item()`.
    """

    def process_item(self, item: Any) -> None:
        self.handle(item)
        return None

    @abstractmethod
    def handle(self, item: Any) -> None:
        """Do something with a finished item — log it, hand it to orchestrator/ once that
        exists, draw an overlay and write/display a frame, etc."""


class Pipeline:
    """Owns a chain of connected `Stage`s (already wired via `.connect()`) and starts/stops
    them as a group, in the right order.
    """

    def __init__(self, stages: list[Stage]) -> None:
        """`stages` should be every stage in the graph, upstream-to-downstream — used to decide
        join order on `stop()`, not to (re-)connect them; connect stages explicitly first."""
        self.stages = stages

    def start(self) -> None:
        for stage in self.stages:
            stage.start()
        logger.info("Pipeline started: %s", " -> ".join(s.name for s in self.stages))

    def stop(self, timeout: float | None = 5.0) -> None:
        """Signal every stage to stop, then join them upstream-to-downstream (not all at once)
        so a downstream `join()` can't outlast an upstream stage still pushing into a full
        queue, then release any resources each stage holds (see `Stage.close()`)."""
        for stage in self.stages:
            stage.stop()
        for stage in self.stages:
            stage.join(timeout)
        for stage in self.stages:
            stage.close()
        logger.info("Pipeline stopped")

    def is_alive(self) -> bool:
        """True if any stage's thread is still running. Lets a caller detect that the pipeline
        finished on its own (e.g. a video-file source reached EOF) rather than being stopped,
        so it doesn't have to block forever waiting for an external stop signal that's never
        coming — see `src/main.py`."""
        return any(stage.is_alive for stage in self.stages)
