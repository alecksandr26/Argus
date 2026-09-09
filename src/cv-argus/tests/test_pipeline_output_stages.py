"""`pipeline/output_stages.py` — `LoggingOutputStage`, the placeholder sink until
`orchestrator/` exists. A real detection logs at INFO; a frame with no detection this cycle
(driver briefly looking away) is DEBUG-only so it doesn't flood the log.
"""

import logging

import numpy as np

from cv_argus.model.detector import DetectionResult
from cv_argus.pipeline import FrameContext
from cv_argus.pipeline.output_stages import LoggingOutputStage

LOGGER = "cv_argus.pipeline.output_stages"


def _ctx(detection=None, face_found=False):
    return FrameContext(
        frame_bgr=np.zeros((4, 4, 3), dtype=np.uint8),
        timestamp_ms=0,
        face_found=face_found,
        detection=detection,
    )


def test_detection_logs_at_info(caplog):
    stage = LoggingOutputStage()
    det = DetectionResult(level=2, probabilities=np.array([0.2, 0.8]))
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        stage.handle(_ctx(detection=det, face_found=True))

    infos = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(infos) == 1
    assert "Drowsy" in infos[0].message
    assert "level=2" in infos[0].message


def test_no_detection_logs_at_debug_only(caplog):
    stage = LoggingOutputStage()
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        stage.handle(_ctx(detection=None, face_found=False))

    assert [r.levelno for r in caplog.records] == [logging.DEBUG]
    assert "no detection" in caplog.records[0].message


def test_no_detection_is_silent_at_info_level(caplog):
    stage = LoggingOutputStage()
    with caplog.at_level(logging.INFO, logger=LOGGER):
        stage.handle(_ctx(detection=None))
    assert caplog.records == []
