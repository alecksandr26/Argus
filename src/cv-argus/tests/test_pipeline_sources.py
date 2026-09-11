"""`pipeline/sources.py` — the frame-rate cap.

`_frame_stride` is pure and always runs. The `VideoCaptureSource` decimation test needs `cv2`
(to write and read a throwaway clip) but no `mediapipe` / model — it checks that a 30 fps file
played through a 5 fps source yields ~1/6 of the frames, with strictly increasing timestamps.

`TestProduceLive` covers the *live-camera* half of the same cap (`_produce_live`'s
grab-without-decode pacing loop) directly against a fake `cv2.VideoCapture`-shaped object and a
monkeypatched `time.monotonic`, rather than a real camera or a real clock — deterministic and
fast. `TestPiCameraSource` covers `PiCameraSource`'s `FrameRate` control wiring the same way
`sources.py`'s module docstring describes for the CSI camera, against a fake `picamera2` module
injected into `sys.modules` (the real package only installs on `arm*`/`aarch64`, see
`requirements.txt`) — `PiCameraSource.produce()` defers its `picamera2` import specifically so
this works without the real package present.
"""

import sys
import time
import types

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


class _FakeLiveCap:
    """Stands in for `cv2.VideoCapture` on a live source: `grab()` advances a shared fake clock
    by a fixed native-camera interval and returns `False` once `num_frames` have been grabbed
    (simulating disconnect/EOF), `retrieve()` always succeeds. `clock` is the same mutable dict
    `time.monotonic` is monkeypatched to read from, so grabbing and the pacing loop's own clock
    reads stay in lockstep without any real sleeping."""

    def __init__(self, clock, num_frames, native_interval):
        self._clock = clock
        self._num_frames = num_frames
        self._native_interval = native_interval
        self.grabs = 0

    def grab(self):
        if self.grabs >= self._num_frames:
            return False
        self.grabs += 1
        self._clock["t"] += self._native_interval
        return True

    def retrieve(self):
        return True, np.full((2, 2, 3), self.grabs % 256, dtype=np.uint8)


@pytest.fixture
def fake_clock(monkeypatch):
    from cv_argus.pipeline import sources

    clock = {"t": 0.0}
    monkeypatch.setattr(sources.time, "monotonic", lambda: clock["t"])
    return clock


class TestProduceLive:
    def test_decimates_a_30fps_camera_to_5fps(self, fake_clock):
        from cv_argus.pipeline import VideoCaptureSource

        source = VideoCaptureSource(0, target_fps=5)
        source._cap = _FakeLiveCap(fake_clock, num_frames=60, native_interval=1.0 / 30)

        frames = list(source._produce_live())

        # 60 grabs at a native 30fps = 2s of sim time; at 5fps that's ~10 kept frames.
        assert 9 <= len(frames) <= 11
        timestamps = [f.timestamp_ms for f in frames]
        assert timestamps == sorted(timestamps)
        assert len(set(timestamps)) == len(timestamps)  # strictly increasing, no dupes

    def test_uncapped_keeps_every_grabbed_frame(self, fake_clock):
        from cv_argus.pipeline import VideoCaptureSource

        source = VideoCaptureSource(0, target_fps=0)
        source._cap = _FakeLiveCap(fake_clock, num_frames=25, native_interval=1.0 / 30)

        frames = list(source._produce_live())
        assert len(frames) == 25

    def test_disconnect_ends_the_generator_without_error(self, fake_clock):
        from cv_argus.pipeline import VideoCaptureSource

        source = VideoCaptureSource(0, target_fps=5)
        source._cap = _FakeLiveCap(fake_clock, num_frames=3, native_interval=1.0)

        frames = list(source._produce_live())  # must not raise, just stop early
        assert len(frames) <= 3

    def test_stop_event_ends_the_loop_promptly(self, fake_clock):
        from cv_argus.pipeline import VideoCaptureSource

        source = VideoCaptureSource(0, target_fps=5)
        source._cap = _FakeLiveCap(fake_clock, num_frames=1000, native_interval=1.0 / 30)

        frames = []
        for frame in source._produce_live():
            frames.append(frame)
            if len(frames) == 3:
                source._stop_event.set()
        assert len(frames) == 3


class TestPiCameraSource:
    """`PiCameraSource.produce()` imports `picamera2` lazily, so a fake module injected into
    `sys.modules` is enough to exercise it without the real package (arm-only) installed."""

    class _FakePicamera2:
        def __init__(self):
            self.configs = []
            self.started = False
            self.stopped = False

        def create_video_configuration(self, main=None, controls=None):
            config = {"main": main, "controls": controls}
            self.configs.append(config)
            return config

        def configure(self, config):
            pass

        def start(self):
            self.started = True

        def capture_array(self):
            return np.zeros((4, 4, 3), dtype=np.uint8)

        def stop(self):
            self.stopped = True

    def _install_fake_picamera2(self, monkeypatch):
        instances = []

        def _factory():
            cam = self._FakePicamera2()
            instances.append(cam)
            return cam

        fake_module = types.SimpleNamespace(Picamera2=_factory)
        monkeypatch.setitem(sys.modules, "picamera2", fake_module)
        return instances

    def _run_one_frame(self, source):
        gen = source.produce()
        next(gen)  # drive it up to (and past) the first yield
        gen.close()  # triggers the `finally: picam2.stop()` cleanup

    def test_frame_rate_becomes_a_frame_rate_control(self, monkeypatch):
        from cv_argus.pipeline.sources import PiCameraSource

        instances = self._install_fake_picamera2(monkeypatch)
        self._run_one_frame(PiCameraSource(frame_rate=5))

        assert instances[-1].configs[-1]["controls"] == {"FrameRate": 5.0}
        assert instances[-1].started and instances[-1].stopped

    def test_default_frame_rate_matches_sample_fps_constant(self, monkeypatch):
        from cv_argus import constants
        from cv_argus.pipeline.sources import PiCameraSource

        instances = self._install_fake_picamera2(monkeypatch)
        self._run_one_frame(PiCameraSource())

        assert instances[-1].configs[-1]["controls"] == {
            "FrameRate": float(constants.DEFAULT_SAMPLE_FPS)
        }

    def test_zero_frame_rate_leaves_controls_empty(self, monkeypatch):
        from cv_argus.pipeline.sources import PiCameraSource

        instances = self._install_fake_picamera2(monkeypatch)
        self._run_one_frame(PiCameraSource(frame_rate=0))

        assert instances[-1].configs[-1]["controls"] == {}
