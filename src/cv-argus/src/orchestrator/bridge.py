"""`OrchestratorBridgeOutputStage` — the thin seam between the pipeline and `Orchestrator`.

No decision logic here — see `orchestrator.py`. This stage's only job is handing each frame's
`DetectionResult` off to `Orchestrator`'s own decision-loop thread via a plain `queue.Queue`,
the same way `OUTPUTS=logging,mjpeg` already fans the inference stage's output to more than one
sink (`Stage.connect()` called more than once — no new pipeline mechanism needed here).

This is *not* wired as a `Stage`-to-`Stage` connection with `Orchestrator` itself acting as
another `Stage`/`Pipeline` member — `Orchestrator` owns its own thread and lifecycle
independently of the `Pipeline` (see the approved alert-pipeline plan's "three independent
peers" decision), so it's handed a plain queue at construction instead of being wired via
`Stage.connect()`.
"""

import logging
import queue

from ..pipeline.stage import FrameContext, OutputStage

logger = logging.getLogger(__name__)


class OrchestratorBridgeOutputStage(OutputStage):
    """Pushes `(ctx.source_id, ctx.detection)` onto `out_queue` for every finished frame.

    Uses `put_nowait` + drop-on-full rather than blocking — `Orchestrator`'s own decision loop
    must never stall the pipeline. A dropped detection here just means one fewer sample toward
    the debounce counter, not a correctness problem (see `orchestrator.py`'s cooldown/debounce
    design): this is a deliberately always-on, structural sink, not one of `main.py`'s
    `OUTPUTS=` demo/observability toggles.
    """

    def __init__(self, out_queue: queue.Queue, name: str = "orchestrator_bridge", **kwargs) -> None:
        super().__init__(name, **kwargs)
        self._out_queue = out_queue

    def handle(self, ctx: FrameContext) -> None:
        try:
            self._out_queue.put_nowait((ctx.source_id, ctx.detection))
        except queue.Full:
            logger.debug("orchestrator_bridge: input queue full, dropping detection")
