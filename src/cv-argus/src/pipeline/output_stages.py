"""Sink stages: what happens to a finished `FrameContext`.

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

import logging

from .stage import FrameContext, OutputStage

logger = logging.getLogger(__name__)


class LoggingOutputStage(OutputStage):
    """Logs each finished frame's detection result. Frames with no detection this cycle (no
    face found, or the inference stage skipped it) are logged at debug level only, so a
    driver briefly looking away doesn't flood the log the way a missed drowsiness reading would
    be worth seeing.
    """

    def __init__(self, name: str = "logging_output", **kwargs) -> None:
        super().__init__(name, **kwargs)

    def handle(self, ctx: FrameContext) -> None:
        if ctx.detection is None:
            logger.debug("[%s] no detection this frame (face_found=%s)", ctx.source_id, ctx.face_found)
            return
        logger.info(
            "[%s] %s (level=%d, probabilities=%s)",
            ctx.source_id,
            ctx.detection.class_name,
            ctx.detection.level,
            ctx.detection.probabilities,
        )
