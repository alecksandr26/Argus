"""`bootstrap.py` — the thread-pinning knobs that must be set before tf/cv2/mediapipe load.

Behaviour: `configure()` only touches the BLAS/OpenMP/TF thread vars when
`CV_ARGUS_NUM_THREADS` is set, and it never overwrites a value the environment already has
(`setdefault`).
"""

import importlib
import os

import cv_argus.bootstrap as bootstrap

_THREAD_VARS = bootstrap._THREAD_ENV


def _clear(monkeypatch, *names):
    for name in names:
        monkeypatch.delenv(name, raising=False)


def test_sets_all_thread_vars_when_num_threads_given(monkeypatch):
    _clear(monkeypatch, "CV_ARGUS_NUM_THREADS", *_THREAD_VARS)
    monkeypatch.setenv("CV_ARGUS_NUM_THREADS", "4")

    bootstrap.configure()

    for var in _THREAD_VARS:
        assert os.environ[var] == "4"


def test_no_op_when_num_threads_unset(monkeypatch):
    _clear(monkeypatch, "CV_ARGUS_NUM_THREADS", *_THREAD_VARS)

    bootstrap.configure()

    for var in _THREAD_VARS:
        assert var not in os.environ


def test_does_not_override_existing_value(monkeypatch):
    _clear(monkeypatch, *_THREAD_VARS)
    monkeypatch.setenv("CV_ARGUS_NUM_THREADS", "4")
    monkeypatch.setenv("OMP_NUM_THREADS", "2")  # a value already chosen elsewhere

    bootstrap.configure()

    assert os.environ["OMP_NUM_THREADS"] == "2"
    assert os.environ["TF_NUM_INTRAOP_THREADS"] == "4"


def test_gpu_is_always_disabled(monkeypatch):
    _clear(monkeypatch, "CUDA_VISIBLE_DEVICES", "MEDIAPIPE_DISABLE_GPU")

    bootstrap.configure()

    assert os.environ["CUDA_VISIBLE_DEVICES"] == "-1"
    assert os.environ["MEDIAPIPE_DISABLE_GPU"] == "1"


def test_module_import_is_idempotent(monkeypatch):
    # Re-importing (as __main__.py and main.py both do) must not raise.
    importlib.reload(bootstrap)
