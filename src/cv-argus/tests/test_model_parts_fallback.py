"""int8 → float fallback: the child-process model probe and `PartsDetector`'s two fallback paths
(startup probe failure, runtime exception). Real child processes on tiny in-test models; the
real Drive files are never touched."""

import numpy as np
import pytest

from cv_argus.model import parts_detector as pd_
from cv_argus.model import parts_probe


@pytest.fixture(scope="module")
def tiny_models(tmp_path_factory):
    """A (20, 20, 10)-output conv net as .tflite and .keras, plus a wrong-shape .tflite."""
    tf = pytest.importorskip("tensorflow")
    d = tmp_path_factory.mktemp("models")

    def build(channels):
        inp = tf.keras.Input((160, 160, 1))
        return tf.keras.Model(inp, tf.keras.layers.Conv2D(channels, 8, strides=8)(inp))

    def to_tflite(m, name):
        m.export(str(d / f"{name}_sm"), verbose=False)   # SavedModel path, as in notebook 12
        path = d / f"{name}.tflite"
        path.write_bytes(tf.lite.TFLiteConverter.from_saved_model(str(d / f"{name}_sm")).convert())
        return path

    good = build(10)
    good.save(d / "good.keras")
    return {"tflite": to_tflite(good, "good"), "keras": d / "good.keras",
            "wrong_shape": to_tflite(build(5), "wrong")}


# --- the probe (real child processes) -----------------------------------------------------

def test_probe_accepts_working_models(tiny_models):
    assert parts_probe.probe("int8", tiny_models["tflite"])[0]
    assert parts_probe.probe("float", tiny_models["keras"])[0]


def test_probe_rejects_a_corrupt_file(tmp_path):
    bad = tmp_path / "bad.tflite"
    bad.write_bytes(b"not a flatbuffer")
    ok, detail = parts_probe.probe("int8", bad)
    assert not ok and detail


def test_probe_rejects_the_wrong_output_shape(tiny_models):
    ok, detail = parts_probe.probe("int8", tiny_models["wrong_shape"])
    assert not ok and "output shape" in detail


def test_probe_survives_a_native_crash(monkeypatch, tmp_path):
    # What the Pi failure can look like: the interpreter dies, no Python exception.
    monkeypatch.setattr(parts_probe, "_CHILD", "import os; os.abort()")
    ok, detail = parts_probe.probe("int8", tmp_path / "x.tflite")
    assert not ok and "signal" in detail


def test_probe_times_out(monkeypatch, tmp_path):
    monkeypatch.setattr(parts_probe, "_CHILD", "import time; time.sleep(30)")
    ok, detail = parts_probe.probe("int8", tmp_path / "x.tflite", timeout=0.5)
    assert not ok and "timed out" in detail


# --- PartsDetector.from_env fallback --------------------------------------------------------

class _Stub:
    def __init__(self, *args, **kwargs):
        self.args = args

    def __call__(self, x):
        return np.zeros((20, 20, 10), np.float32)


@pytest.fixture
def fake_env(monkeypatch, tmp_path):
    from cv_argus.model import downloader
    monkeypatch.setattr(downloader, "download_parts_int8_model", lambda: tmp_path / "m.tflite")
    monkeypatch.setattr(downloader, "download_parts_float_model", lambda: tmp_path / "m.keras")
    monkeypatch.setattr(pd_, "_TFLiteBackend", type("TFLiteStub", (_Stub,), {}))
    monkeypatch.setattr(pd_, "_KerasBackend", type("KerasStub", (_Stub,), {}))
    monkeypatch.setenv("PARTS_PRECISION", "int8")
    monkeypatch.delenv("PARTS_INT8_FALLBACK", raising=False)
    return monkeypatch


def test_failed_int8_probe_falls_back_to_float(fake_env):
    fake_env.setattr(parts_probe, "probe", lambda precision, path: (False, "crashed with signal 4"))
    det = pd_.PartsDetector.from_env()
    assert det.precision == "float" and type(det._backend).__name__ == "KerasStub"
    assert "signal 4" in det.fallback_reason


def test_working_int8_keeps_int8_with_a_runtime_fallback_ready(fake_env):
    fake_env.setattr(parts_probe, "probe", lambda precision, path: (True, "ok"))
    det = pd_.PartsDetector.from_env()
    assert det.precision == "int8" and type(det._backend).__name__ == "TFLiteStub"
    assert det._fallback is not None and det.fallback_reason is None


def test_fallback_can_be_turned_off(fake_env):
    def no_probe(*a):
        raise AssertionError("probe must not run with PARTS_INT8_FALLBACK=0")
    fake_env.setattr(parts_probe, "probe", no_probe)
    fake_env.setenv("PARTS_INT8_FALLBACK", "0")
    det = pd_.PartsDetector.from_env()
    assert det.precision == "int8" and det._fallback is None


def test_float_precision_never_probes(fake_env):
    fake_env.setattr(parts_probe, "probe", lambda *a: (_ for _ in ()).throw(AssertionError("probed")))
    fake_env.setenv("PARTS_PRECISION", "float")
    assert pd_.PartsDetector.from_env().precision == "float"


# --- runtime fallback -----------------------------------------------------------------------

def test_runtime_error_switches_to_float_once():
    def broken(x):
        raise RuntimeError("tensor allocation failed")
    det = pd_.PartsDetector(broken, "int8", fallback=lambda: _Stub())
    assert len(det.detect(np.zeros((1, 160, 160, 1), np.float32))) == 0
    assert det.precision == "float" and "RuntimeError" in det.fallback_reason
    assert det._fallback is None                      # used up: a float failure propagates


def test_runtime_error_without_fallback_propagates():
    def broken(x):
        raise RuntimeError("boom")
    with pytest.raises(RuntimeError):
        pd_.PartsDetector(broken, "int8").detect(np.zeros((1, 160, 160, 1), np.float32))
