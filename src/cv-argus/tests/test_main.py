"""`src/main.py` — the env-var parsing that decides what pipeline gets built. The pipeline
build itself (`_build_pipeline`) downloads bundles and loads models, so it is left to the
Docker tier / manual runs; here we pin the small pure-ish helpers around it.
"""

import logging

import pytest

from cv_argus import constants, main
from cv_argus.pipeline import LoggingOutputStage
from cv_argus.pipeline.sources import PiCameraSource, VideoCaptureSource

LOGGER = "cv_argus.main"


class TestSampleFps:
    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("SAMPLE_FPS", raising=False)
        assert main._sample_fps() == constants.DEFAULT_SAMPLE_FPS

    def test_parses_an_integer_value(self, monkeypatch):
        monkeypatch.setenv("SAMPLE_FPS", "10")
        assert main._sample_fps() == 10.0

    def test_parses_a_float_value(self, monkeypatch):
        monkeypatch.setenv("SAMPLE_FPS", "2.5")
        assert main._sample_fps() == 2.5

    def test_zero_disables_the_cap(self, monkeypatch):
        monkeypatch.setenv("SAMPLE_FPS", "0")
        assert main._sample_fps() == 0.0

    def test_negative_is_clamped_to_zero(self, monkeypatch):
        monkeypatch.setenv("SAMPLE_FPS", "-3")
        assert main._sample_fps() == 0.0

    def test_malformed_falls_back_to_default_with_warning(self, monkeypatch, caplog):
        monkeypatch.setenv("SAMPLE_FPS", "fast")
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            assert main._sample_fps() == constants.DEFAULT_SAMPLE_FPS
        assert any("not a number" in r.message for r in caplog.records)


class TestCameraSource:
    def test_bare_integer_becomes_int(self, monkeypatch):
        monkeypatch.setenv("CAMERA_SOURCE", "0")
        assert main._camera_source() == 0
        assert isinstance(main._camera_source(), int)

    def test_device_path_stays_a_string(self, monkeypatch):
        monkeypatch.setenv("CAMERA_SOURCE", "/dev/video2")
        assert main._camera_source() == "/dev/video2"

    def test_video_file_path_stays_a_string(self, monkeypatch):
        monkeypatch.setenv("CAMERA_SOURCE", "clips/demo.mp4")
        assert main._camera_source() == "clips/demo.mp4"

    def test_default_is_zero(self, monkeypatch):
        monkeypatch.delenv("CAMERA_SOURCE", raising=False)
        assert main._camera_source() == 0


class TestBuildSource:
    def test_default_is_video_capture(self, monkeypatch):
        monkeypatch.delenv("SOURCE", raising=False)
        assert isinstance(main._build_source(), VideoCaptureSource)

    def test_picamera_selected(self, monkeypatch):
        monkeypatch.setenv("SOURCE", "picamera")
        assert isinstance(main._build_source(), PiCameraSource)

    def test_value_is_normalised(self, monkeypatch):
        monkeypatch.setenv("SOURCE", "  Video_Capture  ")
        assert isinstance(main._build_source(), VideoCaptureSource)

    def test_unknown_source_exits(self, monkeypatch):
        monkeypatch.setenv("SOURCE", "webcam9000")
        with pytest.raises(SystemExit, match="Unknown SOURCE"):
            main._build_source()

    def test_sample_fps_env_var_reaches_video_capture_source(self, monkeypatch):
        monkeypatch.delenv("SOURCE", raising=False)
        monkeypatch.setenv("SAMPLE_FPS", "12")
        source = main._build_source()
        assert source._target_fps == 12.0

    def test_sample_fps_env_var_reaches_pi_camera_source(self, monkeypatch):
        monkeypatch.setenv("SOURCE", "picamera")
        monkeypatch.setenv("SAMPLE_FPS", "12")
        source = main._build_source()
        assert source._frame_rate == 12.0

    def test_sample_fps_zero_disables_the_cap_on_either_source(self, monkeypatch):
        monkeypatch.setenv("SAMPLE_FPS", "0")

        monkeypatch.delenv("SOURCE", raising=False)
        assert main._build_source()._target_fps is None

        monkeypatch.setenv("SOURCE", "picamera")
        assert main._build_source()._frame_rate is None


class TestBuildOutputs:
    def test_default_is_a_single_logging_sink(self, monkeypatch):
        monkeypatch.delenv("OUTPUTS", raising=False)
        outputs = main._build_outputs()
        assert len(outputs) == 1
        assert isinstance(outputs[0], LoggingOutputStage)

    def test_comma_list_builds_each(self, monkeypatch):
        monkeypatch.setenv("OUTPUTS", "logging, logging")
        assert len(main._build_outputs()) == 2

    def test_blank_entries_are_ignored(self, monkeypatch):
        monkeypatch.setenv("OUTPUTS", "logging,,")
        assert len(main._build_outputs()) == 1

    def test_empty_outputs_exits(self, monkeypatch):
        monkeypatch.setenv("OUTPUTS", "   ")
        with pytest.raises(SystemExit, match="expected at least one"):
            main._build_outputs()

    def test_unknown_output_exits(self, monkeypatch):
        monkeypatch.setenv("OUTPUTS", "logging,telegram")
        with pytest.raises(SystemExit, match="Unknown output"):
            main._build_outputs()

    def test_mjpeg_builder_is_wired(self, monkeypatch):
        """`mjpeg` must map to `MjpegStreamOutputStage` with host/port from the env — checked
        without binding a real socket by swapping the class for a recorder."""
        built = {}

        class _FakeMjpeg:
            def __init__(self, host, port):
                built["host"], built["port"] = host, port

        monkeypatch.setattr(main, "MjpegStreamOutputStage", _FakeMjpeg)
        monkeypatch.setenv("OUTPUTS", "mjpeg")
        monkeypatch.setenv("DEMO_STREAM_PORT", "9111")
        monkeypatch.setenv("DEMO_STREAM_HOST", "127.0.0.1")

        outputs = main._build_outputs()
        assert isinstance(outputs[0], _FakeMjpeg)
        assert built == {"host": "127.0.0.1", "port": 9111}


class TestLatencyLogInterval:
    def test_parses_a_float(self, monkeypatch):
        monkeypatch.setenv("LATENCY_LOG_INTERVAL", "2.5")
        assert main._latency_log_interval() == 2.5

    def test_negative_is_clamped_to_zero(self, monkeypatch):
        monkeypatch.setenv("LATENCY_LOG_INTERVAL", "-5")
        assert main._latency_log_interval() == 0.0

    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("LATENCY_LOG_INTERVAL", raising=False)
        assert main._latency_log_interval() == 10.0

    def test_malformed_falls_back_to_default_with_warning(self, monkeypatch, caplog):
        monkeypatch.setenv("LATENCY_LOG_INTERVAL", "soon")
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            assert main._latency_log_interval() == 10.0
        assert any("not a number" in r.message for r in caplog.records)
