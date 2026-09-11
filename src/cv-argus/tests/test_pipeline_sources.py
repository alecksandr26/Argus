"""`pipeline/sources.py` — the frame-rate cap.

`_frame_stride` is pure and always runs. The `VideoCaptureSource` decimation test needs `cv2`
(to write and read a throwaway clip) but no `mediapipe` / model — it checks that a 30 fps file
played through a 5 fps source yields ~1/6 of the frames, with strictly increasing timestamps.
"""

import time

import numpy as np
import pytest

from cv_argus.pipeline import OutputStage, Pipeline
from cv_argus.pipeline.sources import _frame_stride


@pytest.mark.parametrize(
    "src_fps, target_fps, expected",
    [
        (30, 5, 6),
        (25, 5, 5),
        (5, 5, 1),
        (10, 5, 2),
        (30, None, 1),   # uncapped -> every frame
        (30, 0, 1),
        (0, 5, 1),       # unknown source fps -> don't try to stride
        (29.97, 5, 6),   # rounds
    ],
)
def test_frame_stride(src_fps, target_fps, expected):
    assert _frame_stride(src_fps, target_fps) == expected


class _Collector(OutputStage):
    def __init__(self):
        super().__init__("collector")
        self.timestamps = []

    def handle(self, ctx):
        self.timestamps.append(ctx.timestamp_ms)


@pytest.fixture
def clip_30fps(tmp_path):
    cv2 = pytest.importorskip("cv2")
    path = tmp_path / "clip.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter.fourcc(*"MJPG"), 30.0, (64, 48))
    if not writer.isOpened():
        pytest.skip("no usable VideoWriter codec in this build")
    for i in range(90):  # 3 seconds at 30 fps
        frame = np.full((48, 64, 3), i % 256, dtype=np.uint8)
        writer.write(frame)
    writer.release()
    return str(path)


def test_video_file_is_decimated_to_target_fps(clip_30fps):
    from cv_argus.pipeline import VideoCaptureSource

    source = VideoCaptureSource(clip_30fps, target_fps=5)
    out = _Collector()
    source.connect(out)
    pipe = Pipeline([source, out])
    pipe.start()
    deadline = time.monotonic() + 10.0
    while pipe.is_alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    pipe.stop(timeout=5.0)

    # 3 s of 30 fps at stride 6 -> ~15 frames, nowhere near the full 90.
    assert 12 <= len(out.timestamps) <= 18
    assert out.timestamps == sorted(out.timestamps)
    assert len(set(out.timestamps)) == len(out.timestamps)  # strictly increasing, no dupes


def test_video_file_uncapped_keeps_every_frame(clip_30fps):
    from cv_argus.pipeline import VideoCaptureSource

    source = VideoCaptureSource(clip_30fps, target_fps=0)
    out = _Collector()
    source.connect(out)
    pipe = Pipeline([source, out])
    pipe.start()
    deadline = time.monotonic() + 10.0
    while pipe.is_alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    pipe.stop(timeout=5.0)

    assert len(out.timestamps) >= 80  # ~all 90 (a couple may be lost to shutdown timing)
