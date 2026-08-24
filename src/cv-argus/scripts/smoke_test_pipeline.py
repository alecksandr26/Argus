"""Smoke test for the Stage/Pipeline threading and queue plumbing (`pipeline/stage.py`) —
exercises the actual correctness risk (thread startup order, sentinel propagation, clean
shutdown on a source that finishes on its own, `Pipeline.is_alive()`) without needing
MediaPipe/TensorFlow or any downloaded model at all.

Not imported by `cv_argus` at runtime — like the rest of `scripts/`, run this by hand:

    python scripts/smoke_test_pipeline.py

Requires the `cv_argus` package importable (e.g. `pip install -e .` from `src/cv-argus/`, or
run from inside the dev container) since it exercises the real `Stage`/`SourceStage`/
`OutputStage`/`Pipeline` classes, not a reimplementation of them.
"""

import sys
import time

from cv_argus.pipeline import OutputStage, Pipeline, SourceStage, Stage


class _CountingSource(SourceStage):
    """Yields the integers `0..n-1`, one every `delay` seconds, then stops on its own — the
    "video file reaches EOF" case, not an externally-`stop()`-ped source."""

    def __init__(self, n: int, delay: float = 0.01, **kwargs) -> None:
        super().__init__("counting_source", **kwargs)
        self._n = n
        self._delay = delay

    def produce(self):
        for i in range(self._n):
            yield i
            time.sleep(self._delay)


class _DoublingStage(Stage):
    """Trivial passthrough stage: doubles each item. Stands in for a real MediaPipe/inference
    stage without needing either dependency installed."""

    def __init__(self, **kwargs) -> None:
        super().__init__("doubling_stage", **kwargs)

    def process_item(self, item: int) -> int:
        return item * 2


class _CollectingOutput(OutputStage):
    """Sink that appends every item it sees to a plain list, so the test can assert on what
    actually made it all the way through the pipeline, in order."""

    def __init__(self, **kwargs) -> None:
        super().__init__("collecting_output", **kwargs)
        self.collected: list[int] = []

    def handle(self, item: int) -> None:
        self.collected.append(item)


def main() -> int:
    n = 20
    source = _CountingSource(n)
    doubler = _DoublingStage()
    output = _CollectingOutput()
    source.connect(doubler).connect(output)
    pipeline = Pipeline([source, doubler, output])

    pipeline.start()
    # The source finishes on its own (like a video file reaching EOF) rather than being
    # stop()-ped externally -- this loop is exactly what src/main.py does with
    # Pipeline.is_alive(), exercised here without a real camera.
    deadline = time.monotonic() + 10.0
    while pipeline.is_alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    pipeline.stop(timeout=5.0)

    expected = [i * 2 for i in range(n)]
    if output.collected == expected:
        print(f"OK: {len(output.collected)} items passed through in order, unmodified beyond doubling.")
        return 0
    print(f"FAILED: expected {expected}, got {output.collected}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
